# Work results: why a result is out, and who may put it back (`0041`)

**Head after this step: `0041_pr_work_result_exclusion_kind`.** One nullable
column, one CHECK, no backfill. See `docs/pr/WORK_RESULTS_BY_PERIOD.md` for the
result model and `docs/pr/WORK_MAINTENANCE.md` for sync and rebuild.

## 1. The three states, and the one that is not enough on its own

```
PENDING    waiting for a validator            no count
COUNTED    accepted; adds to the actual       count
EXCLUDED   out - and *why* decides the rest   no count
```

`EXCLUDED` alone cannot tell the projector whether it may restore the row, so
`pr_work_results.exclusion_kind` says which act wrote it:

| `exclusion_kind` | Written by | Label | Projection may restore? | Released by |
| --- | --- | --- | --- | --- |
| `VALIDATOR_REJECTED` | *Từ chối / Không ghi nhận* (`PR_WORK_VALIDATE`) | Đã từ chối | **No** | *Xem xét lại* (`PR_WORK_VALIDATE`) |
| `ADMIN_REMOVED` | *Xóa kết quả* (`PR_WORK_CONFIGURE`) | Đã xóa khỏi ghi nhận | **Yes** — re-evaluated against current source truth | any canonical projection |
| `SOURCE_REVERSED` | the projector (approval undone) or a rebuild (mapping moved) | Không còn đủ điều kiện | current source truth decides | the source, through the projector |
| `NULL` (legacy, pre-`0041`) | unknown — free text only | Đã loại bỏ | **No** — never guessed | *Xem xét lại* |

`excluded_reason` stays the free text a person typed (required for a
rejection); `excluded_by_user_id` / `excluded_at` say who and when.

## 2. The one convergence policy

`domain/pr/work_results.py::source_may_restore(kind)` is the whole rule, and
`PrWorkResultService.record_source_result` is the only place an excluded row is
restored. The worker, *Đồng bộ lại từ Nội dung*, *Đồng bộ thiếu*, *Xây dựng lại
từ Nội dung* and the projector's dry run all reach it through
`PrContentWorkProjector.project_content`; none restates it.

```
existing COUNTED            + source valid    → UNCHANGED
existing PENDING            + source valid    → stays PENDING unless the source is independently validated
existing COUNTED or PENDING + source withdrawn → REVERSED: EXCLUDED / SOURCE_REVERSED (the pending row is swept too)
existing ADMIN_REMOVED      + source valid    → re-evaluated: COUNTED (independent) or PENDING (self-validated)
existing SOURCE_REVERSED    + source valid    → re-evaluated the same way (the canonical redo)
existing VALIDATOR_REJECTED + anything        → HELD_BY_VALIDATOR: untouched, not refiled, not counted
existing NULL (legacy)      + anything        → HELD_BY_VALIDATOR
source invalid / withdrawn                    → out regardless; an already-excluded row keeps its kind
```

