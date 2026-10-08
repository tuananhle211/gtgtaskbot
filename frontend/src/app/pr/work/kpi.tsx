"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  api,
  type ContributionEligibility,
  type EligibilitySummary,
  type EmployeePlanSummary,
  type QuotaTypeProgress,
  type ReportingPeriod,
  type WorkPlanDetail,
  type WorkType,
} from "@/lib/api";
import { formatQuantity, formatRate, formatWhen } from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import {
  QuotaWorkloadBreakdown,
  QuotaWorkloadLine,
  WorkloadSummary,
  quotaWorkloadFor,
  workloadVariantFor,
} from "./workload";
import {
  approveWorkPlanConfirmation,
  approveWorkPlanRevisionConfirmation,
  discardWorkPlanConfirmation,
  reconcileEligibilityConfirmation,
  removeWorkQuotaConfirmation,
  returnWorkPlanConfirmation,
  reviseWorkPlanConfirmation,
  submitWorkPlanConfirmation,
} from "@/lib/confirmations";

/**
 * Kế hoạch KPI - the quota engine's screen. M2.
 *
 * ## What this screen is for, and what it deliberately does not show
 *
 * It answers *"how much of my counted work is inside an approved KPI quota"* -
 * and it shows **no point total of any kind**, because none exists. `COUNTED` is
 * M1's word for work that was independently validated; `ELIGIBLE` is M2's word
 * for counted work inside an approved cap. Whether either earns points is M6's
 * question, and the vocabulary here says so: *"Đủ điều kiện tính KPI"*, never
 * *"Đã được tính điểm"*.
 *
 * ## The three ideas the layout is built around
 *
 * **Four figures, not one.** Đã ghi nhận, Đủ điều kiện, Vượt hạn mức and Mục
 * tiêu are four different facts about one work type, and a row that showed a
 * single "KPI" number would have to pick one and hide the rest. The first three
 * always reconcile - counted = eligible + over quota + no quota - so a person
 * can check the row adds up.
 *
 * **Absence is not permission.** A work type with counted work and no approved
 * quota says *"Đã ghi nhận công việc, chưa có hạn mức KPI"* in words. It is not
 * shown as zero, not shown as a failure, and not shown as unlimited - it is a
 * decision nobody has taken, and the sentence sends the reader to the person who
 * can take it.
 *
 * **An approved plan is never edited.** The administrator half has no edit form
 * on an approved plan at all: the only control is *"Tạo bản điều chỉnh"*, and
 * the server refuses an edit either way. Hiding the form is a courtesy; the
 * refusal is the rule.
 *
 * ## Where the rules live
 *
 * Not here. Every label is a `*_label` from the server, every control is drawn
 * from a `can_*` flag the server computed from the same checks the writes make,
 * and every amount is a decimal string the server computed - the browser does no
 * quota arithmetic, because a second implementation of the allocation rule is
 * exactly what M2 exists to avoid.
 */

/** The employee half and the administrator half, behind one period picker. */
export function KpiWorkspace({
  mayConfigure,
  mayViewAll,
}: {
  mayConfigure: boolean;
  mayViewAll: boolean;
}) {
  const [periodId, setPeriodId] = useState<string>("");
  const periods = useQuery({ queryKey: ["work-periods"], queryFn: () => api.workPeriods() });
  // The newest month, until somebody picks another. Chosen here rather than
  // defaulted on the server because "this month" is a browsing convenience, and
  // a server that guessed would make a shared link mean different things on
  // different days.
  const selected = periodId || periods.data?.[0]?.id || "";
  const period = periods.data?.find((one) => one.id === selected);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-sm text-[var(--text-muted)]" htmlFor="kpi-period">
          Kỳ báo cáo
        </label>
        <Select
          id="kpi-period"
          value={selected}
          onChange={(event) => setPeriodId(event.target.value)}
          aria-label="Kỳ báo cáo"
          className="text-sm"
        >
          {(periods.data ?? []).map((one) => (
            <option key={one.id} value={one.id}>
              {one.code}
            </option>
          ))}
        </Select>
        {period ? <PeriodBadge period={period} /> : null}
        {mayConfigure ? <OpenPeriodForm onOpened={setPeriodId} /> : null}
      </div>

      {periods.isPending ? <Loading label="Đang tải kỳ báo cáo…" /> : null}
      {periods.isError ? (
        <ErrorBox error={periods.error} onRetry={() => periods.refetch()} />
      ) : null}
      {periods.data?.length === 0 ? (
        <Empty
          message={
            mayConfigure
              ? "Chưa có kỳ báo cáo nào. Mở một tháng để bắt đầu lập kế hoạch KPI."
              : "Chưa có kỳ báo cáo nào. Quản trị viên cần mở kỳ trước."
          }
        />
      ) : null}

      {/*
        M3.1 moved the Content → Work mapping out of this view and into
        *Cấu hình*, beside the work types it selects from - see
        `./mapping.tsx`. It never belonged here: a mapping has nothing to do
        with a reporting period, and it sat two tabs away from the other half
        of the same setup.
      */}

      {selected && period ? <MyKpiProposal periodId={selected} period={period} /> : null}
      {selected ? <MyKpiPlan periodId={selected} /> : null}
      {selected && mayConfigure ? (
        <PlanAdministration periodId={selected} period={period} mayViewAll={mayViewAll} />
      ) : null}
    </div>
  );
}

/**
 * How far beyond change a period has been put.
 *
 * Shown next to the picker rather than buried, because it is the difference
 * between "these figures still move" and "these figures were agreed" - and
 * somebody reading a closed month's numbers needs to know a recompute will not
 * change them.
 */
function PeriodBadge({ period }: { period: ReportingPeriod }) {
  if (period.status === "OPEN") return <Pill>Đang mở</Pill>;
  return (
    <Pill tone="warn">
      {period.status === "LOCKED" ? "Đã khóa" : "Đã chốt"} · không tính lại
    </Pill>
  );
}

// --- The employee view ------------------------------------------------------

/**
 * *Chi tiết tải KPI* - which quotas make the figure above, on request.
 *
 * The list row carries the summary only; the breakdown is the plan detail's,
 * fetched when somebody opens this and not before. Every line is the server's
 * `QuotaWorkload`, printed.
 */
function PlanWorkloadDetails({ planId }: { planId: string }) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["work-plan", planId],
    queryFn: () => api.workPlan(planId),
    enabled: open,
  });
  return (
    <details
      className="mt-1 text-xs"
      open={open}
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-[var(--text-muted)]">Chi tiết tải KPI</summary>
      {detail.isPending && open ? <Loading label="Đang tải chi tiết…" /> : null}
      {detail.isError ? <ErrorBox error={detail.error} onRetry={() => detail.refetch()} /> : null}
      {detail.data ? (
        <div className="mt-1">
          <QuotaWorkloadBreakdown workload={detail.data.workload} />
        </div>
      ) : null}
    </details>
  );
}

/**
 * **The employee's own KPI, in its four states.** KPI self-service.
 *
 * Every state is the server's reading of `plans/mine/summary`: whether there
 * is a plan at all, whether a draft is being written, submitted or returned,
 * and whether an approved plan is in force beside it. This component draws
 * what it is told and calls the route for the one act each state offers -
 * *Tạo KPI của tôi*, *Đề xuất điều chỉnh*, *Gửi duyệt* - and never decides on
 * its own that a proposal is ready or in force.
 *
 * The approved plan and a pending revision are **two cards**, never one: the
 * first says what counts this month, the second what has been proposed, and
 * collapsing them is how a submitted proposal would be read as already in
 * force.
 */
