# M3.1 — Content Workflow → Work Milestone Alignment

> **Superseded in shape by [`WORK_RESULTS_BY_PERIOD.md`](WORK_RESULTS_BY_PERIOD.md)
> (migration `0039`).** A milestone with no pre-existing work item now contributes
> **one result** (`pr_work_results`, keyed `content:{id}:KIND`) to the
> contributor's monthly stream rather than one work item of its own. The
> milestones, the contributor rules, the validation instant, the reversal, the
> period rules and the re-filing boundary below are unchanged; only the row they
> land on moved.

**Status:** implemented. Not deployed. No catch-up run, no backfill.
**Migration:** **none.** Head remains `0034_pr_content_work_projection`. See §7.
**Scope:** which content milestones create workload, and when that workload
becomes countable.
**Out of scope, and absent from the code:** scoring, points, team hierarchy,
manual mirroring of content actions, and any change to M2's eligibility engine.

---

## 1. The rule, in one box

> ### AUTOMATIC CONTENT WORKLOAD MILESTONES — V1
>
> **1. Duyệt trưởng phòng** → the writer's workload.
> `COUNTED` when the head who approved it is **not** the writer.
>
> **2. Gửi bản dựng** → the editor's workload, `COMPLETED`.
> `COUNTED` only after an **independent video approval**.
>
> **No other content step creates automatic workload.**

Receiving a brief, research, intermediate review, the reviewing itself,
shooting, thumbnails, publication, distribution and meetings are all real effort
and none of them is projected. Each would need its own canonical milestone,
contributor and reversal signal; inventing any of the three is how a ledger fills
with rows nobody can defend. They may return later as manual work, recurring
work, or a mapping somebody deliberately configures.

**Content Type → WorkType is owner-configurable**, through the same rule table
M3 shipped and the work types M2.5 lets an owner create. Adding a content work
type needs no developer.

---

## 2. What M3.1 changed, and why

M3 was right about the writer and wrong about the editor.

| | M3 | M3.1 |
| --- | --- | --- |
| `CONTENT_CREATION` milestone | `HEAD_REVIEW → APPROVED` | unchanged |
| `PRODUCTION` milestone | `INTERNAL_REVIEW → READY_TO_PUBLISH` | **the submission** |
| `PRODUCTION` counted at | the same approval | the approval, still |
| `PUBLICATION` | projected, never self-counted | **not projected** |

### The editor's job was invisible

M3 keyed production work on the *acceptance* of the cut. So an editor who handed
in on Friday had nothing on their board until a reviewer got to it on Monday,
and a queue of unreviewed cuts looked like a queue of people who had done
nothing. The workload had happened; the ledger did not know.

M3.1 splits one milestone into **two instants**:

```
Gửi bản dựng ──────────────▶ WorkItem COMPLETED, contribution PENDING
   (submission.created_at)      "đã gửi bản dựng · chờ xác nhận"
        │
        │  somebody else accepts the cut
        ▼
Duyệt video ───────────────▶ same item, same contribution, COUNTED
   (approval.decided_at)        counted_at = the approval's instant
```

Nothing about M1's boundary moved. The editor handing in their own file is not a
second person confirming it, which is exactly why the middle state exists.

### Publication was turned off

The technical objection was already in M3's own code: recording a publication
takes only the *view* right, the platform id and URL are nullable, and nothing
verifies the post exists — so the person who records it is routinely the person
it credits, with no independent evidence. M3 handled that by never letting it
self-count. M3.1's business rule goes further: V1 records workload at two
content milestones, and posting is not one of them.

**Turning the tap off is not draining the tank.** Publication work M3 already
created is kept, keeps resolving its kind and label, and is explicitly exempted
from the orphan sweep — see §8.

---

## 3. The canonical events, as audited

Not from labels in a spec; from the rows.

| Business step | Canonical fact | Contributor | Instant |
| --- | --- | --- | --- |
| **Duyệt trưởng phòng** | `pr_content_transition_events` where `from_stage=HEAD_REVIEW`, `to_stage=APPROVED`, `trigger=HUMAN_APPROVAL`, `reversed_by_event_id IS NULL`, newest | `created_by_user_id` of the `pr_content_versions` row the transition pins | `pr_approval_events.decided_at` |
| **Gửi bản dựng** | a `pr_production_submissions` row — what `PrProductionService.submit_production` writes, audited `PR_PRODUCTION_SUBMITTED`, moving `PRODUCTION → INTERNAL_REVIEW` | `producer_user_id`, copied onto the submission at hand-over | `submission.created_at` |
| **Duyệt video** | the same transition shape, `INTERNAL_REVIEW → READY_TO_PUBLISH` | — (this is the *validator*) | `pr_approval_events.decided_at` |

