"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsRevenueDay } from "@/lib/api";
import { money } from "@/components/ads";
import { changesOf, draftOf, EntryGrid } from "@/components/ads-entry";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { formatDate } from "@/lib/datetime";

/**
 * "Phòng Ads › Nhập doanh thu" (0079): the Trưởng phòng types each day's
 * revenue per marketer, plus the ◆ lines that are nobody's ad account
 * (Hotline, HN chuyển HCM, Google, Tiktok, Chưa gán). A blank cell is "not
 * entered"; every change is kept in the history. The dashboards read it at
 * once (Doanh số, Ads / Doanh thu). A line holding a CRM account (0083) shows
 * the CRM's thực thu instead, locked ("CRM").
 */
export default function AdsRevenuePage() {
  const [day, setDay] = useState("");
  const revenue = useQuery({
    queryKey: ["ads", "revenue", day],
    queryFn: () => api.adsRevenue(day || undefined),
    retry: false,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Nhập doanh thu"
        subtitle="Doanh số từng ngày (số trên đối soát, đã gồm VAT): mỗi người tách SG (khách làm tại cơ sở HCM) và HN (khách SG chuyển làm tại HN - tính KPI cho người đó, không cộng vào doanh thu cơ sở HCM); thêm các nguồn ngoài quảng cáo. Để trống là chưa nhập (khác 0)."
      />
      <label className="flex w-fit flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
        <span>Ngày</span>
        <DateInput
          aria-label="Ngày"
          value={day || revenue.data?.day || ""}
          onChange={(event) => setDay(event.target.value)}
          className="min-h-11 w-44 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
        />
      </label>
      {revenue.isPending ? <Loading label="Đang tải doanh thu…" /> : null}
      {revenue.isError ? (
        <ErrorBox error={revenue.error} onRetry={() => revenue.refetch()} />
      ) : null}
      {revenue.data?.lines.some((line) => line.from_crm) ? (
        <p className="rounded-lg border border-[var(--border)] bg-[var(--accent-soft)] px-4 py-3 text-sm text-[var(--accent-strong)]">
          Các dòng gắn nhãn <span className="font-semibold">CRM</span> lấy
          thực thu tự động từ CRM theo ngày thanh toán (Phòng Ads › Tài khoản
          CRM), không nhập tay.
        </p>
      ) : null}
      {revenue.data ? (
        <Editor key={revenue.data.day} data={revenue.data} />
      ) : null}
    </div>
  );
}

function Editor({ data }: { data: AdsRevenueDay }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => draftOf(data.lines));
  useEffect(() => setDraft(draftOf(data.lines)), [data]);
  const changes = changesOf(data.lines, draft, true);
  const changed = Object.keys(changes).length;
  const save = useMutation({
    mutationFn: () => api.saveAdsRevenue(data.day, changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(["ads", "revenue", saved.day], saved);
      void queryClient.invalidateQueries({ queryKey: ["ads"] });
    },
  });
  return (
    <>
      <EntryGrid
        lines={data.lines}
        draft={draft}
        disabled={save.isPending}
        amountLabel="Doanh thu"
        split
        onChange={(key, value) =>
          setDraft((current) => ({ ...current, [key]: value }))
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-[var(--text-muted)]">
          Tổng ngày {formatDate(data.day)}:{" "}
          <span className="font-semibold text-[var(--text)]">
            {money(data.total)}
          </span>
        </span>
        <ConfirmButton
          tone="primary"
          disabled={changed === 0}
          pending={save.isPending}
          error={save.error}
          onOpenChange={(open) => (open ? save.reset() : undefined)}
          onConfirm={() => save.mutate()}
          spec={{
            title: `Lưu doanh thu ngày ${formatDate(data.day)}?`,
            description:
              "Các ô đã sửa được ghi lại; ô để trống là xoá số đã nhập. Lịch sử sửa vẫn được giữ.",
            confirmLabel: "Lưu doanh thu",
            count: changed > 1 ? changed : undefined,
          }}
        >
          {changed ? `Lưu (${changed} dòng thay đổi)` : "Lưu"}
        </ConfirmButton>
      </div>
    </>
  );
}
