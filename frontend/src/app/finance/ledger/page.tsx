"use client";

/**
 * Tài chính › Bảng kê (đợt 2): the accounting sheet's BCLT and Tổng hợp, built
 * from the CRM. A row = an order on one day ("Đơn mới" the day it was created,
 * "TTCN" a later payment day). The four fees are automatic (card % per payment
 * method, cost price per service, "Cấu hình › Phí & giá cost") unless Kế toán
 * typed them ("Sửa", FINANCE FEES). "Xuất Excel" = the BCLT + Tổng hợp
 * workbook of the month. Since 13/11 the table follows the sheet "BCLT new":
 * the five marks first (APM's suggestion in italics until Kế toán picks),
 * "Diễn giải" one service a line with its price, one NV bán + one Bác sỹ
 * column with every other person on a line of their own below the order.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
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
  type MarkKey,
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

type Line = { row: LedgerRow; index: number };

/** One seller / doctor per line ("BCLT new": one NV bán + one Bác sỹ column). */
function doctorLines(row: LedgerRow): { name: string; kind: string; amount: number }[] {
  if (row.doctor_lines) return row.doctor_lines;
  return Object.entries(row.doctors).map(([kind, who]) => ({ name: who.name, kind, amount: who.amount }));
}

function doctorName(line: { name: string; kind: string }): string {
  const short = line.kind.replace(/^Bác sĩ\s*/, "");
  return short ? `${line.name} (${short})` : line.name;
}

function MarkCell({ row, mark }: { row: LedgerRow; mark: MarkKey }) {
  const value = row.marks?.[mark];
  if (!value) return null;
  const picked = row.picked?.includes(mark);
  return (
    <span
      className={picked ? "font-semibold" : "italic text-[var(--text-muted)]"}
      title={picked ? "Kế toán đã chọn" : "APM gợi ý - bấm Sửa để chọn lại"}
    >
      {value}
    </span>
  );
}

