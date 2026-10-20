"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DeadlineBadge } from "@/components/deadline-badge";
import { Pill } from "@/components/states";
import type { TaskRow } from "@/lib/api";
import { formatAgo } from "@/lib/labels";
import { type StatusColor, rowStatusColor } from "@/lib/status-colors";

/**
 * The pieces every task table draws the same way: the row's status with who
 * holds it, its deadline, a status badge and the pager. `/tasks` and the
 * birthday board (`/tasks/birthday`) both use them, so a row reads - and is
 * coloured - identically on either.
 *
 * And what every task table *does* the same way: a click anywhere on a row
 * opens the task (`useRowNavigation`), the page size is a choice kept on the
 * URL (`?size=`), and a double click on a column header sorts the rows on
 * screen by that column (`?sort=`, `SortHeader`, `sortRows`).
 */

/** Rows per page on every task table, unless the URL says otherwise. */
export const PAGE_SIZE = 10;

/** The page sizes on offer (the API accepts a `limit` up to 200). */
export const PAGE_SIZES = [10, 20, 50, 100] as const;

/** `?size=` as a page size: one of `PAGE_SIZES`, else the default. */
export function pageSizeOf(raw: string | null | undefined): number {
  const asked = Number(raw);
  return (PAGE_SIZES as readonly number[]).includes(asked) ? asked : PAGE_SIZE;
}

// --- Opening a row ---------------------------------------------------------------

/**
 * Marks a cell whose clicks never open the task: the action cell, where a
 * press that lands between two buttons must not navigate.
 */
export const NO_ROW_NAV = { "data-no-row-nav": "" } as const;

const INTERACTIVE = "a, button, input, select, textarea, label, summary, [data-no-row-nav]";

/**
 * "Click anywhere on the row to open the task."
 *
 * Returns the props for a `<tr>`: a pointer cursor and a click handler that
 * goes to the row's `detail_path`. It is a convenience on top of the row's own
 * links (the code, the title), which stay - the row itself gets no role and no tab
 * stop, so nothing interactive is nested in anything interactive. A click is
 * left alone when:
 *
 * - it landed on, or inside, a link, a button, a field or the action cell: a
 *   Drive link opens the file, a button does its own thing;
 * - it came from a dialog portalled out of the row (React bubbles a portal's
 *   events to the component that rendered it, so the row "hears" a click on
 *   its own confirm dialog);
 * - a modifier key is held (the row's links are what opens a new tab), or the
 *   person has just selected some text in the row.
 */
export function useRowNavigation() {
  const router = useRouter();
  return (path: string) => ({
    className: "cursor-pointer",
    onClick: (event: React.MouseEvent<HTMLTableRowElement>) => {
      const target = event.target as Element | null;
      if (!target || !event.currentTarget.contains(target)) return;
      if (target.closest(INTERACTIVE)) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (typeof window !== "undefined" && window.getSelection()?.toString()) return;
      router.push(path);
    },
  });
}

// --- Sorting the rows on screen ---------------------------------------------------

/** The column the rows on screen are sorted by, and which way; null = the server's order. */
export type SortState = { key: string; desc: boolean } | null;

/** One value a row is sorted by: text (alphabetical), a number or a time, or nothing. */
export type SortValue = string | number | null | undefined;

/** What a column sorts by: one value, or several (the later ones break ties). */
export type SortValueOf<Row> = (row: Row) => SortValue | SortValue[];

/** `?sort=`: `deadline` (ascending) or `-deadline` (descending). */
export function parseSort(raw: string | null | undefined): SortState {
  if (!raw) return null;
  const desc = raw.startsWith("-");
  const key = desc ? raw.slice(1) : raw;
  return key ? { key, desc } : null;
}

/**
 * What a double click on a column's header leads to, as the `?sort=` value:
 * ascending, then descending, then back to the server's order ("").
 */
export function nextSort(current: SortState, key: string): string {
  if (!current || current.key !== key) return key;
  return current.desc ? "" : `-${key}`;
}

/** An ISO timestamp as a number to sort by; nothing when it is not one. */
export function sortTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

const isBlank = (value: SortValue) =>
  value === null || value === undefined || (typeof value === "string" && value.trim() === "");

/** Blank values last whichever way the column runs; text by Vietnamese collation. */
function compareValues(left: SortValue, right: SortValue, desc: boolean): number {
  const leftBlank = isBlank(left);
  const rightBlank = isBlank(right);
  if (leftBlank || rightBlank) return leftBlank === rightBlank ? 0 : leftBlank ? 1 : -1;
  const order =
    typeof left === "number" && typeof right === "number"
      ? left - right
      : String(left).localeCompare(String(right), "vi", { numeric: true });
  return desc ? -order : order;
}

/**
 * The rows of the page on screen, sorted by one column. Only these rows: the
 * server pages the list and has its own order, so this never reaches the other
 * pages. A sort naming a column the table does not have (another unit's step)
 * leaves the server's order. Stable: ties keep the order they arrived in.
 */
