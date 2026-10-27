"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AdsTeamReport } from "@/lib/api";
import {
  compactMoney,
  count,
  money,
  percent,
  ratioTone,
} from "@/components/ads";
import { AdsReports } from "@/components/ads-reports";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { formatDate } from "@/lib/datetime";

/**
 * Dashboard Ads, "Tổng team" (design board "Quản lý · Dashboard tổng"): one
 * month of the department against the month before. Khối 1 = the site's
 * revenue (Doanh thu xét KPI, its split HCM / HN chuyển HCM, the commission
 * revenue), Ads / Doanh thu, the KPI against how far the month is, revenue by
 * source, spend and charges; Khối 2 = Facebook Ads (ranking, who needs
 * attention; Google / TikTok Ads come later); Khối 3 = the table per
 * marketer; then "Báo cáo chi tiết" (tháng, ngày). Customer, booking and bill
 * figures are not entered yet: their cards say so. "BM" narrows every block
 * and table to one BM's accounts; revenue is entered per person, not per BM,
 * so its cards then wait ("Không tách theo BM"). "Xem chi tiết theo" opens one
 * marketer's dashboard. A Trưởng phòng ADS, the OWNER / an ADMIN.
 */
export function TeamDashboard({
  onPick,
}: {
  onPick: (userId: string) => void;
}) {
  const [month, setMonth] = useState("");
  const [site, setSite] = useState("all");
  const [connection, setConnection] = useState("");
  const report = useQuery({
    queryKey: ["ads", "team", month, site, connection],
    queryFn: () =>
      api.adsTeam({
        month: month || undefined,
        site,
        connection_id: connection || undefined,
      }),
    retry: false,
    placeholderData: (previous) => previous,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title="Dashboard Ads"
        subtitle={
          report.data
            ? `Tổng team · tháng ${report.data.month.slice(5, 7)}/${report.data.month.slice(0, 4)} · ${formatDate(report.data.start)} – ${formatDate(report.data.end)} · so với tháng trước`
            : "Số liệu cả Phòng Ads theo tháng."
        }
      />
      <section aria-label="Bộ lọc" className="flex flex-wrap items-end gap-3">
        {report.data ? (
          <ViewPicker
            marketers={report.data.marketers}
            value=""
            onPick={onPick}
          />
        ) : null}
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
        {report.data?.connections?.length ? (
          <label className={LABEL}>
            <span>BM</span>
            <Select
              aria-label="BM"
              value={connection}
              onChange={(event) => setConnection(event.target.value)}
            >
              <option value="">Tất cả BM</option>
              {report.data.connections.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                  {row.site ? ` · ${row.site}` : ""}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
      </section>
      {report.isPending ? <Loading label="Đang tải Dashboard Ads…" /> : null}
      {report.isError ? (
        <ErrorBox error={report.error} onRetry={() => report.refetch()} />
      ) : null}
      {report.data ? <Report data={report.data} onPick={onPick} /> : null}
      {report.data ? (
        <AdsReports
          month={report.data.month.slice(0, 7)}
          connectionId={connection || undefined}
          site={site}
        />
      ) : null}
    </div>
  );
}

type Marketer = AdsTeamReport["marketers"][number];

/** Marketers ranked by the month's revenue (then spend), as the server sorts them. */
export function ranked(marketers: Marketer[]): Marketer[] {
  return [...marketers].sort(
    (a, b) =>
      (b.figures.revenue ?? 0) - (a.figures.revenue ?? 0) ||
      b.figures.spend - a.figures.spend,
  );
}

/** "Xem chi tiết theo": the team (default) or one marketer's dashboard. */
export function ViewPicker({
  marketers,
  value,
  onPick,
}: {
  marketers: Marketer[];
  value: string;
  onPick: (userId: string) => void;
}) {
  return (
    <label className={LABEL}>
      <span>Xem chi tiết theo</span>
      <Select
        aria-label="Xem chi tiết theo"
        value={value}
        onChange={(event) => onPick(event.target.value)}
      >
        <option value="">Tổng team (mặc định)</option>
        {ranked(marketers).map((line, index) => (
          <option key={line.user_id} value={line.user_id}>
            {index + 1}. {line.name}
            {line.ads_ratio != null
              ? ` · Ads/DT ${percent(line.ads_ratio)}`
              : ""}
          </option>
        ))}
      </Select>
    </label>
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

/** A waiting card: figures not entered yet, or not split by the BM picked. */
function Missing({
  label,
  note,
  value = "Chưa có dữ liệu",
  testid,
  wide = false,
}: {
  label: string;
  note: string;
  value?: string;
  testid?: string;
  wide?: boolean;
}) {
  return (
    <div
      data-testid={testid}
      className={`rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4 ${wide ? "sm:col-span-2" : ""}`}
    >
      <div className="text-sm text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-lg font-semibold text-[var(--text-muted)]">
        {value}
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

const BY_BM = "Không tách theo BM";
const BY_BM_NOTE =
  "Doanh số nhập theo người, không theo BM: chọn “Tất cả BM” để xem.";

function Report({
  data,
  onPick,
}: {
  data: AdsTeamReport;
  onPick: (userId: string) => void;
}) {
  const now = data.current;
  const before = data.before;
  const byBm = data.revenue_note === "filtered";
  return (
    <>
      <section aria-label="Chỉ số tổng" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 1 · Chỉ số tổng</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {byBm ? (
            <>
              <Missing
                testid="team-revenue"
                wide
                label="Doanh thu xét KPI"
                value={BY_BM}
                note={BY_BM_NOTE}
              />
              <Missing
                testid="team-ratio"
                label="Ads / Doanh thu"
                value={BY_BM}
                note="Cần doanh số để tính."
              />
              <Missing
                testid="team-kpi"
                label="Tiến độ KPI"
                value={BY_BM}
                note="KPI đặt cho cả phòng và từng người, không theo BM."
              />
              <Missing
                testid="team-sources"
                wide
                label="Doanh số theo nguồn"
                value={BY_BM}
                note={BY_BM_NOTE}
              />
            </>
          ) : (
            <>
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
              <Sources data={data} />
            </>
          )}
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
          {byBm ? (
            <Missing
              label="Ads / DT thực"
              value={BY_BM}
              note="Cần doanh số để tính."
            />
          ) : (
            <Tile
              label="Ads / DT thực"
              value={percent(now.ads_ratio_real)}
              change={pointsDelta(now.ads_ratio_real, before.ads_ratio_real)}
              sub="Lập hoá đơn ÷ doanh thu xét KPI"
            />
          )}
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

      <section
        aria-label="Hiệu suất theo nguồn quảng cáo"
        className="space-y-2"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="brand-eyebrow">
            Khối 2 · Hiệu suất theo nguồn quảng cáo · tháng{" "}
            {Number(data.month.slice(5, 7))}
          </h2>
          <span className="text-xs text-[var(--text-muted)]">
            Doanh thu tính theo DT hoa hồng
          </span>
        </div>
        <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <FacebookSource data={data} onPick={onPick} />
          <div className="grid content-start gap-3">
            <PendingSource
              name="Google Ads"
              source={data.sources.find((item) => item.key === "s:GOOGLE")}
            />
            <PendingSource
              name="TikTok Ads"
              source={data.sources.find((item) => item.key === "s:TIKTOK")}
            />
          </div>
        </div>
      </section>

      <section aria-label="Bảng chi tiết theo Marketing" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 3 · Bảng chi tiết theo Marketing</h2>
        <MarketerTable data={data} onPick={onPick} />
      </section>
    </>
  );
}

function Sources({ data }: { data: AdsTeamReport }) {
  const sourceTotal = data.sources.reduce(
    (sum, source) => sum + (source.revenue ?? 0),
    0,
  );
  return (
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
          Ngoài ra Marketing có {money(data.marketers_hn)} doanh số HN (khách SG
          làm tại HN): tính KPI của họ, không tính doanh thu cơ sở HCM.
        </p>
      ) : null}
    </div>
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

const CHIP: Record<string, string> = {
  good: "bg-[var(--good-soft)] text-[var(--good)]",
  watch: "bg-[var(--warn-soft)] text-[var(--warn)]",
  bad: "bg-[var(--bad-soft)] text-[var(--bad)]",
};

/** Ads / DT as a chip coloured by the threshold (with its text, never colour alone). */
function RatioChip({ value }: { value: number | null }) {
  const tone = ratioTone(value);
  return (
    <span
      className={`inline-block min-w-14 rounded-full px-2 py-0.5 text-center text-xs font-semibold tabular-nums ${tone ? CHIP[tone] : "bg-[var(--surface-muted)] text-[var(--text-muted)]"}`}
    >
      {value == null ? "–" : percent(value)}
    </span>
  );
}

function Bar({
  value,
  max,
  fill,
  label,
}: {
  value: number;
  max: number;
  fill: string;
  label: string;
}) {
  return (
    <span className="flex items-center gap-2">
      <span className="h-2.5 flex-1">
        <span
          className={`block h-full rounded-r-[4px] ${fill}`}
          style={{ width: `${Math.max(0, (value / max) * 100)}%` }}
        />
      </span>
      <span className="w-16 shrink-0 text-right text-xs tabular-nums text-[var(--text-muted)]">
        {label}
      </span>
    </span>
  );
}

function SourceHead({ name, meta }: { name: string; meta: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="text-base font-semibold">{name}</h3>
      <span className="text-xs text-[var(--text-muted)]">{meta}</span>
    </div>
  );
}

const WAITING_VALUE = "text-sm font-normal text-[var(--text-muted)]";

/** Facebook Ads: the month's figures, every marketer ranked, who needs attention. */
function FacebookSource({
  data,
  onPick,
}: {
  data: AdsTeamReport;
  onPick: (userId: string) => void;
}) {
  const fb = data.facebook;
  const active = data.marketers.filter((line) => line.figures.spend > 0);
  return (
    <div className={`${CARD} space-y-4`} data-testid="source-facebook">
      <SourceHead name="Facebook Ads" meta={`${active.length} marketing`} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Fact label="Chi tiêu" value={compactMoney(fb.spend)} />
        <Fact
          label="Doanh thu"
          value={
            fb.revenue_net == null ? (
              <span className={WAITING_VALUE}>chưa nhập</span>
            ) : (
              compactMoney(fb.revenue_net)
            )
          }
        />
        <Fact label="Ads / DT" value={<RatioChip value={fb.ads_ratio} />} />
        <Fact
          label="SĐT · Khách đến"
          value={
            <>
              {count(fb.phones)}{" "}
              <span className={WAITING_VALUE}>· khách chưa nhập</span>
            </>
          }
        />
      </div>
      <Ranking data={data} onPick={onPick} />
      <div className="rounded-lg bg-[var(--surface-muted)] p-3">
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
              <li key={alert.user_id} data-testid={`alert-${alert.user_id}`}>
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
  );
}

/**
 * A source not connected yet (Google / TikTok Ads): no spend, only the
 * revenue the Trưởng phòng enters on its ◆ line; the ranking waits.
 */
function PendingSource({
  name,
  source,
}: {
  name: string;
  source?: AdsTeamReport["sources"][number];
}) {
  const revenue = source?.revenue_net ?? null;
  return (
    <div
      className={`${CARD} space-y-3`}
      data-testid={`source-${name.split(" ")[0].toLowerCase()}`}
    >
      <SourceHead name={name} meta="chưa kết nối tài khoản" />
      <div className="grid grid-cols-2 gap-3">
        <Fact
          label="Chi tiêu"
          value={<span className={WAITING_VALUE}>chưa có</span>}
        />
        <Fact
          label="Doanh thu"
          value={
            revenue == null ? (
              <span className={WAITING_VALUE}>chưa nhập</span>
            ) : (
              compactMoney(revenue)
            )
          }
        />
        <Fact label="Ads / DT" value={<RatioChip value={null} />} />
        <Fact
          label="Khách · Bill TB"
          value={<span className={WAITING_VALUE}>chưa nhập</span>}
        />
      </div>
      <div className="rounded-lg border border-dashed border-[var(--border)] p-3 text-xs text-[var(--text-muted)]">
        <div className="font-semibold">Xếp hạng marketing {name}</div>
        Hiện lên khi có chi tiêu từ tài khoản {name} và doanh thu gán theo người
        chạy.
      </div>
    </div>
  );
}

/** Commission revenue and spend per marketer on one money axis. */
function Ranking({
  data,
  onPick,
}: {
  data: AdsTeamReport;
  onPick: (userId: string) => void;
}) {
  // Only people with numbers this month: an idle seat has nothing to rank.
  const order = data.marketers
    .filter((line) => line.figures.spend > 0 || line.figures.revenue_net)
    .sort(
      (a, b) =>
        (b.figures.revenue_net ?? 0) - (a.figures.revenue_net ?? 0) ||
        b.figures.spend - a.figures.spend,
    );
  const maxBar = Math.max(
    1,
    ...order.map((line) =>
      Math.max(line.figures.spend, line.figures.revenue_net ?? 0),
    ),
  );
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[var(--accent)]" /> Doanh thu
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[var(--text-muted)]" /> Chi
          tiêu
        </span>
        <span className="flex items-center gap-1">
          Ads / DT: <RatioChip value={0.3} /> ≤ 40% · <RatioChip value={0.5} />{" "}
          40–60% · <RatioChip value={0.7} /> &gt; 60%
        </span>
      </div>
      {order.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">
          Chưa có Marketing nào có số trong tháng.
        </p>
      ) : (
        <ul className="space-y-3" aria-label="Xếp hạng marketing Facebook Ads">
          {order.map((line) => (
            <li
              key={line.user_id}
              data-testid={`rank-${line.user_id}`}
              className="grid grid-cols-[7.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm"
            >
              <button
                type="button"
                onClick={() => onPick(line.user_id)}
                className="truncate text-left font-medium hover:underline"
                title={line.name}
              >
                {line.name}
              </button>
              <span className="space-y-0.5">
                <Bar
                  value={line.figures.revenue_net ?? 0}
                  max={maxBar}
                  fill="bg-[var(--accent)]"
                  label={
                    line.figures.revenue_net == null
                      ? "chưa có"
                      : compactMoney(line.figures.revenue_net)
                  }
                />
                <Bar
                  value={line.figures.spend}
                  max={maxBar}
                  fill="bg-[var(--text-muted)]"
                  label={compactMoney(line.figures.spend)}
                />
              </span>
              <RatioChip value={line.ads_ratio} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MarketerTable({
  data,
  onPick,
}: {
  data: AdsTeamReport;
  onPick: (userId: string) => void;
}) {
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
                <button
                  type="button"
                  onClick={() => onPick(line.user_id)}
                  className="text-left font-medium text-[var(--accent)] hover:underline"
                >
                  {line.name}
                </button>
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
