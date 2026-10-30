"use client";

import { useState } from "react";
import type {
  AdsKpi,
  AdsPlatform,
  AdsRegionFigures,
  AdsRegionSplit,
  AdsSeriesPoint,
} from "@/lib/api";
import { Select } from "@/components/pr";
import { formatShortDate } from "@/lib/datetime";

/**
 * Pieces shared by the Phòng Ads screens (0078): number formats, the KPI
 * card and the one-measure bar chart. One axis only: a second measure is
 * picked with the "Chỉ số" switch, never drawn on a second scale.
 */

export type DateRange = { start: string; end: string };

/**
 * The platforms (0081), Facebook first - the default. Google / TikTok Ads have
 * no API here: the marketer adds the account and types the numbers.
 */
export const PLATFORMS: ReadonlyArray<{
  value: AdsPlatform;
  label: string;
  manual: boolean;
}> = [
  { value: "FACEBOOK", label: "Facebook Ads", manual: false },
  { value: "GOOGLE", label: "Google Ads", manual: true },
  { value: "TIKTOK", label: "TikTok Ads", manual: true },
];

export function platformLabel(platform: AdsPlatform | undefined): string {
  return (
    PLATFORMS.find((item) => item.value === platform)?.label ?? "Facebook Ads"
  );
}

export function isManual(platform: AdsPlatform | undefined): boolean {
  return Boolean(PLATFORMS.find((item) => item.value === platform)?.manual);
}

/** The "Nền tảng" filter of the dashboards. */
export function PlatformSelect({
  value,
  onChange,
}: {
  value: AdsPlatform;
  onChange: (platform: AdsPlatform) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
      <span>Nền tảng</span>
      <Select
        aria-label="Nền tảng"
        value={value}
        onChange={(event) => onChange(event.target.value as AdsPlatform)}
      >
        {PLATFORMS.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </Select>
    </label>
  );
}

/** An amount inside a sentence, bold so it stands out from the words. */
export function Money({ value }: { value: number | null | undefined }) {
  return <strong className="font-semibold text-[var(--text)]">{money(value)}</strong>;
}

/**
 * The note under the revenue when a BM is picked (2026-10-30): the revenue is
 * that region's CRM accounts', made of the customers served at home and those
 * sent to the other cơ sở.
 */
export function BmRevenueNote({
  region,
  flows,
}: {
  region: "HN" | "HCM";
  flows: AdsRegionSplit["flows"];
}) {
  return region === "HN" ? (
    <>
      HN làm ở HN <Money value={flows.hn_hn ?? 0} /> · HN chuyển SG{" "}
      <Money value={flows.hn_hcm ?? 0} />
    </>
  ) : (
    <>
      SG làm ở SG <Money value={flows.hcm_hcm ?? 0} /> · SG chuyển HN{" "}
      <Money value={flows.hcm_hn ?? 0} />
    </>
  );
}

const REGION_TONE: Record<string, string> = {
  good: "bg-[var(--good-soft)] text-[var(--good)]",
  watch: "bg-[var(--warn-soft)] text-[var(--warn)]",
  bad: "bg-[var(--bad-soft)] text-[var(--bad)]",
};

function RegionLine({
  label,
  value,
  parts,
  figures,
}: {
  label: string;
  value: number;
  parts: React.ReactNode;
  /** With a cost: the Marketing line shows its hiệu suất. */
  figures?: AdsRegionFigures;
}) {
  return (
    <li className="space-y-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-semibold">{label}</span>
        <span className="text-lg font-semibold tabular-nums">{money(value)}</span>
      </div>
      <div className="text-xs text-[var(--text-muted)]">{parts}</div>
      {figures ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <span>Hiệu suất</span>
          <span
            className={`rounded-md px-1.5 py-0.5 font-semibold tabular-nums ${
              figures.tone ? REGION_TONE[figures.tone] : "bg-[var(--surface-muted)]"
            }`}
          >
            {percent(figures.ads_ratio)}
          </span>
          <span>
            = chi phí BM {label} <Money value={figures.invoice || figures.spend} /> ÷ doanh
            thu CRM {label} (hoa hồng) <Money value={figures.marketing_net} />
          </span>
        </div>
      ) : null}
    </li>
  );
}

/**
 * Under "Doanh thu tổng": the same revenue counted by the team that brought
 * the customer (Doanh thu Marketing: HN = HN + HN chuyển SG, SG = SG + SG
 * chuyển HN) and by the branch that served them (Doanh thu Cơ sở: HN = HN +
 * SG chuyển HN, SG = SG + HN chuyển SG). Both add up to the total; hiệu suất =
 * the region's marketing cost (its BM) ÷ its Marketing revenue without VAT.
 */
