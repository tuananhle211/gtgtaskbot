# Step 1F.2.3f.6 — Kỳ báo cáo: one global reporting month, one business fact per lane

A read-model and dashboard correctness patch on `/pr/content`, superseding the
completed-only work month of Step 1F.2.3f.4. One new domain table, one new
bounded write action, no schema change (head stays `0037`), no change to Work,
KPI or the Content→Work projection.

---

## 1. What was wrong

Step 1F.2.3f.4 put a month selector over *Hoàn tất* only. It reached the cards
and not the counts, and it invented a *virtual archive*. Observed in
production:

```
Kỳ công việc: 08/2026
  Đã đăng   67      ← the lifetime figure
  27 cards          ← the month's figure
```

Two defects, one root cause each:

* **Count ≠ cards.** The tab and lane figures come from one `GROUP BY
  workflow_stage` over `ContentQuery.for_counts()`, which drops the group and
  the lane. The month predicate existed only *for a named lane or the completed
  group*, so the count statement carried no month at all. `Đã đăng 67` was the
  cumulative count; the 27 cards were August's.
* **Virtual archive.** A `PUBLISHED` row published before the selected month
  was *shown* under *Lưu trữ* with `archive_reason = OLDER_PUBLISHED`. "Published
  in an older month" and "archived" are different facts, and a card claiming
  the second on a row whose stage says the first is the presentation lying.

## 2. The product decision

**Kỳ báo cáo** is one global month over the whole board — *Chuẩn bị*, *Chờ
duyệt*, *Sản xuất*, *Hoàn tất*, *Đã huỷ* and every lane inside them. Membership
in the month is decided **per lane, by that lane's own business instant**, never
by `created_at` for everything and never by `updated_at` for anything.

The board stays a **current-workflow board**: a row is in exactly one lane (its
current stage, split by producer for `APPROVED`), and the month decides whether
that lane's instant falls inside it. A piece created 28/08, cut 03/09 and posted
08/09 is at `PUBLISHED`; in September it is in *Đã đăng* and nowhere else; in
August it is nowhere. This is a filter on the current lane, **not** an
event-history report and **not** a reconstruction of what the board looked like
on 31 August — see §9.

## 3. Timestamp audit — what exists

| Source | Nature | Written by |
|---|---|---|
| `pr_content_items.created_at` | row creation; content is always created at `IDEA` | `PrContentService.create_content` |
| `pr_content_items.updated_at` | any column write on the row | `TimestampMixin` `onupdate` — **rejected** as a lane fact |
| `pr_content_items.production_started_at` | *first* entry into `PRODUCTION`; stamped once, never moved on re-entry | `PrContentWorkflowService.apply` |
| `pr_content_items.archived_at` | entry into `ARCHIVED` (set once) | `PrContentWorkflowService.apply` |
| `pr_content_items.planned_publish_at` | a plan, not an event | people — **rejected** as a lane fact |
| `pr_content_transition_events` (0022) | **every** stage change, append-only, with `UNDO` edges and `reversed_by_event_id` links; one row per `apply` | `PrContentWorkflowService._record_transition` — the only writer of `workflow_stage` |
| `pr_publications.published_at` | one row per channel, statuses incl. `REVERSED` | `PrPublicationService` |
| `pr_approval_events.decided_at` | a decision, which *causes* a transition; the transition row links to it | `PrApprovalService` |
| producer assignment | **no persisted timestamp** — audit text only, which migration 0022 explicitly rejects as authority | `PrProductionService.assign_producer` / `claim_production` |
| cancellation | no column; the transition into `CANCELLED` | `PrContentWorkflowService.cancel` |

Migration 0022 **backfilled nothing, deliberately**: rows whose last move
predates it have no transition history.

## 4. Per-lane canonical instant — the implemented table

