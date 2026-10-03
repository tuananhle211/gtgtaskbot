"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type WorkScoringRule, type WorkType } from "@/lib/api";
import {
  formatRate,
  scoringModeLabel,
  scoringRuleStatusLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { approveScoringRuleConfirmation } from "@/lib/confirmations";

/**
 * *Quy tắc workload* - what a kind of work is worth in standard minutes. **M6.**
 *
 * ## The one number on this screen, and where it comes from
 *
 * `standard_minutes_per_unit` is the only figure a person types here. Everything
 * derived from it - a month's workload, an index, a coefficient, an amount - is
 * computed by the server and rendered elsewhere. **Nothing in this file
 * multiplies anything.**
 *
 * ## Why an approved rate has no edit button
 *
 * It decides how a month is measured. An editable rate is one that can be changed
 * after the month it priced, invisibly, and the person whose workload figure
 * moved would have no way to find out why. So an approved row offers *"Tạo bản điều chỉnh"* - a new version with
 * its own effective date - and the old one stays readable, closed, and still
 * pricing the month it was approved for.
 *
 * ## The QUANTITY helper is arithmetic *about* the rule, not a second rule
 *
 * "0,9 phút / bình luận → 100 bình luận ≈ 90 phút" is shown because 0.9 is hard
 * to picture. It is presentation: the 100 is never sent anywhere, and the rule
 * stored is the rate per unit.
 */
export function WorkScoringRules() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [revising, setRevising] = useState<WorkScoringRule | null>(null);

  const rules = useQuery({
    queryKey: ["work-scoring-rules"],
    queryFn: () => api.workScoringRules(),
  });
  const types = useQuery({
    queryKey: ["work-types", "all"],
    queryFn: () => api.workTypes({ include_inactive: true }),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["work-scoring-rules"] });
    // A rate change moves every month that reads it, so the performance views
    // must not keep showing figures computed from the old one.
    void queryClient.invalidateQueries({ queryKey: ["performance"] });
  };

  if (rules.isPending) return <Loading label="Đang tải quy tắc workload…" />;
  if (rules.isError) return <ErrorBox error={rules.error} onRetry={() => rules.refetch()} />;

  const rows = rules.data ?? [];
  const byType = new Map<string, WorkScoringRule[]>();
  for (const rule of rows) {
    byType.set(rule.work_type_id, [...(byType.get(rule.work_type_id) ?? []), rule]);
  }

  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Quy tắc workload</h3>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Mỗi loại công việc đáng bao nhiêu <strong>phút chuẩn</strong>. 1 điểm
            workload = 1 phút chuẩn; 300 phút chuẩn là một ngày công KPI.
          </p>
        </div>
        <SecondaryButton
          type="button"
          onClick={() => {
            setRevising(null);
            setCreating((open) => !open);
          }}
        >
          ➕ Thêm quy tắc
        </SecondaryButton>
      </div>

      {creating || revising ? (
        <ScoringRuleForm
          types={types.data ?? []}
          revising={revising}
          onDone={() => {
            setCreating(false);
            setRevising(null);
            refresh();
          }}
          onCancel={() => {
            setCreating(false);
            setRevising(null);
          }}
        />
      ) : null}

      {rows.length === 0 ? (
        <Empty message="Chưa có quy tắc workload nào, nên chưa công việc nào được quy đổi ra phút chuẩn." />
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[52rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
                <th className="py-2 pr-3 font-medium">Loại công việc</th>
                <th className="py-2 pr-3 font-medium">Cách đo</th>
                <th className="py-2 pr-3 font-medium">Quy tắc workload</th>
                <th className="py-2 pr-3 font-medium">Hiệu lực</th>
                <th className="py-2 pr-3 font-medium">Trạng thái</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...byType.values()].flatMap((versions) =>
                versions.map((rule, index) => (
                  <ScoringRuleRow
                    key={rule.id}
                    rule={rule}
                    isLatest={index === 0}
                    onApproved={refresh}
                    onRevise={() => {
                      setCreating(false);
                      setRevising(rule);
                    }}
                  />
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ScoringRuleRow({
  rule,
  isLatest,
  onApproved,
  onRevise,
}: {
  rule: WorkScoringRule;
  isLatest: boolean;
  onApproved: () => void;
  onRevise: () => void;
}) {
  const approve = useMutation({
    mutationFn: () => api.approveWorkScoringRule(rule.id),
    onSuccess: onApproved,
  });

  // KPI workload visibility. **The rule with its basis**, worded by the server
  // - "30 phút / đầu việc", "1 phút / bình luận" - so the table says what one
  // rate multiplies, not only how many minutes. The bare figure is the
  // fallback for a rule whose work type did not travel with it.
  const minutes =
    rule.mode === "EXCLUDED_FROM_PERFORMANCE"
      ? "—"
      : (rule.rule_label ?? `${formatRate(rule.standard_minutes_per_unit)} phút`);

  return (
    <tr className="border-b border-[var(--border)] align-top">
      <td className="py-2 pr-3">
        <p className="font-medium">{rule.work_type_name ?? rule.work_type_code}</p>
        <p className="text-xs text-[var(--text-muted)]">v{rule.version_no}</p>
      </td>
      <td className="py-2 pr-3 text-xs">
        {scoringModeLabel(rule.mode)}
        {rule.measurement_mode_label ? (
          <span className="block text-[var(--text-muted)]">{rule.measurement_mode_label}</span>
        ) : null}
      </td>
      <td className="py-2 pr-3">
        <span data-testid="rule-label">{minutes}</span>
        {rule.mode === "STANDARD_MINUTES" && rule.unit_label ? (
          <span className="block text-xs text-[var(--text-muted)]">
            1 {rule.unit_label} = {formatRate(rule.standard_minutes_per_unit)} phút chuẩn
          </span>
        ) : null}
      </td>
      <td className="py-2 pr-3 text-xs">
        {rule.effective_from}
        {rule.effective_to ? ` → ${rule.effective_to}` : " → nay"}
      </td>
      <td className="py-2 pr-3">
        <Pill tone={rule.status === "APPROVED" ? "good" : "neutral"}>
          {scoringRuleStatusLabel(rule.status)}
        </Pill>
      </td>
      <td className="py-2">
        <div className="flex flex-wrap justify-end gap-1.5">
          {rule.status === "DRAFT" ? (
            <ConfirmButton
              spec={approveScoringRuleConfirmation(
                rule.work_type_name ?? rule.work_type_code ?? "loại việc này",
                formatRate(rule.standard_minutes_per_unit),
              )}
              onConfirm={() => approve.mutate()}
              pending={approve.isPending}
              error={approve.error}
              ariaLabel={`Duyệt quy tắc ${rule.work_type_name ?? ""} v${rule.version_no}`}
            >
              Duyệt
            </ConfirmButton>
          ) : null}
          {/*
            **No "Chỉnh sửa" on an approved rate, ever.** The only forward move
            is a new version, and offering an edit button the server would refuse
            is how somebody learns the screen is guessing.
          */}
          {rule.status === "APPROVED" && isLatest ? (
            <SecondaryButton
              type="button"
              onClick={onRevise}
              aria-label={`Tạo bản điều chỉnh cho ${rule.work_type_name ?? ""}`}
            >
              Tạo bản điều chỉnh
            </SecondaryButton>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

function ScoringRuleForm({
  types,
  revising,
  onDone,
  onCancel,
}: {
  types: WorkType[];
  revising: WorkScoringRule | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [workTypeId, setWorkTypeId] = useState(revising?.work_type_id ?? "");
  const [mode, setMode] = useState<string>(revising?.mode ?? "STANDARD_MINUTES");
  const [minutes, setMinutes] = useState(revising?.standard_minutes_per_unit ?? "");
  const [effectiveFrom, setEffectiveFrom] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.createWorkScoringRule({
        work_type_id: workTypeId,
        mode: mode as "STANDARD_MINUTES" | "EXCLUDED_FROM_PERFORMANCE",
        standard_minutes_per_unit: mode === "STANDARD_MINUTES" ? minutes.trim() : null,
        effective_from: effectiveFrom,
      }),
    onSuccess: onDone,
  });

  const chosen = types.find((one) => one.id === workTypeId);
  const isQuantity = chosen?.default_quota_basis === "QUANTITY";
  const ready = workTypeId && effectiveFrom && (mode !== "STANDARD_MINUTES" || minutes.trim());

  return (
    <form
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h4 className="text-sm font-semibold">
        {revising ? `Bản điều chỉnh — ${revising.work_type_name}` : "Thêm quy tắc workload"}
      </h4>
      {revising ? (
        <p className="text-xs text-[var(--text-muted)]">
          Quy tắc cũ vẫn giữ nguyên và tiếp tục áp dụng cho khoảng thời gian của
          nó. Bản mới chỉ áp dụng từ ngày hiệu lực trở đi.
        </p>
      ) : null}

      <label className="block text-xs">
        Loại công việc
        <Select
          value={workTypeId}
          onChange={(event) => setWorkTypeId(event.target.value)}
          disabled={Boolean(revising)}
          aria-label="Loại công việc"
          className="mt-1 w-full text-sm disabled:opacity-60"
        >
          <option value="">Chọn loại công việc</option>
          {types.map((one) => (
            <option key={one.id} value={one.id}>
              {one.name}
            </option>
          ))}
        </Select>
      </label>

      <fieldset className="text-xs">
        <legend className="mb-1">Cách tính</legend>
        {/*
          Two modes, and the second is not "zero minutes": a work type nobody has
          configured is unfinished setup, while one deliberately kept out of
          performance is a decision. The server keeps them apart and so does this.
        */}
        {(["STANDARD_MINUTES", "EXCLUDED_FROM_PERFORMANCE"] as const).map((value) => (
          <label key={value} className="mr-4 inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="scoring-mode"
              value={value}
              checked={mode === value}
              onChange={() => setMode(value)}
            />
            {scoringModeLabel(value)}
          </label>
        ))}
      </fieldset>

      {mode === "STANDARD_MINUTES" ? (
        <label className="block text-xs">
          Phút chuẩn / đơn vị
          <input
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            inputMode="decimal"
            placeholder="90"
            aria-label="Phút chuẩn / đơn vị"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
          {chosen ? (
            <span className="mt-1 block text-[var(--text-muted)]">
              {/*
                What the number is *per*, from the work type's own facts - the
                basis it measures by and its unit - so "120 phút" is entered
                knowing whether it means per comment or per work item. The
                stored figure is always per one unit; a rule meant as "120
                bình luận = 120 phút" is entered as 1. The ×100 illustration is
                presentation only and is never sent.
              */}
              Cách đo KPI: {chosen.default_quota_basis_label}. Quy tắc tính theo{" "}
              <strong>1 {isQuantity ? chosen.default_unit_label : "đầu việc"}</strong>
              {minutes.trim()
                ? ` — ${formatRate(minutes.trim())} phút / ${isQuantity ? chosen.default_unit_label : "đầu việc"}`
                : ""}
              {isQuantity && minutes.trim()
                ? ` · 100 ${chosen.default_unit_label} ≈ ${formatRate(String(Number(minutes) * 100))} phút`
                : ""}
            </span>
          ) : null}
        </label>
      ) : (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          Loại công việc này vẫn là công việc thật và vẫn hiện trên sổ công việc,
          nhưng không được quy đổi thành phút chuẩn khi tính hiệu suất.
        </p>
      )}

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

      {save.isError ? <ErrorBox error={save.error} /> : null}

      <div className="flex gap-1.5">
        <PrimaryButton type="submit" disabled={!ready || save.isPending}>
          {save.isPending ? "Đang lưu…" : "Tạo bản nháp"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Thôi
        </SecondaryButton>
      </div>
    </form>
  );
}
