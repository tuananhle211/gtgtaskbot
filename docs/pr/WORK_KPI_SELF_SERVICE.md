# KPI self-service — the employee proposes, the manager decides

Migration `0038_pr_kpi_plan_submission_review`. A lifecycle *around* M2's plan
versioning, not a change to it: every rule M2 states — one `APPROVED` per
employee and month, one `DRAFT` in flight, an approved plan never edited in
place, a revision cloned by `revise()` with its quotas, approval superseding the
old version and recomputing the open period — is kept byte for byte.

---

## 1. Audit — what existed

| Piece | Finding |
|---|---|
| Plan table `pr_work_plans` | versioned per `(user, period)`; `status` DRAFT/APPROVED/SUPERSEDED/DISCARDED; `supersedes_plan_id`; `created_by`, `approved_by/at`, `discarded_by/at`; partial unique indexes for one APPROVED and one DRAFT |
| Quotas `pr_work_quotas` | one per work type per plan version; basis/unit validated against the type; mutable only on a DRAFT (service rule) |
| Lifecycle | `create_plan` (first draft), `revise` (clone approved → next DRAFT), `approve` (lock, validate, supersede, recompute M2), `discard` |
| Permissions | every write `PR_WORK_CONFIGURE` (ADMIN, OWNER); reads: own plan or `PR_WORK_VIEW_ALL`; `PR_WORK_MANAGE` grants nothing here |
| Submission state | **none** — no column, no status, nothing in `note` |
| Self-approval | **nothing forbade it**: an OWNER could approve their own plan; no test exercised it and no doc called it intentional |
| Readiness | `_validate_for_approval` only, raising the first problem |
| Locking | `approve`, `revise`, `discard` lock the row; quota edits did **not** |
| Workload facts | M6 scoring rules (`standard_minutes_per_unit` in force by date), policy `daily_target_minutes`, `PrPerformanceTargetService.resolve` (workdays × daily, leave, overrides) |
| Notifications | `PrWorkNotifier`, in-app only, idempotent keys, four work events |
| Period rules | plans need an `OPEN` `MONTH` period; no other lock concept |

## 2. Schema — migration 0038

Five nullable columns on `pr_work_plans` and two check constraints. No status
widened, no table, no historical row touched.

| Column | Meaning |
|---|---|
| `submitted_at`, `submitted_by_user_id` | the employee handed the draft over |
| `returned_at`, `returned_by_user_id` | a manager sent it back |
| `return_note` | the manager's reason, shown on the returned card |

Constraints: `(submitted_at IS NULL) = (submitted_by_user_id IS NULL)` and the
same for the return pair. A draft is read as:

| `status` | `submitted_at` | `returned_at` | review state |
|---|---|---|---|
| DRAFT | null | null | `EDITING` — Bản nháp |
| DRAFT | set | — | `SUBMITTED` — Chờ duyệt |
| DRAFT | null | set | `RETURNED` — Cần chỉnh sửa |

The service clears one when it sets the other; `plan_review_state()` in
`domain/pr/work_quota.py` is the one reading. Roundtrip 0038 → 0037 → 0038 is
tested on PostgreSQL.

## 3. Permissions

| Act | Who |
|---|---|
| `POST /plans/mine` — Tạo KPI của tôi | any `PR_WORK_EXECUTE` holder, **subject = session**, no `user_id` in the body (a smuggled one is a 422) |
| `POST /plans` — create for another | `PR_WORK_CONFIGURE`, unchanged |
| `POST /plans/{id}/revise` — Đề xuất điều chỉnh | `PR_WORK_CONFIGURE` **or the plan's subject** |
| quota add / edit / remove | `PR_WORK_CONFIGURE` on any DRAFT, submitted or not; the subject on their own DRAFT while **not** submitted (`draft_submitted_locked`) |
| `POST /plans/{id}/submit` — Gửi duyệt | the subject only (`not_plan_subject`), on a ready draft |
| `POST /plans/{id}/return` — Trả lại | `PR_WORK_CONFIGURE`, on a submitted draft (`draft_not_submitted`) |
| `POST /plans/{id}/approve` | `PR_WORK_CONFIGURE` **and not the subject** (`self_approval_forbidden`) — no owner override; none existed and none was added |
| `POST /plans/{id}/discard` | `PR_WORK_CONFIGURE` on any DRAFT; the subject on their own unsubmitted draft |
| reads | own plan / own summary, or `PR_WORK_VIEW_ALL`; others get a 404 |

Employees still cannot create work types, change standard minutes, scoring
rules, policies or another person's plan; those surfaces are untouched.

## 4. Lifecycle

```
employee: self_create_plan / revise(approved)   →  v(n) DRAFT  [EDITING]
employee: add / edit / remove quotas
employee: submit  (readiness checked)           →  v(n) DRAFT  [SUBMITTED]  employee locked
manager : edit quotas in place (same version)
manager : return(note)                          →  v(n) DRAFT  [RETURNED]   employee unlocked
employee: edit, submit again                    →  v(n) DRAFT  [SUBMITTED]
manager : approve (readiness re-checked, lock)  →  v(n) APPROVED, v(n-1) SUPERSEDED, M2 recomputed
```

No version is created by submit, return or a manager's correction. A submitted
draft decides nothing: `plan_in_force`, the eligibility summary and every
allocation keep naming the previous APPROVED version (or none) until approval.

**Readiness** is one list, `PrPlanReadinessBlocker` — `period_not_open`,
`subject_inactive`, `plan_has_no_quotas`, `work_type_inactive`,
`quota_unit_mismatch`, `quota_bounds_invalid` — exposed on the detail as
`readiness_blockers` with Vietnamese labels, refused by `submit` as
`plan_not_ready` with `details.blockers`, and re-derived by `approve` through
the unchanged `_validate_for_approval` (same codes).

