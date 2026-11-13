"use client";

/**
 * Tài chính › Hồ sơ chi (đợt 3): "Của tôi" (what I filed), "Chờ tôi xử lý" (a
 * step that waits on me - badge on the menu), "Tất cả" (what I may see:
 * VIEW_REQUESTS everything; a head their department's; what I acted on).
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FIELD, statusTone, TabStrip } from "@/components/finance";
import { PageHeader } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate } from "@/lib/datetime";
import { money, spendApi } from "@/lib/finance";

type Tab = "mine" | "awaiting" | "all";

function RequestsScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "mine";
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["finance", "requests", tab, q],
    queryFn: () => spendApi.requests({ tab, q }),
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title="Hồ sơ chi"
        subtitle="Đề nghị → duyệt mua sắm → mua sắm & nghiệm thu → duyệt thanh toán → chi tiền, theo tuyến duyệt của từng mã chi (Sổ tay)."
        action={
          <Link
            href="/finance/requests/new"
            className="inline-flex min-h-11 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            + Lập hồ sơ chi
          </Link>
        }
      />
      <TabStrip
        label="Hồ sơ chi"
        tabs={[
          ["mine", "Của tôi"],
          ["awaiting", `Chờ tôi xử lý${list.data?.awaiting ? ` (${list.data.awaiting})` : ""}`],
          ["all", "Tất cả"],
        ]}
        value={tab}
        onChange={(value) => router.replace(`/finance/requests?tab=${value}`)}
      />
      <label className="block max-w-md text-xs font-medium text-[var(--text-muted)]">
        Tìm mã hồ sơ / nội dung
        <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${FIELD}`} />
      </label>
      {list.isPending ? <Loading /> : null}
      {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      {list.data && !list.data.rows.length ? <Empty message="Chưa có hồ sơ nào." /> : null}
      {list.data && list.data.rows.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[52rem]">
            <thead>
              <tr>
                <th>Mã</th>
                <th>Nội dung</th>
                <th>Mã chi</th>
                <th className="text-right">Số tiền</th>
                <th>Người lập</th>
                <th>Ngày lập</th>
                <th>Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {list.data.rows.map((row) => (
                <tr key={row.id} data-testid="request-row">
                  <td className="whitespace-nowrap font-medium">
                    <Link href={`/finance/requests/${row.id}`} className="text-[var(--accent)] hover:underline">
                      {row.code}
                    </Link>
                  </td>
                  <td>{row.title}</td>
                  <td className="whitespace-nowrap">{row.budget_code}</td>
                  <td className="whitespace-nowrap text-right tabular-nums">{money(row.amount)}</td>
                  <td>{row.requester}</td>
                  <td className="whitespace-nowrap">{formatDate(row.created_at)}</td>
                  <td>
                    <Pill tone={statusTone(row.status)}>{row.phase_label ?? row.status_label}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export default function FinanceRequestsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <RequestsScreen />
    </Suspense>
  );
}