export function sortRows<Row>(
  rows: readonly Row[],
  sort: SortState,
  columns: Record<string, SortValueOf<Row>>,
): Row[] {
  const valueOf = sort ? columns[sort.key] : undefined;
  if (!sort || !valueOf) return [...rows];
  const list = (row: Row) => {
    const value = valueOf(row);
    return Array.isArray(value) ? value : [value];
  };
  return rows
    .map((row) => ({ row, values: list(row) }))
    .sort((a, b) => {
      for (let index = 0; index < Math.max(a.values.length, b.values.length); index += 1) {
        const order = compareValues(a.values[index], b.values[index], sort.desc);
        if (order !== 0) return order;
      }
      return 0;
    })
    .map((entry) => entry.row);
}

/** Whether `sort` names one of the table's columns (and so is really applied). */
export function sortApplies<Row>(
  sort: SortState,
  columns: Record<string, SortValueOf<Row>>,
): boolean {
  return Boolean(sort && columns[sort.key]);
}

/**
 * A column header that sorts on a double click: ascending, descending, off.
 * The arrow shows the direction; `aria-sort` says it to a screen reader.
 */
export function SortHeader({
  sortKey,
  sort,
  onSort,
  children,
}: {
  sortKey: string;
  sort: SortState;
  onSort: (key: string) => void;
  children: React.ReactNode;
}) {
  const active = sort?.key === sortKey;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.desc ? "descending" : "ascending") : undefined}
      title={
        active
          ? `Đang sắp xếp ${sort.desc ? "giảm dần" : "tăng dần"} · nhấp đúp để ${sort.desc ? "bỏ sắp xếp" : "đảo chiều"}`
          : "Nhấp đúp để sắp xếp"
      }
      onDoubleClick={() => onSort(sortKey)}
      className="cursor-pointer select-none"
    >
      {children}
      {active ? <span aria-hidden="true"> {sort.desc ? "▼" : "▲"}</span> : null}
    </th>
  );
}

export function StatusBadge({
  color,
  children,
}: {
  color: StatusColor;
  children: React.ReactNode;
}) {
  // One inner span: the badge is a flex box (dot + text), and without it each
  // text piece and <strong> would become its own flex column.
  return (
    <span className={`st st-${color}`}>
      <span>{children}</span>
    </span>
  );
}

/** The status text with the people's names in bold and everything else plain. */
export function boldNames(label: string, names: string | null): React.ReactNode {
  if (!names || !label.includes(names)) return label;
  const at = label.indexOf(names);
  return (
    <>
      {label.slice(0, at)}
      <strong className="font-bold">{names}</strong>
      {label.slice(at + names.length)}
    </>
  );
}

/** "Trạng thái · Đang giữ": the row's status, since when, and who holds it. */
export function RowStatusCell({ row, dense = false }: { row: TaskRow; dense?: boolean }) {
  return (
    <td className="w-[15rem] min-w-[13rem] max-w-[17rem]">
      <StatusBadge color={rowStatusColor(row.status, row.state, row.status_label)}>
        {boldNames(row.status_label, row.current_person_name)}
      </StatusBadge>
      {!dense && row.stage_since ? (
        <span className="block text-[11px] text-[var(--text-muted)]">
          ở bước này {formatAgo(row.stage_since)}
        </span>
      ) : null}
      {/* Skipped when the status already names the same people. */}
      {row.current_person_name && row.status_label.includes(row.current_person_name) ? null : (
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
          <span className="text-[var(--text-muted)]">Đang giữ:</span>
          {row.awaiting_assignment ? (
            <Pill tone="warn">Chờ giao</Pill>
          ) : (
            <span className="person">{row.current_person_name ?? "–"}</span>
          )}
        </span>
      )}
    </td>
  );
}

/** "Deadline": the current step's deadline and how often the wish was exceeded. */
export function RowDeadlineCell({ row }: { row: TaskRow }) {
  return (
    <td className="whitespace-nowrap text-xs" data-testid="row-deadline">
      {row.deadline_at ? (
        <span className="flex flex-col items-start gap-1">
          <DeadlineBadge at={row.deadline_at} status={row.deadline_status} />
          {row.deadline_status === "OVERDUE" ? (
            <Pill tone="critical">Quá hạn</Pill>
          ) : null}
        </span>
      ) : (
        <span className="text-[var(--text-muted)]">–</span>
      )}
      {(row.over_deadline_count ?? 0) > 0 ? (
        <span
          className="mt-0.5 block text-[11px] text-[var(--warn)]"
          title="Số lần vượt deadline mong muốn"
        >
          Vượt mong muốn {row.over_deadline_count} lần
        </span>
      ) : null}
    </td>
  );
}

