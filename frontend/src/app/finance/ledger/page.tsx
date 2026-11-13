"use client";

/**
 * Tài chính › Bảng kê (đợt 2): the accounting sheet's BCLT and Tổng hợp, built
 * from the CRM. A row = an order on one day ("Đơn mới" the day it was created,
 * "TTCN" a later payment day). The four fees are automatic (card % per payment
 * method, cost price per service, "Cấu hình › Phí & giá cost") unless Kế toán
 * typed them ("Sửa phí", FINANCE FEES). "Xuất Excel" = the BCLT + Tổng hợp
 * workbook of the month.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FeeDialog } from "@/components/finance-fees";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { monthOf, shiftMonth } from "@/lib/hr-attendance";
import {
  FEE_LABELS,
  financeApi,
  money,
  monthBounds,
  type FeeKey,
  type Ledger,
  type LedgerRow,
} from "@/lib/finance";

const FEE_KEYS: FeeKey[] = ["card_fee", "referral_fee", "car_fee", "cost"];
const field =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

function dayLabel(day: string): string {
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();
}

const DOCTOR_KINDS = ["Bác sĩ phẫu", "Bác sĩ tiêm", "Bác sĩ khác", "Bác sĩ khác 2"] as const;

function Check({ value }: { value: number | null }) {
  if (value === null) return <span className="text-[var(--text-muted)]">—</span>;
  return (
    <span className={value === 0 ? "text-[var(--good)]" : "text-[var(--bad)]"}>{money(value)}</span>
  );
}

function RowsTable({ data, canEdit }: { data: Ledger; canEdit: boolean }) {
  const router = useRouter();
  const [day, setDay] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<LedgerRow | null>(null);
  const rows = useMemo(() => {
    const text = fold(q.trim());
    return data.rows.filter(
      (row) =>
        (!day || row.day === day) &&
        (!text ||
          [row.order_code, row.customer_code, row.customer_name, row.sale, row.marketing, row.services]
            .filter(Boolean)
            .some((value) => fold(String(value)).includes(text))),
    );
  }, [data.rows, day, q]);
  const methods = Object.entries(data.methods);
  const open = (row: LedgerRow) => {
    if (row.order_code) router.push(`/finance/ledger/orders/${encodeURIComponent(row.order_code)}`);
  };
  const sticky = "sticky z-10 bg-[var(--surface)]";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="block w-44 text-xs font-medium text-[var(--text-muted)]">
          Ngày
          <Select value={day} onChange={(event) => setDay(event.target.value)} className="mt-1 w-full">
            <option value="">Cả tháng</option>
            {data.days.map((item) => (
              <option key={item.day} value={item.day}>
                {dayLabel(item.day)}
              </option>
            ))}
          </Select>
        </label>
        <label className="block min-w-60 flex-1 text-xs font-medium text-[var(--text-muted)]">
          Tìm mã đơn / mã khách / tên / dịch vụ / sale / MKT
          <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${field}`} />
        </label>
        <span className="pb-3 text-sm text-[var(--text-muted)]">{rows.length} dòng · bấm một đơn để xem chia doanh thu</span>
      </div>
      {rows.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[220rem] whitespace-nowrap">
            <thead>
              <tr>
                <th className={`${sticky} left-0`}>Ngày</th>
                <th className={`${sticky} left-16`}>Mã ĐH</th>
                <th>Phân loại</th>
                <th>SP/DV</th>
                <th>Tên KH</th>
                <th>Mã KH</th>
                <th>Diễn giải</th>
                <th className="text-right">Doanh số</th>
                <th className="text-right">Thực thu</th>
                <th className="text-right">Nợ</th>
                {methods.map(([key, label]) => (
                  <th key={key} className="text-right">
                    {label}
                  </th>
                ))}
                <th className="text-right">Hoàn tiền</th>
                {FEE_KEYS.map((key) => (
                  <th key={key} className="text-right">
                    {FEE_LABELS[key]}
                  </th>
                ))}
                <th className="text-right">Tổng phí</th>
                <th className="text-right">VAT</th>
                <th className="text-right">Sau VAT & phí</th>
                <th className="text-right">Check DT TVV.KTV.Y tá</th>
                <th className="text-right">Check DT BS</th>
                <th>NV bán 1</th>
                <th className="text-right">DT 1</th>
                <th>NV bán 2</th>
                <th className="text-right">DT 2</th>
                <th>NV bán 3</th>
                <th className="text-right">DT 3</th>
                {DOCTOR_KINDS.map((kind) => (
                  <Fragment key={kind}>
                    <th>{kind}</th>
                    <th className="text-right">DT {kind.slice(7)}</th>
                  </Fragment>
                ))}
                <th>Sale (người phụ trách)</th>
                <th>MKT (nguồn)</th>
                <th>Chat page</th>
                <th>Chi nhánh</th>
                <th>Ghi chú</th>
                <th>Công nợ quá 45 ngày</th>
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const sellers = [...row.sellers, undefined, undefined, undefined].slice(0, 3);
                return (
                  <tr
                    key={`${row.order_id ?? row.order_code}-${row.day}-${row.kind}`}
                    data-testid="ledger-row"
                    onClick={() => open(row)}
                    className={row.order_code ? "cursor-pointer hover:bg-[var(--surface-muted)]" : ""}
                  >
                    <td className={`${sticky} left-0`}>{dayLabel(row.day)}</td>
                    <td className={`${sticky} left-16 font-medium text-[var(--accent)]`}>{row.order_code ?? "—"}</td>
                    <td>
                      <Pill tone={row.kind === "NEW" ? "good" : "neutral"}>{row.kind === "NEW" ? "Đơn mới" : "TTCN"}</Pill>
                    </td>
                    <td>{row.product ? "SP" : "DV"}</td>
                    <td>{row.customer_name ?? ""}</td>
                    <td>{row.customer_code ?? ""}</td>
                    <td className="max-w-80 truncate" title={row.services ?? ""}>{row.services ?? ""}</td>
                    <td className="text-right tabular-nums">{row.sales ? money(row.sales) : ""}</td>
                    <td className="text-right tabular-nums">{money(row.collected)}</td>
                    <td className="text-right tabular-nums">{row.owed ? money(row.owed) : ""}</td>
                    {methods.map(([key]) => (
                      <td key={key} className="text-right tabular-nums">
                        {row.methods[key] ? money(row.methods[key]) : ""}
                      </td>
                    ))}
                    <td className="text-right tabular-nums">{row.refunds ? money(row.refunds) : ""}</td>
                    {FEE_KEYS.map((key) => (
                      <td key={key} className={`text-right tabular-nums ${row.typed.includes(key) ? "font-semibold text-[var(--accent)]" : ""}`}>
                        {row.fees[key] ? money(row.fees[key]) : ""}
                      </td>
                    ))}
                    <td className="text-right tabular-nums">{money(row.fees.total)}</td>
                    <td className="text-right tabular-nums">{money(row.vat)}</td>
                    <td className="text-right font-medium tabular-nums">{money(row.net)}</td>
                    <td className="text-right tabular-nums"><Check value={row.seller_check ?? 0} /></td>
                    <td className="text-right tabular-nums"><Check value={row.doctor_check ?? null} /></td>
                    {sellers.map((item, index) => (
                      <Fragment key={index}>
                        <td>{item?.name ?? ""}</td>
                        <td className="text-right tabular-nums">{item ? money(item.amount) : ""}</td>
                      </Fragment>
                    ))}
                    {DOCTOR_KINDS.map((kind) => (
                      <Fragment key={kind}>
                        <td>{row.doctors[kind]?.name ?? ""}</td>
                        <td className="text-right tabular-nums">
                          {row.doctors[kind] ? money(row.doctors[kind].amount) : ""}
                        </td>
                      </Fragment>
                    ))}
                    <td>{row.sale ?? ""}</td>
                    <td>{row.marketing ?? ""}</td>
                    <td>{row.carepage ?? ""}</td>
                    <td>{row.branch ?? ""}</td>
                    <td>{row.note ?? ""}</td>
                    <td className="text-[var(--bad)]">{row.owed && (row.owed_days ?? 0) > 45 ? `QUÁ (${row.owed_days} ngày)` : ""}</td>
                    {canEdit ? (
                      <td>
                        {row.order_id !== null ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setEditing(row);
                            }}
                            className="min-h-9 text-xs text-[var(--accent)] hover:underline"
                            aria-label={`Sửa phí ${row.order_code ?? ""} ngày ${dayLabel(row.day)}`}
                          >
                            Sửa phí
                          </button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty message="Không có dòng nào." />
      )}
      {editing ? <FeeDialog row={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function SummaryTable({ data }: { data: Ledger }) {
  let running = 0;
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[72rem]">
        <thead>
          <tr>
            <th>Ngày</th>
            <th className="text-right">Doanh số</th>
            <th className="text-right">Còn nợ</th>
            <th className="text-right">DT dịch vụ</th>
            <th className="text-right">DT sản phẩm</th>
            <th className="text-right">Tổng thực thu</th>
            <th className="text-right">Hoàn tiền</th>
            <th className="text-right">Thực thu thuần</th>
            <th className="text-right">Phí</th>
            <th className="text-right">Sau VAT & phí</th>
            <th className="text-right">% KPI về</th>
          </tr>
        </thead>
        <tbody>
          {data.days.map((day) => {
            running += day.collected;
            return (
              <tr key={day.day} data-testid="summary-row">
                <td>{dayLabel(day.day)}</td>
                <td className="text-right tabular-nums">{money(day.sales)}</td>
                <td className="text-right tabular-nums">{money(day.owed)}</td>
                <td className="text-right tabular-nums">{money(day.service_sales)}</td>
                <td className="text-right tabular-nums">{money(day.product_sales)}</td>
                <td className="text-right tabular-nums">{money(day.received)}</td>
                <td className="text-right tabular-nums">{money(day.refunds)}</td>
                <td className="text-right tabular-nums">{money(day.collected)}</td>
                <td className="text-right tabular-nums">{money(day.fees)}</td>
                <td className="text-right tabular-nums">{money(day.net)}</td>
                <td className="text-right tabular-nums">
                  {data.kpi_month ? `${((running / data.kpi_month) * 100).toFixed(1).replace(".", ",")}%` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LedgerScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const month = params.get("month") ?? monthOf();
  const [tab, setTab] = useState<"rows" | "summary">("rows");
  const [since, until] = monthBounds(month);
  const access = useQuery({ queryKey: ["finance", "access"], queryFn: financeApi.access });
  const ledger = useQuery({
    queryKey: ["finance", "ledger", since, until],
    queryFn: () => financeApi.ledger(since, until),
    enabled: Boolean(access.data?.revenue),
    retry: false,
  });
  if (access.isPending) return <Loading />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!access.data.revenue) return <Empty message="Bạn chưa có quyền xem bảng kê doanh thu." />;
  const go = (value: string) => router.replace(`/finance/ledger?month=${value}`);
  return (
    <div className="space-y-5">
      <PageHeader
        title="Bảng kê doanh thu"
        subtitle="Theo bố cục BCLT của file kế toán: mỗi dòng là một đơn trong một ngày (đơn mới hoặc thanh toán công nợ)."
        action={
          <a
            href={financeApi.ledgerExportUrl(month)}
            className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]"
          >
            Xuất Excel
          </a>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, -1))} aria-label="Tháng trước">
          ‹
        </SecondaryButton>
        <span className="min-w-28 text-center font-semibold">
          Tháng {month.slice(5, 7)}/{month.slice(0, 4)}
        </span>
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, 1))} aria-label="Tháng sau">
          ›
        </SecondaryButton>
      </div>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Bảng kê">
        {(
          [
            ["rows", "Bảng kê (BCLT)"],
            ["summary", "Tổng hợp theo ngày"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`min-h-11 rounded-lg border px-3 text-sm ${
              tab === key
                ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {ledger.isPending ? <Loading label="Đang lập bảng kê…" /> : null}
      {ledger.isError ? <ErrorBox error={ledger.error} onRetry={() => ledger.refetch()} /> : null}
      {ledger.data ? (
        tab === "rows" ? (
          <RowsTable data={ledger.data} canEdit={ledger.data.can_edit_fees} />
        ) : (
          <SummaryTable data={ledger.data} />
        )
      ) : null}
    </div>
  );
}

export default function FinanceLedgerPage() {
  return (
    <Suspense fallback={<Loading />}>
      <LedgerScreen />
    </Suspense>
  );
}