`HELD_BY_VALIDATOR` is a settled projection outcome ("the projector reached an
answer: leave it"); the maintenance preview files it as `CORRECT`. A rebuild's
removal step reads counted rows only, so a rebuild is never a way to erase a
rejection. There is no admin override flag: the release is *Xem xét lại*.

### Source truth wins over a stale row

A source-derived result may only become `COUNTED` while its source milestone
is live. Two layers guarantee it:

1. **The projector** sweeps a `PENDING` content row whose milestone vanished
   (an undone head approval) to `SOURCE_REVERSED`, exactly as it does a
   counted one (`_converge_orphan_results`).
2. **The validator's own path** does not trust a stale row. Before counting a
   source-derived `PENDING` result, `validate_results` asks the projector
   (`PrContentWorkProjector.source_is_eligible`, bound through
   `PrWorkResultService.bind_source_truth`) whether the milestone is still
   there. If not: a request that **named** the result is refused with
   `work_result_source_not_eligible` (HTTP 409, "Không thể xác nhận vì Nội
   dung nguồn hiện không còn đủ điều kiện ghi nhận.") and nothing is written;
   a request for *every pending result* converges the row to
   `SOURCE_REVERSED` (reported in `ResultBatch.reversed`) and counts the rest.
   *Xem xét lại* asks the same question and refuses to release a rejection
   into a pending state the source would not back; the rejection and its
   history stay. Manual results have no source and are never asked.

The read model carries `source_eligible` on pending source-derived rows (asked
only for a reader who could validate); `false` withholds `can_validate` /
`can_reject` and the screen shows *Không còn đủ điều kiện* on the row.

## 3. The acts

| Act | Capability | Not the subject | From | To | History | Audit |
| --- | --- | --- | --- | --- | --- | --- |
| Xác nhận | `PR_WORK_VALIDATE` | ✓ | PENDING | COUNTED | `RESULT_COUNTED` | `pr.work_result.counted` |
| Từ chối / Không ghi nhận (reason required) | `PR_WORK_VALIDATE` | ✓ | PENDING, COUNTED | EXCLUDED / VALIDATOR_REJECTED | `RESULT_REJECTED` | `pr.work_result.rejected` |
| Xem xét lại | `PR_WORK_VALIDATE` (any holder, not only the rejecter) | ✓ | EXCLUDED / VALIDATOR_REJECTED or NULL | PENDING | `RESULT_RECONSIDERED` | `pr.work_result.reconsidered` |
| Xóa kết quả | `PR_WORK_CONFIGURE` | — | PENDING, COUNTED | EXCLUDED / ADMIN_REMOVED | `RESULT_ADMIN_REMOVED` | `pr.work.result_admin_removed` |
| source reversal | the projector | — | COUNTED | EXCLUDED / SOURCE_REVERSED | `RESULT_EXCLUDED` | `pr.work_result.excluded` |

Every audit payload names `result_id`, `work_item_id`, `code`, `work_type_id`,
`reporting_period_id`, `subject_user_id`, `quantity`, `source_type`,
`source_key`, the old and new `status` and `exclusion_kind`, the note where
there is one, and the container's `actual_quantity` afterwards.

**Refusals.** *Xóa kết quả* on a validator-rejected row:
`work_result_validator_rejected` — "Kết quả đã bị từ chối bởi người xác nhận.
Hãy dùng 'Xem xét lại' trước khi thay đổi trạng thái." Otherwise an
already-excluded row: `work_result_not_admin_removable`. *Xem xét lại* on
anything but a rejection or a legacy exclusion:
`work_result_not_reconsiderable`. A closed or locked month refuses all three
validator acts as it refuses counting. The subject of the stream cannot
confirm, reject or reconsider their own results whatever they hold
(`self_validation`).

**Not merged.** `PR_WORK_CONFIGURE` alone gives no validation authority and
`PR_WORK_VALIDATE` alone gives no removal; ADMIN and OWNER hold both and act
under whichever the route checks.

## 4. Routes

| Route | Capability |
| --- | --- |
| `POST /api/pr/work/{id}/results/validate` (`result_ids` optional) | `PR_WORK_VALIDATE`, not the subject |
| `POST /api/pr/work/results/{id}/exclude` `{reason}` | `PR_WORK_VALIDATE`, not the subject |
| `POST /api/pr/work/results/{id}/reconsider` `{note?}` | `PR_WORK_VALIDATE`, not the subject |
| `POST /api/pr/work/maintenance/results/{id}/admin-remove` `{note?}` | `PR_WORK_CONFIGURE` |
| `POST /api/pr/work/content/{content_id}/project` | `PR_WORK_CONFIGURE` |

`WorkResultResponse` carries `exclusion_kind`, `exclusion_kind_label`,
`excluded_by_user_id` / `excluded_by_name`, `held_by_validator`, and the
per-row `can_validate` / `can_reject` / `can_reconsider` the server decided;
`status_label` already reads by kind.

## 5. The screen

A pending row offers **[Xác nhận] [Từ chối / Không ghi nhận]** to an authorised
validator; a counted row offers **[Loại bỏ]** (the same dialog, worded for a
take-back). *Từ chối* opens a dialog — "Từ chối kết quả?", the amount, a
required reason, [Hủy] [Từ chối] — and the confirm stays disabled until a
non-blank reason is typed. A rejected row shows *Đã từ chối*, "Lý do / Người
xử lý / Thời gian", and **[Xem xét lại]** behind a confirmation. A content row
a validator rejected offers **no** *Đồng bộ lại từ Nội dung* and no *Xóa kết
quả*; it says the sync would not restore it. An administratively removed
content row keeps *Đồng bộ lại từ Nội dung*, which is its way back.

## 6. Legacy rows and the migration

`0041` adds `exclusion_kind` (nullable, `VARCHAR(20)` value enum) and
`ck_pr_work_results_exclusion_kind_matches_status`
(`status = 'EXCLUDED' OR exclusion_kind IS NULL`). **Nothing is backfilled**:
the free-text prefixes are recognisable but not reliable, and the audit trail
would have to be matched by timestamp to tell a restored-then-rejected row
from a removed one. `NULL` means *legacy, author not recorded*, is held like a
rejection so no human decision is silently reversed, and is released with
*Xem xét lại*. `0039`–`0040` were not yet deployed when this was written, so
the affected population is expected to be empty. Downgrade drops the column;
every excluded row becomes restorable again, as under `0040`.

## 7. Concurrency (PostgreSQL, `tests/integration/test_pr_work_result_exclusion_pg.py`)

Every validator act locks the container row, as counting does, so two acts on
one result serialise: accept vs reject ends rejected and uncounted; two
rejections write one rejection; two acceptances count once; a rejection
racing the worker, a manual sync or the projector is never lost; a
reconsideration racing the projector converges on the source's answer at the
next pass; an administrator's removal racing a rejection ends either
`ADMIN_REMOVED` (removal first; the rejection found an excluded row) or
`VALIDATOR_REJECTED` with the removal refused — never a rejection turned
resyncable.