export function RegionRevenue({
  regions,
  only = null,
  className = "",
}: {
  regions: AdsRegionSplit;
  /** A BM picked: Doanh thu Marketing shows its region alone; Cơ sở stays whole. */
  only?: "HN" | "HCM" | null;
  className?: string;
}) {
  const flow = (key: keyof AdsRegionSplit["flows"]) => regions.flows[key] ?? 0;
  const card = `rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 ${className}`;
  const total =
    only === "HN"
      ? regions.hn.marketing
      : only === "HCM"
        ? regions.hcm.marketing
        : regions.hn.marketing + regions.hcm.marketing;
  return (
    <>
      <div data-testid="revenue-marketing" className={card}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-sm text-[var(--text-muted)]">
            Doanh thu Marketing{only ? ` · BM ${only === "HN" ? "HN" : "SG"}` : ""}
          </span>
          <span className="text-sm font-semibold tabular-nums">{money(total)}</span>
        </div>
        <ul className="mt-2 space-y-2 text-sm">
          {only !== "HCM" ? (
            <RegionLine
              label="HN"
              value={regions.hn.marketing}
              parts={
                <>
                  HN <Money value={flow("hn_hn")} /> + HN chuyển SG{" "}
                  <Money value={flow("hn_hcm")} />
                </>
              }
              figures={regions.hn}
            />
          ) : null}
          {only !== "HN" ? (
            <RegionLine
              label="SG"
              value={regions.hcm.marketing}
              parts={
                <>
                  SG <Money value={flow("hcm_hcm")} /> + SG chuyển HN{" "}
                  <Money value={flow("hcm_hn")} />
                </>
              }
              figures={regions.hcm}
            />
          ) : null}
        </ul>
      </div>
      <div data-testid="revenue-site" className={card}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-sm text-[var(--text-muted)]">Doanh thu Cơ sở</span>
          <span className="text-sm font-semibold tabular-nums">
            {money(regions.hn.site + regions.hcm.site)}
          </span>
        </div>
        <ul className="mt-2 space-y-2 text-sm">
          <RegionLine
            label="HN"
            value={regions.hn.site}
            parts={
              <>
                HN <Money value={flow("hn_hn")} /> + SG chuyển HN{" "}
                <Money value={flow("hcm_hn")} />
              </>
            }
          />
          <RegionLine
            label="SG"
            value={regions.hcm.site}
            parts={
              <>
                SG <Money value={flow("hcm_hcm")} /> + HN chuyển SG{" "}
                <Money value={flow("hn_hcm")} />
              </>
            }
          />
        </ul>
      </div>
    </>
  );
}

const DAY_MS = 86_400_000;

/**
 * One end of a `YYYY-MM-DD` range moved, the range kept valid: the other end
 * follows when crossed (so "Đến ngày" before "Từ ngày" is never sent - the
 * server answers 422 `ads_period_invalid`), and the span stays within a year,
 * the server's limit.
 */
export function moveRange(
  range: DateRange,
  edge: "start" | "end",
  value: string,
): DateRange {
  let start = edge === "start" ? value : range.start;
  let end = edge === "end" ? value : range.end;
  if (start > end) {
    if (edge === "start") end = start;
    else start = end;
  }
  const shift = (iso: string, days: number) =>
    new Date(Date.parse(iso) + days * DAY_MS).toISOString().slice(0, 10);
  if ((Date.parse(end) - Date.parse(start)) / DAY_MS + 1 > 366) {
    if (edge === "start") end = shift(start, 365);
    else start = shift(end, -365);
  }
  return { start, end };
}