function MyKpiProposal({ periodId, period }: { periodId: string; period: ReportingPeriod }) {
  const queryClient = useQueryClient();
  const summary = useQuery({
    queryKey: ["work-plan-mine-summary", periodId],
    queryFn: () => api.myWorkPlanSummary(periodId),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["work-plan-mine-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plan-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plan-history"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plan"] });
    void queryClient.invalidateQueries({ queryKey: ["work-eligibility-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-eligibility"] });
  };
  const create = useMutation({
    mutationFn: () => api.selfCreateWorkPlan({ period_id: periodId }),
    onSuccess: refresh,
    // Somebody (a manager, or this person in another tab) already started
    // one: the server names it, and the summary refresh shows it.
    onError: (failure) => {
      if (planConflict(failure)) refresh();
    },
  });
  const revise = useMutation({
    mutationFn: (planId: string) => api.reviseWorkPlan(planId),
    onSuccess: refresh,
    onError: (failure) => {
      if (planConflict(failure)) refresh();
    },
  });

  if (summary.isPending) return <Loading label="Đang tải kế hoạch KPI của bạn…" />;
  if (summary.isError)
    return <ErrorBox error={summary.error} onRetry={() => summary.refetch()} />;
  const row = summary.data;
  const open = period.status === "OPEN";
  const approved = row.current_status === "APPROVED";
  const draftId = row.latest_draft_id;
  const state = row.draft_review_state;

  return (
    <section aria-label="KPI của tôi" className="space-y-3">
      {/* STATE 1 - nothing yet. */}
      {!row.has_plan ? (
        <div className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface)] p-3">
          <p className="text-sm">Chưa có kế hoạch KPI cho kỳ {period.code}.</p>
          {open ? (
            <button
              type="button"
              onClick={() => create.mutate()}
              disabled={create.isPending}
              className="mt-2 min-h-11 w-full rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] sm:w-auto"
            >
              + Tạo KPI của tôi
            </button>
          ) : (
            <p className="mt-1 text-xs text-[var(--text-muted)]">Kỳ này đã đóng.</p>
          )}
          {create.isError && !planConflict(create.error) ? <ErrorBox error={create.error} /> : null}
        </div>
      ) : null}

      {/* STATE 4 - a plan in force, offered a revision when none is in flight. */}
      {approved && row.current_plan_id ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
          <span className="text-sm font-medium">
            Kế hoạch đang áp dụng · bản v{row.current_version_no}
          </span>
          <Pill tone="good">Đang áp dụng</Pill>
          <span className="text-xs text-[var(--text-muted)]">{row.quota_count} chỉ tiêu</span>
          <div className="grow" />
          {/* KPI workload visibility. What the plan in force asks of the
              month, priced by the server; the draft below carries its own. */}
          <div className="basis-full">
            <WorkloadSummary workload={row.current_workload} variant="applied" />
            <PlanWorkloadDetails planId={row.current_plan_id} />
          </div>
          {!draftId && open ? (
            <button
              type="button"
              onClick={() => revise.mutate(row.current_plan_id!)}
              disabled={revise.isPending}
              className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
            >
              Đề xuất điều chỉnh
            </button>
          ) : null}
          {revise.isError && !planConflict(revise.error) ? <ErrorBox error={revise.error} /> : null}
        </div>
      ) : null}

      {/* STATES 2, 3 and the returned case - the draft, in whichever state it is. */}
      {draftId ? (
        <MyDraftCard
          draftId={draftId}
          versionNo={row.draft_version_no ?? 0}
          state={state}
          isRevision={approved}
          period={period}
          submittedAt={row.draft_submitted_at}
          returnNote={row.draft_return_note}
          onChanged={refresh}
        />
      ) : null}
    </section>
  );
}

/**
 * The employee's draft: editable, submitted, or returned.
 *
 * What is editable is the server's `can_edit`, what may be sent is its
 * `can_submit`, and why not is `readiness_blocker_labels` - shown under a
 * disabled button rather than hiding the button, so an empty draft explains
 * itself. A submitted draft renders its quotas read-only and says when it was
 * sent; a returned one leads with the manager's note.
 */
