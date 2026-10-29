"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { quotaConfirmation } from "@/lib/confirmations";
import { formatScore } from "@/components/performance";
import { ErrorBox, Loading } from "@/components/states";
import { monthLabel } from "@/lib/labels";

/**
 * "Định mức" settings (2026-10-29, the OWNER): points for one hour of work
 * (1 point = 1 standard minute), hours in a work day, and the default share
 * of a day a person is expected to score. A month's standard work days are
 * its days minus Sundays and half of each Saturday; the quota is
 * days x points per day x %. Everybody else reads it, nobody else edits it.
 */
export function QuotaSettingsPanel({ month }: { month: string }) {
  const queryClient = useQueryClient();
  const quota = useQuery({
    queryKey: ["performance", "quota", month],
    queryFn: () => api.performanceQuota(month),
  });
  const [form, setForm] = useState({ points_per_hour: "", hours_per_day: "", quota_pct: "" });
  useEffect(() => {
    if (!quota.data) return;
    setForm({
      points_per_hour: String(quota.data.points_per_hour),
      hours_per_day: String(quota.data.hours_per_day),
      quota_pct: String(quota.data.quota_pct),
    });
  }, [quota.data]);
  const save = useMutation({
    mutationFn: () =>
      api.setPerformanceQuota({
        points_per_hour: Number(form.points_per_hour),
        hours_per_day: Number(form.hours_per_day),
        quota_pct: Number(form.quota_pct),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["performance"] }),
  });

  if (quota.isPending) return <Loading label="Đang tải định mức…" />;
  if (quota.isError) return <ErrorBox error={quota.error} onRetry={() => quota.refetch()} />;
  const data = quota.data;
  const perHour = Number(form.points_per_hour);
  const hours = Number(form.hours_per_day);
  const pct = Number(form.quota_pct);
  const valid =
    Number.isInteger(perHour) && perHour >= 1 && perHour <= 600 &&
    Number.isInteger(hours) && hours >= 1 && hours <= 24 &&
    Number.isInteger(pct) && pct >= 1 && pct <= 100;
  const perDay = valid ? perHour * hours : 0;
  const daily = (perDay * pct) / 100;
  const monthly = Math.round(data.work_days * daily);
  const changed =
    perHour !== data.points_per_hour || hours !== data.hours_per_day || pct !== data.quota_pct;
  const field = (name: keyof typeof form, label: string, hint: string, suffix?: string) => (
    <label className="block text-sm font-medium">
      {label}
      <span className="mt-1 flex items-center gap-2">
        <input
          type="number"
          inputMode="numeric"
          value={form[name]}
          disabled={!data.can_edit}
          onChange={(event) => setForm((was) => ({ ...was, [name]: event.target.value }))}
          className="min-h-10 w-28 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm tabular-nums text-[var(--text)] disabled:opacity-70"
        />
        {suffix ? <span className="text-sm text-[var(--text-muted)]">{suffix}</span> : null}
      </span>
      <span className="mt-1 block text-xs font-normal text-[var(--text-muted)]">{hint}</span>
    </label>
  );
  return (
    <section className="panel p-4" aria-labelledby="quota-settings">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="quota-settings" className="text-base font-semibold">
          Định mức
        </h2>
        <p className="text-xs text-[var(--text-muted)]">
          Ngày công chuẩn = số ngày của tháng − Chủ nhật − ½ mỗi thứ Bảy
        </p>
      </header>
      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        {field("points_per_hour", "Điểm cho 1 giờ công", "1 điểm = 1 phút lao động tiêu chuẩn.", "điểm")}
        {field("hours_per_day", "Số giờ của 1 ngày công", `= ${formatScore(perDay)} điểm cho 1 ngày công.`, "giờ")}
        {field("quota_pct", "Tỷ lệ định mức mặc định", "Phần còn lại dành cho họp, brief, việc hành chính.", "%")}
      </div>
      <dl className="mt-4 grid gap-3 rounded-lg border border-[var(--border)] p-3 text-sm sm:grid-cols-3" aria-label="Định mức tính ra">
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Định mức 1 ngày</dt>
          <dd className="text-lg font-semibold tabular-nums">{formatScore(daily)} điểm</dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Ngày công chuẩn {monthLabel(month).toLowerCase()}</dt>
          <dd className="text-lg font-semibold tabular-nums">
            {new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(data.work_days)} ngày
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Định mức tháng / người</dt>
          <dd className="text-lg font-semibold tabular-nums">{formatScore(monthly)} điểm</dd>
        </div>
      </dl>
      {data.can_edit ? (
        <div className="mt-3 flex justify-end">
          <ConfirmButton
            spec={quotaConfirmation({ pointsPerDay: perDay, pct, daily: formatScore(daily) })}
            pending={save.isPending}
            error={save.error}
            disabled={!valid || !changed}
            onConfirm={() => save.mutate()}
          >
            Lưu định mức
          </ConfirmButton>
        </div>
      ) : null}
    </section>
  );
}
