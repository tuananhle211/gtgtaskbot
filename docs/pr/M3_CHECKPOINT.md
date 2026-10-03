# M3 — checkpoint (paused)

**Paused:** 2026-08-27, at the user's request, part-way through the final
verification sweep.
**Branch:** `feat/pr-module-steps-1a1-1d1`. Nothing committed — the whole branch
is still a working tree, M3 included.
**Not deployed. No production database touched.**

Full design write-up: [`CONTENT_WORK_PROJECTION_M3.md`](CONTENT_WORK_PROJECTION_M3.md).
This file is only the resume note.

---

## 1. What is implemented

M3 is functionally **complete**. Every part of the milestone has code, tests and
documentation behind it. The only thing left is the tail of the verification run
and the written final report.

### New files

| File | What |
| --- | --- |
| `src/meobot/domain/pr/content_work.py` | `PrContentWorkKind`, `PrContentWorkOutcome`, `PrContentWorkProjectionStatus`, `KIND_STAGE_MILESTONES`, `SELF_RECORDED_KINDS`, `content_work_source_key()` |
| `src/meobot/db/models/pr_content_work.py` | `PrContentWorkRule`, `PrContentWorkProjection`; **two** partial unique indexes on the rules table |
| `alembic/versions/0034_pr_content_work_projection.py` | 2 tables, additive, **no backfill**. Head is now `0034` |
| `src/meobot/application/pr_content_work_service.py` | `PrContentWorkRuleService`, `ContentWorkResolver` (exact beats default) |
| `src/meobot/application/pr_content_work_projector.py` | `request_content_work_projection()` (total, runs inside the content transaction) and `PrContentWorkProjector` |
| `src/meobot/tasks/pr_content_work.py` | `pr.sweep_content_work` (30 s beat), `pr.project_content_work`, `pr.recover_stale_content_work` (600 s beat) |
| `src/meobot/api/schemas/pr_content_work.py` | Request/response models, `extra="forbid"` on every request body |
| `src/meobot/api/routers/pr_content_work.py` | 5 routes under `/api/pr/work/content` |
| `tests/unit/test_pr_content_work_projection.py` | 46 tests |
| `tests/integration/test_pr_content_work_atomicity.py` | 7 tests (PostgreSQL) |
| `tests/integration/test_pr_content_work_migrations.py` | 10 tests (PostgreSQL) |
| `frontend/tests/content-work.test.tsx` | 11 tests |
| `docs/pr/CONTENT_WORK_PROJECTION_M3.md` | The milestone write-up |

### Modified files

| File | Change |
| --- | --- |
| `src/meobot/domain/pr/work.py` | `APPROVED → COMPLETED` as a **source-only** transition; `allowed_work_transitions()` / `can_transition_work()` / `assert_work_transition()` gained a `source_derived` flag |
| `src/meobot/application/pr_work_service.py` | `create_source_work()`, `count_source_work()`, `reverse_source_work()`, `_reconcile_eligibility()`, `_refuse_source_mutation()` on cancel / add / remove contributor / deadline / priority |
| `src/meobot/application/pr_workflow_service.py` | Projection requested at `_record_transition` — the chokepoint every stage change including undo passes through |
| `src/meobot/application/pr_publication_service.py` | Projection requested in `register_publication` and `reverse_publication` |
| `src/meobot/application/pr_services.py` | `content_work_rules` and `content_work` wired; the projector shares the caller's `PrWorkService` |
| `src/meobot/application/pr_lifecycle_service.py` | **Bug fix, see §4.** Permanent content delete now removes the projection queue row and **refuses** when source-derived work exists |
| `src/meobot/api/schemas/pr_work.py` | `content_id`, `content_code`, `is_source_derived` |
| `src/meobot/api/main.py` | M3 router mounted **before** M1's `GET /api/pr/work/{id}` |
| `frontend/src/lib/api.ts` | `put` helper, M3 types and calls |
| `frontend/src/app/pr/work/page.tsx` | Source block, link to content, "chờ xác nhận độc lập", manual actions hidden for source-derived work |
| `frontend/src/app/pr/work/kpi.tsx` | `ContentWorkMapping` panel, gated on `PR_WORK_CONFIGURE` **only** (moved out of the period-scoped block) |
| `README.md` | `0034` entry; head and count updated to 34 |
| `docs/pr/WORK_CORE_M1.md`, `WORK_QUOTA_M2.md`, `WORK_PERFORMANCE_ARCHITECTURE.md` | Cross-references, roadmap ticks, closed risks |
| `tests/unit/test_pr_reporting_schema_parity.py` | M3 projector exempted in three sweeps, each with its reason |
| `tests/integration/test_pr_work_core_migrations.py`, `test_pr_work_quota_migrations.py` | `compare_metadata` filters widened for 0034's two tables |
| `tests/unit/test_pr_permanent_delete.py` | New test 29b for the refusal in §4 |