`STAGE_PERIOD_FACT` in `domain/pr/content_views.py` is the authority; the SQL is
`reporting_instant()` in `application/pr_content_query.py`. Keyed by **stage**,
because the two `APPROVED` lanes differ only by a producer column that no dated
row records.

| Lane | Stage | Fact | Canonical instant (SQL, in fallback order) |
|---|---|---|---|
| Ý tưởng | `IDEA` | `STAGE_ENTRY` | entry event into `IDEA` (only after an undo/send-back) → **`created_at`** — creation *is* the entry into `IDEA` |
| Brief / Kịch bản / AI review | `BRIEFING` `SCRIPTING` `AI_REVIEW` | `STAGE_ENTRY` | latest effective entry event → `created_at` (legacy rows only) |
| Chờ duyệt trưởng nhóm | `TEAM_LEAD_REVIEW` | `STAGE_ENTRY` | latest effective entry event → `created_at` (legacy only) |
| Chờ duyệt Head | `HEAD_REVIEW` | `STAGE_ENTRY` | latest effective entry event → `created_at` (legacy only) |
| Chờ nhận sản xuất | `APPROVED`, producer null | `STAGE_ENTRY` | entry into `APPROVED` (the Head approval) → `created_at` (legacy only) |
| Sẵn sàng sản xuất | `APPROVED`, producer set | `STAGE_ENTRY` | same as above — assignment leaves no dated row (§5) |
| Đang sản xuất | `PRODUCTION` | `PRODUCTION_ENTRY` | latest effective entry into `PRODUCTION` → `production_started_at` → `created_at` |
| Chờ duyệt nội bộ | `INTERNAL_REVIEW` | `STAGE_ENTRY` | entry event (the submission's transition) → `created_at` (legacy only) |
| Sẵn sàng đăng | `READY_TO_PUBLISH` | `STAGE_ENTRY` | entry event (internal approval, or a full reversal) → `created_at` (legacy only). **Not** `planned_publish_at`. |
| Đã đăng | `PUBLISHED` | `PUBLICATION` | `MIN(published_at)` over active publications (all statuses but `REVERSED`) → entry into `PUBLISHED` → **NULL** |
| Lưu trữ | `ARCHIVED` | `ARCHIVE` | `archived_at` → entry into `ARCHIVED` → **NULL** |
| Đã huỷ | `CANCELLED` | `CANCELLATION` | entry into `CANCELLED` → **NULL** |
| (retired) | `MEASURED` | `STAGE_ENTRY` | mapped so history rows deserialise; reachable from nothing |

**"Latest effective entry event"** = `MAX(created_at)` of
`pr_content_transition_events` where `to_stage = workflow_stage`, `trigger ≠
UNDO` and `reversed_by_event_id IS NULL`. An undo therefore does not restart
the clock on the stage it returns to — the content never left it — which is the
same reading `PrUndoService._latest_effective` applies.

**Fallback policy, stated once.** Operational lanes hold current work and must
remain reachable from *some* month, so their last resort is `created_at` — and
it is reached only for `IDEA` (where it is the truth) and for rows with no
history (pre-0022). Terminal lanes make a claim about a dated event; a claim
nothing recorded stays `NULL` and the row is in no month rather than in an
invented one.

### 4.1 Production: why the current stay, not `production_started_at`

`production_started_at` is stamped on the *first* entry and deliberately never
moved, so it answers "has this ever been produced" (the delete rule reads it).
*Đang sản xuất* means production **now**: a cut sent back on 02/09 and re-entered
on 03/09 is September's production work. For the common case (no send-back) the
two are identical; the column is the fallback for rows that predate history.

## 5. Where no lane-specific fact exists

*Sẵn sàng sản xuất* (`APPROVED` + producer named) has no persisted "assigned
at". Assignment is not a transition and writes only an audit row. Rather than
read audit text or add a column, both `APPROVED` lanes count in the month the
script was approved for production — the operational step both belong to. The
smallest change if a distinct fact is ever wanted is a `producer_assigned_at`
column stamped in `assign_producer`/`claim_production`; **not added** here, and
not needed for the manager's question.

No lane is left without a trustworthy instant, so no migration was required and
none was written.

## 6. Read model

* `ContentQuery.period_month: date | None` — unchanged meaning, now global;
  `None` keeps every other caller (Telegram, flat list, pickers) cumulative.
* `ContentQuery.current_period: bool` — the wire's `period=CURRENT`, resolved in
  `PrQueryService.content_page` against the same business-timezone `today` the
  dated grants use, via `ContentQuery.with_period(today)`. The resolved month is
  echoed as `ContentPage.period` / `ContentBoardResponse.period`.
* `period_conditions(month, tz)` → `[instant >= lower, instant < upper]` over
  `reporting_instant()`, bounds from the same `day_bounds` the date filters use.
* `content_conditions` applies the lane clause and the period clause
  **independently**, so `for_counts()` (group and lane off) still carries the
  month. The list, the total and the grouped count are three statements over
  one condition list — count = cards by construction.
* `completed_period_conditions`, `PrArchiveReason`, `archive_reason`, the
  `archive_reason` response field and `ContentPage.period_lower` are removed.
* Ordinary filters (`date_field` × `CREATED_AT | PLANNED_PUBLISH_AT |
  UPDATED_AT`, platform, channel, responsible person, priority, content type,
  stage, search, scope) compose by `AND` inside the month and never redefine it.

Cost: one correlated index lookup on `(content_id, created_at)` per reached
row, one `MIN` over `pr_publications(content_id)` for `PUBLISHED` rows only
(`CASE` is lazy). No history is loaded; nothing is classified after pagination.
No new index — both lookups hit existing indexes.

## 7. UX and URL

* One selector, **Kỳ báo cáo**, above the scope strip. It is dashboard scope,
  not a filter: *Xóa bộ lọc* keeps it (and the open group). The completed-only
  *Kỳ công việc* selector is removed.
* URL: `/pr/content?group=PRODUCTION&period=2026-09&…`. Switching group
  preserves `period`. With no `period` in the URL every request sends
  `period=CURRENT`; the selector shows the month the server echoed. The page
  source no longer seeds a month from `new Date()`.
* Cards carry *Thực tế đăng* from the server's `published_at` and no archive
  badge.

## 8. Archive

* **Real archive only.** *Lưu trữ* = `workflow_stage = ARCHIVED` and
  `archived_at` in the month.
* **Single-item action** unchanged: `PUBLISHED → ARCHIVED` via the transition
  action on the detail page (`PR_CONTENT_TRANSITION`).
* **Bulk previous-period archive** — `PrBulkArchiveService`, mirroring the
  bulk approval:
  * `GET /api/pr/contents/archive-candidates?period=YYYY-MM` → `total`,
    `content_ids` (first `BULK_ARCHIVE_MAX_ITEMS = 200` in board order),
    `truncated`, `may_archive`. Predicate = `lane_conditions(PUBLISHED)` +
    `period_conditions(month)` — the *Đã đăng* column of that month. No scope,
    no ordinary filter: an end-of-period action over the department's output.
  * `POST /api/pr/contents/archive-batch {period, content_ids, note?}` →
    refuses an empty batch, more than 200, or a month that is not closed
    (current or future); requires `capability_for_target(ARCHIVED)` once,
    first; locks every row in id order; validates under the lock that each is
    `PUBLISHED` with canonical publication instant inside the month (else
    `409 pr_bulk_archive_stale`, nothing archived, `details.affected` naming
    every offender — `already_archived`, `not_published`, `outside_period`,
    `missing`); then moves each item through
    `PrContentWorkflowService.request_transition` — capability, matrix,
    `archived_at`, audit row, transition event, work-projection request, all
    per item, one transaction. One `pr.content.archive_batch_recorded` audit
    row records the batch. A retry after success is a clean 409.
  * Panel: in *Hoàn tất*, when the month before the selected one has
    candidates and `may_archive`, a `Lưu trữ nội dung kỳ MM/YYYY` button with a
    confirmation naming the server's count. **No cron, no scheduler, no
    first-of-month mutation.**

## 9. Limitation — current stage, not history

Selecting August shows the rows whose *current* lane's instant is in August. A
piece published 27/08 and archived 03/09 is at `ARCHIVED`: in September it is in
*Lưu trữ*; in August it is **not** in *Đã đăng*, because the board does not
reconstruct past stages. "What did the board look like on 31 August" is a
different product (event-history reconstruction) and is not built here.

Consequence worth knowing: an item that entered *Chờ duyệt* in July is not in
September's *Chờ duyệt* — the manager selects July to see it. `MY_ACTIONS`
("Cần tôi xử lý") is period-filtered like every other scope.

## 10. Verification

* `tests/unit/test_pr_reporting_period.py` — 37 tests: table coverage; period in
  every group; `CURRENT` echo; not-`created_at` (created August / produced or
  published September); review entry vs `updated_at`; undo does not restart the
  clock; production current stay vs `production_started_at`; both `APPROVED`
  lanes; ready entry vs `planned_publish_at`; `MIN` active publication, one card
  per item, reversed rows excluded; `archived_at`; old publication is not a
  virtual archive; the lifetime-vs-month published count regression; group and
  lane counts move with the period; pagination total; the three date filters,
  platform, channel, person, priority, content type inside the month; scope not
  widened; bulk archive candidates, transitions through the service, current
  month refused, capability gate before writes, stale batch, retry, bound;
  `MEASURED` retired.
* `tests/integration/test_pr_reporting_period_pg.py` — the same SQL on a
  migrated PostgreSQL (head `0037`, no migration), via the services.
* Frontend `tests/content-views.test.tsx` §79–81 — one selector on every group,
  `CURRENT` sent and echoed, URL persistence across groups, counts and lanes
  refetched on change, *Xóa bộ lọc* keeps the month, lane header = same-month
  query, mobile-accessible native select, cards without archive badges, the
  archive control and its confirmation.

---

## 11. Follow-up 1F.2.3f.6a — default scope and the action queue

Product correction, applied on top of the above; nothing in §§1–10 changes
except where stated here.

* **Default scope is `ALL`** for everybody. `default_scope()` keeps its
  signature (still a function of the capabilities held, so a grant-dependent
  default needs no caller to change) and returns `ALL`. The page sends no
  `scope` when the URL has none, highlights the server's echo, and never writes
  `scope=ALL` into the URL. Previously: `MY_ACTIONS` for anybody holding a
  review grant, `MY_CONTENT` otherwise.
* **`MY_ACTIONS` is read month-free.** `period_applies(scope)` in
  `pr_content_query.py` is the one rule, asked inside `content_conditions`, so
  the cards, the total, the grouped stage counts and the production-state
  counts of the queue all carry the same predicate without the month. The
  month is still sent, resolved and echoed on `period`; the new
  `period_applied: false` on the board response says it was not read. `ALL`,
  `MY_CONTENT` and `TEAM` are unchanged.
* **Carry-over:** a piece that entered Head review on 28/08 and is still
  waiting is absent from `ALL` + September and present in the Head's
  `MY_ACTIONS` under any month. Authorization is untouched: the same piece is
  in nobody else's queue.
* **Ordinary filters** (platform, channel, responsible person, priority,
  content type, the three date fields, stage, search) still narrow the queue.
* **UI:** one selector, value preserved across the tab change, disabled under
  *Cần tôi xử lý* with the helper *"Không áp dụng cho “Cần tôi xử lý”"*, re-enabled
  and re-applied on the way back. The optional *"Tồn từ kỳ trước"* badge is not
  built: the card does not carry the lane instant and adding it would expand
  the summary shape for a badge nobody asked for yet.
* Tests: `test_pr_reporting_period.py` §a1–a15, `test_pr_content_views.py`
  §9–10 (rewritten for the new default), frontend `content-views.test.tsx`
  §69 and §82. No migration; head `0037`.

---

## 12. Hotfix 1F.2.3f.6b — the selector lost the current month

**Root cause.** The page built the selector's options as
`recentMonths(12, <resolved selected month>)`: the *selected* month was the
upper anchor, so choosing August produced `08/2026 … 09/2025` and September was
gone until the URL was edited. A deep link to `?period=2026-03` listed nothing
newer than March.

**Fix.** The board response now carries `current_period` (`YYYY-MM`) - the
first day of the month the service's business-timezone `today` falls in,
independent of the selected month, on every response including `MY_ACTIONS`.
`periodOptionsFor(currentPeriod, selected)` in `lib/labels.ts` anchors the list
on it and runs backwards at least twelve months, further when a deep link
names an older one; a future deep link is offered above the run. Before the
first response only the URL's month is offered - nothing is computed from
`new Date()`, and the page source is asserted not to. `MY_ACTIONS` behaviour
is unchanged: disabled selector, preserved value, correct options underneath.
No migration; head `0037`.

---

## 13. Step 1F.2.3f.6c — the archive leaves the operational board

**Decision.** `ARCHIVED` stays a real terminal state with all its data, its
audit history and the `PUBLISHED → ARCHIVED` action (single and bulk), but it is
no longer shown or counted on the default Content dashboard. *Hoàn tất* is
*Sẵn sàng đăng* and *Đã đăng*.

**Backend.**
* `PrContentBoardView { ACTIVE, ARCHIVE }`, `ARCHIVE_STAGES`, `ARCHIVE_LANES`,
  `board_stages(view)` in `content_views.py`. `GROUP_STAGES[COMPLETED]` and
  `GROUP_LANES[COMPLETED]` no longer contain the archive; the partition tests
  now assert "every operational stage in exactly one group" and that the
  archive lane is in none.
* `ContentQuery.view`. The board route resolves `None` to `ACTIVE`; the flat
  list and Telegram tools keep `None` and still answer `stage=ARCHIVED`.
* `view_conditions(view)`: `workflow_stage <> 'ARCHIVED'` for `ACTIVE`, `IN`
  for `ARCHIVE`. On every board statement - cards, total, both count tables,
  archive candidates - so archived rows are not fetched, not filtered through
  the month expression and not grouped.
* `reporting_instant(view)` is built from the view's stages only: the
  operational board's SQL contains no `archived_at`; the archive view's is
  `COALESCE(archived_at, entry)` with no `CASE`.
* Board response gains `view`.

**Query-shape audit** (count statement, `scope=ALL`, `period=2026-09`, no group):

| | before | after |
|---|---|---|
| `archived_at` references | 2 | 0 |
| `CASE` arms | 14 per bound (28) | 3 per bound (6) |
| correlated subplans | 28 | 4 |
| rows evaluated | every stage incl. archived | `<> 'ARCHIVED'` |

The reduction in subplans is the larger change and came out of the audit
rather than the request: every stage's fact is the same chain (stage-specific
first fact → entry into the current stage → stage-specific last resort), so
the entry subquery is now written once per bound instead of once per arm.

