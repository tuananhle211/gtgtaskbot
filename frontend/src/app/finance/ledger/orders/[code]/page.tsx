"use client";

/**
 * Tài chính › Bảng kê › one order (user 13/11/2026: "click vào phải chia doanh
 * thu về các nhân viên đóng vai trò ở đơn hàng đấy, đủ các thông tin như file
 * excel"). Every role on the order and the revenue credited to each (the
 * accounting sheet: NV bán 1-3 = TVV / KTV / y tá, Bác sĩ phẫu / tiêm / khác /
 * khác 2, Sale / MKT / Chat page, người lên đơn, KTV làm tua), the BCLT
 * checks per day, the services, payments, shares, công tua, and the day rows
 * with their fees ("Sửa phí").
 */

import Link from "next/link";
import { use, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FeeDialog } from "@/components/finance-fees";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { FEE_LABELS, financeApi, money, type CrmPerson, type FeeKey, type LedgerRow, type OrderDetail } from "@/lib/finance";

const FEE_KEYS: FeeKey[] = ["card_fee", "referral_fee", "car_fee", "cost"];

function Who({ person }: { person: CrmPerson | null }) {
  if (!person) return <span className="text-[var(--text-muted)]">—</span>;
  return (
    <>
      <span>{person.name}</span>
      {person.user_name ? (
        <span className="block text-xs text-[var(--text-muted)]">APM: {person.user_name}</span>
      ) : null}
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2" aria-label={title}>
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function CheckCell({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-[var(--text-muted)]">—</span>;
  return value === 0 ? (
    <Pill tone="good">Khớp</Pill>
  ) : (
    <span className="font-medium text-[var(--bad)]">Lệch {money(value)}</span>
  );
}

function Roles({ data }: { data: OrderDetail }) {
  const shared = data.roles.reduce((sum, role) => sum + (role.amount ?? 0), 0);
  return (
    <Section title="Vai trò trên đơn & doanh thu được chia">
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[40rem]">
          <thead>
            <tr>
              <th>Vai trò</th>
              <th>Người (CRM)</th>
              <th className="text-right">Doanh thu được chia</th>
              <th>Ngày</th>
            </tr>
          </thead>
          <tbody>
            {data.roles.map((role, index) => (
              <tr key={index} data-testid="order-role">
                <td className="whitespace-nowrap">{role.label}</td>
                <td>
                  <Who person={role.person} />
                </td>
                <td className="text-right tabular-nums">{role.amount !== null ? money(role.amount) : ""}</td>
                <td className="whitespace-nowrap">{role.day ? formatDate(role.day) : ""}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-semibold">
              <td colSpan={2}>Tổng doanh thu được chia</td>
              <td className="text-right tabular-nums">{money(shared)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </Section>
  );
}

function Days({ data }: { data: OrderDetail }) {
  const [editing, setEditing] = useState<LedgerRow | null>(null);
  return (
    <Section title="Bảng kê theo ngày (BCLT)">
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[90rem] whitespace-nowrap">
          <thead>
            <tr>
              <th>Ngày</th>
              <th>Phân loại</th>
              <th className="text-right">Doanh số</th>
              <th className="text-right">Thực thu</th>
              <th className="text-right">Nợ</th>
              {FEE_KEYS.map((key) => (
                <th key={key} className="text-right">
                  {FEE_LABELS[key]}
                </th>
              ))}
              <th className="text-right">VAT</th>
              <th className="text-right">Sau VAT & phí</th>
              <th>Check DT TVV.KTV.Y tá</th>
              <th>Check DT BS</th>
              {data.can_edit_fees ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={`${row.day}-${row.kind}`} data-testid="order-day">
                <td>{formatDate(row.day)}</td>
                <td>
                  <Pill tone={row.kind === "NEW" ? "good" : "neutral"}>{row.kind === "NEW" ? "Đơn mới" : "TTCN"}</Pill>
                </td>
                <td className="text-right tabular-nums">{row.sales ? money(row.sales) : ""}</td>
                <td className="text-right tabular-nums">{money(row.collected)}</td>
                <td className="text-right tabular-nums">{row.owed ? money(row.owed) : ""}</td>
                {FEE_KEYS.map((key) => (
                  <td key={key} className={`text-right tabular-nums ${row.typed.includes(key) ? "font-semibold text-[var(--accent)]" : ""}`}>
                    {money(row.fees[key])}
                  </td>
                ))}
                <td className="text-right tabular-nums">{money(row.vat)}</td>
                <td className="text-right font-medium tabular-nums">{money(row.net)}</td>
                <td>
                  <CheckCell value={row.seller_check} />
                </td>
                <td>
                  <CheckCell value={row.doctor_check} />
                </td>
                {data.can_edit_fees ? (
                  <td>
                    {row.order_id !== null ? (
                      <button type="button" className="min-h-9 text-xs text-[var(--accent)] hover:underline" onClick={() => setEditing(row)}>
                        Sửa phí
                      </button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Như file kế toán: Check DT TVV.KTV.Y tá = DT NV bán 1 + 2 + 3 - thực thu; Check DT BS = DT bác sĩ
        (phẫu + tiêm + khác) - thực thu, chỉ với dịch vụ. Khớp = 0.
      </p>
      {editing ? <FeeDialog row={editing} onClose={() => setEditing(null)} /> : null}
    </Section>
  );
}

export default function LedgerOrderPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const order = useQuery({
    queryKey: ["finance", "ledger-order", code],
    queryFn: () => financeApi.order(decodeURIComponent(code)),
    retry: false,
  });
  if (order.isPending) return <Loading label="Đang tải đơn hàng…" />;
  if (order.isError) return <ErrorBox error={order.error} onRetry={() => order.refetch()} />;
  const data = order.data;
  const paid = data.payments.filter((p) => p.counted).reduce((sum, p) => sum + p.amount, 0);
  const tiles: [string, string][] = [
    ["Doanh số", data.all_total !== null ? money(data.all_total) : "—"],
    ["Trước VAT", data.amount_before_vat !== null ? money(data.amount_before_vat) : "—"],
    ["VAT", data.vat_amount !== null ? `${money(data.vat_amount)}${data.vat_percent ? ` (${data.vat_percent}%)` : ""}` : "—"],
    ["Đã thu (các lần)", money(paid)],
    ["Còn nợ", data.the_rest !== null ? money(data.the_rest) : "—"],
    ["Giảm giá", data.discount ? money(data.discount) : "0 ₫"],
  ];
  return (
    <div className="space-y-6">
      <Link href="/finance/ledger" className="text-sm text-[var(--accent)] hover:underline">
        ← Bảng kê
      </Link>
      <PageHeader
        title={`Đơn ${data.code}`}
        subtitle={[
          data.customer_name,
          data.customer_code,
          data.created_on ? `tạo ${formatDate(data.created_on)}` : null,
          data.branch,
          data.entity,
          data.role_type,
          data.order_type,
        ]
          .filter(Boolean)
          .join(" · ")}
      />
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6" data-testid="order-tiles">
        {tiles.map(([label, value]) => (
          <div key={label} className="panel p-3">
            <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <Roles data={data} />
      <Days data={data} />
      {data.items.length ? (
        <Section title="Dịch vụ / sản phẩm trên đơn">
          <div className="panel table-scroll-x">
            <table className="table-dense min-w-[32rem]">
              <thead>
                <tr>
                  <th>Dịch vụ</th>
                  <th className="text-right">Số lượng</th>
                  <th className="text-right">Thành tiền</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item, index) => (
                  <tr key={index}>
                    <td>{item.service_name ?? "—"}</td>
                    <td className="text-right tabular-nums">{Number(item.quantity) ? item.quantity : "—"}</td>
                    <td className="text-right tabular-nums">{money(item.total_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      <Section title="Các lần thanh toán">
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[64rem]">
            <thead>
              <tr>
                <th>Ngày</th>
                <th>Phương thức</th>
                <th className="text-right">Số tiền</th>
                <th>Chi nhánh</th>
                <th>Sale</th>
                <th>MKT</th>
                <th>CSKH</th>
                <th>Chat page</th>
              </tr>
            </thead>
            <tbody>
              {data.payments.map((payment) => (
                <tr key={payment.id} data-testid="order-payment">
                  <td className="whitespace-nowrap">{formatDate(payment.day)}</td>
                  <td>
                    {payment.method}
                    {!payment.counted ? <span className="block text-xs text-[var(--text-muted)]">không tính thực thu</span> : null}
                  </td>
                  <td className="text-right tabular-nums">{money(payment.amount)}</td>
                  <td>{payment.branch ?? "—"}</td>
                  <td><Who person={payment.sale} /></td>
                  <td><Who person={payment.marketing} /></td>
                  <td><Who person={payment.cskh} /></td>
                  <td><Who person={payment.carepage} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          Sale, MKT, CSKH, Chat page theo hồ sơ khách trên CRM lúc đồng bộ (CRM quy định), không hẳn là người bán đơn.
        </p>
      </Section>
      <Section title="Chia doanh thu (Kết xuất PM chia DT)">
        {data.shares.length ? (
          <div className="panel table-scroll-x">
            <table className="table-dense min-w-[56rem]">
              <thead>
                <tr>
                  <th>Ngày thanh toán</th>
                  <th>Nhân viên được chia</th>
                  <th>Phòng (CRM)</th>
                  <th>Loại chia</th>
                  <th>Phương thức</th>
                  <th className="text-right">Thanh toán</th>
                  <th className="text-right">Doanh thu được chia</th>
                </tr>
              </thead>
              <tbody>
                {data.shares.map((share, index) => (
                  <tr key={index} data-testid="order-share">
                    <td className="whitespace-nowrap">{formatDate(share.day)}</td>
                    <td><Who person={share.person} /></td>
                    <td>{share.department}</td>
                    <td>{share.kind}</td>
                    <td>{share.payment_type}</td>
                    <td className="text-right tabular-nums">{money(share.payment_amount)}</td>
                    <td className="text-right font-medium tabular-nums">{money(share.shared)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">CRM chưa chia doanh thu đơn này cho ai.</p>
        )}
      </Section>
      <Section title="Công tua (Kết xuất tour)">
        {data.tours.length ? (
          <div className="panel table-scroll-x">
            <table className="table-dense min-w-[56rem]">
              <thead>
                <tr>
                  <th>Thời điểm</th>
                  <th>Nhân viên thực hiện</th>
                  <th>Loại công</th>
                  <th>Loại liệu trình</th>
                  <th>Dịch vụ</th>
                  <th>Thủ thuật</th>
                  <th>Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {data.tours.map((tour, index) => (
                  <tr key={index} data-testid="order-tour">
                    <td className="whitespace-nowrap">{formatDateTime(tour.at)}</td>
                    <td><Who person={tour.person} /></td>
                    <td>{tour.work_type}</td>
                    <td>{tour.treatment}</td>
                    <td>{tour.service ?? ""}</td>
                    <td>{tour.procedure ?? ""}</td>
                    <td>{tour.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">Chưa có công tua cho đơn này.</p>
        )}
      </Section>
    </div>
  );
}
