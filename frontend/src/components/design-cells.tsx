"use client";

import { codeTitle, codeTooltip } from "@/components/design-codes-picker";
import { Select } from "@/components/pr";
import { Pill } from "@/components/states";
import {
  RowDeadlineCell,
  RowStatusCell,
  StatusBadge,
  boldNames,
} from "@/components/task-table";
import type { DesignCodeItem, TaskCell, TaskRow, UnitMember } from "@/lib/api";
import { formatShortDateTime } from "@/lib/datetime";
import { formatAgo, formatWhen } from "@/lib/labels";
import { stepColor } from "@/lib/status-colors";

/**
 * What the Design ban reads on a task row, drawn from the row's `design`
 * block: the script handed in to them ("Nội dung"), the codes to produce
 * ("Tên task") and what the designer handed in ("Thiết kế"). The task table's
 * Design view and the birthday board share these cells; the task page reuses
 * the code list. Nothing here decides anything - a missing value is a dash.
 *
 * On those two tables the status and the deadline are the Design step's own
 * (`DesignStatusCell`, `DesignDeadlineCell`), not the order's: what the ban
 * follows is where *its* step stands, so an order that moved on to Dựng reads
 * "Hoàn thành" there. Their filters are here too: the Design step's state
 * (`DesignStatusSelect`, from the page's `design_states`) and the ban's own
 * people (`designTeam`).
 */

/** The key of the Design step among a row's `cells`. */
export const DESIGN_STEP = "THIET_KE";

/** The row's Design step, when the order has one (and the API sent its cells). */
export function designStep(row: TaskRow): TaskCell | undefined {
  return (row.cells ?? []).find((cell) => cell.key === DESIGN_STEP);
}

/** What the Design tables' status column shows, and sorts by. */
export function designStatusLabel(row: TaskRow): string {
  return designStep(row)?.status_label ?? row.status_label;
}

/** The deadline the Design tables show, and sort by: the Design step's, else the row's. */
export function designDeadline(row: TaskRow): string | null | undefined {
  return designStep(row)?.deadline_at ?? row.deadline_at;
}

/** The Design ban's people on a unit roster: the designers and their Leader. */
export function designTeam(members: UnitMember[] | undefined): UnitMember[] {
  return (members ?? []).filter((member) => member.active && member.role === DESIGN_STEP);
}

/**
 * "Trạng thái · Đang giữ" as the Design ban reads it: the Design step's own
 * state, coloured like a step cell, with the designer when the label does not
 * already name them and how often the step was sent back. A row without a
 * Design step is drawn as everywhere else.
 */
export function DesignStatusCell({ row, dense = false }: { row: TaskRow; dense?: boolean }) {
  const step = designStep(row);
  if (!step) return <RowStatusCell row={row} dense={dense} />;
  const named = Boolean(step.person_name && step.status_label.includes(step.person_name));
  return (
    <td className="w-[15rem] min-w-[13rem] max-w-[17rem]" data-testid="design-status">
      {step.status_label === "—" ? (
        dash
      ) : (
        <StatusBadge color={stepColor(step.status, row.status, step.status_label)}>
          {boldNames(step.status_label, step.person_name)}
        </StatusBadge>
      )}
      {!dense && (step.since || step.revisions > 0) ? (
        <span className="block text-[11px] text-[var(--text-muted)]">
          {step.since ? `ở bước này ${formatAgo(step.since)}` : ""}
          {step.revisions > 0 ? `${step.since ? " · " : ""}sửa ${step.revisions}` : ""}
        </span>
      ) : null}
      {step.person_name && !named ? (
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
          <span className="text-[var(--text-muted)]">Người làm:</span>
          <span className="person">{step.person_name}</span>
        </span>
      ) : null}
    </td>
  );
}

/** "Deadline" as the Design ban reads it: the Design step's own, else the row's. */
export function DesignDeadlineCell({ row }: { row: TaskRow }) {
  const step = designStep(row);
  return (
    <RowDeadlineCell
      row={
        step?.deadline_at
          ? { ...row, deadline_at: step.deadline_at, deadline_status: step.deadline_status }
          : row
      }
    />
  );
}

/**
 * The Design ban's "Trạng thái" filter: where the Design step stands. The
 * choices are the server's (`design_states` on the board page); a value from
 * the URL that is not among them yet (the list has not arrived) is kept as a
 * choice, so the control never shows "Tất cả" over an active filter.
 */
export function DesignStatusSelect({
  value,
  states,
  onChange,
  className = "",
}: {
  value: string;
  states: Array<{ value: string; label: string }> | undefined;
  onChange: (value: string) => void;
  className?: string;
}) {
  const known = states ?? [];
  return (
    <Select
      aria-label="Trạng thái"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={className}
    >
      <option value="">Tất cả</option>
      {known.map((state) => (
        <option key={state.value} value={state.value}>
          {state.label}
        </option>
      ))}
      {value && !known.some((state) => state.value === value) ? (
        <option value={value}>{value}</option>
      ) : null}
    </Select>
  );
}

