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
 * Since 14/11 "Cột hiển thị" switches columns on / off per person (kept on
 * the server, `/api/finance/ledger/columns`); the order code always shows.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FeeDialog } from "@/components/finance-fees";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
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
const METHOD_ORDER = ["5", "2", "6", "4", "1"];
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

type Column = {
  /** The switch: columns sharing a key (a name + its amount) go on / off together. */
  key: string;
  group: string;
  label: string;
  /** The picker's name when the header alone says too little. */
  pick?: string;
  numeric?: boolean;
  wrap?: boolean;
  title?: string;
  cell: (row: LedgerRow) => ReactNode;
  /** A per-person column: what a sub-line (2nd, 3rd person…) shows. */
  person?: (row: LedgerRow, index: number) => ReactNode;
};

function sellerAt(row: LedgerRow, index: number) {
  return row.sellers[index];
}

function doctorAt(row: LedgerRow, index: number) {
  return doctorLines(row)[index];
}

/** The table's columns in the sheet "BCLT new"'s order. */
function ledgerColumns(data: Ledger): Column[] {
  const marks = data.marks ?? [];
  // A JS object puts number-like keys in number order; the sheet's is
  // Trả góp, Thẻ, Nhà xe, CK, Tiền mặt (backend METHOD_ORDER).
  const methods = [
    ...METHOD_ORDER.filter((key) => key in data.methods),
    ...Object.keys(data.methods).filter((key) => !METHOD_ORDER.includes(key)),
  ].map((key) => [key, data.methods[key]] as const);
  return [
    ...marks.map(
      (mark): Column => ({
        key: `mark:${mark.key}`,
        group: "Phân loại dòng",
        label: mark.label,
        cell: (row) => <MarkCell row={row} mark={mark.key} />,
      }),
    ),
    { key: "product", group: "Đơn & khách", label: "SP/DV", cell: (row) => (row.product ? "SP" : "DV") },
    { key: "day", group: "Đơn & khách", label: "Ngày", cell: (row) => dayLabel(row.day) },
    { key: "customer_name", group: "Đơn & khách", label: "Tên KH", cell: (row) => row.customer_name ?? "" },
    { key: "customer_code", group: "Đơn & khách", label: "Mã KH", cell: (row) => row.customer_code ?? "" },
    {
      key: "services",
      group: "Đơn & khách",
      label: "Diễn giải",
      wrap: true,
      cell: (row) =>
        row.items?.length ? (
          <ul className="min-w-64 space-y-0.5" data-testid="ledger-services">
            {row.items.map((item, at) => (
              <li key={at} className="flex justify-between gap-3">
                <span>{item.name}</span>
                <span className="tabular-nums text-[var(--text-muted)]">{money(item.price)}</span>
              </li>
            ))}
          </ul>
        ) : (
          (row.services ?? "")
        ),
    },
    { key: "sales", group: "Tiền", label: "Doanh số", numeric: true, cell: (row) => amount(row.sales) },
    {
      key: "received",
      group: "Tiền",
      label: "Thực thu",
      numeric: true,
      cell: (row) => money(row.received ?? row.collected - row.refunds),
    },
    { key: "owed", group: "Tiền", label: "Nợ", numeric: true, cell: (row) => amount(row.owed) },
    ...methods.map(
      ([method, label]): Column => ({
        key: `method:${method}`,
        group: "Phương thức thanh toán",
        label,
        numeric: true,
        cell: (row) => amount(row.methods[method]),
      }),
    ),
    { key: "refunds", group: "Phí", label: "Hoàn tiền", numeric: true, cell: (row) => amount(-row.refunds) },
    ...FEE_KEYS.map(
      (fee): Column => ({
        key: `fee:${fee}`,
        group: "Phí",
        label: FEE_LABELS[fee],
        numeric: true,
        cell: (row) =>
          row.typed.includes(fee) ? (
            <span className="font-semibold text-[var(--accent)]">{amount(row.fees[fee])}</span>
          ) : (
            amount(row.fees[fee])
          ),
      }),
    ),
    {
      key: "charges",
      group: "Phí",
      label: "Tổng hợp phí",
      title: "= Hoàn tiền + 4 khoản phí",
      numeric: true,
      cell: (row) => money(row.charges ?? row.fees.total - row.refunds),
    },
    {
      key: "net",
      group: "Tiền",
      label: "Thực thu sau phí",
      title: "= Thực thu - VAT - Tổng hợp phí",
      numeric: true,
      cell: (row) => <span className="font-medium">{money(row.net)}</span>,
    },
    {
      key: "seller",
      group: "Chia doanh thu",
      label: "NV bán",
      pick: "NV bán + doanh thu",
      cell: (row) => sellerAt(row, 0)?.name ?? "",
      person: (row, index) => sellerAt(row, index)?.name ?? "",
    },
    {
      key: "seller",
      group: "Chia doanh thu",
      label: "Doanh thu",
      numeric: true,
      cell: (row) => (sellerAt(row, 0) ? money(sellerAt(row, 0)!.amount) : ""),
      person: (row, index) => (sellerAt(row, index) ? money(sellerAt(row, index)!.amount) : ""),
    },
    {
      key: "doctor",
      group: "Chia doanh thu",
      label: "Bác sỹ",
      pick: "Bác sỹ + doanh thu",
      cell: (row) => (doctorAt(row, 0) ? doctorName(doctorAt(row, 0)!) : ""),
      person: (row, index) => (doctorAt(row, index) ? doctorName(doctorAt(row, index)!) : ""),
    },
    {
      key: "doctor",
      group: "Chia doanh thu",
      label: "Doanh thu",
      numeric: true,
      cell: (row) => (doctorAt(row, 0) ? money(doctorAt(row, 0)!.amount) : ""),
      person: (row, index) => (doctorAt(row, index) ? money(doctorAt(row, index)!.amount) : ""),
    },
    { key: "sale", group: "Người phụ trách", label: "Sale (người phụ trách)", cell: (row) => row.sale ?? "" },
    { key: "marketing", group: "Người phụ trách", label: "MKT (nguồn)", cell: (row) => row.marketing ?? "" },
    { key: "carepage", group: "Người phụ trách", label: "Chat page (người tạo)", cell: (row) => row.carepage ?? "" },
    {
      key: "overdue",
      group: "Khác",
      label: "Công nợ quá 45 ngày",
      cell: (row) =>
        row.owed && (row.owed_days ?? 0) > 45 ? (
          <span className="text-[var(--bad)]">QUÁ ({row.owed_days} ngày)</span>
        ) : (
          ""
        ),
    },
    { key: "branch", group: "Khác", label: "Chi nhánh", cell: (row) => row.branch ?? "" },
  ];
}