function MyDraftCard({
  draftId,
  versionNo,
  state,
  isRevision,
  period,
  submittedAt,
  returnNote,
  onChanged,
}: {
  draftId: string;
  versionNo: number;
  state: string | null;
  isRevision: boolean;
  period: ReportingPeriod;
  submittedAt: string | null;
  returnNote: string | null;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["work-plan", draftId], queryFn: () => api.workPlan(draftId) });
  const settle = (next: WorkPlanDetail) => {
    queryClient.setQueryData(["work-plan", next.plan.id], next);
    onChanged();
  };
  const submit = useMutation({ mutationFn: () => api.submitWorkPlan(draftId), onSuccess: settle });
  const discard = useMutation({ mutationFn: () => api.discardWorkPlan(draftId), onSuccess: settle });
  const data = detail.data;
  const submitted = state === "SUBMITTED";
  const returned = state === "RETURNED";
  const heading = isRevision ? `Bản điều chỉnh · v${versionNo}` : `Kế hoạch KPI · bản v${versionNo}`;

  return (
    <section
      aria-label={heading}
      className={`rounded-lg border p-3 ${
        submitted
          ? "border-amber-500/40 bg-amber-500/5"
          : returned
            ? "border-orange-500/40 bg-orange-500/5"
            : "border-[var(--border)] bg-[var(--surface)]"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{heading}</h3>
        {submitted ? (
          <Pill tone="warn">Chờ trưởng phòng duyệt</Pill>
        ) : returned ? (
          <Pill tone="warn">Cần chỉnh sửa</Pill>
        ) : (
          <Pill>Bản nháp</Pill>
        )}
        {submitted && submittedAt ? (
          <span className="text-xs text-[var(--text-muted)]">Gửi lúc {formatWhen(submittedAt)}</span>
        ) : null}
      </div>
      {returned && returnNote ? (
        <p className="mt-2 rounded border border-orange-500/40 bg-[var(--surface)] p-2 text-xs">
          <span className="text-[var(--text-muted)]">Lý do: </span>“{returnNote}”
        </p>
      ) : null}
      {isRevision ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Kế hoạch đang áp dụng vẫn giữ nguyên hiệu lực cho tới khi bản này được duyệt.
        </p>
      ) : submitted ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Chưa có kế hoạch nào có hiệu lực cho tới khi bản này được duyệt.
        </p>
      ) : null}

      {detail.isPending ? <Loading label="Đang tải bản nháp…" /> : null}
      {detail.isError ? (
        <ErrorBox error={detail.error} onRetry={() => void detail.refetch()} />
      ) : null}

      {data ? (
        <div className="mt-2 space-y-2">
          <PlanEditor
            planId={draftId}
            onChanged={onChanged}
            onOpenPlan={() => undefined}
            showLifecycle={false}
          />
          {!submitted ? (
            // Stacked on a phone, in a row on a desktop; the primary act last
            // so it sits under the thumb.
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
              {data.can_discard ? (
                <ConfirmButton
                  spec={discardWorkPlanConfirmation(versionNo)}
                  tone="secondary"
                  pending={discard.isPending}
                  error={discard.error}
                  onConfirm={() => discard.mutate()}
                >
                  Bỏ bản nháp
                </ConfirmButton>
              ) : null}
              {data.can_submit ? (
                <ConfirmButton
                  spec={submitWorkPlanConfirmation(versionNo, period.code)}
                  pending={submit.isPending}
                  error={submit.error}
                  onConfirm={() => submit.mutate()}
                >
                  {returned ? "Gửi lại duyệt" : "Gửi duyệt"}
                </ConfirmButton>
              ) : (
                <button
                  type="button"
                  disabled
                  className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] opacity-50"
                >
                  {returned ? "Gửi lại duyệt" : "Gửi duyệt"}
                </button>
              )}
            </div>
          ) : null}
          {!submitted && !data.can_submit && data.readiness_blocker_labels.length > 0 ? (
            <ul className="text-xs text-[var(--text-muted)]">
              {data.readiness_blocker_labels.map((label) => (
                <li key={label}>{label}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/**
 * *Kế hoạch KPI* - one person's own targets and progress for one month.
 *
 * Reads `/eligibility/summary`, which is a **read**: it materialises nothing, so
 * opening this screen cannot change anybody's figures. A contribution that
 * nothing has evaluated yet is reported as `PENDING_EVALUATION` - *there is a
 * target and nothing has looked at this* - and **not** as `NO_QUOTA`, which
 * would tell somebody their manager set no target when their manager did.
 */
function MyKpiPlan({ periodId }: { periodId: string }) {
  const summary = useQuery({
    queryKey: ["work-eligibility-summary", periodId],
    queryFn: () => api.workEligibilitySummary({ period_id: periodId }),
  });

  if (summary.isPending) return <Loading label="Đang tải kế hoạch KPI…" />;
  if (summary.isError)
    return <ErrorBox error={summary.error} onRetry={() => summary.refetch()} />;

  const data = summary.data;
  return (
    <section aria-label="Kế hoạch KPI" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold">Kế hoạch KPI</h2>
        <p className="text-xs text-[var(--text-muted)]">
          {data.plan_id
            ? `Kế hoạch đang áp dụng · bản v${data.plan_version_no}`
            : "Chưa có kế hoạch KPI được duyệt cho kỳ này."}
        </p>
      </div>

      {data.types.length === 0 ? (
        <Empty message="Chưa có công việc nào được ghi nhận trong kỳ này." />
      ) : (
        <ul className="space-y-2">
          {data.types.map((row) => (
            <li key={row.work_type_id}>
              <QuotaProgressCard row={row} />
            </li>
          ))}
        </ul>
      )}

      <SummaryFooter summary={data} />
      <EligibilityBreakdown periodId={periodId} />
    </section>
  );
}

/**
 * One work type's four figures.
 *
 * `Mục tiêu`, `Đã COUNTED`, `Đủ điều kiện` and `Vượt hạn mức` - written out as
 * four rather than combined, because they are four different facts and the
 * arithmetic between them is the thing a person checks. Amounts are rendered
 * through `formatQuantity`, so a `QUANTITY` row reads "3.000 bình luận" and an
 * `ITEM_COUNT` row reads "20".
 *
 * **No point total, and nowhere to put one.**
 */
function QuotaProgressCard({ row }: { row: QuotaTypeProgress }) {
  const amount = (value: string) => formatQuantity(value, row.unit_label) ?? value;
  const hasQuota = row.target_value !== null;
  // How many of this person's shares have no amount at all. The totals below
  // are over the rest, and saying so is the honest alternative to folding a
  // missing quantity in as zero.
  const unmeasured = row.counted_contributions - row.measured_contributions;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{row.work_type_name}</p>
        <Pill>{row.basis_label}</Pill>
      </div>

      {hasQuota ? (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
          <Figure label="Mục tiêu" value={amount(row.target_value ?? "0")} />
          <Figure label="Đã ghi nhận" value={amount(row.counted_amount)} />
          <Figure label="Đủ điều kiện" value={amount(row.eligible_amount)} tone="good" />
          <Figure
            label="Vượt hạn mức"
            value={amount(row.over_quota_amount)}
            tone={Number(row.over_quota_amount) > 0 ? "warn" : undefined}
          />
        </dl>
      ) : (
        <div className="mt-2 space-y-1">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <Figure label="Đã ghi nhận" value={amount(row.counted_amount)} />
            <Figure label="Số đầu việc" value={String(row.counted_contributions)} />
          </dl>
          {/*
            The sentence the milestone turns on. Not "0 điểm" and not "không
            đạt": the work is real and in the workload, and what is missing is a
            decision by somebody with the authority to make one.
          */}
          <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
            Đã ghi nhận công việc, chưa có hạn mức KPI.
          </p>
        </div>
      )}

      {hasQuota ? (
        <>
          {/*
            Period-container patch. **The KPI comparison, uncapped.** The whole
            counted amount against the target - "27 / 20 · 135% · +7 vượt chỉ
            tiêu" - as the server computed it. The bar is the server's capped
            `progress_percent` idea applied here: a length stops at full, the
            number does not.
          */}
          <p className="mt-2 text-xs font-medium">
            Thực tế {amount(row.counted_amount)} / {amount(row.target_value ?? "0")}
            {row.completion_percent ? ` · ${formatRate(row.completion_percent)}%` : ""}
            {Number(row.over_target_amount) > 0
              ? ` · +${amount(row.over_target_amount)} vượt chỉ tiêu`
              : Number(row.remaining_amount) > 0
                ? ` · còn ${amount(row.remaining_amount)}`
                : ""}
          </p>
          <div
            className="mt-1 h-1.5 w-full overflow-hidden rounded bg-[var(--border)]"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={row.is_target_met ? 100 : Number(row.completion_percent ?? "0")}
            aria-label="Tiến độ so với KPI"
          >
            <div
              className={`h-full ${row.is_target_met ? "bg-emerald-500" : "bg-[var(--text)]"}`}
              style={{
                width: row.is_target_met ? "100%" : `${row.completion_percent ?? "0"}%`,
              }}
            />
          </div>
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Trần hạn mức {amount(row.eligibility_cap ?? "0")}
            {" · "}
            Đạt mục tiêu {amount(row.target_progress)} / {amount(row.target_value ?? "0")}
            {Number(row.extra_eligible_above_target) > 0
              ? ` · Đủ điều kiện thêm ngoài mục tiêu ${amount(row.extra_eligible_above_target)}`
              : ""}
          </p>
        </>
      ) : null}

      {/*
        The rows the figures above leave out, said in words rather than folded
        in as zero. Two different sentences on purpose: one is a form somebody
        has to fill in, the other is a recompute an administrator has to run.
      */}
      {unmeasured > 0 ? (
        <p className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          {row.unmeasurable_contributions > 0 ? (
            <>
              Chưa thể tính hạn mức cho{" "}
              <strong>{row.unmeasurable_contributions}</strong> phần việc — thiếu dữ liệu
              trên đầu việc. Công việc vẫn được ghi nhận đầy đủ.
            </>
          ) : null}
          {row.unmeasurable_contributions > 0 && row.pending_contributions > 0 ? " " : null}
          {row.pending_contributions > 0 ? (
            <>
              <strong>{row.pending_contributions}</strong> phần việc chưa được tính lại
              theo hạn mức của kỳ này.
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

function Figure({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "warn";
}) {
  const colour =
    tone === "good"
      ? "text-emerald-700 dark:text-emerald-300"
      : tone === "warn"
        ? "text-amber-700 dark:text-amber-300"
        : "";
  return (
    <div>
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd className={`text-sm font-semibold ${colour}`}>{value}</dd>
    </div>
  );
}

/**
 * The cross-type figures, and the one sentence explaining why there is no total.
 *
 * Counts of contributions only. Summing COMMENT + VIDEO + DAY would produce a
 * number that is not a quantity of anything, so the strip refuses to have one.
 */
function SummaryFooter({ summary }: { summary: EligibilitySummary }) {
  const byStatus = summary.contributions_by_status;
  return (
    <p className="text-xs text-[var(--text-muted)]">
      Trong kỳ: <strong>{summary.counted_work_items}</strong> đầu việc ·{" "}
      <strong>{summary.counted_contributions}</strong> phần việc được ghi nhận · đủ điều
      kiện <strong>{byStatus.ELIGIBLE ?? 0}</strong> · đủ một phần{" "}
      <strong>{byStatus.PARTIALLY_ELIGIBLE ?? 0}</strong> · vượt hạn mức{" "}
      <strong>{byStatus.OVER_QUOTA ?? 0}</strong> · chưa có hạn mức{" "}
      <strong>{byStatus.NO_QUOTA ?? 0}</strong> · chưa thể tính hạn mức{" "}
      {/* Reported separately, never merged into "chưa có hạn mức": one means
          nobody set a target and the other means a number is missing. */}
      <strong>{byStatus.UNMEASURABLE ?? 0}</strong>
      {(byStatus.PENDING_EVALUATION ?? 0) > 0 ? (
        <>
          {" "}
          · chưa tính <strong>{byStatus.PENDING_EVALUATION}</strong>
        </>
      ) : null}
      . Các loại việc dùng đơn vị khác nhau nên không cộng gộp thành một tổng số lượng.
    </p>
  );
}

/** The per-contribution list, behind a disclosure. The "why is my number that" view. */
function EligibilityBreakdown({ periodId }: { periodId: string }) {
  const [open, setOpen] = useState(false);
  const rows = useQuery({
    queryKey: ["work-eligibility", periodId],
    queryFn: () => api.workEligibility({ period_id: periodId }),
    enabled: open,
  });
  return (
    <details
      className="rounded border border-[var(--border)] p-2"
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-xs font-semibold">
        Chi tiết từng đầu việc
      </summary>
      {rows.isPending && open ? <Loading label="Đang tải chi tiết…" /> : null}
      {rows.isError ? <ErrorBox error={rows.error} /> : null}
      <ul className="mt-2 space-y-1">
        {rows.data?.contributions.map((one) => (
          <li key={one.contribution_id} className="flex flex-wrap items-center gap-2 text-xs">
            <code className="text-[10px] text-[var(--text-muted)]">{one.work_item_code}</code>
            <span className="min-w-0 flex-1 truncate">{one.work_item_title}</span>
            <EligibilityBadge row={one} />
            <span className="text-[10px] text-[var(--text-muted)]">
              {formatWhen(one.counted_at)}
            </span>
          </li>
        ))}
      </ul>
      {rows.data?.contributions.length === 0 ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Chưa có phần việc nào được ghi nhận trong kỳ.
        </p>
      ) : null}
    </details>
  );
}

/**
 * The subtle badge on one contribution.
 *
 * Green only for work actually inside an approved cap. `OVER_QUOTA` and
 * `UNMEASURABLE` are amber rather than red: in both, the work was done
 * correctly and was counted in full - what is wrong is a cap that ran out or a
 * field nobody filled in - and painting either as a failure would be telling
 * somebody off for doing their job.
 *
 * **Every word is the server's.** `quota_status_label` and `reason_label` are
 * composed server-side, so the browser never invents an eligibility sentence and
 * the six states cannot drift into five here.
 */
export function EligibilityBadge({ row }: { row: ContributionEligibility }) {
  const tone =
    row.quota_status === "ELIGIBLE"
      ? "good"
      : row.quota_status === "PARTIALLY_ELIGIBLE" ||
          row.quota_status === "OVER_QUOTA" ||
          row.quota_status === "UNMEASURABLE"
        ? "warn"
        : "neutral";
  // The split, but only when both halves exist. A partial row always has them;
  // guarding anyway keeps a null from being printed as "null".
  const detail =
    row.quota_status === "PARTIALLY_ELIGIBLE" &&
    row.eligible_amount !== null &&
    row.basis_amount !== null
      ? ` ${formatQuantity(row.eligible_amount, row.unit_label) ?? row.eligible_amount}/${
          formatQuantity(row.basis_amount, row.unit_label) ?? row.basis_amount
        }`
      : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Pill tone={tone}>
        {row.quota_status_label}
        {detail}
      </Pill>
      {/* Why, when the server knows. Never an exception message - `reason_label`
          is composed from a stable code. */}
      {row.reason_label ? (
        <span className="text-[10px] text-[var(--text-muted)]">{row.reason_label}</span>
      ) : null}
    </span>
  );
}

// --- The administrator view -------------------------------------------------

/**
 * Plan configuration. `PR_WORK_CONFIGURE` only - ADMIN and OWNER.
 *
 * Deliberately **not** offered to a Trưởng nhóm: `PR_WORK_MANAGE` means deciding
 * whose job a piece of work is, which is a different act from deciding what
 * somebody's KPI targets are, and TasksBot models no team that would make a
 * narrower middle ground honest. The server refuses either way; this list simply
 * does not draw a control that would always fail.
 */
function PlanAdministration({
  periodId,
  period,
  mayViewAll,
}: {
  periodId: string;
  period: ReportingPeriod | undefined;
  mayViewAll: boolean;
}) {
  const queryClient = useQueryClient();
  const [openPlanId, setOpenPlanId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  /**
   * KPI self-service. Which rows the manager is looking at. The figures on the
   * tabs are the server's `counts`; the rows are filtered by the server's own
   * `draft_review_state` and `current_status` - nothing here works out whether
   * a draft is "pending" from a timestamp.
   */
  const [view, setView] = useState<ReviewView>("ALL");

  /**
   * **One row per employee**, from the server. Post-M4.
   *
   * This used to be `api.workPlans` - the plan *version* list - so somebody on
   * their third revision rendered as three cards, of which two were history,
   * and an employee with no plan rendered as nothing at all. Which version is
   * current is the server's decision, not a `.find()` in this file.
   */
  const plans = useQuery({
    queryKey: ["work-plan-summary", periodId],
    queryFn: () => api.workPlanSummary(periodId),
    enabled: mayViewAll,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["work-plan-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plan-history"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plans"] });
    void queryClient.invalidateQueries({ queryKey: ["work-plan"] });
    void queryClient.invalidateQueries({ queryKey: ["work-eligibility-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-eligibility"] });
  };

  const reconcile = useMutation({
    mutationFn: () => api.reconcileWorkEligibility({ period_id: periodId }),
    onSuccess: refresh,
  });

  return (
    <section aria-label="Quản lý kế hoạch KPI" className="space-y-3 border-t pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Quản lý kế hoạch KPI</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setCreating((was) => !was)}
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            {creating ? "Đóng" : "Tạo kế hoạch"}
          </button>
          {/*
            Confirmed, because it can move a contribution between "chưa có hạn
            mức" and "vượt hạn mức" - and somebody looking at their own figures
            deserves to know a click did that. Hidden on a closed period, which
            the server refuses anyway.
          */}
          {period?.status === "OPEN" ? (
            <ConfirmButton
              spec={reconcileEligibilityConfirmation(period.code)}
              tone="secondary"
              pending={reconcile.isPending}
              error={reconcile.error}
              onConfirm={() => reconcile.mutate()}
            >
              Tính lại điều kiện KPI
            </ConfirmButton>
          ) : null}
        </div>
      </div>

      {reconcile.data ? (
        <p className="rounded border border-[var(--border)] p-2 text-xs">
          Đã tính lại kỳ {reconcile.data.period_code} cho {reconcile.data.users} nhân sự:{" "}
          {reconcile.data.created} mới, {reconcile.data.updated} cập nhật,{" "}
          {reconcile.data.removed} gỡ bỏ
          {reconcile.data.unmeasurable > 0
            ? ` · ${reconcile.data.unmeasurable} phần việc chưa đo được (loại việc tính theo số lượng nhưng đầu việc không có số lượng)`
            : ""}
          .
        </p>
      ) : null}
      {reconcile.isError ? <ErrorBox error={reconcile.error} /> : null}

      {creating ? (
        <CreatePlanForm
          periodId={periodId}
          onCreated={(id) => {
            setCreating(false);
            setOpenPlanId(id);
            refresh();
          }}
          onConflict={(id) => {
            setOpenPlanId(id);
            refresh();
          }}
        />
      ) : null}

      {plans.isPending && mayViewAll ? <Loading label="Đang tải kế hoạch…" /> : null}
      {plans.isError ? <ErrorBox error={plans.error} onRetry={() => plans.refetch()} /> : null}
      {plans.data?.items.length === 0 ? (
        <Empty message="Chưa có nhân sự nào đang hoạt động." />
      ) : null}

      {plans.data ? (
        <div role="tablist" aria-label="Trạng thái kế hoạch" className="flex flex-wrap gap-1">
          {REVIEW_VIEWS.map((entry) => {
            const count = reviewCount(entry.key, plans.data.counts, plans.data.items.length);
            const active = view === entry.key;
            return (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setView(entry.key)}
                className={`min-h-11 rounded-full border px-3 text-sm ${
                  active
                    ? "border-[var(--text)] bg-[var(--text)] text-[var(--surface)]"
                    : "border-[var(--border)] bg-[var(--surface)]"
                }`}
              >
                {entry.label}
                {count !== null ? ` ${count}` : ""}
              </button>
            );
          })}
        </div>
      ) : null}
      {plans.data && view !== "ALL" && plans.data.items.filter(matchesView(view)).length === 0 ? (
        <Empty message={REVIEW_VIEWS.find((one) => one.key === view)?.empty ?? ""} />
      ) : null}

      <ul className="space-y-2">
        {plans.data?.items.filter(matchesView(view)).map((row) => (
          <li key={row.user_id}>
            <EmployeePlanRow
              row={row}
              periodId={periodId}
              open={openPlanId === row.user_id}
              onToggle={() => setOpenPlanId(openPlanId === row.user_id ? null : row.user_id)}
              onCreated={refresh}
            />
            {openPlanId === row.user_id && row.current_plan_id ? (
              <EmployeePlanDetail
                userId={row.user_id}
                periodId={periodId}
                planId={row.current_plan_id}
                onChanged={refresh}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One employee's row. **The management entity on this screen.**
 *
 * Prints the current plan's version and quota count, or *"Chưa có kế hoạch"* and
 * a control to start one. Everything on it comes from the server's summary -
 * this component decides nothing about which version is current, and there is
 * nowhere in it that could.
 */
function EmployeePlanRow({
  row,
  periodId,
  open,
  onToggle,
  onCreated,
}: {
  row: EmployeePlanSummary;
  periodId: string;
  open: boolean;
  onToggle: () => void;
  onCreated: () => void;
}) {
  const create = useMutation({
    mutationFn: () => api.createWorkPlan({ user_id: row.user_id, period_id: periodId }),
    onSuccess: onCreated,
  });

  if (!row.has_plan) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface)] p-3">
        <span className="text-sm font-medium">{row.user_name}</span>
        <span className="text-xs text-[var(--text-muted)]">Chưa có kế hoạch · 0 hạn mức</span>
        <div className="grow" />
        {/*
          Named for the person. The toolbar has its own "Tạo kế hoạch" that asks
          who first; this one already knows, and two controls sharing an
          accessible name would tell a screen-reader user nothing about which
          row they are on.
        */}
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending}
          aria-label={`Tạo kế hoạch cho ${row.user_name}`}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          Tạo kế hoạch
        </button>
        {create.isError ? <ErrorBox error={create.error} /> : null}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-left"
    >
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{row.user_name}</span>
        <Pill tone={row.current_status === "APPROVED" ? "good" : "neutral"}>
          {row.current_status_label}
        </Pill>
        <span className="text-xs text-[var(--text-muted)]">
          Kế hoạch hiện tại: v{row.current_version_no} · {row.quota_count} hạn mức
        </span>
      </p>
      {/*
        KPI workload visibility. **The figure a manager scans the list by**,
        on the row rather than behind it: the plan in force's minutes against
        the person's month, from the batched list response - never fetched per
        row, never computed here. A draft's figure is printed on its own line
        below, under its own label; the two are never added.
      */}
      <div className="mt-1">
        <WorkloadSummary
          workload={row.current_workload}
          variant={workloadVariantFor(row.current_status, null)}
          compact
        />
      </div>
      {/*
        **The draft, on the row.** Shown only when it is a *different* row from
        the current plan - with no approved version the draft already is the
        plan above, and saying it twice would read as two of them.

        Here rather than only inside the detail because a manager should not
        have to open an accordion to discover that somebody left a revision
        half-written. That discovery problem is the whole bug.
      */}
      {row.latest_draft_id ? (
        <p className="mt-1 flex flex-wrap items-center gap-1 text-xs">
          {row.latest_draft_id !== row.current_plan_id ? (
            <span className="text-[var(--text-muted)]">Bản điều chỉnh:</span>
          ) : null}
          <span className="font-medium">v{row.draft_version_no}</span>
          {/* KPI self-service. The draft's standing between author and
              approver, in the server's words. A submitted draft is the one
              thing on this row a manager is being asked to act on. */}
          <Pill tone={row.draft_review_state === "SUBMITTED" ? "warn" : "neutral"}>
            {row.draft_review_state_label ?? "Bản nháp"}
          </Pill>
          <span className="text-[var(--text-muted)]">· {row.draft_quota_count} chỉ tiêu</span>
          {row.draft_is_submitted ? (
            <span className="font-medium">· Xem &amp; duyệt</span>
          ) : null}
        </p>
      ) : null}
      {row.latest_draft_id && row.latest_draft_id !== row.current_plan_id ? (
        <div className="mt-1">
          <WorkloadSummary
            workload={row.draft_workload}
            variant={workloadVariantFor(null, row.draft_review_state)}
            compact
          />
        </div>
      ) : null}
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {row.approved_at ? `Duyệt ${formatWhen(row.approved_at)}` : "Chưa duyệt"}
        {/*
          The version count, and only when there is more than one. "1 phiên bản"
          on every first plan is noise; "3 phiên bản" is the thing that tells
          somebody there is a story behind this month's numbers.

          `history_count` is every version. The detail screen's *"Lịch sử thay
          đổi (N)"* counts something narrower - the versions that are over - and
          the two are deliberately different figures under different labels.
        */}
        {row.history_count > 1 ? ` · ${row.history_count} phiên bản` : ""}
      </p>
    </button>
  );
}

/**
 * One employee's KPI detail. **Three sections, and they are not peers.**
 *
 * *Kế hoạch hiện tại* is the plan in force. *Bản điều chỉnh đang soạn* is the
 * revision being written, when there is one. *Lịch sử thay đổi* is the versions
 * that are over.
 *
 * The bug this shape exists to fix: the screen used to render **one** editor -
 * the current plan - and filter everything else into the history accordion on
 * `is_current`. With an approved v3 and a draft v4 that put v4 in history,
 * where nothing acts on it. The only control on screen was *"Tạo bản điều
 * chỉnh"* over v3, which the server refuses because v4 already exists, so the
 * one visible action led to an error and the draft it collided with could not
 * be continued, approved or discarded from anywhere. Escaping needed a database.
 *
 * A draft is **not history**. It is the thing somebody is in the middle of.
 */
function EmployeePlanDetail({
  userId,
  periodId,
  planId,
  onChanged,
}: {
  userId: string;
  periodId: string;
  planId: string;
  onChanged: () => void;
}) {
  const [openPlanId, setOpenPlanId] = useState(planId);
  const history = useQuery({
    queryKey: ["work-plan-history", userId, periodId],
    queryFn: () => api.workPlanHistory(userId, periodId),
  });
  const entries = history.data?.items ?? [];
  // Both flags come from the server, which owns the lifecycle: `is_current` is
  // the plan in force, `is_active_draft` the revision in flight, and with a
  // revision under way they are two different rows. History is what is neither.
  const draft = entries.find((one) => one.is_active_draft) ?? null;
  const past = entries.filter((one) => !one.is_current && !one.is_active_draft);
  // When no approved version exists the draft *is* the current plan, and the
  // editor above already is it - a second panel would be the same row twice.
  const separateDraft = draft && !draft.is_current ? draft : null;

  return (
    <div className="space-y-2">
      <PlanEditor
        planId={openPlanId}
        onChanged={onChanged}
        onOpenPlan={setOpenPlanId}
        onDraftConflict={(id) => {
          void history.refetch();
          setOpenPlanId(id);
        }}
      />

      {separateDraft ? (
        <ActiveDraftPanel
          draftId={separateDraft.id}
          versionNo={separateDraft.version_no}
          onChanged={() => {
            void history.refetch();
            onChanged();
          }}
        />
      ) : null}

      {past.length > 0 ? (
        <details className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3">
          <summary className="cursor-pointer text-xs font-semibold">
            {/* The active draft is deliberately not in this figure. Counting a
                revision somebody is writing as "history" is exactly the framing
                that made it unreachable. */}
            Lịch sử thay đổi ({past.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {past.map((one) => (
              <li key={one.id} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium">v{one.version_no}</span>
                <span className="text-[var(--text-muted)]">{one.status_label}</span>
                <span className="text-[var(--text-muted)]">{one.quota_count} hạn mức</span>
                {one.approved_at ? (
                  <span className="text-[var(--text-muted)]">
                    Duyệt {formatWhen(one.approved_at)}
                  </span>
                ) : null}
                {one.superseded_at ? (
                  <span className="text-[var(--text-muted)]">
                    Thay thế {formatWhen(one.superseded_at)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/**
 * The server's own reason for refusing a plan-creation call, or `null`.
 *
 * Keyed on `details.reason`, the structured code the plan service sends beside
 * the message. The English sentence is a message, not an interface, and
 * matching on it would break the day somebody improved the wording - which is
 * precisely the day a Vietnamese user would be shown it raw.
 *
 * The two codes lead to **two different recoveries**, which is why they are
 * separate values on the wire and separate branches here:
 *
 * * `draft_already_exists` - somebody has already started the revision.
 *   *Continue it*;
 * * `approved_plan_requires_revision` - a plan is in force and `create_plan` is
 *   not how it changes. *Start a revision*.
 */
type PlanConflict = "draft_already_exists" | "approved_plan_requires_revision";

function planConflict(failure: unknown): PlanConflict | null {
  if (!(failure instanceof ApiError)) return null;
  const reason = failure.details.reason;
  return reason === "draft_already_exists" || reason === "approved_plan_requires_revision"
    ? reason
    : null;
}

/** The plan the conflict named, so a screen can open it rather than dead-end. */
function conflictPlanId(failure: unknown): string | null {
  if (!(failure instanceof ApiError)) return null;
  const id = failure.details.plan_id;
  return typeof id === "string" ? id : null;
}

/**
 * **The revision being written, with the three ways out of it.** *Bản điều
 * chỉnh đang soạn.*
 *
 * A draft used to be reachable only by expanding the history accordion, where
 * no control acted on it - so an empty v4 sitting under an approved v3 was a
 * dead end that needed a database to clear. This panel is the fix, and it says
 * three things a manager can act on:
 *
 * * *Tiếp tục chỉnh sửa* opens the **existing** draft. It creates nothing - the
 *   editor below is v4, saving in it leaves v4, and there is no path here that
 *   produces a v5;
 * * *Duyệt* is the server's `can_approve`. M2 refuses a plan with no quotas, so
 *   an empty draft shows the button **disabled with the reason** rather than
 *   hidden - a control that vanishes teaches people the screen is broken;
 * * *Bỏ bản nháp* moves it to `DISCARDED`. Not a deletion: the row and its
 *   audit trail stay, which is what keeps "who proposed this and who dropped
 *   it" answerable and stops v4 becoming a hole in the version sequence.
 *
 * The plan in force is untouched by all three. Creating this draft did not
 * supersede it, editing does not change it, and discarding does not restore
 * anything - only a successful approval moves v3 to *Đã thay thế*.
 */
function ActiveDraftPanel({
  draftId,
  versionNo,
  onChanged,
}: {
  draftId: string;
  versionNo: number;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const detail = useQuery({
    queryKey: ["work-plan", draftId],
    queryFn: () => api.workPlan(draftId),
  });
  const data = detail.data;

  return (
    <section
      aria-label="Bản điều chỉnh đang soạn"
      className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3"
    >
      <h4 className="text-xs font-semibold">Bản điều chỉnh đang soạn</h4>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">v{versionNo}</span>
        {data ? <Pill>{data.plan.status_label}</Pill> : null}
        <span className="text-[var(--text-muted)]">
          {data ? `${data.quotas.length} hạn mức` : "…"}
        </span>
        {data?.plan.updated_at ? (
          <span className="text-[var(--text-muted)]">
            Cập nhật {formatWhen(data.plan.updated_at)}
          </span>
        ) : null}
      </p>
      {/* The proposal's own figure, before anybody expands it. The plan in
          force keeps its figure in the editor above; nothing adds the two. */}
      {data ? (
        <div className="mt-2">
          <WorkloadSummary
            workload={data.workload}
            variant={workloadVariantFor(data.plan.status, data.plan.review_state)}
          />
        </div>
      ) : null}

      {/*
        Wraps rather than scrolls, and the primary action is on its own row: the
        trap was reported on a phone, and three lifecycle controls forced into
        one line is how the way out of it goes off the edge of the screen.
      */}
      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <button
          type="button"
          onClick={() => setEditing((was) => !was)}
          aria-expanded={editing}
          className="min-h-11 rounded border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
        >
          {editing ? "Thu gọn" : "Tiếp tục chỉnh sửa"}
        </button>
        {data ? <DraftLifecycleActions data={data} onChanged={onChanged} /> : null}
      </div>

      {/*
        The readiness sentence, and it is the **server's** rule rather than this
        file's: `can_approve` is false for a draft with no quotas, and M2's
        approval refuses one either way. Shown only when it explains a control
        somebody can see, so a ready draft carries no advice it does not need.
      */}
      {data && !data.can_approve && data.quotas.length === 0 ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Cần ít nhất một hạn mức trước khi duyệt.
        </p>
      ) : null}

      {detail.isError ? (
        <ErrorBox error={detail.error} onRetry={() => void detail.refetch()} />
      ) : null}

      {editing ? (
        <div className="mt-2">
          <PlanEditor
            planId={draftId}
            onChanged={onChanged}
            onOpenPlan={() => undefined}
            showLifecycle={false}
          />
        </div>
      ) : null}
    </section>
  );
}

/**
 * *Duyệt* and *Bỏ bản nháp*, drawn from the server's own flags.
 *
 * Split out so the panel above can put them beside *Tiếp tục chỉnh sửa* while
 * the editor it expands keeps them off its own toolbar - the same two acts
 * offered twice on one screen is two answers to one question.
 *
 * `can_approve` false with quotas present means something other than emptiness
 * refused it - a closed period, or a capability this actor lacks - so the
 * button is simply absent rather than disabled with a reason this screen would
 * have to invent.
 */
function DraftLifecycleActions({
  data,
  onChanged,
}: {
  data: WorkPlanDetail;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const settle = (next: WorkPlanDetail) => {
    queryClient.setQueryData(["work-plan", next.plan.id], next);
    onChanged();
  };
  const approve = useMutation({
    mutationFn: () => api.approveWorkPlan(data.plan.id),
    onSuccess: settle,
  });
  const discard = useMutation({
    mutationFn: () => api.discardWorkPlan(data.plan.id),
    onSuccess: settle,
  });

  return (
    <>
      {data.can_return ? <ReturnControl data={data} onReturned={settle} /> : null}
      {data.can_approve ? (
        <ConfirmButton
          spec={approveWorkPlanRevisionConfirmation(data.plan.user_name, data.plan.version_no)}
          pending={approve.isPending}
          error={approve.error}
          onConfirm={() => approve.mutate()}
        >
          {data.plan.review_state === "SUBMITTED" ? "Duyệt và áp dụng" : "Duyệt"}
        </ConfirmButton>
      ) : data.quotas.length === 0 ? (
        // Disabled rather than hidden: the reason is stated underneath, and a
        // control that disappears when a draft is empty is how somebody
        // concludes there is no way to approve at all.
        <button
          type="button"
          disabled
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] opacity-50"
        >
          Duyệt
        </button>
      ) : null}
      {data.can_discard ? (
        <ConfirmButton
          spec={discardWorkPlanConfirmation(data.plan.version_no)}
          tone="secondary"
          pending={discard.isPending}
          error={discard.error}
          onConfirm={() => discard.mutate()}
        >
          Bỏ bản nháp
        </ConfirmButton>
      ) : null}
      {approve.isError ? <ErrorBox error={approve.error} /> : null}
      {discard.isError ? <ErrorBox error={discard.error} /> : null}
    </>
  );
}

/**
 * One plan version's quotas, and the lifecycle controls the server permits.
 *
 * **An approved plan has no edit form.** `can_edit` is false for every one of
 * them, whoever is asking, so the only control offered is *"Tạo bản điều
 * chỉnh"*. That is the immutability rule as a screen rather than as a docstring.
 */
function PlanEditor({
  planId,
  onChanged,
  onOpenPlan,
  showLifecycle = true,
  onDraftConflict,
}: {
  planId: string;
  onChanged: () => void;
  onOpenPlan: (id: string) => void;
  /**
   * False inside *Bản điều chỉnh đang soạn*, whose own panel carries *Duyệt*
   * and *Bỏ bản nháp*. Offering the same two acts twice on one screen is two
   * answers to one question.
   */
  showLifecycle?: boolean;
  /**
   * Called when *Tạo bản điều chỉnh* loses a race: somebody else created the
   * draft between this screen rendering and the click. See the mutation below.
   */
  onDraftConflict?: (planId: string) => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["work-plan", planId],
    queryFn: () => api.workPlan(planId),
  });
  const [adding, setAdding] = useState(false);

  const settle = (next: WorkPlanDetail) => {
    queryClient.setQueryData(["work-plan", next.plan.id], next);
    onChanged();
  };

  const approve = useMutation({ mutationFn: () => api.approveWorkPlan(planId), onSuccess: settle });
  const revise = useMutation({
    mutationFn: () => api.reviseWorkPlan(planId),
    onSuccess: (next) => {
      settle(next);
      // The revision *is* the thing to work on next, so the panel follows it
      // rather than leaving somebody looking at the version they just decided
      // not to edit.
      onOpenPlan(next.plan.id);
    },
    /**
     * **The race, handled as a state rather than as an error.**
     *
     * Hiding the button while a draft exists is a courtesy; the uniqueness
     * index is the rule, and between this screen rendering and the click
     * somebody else can create the draft. The server then refuses with
     * `draft_already_exists` and names the plan it collided with.
     *
     * Keyed on `details.reason` - the structured code the service already
     * sends - and never on the English sentence, which is a message and not an
     * interface. The screen refreshes onto the draft that now exists, so the
     * outcome is *"here is the revision somebody started"* rather than a red
     * dead end telling a Vietnamese user to reload.
     */
    onError: (failure) => {
      if (planConflict(failure) !== "draft_already_exists") return;
      const conflict = conflictPlanId(failure);
      if (conflict !== null) {
        onChanged();
        onDraftConflict?.(conflict);
      }
    },
  });
  const discard = useMutation({ mutationFn: () => api.discardWorkPlan(planId), onSuccess: settle });
  const removeQuota = useMutation({
    mutationFn: (quotaId: string) => api.removeWorkQuota(planId, quotaId),
    onSuccess: settle,
  });

  if (detail.isPending) return <Loading label="Đang tải kế hoạch…" />;
  if (detail.isError) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;

  const data = detail.data;
  const failure = approve.error ?? revise.error ?? discard.error ?? removeQuota.error ?? null;

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <p className="text-xs text-[var(--text-muted)]">
        {data.plan.user_name ?? "—"} · kỳ {data.period.code} · bản v{data.plan.version_no} ·{" "}
        {data.plan.review_state_label ?? data.plan.status_label}
        {data.approved_by_name ? ` · duyệt bởi ${data.approved_by_name}` : ""}
        {data.plan.submitted_at
          ? ` · gửi duyệt ${formatWhen(data.plan.submitted_at)}${
              data.submitted_by_name ? ` bởi ${data.submitted_by_name}` : ""
            }`
          : ""}
      </p>
      {data.plan.review_state === "RETURNED" && data.plan.return_note ? (
        <p className="rounded border border-orange-500/40 bg-orange-500/5 p-2 text-xs">
          Trả lại{data.returned_by_name ? ` bởi ${data.returned_by_name}` : ""}: “
          {data.plan.return_note}”
        </p>
      ) : null}
      {data.plan.status === "DRAFT" && !data.can_edit && data.is_subject ? (
        <p className="text-xs text-[var(--text-muted)]">
          Bản này đang chờ trưởng phòng duyệt nên bạn không sửa được.
        </p>
      ) : null}

      {data.quotas.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          Chưa có hạn mức nào. Kế hoạch phải có ít nhất một hạn mức mới duyệt được.
        </p>
      ) : (
        <ul className="space-y-1">
          {data.quotas.map((quota) => (
            <li
              key={quota.id}
              className="flex flex-wrap items-center justify-between gap-2 text-xs"
            >
              <span className="flex flex-col gap-0.5">
                <span>
                  {quota.work_type_name}
                  <span className="text-[var(--text-muted)]">
                    {" "}
                    · mục tiêu {formatQuantity(quota.target_value, quota.unit_label) ??
                      quota.target_value}
                    {" · trần "}
                    {formatQuantity(quota.eligibility_cap, quota.unit_label) ??
                      quota.eligibility_cap}
                  </span>
                </span>
                {/* The rule and its product, joined by quota id from the
                    server's breakdown. Absent only when the service was built
                    without M6's readers. */}
                {(() => {
                  const priced = quotaWorkloadFor(data.workload, quota.id);
                  return priced ? <QuotaWorkloadLine row={priced} /> : null;
                })()}
              </span>
              <span className="flex items-center gap-1">
                <Pill>{quota.basis_label}</Pill>
                {data.can_edit ? (
                  <>
                    <QuotaNumbersForm
                      planId={planId}
                      quotaId={quota.id}
                      target={quota.target_value}
                      cap={quota.eligibility_cap}
                      workTypeId={quota.work_type_id}
                      onSaved={settle}
                    />
                    <ConfirmButton
                      spec={removeWorkQuotaConfirmation(quota.work_type_name ?? "loại việc này")}
                      tone="danger"
                      ariaLabel={`Bỏ hạn mức ${quota.work_type_name ?? ""}`}
                      pending={removeQuota.isPending}
                      error={removeQuota.error}
                      onConfirm={() => removeQuota.mutate(quota.id)}
                      className="!min-h-9 !px-2 !text-xs"
                    >
                      Bỏ
                    </ConfirmButton>
                  </>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/*
        KPI workload visibility. The plan's figure, under the quotas whose
        contributions it sums - on the employee's editor, the manager's review
        and the approved plan alike, so *Gửi duyệt* and *Duyệt và áp dụng* are
        both pressed with the same number in view. Informational: the server
        blocks neither on a percentage.
      */}
      {data.quotas.length > 0 ? (
        <WorkloadSummary
          workload={data.workload}
          variant={workloadVariantFor(data.plan.status, data.plan.review_state)}
        />
      ) : null}

      {data.can_edit ? (
        <div>
          <button
            type="button"
            onClick={() => setAdding((was) => !was)}
            className="min-h-11 rounded border border-[var(--border)] px-3 text-xs"
          >
            {adding ? "Đóng" : "Thêm hạn mức"}
          </button>
          {adding ? (
            <AddQuotaForm
              planId={planId}
              onAdded={(next) => {
                setAdding(false);
                settle(next);
              }}
            />
          ) : null}
        </div>
      ) : null}

      {/*
        The sentence that stops somebody hunting for an edit button that will
        never exist. An approved plan is immutable for everybody.
      */}
      {data.plan.status === "APPROVED" ? (
        <p className="rounded border border-[var(--border)] p-2 text-xs text-[var(--text-muted)]">
          Kế hoạch đã duyệt không sửa trực tiếp được. Muốn đổi hạn mức thì tạo bản điều chỉnh —
          bản đang áp dụng vẫn giữ hiệu lực cho tới khi bản mới được duyệt.
        </p>
      ) : null}

      {showLifecycle ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {data.can_return ? <ReturnControl data={data} onReturned={settle} /> : null}
          {data.can_approve ? (
            <ConfirmButton
              spec={
                data.plan.supersedes_plan_id
                  ? approveWorkPlanRevisionConfirmation(
                      data.plan.user_name,
                      data.plan.version_no,
                    )
                  : approveWorkPlanConfirmation(data.plan.user_name, data.period.code)
              }
              pending={approve.isPending}
              error={approve.error}
              onConfirm={() => approve.mutate()}
            >
              {data.plan.review_state === "SUBMITTED"
                ? "Duyệt và áp dụng"
                : data.plan.supersedes_plan_id
                  ? "Duyệt bản điều chỉnh"
                  : "Duyệt kế hoạch"}
            </ConfirmButton>
          ) : null}
          {/*
            **Absent while a revision is already in flight.** `can_revise` is
            false for an approved plan that already has a draft - M2 allows one
            per employee and month - so the control that used to be the only
            thing on this screen, and led straight to a refusal, is simply not
            drawn. The draft's own panel is where the work continues.
          */}
          {data.can_revise ? (
            <ConfirmButton
              spec={reviseWorkPlanConfirmation(data.plan.user_name, data.plan.version_no)}
              tone="secondary"
              pending={revise.isPending}
              error={revise.error}
              onConfirm={() => revise.mutate()}
            >
              Tạo bản điều chỉnh
            </ConfirmButton>
          ) : null}
          {data.can_discard ? (
            <ConfirmButton
              spec={discardWorkPlanConfirmation(data.plan.version_no)}
              tone="secondary"
              pending={discard.isPending}
              error={discard.error}
              onConfirm={() => discard.mutate()}
            >
              Bỏ bản nháp
            </ConfirmButton>
          ) : null}
        </div>
      ) : null}

      {/*
        The lost race, in Vietnamese and as information rather than as failure.
        The screen has already moved onto the draft that exists; this sentence
        says why the version number changed under somebody's hands.
      */}
      {planConflict(revise.error) === "draft_already_exists" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Đã có một bản điều chỉnh đang soạn. Hệ thống đã mở bản đó để bạn tiếp tục.
        </p>
      ) : failure ? (
        <ErrorBox error={failure} />
      ) : null}
    </div>
  );
}

/** KPI self-service. The manager's four ways of looking at the roster. */
type ReviewView = "ALL" | "PENDING" | "APPROVED" | "DRAFTING" | "NONE";

const REVIEW_VIEWS: ReadonlyArray<{ key: ReviewView; label: string; empty: string }> = [
  { key: "ALL", label: "Tất cả", empty: "" },
  { key: "PENDING", label: "Chờ duyệt", empty: "Không có kế hoạch nào đang chờ duyệt." },
  { key: "APPROVED", label: "Đang áp dụng", empty: "Chưa có kế hoạch nào đang áp dụng." },
  { key: "DRAFTING", label: "Bản nháp", empty: "Không có bản nháp nào đang soạn." },
  { key: "NONE", label: "Chưa có KPI", empty: "Mọi nhân sự đều đã có kế hoạch." },
];

function reviewCount(
  view: ReviewView,
  counts:
    | { pending_review: number; approved: number; drafting: number; without_plan: number }
    | undefined,
  total: number,
): number | null {
  if (!counts) return view === "ALL" ? total : null;
  switch (view) {
    case "PENDING":
      return counts.pending_review;
    case "APPROVED":
      return counts.approved;
    case "DRAFTING":
      return counts.drafting;
    case "NONE":
      return counts.without_plan;
    default:
      return total;
  }
}

function matchesView(view: ReviewView): (row: EmployeePlanSummary) => boolean {
  switch (view) {
    case "PENDING":
      return (row) => row.draft_is_submitted;
    case "APPROVED":
      return (row) => row.current_status === "APPROVED";
    case "DRAFTING":
      return (row) => row.latest_draft_id !== null && !row.draft_is_submitted;
    case "NONE":
      return (row) => !row.has_plan;
    default:
      return () => true;
  }
}

/**
 * **Trả lại để chỉnh sửa**, with the reason. KPI self-service.
 *
 * A note field and a confirmed button: the note is optional, but it is the
 * useful half - the employee reads it on the returned card - so it sits
 * beside the button rather than behind a second click.
 */
function ReturnControl({
  data,
  onReturned,
}: {
  data: WorkPlanDetail;
  onReturned: (next: WorkPlanDetail) => void;
}) {
  const [note, setNote] = useState("");
  const send = useMutation({
    mutationFn: () => api.returnWorkPlan(data.plan.id, note.trim() || null),
    onSuccess: (next) => {
      setNote("");
      onReturned(next);
    },
  });
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
      <label className="block min-w-0 grow">
        <span className="sr-only">Lý do trả lại</span>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Lý do trả lại (không bắt buộc)"
          aria-label="Lý do trả lại"
          className="min-h-11 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
        />
      </label>
      <ConfirmButton
        spec={returnWorkPlanConfirmation(data.plan.user_name, data.plan.version_no)}
        tone="secondary"
        pending={send.isPending}
        error={send.error}
        onConfirm={() => send.mutate()}
      >
        Trả lại để chỉnh sửa
      </ConfirmButton>
    </div>
  );
}

/**
 * Opening a month.
 *
 * A parameter form whose submit button is the confirmation step - and the
 * operation is idempotent, so asking for a month that already exists returns it
 * unchanged rather than reopening a closed one.
 */
function OpenPeriodForm({ onOpened }: { onOpened: (id: string) => void }) {
  const queryClient = useQueryClient();
  const now = new Date();
  const [value, setValue] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`,
  );
  const open = useMutation({
    mutationFn: () => {
      const [year, month] = value.split("-");
      return api.ensureWorkPeriod({ year: Number(year), month: Number(month) });
    },
    onSuccess: (period) => {
      void queryClient.invalidateQueries({ queryKey: ["work-periods"] });
      onOpened(period.id);
    },
  });
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        open.mutate();
      }}
    >
      <input
        type="month"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-label="Tháng cần mở"
        className="min-h-11 rounded border border-[var(--border)] bg-[var(--surface)] px-2 text-sm"
      />
      <button
        type="submit"
        disabled={open.isPending || !value}
        className="min-h-11 rounded border border-[var(--border)] px-3 text-sm disabled:opacity-50"
      >
        {open.isPending ? "Đang mở…" : "Mở kỳ"}
      </button>
      {open.isError ? <ErrorBox error={open.error} /> : null}
    </form>
  );
}

