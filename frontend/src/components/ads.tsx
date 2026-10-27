"use client";

import { useState } from "react";
import type { AdsKpi, AdsSeriesPoint } from "@/lib/api";
import { formatShortDate } from "@/lib/datetime";

/**
 * Pieces shared by the Phòng Ads screens (0078): number formats, the KPI
 * card and the one-measure bar chart. One axis only: a second measure is
 * picked with the "Chỉ số" switch, never drawn on a second scale.
 */

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
  note?: string;
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
}: {
  points: AdsSeriesPoint[];
  measure: SeriesMeasure;
  label: string;
  /** Spend bars with the revenue as a line on the same money axis (one unit, one axis). */
  revenueLine?: boolean;
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
  const max = Math.max(...values, ...line.map((value) => value ?? 0), 0) || 1;
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
              aria-label={`${formatShortDate(point.start)}: ${formatMeasure(value, measure)}`}
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
                  {formatShortDate(point.start)}
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
          <div className="font-semibold">Từ {formatShortDate(shown.start)}</div>
          <div>Chi tiêu: {money(shown.spend)}</div>
          <div>
            Doanh số: {money(shown.revenue)} · Ads / DT:{" "}
            {percent(shown.ads_ratio)}
          </div>
          <div>
            SĐT: {count(shown.phones)} · Mess: {count(shown.messages)}
          </div>
          <div>Chi phí / SĐT: {money(shown.cost_per_phone)}</div>
        </div>
      ) : null}
    </div>
  );
}
