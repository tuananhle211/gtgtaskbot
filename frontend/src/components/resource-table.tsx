"use client";

import Link from "next/link";
import type { ResourceRow } from "@/lib/api";
import { Pill } from "@/components/states";
import { NO_ROW_NAV, useRowNavigation } from "@/components/task-table";
import { ResourceRowActions, ResourceStatusButton } from "@/components/resource-actions";
import {
  alertTone,
  employmentTone,
  formatResourceDay,
  rowTone,
  statusTone,
  isWebLink,
} from "@/lib/resources";

/**
 * The resources table, drawn the same way on every screen that lists them:
 * the list, "Tài nguyên của tôi", the look-up by employee, the warning list.
 *
 * A row with a red alert (a holder who left, was deactivated or changed
 * department; a loss) is painted red; one with an amber alert (an overdue
 * review, a status that needs attention) amber. A click anywhere on a row
 * opens the profile.
 */
export const RESOURCE_COLUMNS = [
  "Mã",
  "Loại",
  "Ngày bàn giao",
  "ID",
  "Tên",
  "Ban",
  "Người được bàn giao",
  "Trạng thái",
  "Trạng thái nhân viên",
  "Bàn giao",
] as const;

export function ResourceTable({
  rows,
  emptyMessage = "Không có tài nguyên nào trong bộ lọc này.",
  compact = false,
  actions = false,
}: {
  rows: ResourceRow[];
  emptyMessage?: string;
  /** Without the "Ban" column (the employee look-up already says the department). */
  compact?: boolean;
  /**
   * A last column with each row's quick actions ("⋯": Sửa, Cập nhật trạng
   * thái, Đã rà soát, Đổi người được bàn giao, Xoá - whatever the row's own
   * flags allow, 2026-10-21).
   */
  actions?: boolean;
}) {
  const rowProps = useRowNavigation();
  return (
    <div className="table-scroll-x">
      <table className="table-dense min-w-[1100px]" data-testid="resource-table">
        <thead>
          <tr>
            {actions ? (
              <th scope="col" className="w-10">
                <span className="sr-only">Thao tác</span>
              </th>
            ) : null}
            {RESOURCE_COLUMNS.filter((column) => !compact || column !== "Ban").map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={(compact ? 9 : 10) + (actions ? 1 : 0)} className="text-[var(--text-muted)]">
                {emptyMessage}
              </td>
            </tr>
          ) : null}
          {rows.map((row) => {
            const tone = rowTone(row);
            return (
              <tr
                key={row.id}
                {...rowProps(row.detail_path)}
                data-tone={tone ?? undefined}
                className={`cursor-pointer ${
                  tone === "bad"
                    ? "bg-red-500/10 hover:bg-red-500/15"
                    : tone === "warn"
                      ? "bg-amber-500/10"
                      : ""
                }`}
              >
                {actions ? (
                  // First, so it is in view without scrolling; a press here
                  // opens the menu, never the profile.
                  <td {...NO_ROW_NAV} className="w-10">
                    <ResourceRowActions row={row} />
                  </td>
                ) : null}
                <td className="whitespace-nowrap">
                  <Link
                    href={row.detail_path}
                    className={`font-mono text-xs font-semibold ${
                      tone === "bad" ? "text-red-700 dark:text-red-300" : "text-[var(--accent)]"
                    }`}
                  >
                    {row.code}
                  </Link>
                  {row.alerts.length > 0 ? (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {row.alerts.map((alert) => (
                        <Pill key={alert.kind} tone={alertTone(alert)}>
                          {alert.label}
                        </Pill>
                      ))}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap">{row.type.name}</td>
                <td className="whitespace-nowrap text-xs tabular-nums">
                  {formatResourceDay(row.handed_over_at ?? null) || "–"}
                </td>
                <td className="font-mono text-xs">{row.external_id}</td>
                <td className="max-w-[18rem]">
                  <span className="block truncate font-medium" title={row.name}>
                    {row.name}
                  </span>
                  {row.link && isWebLink(row.link) ? (
                    <a
                      href={row.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate text-xs text-[var(--accent)] underline"
                    >
                      {row.link}
                    </a>
                  ) : row.link ? (
                    <span className="block truncate text-xs text-[var(--text-muted)]" title={row.link}>
                      {row.link}
                    </span>
                  ) : null}
                </td>
                {compact ? null : <td className="whitespace-nowrap">{row.department.name}</td>}
                <td className="whitespace-nowrap">
                  {row.holder ? (
                    <span className="person">{row.holder.full_name}</span>
                  ) : (
                    <span className="text-xs text-[var(--text-muted)]">Phòng Tài nguyên</span>
                  )}
                </td>
                {actions && row.can_edit ? (
                  // 2026-10-23: a click on the status changes it - for whoever
                  // may edit this resource; never opens the profile.
                  <td {...NO_ROW_NAV} className="whitespace-nowrap">
                    <ResourceStatusButton row={row} />
                  </td>
                ) : (
                  <td className="whitespace-nowrap">
                    <Pill tone={statusTone(row.status.status_group)}>{row.status.name}</Pill>
                  </td>
                )}
                <td className="whitespace-nowrap">
                  {row.holder ? (
                    <Pill tone={employmentTone(row.holder.employment_status)}>
                      {row.holder.employment_status_label}
                    </Pill>
                  ) : (
                    <span className="text-xs text-[var(--text-muted)]">–</span>
                  )}
                </td>
                <td className="whitespace-nowrap text-xs">{row.handover.name}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
