"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type AdsDashboard,
  type AdsDashboardQuery,
  type AdsKpi,
  type AdsPlatform,
} from "@/lib/api";
import {
  count,
  isManual,
  KpiCard,
  money,
  moveRange,
  percent,
  PlatformSelect,
  platformLabel,
  FlowList,
  ratioTone,
  RegionSelect,
  SeriesChart,
} from "@/components/ads";
import {
  ManualAccountButton,
  ManualDailyTable,
  ManualDayForm,
  ManualToolbar,
} from "@/components/ads-manual";
import { BudgetBoard } from "@/components/ads-budget";
import { AdsReports } from "@/components/ads-reports";
import { ranked, ViewPicker } from "@/components/ads-team";
import { DateInput } from "@/components/date-input";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate, formatDateTime, formatShortDate } from "@/lib/datetime";

/**
 * Dashboard Ads for one marketer (design board "Nhân viên · Dashboard Ads"):
 * a member's own numbers, or - for a Trưởng phòng ADS / the OWNER / an ADMIN
 * who picked them in "Xem chi tiết theo" - anybody's, under a banner with
 * their rank and "← Về khối tổng".
 *
 * * Khối 1: "Doanh số (xét KPI)" = SG + HN as entered (VAT included), the
 *   commission revenue without the 8% VAT, Ads / Doanh thu = spend ÷
 *   commission revenue, spend, messages, phone numbers. Customer figures
 *   (Bill TB, khách đến cửa, lịch) and Hotline per marketer are not entered
 *   yet: the cards say so, as the design does.
 * * Khối 2: spend (bars) and revenue (line) on one money axis, with the best
 *   revenue day, the days with spend and no revenue, the last five days.
 * * Khối 3: the ad accounts - summary and reconciliation, spend by day,
 *   "Số tiền lập hoá đơn" by day.
 * * Báo cáo chi tiết: the person's months and days (`AdsReports`).
 *
 * "Nền tảng" (0081): Facebook Ads from Meta (default), or Google / TikTok Ads
 * typed by hand - Khối 3 then lists every such account of the person, with
 * "+ Thêm tài khoản", "Nhập theo ngày", editable per-day tables and "Lịch sử
 * sửa" (`components/ads-manual.tsx`); their revenue is entered on the ◆
 * Google / Tiktok line, not per person, so the revenue cards wait.
 */
export function StaffDashboard({
  userId,
  onPick,
  platform = "FACEBOOK",
  onPlatform,
}: {
  /** The marketer picked by a manager; a member's own when absent. */
  userId?: string;
  /** A manager's "Xem chi tiết theo"; absent for a member. */
  onPick?: (userId: string) => void;
  platform?: AdsPlatform;
  onPlatform?: (platform: AdsPlatform) => void;
}) {
  const [filters, setFilters] = useState<AdsDashboardQuery>({ group: "day" });
  const query = {
    ...filters,
    // A BM is Facebook's.
    connection_id: platform === "FACEBOOK" ? filters.connection_id : undefined,
    user_id: userId,
    platform,
  };
  const board = useQuery({
    queryKey: ["ads", "dashboard", query],
    queryFn: () => api.adsDashboard(query),
    retry: false,
    placeholderData: (previous) => previous,
  });
  // A failed pick keeps the last answer (and its filters) on screen under
  // the error, so the dates can be put right.
  const [last, setLast] = useState<AdsDashboard>();
  useEffect(() => {
    if (board.data) setLast(board.data);
  }, [board.data]);
  const shown = board.data ?? last;
  const set = (patch: Partial<AdsDashboardQuery>) =>
    setFilters((current) => ({ ...current, ...patch }));
  const person = board.data?.people.find((item) => item.user_id === userId);
  return (
    <div className="space-y-5">
      <PageHeader
        title={person ? `Dashboard Ads · ${person.name}` : "Dashboard Ads"}
        subtitle={
          board.data
            ? `${platformLabel(board.data.platform)} · ${formatDate(board.data.start)} – ${formatDate(board.data.end)} · so với ${formatDate(board.data.previous_start)} – ${formatDate(board.data.previous_end)}${board.data.last_synced_at ? ` · ${isManual(board.data.platform) ? "nhập tay lần cuối" : "đồng bộ Meta"} lúc ${formatDateTime(board.data.last_synced_at)}` : ""}`
            : "Chi tiêu quảng cáo, doanh số và hoá đơn Meta theo tài khoản quảng cáo."
        }
      />
      {onPick && userId ? (
        <PersonBanner userId={userId} onPick={onPick} />
      ) : null}
      {board.isPending ? <Loading label="Đang tải số liệu quảng cáo…" /> : null}
      {board.isError ? (
        <ErrorBox error={board.error} onRetry={() => board.refetch()} />
      ) : null}
      {shown ? (
        <Workspace
          data={shown}
          userId={userId}
          filters={filters}
          set={set}
          platform={platform}
          onPlatform={onPlatform}
          picker={
            onPick ? <TeamPicker userId={userId ?? ""} onPick={onPick} /> : null
          }
        />
      ) : null}
    </div>
  );
}

