"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsBm, type AdsKpiMonth } from "@/lib/api";
import { BmPicker, bmLabel } from "@/components/ads";
import { changesOf, draftOf, EntryGrid } from "@/components/ads-entry";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Phòng Ads › KPI" (0079): the month's revenue target for the whole team and
 * for each marketer, per BM (0087: BM Hà Nội / BM Sài Gòn). The dashboard
 * sets a BM's KPI against what its region's CRM accounts brought, and both
 * added up against the whole revenue when no BM is picked.
 */
export default function AdsKpiPage() {
  const [month, setMonth] = useState("");
  const [region, setRegion] = useState<AdsBm>("HN");
  const kpi = useQuery({
    queryKey: ["ads", "kpi", month, region],
    queryFn: () => api.adsKpi(month || undefined, region),
    retry: false,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="KPI doanh thu"
        subtitle="Chỉ tiêu doanh thu của cả phòng và từng người theo tháng, nhập riêng cho từng BM."
      />
      <div className="flex flex-wrap items-end gap-3">
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
        <BmPicker value={region} onChange={setRegion} />
      </div>
      {kpi.isPending ? <Loading label="Đang tải KPI…" /> : null}
      {kpi.isError ? (
        <ErrorBox error={kpi.error} onRetry={() => kpi.refetch()} />
      ) : null}
      {kpi.data ? (
        <Editor key={`${kpi.data.month}:${kpi.data.region}`} data={kpi.data} />
      ) : null}
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
    mutationFn: () => api.saveAdsKpi(data.month, data.region, changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(["ads", "kpi", saved.month, saved.region], saved);
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
              title: `Lưu KPI ${bmLabel(data.region)} tháng ${monthNumber}/${year}?`,
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