function amount(value: number | undefined): string {
  return value ? money(value) : "";
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
          [
            row.order_code,
            row.customer_code,
            row.customer_name,
            row.sale,
            row.marketing,
            row.services,
            ...row.sellers.map((item) => item.name),
            ...doctorLines(row).map((item) => item.name),
          ]
            .filter(Boolean)
            .some((value) => fold(String(value)).includes(text))),
    );
  }, [data.rows, day, q]);
  const lines = useMemo(
    () =>
      rows.flatMap((row) => {
        const count = Math.max(row.sellers.length, doctorLines(row).length, 1);
        return Array.from({ length: count }, (_, index): Line => ({ row, index }));
      }),
    [rows],
  );
  const methods = Object.entries(data.methods);
  const marks = data.marks ?? [];
  const open = (row: LedgerRow) => {
    if (row.order_code) router.push(`/finance/ledger/orders/${encodeURIComponent(row.order_code)}`);
  };
  const sticky = "sticky left-0 z-10 bg-[var(--surface)]";
  const num = "text-right tabular-nums";
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
          Tìm mã đơn / mã khách / tên / dịch vụ / nhân viên
          <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${field}`} />
        </label>
        <span className="pb-3 text-sm text-[var(--text-muted)]">
          {rows.length} dòng · bấm một đơn để xem chia doanh thu · chữ nghiêng = APM gợi ý
        </span>
      </div>
      {rows.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[200rem]">
            <thead>
              <tr className="whitespace-nowrap">
                {marks.map((mark) => (
                  <th key={mark.key}>{mark.label}</th>
                ))}
                <th className={sticky}>Số Hóa đơn</th>
                <th>SP/DV</th>
                <th>Ngày</th>
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
                <th className="text-right" title="= Hoàn tiền + 4 khoản phí">
                  Tổng hợp phí
                </th>
                <th className="text-right" title="= Thực thu - VAT - Tổng hợp phí">
                  Thực thu sau phí
                </th>
                <th>NV bán</th>
                <th className="text-right">Doanh thu</th>
                <th>Bác sỹ</th>
                <th className="text-right">Doanh thu</th>
                <th>Sale (người phụ trách)</th>
                <th>MKT (nguồn)</th>
                <th>Chat page (người tạo)</th>
                <th>Công nợ quá 45 ngày</th>
                <th>Chi nhánh</th>
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {lines.map(({ row, index }) => {
                const seller = row.sellers[index];
                const doctor = doctorLines(row)[index];
                const people = (
                  <>
                    <td className="whitespace-nowrap">{seller?.name ?? ""}</td>
                    <td className={num}>{seller ? money(seller.amount) : ""}</td>
                    <td className="whitespace-nowrap">{doctor ? doctorName(doctor) : ""}</td>
                    <td className={num}>{doctor ? money(doctor.amount) : ""}</td>
                  </>
                );
                const key = `${row.order_id ?? row.order_code}-${row.day}-${row.kind}`;
                const clickable = row.order_code ? "cursor-pointer hover:bg-[var(--surface-muted)]" : "";
                if (index > 0) {
                  return (
                    <tr key={`${key}-${index}`} data-testid="ledger-subrow" onClick={() => open(row)} className={clickable}>
                      {marks.map((mark) => (
                        <td key={mark.key} />
                      ))}
                      <td className={`${sticky} text-xs text-[var(--text-muted)]`}>↳ {row.order_code ?? ""}</td>
                      <td colSpan={5 + 3 + methods.length + 1 + FEE_KEYS.length + 2} />
                      {people}
                      <td colSpan={5 + (canEdit ? 1 : 0)} />
                    </tr>
                  );
                }
                return (
                  <tr key={key} data-testid="ledger-row" onClick={() => open(row)} className={`${clickable} align-top`}>
                    {marks.map((mark) => (
                      <td key={mark.key} className="whitespace-nowrap">
                        <MarkCell row={row} mark={mark.key} />
                      </td>
                    ))}
                    <td className={`${sticky} whitespace-nowrap font-medium text-[var(--accent)]`}>
                      {row.order_code ?? "—"}
                      {row.kind === "TTCN" ? (
                        <span className="ml-1">
                          <Pill tone="neutral">TTCN</Pill>
                        </span>
                      ) : null}
                    </td>
                    <td>{row.product ? "SP" : "DV"}</td>
                    <td className="whitespace-nowrap">{dayLabel(row.day)}</td>
                    <td className="whitespace-nowrap">{row.customer_name ?? ""}</td>
                    <td className="whitespace-nowrap">{row.customer_code ?? ""}</td>
                    <td className="min-w-64">
                      {row.items?.length ? (
                        <ul className="space-y-0.5" data-testid="ledger-services">
                          {row.items.map((item, at) => (
                            <li key={at} className="flex justify-between gap-3">
                              <span>{item.name}</span>
                              <span className="tabular-nums text-[var(--text-muted)]">{money(item.price)}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        (row.services ?? "")
                      )}
                    </td>
                    <td className={num}>{amount(row.sales)}</td>
                    <td className={num}>{money(row.received ?? row.collected - row.refunds)}</td>
                    <td className={num}>{amount(row.owed)}</td>
                    {methods.map(([method]) => (
                      <td key={method} className={num}>
                        {amount(row.methods[method])}
                      </td>
                    ))}
                    <td className={num}>{amount(-row.refunds)}</td>
                    {FEE_KEYS.map((fee) => (
                      <td key={fee} className={`${num} ${row.typed.includes(fee) ? "font-semibold text-[var(--accent)]" : ""}`}>
                        {amount(row.fees[fee])}
                      </td>
                    ))}
                    <td className={num}>{money(row.charges ?? row.fees.total - row.refunds)}</td>
                    <td className={`${num} font-medium`}>{money(row.net)}</td>
                    {people}
                    <td className="whitespace-nowrap">{row.sale ?? ""}</td>
                    <td className="whitespace-nowrap">{row.marketing ?? ""}</td>
                    <td className="whitespace-nowrap">{row.carepage ?? ""}</td>
                    <td className="whitespace-nowrap text-[var(--bad)]">
                      {row.owed && (row.owed_days ?? 0) > 45 ? `QUÁ (${row.owed_days} ngày)` : ""}
                    </td>
                    <td className="whitespace-nowrap">{row.branch ?? ""}</td>
                    {canEdit ? (
                      <td>
                        {row.order_id !== null ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setEditing(row);
                            }}
                            className="min-h-9 whitespace-nowrap text-xs text-[var(--accent)] hover:underline"
                            aria-label={`Sửa dòng ${row.order_code ?? ""} ngày ${dayLabel(row.day)}`}
                          >
                            Sửa
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
      {editing ? <FeeDialog row={editing} marks={marks} onClose={() => setEditing(null)} /> : null}
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
        subtitle="Theo bố cục BCLT new của file kế toán: mỗi dòng là một đơn trong một ngày (đơn mới hoặc thanh toán công nợ), mỗi người được chia một dòng."
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
