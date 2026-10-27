"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type AdsFigures,
  type AdsHistory,
  type AdsSeriesPoint,
} from "@/lib/api";
import {
  compactMoney,
  count,
  money,
  percent,
  ratioTone,
  SeriesChart,
} from "@/components/ads";
import { ErrorBox, Loading } from "@/components/states";
import { formatShortDate } from "@/lib/datetime";

/**
 * "Báo cáo chi tiết" at the end of Dashboard Ads (design board "Quản lý ·
 * Dashboard tổng"), two sections folded by default, each opening on its own
 * with a one-line summary on its header: "Báo cáo ngày" first (the month's
 * days: chart, call-outs, Facebook month to date, the daily table), then
 * "Báo cáo tháng" (April to the month: the trend chart and the ①-⑤ table). The team, or one marketer
 * (`GET /api/ads/history`). Figures nobody enters yet (customers, bookings,
 * bills) and the sources not connected (Google / TikTok Ads) are waiting
 * cells, never sample numbers.
 */
export function AdsReports({
  month,
  userId,
  connectionId,
  site,
}: {
  /** `YYYY-MM`; empty = this month. */
  month?: string;
  userId?: string;
  connectionId?: string;
  site?: string;
}) {
  const [open, setOpen] = useState({ day: false, month: false });
  const history = useQuery({
    queryKey: ["ads", "history", month, userId, connectionId, site],
    queryFn: () =>
      api.adsHistory({
        month: month || undefined,
        user_id: userId || undefined,
        connection_id: connectionId || undefined,
        site: site && site !== "all" ? site : undefined,
      }),
    retry: false,
    placeholderData: (previous) => previous,
  });
  const data = history.data;
  const span = data?.months.length
    ? `${monthLabel(data.months[0].month)} – ${monthLabel(data.months[data.months.length - 1].month)}`
    : "";
  return (
    <section aria-label="Báo cáo chi tiết" className="space-y-3">
      <h2 className="brand-eyebrow">Báo cáo chi tiết</h2>
      {history.isPending ? <Loading label="Đang tải báo cáo…" /> : null}
      {history.isError ? (
        <ErrorBox error={history.error} onRetry={() => history.refetch()} />
      ) : null}
      {data ? (
        <DayReport
          data={data}
          open={open.day}
          onToggle={() => setOpen((now) => ({ ...now, day: !now.day }))}
        />
      ) : null}
      {data ? (
        <MonthReport
          data={data}
          span={span}
          open={open.month}
          onToggle={() => setOpen((now) => ({ ...now, month: !now.month }))}
        />
      ) : null}
    </section>
  );
}

const CARD = "rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4";
const WAITING =
  "rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4";
const TONE_TEXT: Record<string, string> = {
  good: "text-[var(--good)]",
  watch: "text-[var(--warn)]",
  bad: "text-[var(--bad)]",
};

function monthLabel(month: string): string {
  return `T${Number(month.slice(5, 7))}`;
}

function short(value: number | null | undefined): string {
  return value ? compactMoney(value) : "–";
}

function Ratio({ value }: { value: number | null | undefined }) {
  const tone = ratioTone(value ?? null);
  return (
    <span className={tone ? TONE_TEXT[tone] : ""}>
      {value == null ? "–" : percent(value)}
    </span>
  );
}

/** Spend ÷ commission revenue, the design's per-day Ads / DT. */
function dayRatio(figures: AdsFigures): number | null {
  return figures.spend && figures.revenue_net
    ? figures.spend / figures.revenue_net
    : null;
}

/** A report folded to its header (title + one-line summary) until opened. */
function Fold({
  title,
  summary,
  open,
  onToggle,
  testid,
  children,
}: {
  title: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  testid: string;
  children: React.ReactNode;
}) {
  const body = `fold-${testid}`;
  return (
    <section className="panel overflow-hidden">
      <h3 className="m-0">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={body}
          onClick={onToggle}
          className="flex min-h-11 w-full items-center justify-between gap-3 p-4 text-left hover:bg-[var(--surface-muted)]"
        >
          <span className="min-w-0">
            <span className="block text-base font-semibold">{title}</span>
            <span className="block text-xs font-normal text-[var(--text-muted)]">
              {summary}
            </span>
          </span>
          <svg
            viewBox="0 0 24 24"
            aria-hidden="true"
            className={`h-5 w-5 shrink-0 text-[var(--text-muted)] transition-transform ${open ? "rotate-180" : ""}`}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
      </h3>
      {open ? (
        <div
          id={body}
          data-testid={testid}
          className="border-t border-[var(--border)] p-4"
        >
          {children}
        </div>
      ) : null}
    </section>
  );
}

