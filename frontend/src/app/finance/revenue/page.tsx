"use client";

/**
 * Tài chính › Doanh thu (0105): one month of the CRM's numbers, read in the
 * one run shared with Phòng Ads (0h, 5h, 12h, 18h). Thực thu = tiền về by the
 * day paid (VAT included, points left out, refunds negative); Doanh số = the
 * orders created in the month, Doanh thu = Doanh số - VAT, Còn nợ = what those
 * orders still owe. Split by pháp nhân / cơ sở (the CRM branch's mapping on
 * "Cấu hình"), CRM branch, Marketing team, person; plus the revenue shared to
 * doctors / TVV / KTV / nurses and the công tua.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { change, financeApi, money, type Line, type MonthRevenue } from "@/lib/finance";
import { formatDateTime } from "@/lib/datetime";
import { monthOf, shiftMonth } from "@/lib/hr-attendance";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";

type Split = "entity" | "site" | "branch" | "team";
type View = Split | "people" | "shares" | "tours";

const VIEWS: { key: View; label: string }[] = [
  { key: "entity", label: "Theo pháp nhân" },
  { key: "site", label: "Theo cơ sở" },
  { key: "branch", label: "Theo chi nhánh CRM" },
  { key: "team", label: "Theo đội Marketing" },
  { key: "people", label: "Theo người" },
  { key: "shares", label: "Chia doanh thu" },
  { key: "tours", label: "Công tua" },
];

function monthLabel(month: string): string {
  const [year, value] = month.split("-");
  return `Tháng ${value}/${year}`;
}

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "bad";
}) {
  return (
    <div className="panel p-4">
      <dt className="text-xs font-medium text-[var(--text-muted)]">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl">{value}</dd>
      {hint ? (
        <p
          className={`mt-1 text-xs ${
            tone === "good"
              ? "text-[var(--good)]"
              : tone === "bad"
                ? "text-[var(--bad)]"
                : "text-[var(--text-muted)]"
          }`}
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}

function versus(
  now: number,
  before: number,
  span: string,
): { hint: string; tone?: "good" | "bad" } {
  const text = change(now, before);
  if (text === "—") return { hint: `Chưa có số ${span}` };
  return { hint: `${text} so với ${span}`, tone: now >= before ? "good" : "bad" };
}

/** "01/10 - 13/10" (same days, month running) or "tháng 10" (a whole month). */
function spanLabel(data: MonthRevenue): string {
  const [, fromMonth, fromDay] = data.previous_from.split("-");
  const [, , toDay] = data.previous_to.split("-");
  const lastDay = new Date(Number(data.previous_from.slice(0, 4)), Number(fromMonth), 0).getDate();
  if (fromDay === "01" && Number(toDay) === lastDay) return `tháng ${Number(fromMonth)}`;
  return `${fromDay}/${fromMonth} - ${toDay}/${fromMonth}`;
}

function Headline({ data }: { data: MonthRevenue }) {
  const { figures, previous } = data;
  const span = spanLabel(data);
  const collected = versus(figures.collected, previous.collected, span);
  const sales = versus(figures.sales, previous.sales, span);
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="finance-tiles">
      <Tile
        label="Thực thu (tiền về, gồm VAT)"
        value={money(figures.collected)}
        hint={collected.hint}
        tone={collected.tone}
      />
      <Tile
        label="Thực thu sau VAT"
        value={money(figures.collected_net)}
        hint={`VAT trong thực thu: ${money(figures.collected_vat)}`}
      />
      <Tile
        label="Doanh số (đơn tạo trong tháng)"
        value={money(figures.sales)}
        hint={sales.hint}
        tone={sales.tone}
      />
      <Tile
        label="Doanh thu (doanh số - VAT)"
        value={money(figures.revenue)}
        hint={`VAT của đơn: ${money(figures.sales_vat)}`}
      />
      <Tile
        label="Còn nợ (đơn trong tháng)"
        value={money(figures.owed)}
        hint={`Trên ${figures.orders.toLocaleString("vi-VN")} đơn tạo trong tháng`}
      />
      <Tile
        label="Thực thu sau VAT & phí"
        value={money(data.collected_after_fees ?? figures.collected_net)}
        hint={`Phí (quẹt, HH, xe, giá cost): ${money(data.fees ?? 0)}`}
      />
      <Tile
        label="% KPI về"
        value={
          data.kpi
            ? `${((figures.collected / data.kpi) * 100).toFixed(1).replace(".", ",")}%`
            : "Chưa đặt KPI"
        }
        hint={data.kpi ? `KPI thực thu: ${money(data.kpi)}` : "Đặt ở Cấu hình › KPI doanh thu"}
      />
      <Tile
        label="Số khoản thu"
        value={figures.payments.toLocaleString("vi-VN")}
        hint={figures.refunds ? `Hoàn tiền: ${money(figures.refunds)}` : "Không có hoàn tiền"}
      />
    </dl>
  );
}

