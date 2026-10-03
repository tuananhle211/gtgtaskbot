# Work maintenance — content sync, rebuild, removals, work type deletion

**`PR_WORK_CONFIGURE` only.** ADMIN and OWNER hold it; TEAM_LEAD and EMPLOYEE do
not, and every route below refuses them with a 403 on the service, whatever the
screen drew. No route reads a role name.

No migration. Head stays at `0040`.

## 1. Why

The work taxonomy and the Content → Work mappings are still being normalised.
Correcting a mapping changes what *future* content is filed as; it does not
move what was already counted — a counted result stays where its month reported
it (*counted result stability*). An administrator therefore needs a way to make
the ledger agree with the corrected configuration, and a way to delete the
types that correction left behind.

## 2. The service, and the one thing it adds

`PrWorkMaintenanceService` (`application/pr_work_maintenance_service.py`) runs
`PrContentWorkProjector.project_content` for every piece of content in scope —
the same call the worker makes. Mapping precedence (exact → default →
auto-provision), contributor, validator and the self-validation rule are the
projector's. The service adds exactly one step the projector does not take on
its own: **excluding a counted content result filed under a type the mapping no
longer resolves to**, so the projector's refile rule can move it.

Content-derived work is identified by provenance — `source_type = CONTENT` and
the source key — never by a work type's name. Manual and recurring results are
not read by sync or rebuild.

| Operation | What it does | Writes |
| --- | --- | --- |
| preview | dry-run projection over the scope, classified against the ledger | none |
| sync missing | projects only content with no result yet | additive; a second run writes nothing |
| rebuild | excludes wrongly-typed counted results, then projects everything in scope | moved, recreated, reversed results; M2 and stored M6 figures recomputed |
| admin remove result | excludes one pending or counted result (any source) as `ADMIN_REMOVED`; refused on a validator-rejected row | container, contribution, M2, stored M6 follow; the next projection may restore it |
| remove empty container | deletes a period container with no results that nobody assigned | its contribution, history and allocation rows go with it |
| delete work type | deletes a type nothing refers to; inactive mappings go with it | refused with counts otherwise |
| delete legacy work item | deletes one **pre-`0039`, item-grain** content work item, chosen and confirmed by the administrator | its contributions, evidence, history and the M2/M6 allocation rows on those contributions; M2 and the stored M6 figure recomputed. **Nothing recorded in its place** |

**Scope**: a reporting period (mandatory), optionally one person and one content
type. Candidates are content whose accepting decision fell in the month plus
content that already has a content-derived result in one of the month's
containers. Bounded at `MAX_MAINTENANCE_CONTENT` (200) per run; a larger scope
reports `truncated` and is run again — every operation converges.

**Findings**: `CORRECT`, `MISSING`, `WRONG_WORK_TYPE`, `STALE`, `NEW_WORK_TYPE`,
`UNMAPPED`, `UNRESOLVED`, `BLOCKED`. Counts and up to forty samples travel; never
rows.

## 3. Guards

* Only an **OPEN** period may be synced, rebuilt or cleaned:
  `work_period_not_open_for_cleanup`, "Không thể chỉnh sửa dữ liệu công việc của
  kỳ đã đóng hoặc khóa." There is no override.
* A period holding a **finalised** performance figure refuses rebuild and
  removal with the same reason and `cause: performance_finalized`.
* A result a validator rejected: `work_result_validator_rejected` — the
  release is *Xem xét lại*, not a removal. Any other already-excluded result:
  `work_result_not_admin_removable`.
* A container that has results, evidence, a manager's assignment or a recurring
  occurrence: `work_container_not_removable`.
* A referenced work type: `work_type_still_in_use`, with `references` — active
  mappings, work items, results, contributions, routines, quotas, M2
  allocations, scoring rules, M6 allocations. Nothing cascades.
* A draft quota moved onto a type the plan already has: `quota_work_type_already_exists`.

## 3b. Deleting one legacy content work item

Before `0039` the projector wrote *one work item per content milestone*.
Those rows are still valid history, and an administrator who opens one and
decides it should go can delete **that row**:

```
DELETE /api/pr/work/maintenance/items/{work_item_id}[?note=…]
```

`PrWorkMaintenanceService.admin_delete_legacy_work_item`, `PR_WORK_CONFIGURE`
only.