/** This month's team report: the ranking behind the banner and the picker. */
function useTeamMonth() {
  return useQuery({
    queryKey: ["ads", "team", "", "all", ""],
    queryFn: () => api.adsTeam({ site: "all" }),
    retry: false,
  });
}

function TeamPicker({
  userId,
  onPick,
}: {
  userId: string;
  onPick: (userId: string) => void;
}) {
  const team = useTeamMonth();
  return team.data ? (
    <ViewPicker
      marketers={team.data.marketers}
      value={userId}
      onPick={onPick}
    />
  ) : null;
}

/** "Đang xem dashboard nhân viên của X · Hạng n/N theo doanh thu". */
function PersonBanner({
  userId,
  onPick,
}: {
  userId: string;
  onPick: (userId: string) => void;
}) {
  const team = useTeamMonth();
  const order = ranked(team.data?.marketers ?? []);
  const index = order.findIndex((line) => line.user_id === userId);
  const line = index >= 0 ? order[index] : null;
  const teamRevenue = order.reduce(
    (sum, item) => sum + (item.figures.revenue ?? 0),
    0,
  );
  const month = team.data ? `T${Number(team.data.month.slice(5, 7))}` : "";
  return (
    <div
      data-testid="person-banner"
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--accent)]/30 bg-[var(--accent-soft)] p-4"
    >
      <div>
        <div className="text-sm text-[var(--accent-strong)]">
          Đang xem dashboard nhân viên của{" "}
          <span className="font-semibold">{line?.name ?? "…"}</span>
        </div>
        {line ? (
          <div className="text-xs text-[var(--text-muted)]">
            {line.figures.revenue
              ? `Hạng ${index + 1}/${order.length} theo doanh thu ${month} · đóng góp ${percent(teamRevenue ? line.figures.revenue / teamRevenue : null)} doanh thu team`
              : `Chưa có doanh thu ${month}`}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onPick("")}
        className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]"
      >
        ← Về khối tổng
      </button>
    </div>
  );
}

const LABEL =
  "flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]";
const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
const CARD = "rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4";

const RATIO_BORDER: Record<string, string> = {
  good: "border-[var(--good)]",
  watch: "border-[var(--warn)]",
  bad: "border-[var(--bad)]",
};

/** A card the design keeps for figures that are not entered yet. */
function Missing({
  label,
  note,
  testid,
}: {
  label: string;
  note: string;
  testid?: string;
}) {
  return (
    <div
      data-testid={testid}
      className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="text-sm text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-lg font-semibold text-[var(--text-muted)]">
        Chưa có dữ liệu
      </div>
      <div className="mt-1 text-xs text-[var(--text-muted)]">{note}</div>
    </div>
  );
}