**Manager-created drafts need no submission**: a manager's own draft is
approved directly by any other configurer, exactly as before.

**Concurrency.** Quota edits now lock the plan row like the lifecycle acts, so
a save racing a submission reads the submission. The stale action always gets
a structured refusal: `draft_submitted_locked`, `plan_not_draft`,
`draft_already_submitted`, `draft_already_exists`; the partial unique indexes
remain the last word on duplicate drafts and duplicate approvals.

## 5. Workload preview (advisory)

`PlanWorkload` on the detail and on both summary rows:
`projected_minutes = Σ target × standard_minutes_per_unit` over quotas whose
work type has an approved `STANDARD_MINUTES` rule in force on the period's
last day (excluded types add nothing; unpriced types are listed in
`unscored_work_type_ids`); `target_minutes` from `PrPerformanceTargetService`
with the approved policy's daily minutes; `percent` rounded to M6's
`WORKLOAD_QUANTUM`. No band, no gate, no score, no M6 write.

## 6. Read model and API

* `GET /plans/mine/summary?period_id=` — the caller's `EmployeePlanSummary`
  with `draft_review_state`, `draft_is_submitted`, `draft_submitted_at`,
  `draft_submitted_by_user_id`, `draft_returned_at`, `draft_return_note`,
  `draft_workload`, `current_workload`.
* `GET /plans/summary` rows gain the same fields; the list gains `counts`
  (`pending_review`, `approved`, `drafting`, `without_plan`) counted
  server-side from the same rows.
* `WorkPlanResponse` gains `review_state`, `review_state_label`,
  `submitted_*`, `returned_*`, `return_note`; `WorkPlanDetailResponse` gains
  `is_subject`, `can_submit`, `can_return`, `readiness_blockers`,
  `readiness_blocker_labels`, `submitted_by_name`, `returned_by_name`,
  `workload`.
* New routes: `POST /plans/mine`, `POST /plans/{id}/submit`,
  `POST /plans/{id}/return`.

## 7. Audit and notifications

Audit: `pr.work_plan.created` (with `self_service`), `pr.work_plan.revised`
(with `self_service`), `pr.work_plan.submitted`, `pr.work_plan.returned`, the
existing approved / superseded / discarded / quota events.

In-app notifications through the existing `PrWorkNotifier`, idempotent per
plan and instant: `pr_kpi_plan_submitted` to every active ADMIN/OWNER (the
role behind `PR_WORK_CONFIGURE`), `pr_kpi_plan_approved` and
`pr_kpi_plan_returned` to the subject. Never to whoever acted.

## 8. UI

Employee (`Kế hoạch KPI` tab, section *KPI của tôi*): no plan → *+ Tạo KPI của
tôi*; editable draft → editor, *Tải KPI dự kiến*, *Bỏ bản nháp*, *Gửi duyệt*
(disabled with the server's reasons when not ready); submitted → read-only,
*Chờ trưởng phòng duyệt*, *Gửi lúc …*; returned → *Cần chỉnh sửa* with the
note, editor, *Gửi lại duyệt*; approved → *Kế hoạch đang áp dụng · bản vN* with
*Đề xuất điều chỉnh*, and a pending revision as its own card. Actions stack on
narrow screens; no table.

Manager: tabs *Tất cả / Chờ duyệt N / Đang áp dụng / Bản nháp / Chưa có KPI*
from the server's counts; rows show the draft's review state and projected
workload; a submitted draft opens to the editor with *Trả lại để chỉnh sửa*
(note field) and *Duyệt và áp dụng*. Reason codes are mapped to Vietnamese in
`lib/labels.ts`.

## 9. Not changed

M2 allocation semantics, `candidate_sort_key`, the NO_QUOTA rules; M6
formulas, gates and bands; M1 Work, M3 projection, M4 manual and recurring
work, `execution_at`; work-type and scoring configuration permissions; the
Content dashboard.

---

## 10. Hotfix — *Bỏ bản nháp* and *Gửi duyệt* returned HTTP 500

**Root cause, shared.** Discard, submit and return each flush an `UPDATE` to
the plan row. `updated_at` is maintained by the database (`onupdate=func.now()`),
so the flush expires that attribute on the identity-mapped object. Every
write returns through `detail()`, whose result the router serialises with
Pydantic in ordinary synchronous code; reading the expired attribute there
asks SQLAlchemy for I/O outside its async context and raises
`MissingGreenlet`, which the error middleware reports as a 500. Approval
escaped only because the M2 recompute happened to `SELECT` the plan again and
repopulated the attribute. The failing layer is **response serialisation,
after flush and before commit**; the request's transaction rolled back, so no
partial state reached the database (verified on PostgreSQL: after a failed
discard the row stays `DRAFT`, after a failed submit `submitted_at` stays
null).

**Fix.** One line in `PrWorkPlanService.detail()`: `await
self._session.refresh(plan)` before the read model is built, so nothing a
serializer reads can be expired. No schema change, no migration; the 0038
constraints were audited against the full state matrix (editing, submitted,
returned, discarded-after-submission, approved-after-submission) and every
state the service writes is valid under them.

**Verified over HTTP** on SQLite and on PostgreSQL 17 through the real app,
session dependency and commit: discard of an empty own draft (200), submit of
an empty draft (422 `plan_not_ready` with `plan_has_no_quotas`, row untouched),
submit of a ready draft (200, both submission columns set together), repeat
submit (409), author discard and stale save on a submitted draft (409
`draft_submitted_locked`), another employee's discard (404), manager edit and
return (200, return columns set together, submission cleared), resubmit (200,
return cleared), self-approval (403), manager approval (200, plan in force),
second approval refused.
