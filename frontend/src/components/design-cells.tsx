"use client";

import { Pill } from "@/components/states";
import type { DesignCodeItem, TaskRow } from "@/lib/api";

/**
 * What the Design ban reads on a task row, drawn from the row's `design`
 * block: the script handed in to them ("Nội dung"), the codes to produce
 * ("Tên task") and what the designer handed in ("Thiết kế"). The task table's
 * Design view and the birthday board share these cells; the task page reuses
 * the code list. Nothing here decides anything - a missing value is a dash.
 */

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
export function DesignCodeLines({ items }: { items: DesignCodeItem[] }) {
  return (
    <ul className="space-y-1">
      {items.map((item) => (
        <li key={item.code_id} className="flex items-start gap-1.5 leading-snug">
          <span className="min-w-0 flex-1">
            {item.name}{" "}
            <span className="rounded bg-[var(--surface-muted)] px-1 py-0.5 font-mono text-[11px] font-semibold">
              {item.code}
            </span>
          </span>
          <span className="shrink-0 font-semibold tabular-nums">× {item.quantity}</span>
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

/** "Thiết kế": the designer and the design link handed in. */
export function DesignerCell({ row }: { row: TaskRow }) {
  const design = row.design;
  return (
    <td className="min-w-[9rem] max-w-[14rem] text-xs" data-testid="design-designer">
      {design?.designer ? (
        <span className="person block text-[13px] leading-snug">{design.designer}</span>
      ) : (
        <span className="text-[var(--text-muted)]">Chưa có người thiết kế</span>
      )}
      {design?.design_link ? (
        <span className="mt-0.5 block">
          <WebLink href={design.design_link} label="Link thiết kế" />
        </span>
      ) : null}
    </td>
  );
}
