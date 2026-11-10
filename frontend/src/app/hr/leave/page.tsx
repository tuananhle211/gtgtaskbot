"use client";

/**
 * "Đơn từ" (0100): xin nghỉ, đi muộn, về sớm. Everybody sends their own and
 * sees their annual leave; "Chờ tôi duyệt" lists what waits on the caller
 * (Trưởng phòng → Giám đốc khối → Giám đốc → HCNS); "Tất cả" the requests
 * the caller may see (their departments' people, every one for HCNS).
 */

import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { attendanceApi, days, describeAbsence, monthOf, type Absence } from "@/lib/hr-attendance";
import { formatDateTime } from "@/lib/datetime";
import {
  AbsenceDialog,
  BalanceCard,
  CancelButton,
  DecideButtons,
  StatusCell,
} from "@/components/hr-attendance";
import { PageHeader, PrimaryButton, Select, TabStrip } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

type Tab = "mine" | "awaiting" | "all";

function Rows({ items, tab }: { items: Absence[]; tab: Tab }) {
  if (items.length === 0) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        {tab === "awaiting" ? "Không có đơn nào chờ bạn duyệt." : "Chưa có đơn nào."}
      </p>
    );
  }
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[56rem] [&_td]:align-top">
        <thead>
          <tr>
            {tab !== "mine" ? <th>Người gửi</th> : null}
            <th>Đơn</th>
            <th>Ngày / phút</th>
            <th>Lý do</th>
            <th>Gửi lúc</th>
            <th>Trạng thái</th>
            <th>Thao tác</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} data-testid="absence-row">
              {tab !== "mine" ? (
                <td>
                  <span className="font-medium">{item.user?.name}</span>
                  <span className="block font-mono text-xs text-[var(--text-muted)]">{item.user?.member_code}</span>
                  {item.departments.length ? (
                    <span className="block text-xs text-[var(--text-muted)]">{item.departments.join(", ")}</span>
                  ) : null}
                </td>
              ) : null}
              <td>
                <Link href={`/hr/leave/${item.id}`} className="font-medium text-[var(--accent)] hover:underline">
                  {describeAbsence(item)}
                </Link>
              </td>
              <td className="whitespace-nowrap tabular-nums">
                {item.kind === "LEAVE" ? `${days(item.days)} ngày` : `${item.minutes} phút`}
              </td>
              <td className="max-w-[18rem] text-sm">{item.reason}</td>
              <td className="whitespace-nowrap text-xs">{formatDateTime(item.created_at)}</td>
              <td>
                <StatusCell item={item} />
              </td>
              <td className="whitespace-nowrap">
                <DecideButtons item={item} compact />
                <CancelButton item={item} own={item.own} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function LeavePage() {
  return (
    <Suspense fallback={<Loading label="Đang tải đơn…" />}>
      <LeaveScreen />
    </Suspense>
  );
}

function LeaveScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = ((params.get("tab") as Tab | null) ?? "mine") satisfies Tab;
  const [open, setOpen] = useState(params.get("new") === "1");
  const [month, setMonth] = useState("");
  const [status, setStatus] = useState("");
  const access = useQuery({ queryKey: ["hr-attendance", "access"], queryFn: attendanceApi.access });
  const mine = useQuery({ queryKey: ["hr-attendance", "leave-me"], queryFn: () => attendanceApi.myLeave() });
  const list = useQuery({
    queryKey: ["hr-attendance", "absences", tab, month, status],
    queryFn: () =>
      attendanceApi.absences({ tab, month: month || undefined, status: status || undefined }),
  });
  const awaiting = list.data?.awaiting ?? access.data?.awaiting ?? 0;
  const tabs = [
    { key: "mine", label: "Đơn của tôi" },
    { key: "awaiting", label: "Chờ tôi duyệt", count: awaiting || undefined },
    ...(access.data?.lists || access.data?.timesheet ? [{ key: "all", label: "Tất cả" }] : []),
  ];
  const months = Array.from({ length: 6 }, (_, index) => {
    const date = new Date();
    date.setMonth(date.getMonth() - index + 1, 1);
    return monthOf(date);
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Đơn từ"
        subtitle="Xin nghỉ, đi muộn, về sớm. Đơn nghỉ duyệt lần lượt Trưởng phòng → Giám đốc khối → Giám đốc → HCNS; đi muộn / về sớm qua Trưởng phòng rồi HCNS."
        action={
          <PrimaryButton type="button" onClick={() => setOpen(true)}>
            + Tạo đơn
          </PrimaryButton>
        }
      />
      {mine.data && tab === "mine" ? <BalanceCard balance={mine.data.balance} /> : null}
      <div className="flex flex-wrap items-end gap-3">
        <TabStrip
          tabs={tabs}
          active={tab}
          onSelect={(key) => router.replace(`/hr/leave?tab=${key}`)}
          label="Danh sách đơn"
        />
        <div className="ml-auto flex flex-wrap gap-2">
          <Select value={month} onChange={(event) => setMonth(event.target.value)} aria-label="Tháng">
            <option value="">Mọi tháng</option>
            {months.map((item) => (
              <option key={item} value={item}>
                Tháng {item.slice(5)}/{item.slice(0, 4)}
              </option>
            ))}
          </Select>
          <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Trạng thái">
            <option value="">Mọi trạng thái</option>
            {(access.data?.choices.statuses ?? []).map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {list.isPending ? (
        <Loading label="Đang tải đơn…" />
      ) : list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <Rows items={list.data.items} tab={tab} />
      )}
      <AbsenceDialog open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
