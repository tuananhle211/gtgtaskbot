"use client";

/** One leave / late / early request: what, why, its approval strip, the actions. */

import Link from "next/link";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { attendanceApi, days, describeAbsence } from "@/lib/hr-attendance";
import { formatDateTime } from "@/lib/datetime";
import {
  BalanceCard,
  CancelButton,
  DecideButtons,
  EvidenceGallery,
  StatusCell,
  StepStrip,
} from "@/components/hr-attendance";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

export default function AbsencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const item = useQuery({ queryKey: ["hr-attendance", "absence", id], queryFn: () => attendanceApi.absence(id) });
  if (item.isPending) return <Loading label="Đang tải đơn…" />;
  if (item.isError) return <ErrorBox error={item.error} onRetry={() => item.refetch()} />;
  const row = item.data;
  return (
    <div className="space-y-4">
      <Link href="/hr/leave" className="text-sm text-[var(--accent)] hover:underline">
        ← Đơn từ
      </Link>
      <PageHeader title={describeAbsence(row)} subtitle={`${row.user?.name ?? ""} · gửi ${formatDateTime(row.created_at)}`} />
      <section className="panel space-y-3 p-4">
        <dl className="grid gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Người gửi</dt>
            <dd className="font-medium">{row.user?.name}</dd>
            <dd className="text-xs text-[var(--text-muted)]">{row.departments.join(", ")}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Loại</dt>
            <dd className="font-medium">{row.leave_type?.name ?? row.kind_label}</dd>
            {row.leave_type && !row.leave_type.paid ? (
              <dd className="text-xs text-[var(--text-muted)]">Không tính lương</dd>
            ) : null}
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">
              {row.kind === "LEAVE" ? "Số ngày công" : row.kind === "EXPLAIN" ? "Giờ khai" : "Số phút"}
            </dt>
            <dd className="text-2xl font-semibold tabular-nums">
              {row.kind === "LEAVE"
                ? days(row.days)
                : row.kind === "EXPLAIN"
                  ? `${row.check_in ?? "--"} → ${row.check_out ?? "--"}`
                  : row.minutes}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Trạng thái</dt>
            <dd>
              <StatusCell item={row} />
            </dd>
          </div>
        </dl>
        <div>
          <p className="text-xs text-[var(--text-muted)]">Lý do</p>
          <p className="text-sm">{row.reason}</p>
        </div>
        {row.files?.length ? (
          <div className="space-y-1">
            <p className="text-xs text-[var(--text-muted)]">Ảnh xác minh (bấm để xem cỡ lớn)</p>
            <EvidenceGallery files={row.files} />
          </div>
        ) : null}
        {row.cancel_note ? (
          <p className="text-sm text-[var(--text-muted)]">
            Huỷ bởi {row.cancelled_by?.name}: “{row.cancel_note}”
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <DecideButtons item={row} compact />
          <CancelButton item={row} own={row.own} />
        </div>
      </section>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">Các bước duyệt</h2>
        <StepStrip item={row} />
      </section>
      {row.balance ? <BalanceCard balance={row.balance} title={`Phép năm của ${row.user?.name ?? ""}`} /> : null}
    </div>
  );
}
