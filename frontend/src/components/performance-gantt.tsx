"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { TimelineBar, TimelineGroup } from "@/lib/api";
import { PersonLink } from "@/components/person-link";
import { PHASE_NAMES, QuotaBar, formatScore, reportStyle } from "@/components/performance";
import { Empty } from "@/components/states";
import { formatShortDate, formatShortDateTime } from "@/lib/datetime";

/**
 * "Tiến độ" (Gantt): one group per person - folded by default to one line
 * of small bars, unfolded to one row per step - on the month's days. A bar
 * runs from when they took the step to when it was done; an open step's
 * runs to today (solid) and on to its deadline (striped, "dự kiến"); a step
 * handed to them ahead runs from today to its deadline, dotted. Colours by
 * step (Biên kịch blue, Design yellow, Dựng green), red edge when late, ◆
 * at an open step's deadline. Days in Vietnam time; Sundays shaded; a line
 * marks now.
 */

const VN_OFFSET_MS = 7 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const WEEKDAYS = ["CN", "T2", "T3", "T4", "T5", "T6", "T7"];
/** Lanes of small bars on a folded person's line. */
const MAX_LANES = 4;

/** Fill and edge per step: a soft fill with the step's colour as the edge. */
const BAR_TONE: Record<string, { fill: string; edge: string }> = {
  BIEN_TAP: { fill: "var(--accent-soft)", edge: "var(--accent)" },
  THIET_KE: { fill: "var(--yellow-soft)", edge: "var(--yellow)" },
  DUNG: { fill: "var(--good-soft)", edge: "var(--good)" },
  // 0085: what is earned outside the steps, a mark on the day it counted.
  PUBLISH: { fill: "var(--magenta-soft)", edge: "var(--magenta)" },
  SEEDING: { fill: "var(--surface-muted)", edge: "var(--brand-sky)" },
  OTHER: { fill: "var(--surface-muted)", edge: "var(--text-muted)" },
};

/** A bar (or its label) opens its task when it has one; seeding has none. */
function BarBox({
  href,
  children,
  ...rest
}: {
  href?: string | null;
  children: React.ReactNode;
  title?: string;
  "aria-label"?: string;
  className: string;
  style?: React.CSSProperties;
}) {
  return href ? (
    <Link href={href} {...rest}>
      {children}
    </Link>
  ) : (
    <span {...rest}>{children}</span>
  );
}
const toneOf = (phase: string) => BAR_TONE[phase] ?? BAR_TONE.THIET_KE;
const stripes = (color: string) =>
  `repeating-linear-gradient(135deg, ${color} 0 5px, transparent 5px 9px)`;

const STATUS_LABEL: Record<string, string> = {
  EARNED: "Đã xong",
  PENDING_REVIEW: "Chờ duyệt mã",
  IN_PROGRESS: "Đang làm",
  UPCOMING: "Chưa tới lượt",
};

/** The month's bounds as instants (Vietnam midnight to midnight). */
export function monthFrame(month: string) {
  const [year, number] = month.split("-").map(Number);
  const start = Date.UTC(year, number - 1, 1) - VN_OFFSET_MS;
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const end = start + days * DAY_MS;
  const dayList = Array.from({ length: days }, (_, index) => {
    const weekday = new Date(Date.UTC(year, number - 1, index + 1)).getUTCDay();
    return { day: index + 1, weekday, label: WEEKDAYS[weekday] };
  });
  return { start, end, days, dayList };
}

type Frame = ReturnType<typeof monthFrame>;

/**
 * Where a bar sits, as % of the month (`null` when outside it): `left` and
 * `width` of the whole span, `actual` = the share of it already behind us
 * (all of a done step, none of one ahead), `deadline` = the ◆ of an open
 * step inside the month.
 */
export function barGeometry(bar: TimelineBar, frame: Frame, now: number) {
  const begin = Date.parse(bar.start);
  const deadline = bar.deadline_at ? Date.parse(bar.deadline_at) : null;
  const actualEnd = bar.done_at
    ? Date.parse(bar.done_at)
    : bar.status === "UPCOMING"
      ? begin
      : Math.max(now, begin);
  const finish = bar.done_at ? actualEnd : Math.max(actualEnd, deadline ?? actualEnd);
  const span = frame.end - frame.start;
  const at = (moment: number) => (moment - frame.start) / span;
  const left = Math.max(0, at(begin));
  const right = Math.min(1, at(finish));
  if (right <= 0 || left >= 1 || finish < begin) return null;
  const width = Math.max(right - left, 0.008);
  const actual = Math.min(1, Math.max(0, (Math.min(1, at(actualEnd)) - left) / width));
  const mark = deadline !== null && !bar.done_at ? at(deadline) : null;
  return {
    left: left * 100,
    width: width * 100,
    actual: right - left > 0 ? actual : 1,
    deadline: mark !== null && mark >= 0 && mark <= 1 ? mark * 100 : null,
  };
}