Deliberately **not** used as the production milestone: producer assignment,
`production_started_at`, an artifact pasted anywhere, or a draft autosave. The
hand-in is the job being finished.

---

## 4. Contributors

Both come from **immutable historical rows**, never from the content item's
current columns. `owner_user_id` and `producer_user_id` both change, and reading
them during a catch-up would credit today's owner for a script somebody else
wrote two months ago — a wrong row that looks exactly like a right one.

* **writer** — the author of the *version that was approved*;
* **editor** — the producer of the *accepted* submission when there is an
  acceptance, and of the **latest** submission before that. V2 is the same job
  as V1, and the current cut is the job as it stands.

Where the chain breaks — a pre-versioning item, a transition that pins no
version — the outcome is `UNRESOLVED_CONTRIBUTOR` and **nothing is written**.
Crediting nobody is the safe failure; crediting the wrong person is
indistinguishable from a correct row afterwards.

No fractional credit was invented, and several people touching a piece does not
produce several contributors.

---

## 5. Source identity — one job, however many attempts

```
content:{content_id}:CONTENT_CREATION
content:{content_id}:PRODUCTION
```

The key names **no submission and no transition event**, and that is the whole
idempotency contract:

* V1, V2, V3, V4 of the same cut → **one** work item, one contribution;
* a worker retry → the same item;
* an approval undone and re-made → the same item, re-counted on the new instant;
* two concurrent workers → the loser fails on `uq_pr_work_items_source`, a
  **partial** unique index over `source_key IS NOT NULL`, and the next run finds
  the winner's row.

The partial half matters as much: manual work has no key, and a total index
would forbid a second manual row.

---

## 6. Counting, and taking it back

`counted_at` is the **validation** instant — the head's approval for a script,
the video approval for a cut — and never the projector's clock. A worker retry
next month must not move a contribution into next month's reporting period.

**Withdrawal is a state M3 could not reach.** Under M3 the acceptance *was* the
production milestone, so losing it made the work an orphan and the whole item
was reversed. Under M3.1 the submission is the milestone and it does not
un-happen — submissions are never deleted, and `PrUndoService` refuses to undo
the handoff while one exists. So when an acceptance is withdrawn:

* the **work item stays**, at `COMPLETED`. The editing happened;
* the **count is reversed** — contributions to `EXCLUDED`, nothing erased;
* a re-acceptance counts it again, on the new instant, with no second row.

Subject to the period rules below, which have no force flag.

---

## 7. Schema — why there is no migration

`pr_content_work_rules` already keys on **`(contribution_kind, content_type) →
work_type`**, with two partial unique indexes: one rule per typed pair, one
default per kind. That is exactly the model this milestone needs, *including a
per-content-type production mapping* — the prompt's preferred option B, already
built.

Everything M3.1 changed is which rows the projector reads and what it concludes.
No column, table, index or constraint moved, so **no `0035` was written** and the
head is still `0034`. `test_05` in the PostgreSQL suite asserts that, so the
claim cannot rot.

---

## 8. Reconciliation and diagnostics

The existing outcome vocabulary already covers what the milestone asks for:

| Asked for | Existing outcome |
| --- | --- |
| `PROJECTED` | `PROJECTED` |
| `ALREADY_CORRECT` | `UNCHANGED` |
| `PENDING_INDEPENDENT_VALIDATION` | `PENDING_VALIDATION` |
| `UNMAPPED_WORK_TYPE` | `NO_MAPPING` |
| `UNRESOLVED_CONTRIBUTOR` | `UNRESOLVED_CONTRIBUTOR` |
| `BLOCKED_PERIOD_STATE` | `BLOCKED_BY_PERIOD` |
| `FAILED` | `PrContentWorkProjectionStatus.FAILED` on the queue row |

plus `REVERSED` and `NOT_QUALIFIED`. Reconciliation stays idempotent, stays
bounded to open-period candidates, and **runs no bulk catch-up**.

**The publication exemption.** Publication milestones are never live now, so
without an explicit skip every publication work item M3 ever created would look
orphaned and be reversed on the next sweep — silently deleting counted workload
from months people have already been assessed on. `_converge_orphans` skips the
kind outright, and `test_21c` pins it.

### Periods