---

## 2. What is verified

| Check | Result |
| --- | --- |
| `ruff check src tests` | **clean** |
| `ruff format --check src tests` | **clean** — 534 files already formatted |
| `mypy src` | **clean** — no issues in 403 source files |
| `tsc --noEmit` | **clean** |
| `next build` | **succeeds** |
| Frontend `vitest run` (whole suite) | **728 passed / 25 files**, 0 failed |
| `pytest tests/integration -m integration` (PostgreSQL) | **365 passed**, 0 failed |
| `pytest tests/unit/test_pr_content_work_projection.py` | **46 passed** |
| `pytest tests/unit/test_pr_permanent_delete.py` | **25 passed** |
| `pytest tests/unit/test_pr_reporting_schema_parity.py` | **53 passed** |
| Full backend `pytest` | ran to completion: **8 failures, all pre-existing** (§4) |

### Performance, measured

PostgreSQL 17, single process, scratch database. Script:
`scratchpad/bench_m3.py` (throwaway, not committed).

| Scenario | Items | Queries | ms | ms/item |
| --- | ---: | ---: | ---: | ---: |
| project 1, first run | 1 | 39 | 86 | 86 |
| project 1, converged no-op | 1 | 15 | 14 | 14 |
| reconcile 100, first run | 100 | 3 902 | 5 420 | 54 |
| reconcile 100, converged | 100 | 1 402 | 1 061 | 11 |
| reconcile 200, first run | 200 | 7 802 | 27 438 | 137 |
| reconcile 200, one author | 200 | 7 802 | 49 875 | **249** |
| reconcile 200, four authors | 200 | 7 798 | 9 062 | **45** |

**Query count is exactly linear** (39/item first run, 15/item converged, at every
size). Wall clock is superlinear, and the last two rows isolate why: the *same*
200 items and the *same* ~7 800 queries cost 5.5× more dealt to one author than
to four. The quadratic term is **M2's** `evaluate()`, which recomputes a whole
`(user, period)` — which is what makes undo/redo converge. Bounded in practice by
one person's output in one month. Chunking the reconcile does **not** help
(measured; slightly worse).

---

## 3. Tests run, with results

Everything below was run in this session, in this order.

1. `frontend/tests/content-work.test.tsx` — 11/11 after two fixes (§5).
2. `npx vitest run` — 728/728, 25 files.
3. `npx tsc --noEmit` — clean. `npx next build` — succeeds.
4. `tests/integration/test_pr_content_work_migrations.py` — 10/10.
5. `tests/integration/test_pr_content_work_atomicity.py` — 7/7.
6. `pytest tests/integration -m integration` — 365/365.
7. `ruff check` / `ruff format --check` / `mypy src` — all clean.
8. Full backend `pytest -q -p no:randomly` — completed; 8 failures, all §4.

**PostgreSQL used:** a throwaway container `meobot-m3-pg`
(`postgres:17`, host port **55432**, password `m3test`). Every suite creates its
own uniquely-named scratch database and drops it. The container is **still
running** — see §7.

---

## 4. Known failures

### Pre-existing, not M3 — 8 failures, leave alone

```
tests/unit/test_hr_requests.py::test_history_events_carry_no_reason
tests/unit/test_hr_requests.py::test_a_private_note_never_reaches_the_requester_view
tests/unit/test_notification_routing.py::test_hr_submission_queues_a_private_card_with_the_reason
tests/unit/test_notification_routing.py::test_the_attendance_update_carries_no_reason
tests/unit/test_notification_routing.py::test_approval_queues_a_private_result_for_the_member
tests/unit/test_notification_routing.py::test_a_rejection_reason_never_reaches_a_group
tests/unit/test_notification_routing.py::test_an_unreachable_member_does_not_invalidate_the_decision
tests/unit/test_notification_routing.py::test_a_missing_attendance_group_does_not_block_the_member_message
```

All eight are one **date time-bomb**, not eight bugs. Both files hard-code
`TOMORROW = date(2026, 7, 31)`; `hr_request_service.py:111` refuses an attendance
request for a past date, so every one of them started failing on **2026-08-01**.
Today is 2026-08-27.

Evidence they are not M3's:

* `git diff --name-only main...HEAD` lists **neither** file — the whole branch
  touches neither;
* both were last modified by commit `127ac79` (2026-08-01), which is not on this
  branch;
* the failure is `ValidationError: Bạn chỉ có thể xin nghỉ hoặc đi muộn cho hôm
  nay và những ngày sắp tới.` — the HR calendar rule, nothing to do with work
  projection.

Fixing them is a separate, unrelated change and was deliberately **not** made.

### Real failures found and fixed during verification — 3

