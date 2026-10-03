"use client";

import { useCallback, useState } from "react";
import type { ContentSummary } from "@/lib/api";
import { stageLabel } from "@/lib/labels";
import { bulkApproveConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import { SecondaryButton } from "@/components/pr";

/**
 * Selecting several items at one approval step, and approving them together.
 *
 * ## What a selection is allowed to be
 *
 * Step 1F.2.8. One selection, one **step**: every item in it stands at the same
 * gate, because the server refuses a batch that mixes two and a screen that let
 * somebody build one would be collecting a refusal. Ticking a card in a
 * different lane therefore starts a new selection rather than adding to the
 * old - visibly, because the bar's step label changes with it.
 *
 * ## Why the filter signature is the key
 *
 * A selection means "these items, in this context". Change the context - the
 * search, the channel, the responsible person, the dates, the scope tab, the
 * lifecycle group - and the set somebody was choosing from is a different set,
 * so the choice is stale. Keeping it would leave rows selected that are no
 * longer on screen, which is the "hidden selection from a previous filter" this
 * step forbids: somebody would press "Duyệt 12 nội dung" over a board showing
 * three.
 *
 * So the selection is held **against a signature of the filters**, and any
 * change to them drops it. That single rule covers every filter at once,
 * including ones added later, which is why it is a signature rather than a list
 * of `useEffect` dependencies somebody has to remember to extend.
 *
 * ## Why it does not survive leaving the board
 *
 * It is React state only, deliberately. Carrying a selection across a
 * navigation would need one of the browser's own key-value stores, and
 * `tests/security.test.ts` forbids every one of them anywhere in `src/` - their
 * absence is the evidence that no code path is trying to hold a credential in
 * JavaScript, and a bulk selection is not worth an exception to that rule.
 * Opening a card therefore returns to an empty selection, which is the safe
 * direction to be wrong in: the cost of forgetting is re-ticking twelve boxes,
 * and the cost of remembering wrongly is approving something nobody looked at.
 *
 * Ticking a checkbox does **not** navigate, because the checkbox is rendered
 * beside the card's link rather than inside it - see `ContentCard`. So the
 * ordinary "tick some, read one, come back" loop is the browser's Back button,
 * and this state goes with it.
 */

export interface BulkSelection {
  /** The `PrApprovalStage` the batch will be filed under. */
  readonly gate: string;
  /** The workflow stage those items stand at. What the dialog names. */
  readonly stage: string;
  readonly ids: readonly string[];
  /**
   * The whole eligible queue at the step, when a select-all found more than the
   * server would take in one batch. Present only then, and only so the
   * confirmation can say it is approving the first N of a longer queue.
   */
  readonly eligibleTotal?: number;
}

/**
 * The board's selection state, tied to the filter context it was made in.
 *
 * @param signature Everything that defines which items are on screen. The
 *   caller builds it from the URL, so a filter added to the page joins this
 *   automatically.
 */
export function useBulkSelection(signature: string) {
  const [selection, setSelection] = useState<BulkSelection | null>(null);
  const [context, setContext] = useState(signature);

  // Any change to the filter context drops the selection. One rule, every
  // filter - see the module docstring. Reconciled during render rather than in
  // an effect, so the board never paints a frame with a stale count in the bar.
  if (context !== signature) {
    setContext(signature);
    if (selection !== null) setSelection(null);
  }

  /** Tick or untick one card. A different step starts a new selection. */
  const toggle = useCallback((gate: string, stage: string, item: ContentSummary) => {
    setSelection((current) => {
      const ids =
        current && current.gate === gate
          ? current.ids.includes(item.id)
            ? current.ids.filter((id) => id !== item.id)
            : [...current.ids, item.id]
          : [item.id];
      return ids.length > 0 ? { gate, stage, ids } : null;
    });
  }, []);

  /** Add every eligible card loaded in a lane; ticking again clears them. */
  const togglePage = useCallback((gate: string, stage: string, items: ContentSummary[]) => {
    const page = items.map((item) => item.id);
    setSelection((current) => {
      const same = current && current.gate === gate ? current.ids : [];
      const allOn = page.length > 0 && page.every((id) => same.includes(id));
      const ids = allOn
        ? same.filter((id) => !page.includes(id))
        : [...same, ...page.filter((id) => !same.includes(id))];
      return ids.length > 0 ? { gate, stage, ids } : null;
    });
  }, []);

  /**
   * Replace the selection with a server-resolved batch for the whole step.
   *
   * Replace rather than merge: "chọn tất cả ở bước này" is a statement about the
   * step, and unioning it with whatever was ticked before would produce a
   * selection whose size nobody could predict from what they pressed.
   */
  const selectAll = useCallback(
    (gate: string, stage: string, ids: readonly string[], eligibleTotal: number) => {
      setSelection(
        ids.length > 0
          ? {
              gate,
              stage,
              ids: [...ids],
              ...(eligibleTotal > ids.length ? { eligibleTotal } : {}),
            }
          : null,
      );
    },
    [],
  );

  const clear = useCallback(() => setSelection(null), []);

  return { selection, toggle, togglePage, selectAll, clear };
}

export function BulkApprovalBar({
  selection,
  titles,
  pending,
  error,
  onApprove,
  onClear,
}: {
  selection: BulkSelection;
  /** Titles of whatever is loaded, for the dialog's five-item sample. */
  titles: ReadonlyMap<string, string>;
  pending: boolean;
  error?: unknown;
  onApprove: () => void;
  onClear: () => void;
}) {
  const count = selection.ids.length;
  const spec = bulkApproveConfirmation({
    count,
    stage: selection.stage,
    titles: selection.ids
      .map((id) => titles.get(id))
      .filter((title): title is string => Boolean(title)),
    eligibleTotal: selection.eligibleTotal,
  });

  return (
    <div
      role="region"
      aria-label="Thao tác hàng loạt"
      className="sticky bottom-0 z-20 -mx-4 mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--surface)]/95 px-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 backdrop-blur sm:mx-0 sm:rounded-xl sm:border sm:px-4"
    >
      <p className="text-sm">
        <span className="font-medium tabular-nums">Đã chọn {count} nội dung</span>
        <span className="text-[var(--text-muted)]"> · {stageLabel(selection.stage)}</span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ConfirmButton
          spec={spec}
          pending={pending}
          error={error}
          onConfirm={onApprove}
        >
          {`Duyệt ${count} nội dung`}
        </ConfirmButton>
        <SecondaryButton type="button" disabled={pending} onClick={onClear}>
          Bỏ chọn
        </SecondaryButton>
      </div>
    </div>
  );
}
