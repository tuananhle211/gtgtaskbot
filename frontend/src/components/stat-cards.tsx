"use client";

import { UnitTags } from "@/components/unit-panel";
import type { MemberStats } from "@/lib/api";

/**
 * One person's month in figures: the headline cards (performance score,
 * output, on-time, not-returned, effort, KPI items) and the ORD / PR
 * breakdowns. Drawn on "Hiệu suất của tôi" (`/account`) and on somebody's
 * profile page (`/people/<id>`, 2026-10-19) from the same `MemberStats`.
 */
const POINTS = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
/** `points` is numeric on the server and may arrive as a decimal string. */
export const formatPoints = (value: number | string) => POINTS.format(Number(value) || 0);
export const formatRate = (value: number | null) =>
  value === null || value === undefined ? "–" : `${Math.round(Number(value) * 100)}%`;

type Tone = "green" | "blue" | "violet" | "amber";
const TONE_ICON: Record<Tone, string> = {
  green: "bg-[var(--good-soft)] text-[var(--good)]",
  blue: "bg-[var(--accent-soft)] text-[var(--accent-strong)]",
  violet: "tone-violet",
  amber: "bg-[var(--warn-soft)] text-[var(--warn)]",
};

export function StatCards({ stats, showEffort }: { stats: MemberStats; showEffort: boolean }) {
  const asRate = (value: number | null | undefined) =>
    value === null || value === undefined ? null : Number(value);
  const score =
    stats.performance_score === null || stats.performance_score === undefined
      ? null
      : Number(stats.performance_score);
  const effort = asRate(stats.effort_rate);
  const lateCount = stats.late_count ?? 0;
  type StatCard = {
    label: string;
    value: string;
    hint: string;
    icon: GlyphName;
    tone: Tone;
    meter?: number | null;
    warn?: boolean;
  };
  /** Token figures: not drawn while ORD has effort switched off. */
  const effortCard: StatCard = {
    label: "Effort",
    value:
      stats.tokens_used === undefined
        ? "–"
        : `${formatPoints(stats.tokens_used)}/${formatPoints(stats.tokens_budget ?? 0)}`,
    hint:
      effort === null
        ? "Token đã dùng / quỹ các ngày làm việc"
        : `Token đã dùng / quỹ · ${formatRate(effort)}${effort > 1 ? " · vượt quỹ" : ""}`,
    icon: "bolt",
    tone: "blue",
    meter: effort === null ? undefined : Math.min(effort, 1),
    warn: effort !== null && effort > 1,
  };
  const headline: StatCard[] = [
    {
      label: "Điểm hiệu suất",
      value: score === null ? "–" : `${formatPoints(score)}/100`,
      hint: "Sản lượng, đúng hạn và không bị trả, theo trọng số của luồng",
      icon: "star",
      tone: "green",
      meter: score === null ? null : score / 100,
    },
    {
      label: "Sản lượng",
      value: String(stats.nodes_done),
      hint:
        stats.output_target !== null && stats.output_target !== undefined
          ? `Công đoạn xong lần đầu trong tháng · mốc ${formatPoints(stats.output_target)}`
          : "Công đoạn xong lần đầu trong tháng",
      icon: "check-circle",
      tone: "blue",
    },
    {
      label: "Đúng hạn",
      value: formatRate(asRate(stats.on_time_rate)),
      hint: `Công đoạn xong trước deadline · Số lần trễ hạn: ${lateCount}`,
      icon: "clock",
      tone: "amber",
      meter: asRate(stats.on_time_rate),
      warn: lateCount > 0,
    },
    {
      label: "Không bị trả",
      value: formatRate(asRate(stats.first_pass_rate)),
      hint: "Công đoạn được duyệt không bị trả",
      icon: "target",
      tone: "violet",
      meter: asRate(stats.first_pass_rate),
    },
    ...(showEffort ? [effortCard] : []),
    {
      label: "Mục KPI được tính",
      value: String(stats.work_items_counted),
      hint: "Mục đã tính KPI trong tháng, cả PR và ORD",
      icon: "list",
      tone: "amber",
    },
  ];
  const groups: Array<{
    unit: string;
    title: string;
    items: Array<{ label: string; value: string; hint?: string; warn?: boolean }>;
  }> = [
    {
      unit: "ADS",
      title: "Order ORD",
      items: [
        { label: "Đang làm", value: String(stats.nodes_in_progress), hint: "Đang giao cho bạn, mọi tháng" },
        { label: "Bị trả sửa", value: String(stats.revisions), warn: stats.revisions > 0 },
        { label: "Số lần trễ hạn", value: String(lateCount), warn: lateCount > 0 },
        { label: "Điểm loại video", value: formatPoints(stats.points), hint: "Điểm loại video của các công đoạn đã xong" },
        { label: "Order đã tạo / hoàn thành", value: `${stats.orders_created} / ${stats.orders_completed}` },
      ],
    },
    {
      unit: "PR",
      title: "Nội dung PR",
      items: [
        { label: "Nội dung phụ trách", value: String(stats.pr_contents_owned) },
        { label: "Sản xuất", value: String(stats.pr_productions_done) },
        { label: "Lượt duyệt", value: String(stats.pr_approvals) },
      ],
    },
  ];

  return (
    <div className="space-y-4">
      <ul aria-label="Chỉ số chính" className="grid grid-cols-1 gap-4 min-[420px]:grid-cols-2 lg:grid-cols-3">
        {headline.map((card) => (
          <li key={card.label} className="panel flex flex-col p-5">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-medium text-[var(--text-muted)]">{card.label}</p>
              <span className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONE_ICON[card.tone]}`}>
                <Glyph name={card.icon} size={18} />
              </span>
            </div>
            <p
              className={`mt-2 text-3xl font-semibold tabular-nums tracking-tight ${
                card.warn ? "text-[var(--warn)]" : ""
              }`}
            >
              {card.value}
            </p>
            {card.meter !== undefined ? (
              <div
                aria-hidden="true"
                className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--surface-muted)] ring-1 ring-inset ring-[var(--border)]"
              >
                <div
                  className="h-full rounded-full bg-[var(--good)] transition-[width]"
                  style={{ width: `${Math.round((card.meter ?? 0) * 100)}%` }}
                />
              </div>
            ) : null}
            <p className="mt-auto pt-2 text-xs leading-relaxed text-[var(--text-muted)]">{card.hint}</p>
          </li>
        ))}
      </ul>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.map((group) => (
          <section key={group.unit} aria-label={group.title} className="panel overflow-hidden">
            <header className="flex items-center gap-2 border-b border-[var(--border)] px-5 py-3">
              <UnitTags units={[group.unit]} />
              <h3 className="text-sm font-semibold">{group.title}</h3>
            </header>
            <ul
              aria-label={`Số liệu ${group.title}`}
              className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3"
            >
              {group.items.map((item) => (
                <li
                  key={item.label}
                  className="min-w-0 border-b border-r border-[var(--border)] px-4 py-4 sm:px-5"
                >
                  <p className="text-xs font-medium leading-snug text-[var(--text-muted)]">{item.label}</p>
                  <p
                    className={`mt-1.5 text-xl font-semibold tabular-nums tracking-tight ${
                      item.warn ? "text-[var(--warn)]" : ""
                    }`}
                  >
                    {item.value}
                  </p>
                  {item.hint ? <p className="mt-1 text-[11px] leading-snug text-[var(--text-muted)]">{item.hint}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}


// --- Icons ---------------------------------------------------------------------

export type GlyphName =
  | "camera"
  | "key"
  | "logout"
  | "lock"
  | "star"
  | "check-circle"
  | "target"
  | "list"
  | "clock"
  | "bolt"
  | "x";

/** Stroke icons for this page. Decorative: the text beside them carries the meaning. */
export function Glyph({ name, size = 16 }: { name: GlyphName; size?: number }) {
  const paths: Record<GlyphName, React.ReactNode> = {
    camera: (
      <>
        <path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2.5h6L16.5 7h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />
        <circle cx="12" cy="13" r="3.5" />
      </>
    ),
    key: (
      <>
        <circle cx="8" cy="15" r="4" />
        <path d="M10.85 12.15L19 4M16 7l2.5 2.5M14 9l1.5 1.5" />
      </>
    ),
    logout: (
      <>
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
        <path d="M16 17l5-5-5-5M21 12H9" />
      </>
    ),
    lock: (
      <>
        <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
        <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5M12 14.5v2" />
      </>
    ),
    star: <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />,
    "check-circle": (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12.5l2.7 2.7L16 9.8" />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.2" />
      </>
    ),
    list: (
      <>
        <path d="M9 6h11M9 12h11M9 18h11" />
        <path d="M4 6h.01M4 12h.01M4 18h.01" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    bolt: <path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" />,
    x: <path d="M6 6l12 12M18 6L6 18" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {paths[name]}
    </svg>
  );
}