| Period state | Behaviour |
| --- | --- |
| `OPEN` | reconcile freely — project, count, re-count, reverse |
| `CLOSED` / `LOCKED` | **`BLOCKED_BY_PERIOD`**, and the numbers stay as agreed |

There is no force flag, and M3 never writes a quota allocation. When a
contribution becomes `COUNTED`, M2's evaluator runs behind the same savepoint it
always did; a missing quota is `NO_QUOTA` and does not stop the work counting.

---

## 9. Mapping changes and content-type changes

Two ways a work type turns out wrong without anybody doing anything wrong:
somebody fixes the content's type, or the owner replaces the mapping. M3
resolved the work type **once, at creation**, so both left the work under a
heading nobody meant — permanently, and invisibly. M3.1 adds a correction path
with one boundary:

| State of the work | On the next projection |
| --- | --- |
| nothing `COUNTED` yet | **re-filed** under the currently configured type |
| any contribution `COUNTED` | **left alone**, whatever the period's state |

The boundary is `COUNTED` rather than the period status, and that is deliberate:
a counted contribution is a figure in a reporting period somebody may already
have been assessed on, and re-filing it would rewrite what that month claimed.
Refusing at the ledger rather than at the projector means no projection path can
route around it — `PrWorkService.retype_source_work` checks it itself.

So saving a mapping **never mass-rewrites history**. It corrects what has not
yet been counted, and stops. `test_39` in M3's suite pins the counted half;
tests 24-27 in M3.1's pin the uncounted half and the boundary between them.

---

## 10. Permissions and mutability

| | Employee | Team Lead (`PR_WORK_MANAGE`) | Owner/Admin (`PR_WORK_CONFIGURE`) |
| --- | --- | --- | --- |
| read own source-derived work | yes | yes | yes |
| edit mapping | no | **no** | yes |
| reconcile | no | no | yes |
| validate somebody else's work | via `PR_WORK_VALIDATE`, never their own | same | same |

Source-derived work stays **read-only at the server**: contributor, work type,
source link, lifecycle state, quantity, cancellation and completion are owned by
the content workflow, and `_require_source_derived` refuses the edges. The one
manual action that remains is exactly the one M3 deliberately leaves open —
independent validation of a contribution the source could not validate itself.

Employees never mirror a content action in Work. There is no second "Hoàn thành"
to press.

---

## 11. Frontend

* **`/pr/work?view=config`** — *Cấu hình* now holds both halves of the setup:
  the work types (M2.5) and **Mapping nội dung** beside them. M3 had put the
  mapping under *Kế hoạch KPI*, the only `PR_WORK_CONFIGURE` surface at the
  time; a mapping has nothing to do with a reporting period, and the two
  settings a person needs together were two tabs apart.
* The kind picker offers **the two V1 milestones only**. A rule configured
  before M3.1 for publication still renders, labelled *"Đăng bài (không còn tự
  động)"* — retired, not forgotten.
* **Content detail → Tổng quan → Công việc liên quan** — a compact list of the
  work this piece produced, each row linking into Work. Not a second work
  dashboard: no filters, no actions. Scoped by the caller's own permissions, so
  an employee sees their own contribution and a manager sees everybody's.
* **Work detail** says *Nguồn: Nội dung*, links back to the content, and its
  pending sentence was widened — `COMPLETED` now has two causes, and the old
  copy asserted the self-approval one, which would be wrong for every unreviewed
  cut.
* No score, no points, anywhere.

---

## 12. Known limitations

1. **Contributor is the latest submission's producer.** If editor A hands in V1
   and editor B hands in V2, credit follows B (or whoever's cut was accepted).
   No fractional split was invented; the alternative needs a product decision
   about shared production nobody has taken.
2. **`Công việc liên quan` is permission-scoped, not content-scoped.** An
   employee looking at a colleague's content sees only their own row. Widening
   it would be a back door into the department-wide view `PR_WORK_VIEW_ALL`
   gates.
3. **No publication, thumbnail, shoot or review workload.** By decision, not
   oversight.
4. **`correct_submission` does not itself request a projection.** Correcting an
   artifact link changes no milestone, so nothing needs reprojecting; if that
   ever changes, the sweep and reconcile still converge.
5. **M2 recomputation dominates reconcile time** for many content items
   belonging to one employee — M3's measured limitation, unchanged and
   deliberately not re-opened here.

---

## 13. What M3.1 must not be read as doing

No scoring. No points. No team model. No content mapping table beyond the one
M3 built. No manual duplicate reporting. No bulk catch-up, no backfill, no
deploy.