/** 1.250.000.000 ₫ */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value).toLocaleString("vi-VN")} ₫`;
}

/** 4.250 */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Math.round(value).toLocaleString("vi-VN");
}

/** 0.24 -> "24,0%" */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

/** "128 tr", "1,25 tỷ" for an axis or a bar label. */
export function compactMoney(value: number): string {
  if (value >= 1e9)
    return `${(value / 1e9).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`;
  if (value >= 1e6)
    return `${(value / 1e6).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} tr`;
  if (value >= 1e3)
    return `${Math.round(value / 1e3).toLocaleString("vi-VN")} k`;
  return Math.round(value).toLocaleString("vi-VN");
}

/** The design's bands for Ads / Doanh thu: ≤ 40% good, ≤ 60% watch, above: act now. */
export function ratioTone(
  value: number | null | undefined,
): "good" | "watch" | "bad" | null {
  if (value === null || value === undefined) return null;
  if (value <= 0.4) return "good";
  if (value <= 0.6) return "watch";
  return "bad";
}

export function kpiValue(kpi: AdsKpi): string {
  if (kpi.key === "phone_rate" || kpi.key === "ads_ratio")
    return percent(kpi.value);
  if (kpi.key === "messages" || kpi.key === "phones") return count(kpi.value);
  return money(kpi.value);
}

/** Amounts spent: more or less is neither good nor bad by itself. */
const NEUTRAL_KPIS = new Set(["spend", "invoice"]);

/** "▲ 8,4%" with its tone: up is good unless the KPI is a cost; spend is neutral. */
export function changeOf(
  kpi: AdsKpi,
): { text: string; tone: "good" | "bad" | "muted" } | null {
  if (kpi.change === null) return null;
  const up = kpi.change >= 0;
  const better = kpi.lower_is_better ? !up : up;
  const size = Math.abs(kpi.change * 100).toLocaleString("vi-VN", {
    maximumFractionDigits: 1,
  });
  return {
    text: `${up ? "▲" : "▼"} ${size}% so kỳ trước`,
    tone:
      kpi.change === 0 || NEUTRAL_KPIS.has(kpi.key)
        ? "muted"
        : better
          ? "good"
          : "bad",
  };
}

const TONE_TEXT = {
  good: "text-[var(--good)]",
  bad: "text-[var(--bad)]",
  muted: "text-[var(--text-muted)]",
} as const;

export function KpiCard({
  kpi,
  hero = false,
  note,
  border = "border border-[var(--border)]",
}: {
  kpi: AdsKpi;
  hero?: boolean;
  note?: React.ReactNode;
  /** The card's border classes (a highlighted card passes a coloured one). */
  border?: string;
}) {
  const delta = changeOf(kpi);
  return (
    <div
      data-testid={`kpi-${kpi.key}`}
      className={`rounded-xl ${border} bg-[var(--surface)] p-4 ${hero ? "sm:col-span-2" : ""}`}
    >
      <div className="text-sm text-[var(--text-muted)]">{kpi.label}</div>
      <div
        className={`mt-1 font-semibold tabular-nums ${hero ? "text-3xl" : "text-2xl"}`}
      >
        {kpiValue(kpi)}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2 text-xs">
        {delta ? (
          <span className={`font-medium ${TONE_TEXT[delta.tone]}`}>
            {delta.text}
          </span>
        ) : null}
        {note ? <span className="text-[var(--text-muted)]">{note}</span> : null}
        {!delta && !note ? (
          <span className="text-[var(--text-muted)]">
            Chưa có kỳ trước để so
          </span>
        ) : null}
      </div>
    </div>
  );
}

export type SeriesMeasure =
  "spend" | "revenue" | "ads_ratio" | "phones" | "messages" | "cost_per_phone";

export const MEASURES: ReadonlyArray<{ value: SeriesMeasure; label: string }> =
  [
    { value: "spend", label: "Tiền chi tiêu" },
    { value: "revenue", label: "Doanh số" },
    { value: "ads_ratio", label: "% Ads / Doanh thu" },
    { value: "phones", label: "SĐT thu được" },
    { value: "messages", label: "Số mess" },
    { value: "cost_per_phone", label: "Chi phí / SĐT" },
  ];

function measureOf(point: AdsSeriesPoint, measure: SeriesMeasure): number {
  return point[measure] ?? 0;
}

export function formatMeasure(value: number, measure: SeriesMeasure): string {
  if (measure === "ads_ratio") return percent(value);
  return measure === "phones" || measure === "messages"
    ? count(value)
    : money(value);
}

/** One measure over time, one bar per bucket, a tooltip on hover / focus. */
export function SeriesChart({
  points,
  measure,
  label,
  revenueLine = false,
  target,
  labelOf = (point) => formatShortDate(point.start),
  tooltip,
}: {
  points: AdsSeriesPoint[];
  measure: SeriesMeasure;
  label: string;
  /** Spend bars with the revenue as a line on the same money axis (one unit, one axis). */
  revenueLine?: boolean;
  /** A target per bucket (the month's KPI), dashed, on the same money axis. */
  target?: Array<number | null>;
  /** The bucket's name on the axis and in the tooltip (default its date). */
  labelOf?: (point: AdsSeriesPoint) => string;
  /** The tooltip's lines under the bucket's name (default the day's figures). */
  tooltip?: (point: AdsSeriesPoint, index: number) => React.ReactNode;
}) {
  const [active, setActive] = useState<number | null>(null);
  const width = 960;
  const height = 260;
  const left = 56;
  const bottom = 28;
  const top = 12;
  const plotHeight = height - top - bottom;
  const values = points.map((point) => measureOf(point, measure));
  const line = revenueLine ? points.map((point) => point.revenue ?? null) : [];
  const goals = target ?? [];
  const max =
    Math.max(
      ...values,
      ...line.map((value) => value ?? 0),
      ...goals.map((value) => value ?? 0),
      0,
    ) || 1;
  const step = (width - left) / Math.max(points.length, 1);
  const bar = Math.max(4, Math.min(28, step - 2));
  const ticks = [0, 0.5, 1].map((share) => share * max);
  const isMoney =
    measure === "spend" ||
    measure === "revenue" ||
    measure === "cost_per_phone";
  const labelEvery = Math.ceil(points.length / 14);
  const shown = active !== null ? points[active] : null;
  if (!values.some(Boolean) && !line.some(Boolean)) {
    return (
      <p className="py-10 text-center text-sm text-[var(--text-muted)]">
        Chưa có số liệu trong khoảng này.
      </p>
    );
  }
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={`${label} theo thời gian`}
      >
        {ticks.map((tick) => {
          const y = top + plotHeight - (tick / max) * plotHeight;
          return (
            <g key={tick}>
              <line
                x1={left}
                x2={width}
                y1={y}
                y2={y}
                stroke="var(--border)"
                strokeWidth={1}
              />
              <text
                x={left - 8}
                y={y + 4}
                textAnchor="end"
                fontSize={12}
                fill="var(--text-muted)"
              >
                {measure === "ads_ratio"
                  ? percent(tick)
                  : isMoney
                    ? compactMoney(tick)
                    : Math.round(tick).toLocaleString("vi-VN")}
              </text>
            </g>
          );
        })}
        {points.map((point, index) => {
          const value = values[index];
          const h = (value / max) * plotHeight;
          const x = left + index * step + (step - bar) / 2;
          const y = top + plotHeight - h;
          return (
            <g
              key={point.start}
              tabIndex={0}
              onMouseEnter={() => setActive(index)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(index)}
              onBlur={() => setActive(null)}
              aria-label={`${labelOf(point)}: ${formatMeasure(value, measure)}`}
            >
              {/* A hit target taller and wider than the bar. */}
              <rect
                x={left + index * step}
                y={top}
                width={step}
                height={plotHeight}
                fill="transparent"
              />
              {h > 0 ? (
                <path
                  d={`M${x},${top + plotHeight} V${y + Math.min(4, h)} Q${x},${y} ${x + Math.min(4, bar / 2)},${y} H${x + bar - Math.min(4, bar / 2)} Q${x + bar},${y} ${x + bar},${y + Math.min(4, h)} V${top + plotHeight} Z`}
                  fill="var(--accent)"
                  opacity={active === null || active === index ? 1 : 0.45}
                />
              ) : null}
              {index % labelEvery === 0 ? (
                <text
                  x={left + index * step + step / 2}
                  y={height - 8}
                  textAnchor="middle"
                  fontSize={12}
                  fill="var(--text-muted)"
                >
                  {labelOf(point)}
                </text>
              ) : null}
            </g>
          );
        })}
        {revenueLine ? (
          <g aria-hidden="true">
            <polyline
              fill="none"
              stroke="var(--magenta)"
              strokeWidth={2}
              strokeLinejoin="round"
              points={line
                .map((value, index) =>
                  value === null
                    ? null
                    : `${left + index * step + step / 2},${top + plotHeight - (value / max) * plotHeight}`,
                )
                .filter(Boolean)
                .join(" ")}
            />
            {line.map((value, index) =>
              value === null ? null : (
                <circle
                  key={index}
                  cx={left + index * step + step / 2}
                  cy={top + plotHeight - (value / max) * plotHeight}
                  r={4}
                  fill="var(--surface)"
                  stroke="var(--magenta)"
                  strokeWidth={2}
                />
              ),
            )}
          </g>
        ) : null}
        {goals.map((value, index) =>
          value === null ? null : (
            <line
              key={`goal-${index}`}
              aria-hidden="true"
              x1={left + index * step + 4}
              x2={left + (index + 1) * step - 4}
              y1={top + plotHeight - (value / max) * plotHeight}
              y2={top + plotHeight - (value / max) * plotHeight}
              stroke="var(--text)"
              strokeWidth={2}
              strokeDasharray="5 4"
            />
          ),
        )}
        <line
          x1={left}
          x2={width}
          y1={top + plotHeight}
          y2={top + plotHeight}
          stroke="var(--text-muted)"
          strokeWidth={1}
        />
      </svg>
      {shown ? (
        <div
          role="status"
          className="pointer-events-none absolute right-2 top-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs shadow-[var(--shadow-pop)]"
        >
          <div className="font-semibold">
            {tooltip ? labelOf(shown) : `Từ ${formatShortDate(shown.start)}`}
          </div>
          {tooltip ? (
            tooltip(shown, active ?? 0)
          ) : (
            <>
              <div>Chi tiêu: {money(shown.spend)}</div>
              <div>
                Doanh số: {money(shown.revenue)} · Ads / DT:{" "}
                {percent(shown.ads_ratio)}
              </div>
              <div>
                SĐT: {count(shown.phones)} · Mess: {count(shown.messages)}
              </div>
              <div>Chi phí / SĐT: {money(shown.cost_per_phone)}</div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