/** A draft plan for one person and one month. The submit button is the confirmation. */
function CreatePlanForm({
  periodId,
  onCreated,
  onConflict,
}: {
  periodId: string;
  /**
   * The **employee** the form acted on, not the plan it made: the list expands
   * by `user_id`, and handing it a plan id left the row closed after a
   * successful create.
   */
  onCreated: (userId: string) => void;
  /** Refresh and open that employee's row, where the working control is. */
  onConflict: (userId: string) => void;
}) {
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const [userId, setUserId] = useState("");
  const create = useMutation({
    mutationFn: () => api.createWorkPlan({ user_id: userId, period_id: periodId }),
    onSuccess: () => onCreated(userId),
    /**
     * Either conflict means this screen was describing a state that has moved
     * on, so the list is refetched and the employee's row opened - where the
     * control that *does* work is waiting: *Tiếp tục chỉnh sửa* for a draft,
     * *Tạo bản điều chỉnh* for a plan in force.
     */
    onError: (failure) => {
      if (planConflict(failure) !== null) onConflict(userId);
    },
  });
  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <p className="text-xs text-[var(--text-muted)]">
        Kế hoạch mới là bản nháp và <strong>chưa quyết định gì cả</strong> — chỉ bản được duyệt
        mới ảnh hưởng tới điều kiện tính KPI.
      </p>
      <Select
        value={userId}
        onChange={(event) => setUserId(event.target.value)}
        aria-label="Nhân sự"
        className="w-full text-sm"
      >
        <option value="">Chọn nhân sự</option>
        {people.data?.map((person) => (
          <option key={person.user_id} value={person.user_id}>
            {person.full_name}
          </option>
        ))}
      </Select>
      <button
        type="submit"
        disabled={!userId || create.isPending}
        className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
      >
        {create.isPending ? "Đang tạo…" : "Tạo bản nháp"}
      </button>
      {/*
        **Two refusals, two recoveries.** This form picks any employee, so it can
        collide with a state it did not know about - a draft somebody started, or
        a plan already in force. Both are said in Vietnamese and point at the
        control that actually works, rather than shown as the server's English
        sentence with nowhere to go.

        Normal navigation prevents both: a row with a plan does not offer *Tạo
        kế hoạch* at all. This is the stale client, and the server is what makes
        it correct.
      */}
      {planConflict(create.error) === "draft_already_exists" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Nhân sự này đã có một bản kế hoạch đang soạn. Hãy mở dòng của họ và bấm
          “Tiếp tục chỉnh sửa”.
        </p>
      ) : planConflict(create.error) === "approved_plan_requires_revision" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Nhân sự này đã có kế hoạch đang áp dụng. Hãy tạo bản điều chỉnh thay vì
          tạo kế hoạch mới.
        </p>
      ) : create.isError ? (
        <ErrorBox error={create.error} />
      ) : null}
    </form>
  );
}