function DailyBars({ days }: { days: MonthRevenue["days"] }) {
  const top = Math.max(1, ...days.map((day) => day.collected));
  return (
    <section className="panel p-4" aria-label="Thực thu theo ngày">
      <h2 className="text-base font-semibold">Thực thu theo ngày</h2>
      <div className="mt-3 flex h-36 items-end gap-[2px]" role="list">
        {days.map((day) => {
          const share = Math.max(0, day.collected) / top;
          const label = `${day.day.slice(8, 10)}/${day.day.slice(5, 7)}: ${money(day.collected)}`;
          return (
            <div
              key={day.day}
              role="listitem"
              aria-label={label}
              title={label}
              className="flex h-full min-w-0 flex-1 items-end"
            >
              <div
                className="w-full rounded-t-sm"
                style={{
                  height: `${Math.max(share * 100, day.collected ? 2 : 0)}%`,
                  background: "var(--brand-blue)",
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-xs text-[var(--text-muted)]">
        <span>{days[0]?.day.slice(8, 10)}</span>
        <span>{days.at(-1)?.day.slice(8, 10)}</span>
      </div>
    </section>
  );
}

function SplitTable({ lines, unit, kpi = false }: { lines: Line[]; unit: string; kpi?: boolean }) {
  if (!lines.length) return <Empty message="Tháng này chưa có số." />;
  const total = lines.reduce(
    (sum, line) => ({
      collected: sum.collected + line.figures.collected,
      net: sum.net + line.figures.collected_net,
      sales: sum.sales + line.figures.sales,
      revenue: sum.revenue + line.figures.revenue,
      owed: sum.owed + line.figures.owed,
      payments: sum.payments + line.figures.payments,
    }),
    { collected: 0, net: 0, sales: 0, revenue: 0, owed: 0, payments: 0 },
  );
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[52rem]">
        <thead>
          <tr>
            <th>{unit}</th>
            <th className="text-right">Thực thu</th>
            <th className="text-right">Sau VAT</th>
            <th className="text-right">Doanh số</th>
            <th className="text-right">Doanh thu</th>
            <th className="text-right">Còn nợ</th>
            <th className="text-right">Khoản thu</th>
            {kpi ? <th className="text-right">% KPI</th> : null}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.key || "none"} data-testid="split-row">
              <td>
                {line.key ? line.label : <Pill tone="warn">{line.label}</Pill>}
              </td>
              <td className="text-right tabular-nums">{money(line.figures.collected)}</td>
              <td className="text-right tabular-nums">{money(line.figures.collected_net)}</td>
              <td className="text-right tabular-nums">{money(line.figures.sales)}</td>
              <td className="text-right tabular-nums">{money(line.figures.revenue)}</td>
              <td className="text-right tabular-nums">{money(line.figures.owed)}</td>
              <td className="text-right tabular-nums">{line.figures.payments}</td>
              {kpi ? (
                <td className="text-right tabular-nums" title={line.kpi ? `KPI ${money(line.kpi)}` : undefined}>
                  {line.kpi ? `${((line.figures.collected / line.kpi) * 100).toFixed(1).replace(".", ",")}%` : "—"}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <td>Tổng</td>
            <td className="text-right tabular-nums">{money(total.collected)}</td>
            <td className="text-right tabular-nums">{money(total.net)}</td>
            <td className="text-right tabular-nums">{money(total.sales)}</td>
            <td className="text-right tabular-nums">{money(total.revenue)}</td>
            <td className="text-right tabular-nums">{money(total.owed)}</td>
            <td className="text-right tabular-nums">{total.payments}</td>
            {kpi ? <td /> : null}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Account({ name, userName }: { name: string; userName: string | null }) {
  return (
    <>
      <span className="font-medium">{name}</span>
      <span className="block text-xs text-[var(--text-muted)]">
        {userName ? `APM: ${userName}` : "Chưa gắn tài khoản APM"}
      </span>
    </>
  );
}

function PeopleTable({ data }: { data: MonthRevenue }) {
  const [role, setRole] = useState(data.roles[0]?.role ?? "telesales");
  const current = data.roles.find((item) => item.role === role);
  return (
    <div className="space-y-3">
      <label className="block max-w-xs text-xs font-medium text-[var(--text-muted)]">
        Vai trò trên khoản thu
        <Select value={role} onChange={(event) => setRole(event.target.value)} className="mt-1 w-full">
          {data.roles.map((item) => (
            <option key={item.role} value={item.role}>
              {item.label}
            </option>
          ))}
        </Select>
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Telesale, Marketing, CSKH, Carepage là người phụ trách trên hồ sơ khách lúc đồng bộ (CRM
        quy định), không hẳn là người bán đơn đó. Người lên đơn lấy theo đơn.
      </p>
      {current && current.people.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[32rem]">
            <thead>
              <tr>
                <th>Người (CRM)</th>
                <th className="text-right">Thực thu</th>
                <th className="text-right">Khoản thu</th>
              </tr>
            </thead>
            <tbody>
              {current.people.map((person) => (
                <tr key={person.crm_id} data-testid="person-row">
                  <td>
                    <Account name={person.name} userName={person.user_name} />
                  </td>
                  <td className="text-right tabular-nums">{money(person.collected)}</td>
                  <td className="text-right tabular-nums">{person.payments}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty message="Không có khoản thu nào ghi người ở vai trò này." />
      )}
    </div>
  );
}

function SharesTable({ data }: { data: MonthRevenue }) {
  if (!data.shares.length) return <Empty message="Tháng này chưa có doanh thu chia." />;
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[36rem]">
        <thead>
          <tr>
            <th>Người được chia</th>
            <th>Phòng (CRM)</th>
            <th className="text-right">Doanh thu được chia</th>
            <th className="text-right">Số dòng</th>
          </tr>
        </thead>
        <tbody>
          {data.shares.map((line) => (
            <tr key={line.crm_id ?? line.name} data-testid="share-row">
              <td>
                <Account name={line.name} userName={line.user_name} />
              </td>
              <td>{line.department ?? "—"}</td>
              <td className="text-right tabular-nums">{money(line.shared)}</td>
              <td className="text-right tabular-nums">{line.lines}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ToursTable({ data }: { data: MonthRevenue }) {
  if (!data.tours.length) return <Empty message="Tháng này chưa có công tua." />;
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[32rem]">
        <thead>
          <tr>
            <th>Người làm</th>
            <th className="text-right">Công chính</th>
            <th className="text-right">Hỗ trợ</th>
            <th className="text-right">Tổng</th>
          </tr>
        </thead>
        <tbody>
          {data.tours.map((line) => (
            <tr key={line.crm_id ?? line.name} data-testid="tour-row">
              <td>
                <Account name={line.name} userName={line.user_name} />
              </td>
              <td className="text-right tabular-nums">{line.primary}</td>
              <td className="text-right tabular-nums">{line.support}</td>
              <td className="text-right tabular-nums">{line.primary + line.support}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RevenueScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const month = params.get("month") ?? monthOf();
  const [view, setView] = useState<View>("entity");
  const access = useQuery({ queryKey: ["finance", "access"], queryFn: financeApi.access });
  const revenue = useQuery({
    queryKey: ["finance", "revenue", month],
    queryFn: () => financeApi.revenue(month),
    enabled: Boolean(access.data?.revenue),
    retry: false,
  });
  const splits = useMemo(
    () =>
      revenue.data
        ? {
            entity: { lines: revenue.data.by_entity, unit: "Pháp nhân" },
            site: { lines: revenue.data.by_site, unit: "Cơ sở", kpi: true },
            branch: { lines: revenue.data.by_branch, unit: "Chi nhánh CRM" },
            team: { lines: revenue.data.by_team, unit: "Đội Marketing", kpi: true },
          }
        : null,
    [revenue.data],
  );
  if (access.isPending) return <Loading />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!access.data.revenue) {
    return <Empty message="Bạn chưa có quyền xem doanh thu. Nhờ chủ sở hữu cấp quyền Tài chính." />;
  }
  const go = (value: string) => router.replace(`/finance/revenue?month=${value}`);
  const data = revenue.data;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Doanh thu"
        subtitle="Lấy từ CRM: thực thu theo ngày khách trả, doanh số theo ngày tạo đơn. Tự đồng bộ lúc 0h, 5h, 12h, 18h."
      />
      <div className="flex flex-wrap items-center gap-2">
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, -1))} aria-label="Tháng trước">
          ‹
        </SecondaryButton>
        <span className="min-w-28 text-center font-semibold">{monthLabel(month)}</span>
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, 1))} aria-label="Tháng sau">
          ›
        </SecondaryButton>
        {data?.synced_at ? (
          <span className="text-xs text-[var(--text-muted)]">
            Đồng bộ CRM lần cuối {formatDateTime(data.synced_at)}
            {data.sync_status === "FAILED" ? " · có lỗi" : ""}
          </span>
        ) : null}
      </div>
      {revenue.isPending ? <Loading label="Đang tính doanh thu…" /> : null}
      {revenue.isError ? <ErrorBox error={revenue.error} onRetry={() => revenue.refetch()} /> : null}
      {data ? (
        <>
          {data.sync_status === "FAILED" && data.sync_message ? (
            <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
              Lần đồng bộ gần nhất có lỗi: {data.sync_message}
            </div>
          ) : null}
          {data.unmapped_branches ? (
            <div
              className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm"
              data-testid="unmapped-note"
            >
              {data.unmapped_branches} chi nhánh CRM chưa gắn pháp nhân / cơ sở: số của chúng nằm ở
              dòng &quot;Chưa gắn&quot;.{" "}
              {access.data.settings ? (
                <Link href="/finance/settings" className="font-medium text-[var(--accent)] hover:underline">
                  Gắn ngay
                </Link>
              ) : null}
            </div>
          ) : null}
          <Headline data={data} />
          <DailyBars days={data.days} />
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Xem doanh thu">
            {VIEWS.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={view === item.key}
                onClick={() => setView(item.key)}
                className={`min-h-11 rounded-lg border px-3 text-sm ${
                  view === item.key
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                    : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          {splits && view in splits ? (
            <SplitTable {...splits[view as Split]} />
          ) : view === "people" ? (
            <PeopleTable data={data} />
          ) : view === "shares" ? (
            <SharesTable data={data} />
          ) : (
            <ToursTable data={data} />
          )}
        </>
      ) : null}
    </div>
  );
}

export default function FinanceRevenuePage() {
  return (
    <Suspense fallback={<Loading />}>
      <RevenueScreen />
    </Suspense>
  );
}
