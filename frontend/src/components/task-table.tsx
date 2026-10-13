"use client";

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
 */

/** Rows per page on every task table. */
export const PAGE_SIZE = 10;

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
 */
export function Pager({
  page,
  total,
  shown,
  onPage,
  noun = "task",
}: {
  page: number;
  total: number;
  shown: number;
  onPage: (page: number) => void;
  /** What a row is called in the range line: "task", "order". */
  noun?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = (page - 1) * PAGE_SIZE + shown;
  const numbers: Array<number | "gap"> = [];
  for (let n = 1; n <= pages; n += 1) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 2) numbers.push(n);
    else if (numbers[numbers.length - 1] !== "gap") numbers.push("gap");
  }
  const button =
    "inline-flex min-h-9 min-w-9 items-center justify-center rounded-lg border border-[var(--border)] px-2.5 text-xs disabled:opacity-40";
  return (
    <nav
      aria-label="Phân trang"
      className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--text-muted)]"
    >
      <span>
        {first}–{last} / {total} {noun} · {PAGE_SIZE} dòng mỗi trang
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
    </nav>
  );
}