function Legend({
  items,
}: {
  items: Array<{ label: string; mark: "bar" | "line" | "dash" }>;
}) {
  return (
    <div className="mb-2 flex flex-wrap gap-4 text-xs text-[var(--text-muted)]">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          {item.mark === "bar" ? (
            <span className="h-3 w-3 rounded-sm bg-[var(--accent)]" />
          ) : item.mark === "line" ? (
            <span className="h-0.5 w-4 rounded bg-[var(--magenta)]" />
          ) : (
            <span className="w-4 border-t-2 border-dashed border-[var(--text)]" />
          )}
          {item.label}
        </span>
      ))}
      <span>cùng một trục tiền</span>
    </div>
  );
}

/** The last day of the window with any number in it. */
function lastDayWithData(data: AdsHistory): string | null {
  const days = data.days.filter(
    (line) => line.figures.spend > 0 || line.figures.revenue != null,
  );
  return days.length ? days[days.length - 1].day : null;
}

type MonthLine = AdsHistory["months"][number];
type MonthRow =
  | { label: string; cell: (line: MonthLine) => React.ReactNode }
  | { label: string; waiting: string };

const MONTH_GROUPS: Array<{ title: string; rows: MonthRow[] }> = [
  {
    title: "① Chi phí",
    rows: [
      { label: "Chi phí Ads", cell: (line) => short(line.figures.spend) },
      {
        label: "Tiền lập hoá đơn",
        cell: (line) => short(line.figures.invoice),
      },
    ],
  },
  {
    title: "② Lead",
    rows: [
      {
        label: "SĐT",
        cell: (line) =>
          line.figures.phones ? count(line.figures.phones) : "–",
      },
      {
        label: "Chi phí / SĐT",
        cell: (line) => short(line.figures.cost_per_phone),
      },
    ],
  },
  {
    title: "③ Khách đến cửa & chất lượng chốt",
    rows: [
      {
        label: "Khách đến cửa · Tỷ lệ chốt · Nóng · Gối · Tối ưu · Tỷ lệ bong",
        waiting: "Chưa có dữ liệu · chưa nhập khách, lịch và kết quả chốt",
      },
    ],
  },
  {
    title: "④ Doanh thu",
    rows: [
      {
        label: "KPI",
        cell: (line) =>
          line.kpi ? (
            compactMoney(line.kpi)
          ) : (
            <span className="text-[var(--text-muted)]">chưa nhập</span>
          ),
      },
      {
        label: "Doanh thu xét KPI",
        cell: (line) => short(line.figures.revenue),
      },
      {
        label: "% hoàn thành KPI",
        cell: (line) => (line.kpi_rate == null ? "–" : percent(line.kpi_rate)),
      },
      { label: "Bill trung bình", waiting: "Chưa có dữ liệu · chưa nhập bill" },
    ],
  },
  {
    title: "⑤ Hiệu quả",
    rows: [
      {
        label: "Ads / DT thực",
        cell: (line) => <Ratio value={line.figures.ads_ratio_real} />,
      },
    ],
  },
];