**What "legacy" means, canonically.**
`is_legacy_content_work_item(item)` in `domain/pr/content_work.py`:
`source_type = CONTENT` **and** `reporting_period_id IS NULL`. Provenance
columns only — never the title (`"Nội dung: …"` is a display string), the
content code, the work type's name or anything decoded from the source key.
A period container is opened as `MANUAL` and keyed on its month; a modern
content result is a `pr_work_results` row; manual work is `MANUAL`; recurring
work is `RECURRING`. All four are refused with `work_item_not_legacy_content`
and a `cause` (`manual` · `recurring` · `period_container`). The list and the
detail expose the same predicate as `is_legacy_content_work`, derived from the
row in memory — no extra query per card — and the detail adds
`can_delete_legacy` (legacy **and** the actor holds `PR_WORK_CONFIGURE`).

**What the delete does, in order, in one transaction.** Authorise; lock the
row (`FOR UPDATE` — two administrators pressing at once get one winner and one
`work_item_not_found`); check the predicate; refuse a row that holds results
(`work_item_delete_blocked`); resolve the month — the earliest `counted_at`
of a counted contribution, else the execution instant; refuse a `CLOSED` or
`LOCKED` month or a finalised figure (`work_period_not_open_for_cleanup`,
`operation: legacy_delete`, message *"Không thể xóa dữ liệu công việc của kỳ
đã đóng hoặc khóa."*); count the children; delete the M2 and M6 allocation
rows keyed on this row's contributions, then its history, evidence,
contributions and the row; audit `pr.work.item_admin_deleted` against the
item's id with a summary (code, title, source key, content id and code, work
type, responsible user, period, per-kind counts, note — never a content
body); re-evaluate M2 for each person whose counted work left the month;
recompute their stored, unfinalised M6 figure. Formulas do not change.

**What it never does.** It does not read the content item for writing: the
row, its lifecycle, approvals, versions, attachments and history are
untouched. It creates no result, opens no container, requests no projection
and runs no sync or rebuild — `results_created` is `0` and
`projection_requested` is `false` in the response, and a regression test pins
both. A projection another content event already queued is left exactly as it
was: it is the content workflow's. The actual therefore **drops and stays
down** until the projector next looks at the piece — the worker, after a
legitimate content event; the administrator, through *Đồng bộ lại từ Nội
dung* on the piece; or the period-wide sync — and records the still-accepted
piece again as a modern result. Deleting is not suppressing: see §3c.

## 3d. Deleting one terminal work item (cancelled or rejected)

Cancelling never deletes and rejecting never deletes: the row, its history
and its audit trail stay. What changed is that a `CANCELLED` row is **out of
the default dashboard** (a `REJECTED` one is listed as before), and an
administrator who opens a cancelled or rejected ordinary row may delete
**that row** when it is safe:

```
DELETE /api/pr/work/maintenance/terminal-items/{work_item_id}[?note=…]
```

`PrWorkMaintenanceService.admin_delete_terminal_work_item`, `PR_WORK_CONFIGURE`
only, for `TERMINAL_DELETABLE_STATUSES = {CANCELLED, REJECTED}`. **Hard-delete
is maintenance, not a transition**: `WORK_TRANSITIONS` still has no edge out
of `REJECTED`, and a rejected proposal is not cancelled on the way out - the
delete records `previous_status` (`CANCELLED` or `REJECTED`) instead. A
**second eligibility rule beside §3b, not a widening of it**: §3b is about
*provenance* (an old content projection, whatever its status), this is about
*lifecycle* (work that ended without being done, whatever its source). A row
may satisfy either; neither predicate reads the other. A terminal manual row
is still refused by §3b (`work_item_not_legacy_content`), and an in-flight or
approved row by this route (`work_item_not_terminal`).

**Where cancelled work went.** `WorkQuery.status = None` — the default of
every list, total, summary tile and search — means *every status except
`CANCELLED`*, decided in `PrWorkQueryService._conditions`, never in the
browser. `status=CANCELLED` is the explicit view and the only way a cancelled
row reaches a page or a count. *Đã hủy* is the last option of the existing
*Trạng thái* filter. A `?item=<cancelled-id>` on the default view still opens
the row, drawn above the list and marked *không nằm trong bộ lọc hiện tại*,
with *Mở mục Đã hủy* beside it; the filter is not changed behind the person's
back.