/**
 * Adding one work type's target and cap to a draft.
 *
 * The basis and the unit are **not** fields: they come from the work type,
 * which is the semantic authority, and offering them would let somebody
 * configure a quota in units the work is never recorded in. The picker shows
 * the type's own basis so the choice is legible.
 */
function AddQuotaForm({
  planId,
  onAdded,
}: {
  planId: string;
  onAdded: (next: WorkPlanDetail) => void;
}) {
  const types = useQuery({ queryKey: ["work-types"], queryFn: () => api.workTypes() });
  const [workTypeId, setWorkTypeId] = useState("");
  const [target, setTarget] = useState("");
  const [cap, setCap] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api.addWorkQuota(planId, {
        work_type_id: workTypeId,
        target_value: target.trim(),
        // A cap nobody typed is the target: "the plan asks for 20 and 20 may be
        // eligible" is the ordinary case, and defaulting to it here means the
        // second field is for the deliberate decision to allow more.
        eligibility_cap: (cap.trim() || target.trim()),
      }),
    onSuccess: onAdded,
  });
  const chosen: WorkType | undefined = types.data?.find((one) => one.id === workTypeId);

  return (
    <form
      className="mt-2 space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      {/*
        M2.5. Same rule as the work-creation picker: an empty taxonomy is a
        sentence rather than a selector with nothing in it. Only the admin
        wording is needed here - this form lives inside plan administration,
        which is already `PR_WORK_CONFIGURE`-gated.

        Active types only, and that is the default rather than a filter applied
        here: a retired type must not acquire a *new* quota, while the quotas it
        already has keep rendering on the plans that approved them.
      */}
      {types.isSuccess && (types.data?.length ?? 0) === 0 ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình.
        </p>
      ) : (
        <Select
          value={workTypeId}
          onChange={(event) => setWorkTypeId(event.target.value)}
          aria-label="Loại công việc"
          className="w-full text-xs"
        >
          <option value="">Chọn loại công việc</option>
          {types.data?.map((one) => (
            <option key={one.id} value={one.id}>
              {one.name} · {one.default_quota_basis_label}
            </option>
          ))}
        </Select>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">
          Mục tiêu {chosen?.default_quota_basis === "QUANTITY" ? `(${chosen.default_unit_label})` : ""}
          <input
            type="number"
            min="0"
            step="0.01"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs"
          />
        </label>
        <label className="text-xs">
          Trần hạn mức
          <input
            type="number"
            min="0"
            step="0.01"
            value={cap}
            onChange={(event) => setCap(event.target.value)}
            placeholder="bằng mục tiêu"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs"
          />
        </label>
      </div>
      <p className="text-[10px] text-[var(--text-muted)]">
        Trần hạn mức không được nhỏ hơn mục tiêu. Phần giữa mục tiêu và trần vẫn đủ điều kiện
        tính KPI; vượt trần thì không.
      </p>
      <button
        type="submit"
        disabled={!workTypeId || !target.trim() || add.isPending}
        className="min-h-11 rounded bg-[var(--text)] px-3 text-xs text-[var(--surface)] disabled:opacity-50"
      >
        {add.isPending ? "Đang lưu…" : "Thêm hạn mức"}
      </button>
      {add.isError ? <ErrorBox error={add.error} /> : null}
    </form>
  );
}