**Measured** on a disposable PostgreSQL 17, 20 000 items (60% archived, 20%
published), 57 000 transition events, the count statement, median of 7:

| shape | median |
|---|---|
| before (per-arm CASE, no view clause) | 5 334 ms |
| view clause only, per-arm CASE | 4 602 ms |
| view clause + one entry subquery per bound | 732 ms |
| + `SET LOCAL jit = off` | 67 ms |

`EXPLAIN (ANALYZE)` attributed 4 368 of 4 462 ms to JIT compilation (333
functions) in the first shape and 712 of 795 ms (45 functions) in the third;
the rows themselves cost ~70-100 ms throughout. The planner prices the
correlated lookups per candidate row, so the estimate crosses
`jit_optimize_above_cost` on any table of this size, and JIT gains nothing on
a statement this shape. `PrQueryService._read_row_at_a_time()` now issues
`SET LOCAL jit = off` on PostgreSQL for the transaction of the three board
reads (`content_page`, `archive_candidates`, `approvable_selection`). No-op on
SQLite; no server configuration touched.

**Frontend.** `board.ts` COMPLETED is two stages, two columns; `ARCHIVE_VIEW`
is the archive's one lane. `?view=archive` opens *Nội dung lưu trữ* - month by
`archived_at`, ordinary filters intact, no group tabs, no scope strip, read as
the whole archive - reached by *Xem nội dung lưu trữ* on the period bar and left
by *Quay lại bảng nội dung*. Nothing archive-shaped is requested until that
view is open; the *Lưu trữ nội dung kỳ trước* control stays on the board's
*Hoàn tất* and is absent from the archive.