**Cancelled is not safe.** Before anything is touched the row's accounting is
counted — results, `COUNTED` contributions, and the M2 and M6 allocation rows
on any of its contributions — and any non-zero count refuses the whole delete
with `terminal_work_item_delete_blocked`, `cause: blocking_references` and
the counts in `details.blocking`. The lifecycle writes none of these on a row
it cancels (cancelling excludes the pending credit; allocations are only ever
written against counted contributions), so a non-zero here is old or
hand-shaped data: it is reported, never repaired, never cascaded. A period
container — which the lifecycle never lets into `CANCELLED` — refuses with
`cause: period_container`; a `CLOSED` or `LOCKED` month with
`work_period_not_open_for_cleanup`, `operation: terminal_delete`. A modern
result is never removed through here: that is *Xóa kết quả công việc*, and
its own accounting semantics, in §3c.

**What the delete does, in order, in one transaction.** Authorise; lock the
row (`FOR UPDATE` — two administrators get one winner and one
`work_item_not_found`); require `CANCELLED` or `REJECTED` and not a container; count the
blockers and refuse on any; resolve the month (execution instant, else
completion, else creation) and refuse a shut one; lock the period after the
row, the order every other writer takes; count the children; delete its
history, its evidence and its (uncounted) contributions, then the row; audit
`pr.work.item_admin_deleted` against the item's id with `operation:
terminal_delete`, `previous_status` (`CANCELLED` / `REJECTED`), code, title, work type,
source type and key, content id and code, recurring occurrence, responsible
user, cancel reason and who cancelled, per-kind counts and the note — never a
body. No M2 re-evaluation and no M6 refresh: no counted credit left, so no
figure moved. `results_created` is `0` and `projection_requested` is `false`.

**The read model — one administrative delete per detail.** The detail
carries `can_admin_delete` and, for a holder of `PR_WORK_CONFIGURE` reading a
row one of the two rules covers, `admin_delete` (`rule: legacy | terminal`,
`deletable`, `reason`, `cause`, `blocking`, `period_code`, `message`,
`previous_status`); everybody else gets `false` and `null`, and so does every
row neither rule covers. The screen draws *Xóa công việc* from the flag under
the rule's own block — the legacy block carries the content resync beside the
delete, the terminal block the status being deleted — and, when the flag is
false, the server's sentence and *Đang còn: • n kết quả công việc • n ghi
nhận hiệu suất • n phân bổ KPI/M2* from the counts. A row both rules cover is
reported under `legacy` (one control per panel) and the server accepts either
route for it. React infers nothing from `status`.

**Races**, pinned on PostgreSQL in
`tests/integration/test_pr_work_terminal_delete_pg.py`, for both terminal
statuses: two deletes; a delete
against a result report, an evidence write, a list read, a status mutation and
an M2 reconcile; and a failure after the child cleanup, which commits nothing.

## 3c. The four operations, and why a removed result can come back

| Operation | Who | Calls the projector? | Afterwards the source… |
| --- | --- | --- | --- |
| normal automatic projection | worker, after a content transition or publication | is the projector | — |
| *Đồng bộ lại từ Nội dung* (one piece) | ADMIN/OWNER | `POST /api/pr/work/content/{content_id}/project` → `reconcile([content_id])` → `project_content` | is re-evaluated now |
| delete legacy work item | ADMIN/OWNER | **never** | stays eligible |
| delete terminal work item (§3d) | ADMIN/OWNER | **never** | untouched — a terminal row holds no result |
| admin remove modern result | ADMIN/OWNER | **never** | stays eligible |

There is one projector. The per-content button, the period-wide sync and
rebuild, and the worker all call `PrContentWorkProjector.project_content` with
the same resolver (exact → default → auto-provision), the same contributor and
validator rules, the same period guard and the same source-key convergence.
No route, screen or maintenance method writes a result of its own.

**An administrative removal is an exclusion, and an exclusion is not a
tombstone.** `record_source_result` finds the row by `(CONTENT, source_key)`
— one row per milestone, for ever — and settles it against what the source
says *now*: `COUNTED` on the source's instant when the milestone is live and
independently validated; `PENDING` when it is live but self-validated (an
excluded row returns to `PENDING` so a validator can count it); left
`EXCLUDED` when the milestone is gone (`REVERSED` / `NOT_QUALIFIED`), when the
month is shut (`BLOCKED_BY_PERIOD`), or when nothing maps
(`NO_MAPPING`). The current mapping decides the container: an uncounted row is
refiled to the corrected type's stream, so a result removed under type A and
re-synced after the mapping moved to B lands under B and A is not recreated.
Five syncs, or a sync racing the worker, converge on one row and one unit —
pinned on PostgreSQL in `tests/integration/test_pr_content_work_resync_pg.py`.

**A validator's rejection is the one exclusion the projector does not
re-evaluate** (`0041`, `exclusion_kind = VALIDATOR_REJECTED`): every path —
the worker, this button, *Đồng bộ thiếu*, *Xây dựng lại* — reports
`HELD_BY_VALIDATOR` and writes nothing. A pre-`0041` exclusion with no
recorded kind is held the same way. *Xem xét lại* (`PR_WORK_VALIDATE`) is the
release. See `docs/pr/WORK_RESULT_EXCLUSION_SEMANTICS.md`.

**Where the button is.** On every content result inside a period container's
detail (beside, never merged with, *Xóa kết quả công việc*); on a legacy row's
detail (beside *Xóa công việc*); and on the content item's own page under
*Công việc liên quan*, which for ADMIN/OWNER is drawn even when nothing is
recorded yet — the one place that outlives a removed result. The outcome is
the projector's own vocabulary, worded by `contentWorkOutcomeMessage`.

**On the screen.** A legacy row shows a quiet *Dữ liệu cũ từ Nội dung* pill
to people who hold the capability; *Xóa công việc* is inside the expanded
detail only, behind a dialog that lists what the delete leaves alone. On
success the list drops the card, clears `?item=`, says *Đã xóa công việc cũ.*
and calls nothing else.

## 4. Execution

Synchronous and bounded, like the existing `/work/content/reconcile` route: one
transaction per request, the same savepoints and row locks the projector and
the result service already take. Two rebuilds over one scope, a rebuild racing
the worker, a removal racing a projection and a type delete racing a draft
quota are all pinned on PostgreSQL 17 in
`tests/integration/test_pr_work_maintenance_pg.py`; a failure between the
exclusion step and the reprojection rolls both back.

## 5. Audit

`pr.work.content_sync_requested` / `_completed`,
`pr.work.content_rebuild_requested` / `_completed` (scope, preview counts,
outcome counts, note), `pr.work.result_admin_removed`,
`pr.work.container_admin_removed`, `pr.work.item_admin_deleted` (with
`operation: legacy_delete` or `terminal_delete` with `previous_status` in `before_data`),
`pr.work_type.deleted`, `pr.work_plan.quota_work_type_changed`. Counts and
ids, never row dumps.

## 6. The intended sequence

1. Fix work types (*Cấu hình → Loại công việc*).
2. Fix Content → Work mappings (*Ánh xạ nội dung → công việc*).
3. Move draft KPI quotas to the corrected types (*Kế hoạch KPI → Sửa*).
3b. Open any legacy content work items and delete the ones that should go —
    one at a time, each confirmed. Nothing is re-recorded by this step.
4. *Đồng bộ dữ liệu công việc* → **Kiểm tra dữ liệu**.
5. **Đồng bộ thiếu** for gaps, or **Xây dựng lại từ Nội dung** for a corrected month.
6. Check actuals, KPI, M2, M6.
7. *Loại công việc → Xóa*: sweep the empty streams the rebuild left, then delete.

## 7. Follow-ups, not in this patch

* No persistent suppression ("never record this content as work"). Removing a
  content-derived result is an exclusion; the next projection — automatic,
  per-content or period-wide — records it again while the source is still
  accepted, and the confirmation says so. See §3c.
* No rewrite of CLOSED or LOCKED periods.
* No automatic detection, batch deletion or migration of legacy content work
  items. The administrator opens a row, reads it and decides; the system
  provides the one-row delete and nothing that chooses for them.
* The same for terminal work items (§3d): nothing is purged on cancel, on
  reject, on deploy or on a schedule. One row, one person, one confirmation.
