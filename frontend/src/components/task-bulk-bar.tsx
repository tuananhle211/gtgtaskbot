"use client";

import { ConfirmButton, type ConfirmSpec } from "@/components/confirm";
import { SecondaryButton } from "@/components/pr";

/**
 * The bar under the task table once the Trưởng phòng PR has ticked rows
 * (2026-10-19): how many are chosen, and the actions that apply to all of
 * them at once. One action for now - "Duyệt task" - the bar is where the
 * next ones go.
 *
 * The selection itself lives on the page (it is dropped with every filter
 * change, like the PR board's); this component only says and asks.
 */
export function TaskBulkBar({
  count,
  approvable,
  pending,
  error,
  onApprove,
  onClear,
}: {
  /** How many rows are ticked. */
  count: number;
  /** How many of them stand at a review gate, so "Duyệt" applies to them. */
  approvable: number;
  pending: boolean;
  error?: unknown;
  onApprove: () => void;
  onClear: () => void;
}) {
  const skipped = count - approvable;
  const spec: ConfirmSpec = {
    title: `Duyệt ${approvable} task?`,
    description:
      `${approvable} task đang chờ duyệt sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.` +
      (skipped > 0 ? ` ${skipped} task đã chọn chưa ở bước duyệt sẽ được bỏ qua.` : ""),
    confirmLabel: `Duyệt ${approvable} task`,
    variant: "primary",
    count: approvable,
  };
  return (
    <div
      role="region"
      aria-label="Thao tác hàng loạt"
      className="sticky bottom-0 z-20 -mx-4 mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--surface)]/95 px-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 backdrop-blur sm:mx-0 sm:rounded-xl sm:border sm:px-4"
    >
      <p className="text-sm">
        <span className="font-medium tabular-nums">Đã chọn {count} task</span>
        <span className="text-[var(--text-muted)]">
          {" "}
          · {approvable} đang chờ duyệt
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ConfirmButton
          spec={spec}
          pending={pending}
          error={error}
          disabled={approvable === 0}
          onConfirm={onApprove}
          tone="primary"
        >
          {`Duyệt ${approvable} task`}
        </ConfirmButton>
        <SecondaryButton type="button" disabled={pending} onClick={onClear}>
          Bỏ chọn
        </SecondaryButton>
      </div>
    </div>
  );
}