/** The picker's switches: one per key, in table order, grouped. */
function switchesOf(columns: Column[]): { group: string; items: { key: string; label: string }[] }[] {
  const groups: { group: string; items: { key: string; label: string }[] }[] = [];
  const seen = new Set<string>();
  for (const column of columns) {
    if (seen.has(column.key)) continue;
    seen.add(column.key);
    let group = groups.find((item) => item.group === column.group);
    if (!group) groups.push((group = { group: column.group, items: [] }));
    group.items.push({ key: column.key, label: column.pick ?? column.label });
  }
  return groups;
}

const LEDGER_COLUMNS_KEY = ["finance", "ledger-columns"] as const;

/**
 * "Cột hiển thị" (14/11): which columns I see. Saved on the server at each
 * tick (my own, every device; like the menu pins: no confirmation, the
 * screen changes at once and goes back if the save fails). The Excel export
 * keeps every column.
 */
function ColumnsDialog({
  columns,
  hidden,
  onClose,
}: {
  columns: Column[];
  hidden: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  const groups = switchesOf(columns);
  const keys = groups.flatMap((group) => group.items.map((item) => item.key));
  const save = (next: string[]) => {
    const before = queryClient.getQueryData(LEDGER_COLUMNS_KEY);
    queryClient.setQueryData(LEDGER_COLUMNS_KEY, { hidden: next });
    setError(null);
    financeApi.setLedgerColumns(next).catch((failure: unknown) => {
      queryClient.setQueryData(LEDGER_COLUMNS_KEY, before);
      setError(failure);
    });
  };
  const toggle = (key: string, shown: boolean) =>
    save(shown ? hidden.filter((item) => item !== key) : [...hidden, key]);
  const shownCount = keys.filter((key) => !hidden.includes(key)).length;
  return (
    <Modal open onClose={onClose} title="Cột hiển thị" description={`Đang hiện ${shownCount}/${keys.length} cột. Mã đơn luôn hiện; file Excel vẫn đủ cột.`}>
      <ModalBody className="space-y-4">
        {error ? <NoticeBox error={error} /> : null}
        <div className="flex flex-wrap gap-2">
          <SecondaryButton type="button" onClick={() => save([])}>
            Hiện tất cả
          </SecondaryButton>
          <SecondaryButton type="button" onClick={() => save(keys)}>
            Ẩn tất cả
          </SecondaryButton>
        </div>
        {groups.map((group) => (
          <fieldset key={group.group} className="space-y-1">
            <legend className="brand-eyebrow mb-1">{group.group}</legend>
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
              {group.items.map((item) => (
                <label key={item.key} className="flex min-h-10 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={!hidden.includes(item.key)}
                    onChange={(event) => toggle(item.key, event.target.checked)}
                  />
                  {item.label}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Xong
        </SecondaryButton>
      </ModalFooter>
    </Modal>
  );
}

function RowsTable({ data, canEdit }: { data: Ledger; canEdit: boolean }) {
  const router = useRouter();
  const [day, setDay] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<LedgerRow | null>(null);
  const [picking, setPicking] = useState(false);
  const prefs = useQuery({ queryKey: LEDGER_COLUMNS_KEY, queryFn: financeApi.ledgerColumns, retry: false });
  const hidden = prefs.data?.hidden ?? [];
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
  const columns = useMemo(() => ledgerColumns(data), [data]);
  const shown = columns.filter((column) => !hidden.includes(column.key));
  // "BCLT new": the five marks come before Số Hóa đơn.
  const lead = shown.filter((column) => column.key.startsWith("mark:"));
  const rest = shown.filter((column) => !column.key.startsWith("mark:"));
  const marks = data.marks ?? [];
  // Extra lines only for the people whose columns are shown.
  const sellersShown = !hidden.includes("seller");
  const doctorsShown = !hidden.includes("doctor");
  const lines = useMemo(
    () =>
      rows.flatMap((row) => {
        const count = Math.max(
          sellersShown ? row.sellers.length : 0,
          doctorsShown ? doctorLines(row).length : 0,
          1,
        );
        return Array.from({ length: count }, (_, index): Line => ({ row, index }));
      }),
    [rows, sellersShown, doctorsShown],
  );
  const open = (row: LedgerRow) => {
    if (row.order_code) router.push(`/finance/ledger/orders/${encodeURIComponent(row.order_code)}`);
  };
  const sticky = "sticky left-0 z-10 bg-[var(--surface)]";
  const hiddenCount = switchesOf(columns).reduce(
    (sum, group) => sum + group.items.filter((item) => hidden.includes(item.key)).length,
    0,
  );
  const cellClass = (column: Column) =>
    `${column.numeric ? "text-right tabular-nums" : ""} ${column.wrap ? "" : "whitespace-nowrap"}`;
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
        <SecondaryButton type="button" onClick={() => setPicking(true)}>
          Cột hiển thị{hiddenCount ? ` (ẩn ${hiddenCount})` : ""}
        </SecondaryButton>
        <span className="pb-3 text-sm text-[var(--text-muted)]">
          {rows.length} dòng · bấm một đơn để xem chia doanh thu{marks.length ? " · chữ nghiêng = APM gợi ý" : ""}
        </span>
      </div>
      {rows.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-full">
            <thead>
              <tr className="whitespace-nowrap">
                {lead.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
                <th className={sticky}>Số Hóa đơn</th>
                {rest.map((column, at) => (
                  <th key={`${column.key}-${at}`} className={column.numeric ? "text-right" : ""} title={column.title}>
                    {column.label}
                  </th>
                ))}
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {lines.map(({ row, index }) => {
                const key = `${row.order_id ?? row.order_code}-${row.day}-${row.kind}`;
                const clickable = row.order_code ? "cursor-pointer hover:bg-[var(--surface-muted)]" : "";
                if (index > 0) {
                  return (
                    <tr key={`${key}-${index}`} data-testid="ledger-subrow" onClick={() => open(row)} className={clickable}>
                      {lead.map((column) => (
                        <td key={column.key} />
                      ))}
                      <td className={`${sticky} whitespace-nowrap text-xs text-[var(--text-muted)]`}>↳ {row.order_code ?? ""}</td>
                      {rest.map((column, at) => (
                        <td key={`${column.key}-${at}`} className={cellClass(column)}>
                          {column.person ? column.person(row, index) : null}
                        </td>
                      ))}
                      {canEdit ? <td /> : null}
                    </tr>
                  );
                }
                return (
                  <tr key={key} data-testid="ledger-row" onClick={() => open(row)} className={`${clickable} align-top`}>
                    {lead.map((column) => (
                      <td key={column.key} className={cellClass(column)}>
                        {column.cell(row)}
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
                    {rest.map((column, at) => (
                      <td key={`${column.key}-${at}`} className={cellClass(column)}>
                        {column.cell(row)}
                      </td>
                    ))}
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
      {picking ? <ColumnsDialog columns={columns} hidden={hidden} onClose={() => setPicking(false)} /> : null}
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
