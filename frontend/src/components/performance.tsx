"use client";

import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type PerformanceFigures,
  type PerformanceLine,
  type PerformanceCodeRow,
  type PerformanceLineStatus,
  type PerformanceTrendPoint,
} from "@/lib/api";
import { phaseStyle } from "@/components/design-codes-picker";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatShortDateTime } from "@/lib/datetime";
import { monthLabel } from "@/lib/labels";

/**
 * The points reports' pieces ("Hiệu suất", docs/perf/PERFORMANCE_REPORTS_PLAN.md):
 * one person's month (`PerformancePanel`, on "Của tôi" and the profile page),
 * the figure tiles, the bar charts and the month box the team and company
 * screens share. Points only - no money, no percentages of a target.
 */

const NUMBER = new Intl.NumberFormat("vi-VN");
export const formatScore = (value: number) => NUMBER.format(Math.round(value || 0));
const formatPercent = (value: number | null) =>
  value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;

/**
 * The one series colour. Inline on purpose: the brand layer paints every
 * `bg-[var(--accent)]` with a gradient, and a bar's length must read flat.
 */
export const BAR_FILL = "var(--accent)";

export function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/** "T10" from "2026-10": the trend's axis labels. */
const shortMonth = (code: string) => `T${Number(code.split("-")[1] ?? "") || code}`;

const PHASES = ["BIEN_TAP", "THIET_KE", "DUNG"] as const;
export const PHASE_NAMES: Record<string, string> = {
  BIEN_TAP: "Biên kịch",
  THIET_KE: "Design",
  DUNG: "Dựng",
};

export function MonthField({ value, onChange }: { value: string; onChange: (month: string) => void }) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
      Tháng
      <input
        type="month"
        value={value}
        aria-label="Tháng"
        onChange={(event) => {
          if (event.target.value) onChange(event.target.value);
        }}
        className="min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
      />
    </label>
  );
}

/** The Excel button: a plain download link (the session cookie goes with it). */
export function ExportLink({ href }: { href: string }) {
  return (
    <a
      href={href}
      download
      className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm font-medium hover:bg-[var(--surface-muted)]"
    >
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M12 4v11M7 10l5 5 5-5M5 20h14" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      Xuất Excel
    </a>
  );
}

export type Tile = { label: string; value: string; hint?: string; warn?: boolean; accent?: boolean };

export function Tiles({ tiles, label }: { tiles: Tile[]; label: string }) {
  return (
    <ul aria-label={label} className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 lg:grid-cols-4">
      {tiles.map((tile) => (
        <li key={tile.label} className="panel flex flex-col p-4">
          <p className="text-sm font-medium text-[var(--text-muted)]">{tile.label}</p>
          <p
            className={`mt-1.5 font-semibold tabular-nums tracking-tight ${tile.accent ? "text-4xl" : "text-3xl"} ${
              tile.warn ? "text-[var(--warn)]" : ""
            }`}
          >
            {tile.value}
          </p>
          {tile.hint ? <p className="mt-auto pt-2 text-xs leading-relaxed text-[var(--text-muted)]">{tile.hint}</p> : null}
        </li>
      ))}
    </ul>
  );
}

/** The four headline figures of a person (or a team's totals). */
export function figureTiles(figures: PerformanceFigures): Tile[] {
  const delta = figures.points - figures.previous_points;
  const returns = figures.returns_fault + figures.returns_other + figures.returns_codes;
  return [
    {
      label: "Tổng điểm",
      value: formatScore(figures.points),
      accent: true,
      hint:
        figures.previous_points || figures.points
          ? `Tháng trước ${formatScore(figures.previous_points)} · ${delta >= 0 ? "+" : "−"}${formatScore(Math.abs(delta))}`
          : "Điểm của các bước đã xong trong tháng",
    },
    {
      label: "Task xong",
      value: String(figures.steps_done),
      hint: `Đúng hạn ${formatPercent(figures.on_time_rate)} · trễ ${figures.late_count}`,
      warn: figures.late_count > 0,
    },
    {
      label: "Đang làm (dự kiến)",
      value: formatScore(figures.pending_points),
      hint: `${figures.pending_count} bước chưa xong · không cộng vào tổng`,
    },
    {
      label: "Bị trả",
      value: String(returns),
      hint: `Làm lỗi ${figures.returns_fault} · Đổi yêu cầu ${figures.returns_other} · Mã chưa hợp lý ${figures.returns_codes}`,
      warn: figures.returns_fault > 0,
    },
  ];
}

