"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type PerformancePolicy } from "@/lib/api";
import {
  PERFORMANCE_LEVEL_ORDER,
  formatRate,
  performanceLevelLabel,
  scoringRuleStatusLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { approvePerformancePolicyConfirmation } from "@/lib/confirmations";

/**
 * *Chính sách hiệu suất* - the weights, caps, barems, gate and bands. **M6.**
 *
 * ## Everything on this screen is rendered from the policy
 *
 * Not one score is written in this file. The barems, the gate thresholds and the
 * band names all come from the approved policy row, so a department that decides
 * *Tốt* is worth 108 changes one version and every screen follows. A hardcoded
 * `105` here would be a second policy that silently disagrees with the one the
 * server computes with.
 *
 * ## The gate, explained rather than applied
 *
 * The quality gate is the part people ask about, so it is rendered as the
 * sentence it is - *"khối lượng cao không bù được cho chất lượng dưới chuẩn"* -
 * followed by the actual thresholds from the policy. The screen never applies
 * it; it explains what the server already did.
 */
export function PerformancePolicyPanel() {
  const queryClient = useQueryClient();
  const [drafting, setDrafting] = useState(false);
  const policies = useQuery({
    queryKey: ["performance-policies"],
    queryFn: api.performancePolicies,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["performance-policies"] });
    void queryClient.invalidateQueries({ queryKey: ["performance"] });
  };

  if (policies.isPending) return <Loading label="Đang tải chính sách hiệu suất…" />;
  if (policies.isError)
    return <ErrorBox error={policies.error} onRetry={() => policies.refetch()} />;

  const rows = policies.data ?? [];
  const active = rows.find((one) => one.status === "APPROVED");
  const history = rows.filter((one) => one.id !== active?.id);

  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Chính sách hiệu suất</h3>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Trọng số, thang điểm và ngưỡng chất lượng dùng để tính chỉ số hiệu suất.
          </p>
        </div>
        <SecondaryButton type="button" onClick={() => setDrafting((open) => !open)}>
          {active ? "Tạo bản điều chỉnh" : "Tạo chính sách"}
        </SecondaryButton>
      </div>

      {drafting ? (
        <PolicyDraftForm
          onDone={() => {
            setDrafting(false);
            refresh();
          }}
          onCancel={() => setDrafting(false)}
        />
      ) : null}

      {rows.length === 0 ? (
        <Empty message="Chưa có chính sách hiệu suất áp dụng cho kỳ này. Hiệu suất chưa thể tính được." />
      ) : null}

      {active ? <ActivePolicy policy={active} /> : null}

      {rows.filter((one) => one.status === "DRAFT").map((draft) => (
        <DraftPolicy key={draft.id} policy={draft} onApproved={refresh} />
      ))}

      {history.length > 0 ? (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-[var(--text-muted)]">
            Lịch sử chính sách ({history.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {history.map((one) => (
              <li key={one.id} className="flex flex-wrap items-center gap-2">
                <Pill tone="neutral">v{one.version_no}</Pill>
                <span>Hiệu lực từ {one.effective_from}</span>
                <span className="text-[var(--text-muted)]">
                  {scoringRuleStatusLabel(one.status)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function ActivePolicy({ policy }: { policy: PerformancePolicy }) {
  const weights: Array<[string, string]> = [
    ["Workload", policy.workload_weight],
    ["Chất lượng", policy.quality_weight],
    ["Tiến độ", policy.timeliness_weight],
    ["Kết quả & đóng góp chung", policy.business_contribution_weight],
  ];
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone="good">Đang áp dụng</Pill>
        <span className="text-xs text-[var(--text-muted)]">
          v{policy.version_no} · hiệu lực từ {policy.effective_from}
        </span>
      </div>

      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Phút chuẩn / ngày</dt>
          <dd className="text-sm">{policy.daily_target_minutes}</dd>
        </div>
        {weights.map(([term, value]) => (
          <div key={term}>
            <dt className="text-xs text-[var(--text-muted)]">{term}</dt>
            <dd className="text-sm">{formatRate(value)}%</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Workload tối đa</dt>
          <dd className="text-sm">{formatRate(policy.workload_score_cap)}%</dd>
        </div>
      </dl>

      <div className="grid gap-3 sm:grid-cols-3">
        <Barem title="Chất lượng" scores={policy.quality_scores} />
        <Barem title="Tiến độ" scores={policy.timeliness_scores} />
        <Barem title="Kết quả & đóng góp chung" scores={policy.business_contribution_scores} />
      </div>

      <QualityGate policy={policy} />
    </div>
  );
}

/** One dimension's rungs, in barem order, **from the policy**. */
function Barem({ title, scores }: { title: string; scores: Record<string, string> }) {
  return (
    <div className="rounded border border-[var(--border)] p-2">
      <p className="text-xs font-medium">{title}</p>
      <ul className="mt-1 space-y-0.5 text-xs">
        {PERFORMANCE_LEVEL_ORDER.filter((level) => scores[level] !== undefined).map((level) => (
          <li key={level} className="flex justify-between gap-2">
            <span>{performanceLevelLabel(level)}</span>
            <span className="font-medium">{formatRate(scores[level])}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The gate, as a sentence and then as the policy's own thresholds.
 *
 * Presented as a *rule about quality* rather than as a deduction, because the
 * question it answers is "why is my index lower than the sum of its parts" and
 * an unexplained cap is exactly what makes that question feel arbitrary.
 */
function QualityGate({ policy }: { policy: PerformancePolicy }) {
  return (
    <div className="rounded border border-[var(--border)] p-2">
      <p className="text-xs font-medium">Ngưỡng chất lượng</p>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Chất lượng là điều kiện giới hạn hiệu suất: khối lượng cao không bù được
        cho chất lượng dưới chuẩn. Tiến độ và kết quả chung <strong>không</strong>{" "}
        có ngưỡng chặn — hai mục đó đã có trọng số riêng.
      </p>
      <ul className="mt-1 space-y-0.5 text-xs">
        {policy.quality_gate.map(([floor, cap], index) => (
          <li key={index} className="flex justify-between gap-2">
            <span>Chất lượng ≥ {formatRate(floor)}</span>
            <span className="font-medium">
              {cap === null ? "không giới hạn thêm" : `PI tối đa ${formatRate(cap)}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DraftPolicy({
  policy,
  onApproved,
}: {
  policy: PerformancePolicy;
  onApproved: () => void;
}) {
  const approve = useMutation({
    mutationFn: () => api.approvePerformancePolicy(policy.id),
    onSuccess: onApproved,
  });
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
      <Pill tone="warn">Nháp</Pill>
      <span>
        v{policy.version_no} · hiệu lực từ {policy.effective_from} ·{" "}
        {formatRate(policy.workload_weight)}/{formatRate(policy.quality_weight)}/
        {formatRate(policy.timeliness_weight)}/
        {formatRate(policy.business_contribution_weight)}
      </span>
      <ConfirmButton
        spec={approvePerformancePolicyConfirmation(policy.version_no)}
        onConfirm={() => approve.mutate()}
        pending={approve.isPending}
        error={approve.error}
        ariaLabel={`Duyệt chính sách v${policy.version_no}`}
      >
        Duyệt
      </ConfirmButton>
    </div>
  );
}

function PolicyDraftForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [daily, setDaily] = useState("300");
  const [workload, setWorkload] = useState("50");
  const [quality, setQuality] = useState("30");
  const [timeliness, setTimeliness] = useState("10");
  const [contribution, setContribution] = useState("10");

  const save = useMutation({
    mutationFn: () =>
      api.createPerformancePolicy({
        effective_from: effectiveFrom,
        daily_target_minutes: Number(daily),
        workload_weight: workload,
        quality_weight: quality,
        timeliness_weight: timeliness,
        business_contribution_weight: contribution,
      }),
    onSuccess: onDone,
  });

  // Presentation only: the server refuses a policy that does not total 100, and
  // this is here so somebody sees it before they submit rather than after.
  const total = [workload, quality, timeliness, contribution].reduce(
    (sum, value) => sum + (Number(value) || 0),
    0,
  );

  return (
    <form
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h4 className="text-sm font-semibold">Chính sách hiệu suất mới</h4>
      <p className="text-xs text-[var(--text-muted)]">
        Chính sách đã duyệt không sửa được. Bản mới áp dụng cho các kỳ từ ngày
        hiệu lực trở đi; kỳ đã chốt vẫn giữ chính sách đã dùng.
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">
          Hiệu lực từ
          <input
            type="date"
            value={effectiveFrom}
            onChange={(event) => setEffectiveFrom(event.target.value)}
            aria-label="Hiệu lực từ"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="block text-xs">
          Phút chuẩn / ngày
          <input
            value={daily}
            onChange={(event) => setDaily(event.target.value)}
            inputMode="numeric"
            aria-label="Phút chuẩn / ngày"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        {(
          [
            ["Workload", workload, setWorkload],
            ["Chất lượng", quality, setQuality],
            ["Tiến độ", timeliness, setTimeliness],
            ["Kết quả & đóng góp chung", contribution, setContribution],
          ] as const
        ).map(([labelText, value, setter]) => (
          <label key={labelText} className="block text-xs">
            {labelText} (%)
            <input
              value={value}
              onChange={(event) => setter(event.target.value)}
              inputMode="decimal"
              aria-label={labelText}
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
        ))}
      </div>

      <p className={`text-xs ${total === 100 ? "text-[var(--text-muted)]" : "text-red-500"}`}>
        Tổng trọng số: {total}% {total === 100 ? "" : "— phải đúng bằng 100%"}
      </p>

      {save.isError ? <ErrorBox error={save.error} /> : null}

      <div className="flex gap-1.5">
        <PrimaryButton type="submit" disabled={!effectiveFrom || save.isPending}>
          {save.isPending ? "Đang lưu…" : "Tạo bản nháp"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Thôi
        </SecondaryButton>
      </div>
    </form>
  );
}