1. **`test_pr_permanent_delete.py::test_29a`** — *a genuine bug M3 introduced.*
   `pr_content_work_projections.content_id` is a `RESTRICT` foreign key that was
   not in the content delete plan. Fixed in `pr_lifecycle_service.py`, and the
   fix has two halves:
   * the queue row (operational scaffolding, no business meaning) is now deleted
     with the aggregate;
   * a delete is now **refused** when source-derived work exists. Work links back
     by `source_key` **text**, so no constraint would have fired and counted
     contributions would have been silently orphaned — possibly inside a reported
     month. Same shape as the existing publication refusal, and for the same
     reason. New test `29b` covers it.
2. **`test_pr_reporting_schema_parity.py`** ×2 — the projector legitimately reads
   `PrPublication` (a source milestone) and `PrReportingPeriod` (to *refuse* a
   projection into a shut month). Added to the exemption lists in three sweeps,
   each with its reason written out. The absolute "no report run / artifact /
   weekly input" half is untouched.
3. **`test_pr_work_core_migrations.py`** and **`test_pr_work_quota_migrations.py`**
   `compare_metadata` filters — M3's two tables are not named `pr_work_*` but
   both carry a foreign key to `pr_work_types`, so `"pr_work" in str(difference)`
   matched them against databases deliberately stopped at 0032/0033. Filters
   widened, with the reason recorded.

---

## 5. Fixes made this session worth remembering

* **`content-work.test.tsx` tests 7–8** failed because `stubFetch` takes the
  *first substring hit* and the bare `/api/pr/work` list route is a prefix of
  every literal path — so `/api/pr/work/periods` returned a page object and the
  component threw. `routes()` now takes an `extra` list that is **prepended**.
* **The mapping panel was unreachable** with no reporting period, because it sat
  inside `PlanAdministration` (rendered only when a period is selected). It is
  now in `KpiWorkspace`, gated on `PR_WORK_CONFIGURE` alone — the mapping has
  nothing to do with a period, and hiding it behind "open a month first" hid it
  exactly when a department is setting M3 up for the first time.

---

## 6. Unfinished work

Two items. Neither is code.

1. **The tail of the full-suite verification.** The backend suite *has* run to
   completion with the 8 pre-existing failures above. A final re-run was started
   only to capture the exact **pass count** for the report, and was interrupted
   (§7). Nothing depends on it but the number.
2. **The 35-point FINAL REPORT** required by the milestone prompt has not been
   written. All the material for it exists — this file, plus
   `CONTENT_WORK_PROJECTION_M3.md`.

### Deliberately not implemented

**Part Y — notifications for self-approved work pending validation.** A
self-approved piece shows *"Chờ xác nhận độc lập"* on the work board and is
pushed to nobody. Routing it safely needs a notion of *who validates for this
person*, and M3 has no team model to ask; inventing one would be inventing the
team hierarchy this milestone is forbidden to create. The prompt permits omitting
it when routing cannot be targeted safely. Recorded as limitation 1 in the
milestone doc.

---

## 7. Interrupted processes and leftover state

* **Interrupted:** background bash task `bab83gsxc` —
  `.venv/bin/pytest -p no:randomly --tb=no -q`. Stopped with `TaskStop`. It was a
  **read-only** run whose only purpose was to print the pass-count summary line;
  it had already been established that the suite completes with the 8 failures in
  §4. Nothing was left half-written.
* **Still running:** Docker container **`meobot-m3-pg`** (`postgres:17`, host
  port 55432, password `m3test`). It is a throwaway created for this session.
  Its `postgres` database was migrated to head so `tests/integration/test_database.py`
  could run. **Remove it when it is no longer wanted:**

  ```bash
  docker rm -f meobot-m3-pg
  ```

  No other database was touched. The user's other PostgreSQL server
  (`content-factory-postgres-1`) was **not** used in this session.
* **Scratch files** (safe to delete, not part of the repo):
  `<scratchpad>/bench_m3.py`, `<scratchpad>/int.txt`.
* **Nothing is committed.** No `git add`, no commit, no push, no deploy.

---

## 8. Exact next step to resume

```bash
# 1. The database, if it was removed in the meantime
docker run -d --name meobot-m3-pg -e POSTGRES_PASSWORD=m3test -p 55432:5432 postgres:17
sleep 8
export MEOBOT_TEST_DATABASE_URL="postgresql+asyncpg://postgres:m3test@localhost:55432/postgres"
DATABASE_URL="$MEOBOT_TEST_DATABASE_URL" .venv/bin/alembic upgrade head   # for test_database.py only

# 2. The one number the report is missing
.venv/bin/pytest -p no:randomly --tb=no -q | tail -3
#    Expect: 8 failed, <N> passed — the 8 being §4's HR date time-bombs.

# 3. Then write the 35-point FINAL REPORT. No code change should be needed.
```

If step 2 shows **any failure outside the eight named in §4**, that is new and
should be investigated before the report is written.