**Unchanged.** Lane facts, `MIN(active published_at)`, `archived_at` as the
archive's month, bulk archive, `ALL` default, `MY_ACTIONS` month bypass,
MEASURED retirement. `MY_ACTIONS` cannot show an archived row even when an
open task on it would match the queue's predicate - the view clause is on the
queue too. No migration; head `0037`.

---

## 14. Step 1F.2.3f.6d — the reporting month is optional, off by default

**Decision.** `/pr/content` opens on **Tất cả** and **Tất cả kỳ**: every active
row, no reporting-period predicate. A month narrows the board only when a
person chooses one.

**Backend.** A missing `period` has always meant no month at the query level
(`content_conditions` builds the period clause only when `period_month` is
set); what changed is that the board no longer receives `CURRENT` by default.
`CURRENT` stays as an explicit opt-in. Response semantics on every board
answer:

| request | `period` | `current_period` | `period_applied` |
|---|---|---|---|
| default | `null` | current business month | `false` |
| `period=2026-08`, ALL/MINE/TEAM | `2026-08` | current | `true` |
| `period=2026-08`, MY_ACTIONS | `2026-08` | current | `false` |
| `view=ARCHIVE`, `period=2026-08` | `2026-08` | current | `true` (by `archived_at`) |
| `view=ARCHIVE`, no period | `null` | current | `false` |

