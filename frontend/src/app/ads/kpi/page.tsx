"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsKpiMonth } from "@/lib/api";
import { changesOf, draftOf, EntryGrid } from "@/components/ads-entry";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Phòng Ads › KPI" (0079): the month's revenue target for the whole team and
 * for each marketer. Quản lý A shows each person's completion against it.
 */
export default function AdsKpiPage() {
  const [month, setMonth] = useState("");
  const kpi = useQuery({
    queryKey: ["ads", "kpi", month],
    queryFn: () => api.adsKpi(month || undefined),
    retry: false,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="KPI doanh thu"
        subtitle="Chỉ tiêu doanh thu của cả phòng và từng người theo tháng."
      />
      <label className="flex w-fit flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
        <span>Tháng</span>
        <input
          type="month"
          aria-label="Tháng"
          value={month || kpi.data?.month || ""}
          onChange={(event) => setMonth(event.target.value)}
          className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
        />
      </label>
      {kpi.isPending ? <Loading label="Đang tải KPI…" /> : null}
      {kpi.isError ? (
        <ErrorBox error={kpi.error} onRetry={() => kpi.refetch()} />
      ) : null}
      {kpi.data ? <Editor key={kpi.data.month} data={kpi.data} /> : null}
    </div>
  );
}

function Editor({ data }: { data: AdsKpiMonth }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => draftOf(data.lines));
  useEffect(() => setDraft(draftOf(data.lines)), [data]);
  const changes = changesOf(data.lines, draft);
  const changed = Object.keys(changes).length;
  const save = useMutation({
    mutationFn: () => api.saveAdsKpi(data.month, changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(["ads", "kpi", saved.month], saved);
      void queryClient.invalidateQueries({ queryKey: ["ads"] });
    },
  });
  const [year, monthNumber] = data.month.split("-");
  return (
    <>
      <EntryGrid
        lines={data.lines}
        draft={draft}
        disabled={!data.can_edit || save.isPending}
        amountLabel="KPI"
        onChange={(key, value) =>
          setDraft((current) => ({ ...current, [key]: value }))
        }
      />
      {data.can_edit ? (
        <div className="flex justify-end">
          <ConfirmButton
            tone="primary"
            disabled={changed === 0}
            pending={save.isPending}
            error={save.error}
            onOpenChange={(open) => (open ? save.reset() : undefined)}
            onConfirm={() => save.mutate()}
            spec={{
              title: `Lưu KPI tháng ${monthNumber}/${year}?`,
              description:
                "Chỉ tiêu mới áp dụng ngay trên Dashboard Ads; ô để trống là bỏ chỉ tiêu.",
              confirmLabel: "Lưu KPI",
            }}
          >
            {changed ? `Lưu KPI (${changed} dòng thay đổi)` : "Lưu KPI"}
          </ConfirmButton>
        </div>
      ) : null}
    </>
  );
}