/**
 * Editing a draft quota in place: its two numbers, and - work-taxonomy cleanup -
 * the kind of work it is about.
 *
 * A parameter form rather than a dialog: it edits a **draft**, which has decided
 * nothing, and the click that matters is *"Duyệt kế hoạch"* further down. Asking
 * twice for one decision teaches people to dismiss dialogs.
 *
 * The work-type picker offers the **active** types the server lists; the server
 * re-derives the quota's basis and unit from the chosen type and refuses a type
 * the plan already has a quota for. Nothing about the numbers is recomputed
 * here.
 */
function QuotaNumbersForm({
  planId,
  quotaId,
  target,
  cap,
  workTypeId,
  onSaved,
}: {
  planId: string;
  quotaId: string;
  target: string;
  cap: string;
  workTypeId: string;
  onSaved: (next: WorkPlanDetail) => void;
}) {
  const [open, setOpen] = useState(false);
  const [nextTarget, setNextTarget] = useState(target);
  const [nextCap, setNextCap] = useState(cap);
  const [nextType, setNextType] = useState(workTypeId);
  const types = useQuery({
    queryKey: ["work-types"],
    queryFn: () => api.workTypes(),
    enabled: open,
  });
  const save = useMutation({
    mutationFn: () =>
      api.updateWorkQuota(planId, quotaId, {
        target_value: nextTarget.trim(),
        eligibility_cap: nextCap.trim(),
        // Sent only when the type actually changes, so an edit of the numbers
        // is the same request it always was.
        ...(nextType !== workTypeId ? { work_type_id: nextType } : {}),
      }),
    onSuccess: (next) => {
      setOpen(false);
      onSaved(next);
    },
  });
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-9 rounded border border-[var(--border)] px-2 text-xs"
      >
        Sửa
      </button>
    );
  }
  return (
    <form
      className="flex flex-wrap items-center gap-1"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <Select
        value={nextType}
        onChange={(event) => setNextType(event.target.value)}
        aria-label="Loại công việc của chỉ tiêu"
        className="text-xs"
      >
        <option value={workTypeId}>Giữ loại hiện tại</option>
        {(types.data ?? [])
          .filter((one) => one.id !== workTypeId)
          .map((one) => (
            <option key={one.id} value={one.id}>
              {one.name}
            </option>
          ))}
      </Select>
      <input
        type="number"
        min="0"
        step="0.01"
        value={nextTarget}
        onChange={(event) => setNextTarget(event.target.value)}
        aria-label="Mục tiêu mới"
        className="w-20 rounded border border-[var(--border)] bg-[var(--surface)] px-1 py-1 text-xs"
      />
      <input
        type="number"
        min="0"
        step="0.01"
        value={nextCap}
        onChange={(event) => setNextCap(event.target.value)}
        aria-label="Trần hạn mức mới"
        className="w-20 rounded border border-[var(--border)] bg-[var(--surface)] px-1 py-1 text-xs"
      />
      <button
        type="submit"
        disabled={save.isPending}
        className="min-h-9 rounded bg-[var(--text)] px-2 text-xs text-[var(--surface)] disabled:opacity-50"
      >
        Lưu
      </button>
      {save.isError ? <ErrorBox error={save.error} /> : null}
    </form>
  );
}



/**
 * The three semantic kinds, in Vietnamese.
 *
 * Composed here rather than sent as a `*_label` because these are the names of
 * **domain concepts a client picks from**, not the state of a row - the same
 * reason `WORK_PRESETS` lives in the browser. Nothing branches on the wording.
 */