function share(
  part: number | null | undefined,
  whole: number | null | undefined,
): string {
  return part != null && whole ? percent(part / whole) : "—";
}

type Tab = "summary" | "entry" | "spend" | "invoice";

function Workspace({
  data,
  filters,
  set,
  picker,
  userId,
  platform,
  onPlatform,
}: {
  /** The marketer a manager picked (a member: their own, from the server). */
  userId?: string;
  data: AdsDashboard;
  filters: AdsDashboardQuery;
  set: (patch: Partial<AdsDashboardQuery>) => void;
  picker: React.ReactNode;
  platform: AdsPlatform;
  onPlatform?: (platform: AdsPlatform) => void;
}) {
  const [tab, setTab] = useState<Tab>("summary");
  const manual = isManual(data.platform);
  const shownTab: Tab = tab === "entry" && !manual ? "summary" : tab;
  const [asTable, setAsTable] = useState(false);
  const kpi: Record<string, AdsKpi> = Object.fromEntries(
    data.kpis.map((item) => [item.key, item]),
  );
  const revenue = kpi.revenue?.value ?? null;
  // No revenue to show: one marketer's Google / TikTok numbers (that revenue
  // is entered per source). Pháp nhân / BM / cơ sở narrow the costs only.
  const byPlatform = data.revenue_note === "platform";
  const region = filters.region;
  const tone = ratioTone(kpi.ads_ratio?.value);
  // Both ends sent, the other following when crossed (never a 422).
  const pickDay = (edge: "start" | "end", value: string) => {
    if (!value) return;
    set(
      moveRange(
        { start: filters.start ?? data.start, end: filters.end ?? data.end },
        edge,
        value,
      ),
    );
  };
  const highlights = data.highlights;
  return (
    <>
      <section aria-label="Bộ lọc" className="flex flex-wrap items-end gap-3">
        {picker}
        {onPlatform ? (
          <PlatformSelect value={platform} onChange={onPlatform} />
        ) : null}
        <label className={LABEL}>
          <span>Từ ngày</span>
          <DateInput
            value={filters.start ?? data.start}
            onChange={(event) => pickDay("start", event.target.value)}
            aria-label="Từ ngày"
            className={`${FIELD} w-40`}
          />
        </label>
        <label className={LABEL}>
          <span>Đến ngày</span>
          <DateInput
            value={filters.end ?? data.end}
            onChange={(event) => pickDay("end", event.target.value)}
            aria-label="Đến ngày"
            className={`${FIELD} w-40`}
          />
        </label>
        <label className={LABEL}>
          <span>Pháp nhân</span>
          <Select
            aria-label="Pháp nhân"
            value={filters.brand ?? ""}
            onChange={(event) =>
              set({ brand: event.target.value || undefined })
            }
          >
            <option value="">Tất cả</option>
            {data.brands
              .filter((brand) => brand.value !== "OTHER")
              .map((brand) => (
                <option key={brand.value} value={brand.value}>
                  {brand.label}
                </option>
              ))}
          </Select>
        </label>
        {data.connections.length && !manual ? (
          <label className={LABEL}>
            <span>BM</span>
            <Select
              aria-label="BM"
              value={filters.connection_id ?? ""}
              onChange={(event) =>
                set({ connection_id: event.target.value || undefined })
              }
            >
              <option value="">Tất cả BM</option>
              {data.connections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.name}
                  {connection.site ? ` · ${connection.site}` : ""}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
        <RegionSelect
          value={region ?? ""}
          onChange={(value) =>
            set({ region: (value || undefined) as AdsDashboardQuery["region"] })
          }
        />
      </section>

      <section aria-label="Chỉ số chính" className="space-y-2">
        <h2 className="brand-eyebrow">Khối 1 · Chỉ số chính</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {byPlatform || revenue === null ? (
            <div
              data-testid="kpi-revenue"
              className={`${CARD} border-dashed sm:col-span-2`}
            >
              <div className="text-sm text-[var(--text-muted)]">
                Doanh số (xét KPI)
              </div>
              <div className="mt-1 text-2xl font-semibold text-[var(--text-muted)]">
                {byPlatform ? "Không tách được" : "Chưa nhập"}
              </div>
              <div className="mt-1 text-xs text-[var(--text-muted)]">
                {byPlatform
                  ? `Doanh thu ${platformLabel(data.platform)} nhập chung ở dòng ◆ của nguồn (Nhập doanh thu), không theo người: xem ở Tổng team.`
                  : region
                    ? "Chưa có doanh số thuộc lựa chọn Miền này trong khoảng ngày."
                    : "Chưa có doanh số nào trong khoảng ngày này (CRM hoặc Phòng Ads › Nhập doanh thu)."}
              </div>
            </div>
          ) : (
            <KpiCard
              kpi={kpi.revenue}
              hero
              note={`Doanh thu hoa hồng ${money(data.revenue_net)} · Bill TB chưa có dữ liệu`}
            />
          )}
          {kpi.revenue_sg?.value && !byPlatform ? (
            <KpiCard
              kpi={kpi.revenue_sg}
              note={`${share(kpi.revenue_sg.value, revenue)} · khách làm tại cơ sở HCM`}
            />
          ) : null}
          {kpi.revenue_hn?.value && !byPlatform ? (
            <KpiCard
              kpi={kpi.revenue_hn}
              note={`${share(kpi.revenue_hn.value, revenue)} · khách làm tại cơ sở HN`}
            />
          ) : null}
          {!byPlatform && data.flows && Object.values(data.flows).some(Boolean) ? (
            <div data-testid="kpi-flows" className={`${CARD} sm:col-span-2`}>
              <div className="text-sm text-[var(--text-muted)]">
                Luồng khách · đội MKT → cơ sở làm
              </div>
              <FlowList values={data.flows} />
            </div>
          ) : null}
          {manual ? null : (
            <Missing
              label="Hotline"
              note="Chưa gán Hotline theo marketing."
              testid="kpi-hotline"
            />
          )}
          {kpi.spend ? <KpiCard kpi={kpi.spend} /> : null}
          {manual && kpi.invoice ? (
            <KpiCard
              kpi={kpi.invoice}
              note={`Nhập tay · chênh với chi tiêu ${money((kpi.invoice.value ?? 0) - (kpi.spend?.value ?? 0))}`}
            />
          ) : null}
          {kpi.ads_ratio && !byPlatform ? (
            <KpiCard
              kpi={kpi.ads_ratio}
              note="Chi tiêu ÷ DT hoa hồng · ngưỡng ≤ 40%"
              border={`border-2 ${tone ? RATIO_BORDER[tone] : "border-[var(--border)]"}`}
            />
          ) : null}
          {manual ? (
            <Missing
              label="Mess · SĐT"
              note={`${platformLabel(data.platform)} nhập tay chi tiêu và lập hoá đơn; chưa nhập mess, SĐT.`}
              testid="kpi-manual-leads"
            />
          ) : null}
          {kpi.messages && !manual ? <KpiCard kpi={kpi.messages} /> : null}
          {kpi.cost_per_message && !manual ? (
            <KpiCard kpi={kpi.cost_per_message} note="= Chi tiêu ÷ Số mess" />
          ) : null}
          {kpi.phones && !manual ? (
            <KpiCard
              kpi={kpi.phones}
              note={`${kpi.phone_rate?.value != null ? `${percent(kpi.phone_rate.value)} mess ra số · ` : ""}khách đến cửa chưa có dữ liệu`}
            />
          ) : null}
          {kpi.cost_per_phone && !manual ? (
            <KpiCard
              kpi={kpi.cost_per_phone}
              note="Lịch nóng · gối · tối ưu chưa có dữ liệu"
            />
          ) : null}
        </div>
      </section>

      <section aria-label="Xu hướng" className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="brand-eyebrow">
            Khối 2 · Xu hướng chi tiêu và doanh số theo ngày
          </h2>
          <button
            type="button"
            onClick={() => setAsTable((value) => !value)}
            className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm hover:bg-[var(--surface-muted)]"
          >
            {asTable ? "Xem biểu đồ" : "Xem bảng"}
          </button>
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className={CARD}>
            <div className="mb-2 flex flex-wrap gap-4 text-xs text-[var(--text-muted)]">
              <span className="flex items-center gap-1.5">
                <span className="h-3 w-3 rounded-sm bg-[var(--accent)]" /> Tiền
                chi tiêu (cột)
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-0.5 w-4 rounded bg-[var(--magenta)]" /> Doanh
                số xét KPI (đường)
              </span>
              <span>cùng một trục tiền</span>
            </div>
            {asTable ? (
              <div className="table-scroll-x">
                <table className="table-dense w-full">
                  <thead>
                    <tr>
                      <th className="text-left">Ngày</th>
                      <th className="text-right">Chi tiêu</th>
                      <th className="text-right">Doanh số</th>
                      <th className="text-right">Ads / DT</th>
                      <th className="text-right">SĐT</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.series.map((point) => (
                      <tr key={point.start}>
                        <td>{formatShortDate(point.start)}</td>
                        <td className="text-right tabular-nums">
                          {money(point.spend)}
                        </td>
                        <td className="text-right tabular-nums">
                          {money(point.revenue)}
                        </td>
                        <td className="text-right tabular-nums">
                          {percent(point.ads_ratio)}
                        </td>
                        <td className="text-right tabular-nums">
                          {count(point.phones)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <SeriesChart
                points={data.series}
                measure="spend"
                label="Chi tiêu và doanh số"
                revenueLine
              />
            )}
          </div>
          <div className="space-y-3">
            <div className={CARD}>
              <div className="text-xs font-semibold uppercase text-[var(--good)]">
                Ngày doanh số cao nhất
              </div>
              {highlights?.best_revenue_day ? (
                <>
                  <div className="mt-1 text-lg font-semibold">
                    {formatShortDate(highlights.best_revenue_day.start)} ·{" "}
                    {money(highlights.best_revenue_day.revenue)}
                  </div>
                  <div className="text-xs text-[var(--text-muted)]">
                    Chi {money(highlights.best_revenue_day.spend)} · Ads/DT{" "}
                    {percent(highlights.best_revenue_day.ads_ratio)}
                  </div>
                </>
              ) : (
                <div className="mt-1 text-sm text-[var(--text-muted)]">
                  Chưa có doanh số trong khoảng này.
                </div>
              )}
            </div>
            <div className={CARD}>
              <div className="text-xs font-semibold uppercase text-[var(--bad)]">
                Có chi tiêu, chưa ra doanh số
              </div>
              {byPlatform ? (
                <div className="mt-1 text-sm text-[var(--text-muted)]">
                  Doanh thu nền tảng này không tách theo người.
                </div>
              ) : highlights && highlights.zero_revenue_days.length ? (
                <>
                  <div className="mt-1 text-lg font-semibold">
                    {highlights.zero_revenue_days.length} ngày · chi{" "}
                    {money(
                      highlights.zero_revenue_days.reduce(
                        (sum, point) => sum + point.spend,
                        0,
                      ),
                    )}
                  </div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {highlights.zero_revenue_days
                      .slice(-6)
                      .map((point) => formatShortDate(point.start))
                      .join(" · ")}
                  </div>
                </>
              ) : (
                <div className="mt-1 text-sm text-[var(--text-muted)]">
                  Không có ngày nào.
                </div>
              )}
            </div>
            <div className={CARD}>
              <div className="text-xs font-semibold uppercase text-[var(--text-muted)]">
                Chi tiêu 5 ngày gần nhất
              </div>
              <div className="mt-1 text-lg font-semibold">
                {money(highlights?.last5_spend ?? 0)}
              </div>
              <div className="text-xs text-[var(--text-muted)]">
                {highlights && highlights.last5_days
                  ? `TB ${money(highlights.last5_spend / highlights.last5_days)}/ngày${
                      highlights.earlier_daily_spend != null
                        ? ` · trước đó ${money(highlights.earlier_daily_spend)}/ngày`
                        : ""
                    }`
                  : "Chưa có chi tiêu."}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="Theo tài khoản quảng cáo" className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="brand-eyebrow">
            Khối 3 · Chi tiêu và lập hoá đơn theo tài khoản quảng cáo
          </h2>
          <div
            role="tablist"
            aria-label="Bảng tài khoản"
            className="flex flex-wrap gap-1 rounded-lg bg-[var(--surface-muted)] p-1"
          >
            {(
              [
                ["summary", "Tổng hợp & đối soát"],
                ...(manual ? ([["entry", "Nhập theo ngày"]] as const) : []),
                ["spend", "Chi tiêu theo ngày"],
                ["invoice", "Lập hoá đơn theo ngày"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={shownTab === value}
                onClick={() => setTab(value)}
                className={`min-h-9 rounded-md px-3 text-sm ${
                  shownTab === value
                    ? "bg-[var(--surface)] font-semibold text-[var(--text)]"
                    : "text-[var(--text-muted)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {manual ? <ManualToolbar data={data} userId={userId} /> : null}
        {data.accounts.length === 0 ? (
          <p className="panel p-4 text-sm text-[var(--text-muted)]">
            {manual
              ? `Chưa có tài khoản ${platformLabel(data.platform)} nào. Bấm “+ Thêm tài khoản” để thêm tài khoản đang chạy, rồi nhập chi tiêu và số tiền lập hoá đơn theo ngày.`
              : "Chưa có chi tiêu trong khoảng này. Nếu vừa thêm BM, đợi lượt đồng bộ 08:00 hoặc bấm “Đồng bộ ngay” ở Phòng Ads › Kết nối BM; tài khoản chưa gán người được gán ở “Tài khoản QC”."}
          </p>
        ) : shownTab === "summary" ? (
          <SummaryTable data={data} />
        ) : shownTab === "entry" ? (
          <ManualDayForm data={data} />
        ) : manual ? (
          <ManualDailyTable data={data} kind={shownTab} />
        ) : (
          <DailyTable data={data} kind={shownTab} />
        )}
      </section>

      <BudgetBoard month={data.end.slice(0, 7)} userId={userId} />

      <AdsReports
        month={data.end.slice(0, 7)}
        userId={userId}
        connectionId={manual ? undefined : filters.connection_id}
        site={region}
        platform={data.platform}
      />
    </>
  );
}

function SummaryTable({ data }: { data: AdsDashboard }) {
  const totals = data.accounts.reduce(
    (sum, line) => ({
      spend: sum.spend + line.spend,
      invoice: sum.invoice + line.invoice,
      unmatched: sum.unmatched + (line.matched ? 0 : 1),
    }),
    { spend: 0, invoice: 0, unmatched: 0 },
  );
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense w-full min-w-[920px]">
        <thead>
          <tr>
            <th className="text-left">STT</th>
            <th className="text-left">Tài khoản quảng cáo</th>
            <th className="text-left">ID TKQC · Số thẻ</th>
            <th className="text-left">Pháp nhân</th>
            <th className="text-right">Số tiền chi tiêu</th>
            <th className="text-right">Số tiền lập hoá đơn</th>
            <th className="text-right">LHĐ / chi tiêu</th>
            <th className="text-left">Đối soát</th>
          </tr>
        </thead>
        <tbody>
          {data.accounts.map((line, index) => (
            <tr
              key={line.account_id}
              data-testid={`ads-line-${line.account_id}`}
            >
              <td className="text-[var(--text-muted)]">{index + 1}</td>
              <td>
                <div className="font-medium">{line.name}</div>
                <div className="text-xs text-[var(--text-muted)]">
                  {line.connection_name}
                  {line.site && !line.connection_name.includes(line.site)
                    ? ` · ${line.site}`
                    : ""}
                  {line.user_name ? ` · ${line.user_name}` : " · Chưa gán"}
                </div>
                <ManualAccountButton data={data} line={line} />
              </td>
              <td className="text-sm tabular-nums">
                {line.external_id ?? line.account_id}
                {line.card_last4 ? (
                  <div className="text-xs text-[var(--text-muted)]">
                    •••• {line.card_last4}
                  </div>
                ) : null}
              </td>
              <td className="text-sm">{line.brand_label}</td>
              <td className="text-right tabular-nums">{money(line.spend)}</td>
              <td className="text-right tabular-nums">{money(line.invoice)}</td>
              <td className="text-right tabular-nums">
                {percent(line.invoice_ratio)}
              </td>
              <td>
                <Pill tone={line.matched ? "good" : "bad"}>
                  {line.matched ? "Đã khớp" : "Chưa khớp"}
                </Pill>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <td />
            <td>Tổng cộng</td>
            <td />
            <td />
            <td className="text-right tabular-nums">{money(totals.spend)}</td>
            <td className="text-right tabular-nums">{money(totals.invoice)}</td>
            <td className="text-right tabular-nums">
              {percent(totals.spend ? totals.invoice / totals.spend : null)}
            </td>
            <td className="text-xs text-[var(--text-muted)]">
              {totals.unmatched}/{data.accounts.length} chưa khớp
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** One row per account, one column per day (spend or "Số tiền lập hoá đơn"). */
function DailyTable({
  data,
  kind,
}: {
  data: AdsDashboard;
  kind: "spend" | "invoice";
}) {
  const days = data.days ?? [];
  if (!days.length) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        Chọn khoảng tối đa 62 ngày để xem theo từng ngày.
      </p>
    );
  }
  const valuesOf = (line: AdsDashboard["accounts"][number]) =>
    (kind === "spend" ? line.spend_by_day : line.invoice_by_day) ?? [];
  const dayTotals = days.map((_, index) =>
    data.accounts.reduce((sum, line) => sum + (valuesOf(line)[index] ?? 0), 0),
  );
  const million = (value: number) =>
    value > 0
      ? (value / 1e6).toLocaleString("vi-VN", {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        })
      : "–";
  return (
    <div className="panel table-scroll-x">
      <table
        className="table-dense w-full"
        style={{ minWidth: `${320 + days.length * 56}px` }}
      >
        <thead>
          <tr>
            <th className="text-left">Tài khoản · ID TKQC</th>
            <th className="text-left">Pháp nhân</th>
            <th className="text-right">Tổng</th>
            {days.map((day) => (
              <th key={day} className="text-right">
                {formatShortDate(day).slice(0, 2)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.accounts.map((line) => {
            const values = valuesOf(line);
            return (
              <tr key={line.account_id}>
                <td>
                  <div className="font-medium">{line.name}</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {line.account_id}
                  </div>
                </td>
                <td className="text-sm">{line.brand_label}</td>
                <td className="text-right tabular-nums">
                  {money(values.reduce((sum, value) => sum + value, 0))}
                </td>
                {values.map((value, index) => (
                  <td key={days[index]} className="text-right tabular-nums">
                    {million(value)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <td>TỔNG NGÀY</td>
            <td />
            <td className="text-right tabular-nums">
              {money(dayTotals.reduce((sum, value) => sum + value, 0))}
            </td>
            {dayTotals.map((value, index) => (
              <td key={days[index]} className="text-right tabular-nums">
                {million(value)}
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
      <p className="px-3 pb-2 text-xs text-[var(--text-muted)]">
        Các cột ngày tính bằng triệu ₫.
      </p>
    </div>
  );
}
