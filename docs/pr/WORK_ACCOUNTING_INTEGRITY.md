# Work accounting integrity — the pre-deploy fix

No migration. Alembic head stays `0041_pr_work_result_exclusion_kind`.

The model, stated once:

| Concept | Owner | Role |
|---|---|---|
| Work Result / contribution | `pr_work_results`, `pr_work_contributions` | **determines the Actual** |
| KPI target | `pr_work_quotas.target_value` | a target, nothing else |
| M2 allocation | `pr_work_quota_allocations` | **classifies** counted Actual: eligible / over quota / no quota |
| M6 | scoring rules, `pr_performance_results` | prices counted work |

The Actual never depends on an allocation row having materialised, and a KPI
never creates, deletes, caps, gates or manufactures work.

## 1. Two validation layers, one convergence rule

A source-derived result can be counted two ways, and the projector now knows
which:

| `PrWorkCountOrigin` | Written by | Meaning |
|---|---|---|
| `SOURCE` | `record_source_result`, `count_source_work` | the source's own independent validator counted it |
| `WORK_VALIDATOR` | `validate_results`, `approve` | a person holding `PR_WORK_VALIDATE` counted it in the Work module |

The origin is stamped as `origin` on the `RESULT_COUNTED` / `COUNTED` /
`APPROVED` history row in the same transaction as the count, and read back by
`PrWorkResultService.count_origin` (results) and `PrWorkService.count_origin`
(legacy one-off items). A `RESULT_COUNTED` row without the key is a
`validate_results` row (that path never wrote `source_validated_by_user_id`),
so pre-patch human counts classify correctly.

`source_may_reverse_count(source_eligible, source_independent, origin)` in
`domain/pr/work_results.py` is **the one rule**, asked by `_converge_result`
(results), `_settle` (legacy items) and therefore by the worker, the
single-content button, the batch sync and the rebuild alike:

| Source milestone | Source validation independent? | Count origin | Verdict |
|---|---|---|---|
| gone | — | any | **reverse** (`SOURCE_REVERSED`) |
| live | yes | any | keep |
| live | no | `SOURCE` | **reverse** — the source withdrew its own validation |
| live | no | `WORK_VALIDATOR` | **keep** — the person's confirmation is the independent validation |
| live | no | unknown | reverse — the projector never guesses a person into existence |

Why not `counted_by_user_id` against the acceptance actor: the row's own
columns cannot tell a head who accepted a cut (source) from the same head
who later confirmed it in the Work module (person), and after a withdrawal
the current milestone no longer names its former validator at all. The
timeline row is persisted, written by both paths, and unambiguous.

The legacy path (`count_source_work`) revives an `EXCLUDED` contribution only
when its newest `EXCLUDED` history row carries `origin = SOURCE`, which
`reverse_source_work` now stamps. Any other exclusion is a person's and is
held; the audit row names it under `held_excluded_contributions`.

## 2. Period containers

A container is the system's accounting stream for one person, one type and
one month. `_refuse_container` now guards **cancel**, **accept** and
**reject** as well as start / complete / approve / reopen / contributors.

`ensure_container` refuses a container found `CANCELLED` with
`period_container_cancelled` (409) rather than filing results into it. No
current path produces that state; a row left by the old lifecycle is
repaired by *cleanup-empty-containers*, which now removes a cancelled empty
stream even when it was assigned or opened by a routine. The next report
opens a live stream in the same slot. Recurring activation and generation
skip an existing cancelled container exactly as before (they never file
results), so nothing is resurrected silently.

## 3. One Actual, three readers

`measure_counted(contribution, item, basis=…)` in
`pr_work_quota_service.py` is the one measuring call. M2's evaluator, M2's
read model and M6's `counted_amount_of` all go through it:

* a one-off job measures as before (one item on `ITEM_COUNT`, its quantity on
  `QUANTITY`);
* a **period container** measures as the sum of its counted results on either
  basis — 23 counted scripts are 23 items, not one contribution.

The KPI read model reports the **live** amount for every row, including
`PENDING_EVALUATION` (amount known, split unknown) and materialised rows
(the allocation supplies the split and the basis; the amount is measured).
`counted_amount_of` never reads `allocation.basis_amount`. A failed or
unreconciled M2 hand-off therefore changes only the classification.

## 4. Recurring accounting mode

`update_template` refuses a change of `accumulate_by_period` with
`recurring_accounting_mode_locked_for_period` (409) while the current
reporting month holds a firing that generated work, or — for an
accumulating routine — a container with a result. The change applies from
the next period. Nothing is migrated.

## 5. Finalised performance

`PrWorkPeriodService.performance_finalized(user_id, period_id)` is the guard.
It refuses, with `performance_finalized` (409):

* every result mutation on the subject's stream (`_require_open`): report,
  validate, reject, reconsider, withdraw;
* `record_source_result` and the projector (`BLOCKED_BY_PERIOD`, detail
  `FINALIZED`) for the contributor's month;
* one-off `approve` for any pending contributor whose month is agreed;
* maintenance already refused rebuild, sync and admin removal.

Period close is **not** implemented here; that is a separate lifecycle task.

## 6. Current counter state

Every exclusion (validator rejection, source reversal, administrator
removal) clears `counted_by_user_id` with `counted_at`. Who counted it
before stays on the timeline and in the audit trail.
