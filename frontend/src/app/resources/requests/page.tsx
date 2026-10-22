"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/datetime";
import { formatVnd, requestTone } from "@/lib/resources";
import { PageHeader } from "@/components/pr";
import { PersonLink } from "@/components/person-link";
import { ErrorBox, Loading, Pill } from "@/components/states";

/**
 * "Đề xuất tài nguyên" (2026-10-21): the requests this person may see - their
 * own, those they decide as a head, every one for the Phòng Tài nguyên. A
 * tinted row waits on the viewer. Anybody may make one ("+ Tạo đề xuất").
 */
type Tab = "all" | "mine" | "open";

export default function ResourceRequestsPage() {
  const [tab, setTab] = useState<Tab>("all");
  const list = useQuery({
    queryKey: ["resource-requests", tab],
    queryFn: () => api.resourceRequests(tab),
  });
  const awaiting = list.data?.awaiting_count ?? 0;
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "all", label: "Tất cả" },
    { key: "mine", label: `Chờ tôi xử lý (${awaiting})` },
    { key: "open", label: "Đang mở" },
  ];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Đề xuất tài nguyên"
        subtitle="Đề xuất mua tài nguyên: trưởng phòng duyệt, Phòng Tài nguyên duyệt chi, mua và bàn giao, người đề xuất xác nhận. Dòng tô màu là việc đang chờ bạn."
        action={
          <Link
            href="/resources/requests/new"
            className="inline-flex min-h-10 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            + Tạo đề xuất
          </Link>
        }
      />
      <div className="panel p-3 sm:p-4">
        <div role="tablist" aria-label="Lọc đề xuất" className="flex flex-wrap gap-2">
          {tabs.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={tab === item.key}
              onClick={() => setTab(item.key)}
              className={`min-h-9 rounded-full border px-3 text-sm ${
                tab === item.key
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        {list.isPending ? <Loading /> : null}
        {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
        {list.data ? (
          list.data.items.length === 0 ? (
            <p className="mt-4 text-sm text-[var(--text-muted)]">Chưa có đề xuất nào ở đây.</p>
          ) : (
            <div className="table-scroll-x mt-3">
              <table className="table-dense w-full">
                <thead>
                  <tr>
                    <th scope="col">Mã ĐX</th>
                    <th scope="col">Loại</th>
                    <th scope="col">SL</th>
                    <th scope="col">Mục đích</th>
                    <th scope="col">Người đề xuất</th>
                    <th scope="col" className="text-right">
                      Dự toán
                    </th>
                    <th scope="col">Cần trước</th>
                    <th scope="col">Trạng thái</th>
                    <th scope="col">Đã nhận</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((item) => (
                    <tr
                      key={item.id}
                      data-awaiting={item.awaiting_me ? "true" : undefined}
                      className={item.awaiting_me ? "bg-[var(--accent-soft)]" : undefined}
                    >
                      <td className="whitespace-nowrap font-mono">
                        <Link href={item.detail_path} className="text-[var(--accent)] underline">
                          {item.code}
                        </Link>
                      </td>
                      <td>{item.type_name}</td>
                      <td className="tabular-nums">{item.quantity}</td>
                      <td className="min-w-[12rem]">
                        <span className="block">{item.purpose}</span>
                        <span className="block text-xs text-[var(--text-muted)]">{item.site_label}</span>
                      </td>
                      <td className="whitespace-nowrap">
                        <PersonLink userId={item.requester.user_id} name={item.requester.full_name} />
                      </td>
                      <td className="whitespace-nowrap text-right tabular-nums">{formatVnd(item.budget)}</td>
                      <td className="whitespace-nowrap">{item.needed_by ? formatDate(item.needed_by) : "–"}</td>
                      <td className="whitespace-nowrap">
                        <Pill tone={requestTone(item.stage)}>{item.stage_label}</Pill>
                      </td>
                      <td className="whitespace-nowrap tabular-nums">
                        {item.delivered > 0 ? `${item.delivered}/${item.quantity}` : "–"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}
