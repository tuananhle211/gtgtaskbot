"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type AdsBudgetCell,
  type AdsBudgetLine,
  type AdsBudgetMonth,
  type AdsBudgetStatus,
  type AdsBudgetWeek,
} from "@/lib/api";
import { formatShortDate } from "@/lib/datetime";
import { compactMoney, money, percent } from "@/components/ads";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Ngân sách chạy ads theo tuần" (0082): each marketer's four weekly budgets
 * (days 1-7, 8-14, 15-21, the 22nd to the month's end, set by the Trưởng
 * phòng on /ads/budget) against what they spent that week on every platform.
 * A cell turns red when the spend went over ("Vượt ngưỡng"), amber when a
 * finished week stayed under 90% ("Chưa đến ngưỡng"), green when it reached
 * it; the running week stays blue until it goes over.
 */

/** Status -> the `.st-<colour>` pill, the cell's tint and the bar's fill. */
const TONE: Record<
  AdsBudgetStatus,
  { pill: string; cell: string; bar: string }
> = {
  OVER: { pill: "st-red", cell: "bg-[var(--bad-soft)]", bar: "var(--bad)" },
  UNDER: {
    pill: "st-amber",
    cell: "bg-[var(--warn-soft)]",
    bar: "var(--warn)",
  },
  REACHED: {
    pill: "st-green",
    cell: "bg-[var(--good-soft)]",
    bar: "var(--good)",
  },
  RUNNING: { pill: "st-blue", cell: "", bar: "var(--accent)" },
  UPCOMING: { pill: "st-slate", cell: "", bar: "var(--border)" },
  NO_BUDGET: { pill: "st-slate", cell: "", bar: "var(--border)" },
};

export function budgetTone(status: AdsBudgetStatus) {
  return TONE[status];
}

/** "01/10 – 07/10" */
export function weekDays(week: AdsBudgetWeek): string {
  return `${formatShortDate(week.start)} – ${formatShortDate(week.end)}`;
}

function Meter({ rate, fill }: { rate: number | null; fill: string }) {
  const width = rate === null ? 0 : Math.min(rate, 1) * 100;
  return (
    <div
      aria-hidden
      className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-muted)]"
    >
      <div
        className="h-full rounded-full"
        style={{ width: `${width}%`, background: fill }}
      />
    </div>
  );
}

function CellView({
  cell,
  week,
  name,
}: {
  cell: AdsBudgetCell;
  week: AdsBudgetWeek;
  name: string;
}) {
  const tone = TONE[cell.status];
  const detail =
    cell.budget === null
      ? `${name} · Tuần ${week.index}: chi ${money(cell.spend)}, chưa đặt ngân sách`
      : `${name} · Tuần ${week.index}: chi ${money(cell.spend)} / ngân sách ${money(cell.budget)} (${percent(cell.rate)}) · ${cell.status_label}`;
  return (
    <td
      className={`align-top ${tone.cell}`}
      title={detail}
      aria-label={detail}
      data-status={cell.status}
    >
      <div className="min-w-[132px] space-y-1">
        <div className="flex items-baseline justify-between gap-2 tabular-nums">
          <span className="font-semibold">{compactMoney(cell.spend)}</span>
          <span className="text-xs text-[var(--text-muted)]">
            / {cell.budget === null ? "—" : compactMoney(cell.budget)}
          </span>
        </div>
        {cell.budget !== null ? (
          <Meter rate={cell.rate} fill={tone.bar} />
        ) : null}
        <div className="flex items-center justify-between gap-2">
          {cell.status === "NO_BUDGET" ? (
            <span className="text-xs text-[var(--text-muted)]">
              {cell.status_label}
            </span>
          ) : (
            <span className={`st ${tone.pill}`}>{cell.status_label}</span>
          )}
          {cell.rate !== null ? (
            <span className="text-xs tabular-nums text-[var(--text-muted)]">
              {percent(cell.rate)}
            </span>
          ) : null}
        </div>
      </div>
    </td>
  );
}

function MonthCell({ line }: { line: AdsBudgetLine }) {
  const over = line.budget !== null && line.spend > line.budget;
  const rate = line.budget ? line.spend / line.budget : null;
  return (
    <td
      className={`align-top ${over ? "bg-[var(--bad-soft)]" : ""}`}
      data-status={over ? "OVER" : undefined}
    >
      <div className="min-w-[120px] space-y-1 tabular-nums">
        <div className="font-semibold">{compactMoney(line.spend)}</div>
        <div className="text-xs text-[var(--text-muted)]">
          / {line.budget === null ? "chưa đặt" : compactMoney(line.budget)}
          {rate !== null ? ` · ${percent(rate)}` : ""}
        </div>
      </div>
    </td>
  );
}

function Legend() {
  const items: Array<[AdsBudgetStatus, string]> = [
    ["OVER", "Vượt ngưỡng: chi quá ngân sách tuần"],
    ["REACHED", "Đạt ngưỡng: tuần đã qua, chi từ 90%"],
    ["UNDER", "Chưa đến ngưỡng: tuần đã qua, chi dưới 90%"],
    ["RUNNING", "Đang chạy: tuần hiện tại, chưa vượt"],
  ];
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
      {items.map(([status, label]) => (
        <li key={status} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: TONE[status].bar }}
          />
          {label}
        </li>
      ))}
    </ul>
  );
}