const dash = <span className="text-[var(--text-muted)]">–</span>;

/** Only an http(s) address becomes an anchor; anything else is shown as text. */
export function WebLink({ href, label }: { href: string; label: string }) {
  if (!/^https?:\/\//i.test(href.trim())) {
    return <span className="break-all">{`${label}: ${href}`}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-[var(--accent)] hover:underline"
    >
      {label} ↗
    </a>
  );
}

/** The picked codes, one per line: the name, its code and "× quantity". */
export function DesignCodeLines({
  items,
  showPoints = false,
}: {
  items: DesignCodeItem[];
  /** 0074: each line's points beside it. */
  showPoints?: boolean;
}) {
  return (
    <ul className="space-y-1">
      {items.map((item, index) => (
        <li
          key={`${item.code_id}-${item.form ?? ""}-${index}`}
          className="flex items-start gap-1.5 leading-snug"
          title={codeTooltip(item)}
        >
          <span className="min-w-0 flex-1">
            {item.free ? (
              <>
                <span className="rounded bg-[var(--surface-muted)] px-1 py-0.5 text-[11px] font-semibold">
                  Việc khác
                </span>{" "}
                {item.note}
              </>
            ) : (
              <>
                {codeTitle(item)}{" "}
                <span className="rounded bg-[var(--surface-muted)] px-1 py-0.5 font-mono text-[11px] font-semibold">
                  {item.code}
                </span>
                {item.form && item.form !== "NEW" ? (
                  <span className="ml-1 text-xs text-[var(--text-muted)]">
                    · {item.form_label ?? item.form}
                  </span>
                ) : null}
              </>
            )}
          </span>
          {item.free ? null : (
            <span className="shrink-0 font-semibold tabular-nums">× {item.quantity}</span>
          )}
          {showPoints ? (
            <span className="w-16 shrink-0 text-right font-bold tabular-nums">
              {item.points ?? 0}đ
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * "Nội dung": the link the Biên kịch step handed in and who was in charge of
 * it; with no script link, the order's own reference link.
 */
export function DesignContentCell({ row }: { row: TaskRow }) {
  const design = row.design;
  const script = design?.script_link ?? null;
  const reference = design?.reference_link ?? null;
  return (
    <td className="min-w-[11rem] max-w-[16rem] text-xs" data-testid="design-content">
      {script ? (
        <WebLink href={script} label="Kịch bản" />
      ) : reference ? (
        <WebLink href={reference} label="Link tham khảo" />
      ) : (
        dash
      )}
      {design?.script_by ? (
        <span className="mt-0.5 block text-[var(--text-muted)]">
          Biên kịch: <span className="person">{design.script_by}</span>
        </span>
      ) : null}
    </td>
  );
}

/** "Tên task": the codes to produce, and a mark while a change waits for the Leader. */
export function DesignCodesCell({ row }: { row: TaskRow }) {
  const codes = row.design?.codes ?? [];
  const proposed = row.design?.proposed_codes ?? null;
  return (
    <td className="min-w-[15rem] max-w-[24rem] text-xs" data-testid="design-codes">
      {codes.length > 0 ? <DesignCodeLines items={codes} /> : dash}
      {proposed ? (
        <span className="mt-1 block">
          <Pill tone="warn">Chờ duyệt đổi mã</Pill>
        </span>
      ) : null}
    </td>
  );
}

/** "Thiết kế": the designer, the design link handed in, and the day it was. */
export function DesignerCell({ row }: { row: TaskRow }) {
  const design = row.design;
  return (
    <td className="min-w-[9rem] max-w-[14rem] text-xs" data-testid="design-designer">
      {design?.designer ? (
        <span className="person block text-[13px] leading-snug">{design.designer}</span>
      ) : (
        <span className="text-[var(--text-muted)]">Chưa có người thiết kế</span>
      )}
      {design?.assignee_total && design.assignee_total > 1 ? (
        <span
          data-testid="design-submitted-count"
          className="mt-0.5 block text-[11px] text-[var(--text-muted)]"
        >
          Đã nộp {design.submitted_count ?? 0}/{design.assignee_total}
        </span>
      ) : null}
      {design?.design_link ? (
        <span className="mt-0.5 block">
          <WebLink href={design.design_link} label="Link thiết kế" />
        </span>
      ) : null}
      {design?.design_submitted_at ? (
        <span
          data-testid="design-submitted"
          className="date-chip date-chip-done mt-1"
          title={`Nộp link thiết kế ${formatWhen(design.design_submitted_at)}`}
        >
          Nộp {formatShortDateTime(design.design_submitted_at)}
        </span>
      ) : null}
    </td>
  );
}
