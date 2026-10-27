"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AdsFigures, type AdsTeamReport } from "@/lib/api";
import {
  compactMoney,
  count,
  money,
  percent,
  ratioTone,
} from "@/components/ads";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { formatDate, formatShortDate } from "@/lib/datetime";

/**
 * "Phòng Ads › Dashboard tổng" (design board "Quản lý · Dashboard tổng"): one
 * month of the department against the month before. Khối 1 = the site's
 * revenue (Doanh thu xét KPI, its split HCM / HN chuyển HCM, the commission
 * revenue), Ads / Doanh thu, the KPI against how far the month is, revenue by
 * source, spend and charges; Khối 2 = Facebook Ads (ranking, who needs
 * attention; Google / TikTok Ads come later); Khối 3 = the table per
 * marketer; then "Báo cáo tháng" and "Báo cáo ngày". Customer, booking and
 * bill figures are not entered yet: their cards say so. A Trưởng phòng ADS,
 * the OWNER / an ADMIN.
 */
export default function AdsTeamPage() {
  const [month, setMonth] = useState("");
  const [site, setSite] = useState("all");
  const router = useRouter();
  const report = useQuery({
    queryKey: ["ads", "team", month, site],
    queryFn: () => api.adsTeam({ month: month || undefined, site }),
    retry: false,
    placeholderData: (previous) => previous,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title="Dashboard tổng"
        subtitle={
          report.data
            ? `Tháng ${report.data.month.slice(5, 7)}/${report.data.month.slice(0, 4)} · ${formatDate(report.data.start)} – ${formatDate(report.data.end)} · so với tháng trước`
            : "Số liệu cả Phòng Ads theo tháng."
        }
      />
      <section aria-label="Bộ lọc" className="flex flex-wrap items-end gap-3">
        <label className={LABEL}>
          <span>Tháng</span>
          <input
            type="month"
            aria-label="Tháng"
            value={month || report.data?.month.slice(0, 7) || ""}
            onChange={(event) => setMonth(event.target.value)}
            className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
          />
        </label>
        <label className={LABEL}>
          <span>Cơ sở</span>
          <Select
            aria-label="Cơ sở"
            value={site}
            onChange={(event) => setSite(event.target.value)}
          >
            <option value="all">Tất cả</option>
            <option value="hcm">Cơ sở HCM</option>
            <option value="hn">HN chuyển HCM</option>
          </Select>
        </label>
        {report.data?.marketers.length ? (
          <label className={LABEL}>
            <span>Xem chi tiết theo</span>
            <Select
              aria-label="Xem chi tiết theo"
              value=""
              onChange={(event) => {
                if (event.target.value)
                  router.push(`/ads?user_id=${event.target.value}`);
              }}
            >
              <option value="">Cả team</option>
              {report.data.marketers.map((line) => (
                <option key={line.user_id} value={line.user_id}>
                  {line.name}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
      </section>
      {report.isPending ? <Loading label="Đang tải Dashboard tổng…" /> : null}
      {report.isError ? (
        <ErrorBox error={report.error} onRetry={() => report.refetch()} />
      ) : null}
      {report.data ? <Report data={report.data} /> : null}
    </div>
  );
}

const LABEL =
  "flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]";
const CARD = "rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4";

function delta(
  now: number | null,
  before: number | null,
  lowerIsBetter = false,
  neutral = false,
) {
  if (now === null || before === null || before === 0) return null;
  const change = now / before - 1;
  const up = change >= 0;
  const good = lowerIsBetter ? !up : up;
  return {
    text: `${up ? "▲" : "▼"} ${Math.abs(change * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}% so tháng trước`,
    tone:
      neutral || change === 0
        ? "text-[var(--text-muted)]"
        : good
          ? "text-[var(--good)]"
          : "text-[var(--bad)]",
  };
}

/** A ratio's change in percentage points ("▼ 2,1 điểm"). */
function pointsDelta(now: number | null, before: number | null) {
  if (now === null || before === null) return null;
  const change = (now - before) * 100;
  return {
    text: `${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} điểm so tháng trước`,
    tone:
      change === 0
        ? "text-[var(--text-muted)]"
        : change < 0
          ? "text-[var(--good)]"
          : "text-[var(--bad)]",
  };
}

function Tile({
  label,
  value,
  sub,
  change,
  testid,
  wide = false,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  change?: ReturnType<typeof delta>;
  testid?: string;
  wide?: boolean;
}) {
  return (
    <div
      data-testid={testid}
      className={`${CARD} ${wide ? "sm:col-span-2" : ""}`}
    >
      <div className="text-sm text-[var(--text-muted)]">{label}</div>
      <div
        className={`mt-1 font-semibold tabular-nums ${wide ? "text-3xl" : "text-2xl"}`}
      >
        {value}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2 text-xs">
        {change ? (
          <span className={`font-medium ${change.tone}`}>{change.text}</span>
        ) : null}
        {sub ? <span className="text-[var(--text-muted)]">{sub}</span> : null}
      </div>
    </div>
  );
}

function Missing({ label, note }: { label: string; note: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="text-sm text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-lg font-semibold text-[var(--text-muted)]">
        Chưa có dữ liệu
      </div>
      <div className="mt-1 text-xs text-[var(--text-muted)]">{note}</div>
    </div>
  );
}

const TONE_TEXT: Record<string, string> = {
  good: "text-[var(--good)]",
  watch: "text-[var(--warn)]",
  bad: "text-[var(--bad)]",
};

function Ratio({ value }: { value: number | null }) {
  const tone = ratioTone(value);
  return (
    <span className={`font-semibold ${tone ? TONE_TEXT[tone] : ""}`}>
      {percent(value)}
    </span>
  );
}

function Report({ data }: { data: AdsTeamReport }) {
  const now = data.current;
  const before = data.before;
  const sourceTotal = data.sources.reduce(
    (sum, source) => sum + (source.revenue ?? 0),
    0,
  );
  return (
    <>
      <section aria-label="Chỉ số tổng" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 1 · Chỉ số tổng</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Tile
            testid="team-revenue"
            wide
            label="Doanh thu xét KPI"
            value={now.revenue === null ? "Chưa nhập" : money(now.revenue)}
            change={delta(now.revenue, before.revenue)}
            sub={`Cơ sở HCM ${money(data.revenue_hcm)} · HN chuyển HCM ${money(data.revenue_hn_transfer)} · DT hoa hồng ${money(now.revenue_net)}`}
          />
          <Tile
            testid="team-ratio"
            label="Ads / Doanh thu"
            value={percent(now.ads_ratio)}
            change={pointsDelta(now.ads_ratio, before.ads_ratio)}
            sub="Lập hoá đơn ÷ DT hoa hồng · ngưỡng ≤ 40%"
          />
          <KpiProgress data={data} />
          <div data-testid="team-sources" className={`${CARD} sm:col-span-2`}>
            <div className="text-sm text-[var(--text-muted)]">
              Doanh số theo nguồn
            </div>
            {sourceTotal ? (
              <ul className="mt-2 space-y-1.5 text-sm">
                {data.sources
                  .filter((source) => source.revenue)
                  .map((source) => (
                    <li
                      key={source.key}
                      className="grid grid-cols-[minmax(0,1fr)_7rem_3.5rem] items-center gap-2"
                    >
                      <span className="truncate">{source.label}</span>
                      <span className="text-right tabular-nums">
                        {compactMoney(source.revenue ?? 0)}
                      </span>
                      <span className="text-right text-xs tabular-nums text-[var(--text-muted)]">
                        {percent((source.revenue ?? 0) / sourceTotal)}
                      </span>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-[var(--text-muted)]">
                Chưa nhập doanh thu tháng này.
              </p>
            )}
            {data.marketers_hn ? (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Ngoài ra Marketing có {money(data.marketers_hn)} doanh số HN
                (khách SG làm tại HN): tính KPI của họ, không tính doanh thu cơ
                sở HCM.
              </p>
            ) : null}
          </div>
          <Tile
            label="Chi phí Ads"
            value={money(now.spend)}
            change={delta(now.spend, before.spend, false, true)}
          />
          <Tile
            label="Số tiền lập hoá đơn"
            value={money(now.invoice)}
            sub={
              now.invoice
                ? `Chênh với chi tiêu ${money(now.invoice - now.spend)}`
                : "Chưa có hoá đơn Meta"
            }
          />
          <Tile
            label="Ads / DT thực"
            value={percent(now.ads_ratio_real)}
            change={pointsDelta(now.ads_ratio_real, before.ads_ratio_real)}
            sub="Lập hoá đơn ÷ doanh thu xét KPI"
          />
          <Tile
            label="SĐT · Chi phí / SĐT"
            value={`${count(now.phones)} · ${money(now.cost_per_phone)}`}
            change={delta(now.phones, before.phones)}
          />
          <Missing
            label="Bill TB · Tỷ lệ chốt / bong"
            note="Chưa nhập khách, lịch và bill."
          />
          <Missing
            label="Cơ cấu khách · Khách đến cửa"
            note="Chưa nhập khách mới / cũ, khách đến cửa."
          />
        </div>
      </section>

      <section aria-label="Facebook Ads" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 2 · Facebook Ads</h2>
        <div className={`${CARD} grid grid-cols-2 gap-3 sm:grid-cols-5`}>
          <Fact label="Marketing" value={count(data.marketers.length)} />
          <Fact label="Chi tiêu" value={money(data.facebook.spend)} />
          <Fact label="DT hoa hồng" value={money(data.facebook.revenue_net)} />
          <Fact
            label="Ads / DT"
            value={<Ratio value={data.facebook.ads_ratio} />}
          />
          <Fact label="SĐT" value={count(data.facebook.phones)} />
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Ranking data={data} />
          <div className={CARD}>
            <div className="text-xs font-semibold uppercase text-[var(--bad)]">
              Cần xử lý ngay
            </div>
            {data.attention.length === 0 ? (
              <p className="mt-1 text-sm text-[var(--text-muted)]">
                Không có ai vượt ngưỡng.
              </p>
            ) : (
              <ul className="mt-1 space-y-1.5 text-sm">
                {data.attention.map((alert) => (
                  <li
                    key={alert.user_id}
                    data-testid={`alert-${alert.user_id}`}
                  >
                    <span className="font-medium">{alert.name}</span>{" "}
                    <span className="text-[var(--text-muted)]">
                      {alert.kind === "no_revenue"
                        ? `chi ${compactMoney(alert.spend)}, ${count(alert.phones)} SĐT, chưa có doanh thu`
                        : `Ads/DT ${percent(alert.ads_ratio)} · chi ${compactMoney(alert.spend)}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <section aria-label="Bảng chi tiết theo Marketing" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 3 · Bảng chi tiết theo Marketing</h2>
        <MarketerTable data={data} />
      </section>

      <details className="panel group">
        <summary className="flex min-h-11 cursor-pointer items-center px-4 text-sm font-semibold">
          Báo cáo chi tiết · tháng và ngày
        </summary>
        <div className="space-y-5 border-t border-[var(--border)] p-4">
          <MonthTable data={data} />
          <DayTable data={data} />
        </div>
      </details>
    </>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-[var(--text-muted)]">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function KpiProgress({ data }: { data: AdsTeamReport }) {
  const rate = data.team_kpi_rate;
  const behind = rate !== null && rate < data.month_progress;
  return (
    <div data-testid="team-kpi" className={CARD}>
      <div className="text-sm text-[var(--text-muted)]">Tiến độ KPI</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">
        {data.team_kpi ? percent(rate) : "Chưa nhập KPI"}
      </div>
      {data.team_kpi ? (
        <>
          <div
            className="relative mt-2 h-2.5 rounded-full bg-[var(--surface-muted)]"
            role="img"
            aria-label={`Đạt ${percent(rate)} KPI, tháng đã qua ${percent(data.month_progress)}`}
          >
            <div
              className={`h-full rounded-full ${behind ? "bg-[var(--warn)]" : "bg-[var(--good)]"}`}
              style={{ width: `${Math.min(1, rate ?? 0) * 100}%` }}
            />
            <div
              className="absolute -top-1 h-4.5 w-0.5 bg-[var(--text)]"
              style={{ left: `${Math.min(1, data.month_progress) * 100}%` }}
            />
          </div>
          <div className="mt-1 text-xs text-[var(--text-muted)]">
            KPI {compactMoney(data.team_kpi)} · tháng đã qua{" "}
            {percent(data.month_progress)}
            {behind ? " · đang chậm tiến độ" : ""}
          </div>
        </>
      ) : (
        <div className="mt-1 text-xs text-[var(--text-muted)]">
          Nhập ở Phòng Ads › KPI doanh thu.
        </div>
      )}
    </div>
  );
}

/** Commission revenue and spend per marketer on one money axis. */
function Ranking({ data }: { data: AdsTeamReport }) {
  const maxBar = Math.max(
    1,
    ...data.marketers.map((line) =>
      Math.max(line.figures.spend, line.figures.revenue_net ?? 0),
    ),
  );
  // Only people with numbers this month: an idle seat has nothing to rank.
  const ranked = data.marketers
    .filter((line) => line.figures.spend > 0 || line.figures.revenue_net)
    .sort(
      (a, b) => (b.figures.revenue_net ?? 0) - (a.figures.revenue_net ?? 0),
    );
  return (
    <div className={CARD}>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-[var(--text-muted)]">
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[var(--accent)]" /> DT hoa hồng
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[var(--text-muted)]" /> Chi
          tiêu
        </span>
        <span>Ads / DT: ≤ 40% · 40–60% · &gt; 60%</span>
      </div>
      {ranked.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">
          Chưa có Marketing nào có số trong tháng.
        </p>
      ) : (
        <ul className="space-y-3">
          {ranked.map((line) => (
            <li
              key={line.user_id}
              data-testid={`rank-${line.user_id}`}
              className="grid grid-cols-[8rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm"
            >
              <span className="truncate font-medium" title={line.name}>
                {line.name}
              </span>
              <span className="space-y-1">
                <span className="flex items-center gap-2">
                  <span
                    className="h-2.5 rounded-r bg-[var(--accent)]"
                    style={{
                      width: `${((line.figures.revenue_net ?? 0) / maxBar) * 100}%`,
                    }}
                  />
                  <span className="text-xs tabular-nums">
                    {line.figures.revenue_net === null
                      ? "chưa có"
                      : compactMoney(line.figures.revenue_net)}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <span
                    className="h-2.5 rounded-r bg-[var(--text-muted)]"
                    style={{ width: `${(line.figures.spend / maxBar) * 100}%` }}
                  />
                  <span className="text-xs tabular-nums text-[var(--text-muted)]">
                    {compactMoney(line.figures.spend)}
                  </span>
                </span>
              </span>
              <span className="text-right">
                <Ratio value={line.ads_ratio} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MarketerTable({ data }: { data: AdsTeamReport }) {
  const fb = data.facebook;
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense w-full min-w-[1040px]">
        <thead>
          <tr>
            <th className="text-left">Marketing</th>
            <th className="text-right">Chi tiêu</th>
            <th className="text-right">Lập hoá đơn</th>
            <th className="text-right">SĐT</th>
            <th className="text-right">Chi phí / SĐT</th>
            <th className="text-right">Doanh số xét KPI</th>
            <th className="text-right">DT hoa hồng</th>
            <th className="text-right">KPI · đạt</th>
            <th className="text-right">Ads / DT</th>
          </tr>
        </thead>
        <tbody>
          {data.marketers.map((line) => (
            <tr key={line.user_id} data-testid={`marketer-${line.user_id}`}>
              <td>
                <Link
                  href={`/ads?user_id=${line.user_id}`}
                  className="font-medium text-[var(--accent)] hover:underline"
                >
                  {line.name}
                </Link>
                {line.member_code ? (
                  <div className="text-xs text-[var(--text-muted)]">
                    {line.member_code}
                  </div>
                ) : null}
              </td>
              <td className="text-right tabular-nums">
                {money(line.figures.spend)}
              </td>
              <td className="text-right tabular-nums">
                {money(line.figures.invoice)}
              </td>
              <td className="text-right tabular-nums">
                {count(line.figures.phones)}
              </td>
              <td className="text-right tabular-nums">
                {money(line.figures.cost_per_phone)}
              </td>
              <td className="text-right tabular-nums">
                {money(line.figures.revenue)}
              </td>
              <td className="text-right tabular-nums">
                {money(line.figures.revenue_net)}
              </td>
              <td className="text-right tabular-nums">
                {line.kpi
                  ? `${compactMoney(line.kpi)} · ${percent(line.kpi_rate)}`
                  : "—"}
              </td>
              <td className="text-right">
                <Ratio value={line.ads_ratio} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <td>TỔNG FACEBOOK ADS</td>
            <td className="text-right tabular-nums">{money(fb.spend)}</td>
            <td className="text-right tabular-nums">{money(fb.invoice)}</td>
            <td className="text-right tabular-nums">{count(fb.phones)}</td>
            <td className="text-right tabular-nums">
              {money(fb.cost_per_phone)}
            </td>
            <td className="text-right tabular-nums">{money(fb.revenue)}</td>
            <td className="text-right tabular-nums">{money(fb.revenue_net)}</td>
            <td />
            <td className="text-right">
              <Ratio value={fb.ads_ratio} />
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function FigureCells({ figures }: { figures: AdsFigures }) {
  return (
    <>
      <td className="text-right tabular-nums">{money(figures.spend)}</td>
      <td className="text-right tabular-nums">{money(figures.invoice)}</td>
      <td className="text-right tabular-nums">{money(figures.revenue)}</td>
      <td className="text-right tabular-nums">{money(figures.revenue_net)}</td>
      <td className="text-right">
        <Ratio value={figures.ads_ratio} />
      </td>
      <td className="text-right tabular-nums">{count(figures.phones)}</td>
    </>
  );
}

const FIGURE_HEADERS = [
  "① Chi tiêu",
  "② Lập hoá đơn",
  "③ Doanh thu xét KPI",
  "④ DT hoa hồng",
  "⑤ Ads / DT",
  "SĐT",
];

function MonthTable({ data }: { data: AdsTeamReport }) {
  const maxRevenue = Math.max(
    1,
    ...data.months.map((line) => line.figures.revenue ?? 0),
  );
  return (
    <div className="space-y-2">
      <h3 className="text-base font-semibold">Báo cáo tháng</h3>
      <div className="table-scroll-x">
        <table className="table-dense w-full min-w-[880px]">
          <thead>
            <tr>
              <th className="text-left">Tháng</th>
              {FIGURE_HEADERS.map((header) => (
                <th key={header} className="text-right">
                  {header}
                </th>
              ))}
              <th className="text-left">KPI · đạt</th>
            </tr>
          </thead>
          <tbody>
            {data.months.map((line) => (
              <tr
                key={line.month}
                data-testid={`month-${line.month.slice(0, 7)}`}
              >
                <td>
                  T{Number(line.month.slice(5, 7))}/{line.month.slice(0, 4)}
                  <div className="mt-1 h-1.5 w-24 rounded-full bg-[var(--surface-muted)]">
                    <div
                      className="h-full rounded-full bg-[var(--accent)]"
                      style={{
                        width: `${((line.figures.revenue ?? 0) / maxRevenue) * 100}%`,
                      }}
                    />
                  </div>
                </td>
                <FigureCells figures={line.figures} />
                <td className="tabular-nums">
                  {line.kpi
                    ? `${compactMoney(line.kpi)} · ${percent(line.kpi_rate)}`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DayTable({ data }: { data: AdsTeamReport }) {
  return (
    <div className="space-y-2">
      <h3 className="text-base font-semibold">Báo cáo ngày</h3>
      <div className="table-scroll-x">
        <table className="table-dense w-full min-w-[820px]">
          <thead>
            <tr>
              <th className="text-left">Ngày</th>
              {FIGURE_HEADERS.map((header) => (
                <th key={header} className="text-right">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.days.map((line) => (
              <tr key={line.day}>
                <td>{formatShortDate(line.day)}</td>
                <FigureCells figures={line.figures} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