No magic month stands for "all". `current_period` is the anchor for the option
list and the previous-period archive, never the selected month.

**No-period query shape.** The count statement without a month is
`workflow_stage <> 'ARCHIVED'` plus the ordinary filters: no `CASE`, no
transition-history subquery, no publication aggregate - asserted by a test
that compiles both statements. `SET LOCAL jit = off` stays on the three board
reads; it costs one round trip and is what keeps the month-filtered statement
at ~70 ms rather than seconds. Pagination is unchanged: a lane returns one
page and its full `total`.

**Frontend.** The selector lists *Tất cả kỳ* first, then months from
`current_period` downwards; choosing *Tất cả kỳ* removes `period` from the URL
and nothing else. Under *Cần tôi xử lý* the selector is disabled and its value
(a month or *Tất cả kỳ*) is preserved, by the server's resolved scope. *Xóa bộ
lọc* now clears the month with the other filters and keeps the group and the
archive view. The previous-period archive control targets an explicitly
selected closed month, otherwise the month before `current_period`; the label
always names the month. The archive view opens on *Tất cả kỳ* from the default
board and keeps an explicitly selected month.

**Unchanged.** Lane facts, `MIN(active published_at)`, `MY_ACTIONS` month
bypass, the active-board archive exclusion, bulk archive semantics, MEASURED
retirement. Telegram tools and pickers never sent a period and are untouched.
No migration; head `0037`.