/**
 * Page numbers under the table: first, previous, a window of numbers around
 * the current page with ellipses, next, last. The range shown ("21–40 / 1223")
 * says where the visible rows sit in the whole list.
 *
 * With `onPageSize` it also offers the page size ("Số dòng"), and with
 * `sorted` it says that the sort covers this page only - the server pages the
 * list in its own order, so a sorted page is not a sorted list.
 *
 * It is docked: it sticks to the bottom of the screen while its panel is in
 * view, and carries the table's sideways scrollbar with it. A wide table's own
 * scrollbar sits under its last row, so reaching the columns on the right of
 * the first rows meant scrolling all the way down first. The docked bar is a
 * stand-in: it mirrors the `.table-scroll-x` in the same panel (found in the
 * DOM, so the pages need no wiring), both scroll together, and the table's own
 * bar is hidden (`.is-docked`) while the stand-in is there.
 */
export function Pager({
  page,
  total,
  shown,
  onPage,
  noun = "task",
  pageSize = PAGE_SIZE,
  onPageSize,
  sorted = false,
}: {
  page: number;
  total: number;
  shown: number;
  onPage: (page: number) => void;
  /** What a row is called in the range line: "task", "order". */
  noun?: string;
  /** Rows per page (the request's `limit`). */
  pageSize?: number;
  /** Set to offer the page-size choice; the caller goes back to page 1. */
  onPageSize?: (size: number) => void;
  /** The rows on screen are sorted by a column (client-side, this page only). */
  sorted?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = (page - 1) * pageSize + shown;
  const numbers: Array<number | "gap"> = [];
  for (let n = 1; n <= pages; n += 1) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 2) numbers.push(n);
    else if (numbers[numbers.length - 1] !== "gap") numbers.push("gap");
  }
  const button =
    "inline-flex min-h-9 min-w-9 items-center justify-center rounded-lg border border-[var(--border)] px-2.5 text-xs disabled:opacity-40";
  const dockRef = useRef<HTMLElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const [span, setSpan] = useState<{ scroll: number; client: number } | null>(null);
  useEffect(() => {
    const bar = barRef.current;
    const area = dockRef.current?.parentElement?.querySelector<HTMLElement>(".table-scroll-x");
    if (!bar || !area) return;
    area.classList.add("is-docked");
    const measure = () => setSpan({ scroll: area.scrollWidth, client: area.clientWidth });
    // Setting the same value fires no scroll event, so the pair cannot loop.
    const fromArea = () => {
      if (bar.scrollLeft !== area.scrollLeft) bar.scrollLeft = area.scrollLeft;
    };
    const fromBar = () => {
      if (area.scrollLeft !== bar.scrollLeft) area.scrollLeft = bar.scrollLeft;
    };
    measure();
    area.addEventListener("scroll", fromArea, { passive: true });
    bar.addEventListener("scroll", fromBar, { passive: true });
    window.addEventListener("resize", measure);
    const watcher = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    watcher?.observe(area);
    if (area.firstElementChild) watcher?.observe(area.firstElementChild);
    return () => {
      area.classList.remove("is-docked");
      area.removeEventListener("scroll", fromArea);
      bar.removeEventListener("scroll", fromBar);
      window.removeEventListener("resize", measure);
      watcher?.disconnect();
    };
  }, []);
  const overflows = span !== null && span.scroll > span.client + 1;
  return (
    <nav ref={dockRef} aria-label="Phân trang" className="pager-dock text-xs text-[var(--text-muted)]">
      <div
        ref={barRef}
        aria-hidden="true"
        data-testid="pager-scrollbar"
        className={`table-dock-bar ${overflows ? "" : "hidden"}`}
      >
        <div className="h-px" style={{ width: span?.scroll ?? 0 }} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>
          {first}–{last} / {total} {noun} · {pageSize} dòng mỗi trang
        </span>
        {onPageSize ? (
          <label className="inline-flex items-center gap-1.5">
            Số dòng
            <select
              value={pageSize}
              onChange={(event) => onPageSize(Number(event.target.value))}
              className="min-h-9 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-xs text-[var(--text)]"
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {sorted ? (
          <span role="status" className="font-medium text-[var(--warn)]">
            Đang sắp xếp trong trang này
          </span>
        ) : null}
      </span>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(1)} aria-label="Trang đầu">
          «
        </button>
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Trước
        </button>
        {numbers.map((n, index) =>
          n === "gap" ? (
            <span key={`gap-${index}`} className="px-1">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              aria-current={n === page ? "page" : undefined}
              onClick={() => onPage(n)}
              className={`${button} ${
                n === page
                  ? "border-[var(--accent)] bg-[var(--accent)] font-semibold text-[var(--accent-text)]"
                  : "bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
              }`}
            >
              {n}
            </button>
          ),
        )}
        <button type="button" className={button} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Sau
        </button>
        <button type="button" className={button} disabled={page >= pages} onClick={() => onPage(pages)} aria-label="Trang cuối">
          »
        </button>
      </div>
      </div>
    </nav>
  );
}
