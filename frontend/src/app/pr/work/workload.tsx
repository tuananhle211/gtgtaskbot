"use client";

import type { PlanWorkload, QuotaWorkload } from "@/lib/api";
import { Pill } from "@/components/states";

/**
 * **Tải KPI, printed from the server's arithmetic.** KPI workload visibility.
 *
 * Every number on these two components is a field of `PlanWorkload` or
 * `QuotaWorkload` - the one calculator's output. Nothing here multiplies a
 * target by a rate, divides minutes by a month or decides whether a
 * percentage exists: the server sends `contribution_minutes`, `percent` and
 * `is_complete`, and this file formats them. The same two components draw
 * the employee's card, the employee's editor, the manager's row, the review
 * before *Duyệt và áp dụng* and the draft preview, so those five screens
 * cannot disagree.
 *
 * Three states, all the server's:
 *
 * * complete with a target - "6.204 / 6.600 phút · 94%";
 * * complete without a target - the minutes, and the server's sentence for
 *   why the month has no 100% ("Chưa có lịch làm việc…"). Never "0%";
 * * incomplete - the minutes *that were priced*, how many quotas were not,
 *   and no percentage. Four priced quotas and one unpriced are not "90%".
 *
 * No band and no verdict. The product has performance bands for M6 scores,
 * not for planned workload, and inventing "Cân bằng" here would be a second
 * scale nobody approved. The bar under the figure is a picture of the same
 * two numbers, nothing more.
 */

/** "6204.00" → "6.204"; "0.9" → "0,9". Vietnamese digits, no stored strings. */
export function formatMinutes(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}