export function BudgetTable({
  data,
  onPick,
}: {
  data: AdsBudgetMonth;
  onPick?: (userId: string) => void;
}) {
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense w-full min-w-[820px]">
        <thead>
          <tr>
            <th className="sticky left-0 z-[1] bg-[var(--surface-muted)] text-left">
              Marketing
            </th>
            {data.weeks.map((week) => (
              <th key={week.index} className="text-left">
                <div>Tuần {week.index}</div>
                <div className="font-normal normal-case tracking-normal">
                  {weekDays(week)}
                </div>
              </th>
            ))}
            <th className="text-left">Cả tháng</th>
          </tr>
        </thead>
        <tbody>
          {data.lines.map((line) => (
            <tr key={line.user_id ?? "team"} data-testid={`budget-${line.user_id}`}>
              <th
                scope="row"
                className="sticky left-0 z-[1] bg-[var(--surface)] text-left align-top font-medium"
              >
                {onPick && line.user_id ? (
                  <button
                    type="button"
                    onClick={() => onPick(line.user_id as string)}
                    className="text-left hover:underline"
                  >
                    {line.name}
                  </button>
                ) : (
                  line.name
                )}
                {line.member_code ? (
                  <div className="text-xs font-normal text-[var(--text-muted)]">
                    {line.member_code}
                  </div>
                ) : null}
              </th>
              {line.cells.map((cell, index) => (
                <CellView
                  key={cell.week}
                  cell={cell}
                  week={data.weeks[index]}
                  name={line.name}
                />
              ))}
              <MonthCell line={line} />
            </tr>
          ))}
        </tbody>
        {data.total ? (
          <tfoot>
            <tr data-testid="budget-total" className="font-semibold">
              <th
                scope="row"
                className="sticky left-0 z-[1] bg-[var(--surface)] text-left align-top"
              >
                {data.total.name}
              </th>
              {data.total.cells.map((cell, index) => (
                <CellView
                  key={cell.week}
                  cell={cell}
                  week={data.weeks[index]}
                  name={data.total?.name ?? ""}
                />
              ))}
              <MonthCell line={data.total} />
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

/** One person's four weeks as cards (their own dashboard). */
function BudgetCards({ data }: { data: AdsBudgetMonth }) {
  const line = data.lines[0];
  if (!line) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {line.cells.map((cell, index) => {
        const week = data.weeks[index];
        const tone = TONE[cell.status];
        return (
          <article
            key={cell.week}
            data-status={cell.status}
            aria-label={`Tuần ${week.index}`}
            className={`space-y-2 rounded-xl border border-[var(--border)] p-4 ${tone.cell || "bg-[var(--surface)]"}`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold">Tuần {week.index}</h3>
              <span className="text-xs text-[var(--text-muted)]">
                {weekDays(week)}
              </span>
            </div>
            <div className="text-2xl font-semibold tabular-nums">
              {money(cell.spend)}
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              Ngân sách{" "}
              {cell.budget === null ? "chưa đặt" : money(cell.budget)}
              {cell.rate !== null ? ` · đã chi ${percent(cell.rate)}` : ""}
            </div>
            {cell.budget !== null ? (
              <Meter rate={cell.rate} fill={tone.bar} />
            ) : null}
            <span className={`st ${tone.pill}`}>{cell.status_label}</span>
          </article>
        );
      })}
    </div>
  );
}

/**
 * The dashboards' block: the whole team (a table, a name opens that
 * marketer) or one person (`userId`, or a member's own: four cards).
 */
export function BudgetBoard({
  month,
  userId,
  onPick,
  title = "Ngân sách chạy ads theo tuần",
  editLink = true,
}: {
  /** `YYYY-MM`. */
  month: string;
  userId?: string;
  onPick?: (userId: string) => void;
  title?: string;
  /** "Đặt ngân sách tuần" for whoever may edit (not on /ads/budget itself). */
  editLink?: boolean;
}) {
  const budget = useQuery({
    queryKey: ["ads", "budget", month, userId ?? ""],
    queryFn: () => api.adsBudget(month, userId),
    retry: false,
  });
  const data = budget.data;
  const one = Boolean(userId) || (data !== undefined && data.total === null);
  const [year, monthNumber] = month.split("-");
  return (
    <section aria-label="Ngân sách tuần" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="brand-eyebrow">
          {title} · tháng {Number(monthNumber)}/{year}
        </h2>
        {editLink && data?.can_edit ? (
          <Link
            href={`/ads/budget?month=${month}`}
            className="text-sm font-medium text-[var(--accent)] hover:underline"
          >
            Đặt ngân sách tuần
          </Link>
        ) : null}
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Chi tiêu = mọi nền tảng (Facebook, Google, TikTok) trên các tài khoản
        quảng cáo người đó giữ.
      </p>
      <Legend />
      {budget.isPending ? <Loading label="Đang tải ngân sách…" /> : null}
      {budget.isError ? (
        <ErrorBox error={budget.error} onRetry={() => budget.refetch()} />
      ) : null}
      {data && data.lines.length === 0 ? (
        <p className="panel p-4 text-sm text-[var(--text-muted)]">
          Chưa có ai trong Phòng Ads.
        </p>
      ) : null}
      {data && data.lines.length > 0 ? (
        one ? (
          <BudgetCards data={data} />
        ) : (
          <BudgetTable data={data} onPick={onPick} />
        )
      ) : null}
    </section>
  );
}