/** Folded view: bars packed into as few lanes as fit without overlapping. */
export function packLanes<T extends { left: number; width: number }>(items: T[]): T[][] {
  const lanes: { end: number; items: T[] }[] = [];
  for (const item of [...items].sort((a, b) => a.left - b.left)) {
    const lane = lanes.find((candidate) => candidate.end <= item.left);
    if (lane) {
      lane.items.push(item);
      lane.end = item.left + item.width;
    } else {
      lanes.push({ end: item.left + item.width, items: [item] });
    }
  }
  return lanes.map((lane) => lane.items);
}

function barText(bar: TimelineBar): string {
  const codes = bar.codes.join(", ") || "chưa có mã";
  const due = bar.deadline_at ? `hạn ${formatShortDate(bar.deadline_at)}` : "chưa có hạn";
  const when =
    bar.status === "UPCOMING"
      ? `Sắp tới · ${due}`
      : bar.done_at
        ? `${formatShortDate(bar.start)} - ${formatShortDate(bar.done_at)}`
        : `${formatShortDate(bar.start)} - ${due}`;
  return `${when} · ${codes} · ${formatScore(bar.points)}đ`;
}

function barTitle(bar: TimelineBar): string {
  return [
    `${bar.order_code} · ${bar.order_title}`,
    `${bar.phase_label} · ${STATUS_LABEL[bar.status] ?? bar.status}${bar.late ? " · trễ hạn" : ""}`,
    `Mã task: ${bar.codes.join(", ") || "chưa có"}`,
    bar.status === "UPCOMING" ? null : `Nhận: ${formatShortDateTime(bar.start)}`,
    bar.done_at ? `Xong: ${formatShortDateTime(bar.done_at)}` : null,
    bar.deadline_at ? `Deadline: ${formatShortDateTime(bar.deadline_at)}` : null,
    `Điểm: ${formatScore(bar.points)}${bar.done_at ? "" : " (dự kiến)"}`,
  ]
    .filter(Boolean)
    .join("\n");
}

const LEFT = "w-64 shrink-0 sm:w-80";