/** "94.3" → "94,3"; "94.0" → "94". One decimal at most - the server's quantum. */
export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`;
}

/** "28.00" → "28"; "12.50" → "12,5". A target as a person wrote it. */
export function formatTarget(value: string | number): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}

/**
 * Which plan the figure is about. The label is the whole difference between
 * *what counts this month* and *what has been proposed*, so it travels as a
 * prop rather than being inferred from the numbers.
 */
export type WorkloadVariant = "applied" | "projected" | "proposed";

const VARIANT_LABELS: Record<WorkloadVariant, string> = {
  applied: "Tải KPI",
  projected: "Tải dự kiến",
  proposed: "Tải đề xuất",
};

export function workloadVariantFor(
  status: string | null,
  reviewState: string | null,
): WorkloadVariant {
  if (status === "APPROVED") return "applied";
  if (reviewState === "SUBMITTED") return "proposed";
  return "projected";
}

/** The one-line figure: "6.204 / 6.600 phút · 94%", or its honest fallback. */
export function workloadHeadline(workload: PlanWorkload): string {
  const minutes = formatMinutes(workload.projected_minutes);
  if (!workload.is_complete) return `${minutes} phút`;
  if (workload.target_minutes === null) return `${minutes} phút`;
  const target = formatMinutes(workload.target_minutes);
  return workload.percent === null
    ? `${minutes} / ${target} phút`
    : `${minutes} / ${target} phút · ${formatPercent(workload.percent)}`;
}

export function WorkloadSummary({
  workload,
  variant,
  compact = false,
}: {
  workload: PlanWorkload | null | undefined;
  variant: WorkloadVariant;
  /** One line for a list row; the full explanation on a card or editor. */
  compact?: boolean;
}) {
  if (!workload) return null;
  const incomplete = !workload.is_complete;
  const label = incomplete && !compact ? "Tải đã tính" : VARIANT_LABELS[variant];
  const percent = workload.percent === null ? null : Number(workload.percent);
  const ratio = percent === null ? null : Math.max(0, Math.min(percent, 150)) / 150;

  if (compact) {
    return (
      <p data-testid="workload-compact" className="text-xs">
        <span className="text-[var(--text-muted)]">{label} </span>
        <span className="font-medium">{workloadHeadline(workload)}</span>
        {incomplete ? (
          <span className="text-[var(--text-muted)]">
            {" "}
            · {workload.unpriced_quota_count} chỉ tiêu chưa quy đổi
          </span>
        ) : workload.target_minutes === null ? (
          <span className="text-[var(--text-muted)]"> · chưa có mục tiêu phút</span>
        ) : null}
      </p>
    );
  }

  return (
    <div
      data-testid="workload-summary"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs"
    >
      <p className="text-[var(--text-muted)]">{label}</p>
      <p className="text-sm font-semibold">{workloadHeadline(workload)}</p>
      {ratio !== null ? (
        <div
          className="mt-1 h-1.5 w-full overflow-hidden rounded bg-[var(--border)]"
          aria-hidden="true"
        >
          <div className="h-full bg-[var(--text)]" style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
      {incomplete ? (
        <>
          <p className="mt-1 text-[var(--text-muted)]">
            {workload.unpriced_quota_count} chỉ tiêu chưa quy đổi · Chưa thể tính chính xác % tải
          </p>
          <p className="text-[var(--text-muted)]">
            Chưa có quy tắc workload:{" "}
            {workload.unpriced_work_types
              .map((row) => row.work_type_name ?? row.work_type_id)
              .join(", ")}
          </p>
        </>
      ) : null}
      {workload.target_minutes !== null ? (
        <p className="mt-1 text-[var(--text-muted)]">
          Mốc 100% của tháng: {formatMinutes(workload.target_minutes)} phút
          {workload.target_is_overridden
            ? " (mức đã được điều chỉnh riêng)"
            : workload.eligible_workdays !== null && workload.daily_target_minutes !== null
              ? ` = ${formatTarget(workload.eligible_workdays)} ngày công × ${formatMinutes(workload.daily_target_minutes)} phút`
              : ""}
        </p>
      ) : (
        <p className="mt-1 text-[var(--text-muted)]">
          {workload.target_unresolved_label ?? "Chưa xác định được mục tiêu phút của kỳ."}
        </p>
      )}
    </div>
  );
}

/** The per-quota workload for one quota row, by id, or `null` when absent. */
export function quotaWorkloadFor(
  workload: PlanWorkload | null | undefined,
  quotaId: string,
): QuotaWorkload | null {
  return workload?.quotas.find((row) => row.quota_id === quotaId) ?? null;
}

/**
 * "28 đầu việc · 30 phút/đầu việc → 840 phút" - target, rule, contribution,
 * every part of it a server field. An unpriced quota says so in the server's
 * words and shows no number, because a missing rule is not zero workload.
 */
export function QuotaWorkloadLine({ row }: { row: QuotaWorkload }) {
  const target = `${formatTarget(row.target_value)} ${row.target_unit_label}`;
  if (row.status === "NO_SCORING_RULE") {
    return (
      <span data-testid="quota-workload" data-status={row.status} className="text-xs">
        <span className="text-[var(--text-muted)]">{target}</span>
        {" · "}
        <Pill tone="warn">⚠ {row.status_label}</Pill>
      </span>
    );
  }
  if (row.status === "EXCLUDED_FROM_PERFORMANCE") {
    return (
      <span data-testid="quota-workload" data-status={row.status} className="text-xs">
        <span className="text-[var(--text-muted)]">{target}</span>
        {" · "}
        <span className="text-[var(--text-muted)]">{row.status_label}</span>
      </span>
    );
  }
  return (
    <span data-testid="quota-workload" data-status={row.status} className="text-xs">
      <span className="text-[var(--text-muted)]">
        {target} · {row.rule_label?.replace(" / ", "/")}
      </span>{" "}
      <span className="font-medium">→ {formatMinutes(row.contribution_minutes)} phút</span>
    </span>
  );
}

/** The whole breakdown, stacked - one row per quota, for a card or a review. */
export function QuotaWorkloadBreakdown({
  workload,
}: {
  workload: PlanWorkload | null | undefined;
}) {
  if (!workload || workload.quotas.length === 0) return null;
  return (
    <ul data-testid="workload-breakdown" className="space-y-1">
      {workload.quotas.map((row) => (
        <li
          key={row.quota_id}
          className="flex flex-col gap-0.5 rounded border border-[var(--border)] p-2"
        >
          <span className="text-xs font-medium">{row.work_type_name ?? row.work_type_code}</span>
          <QuotaWorkloadLine row={row} />
        </li>
      ))}
    </ul>
  );
}