/**
 * One series of columns (the weeks, the months): one hue, thin columns with
 * a rounded top on a shared baseline, the value over each column and a
 * tooltip on hover / focus.
 */
export function ColumnChart({
  title,
  data,
  unit = "điểm",
}: {
  title: string;
  data: Array<{ label: string; value: number; detail?: string }>;
  unit?: string;
}) {
  const max = Math.max(1, ...data.map((item) => item.value));
  return (
    <figure className="panel p-4">
      <figcaption className="text-sm font-semibold">{title}</figcaption>
      <div role="list" aria-label={title} className="mt-3 flex h-44 items-end gap-2 border-b border-[var(--border)] pb-px">
        {data.map((item) => (
          <div
            key={item.label}
            role="listitem"
            tabIndex={0}
            aria-label={`${item.label}: ${formatScore(item.value)} ${unit}`}
            className="group relative flex h-full min-w-0 flex-1 flex-col items-center justify-end focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <span className="mb-1 text-[11px] font-medium tabular-nums text-[var(--text-muted)]">
              {item.value ? formatScore(item.value) : ""}
            </span>
            <span
              aria-hidden="true"
              className="w-full max-w-10 rounded-t-[4px] transition-opacity group-hover:opacity-80"
              style={{ height: `${Math.max(item.value ? 2 : 0, (item.value / max) * 85)}%`, background: BAR_FILL }}
            />
            <span
              role="tooltip"
              className="pointer-events-none absolute bottom-full z-10 mb-1 hidden whitespace-nowrap rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs text-[var(--text)] shadow-[var(--shadow-pop)] group-hover:block group-focus-visible:block"
            >
              <span className="font-semibold">{item.label}</span> · {formatScore(item.value)} {unit}
              {item.detail ? <span className="block text-[var(--text-muted)]">{item.detail}</span> : null}
            </span>
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="mt-1.5 flex gap-2">
        {data.map((item) => (
          <span key={item.label} className="min-w-0 flex-1 truncate text-center text-[11px] text-[var(--text-muted)]">
            {item.label}
          </span>
        ))}
      </div>
    </figure>
  );
}

export function trendColumns(trend: PerformanceTrendPoint[]) {
  return trend.map((point) => ({
    label: shortMonth(point.month),
    value: point.points,
    detail: monthLabel(point.month),
  }));
}

/**
 * Horizontal bars, one series (departments, people): the name, a bar on a
 * shared scale, the value in ink beside it.
 */
export function BarList({
  title,
  rows,
  unit = "điểm",
}: {
  title: string;
  rows: Array<{ key: string; label: React.ReactNode; name: string; value: number; detail?: string }>;
  unit?: string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return (
    <figure className="panel p-4">
      <figcaption className="text-sm font-semibold">{title}</figcaption>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--text-muted)]">Chưa có điểm trong tháng.</p>
      ) : (
        <ul aria-label={title} className="mt-3 space-y-2.5">
          {rows.map((row) => (
            <li
              key={row.key}
              title={`${row.name}: ${formatScore(row.value)} ${unit}${row.detail ? ` · ${row.detail}` : ""}`}
              className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-sm"
            >
              <span className="truncate">{row.label}</span>
              <span aria-hidden="true" className="h-2.5 overflow-hidden rounded-r-[4px] bg-[var(--surface-muted)]">
                <span
                  className="block h-full rounded-r-[4px]"
                  style={{ width: `${(row.value / max) * 100}%`, background: BAR_FILL }}
                />
              </span>
              <span className="tabular-nums font-semibold">{formatScore(row.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

/** Points by phase: one stacked bar (the phases' own colours) and its legend. */
export function PhaseSplit({ byPhase }: { byPhase: Record<string, number> }) {
  const total = PHASES.reduce((sum, phase) => sum + (byPhase[phase] ?? 0), 0);
  return (
    <figure className="panel p-4">
      <figcaption className="text-sm font-semibold">Điểm theo bước</figcaption>
      {total === 0 ? (
        <p className="mt-3 text-sm text-[var(--text-muted)]">Chưa có điểm trong tháng.</p>
      ) : (
        <>
          <div aria-hidden="true" className="mt-4 flex h-3 gap-[2px] overflow-hidden rounded-[4px]">
            {PHASES.filter((phase) => byPhase[phase]).map((phase) => (
              <span
                key={phase}
                className={`h-full ${phaseStyle(phase).bar}`}
                style={{ width: `${((byPhase[phase] ?? 0) / total) * 100}%` }}
              />
            ))}
          </div>
          <ul aria-label="Điểm theo bước" className="mt-4 space-y-2">
            {PHASES.map((phase) => (
              <li key={phase} className="flex items-center justify-between gap-3 text-sm">
                <span className="inline-flex items-center gap-2">
                  <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-sm ${phaseStyle(phase).bar}`} />
                  {PHASE_NAMES[phase]}
                </span>
                <span className="tabular-nums">
                  <span className="font-semibold">{formatScore(byPhase[phase] ?? 0)}</span>
                  <span className="ml-2 text-xs text-[var(--text-muted)]">
                    {Math.round(((byPhase[phase] ?? 0) / total) * 100)}%
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </figure>
  );
}

/**
 * How many of each task code a department (or a head's people) did in the
 * month: grouped by step, each group with its subtotal, then the total.
 */
export function CodeTable({
  rows,
  title = "Số lượng theo mã task",
  showPeople = true,
}: {
  rows: PerformanceCodeRow[];
  title?: string;
  /** "Số người": pointless on one person's page. */
  showPeople?: boolean;
}) {
  const groups = PHASES.map((phase) => ({ phase, rows: rows.filter((row) => row.phase === phase) })).filter(
    (group) => group.rows.length > 0,
  );
  const quantity = rows.reduce((sum, row) => sum + row.quantity, 0);
  const points = rows.reduce((sum, row) => sum + row.points, 0);
  return (
    <section className="panel overflow-hidden" aria-labelledby="performance-codes">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
        <h3 id="performance-codes" className="text-base font-semibold">
          {title}
        </h3>
        <p className="text-xs text-[var(--text-muted)]">Các bước đã xong trong tháng · điểm sau hệ số</p>
      </header>
      {rows.length === 0 ? (
        <div className="p-4">
          <Empty message="Chưa có mã task nào được tính trong tháng." />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[42rem] text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                <th className="px-4 py-2 font-medium">Mã task</th>
                <th className="px-4 py-2 font-medium">Tên / ví dụ</th>
                <th className="px-4 py-2 font-medium">Hình thức</th>
                <th className="px-4 py-2 text-right font-medium">Số lượng</th>
                {showPeople ? <th className="px-4 py-2 text-right font-medium">Số người</th> : null}
                <th className="px-4 py-2 text-right font-medium">Điểm</th>
              </tr>
            </thead>
            {groups.map((group) => {
              const style = phaseStyle(group.phase);
              return (
                <tbody key={group.phase} className="divide-y divide-[var(--border)] border-b border-[var(--border)]">
                  <tr className="bg-[var(--surface-muted)]">
                    <th scope="rowgroup" colSpan={3} className="px-4 py-2 text-left text-xs font-semibold">
                      <span className="inline-flex items-center gap-2">
                        <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-sm ${style.bar}`} />
                        {PHASE_NAMES[group.phase]}
                      </span>
                    </th>
                    <td className="px-4 py-2 text-right text-xs font-semibold tabular-nums">
                      {formatScore(group.rows.reduce((sum, row) => sum + row.quantity, 0))}
                    </td>
                    {showPeople ? <td /> : null}
                    <td className="px-4 py-2 text-right text-xs font-semibold tabular-nums">
                      {formatScore(group.rows.reduce((sum, row) => sum + row.points, 0))}
                    </td>
                  </tr>
                  {group.rows.map((row) => (
                    <tr key={`${row.code}-${row.form ?? ""}`}>
                      <td className="px-4 py-2.5">
                        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${style.chip}`}>
                          {row.code}
                        </span>
                      </td>
                      <td className="max-w-[20rem] truncate px-4 py-2.5 text-xs text-[var(--text-muted)]" title={row.name}>
                        {row.name || "–"}
                      </td>
                      <td className="px-4 py-2.5 text-xs">{row.form_label ?? (row.free ? "–" : "Làm mới")}</td>
                      <td className="px-4 py-2.5 text-right font-semibold tabular-nums">{formatScore(row.quantity)}</td>
                      {showPeople ? (
                        <td className="px-4 py-2.5 text-right tabular-nums text-[var(--text-muted)]">{row.people ?? "–"}</td>
                      ) : null}
                      <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(row.points)}</td>
                    </tr>
                  ))}
                </tbody>
              );
            })}
            <tfoot>
              <tr className="font-semibold">
                <td colSpan={3} className="px-4 py-2.5">
                  Tổng
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(quantity)}</td>
                {showPeople ? <td /> : null}
                <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(points)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </section>
  );
}

const STATUS_LABEL: Record<PerformanceLineStatus, string> = {
  EARNED: "Đã tính",
  PENDING_REVIEW: "Chờ duyệt mã",
  IN_PROGRESS: "Đang làm",
};

type LineFilter = "EARNED" | "PENDING" | "ALL";

/** Every step of the month (and what is still under way), with its codes. */
export function PerformanceLines({ lines }: { lines: PerformanceLine[] }) {
  const [filter, setFilter] = useState<LineFilter>("EARNED");
  const shown = lines.filter((line) =>
    filter === "ALL" ? true : filter === "EARNED" ? line.status === "EARNED" : line.status !== "EARNED",
  );
  const counts = {
    EARNED: lines.filter((line) => line.status === "EARNED").length,
    PENDING: lines.filter((line) => line.status !== "EARNED").length,
    ALL: lines.length,
  };
  const filters: Array<{ key: LineFilter; label: string }> = [
    { key: "EARNED", label: "Đã tính" },
    { key: "PENDING", label: "Đang chờ" },
    { key: "ALL", label: "Tất cả" },
  ];
  return (
    <section className="panel overflow-hidden" aria-labelledby="performance-lines">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
        <h3 id="performance-lines" className="text-base font-semibold">
          Chi tiết công việc
        </h3>
        <div role="radiogroup" aria-label="Lọc công việc" className="flex gap-1">
          {filters.map((item) => (
            <button
              key={item.key}
              type="button"
              role="radio"
              aria-checked={filter === item.key}
              onClick={() => setFilter(item.key)}
              className={`min-h-9 rounded-full border px-3 text-xs font-medium ${
                filter === item.key
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]"
              }`}
            >
              {item.label} · {counts[item.key]}
            </button>
          ))}
        </div>
      </header>
      {shown.length === 0 ? (
        <div className="p-4">
          <Empty message={filter === "EARNED" ? "Chưa có bước nào xong trong tháng." : "Không có việc nào đang chờ."} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                <th className="px-4 py-2 font-medium">Ngày</th>
                <th className="px-4 py-2 font-medium">Order</th>
                <th className="px-4 py-2 font-medium">Bước</th>
                <th className="px-4 py-2 font-medium">Mã task</th>
                <th className="px-4 py-2 text-right font-medium">Điểm</th>
                <th className="px-4 py-2 font-medium">Trạng thái</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {shown.map((line) => {
                const style = phaseStyle(line.phase);
                return (
                  <tr key={`${line.node_id}-${line.status}`} className="align-top">
                    <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-[var(--text-muted)]">
                      {line.at ? formatShortDateTime(line.at) : "–"}
                    </td>
                    <td className="px-4 py-2.5">
                      <Link href={`/tasks/${line.order_code}`} className="font-mono text-xs font-semibold text-[var(--accent)] hover:underline">
                        {line.order_code}
                      </Link>
                      <p className="max-w-[16rem] truncate text-xs text-[var(--text-muted)]" title={line.order_title}>
                        {line.order_title}
                      </p>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${style.chip}`}>
                        {line.phase_label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <ul className="space-y-1">
                        {line.items.map((item, index) => (
                          <li key={`${item.code_id}-${index}`} className="text-xs">
                            <span className={`font-semibold ${style.ink}`}>{item.free ? "Việc khác" : item.code}</span>
                            {item.free ? null : item.form && item.form !== "NEW" ? (
                              <span className="text-[var(--text-muted)]"> · {item.form_label}</span>
                            ) : null}
                            {item.quantity > 1 ? <span className="text-[var(--text-muted)]"> × {item.quantity}</span> : null}
                            {item.note ? <span className="text-[var(--text-muted)]"> · {item.note}</span> : null}
                            <span className="ml-1 tabular-nums text-[var(--text-muted)]">({formatScore(item.points ?? 0)})</span>
                          </li>
                        ))}
                        {line.items.length === 0 ? <li className="text-xs text-[var(--text-muted)]">Chưa có mã</li> : null}
                      </ul>
                    </td>
                    <td className="px-4 py-2.5 text-right font-semibold tabular-nums">{formatScore(line.points)}</td>
                    <td className="px-4 py-2.5">
                      <Pill tone={line.status === "EARNED" ? "good" : line.status === "PENDING_REVIEW" ? "warn" : "neutral"}>
                        {STATUS_LABEL[line.status]}
                      </Pill>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * One person's month: the tiles, the weeks, the phases and the lines. A
 * refusal (403: somebody who does not report to the viewer) is a sentence.
 */
export function PerformancePanel({ userId, month }: { userId: string; month: string }) {
  const report = useQuery({
    queryKey: ["performance", "person", userId, month],
    queryFn: () => api.performancePerson(userId, month),
    placeholderData: keepPreviousData,
    retry: false,
  });
  if (report.isPending) return <Loading label="Đang tải điểm…" />;
  if (report.isError) {
    if (report.error instanceof ApiError && report.error.status === 403) {
      return (
        <p role="note" className="panel p-4 text-sm text-[var(--text-muted)]">
          Điểm hiệu suất chỉ hiện với chính người đó và Trưởng phòng họ báo cáo trực tiếp.
        </p>
      );
    }
    return <ErrorBox error={report.error} onRetry={() => report.refetch()} />;
  }
  const data = report.data;
  const weeks = ["1-7", "8-14", "15-21", "22-28", "29+"];
  return (
    <div className="space-y-4">
      <Tiles label="Điểm trong tháng" tiles={figureTiles(data.figures)} />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <ColumnChart
          title="Điểm theo tuần"
          data={weeks.map((label, index) => ({ label: `Ngày ${label}`, value: data.by_week[index] ?? 0 }))}
        />
        <PhaseSplit byPhase={data.by_phase} />
      </div>
      <CodeTable rows={data.codes ?? []} showPeople={false} />
      <PerformanceLines lines={data.lines} />
    </div>
  );
}