export function Gantt({
  month,
  groups,
  now = Date.now(),
  quota,
}: {
  month: string;
  groups: TimelineGroup[];
  now?: number;
  /** One person's quota for the month ("định mức"): a thin bar per person. */
  quota?: { monthly: number; to_date: number } | null;
}) {
  const frame = useMemo(() => monthFrame(month), [month]);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const span = frame.end - frame.start;
  const todayLeft = now >= frame.start && now < frame.end ? ((now - frame.start) / span) * 100 : null;
  const sundays = frame.dayList.filter((day) => day.weekday === 0);
  const dayWidth = 100 / frame.days;

  /** The day grid behind every row: Sundays shaded, a line for now. */
  const grid = (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      {sundays.map((day) => (
        <span
          key={day.day}
          className="absolute inset-y-0 bg-[var(--surface-muted)]"
          style={{ left: `${(day.day - 1) * dayWidth}%`, width: `${dayWidth}%` }}
        />
      ))}
      {todayLeft !== null ? (
        <span className="absolute inset-y-0 w-px bg-[var(--bad)]" style={{ left: `${todayLeft}%` }} />
      ) : null}
    </div>
  );

  if (groups.length === 0) {
    return (
      <div className="panel p-4">
        <Empty message="Không có task nào trong tháng." />
      </div>
    );
  }
  return (
    <section className="panel overflow-hidden" aria-label="Biểu đồ tiến độ">
      <div className="overflow-x-auto">
        <div className="min-w-[72rem]">
          {/* The days. */}
          <div className="flex border-b border-[var(--border)] bg-[var(--surface)]">
            <div className={`${LEFT} sticky left-0 z-20 flex items-end border-r border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]`}>
              Nhân sự · task
            </div>
            <div className="relative flex flex-1">
              {frame.dayList.map((day) => {
                const isToday = todayLeft !== null && Math.floor((now - frame.start) / DAY_MS) === day.day - 1;
                return (
                  <div
                    key={day.day}
                    className={`flex-1 py-1.5 text-center text-[11px] leading-tight ${
                      isToday
                        ? "bg-[var(--bad)] font-semibold text-white"
                        : day.weekday === 0
                          ? "bg-[var(--surface-muted)] text-[var(--text-muted)]"
                          : "text-[var(--text-muted)]"
                    }`}
                  >
                    <span className="block font-semibold tabular-nums">{day.day}</span>
                    <span className="block">{day.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
          {groups.map((group) => {
            const isOpen = opened[group.user_id] ?? false;
            const placed = group.bars.flatMap((bar) => {
              const geometry = barGeometry(bar, frame, now);
              return geometry ? [{ bar, ...geometry }] : [];
            });
            const lanes = packLanes(placed);
            const hidden = lanes.slice(MAX_LANES).reduce((sum, lane) => sum + lane.length, 0);
            return (
              <div key={group.user_id} role="group" aria-label={group.name} className="border-b border-[var(--border)] last:border-b-0">
                {/* The person, with their steps in small when folded. */}
                <div className="flex bg-[var(--surface)]">
                  <div className={`${LEFT} sticky left-0 z-10 flex items-center gap-2 border-r border-[var(--border)] bg-[var(--surface)] px-2 py-2`}>
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Thu gọn" : "Mở"} task của ${group.name}`}
                      disabled={group.bars.length === 0}
                      onClick={() => setOpened((was) => ({ ...was, [group.user_id]: !isOpen }))}
                      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--text-muted)] hover:bg-[var(--surface-muted)] disabled:opacity-30"
                    >
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className={isOpen ? "" : "-rotate-90"}>
                        <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                    <div className="min-w-0">
                      <PersonLink userId={group.user_id} name={group.name} className="block truncate text-sm font-semibold" />
                      <p className="truncate text-xs text-[var(--text-muted)]">
                        {group.departments.join(", ") || "Chưa có phòng"} · <span className="font-semibold text-[var(--text)]">{formatScore(group.points)} điểm</span>
                        {group.pending_points ? ` · dự kiến ${formatScore(group.pending_points)}` : ""}
                      </p>
                      {quota?.monthly ? (
                        <div className="mt-1 max-w-48">
                          <QuotaBar points={group.points} quota={quota.monthly} toDate={quota.to_date} compact />
                        </div>
                      ) : null}
                      <p className="truncate text-xs text-[var(--text-muted)]">
                        {group.bars.length} task{group.upcoming ? ` · ${group.upcoming} sắp tới` : ""}
                        {hidden && !isOpen ? ` · mở để xem thêm ${hidden}` : ""}
                      </p>
                    </div>
                  </div>
                  <div className="relative min-h-14 flex-1">
                    {grid}
                    {group.bars.length === 0 ? (
                      <p className="relative px-3 py-4 text-xs text-[var(--text-muted)]">Không có task trong tháng.</p>
                    ) : isOpen ? null : (
                      <button
                        type="button"
                        onClick={() => setOpened((was) => ({ ...was, [group.user_id]: true }))}
                        aria-label={`Mở ${group.bars.length} task của ${group.name}`}
                        className="relative block h-full w-full py-2 text-left"
                      >
                        {lanes.slice(0, MAX_LANES).map((lane, index) => (
                          <span key={index} className="relative mb-1 block h-2 last:mb-0">
                            {lane.map((item) => {
                              const tone = toneOf(item.bar.phase);
                              return (
                                <span
                                  key={`${item.bar.node_id}-${item.bar.status}`}
                                  title={barTitle(item.bar)}
                                  className="absolute inset-y-0 overflow-hidden rounded-full"
                                  style={{
                                    left: `${item.left}%`,
                                    width: `${item.width}%`,
                                    background: item.bar.status === "UPCOMING" ? "transparent" : stripes(tone.edge),
                                    border: item.bar.status === "UPCOMING" ? `1px dotted ${tone.edge}` : undefined,
                                    outline: item.bar.late ? "1px solid var(--bad)" : undefined,
                                  }}
                                >
                                  <span className="absolute inset-y-0 left-0" style={{ width: `${item.actual * 100}%`, background: tone.edge }} />
                                </span>
                              );
                            })}
                          </span>
                        ))}
                      </button>
                    )}
                  </div>
                </div>
                {/* Their steps, one row each. */}
                {isOpen
                  ? group.bars.map((bar) => {
                      const geometry = barGeometry(bar, frame, now);
                      const tone = toneOf(bar.phase);
                      const style = reportStyle(bar.phase);
                      const edge = bar.late ? "var(--bad)" : tone.edge;
                      const line = bar.status === "UPCOMING" ? "dotted" : bar.done_at ? "solid" : "dashed";
                      return (
                        <div key={`${bar.node_id}-${bar.status}`} className="flex border-t border-[var(--border)]">
                          <div className={`${LEFT} sticky left-0 z-10 border-r border-[var(--border)] bg-[var(--surface)] py-2 pl-12 pr-3`}>
                            <p className="truncate text-sm" title={bar.order_title}>
                              {bar.order_code ? (
                                <BarBox
                                  href={bar.link ?? `/tasks/${bar.order_code}`}
                                  className="font-mono text-xs font-semibold text-[var(--accent)] hover:underline"
                                >
                                  {bar.order_code}
                                </BarBox>
                              ) : null}{" "}
                              {bar.order_title}
                            </p>
                            <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-[var(--text-muted)]">
                              <span className={`inline-flex items-center rounded-full border px-1.5 text-[11px] font-medium ${style.chip}`}>
                                {bar.phase_label}
                              </span>
                              <span>{bar.codes.join(", ") || "chưa có mã"}</span>
                              <span className="font-semibold text-[var(--text)]">{formatScore(bar.points)}đ</span>
                              <span>· {STATUS_LABEL[bar.status]}</span>
                              {bar.deadline_at && !bar.done_at ? <span>· hạn {formatShortDate(bar.deadline_at)}</span> : null}
                              {bar.late ? <span className="font-medium text-[var(--bad)]">· trễ hạn</span> : null}
                            </p>
                          </div>
                          <div className="relative min-h-12 flex-1">
                            {grid}
                            {geometry ? (
                              <>
                                <BarBox
                                  href={bar.link ?? (bar.order_code ? `/tasks/${bar.order_code}` : null)}
                                  title={barTitle(bar)}
                                  aria-label={barTitle(bar).replaceAll("\n", ". ")}
                                  className="absolute top-1/2 flex h-7 -translate-y-1/2 items-center overflow-hidden rounded-[4px] text-[11px] font-medium text-[var(--text)] hover:brightness-95 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                                  style={{
                                    left: `${geometry.left}%`,
                                    width: `${geometry.width}%`,
                                    background: stripes(tone.fill),
                                    border: `1px ${line} ${edge}`,
                                    borderLeft: `4px ${bar.status === "UPCOMING" ? "dotted" : "solid"} ${edge}`,
                                  }}
                                >
                                  <span
                                    aria-hidden="true"
                                    className="absolute inset-y-0 left-0"
                                    style={{ width: `${geometry.actual * 100}%`, background: tone.fill }}
                                  />
                                  <span className="relative truncate px-2">{barText(bar)}</span>
                                </BarBox>
                                {geometry.deadline !== null ? (
                                  <span
                                    aria-hidden="true"
                                    title="Deadline"
                                    className={`absolute top-1/2 -translate-x-1/2 -translate-y-1/2 text-xs ${bar.late ? "text-[var(--bad)]" : "text-[var(--text-muted)]"}`}
                                    style={{ left: `${geometry.deadline}%` }}
                                  >
                                    ◆
                                  </span>
                                ) : null}
                              </>
                            ) : (
                              <p className="relative px-3 py-3 text-xs text-[var(--text-muted)]">Ngoài tháng này</p>
                            )}
                          </div>
                        </div>
                      );
                    })
                  : null}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** What the colours and edges mean. */
export function GanttLegend() {
  return (
    <ul aria-label="Chú thích" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
      {Object.entries(BAR_TONE).map(([phase, tone]) => (
        <li key={phase} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-3 w-5 rounded-[3px]" style={{ background: tone.fill, borderLeft: `4px solid ${tone.edge}` }} />
          {PHASE_NAMES[phase]}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="h-3 w-5 rounded-[3px] border border-dashed border-[var(--text-muted)]" style={{ background: stripes("var(--surface-muted)") }} />
        Đang làm · sọc = dự kiến tới deadline
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="h-3 w-5 rounded-[3px] border border-dotted border-[var(--text-muted)]" />
        Chưa tới lượt
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true">◆</span>
        Deadline
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="h-3 w-5 rounded-[3px] border border-[var(--bad)]" />
        Trễ hạn
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="h-3 w-px bg-[var(--bad)]" />
        Hôm nay
      </li>
    </ul>
  );
}