function MonthReport({
  data,
  span,
  open,
  onToggle,
}: {
  data: AdsHistory;
  span: string;
  open: boolean;
  onToggle: () => void;
}) {
  const filtered = data.revenue_note === "filtered";
  const last = lastDayWithData(data);
  // The month in view is still running while its last day is not over.
  const running = data.days.length > 0 && data.days.length < daysIn(data.end);
  const current = data.months[data.months.length - 1];
  const labelOf = (line: MonthLine, index: number) =>
    `${monthLabel(line.month)}${running && index === data.months.length - 1 ? "*" : ""}`;
  const points: AdsSeriesPoint[] = data.months.map((line) => ({
    start: line.month,
    spend: line.figures.spend,
    messages: 0,
    phones: line.figures.phones,
    cost_per_phone: line.figures.cost_per_phone,
    revenue: line.figures.revenue,
    revenue_net: line.figures.revenue_net,
    ads_ratio: line.figures.ads_ratio,
  }));
  const summary = [
    `Xu hướng ${span}`,
    `${monthLabel(current.month)}: chi phí ${short(current.figures.spend)}`,
    current.figures.revenue != null
      ? `doanh thu ${short(current.figures.revenue)}`
      : null,
    current.kpi_rate != null ? `đạt ${percent(current.kpi_rate)} KPI` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Fold
      title="Báo cáo tháng"
      summary={summary}
      open={open}
      onToggle={onToggle}
      testid="report-month"
    >
      <Legend
        items={[
          { label: "Chi phí Ads (cột)", mark: "bar" },
          { label: "Doanh thu xét KPI (đường)", mark: "line" },
          { label: "KPI tháng (nét đứt)", mark: "dash" },
        ]}
      />
      <SeriesChart
        points={points}
        measure="spend"
        label="Chi phí, doanh thu và KPI theo tháng"
        revenueLine
        target={data.months.map((line) => line.kpi)}
        labelOf={(point) =>
          labelOf(
            data.months.find((line) => line.month === point.start)!,
            data.months.findIndex((line) => line.month === point.start),
          )
        }
        tooltip={(_, index) => {
          const line = data.months[index];
          return (
            <>
              <div>Chi phí Ads: {money(line.figures.spend)}</div>
              <div>Doanh thu xét KPI: {money(line.figures.revenue)}</div>
              <div>
                KPI: {money(line.kpi)} · đạt {percent(line.kpi_rate)}
              </div>
            </>
          );
        }}
      />
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {running && last
          ? `* ${monthLabel(current.month)} tính đến ${formatShortDate(last)}${current.kpi ? "" : ", chưa có KPI"}. `
          : ""}
        {filtered
          ? "Đang lọc một BM: doanh thu và KPI nhập theo người, không tách theo BM."
          : ""}
      </p>
      <div className="mt-3 table-scroll-x">
        <table
          className="table-dense w-full"
          style={{ minWidth: `${240 + data.months.length * 96}px` }}
        >
          <thead>
            <tr>
              <th className="text-left">Chỉ số</th>
              {data.months.map((line, index) => (
                <th key={line.month} className="text-right">
                  {labelOf(line, index)}
                </th>
              ))}
            </tr>
          </thead>
          {MONTH_GROUPS.map((group) => (
            <tbody key={group.title}>
              <tr>
                <th
                  colSpan={data.months.length + 1}
                  className="bg-[var(--surface-muted)] text-left"
                >
                  {group.title}
                </th>
              </tr>
              {group.rows.map((row) => (
                <tr key={row.label}>
                  <td className="text-sm">{row.label}</td>
                  {"waiting" in row ? (
                    <td
                      colSpan={data.months.length}
                      className="text-center text-xs text-[var(--text-muted)]"
                    >
                      <span className="inline-block rounded-md border border-dashed border-[var(--border)] px-2 py-1">
                        {row.waiting}
                      </span>
                    </td>
                  ) : (
                    data.months.map((line) => (
                      <td key={line.month} className="text-right tabular-nums">
                        {row.cell(line)}
                      </td>
                    ))
                  )}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </Fold>
  );
}

function daysIn(end: string): number {
  return Number(end.slice(8, 10));
}

function DayReport({
  data,
  open,
  onToggle,
}: {
  data: AdsHistory;
  open: boolean;
  onToggle: () => void;
}) {
  const filtered = data.revenue_note === "filtered";
  const days = data.days;
  const total = days.reduce(
    (sum, line) => ({
      spend: sum.spend + line.figures.spend,
      phones: sum.phones + line.figures.phones,
      revenue:
        line.figures.revenue == null
          ? sum.revenue
          : (sum.revenue ?? 0) + line.figures.revenue,
      revenue_net:
        line.figures.revenue_net == null
          ? sum.revenue_net
          : (sum.revenue_net ?? 0) + line.figures.revenue_net,
    }),
    {
      spend: 0,
      phones: 0,
      revenue: null as number | null,
      revenue_net: null as number | null,
    },
  );
  const points: AdsSeriesPoint[] = days.map((line) => ({
    start: line.day,
    spend: line.figures.spend,
    messages: 0,
    phones: line.figures.phones,
    cost_per_phone: line.figures.cost_per_phone,
    revenue: line.figures.revenue,
    revenue_net: line.figures.revenue_net,
    ads_ratio: dayRatio(line.figures),
  }));
  const last = lastDayWithData(data);
  const lastLine = days.find((line) => line.day === last);
  const summary = total.spend
    ? [
        `Lũy kế ${short(total.spend)}`,
        `${count(total.phones)} SĐT`,
        total.revenue != null ? `doanh thu ${short(total.revenue)}` : null,
        last ? `đến ${formatShortDate(last)}` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "Chưa có chi tiêu trong tháng";
  return (
    <Fold
      title={`Báo cáo ngày · tháng ${Number(data.month.slice(5, 7))}`}
      summary={summary}
      open={open}
      onToggle={onToggle}
      testid="report-day"
    >
      <Legend
        items={[
          { label: "Chi tiêu (cột)", mark: "bar" },
          { label: "Doanh thu xét KPI (đường)", mark: "line" },
        ]}
      />
      <SeriesChart
        points={points}
        measure="spend"
        label="Chi tiêu và doanh thu từng ngày"
        revenueLine
        labelOf={(point) => String(Number(point.start.slice(8, 10)))}
        tooltip={(point) => (
          <>
            <div>Chi tiêu: {money(point.spend)}</div>
            <div>Doanh thu xét KPI: {money(point.revenue)}</div>
            <div>
              Ads / DT: {percent(point.ads_ratio)} · SĐT: {count(point.phones)}
            </div>
          </>
        )}
      />
      {lastLine &&
      lastLine.figures.spend > 0 &&
      !filtered &&
      lastLine.figures.revenue == null ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Ngày {formatShortDate(lastLine.day)} đã có chi tiêu, chưa có doanh
          thu.
        </p>
      ) : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SpendTrend data={data} />
        <OverLimit data={data} />
        <div className={CARD} data-testid="facebook-month">
          <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
            Facebook Ads · lũy kế tháng
          </div>
          <dl className="mt-1 grid grid-cols-3 gap-2 text-sm">
            <div>
              <dt className="text-xs text-[var(--text-muted)]">Ngân sách</dt>
              <dd className="font-semibold tabular-nums">
                {short(total.spend)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--text-muted)]">SĐT</dt>
              <dd className="font-semibold tabular-nums">
                {count(total.phones)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--text-muted)]">Giá số</dt>
              <dd className="font-semibold tabular-nums">
                {total.phones ? short(total.spend / total.phones) : "–"}
              </dd>
            </div>
          </dl>
        </div>
        <div className={WAITING} data-testid="other-sources">
          <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
            Google Ads · TikTok Ads
          </div>
          <div className="mt-1 text-lg font-semibold text-[var(--text-muted)]">
            Chưa kết nối
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            Chưa lấy số từ Google Ads, TikTok Ads.
          </div>
        </div>
      </div>
      <div className="mt-3 max-h-[32rem] overflow-auto rounded-xl border border-[var(--border)]">
        <table className="table-dense w-full min-w-[760px]">
          <thead className="sticky top-0 z-10 bg-[var(--surface)]">
            <tr>
              <th
                rowSpan={2}
                className="sticky left-0 z-20 bg-[var(--surface)] text-left"
              >
                Ngày
              </th>
              <th colSpan={3} className="text-center">
                Facebook Ads
              </th>
              <th colSpan={3} className="text-center">
                Kết quả
              </th>
            </tr>
            <tr>
              <th className="text-right">Ngân sách</th>
              <th className="text-right">SĐT</th>
              <th className="text-right">Giá số</th>
              <th className="text-center">Khách đến</th>
              <th className="text-right">Doanh thu</th>
              <th className="text-right">Ads / DT</th>
            </tr>
            <tr className="font-semibold" data-testid="day-total">
              <td className="sticky left-0 z-20 bg-[var(--surface-muted)]">
                Lũy kế tháng
              </td>
              <td className="bg-[var(--surface-muted)] text-right tabular-nums">
                {money(total.spend)}
              </td>
              <td className="bg-[var(--surface-muted)] text-right tabular-nums">
                {count(total.phones)}
              </td>
              <td className="bg-[var(--surface-muted)] text-right tabular-nums">
                {total.phones ? money(total.spend / total.phones) : "–"}
              </td>
              <td className="bg-[var(--surface-muted)] text-center text-[var(--text-muted)]">
                –
              </td>
              <td className="bg-[var(--surface-muted)] text-right tabular-nums">
                {total.revenue == null ? "–" : money(total.revenue)}
              </td>
              <td className="bg-[var(--surface-muted)] text-right">
                <Ratio
                  value={
                    total.spend && total.revenue_net
                      ? total.spend / total.revenue_net
                      : null
                  }
                />
              </td>
            </tr>
          </thead>
          <tbody>
            {days.map((line, index) => {
              const figures = line.figures;
              const spent = figures.spend > 0;
              return (
                <tr key={line.day} data-testid={`day-${line.day}`}>
                  <td className="sticky left-0 bg-[var(--surface)]">
                    {formatShortDate(line.day)}
                  </td>
                  <td className="text-right tabular-nums">
                    {spent ? money(figures.spend) : "–"}
                  </td>
                  <td className="text-right tabular-nums">
                    {spent ? count(figures.phones) : "–"}
                  </td>
                  <td className="text-right tabular-nums">
                    {figures.phones ? money(figures.cost_per_phone) : "–"}
                  </td>
                  {index === 0 ? (
                    <td
                      rowSpan={days.length}
                      className="align-middle text-center text-xs text-[var(--text-muted)]"
                    >
                      <span className="inline-block rounded-md border border-dashed border-[var(--border)] px-2 py-1">
                        Chưa có dữ liệu
                      </span>
                    </td>
                  ) : null}
                  <td className="text-right tabular-nums">
                    {figures.revenue == null ? "–" : money(figures.revenue)}
                  </td>
                  <td className="text-right">
                    <Ratio value={dayRatio(figures)} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        Tiêu đề và dòng lũy kế cố định khi cuộn · “–” = ngày không có số · Ads /
        DT = chi tiêu ÷ doanh thu hoa hồng · khách đến cửa chưa nhập · Google
        Ads, TikTok Ads chưa kết nối.
        {filtered ? " Đang lọc một BM: không có doanh thu." : ""}
      </p>
    </Fold>
  );
}

/** "Chi tiêu đang giảm": the last five days with spend against the first week. */
function SpendTrend({ data }: { data: AdsHistory }) {
  const spent = data.days.filter((line) => line.figures.spend > 0);
  if (!spent.length) {
    return (
      <div className={WAITING}>
        <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
          Xu hướng chi tiêu
        </div>
        <div className="mt-1 text-sm text-[var(--text-muted)]">
          Chưa có chi tiêu trong tháng.
        </div>
      </div>
    );
  }
  const lastFive = spent.slice(-5);
  const average =
    lastFive.reduce((sum, line) => sum + line.figures.spend, 0) /
    lastFive.length;
  const firstWeek = data.days
    .slice(0, 7)
    .filter((line) => line.figures.spend > 0);
  // Only once the two windows no longer overlap.
  const compare = spent.length >= 10 && firstWeek.length > 0;
  const weekAverage = compare
    ? firstWeek.reduce((sum, line) => sum + line.figures.spend, 0) /
      firstWeek.length
    : null;
  const trend =
    weekAverage == null
      ? "Chi tiêu gần đây"
      : average < weekAverage * 0.9
        ? "Chi tiêu đang giảm"
        : average > weekAverage * 1.1
          ? "Chi tiêu đang tăng"
          : "Chi tiêu ổn định";
  return (
    <div className={CARD} data-testid="spend-trend">
      <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
        {trend}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">
        {compactMoney(average)}/ngày
      </div>
      <div className="text-xs text-[var(--text-muted)]">
        TB {lastFive.length} ngày {formatShortDate(lastFive[0].day)}–
        {formatShortDate(lastFive[lastFive.length - 1].day)}
        {weekAverage != null
          ? ` · tuần đầu tháng ${compactMoney(weekAverage)}/ngày`
          : ""}
      </div>
    </div>
  );
}

/** Days whose Ads / DT went over 100%, and the one nearest under it. */
function OverLimit({ data }: { data: AdsHistory }) {
  const rated = data.days
    .map((line) => ({ day: line.day, ratio: dayRatio(line.figures) }))
    .filter(
      (line): line is { day: string; ratio: number } => line.ratio != null,
    );
  if (data.revenue_note === "filtered" || !rated.length) {
    return (
      <div className={WAITING}>
        <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
          Ngày Ads / DT vượt 100%
        </div>
        <div className="mt-1 text-sm text-[var(--text-muted)]">
          {data.revenue_note === "filtered"
            ? "Không tách doanh thu theo BM."
            : "Chưa có doanh thu trong tháng."}
        </div>
      </div>
    );
  }
  const over = rated.filter((line) => line.ratio > 1);
  const near = rated
    .filter((line) => line.ratio > 0.9 && line.ratio <= 1)
    .sort((a, b) => b.ratio - a.ratio)[0];
  return (
    <div className={CARD} data-testid="over-limit">
      <div
        className={`text-xs font-semibold uppercase ${over.length ? "text-[var(--bad)]" : "text-[var(--good)]"}`}
      >
        Ngày Ads / DT vượt 100%
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">
        {over.length} ngày
      </div>
      <div className="text-xs text-[var(--text-muted)]">
        {over.map((line) => formatShortDate(line.day)).join(" · ")}
        {near
          ? `${over.length ? " · " : ""}ngày ${formatShortDate(near.day)} sát ngưỡng (${percent(near.ratio)})`
          : ""}
      </div>
    </div>
  );
}
