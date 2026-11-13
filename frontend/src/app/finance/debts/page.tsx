"use client";

/**
 * Tài chính › Công nợ (đợt 2; R10): every CRM order still owing, aged from the
 * day it was created and put in the largest milestone it passed ("Cấu hình ›
 * Công nợ", default 30 / 45 / 60 days). The 5h CRM run re-reads the year's
 * unpaid orders, then the day's summary goes to the people set there.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate } from "@/lib/datetime";
import { financeApi, money } from "@/lib/finance";

const field =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

export default function FinanceDebtsPage() {
  const [bucket, setBucket] = useState("");
  const [q, setQ] = useState("");
  const access = useQuery({ queryKey: ["finance", "access"], queryFn: financeApi.access });
  const debts = useQuery({
    queryKey: ["finance", "debts", bucket, q],
    queryFn: () => financeApi.debts({ min_bucket: bucket ? Number(bucket) : undefined, q }),
    enabled: Boolean(access.data?.revenue),
    retry: false,
  });
  if (access.isPending) return <Loading />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!access.data.revenue) return <Empty message="Bạn chưa có quyền xem công nợ." />;
  const data = debts.data;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Công nợ"
        subtitle="Đơn CRM còn nợ, tính tuổi nợ từ ngày tạo đơn. Mỗi sáng sau lần đồng bộ 5h, người được chọn ở Cấu hình › Công nợ nhận tóm tắt."
      />
      {data ? (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="debt-buckets">
          {data.buckets.map((item) => (
            <div key={item.milestone ?? "none"} className="panel p-4">
              <dt className="text-xs font-medium text-[var(--text-muted)]">
                {item.milestone === null ? "Chưa quá mốc nào" : `Quá ${item.milestone} ngày`}
              </dt>
              <dd className="mt-1 text-xl font-semibold tabular-nums">{money(item.owed)}</dd>
              <p className="mt-1 text-xs text-[var(--text-muted)]">{item.orders} đơn</p>
            </div>
          ))}
        </dl>
      ) : null}
      <div className="flex flex-wrap items-end gap-3">
        <label className="block w-52 text-xs font-medium text-[var(--text-muted)]">
          Tuổi nợ
          <Select value={bucket} onChange={(event) => setBucket(event.target.value)} className="mt-1 w-full">
            <option value="">Tất cả đơn còn nợ</option>
            {(data?.milestones ?? []).map((mark) => (
              <option key={mark} value={mark}>
                Quá {mark} ngày trở lên
              </option>
            ))}
          </Select>
        </label>
        <label className="block min-w-60 flex-1 text-xs font-medium text-[var(--text-muted)]">
          Tìm mã đơn / mã khách
          <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${field}`} />
        </label>
      </div>
      {debts.isPending ? <Loading /> : null}
      {debts.isError ? <ErrorBox error={debts.error} onRetry={() => debts.refetch()} /> : null}
      {data && !data.lines.length ? <Empty message="Không có đơn nào còn nợ." /> : null}
      {data && data.lines.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[56rem]">
            <thead>
              <tr>
                <th>Mã ĐH</th>
                <th>Ngày tạo</th>
                <th className="text-right">Tuổi nợ</th>
                <th>Khách</th>
                <th>Chi nhánh</th>
                <th>Người lên đơn</th>
                <th className="text-right">Doanh số</th>
                <th className="text-right">Còn nợ</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.order_id} data-testid="debt-row">
                  <td className="font-medium">{line.code ?? line.order_id}</td>
                  <td>{formatDate(line.created_on)}</td>
                  <td className="text-right tabular-nums">
                    {line.age} ngày{" "}
                    {line.bucket ? <Pill tone={line.bucket >= 45 ? "bad" : "warn"}>&gt;{line.bucket}</Pill> : null}
                  </td>
                  <td>
                    {line.customer_name ? <span className="block">{line.customer_name}</span> : null}
                    <span className="text-xs text-[var(--text-muted)]">{line.customer_code ?? ""}</span>
                  </td>
                  <td>{line.branch ?? "—"}</td>
                  <td>{line.owner ?? "—"}</td>
                  <td className="text-right tabular-nums">{money(line.total)}</td>
                  <td className="text-right font-medium tabular-nums">{money(line.owed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
