"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DangerButton, PrimaryButton, SecondaryButton } from "@/components/pr";
import { ErrorBox } from "@/components/states";

/**
 * The one confirmation dialog in the panel.
 *
 * ## Why confirmation is not about danger
 *
 * Step 1F.2.8. Before it, the only actions that asked twice were the ones that
 * *looked* frightening - deleting a resource, cancelling a piece - and they
 * asked with a hand-rolled pair of inline buttons that each card reimplemented.
 * Everything else went on the first click: approving, claiming a production,
 * assigning somebody else's work, revoking a grant, disconnecting a TikTok
 * account. None of those is destructive and every one of them moves
 * responsibility, authority or workflow state onto or off somebody.
 *
 * So the rule this component exists to make cheap is **state, not danger**: an
 * action confirms when it changes business state, responsibility, permissions,
 * workflow or an integration - and does not when it only changes what is on
 * screen. Filtering, searching, sorting, changing tab, paging, opening a card,
 * opening a form and opening a provider's own OAuth consent screen all go
 * straight through, because making navigation ask twice is how people learn to
 * dismiss the question without reading it.
 *
 * ## Why one component
 *
 * The inline pairs disagreed about their own wording, none of them trapped
 * focus, none was reachable by keyboard in the same way twice, and none could
 * be prevented from double-submitting. One component fixes all four at once and
 * makes the fifth thing - *the copy* - the only decision left at a call site.
 * The browser's own modal helpers are deliberately used nowhere in this app:
 * they cannot be styled, cannot say what they are about in more than one line,
 * and block the whole tab. `tests/confirmation.test.tsx` sweeps `src/` for
 * them.
 *
 * ## What the copy has to do
 *
 * Never "Bạn có chắc không?". Every dialog answers three questions in the two
 * fields it has - *what will happen*, *to what or whom*, and *what changes as a
 * result* - and the confirm button repeats the verb so the last thing under the
 * cursor is the action itself: `[Phân công Hà Chi]`, not `[Lưu]`. The copy
 * itself lives in `lib/confirmations.ts` so it can be read in one place and
 * asserted in one test.
 *
 * ## Accessibility, and what "safe" means for ESC
 *
 * `role="dialog"` + `aria-modal`, labelled by its own title and described by
 * its own body. Focus moves in on open and is **returned to whatever opened it**
 * on close, Tab cycles inside, and Escape cancels - except while a request is in
 * flight, because dismissing the dialog then would leave somebody looking at a
 * board with no idea whether the thing happened. That is the whole of "when
 * safe".
 *
 * Which control gets focus depends on the variant, and it is the one place this
 * component takes a position: a **destructive** dialog focuses *Thôi*, so a
 * stray Enter cancels; an ordinary one focuses the confirm button, because the
 * person pressed a button meaning to do the thing and the dialog is there to
 * tell them what it is.
 *
 * ## Why it is portalled, and the bug that made it necessary
 *
 * The overlay is rendered into `document.body` rather than where the trigger
 * sits. That is not tidiness: **`backdrop-filter` makes an element a containing
 * block for its `position: fixed` descendants**, exactly as `transform` and
 * `filter` do. Both of this app's sticky action bars carry `backdrop-blur`, and
 * the bulk bar *contains* a `ConfirmButton` - so `fixed inset-0` was resolving
 * against a sixty-pixel strip at the bottom of the window instead of against
 * the viewport. The dialog appeared jammed into the bar, `items-center` looked
 * like it did nothing (the flex container really was that short), and no
 * `z-index` could lift it out, because the containing block also traps the
 * stacking order.
 *
 * A portal is the fix that cannot regress: with the overlay mounted on `body`,
 * no ancestor a future caller happens to render it inside - blurred, filtered,
 * transformed or `overflow: hidden` - can capture it again.
 *
 * ## The two layouts
 *
 * **Phone (below `md`).** A bottom sheet: full width, rounded top corners only,
 * and the footer padded past the home indicator with
 * `env(safe-area-inset-bottom)`. The sheet is right here because a centred card
 * on a small screen puts its buttons under the on-screen keyboard.
 *
 * **Laptop and up (`md`, 768px).** A centred card, 512px wide (`max-w-lg`),
 * 16px corners all round, 24px of padding, on a full-screen scrim. Not anchored
 * to the bottom, and never merged with whatever sticky toolbar is behind it.
 *
 * The panel is a flex **column** in both: the body scrolls and the footer stays
 * put, so a dialog listing five titles and an error still has its buttons on
 * screen rather than below the fold of its own scroll area.
 */

export type ConfirmVariant = "primary" | "destructive";

/** Everything a confirmation says. Assembled in `lib/confirmations.ts`. */
export interface ConfirmSpec {
  /** A question naming the action and its object: "Phân công Hà Chi sản xuất?" */
  title: string;
  /** What will be true afterwards. One or two sentences, never a warning label. */
  description: string;
  /** The verb, repeated: "Duyệt 12 nội dung", "Thu hồi quyền". */
  confirmLabel: string;
  /** Defaults to "Thôi". */
  cancelLabel?: string;
  /** `destructive` for irreversible or authority-removing actions. */
  variant?: ConfirmVariant;
  /**
   * How many records this affects, when there is more than one. Rendered as its
   * own line so a batch cannot be mistaken for a single item at a glance.
   */
  count?: number;
  /**
   * Anything further worth reading before pressing: a sample of the items, a
   * note about what is not included. Never hundreds of rows - see
   * `bulkApproveConfirmation`, which shows five and counts the rest.
   */
  details?: React.ReactNode;
}

/** Focusable descendants, in tab order. Used by the trap. */
function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

export function ConfirmDialog({
  open,
  spec,
  pending = false,
  error,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  spec: ConfirmSpec;
  /** A request is in flight: both buttons lock and Escape stops cancelling. */
  pending?: boolean;
  /** The failure, shown inside the dialog so the person keeps their context. */
  error?: unknown;
  /**
   * The confirm cannot be pressed yet - a dialog whose `details` collect a
   * required field says so here rather than refusing inside `onConfirm`, which
   * would trip the one-confirm-per-opening latch without ever starting a
   * request. Cancel and Escape still work.
   */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const bodyId = useId();
  const panel = useRef<HTMLDivElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  // `document` does not exist while this page is server-rendered, and a portal
  // needs a node. Mounting flips after hydration, so the server and the first
  // client render agree on "no dialog" - which they already did, because a
  // dialog is only open in response to a click.
  const [mounted, setMounted] = useState(false);
  // One confirm per opening. `pending` alone is not enough: a mutation that has
  // not started yet is not pending, and two fast clicks would both get through.
  const fired = useRef(false);
  const variant = spec.variant ?? "primary";

  useEffect(() => setMounted(true), []);

  // A failed submit must be retryable, and a successful one must not be
  // repeatable by a second click that arrived before the dialog closed. Both
  // fall out of one rule: the latch is held for exactly as long as a request is.
  useEffect(() => {
    if (!pending) fired.current = false;
  }, [pending]);

  // `mounted` is a dependency, not a formality: on the very first render the
  // portal has not been created yet, so `panel.current` is null and there is
  // nothing to focus. Without it, focus would silently never enter the dialog -
  // and with it the effect runs again the moment the portal exists.
  useEffect(() => {
    if (!open || !mounted) {
      fired.current = false;
      return;
    }
    opener.current = document.activeElement as HTMLElement | null;
    const target = panel.current?.querySelector<HTMLElement>(
      variant === "destructive"
        ? "[data-confirm-cancel]"
        : "[data-confirm-accept]",
    );
    target?.focus();
    // The page behind a modal must not scroll under it on a phone, where the
    // dialog is a sheet and the body would otherwise move beneath the thumb.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      // Back to whatever opened it, which is where the reading position is.
      opener.current?.focus?.();
    };
  }, [open, variant, mounted]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        // Not while a request is in flight - see the module docstring.
        if (pending) return;
        event.stopPropagation();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = focusableWithin(panel.current);
      if (focusable.length === 0) {
        // Both buttons are disabled, which is what `pending` looks like. The
        // panel itself is the trap's floor - without this, Tab would walk out
        // of a modal that is mid-request and into the page behind it.
        event.preventDefault();
        panel.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (
        event.shiftKey &&
        (active === first || !panel.current.contains(active))
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onCancel, pending],
  );

  if (!open || !mounted) return null;

  const Accept = variant === "destructive" ? DangerButton : PrimaryButton;

  return createPortal(
    <div
      // Above every sticky bar in the app (they sit at `z-10`/`z-20`), and now
      // genuinely above them: the portal means this `fixed` box is measured
      // against the viewport rather than against a blurred ancestor.
      //
      // `items-end` on a phone, `md:items-center` from 768px up. The scrim is
      // the same full-screen wash in both, so the page behind is visibly
      // inactive rather than merely covered at one edge.
      className="fixed inset-0 z-[100] flex items-end justify-center bg-black/50 md:items-center md:p-6"
      // A click on the backdrop is a cancel, and only a click that started
      // there - dragging a selection out of the panel must not dismiss it.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        // Programmatically focusable only - see the Tab handler.
        tabIndex={-1}
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onKeyDown={handleKeyDown}
        // Sheet below `md`, 512px card from `md` up - see the module docstring
        // for why the two shapes differ. A flex **column** in both, so the body
        // scrolls under a footer that stays where it is.
        className="flex max-h-[90vh] w-full flex-col overflow-hidden rounded-t-2xl border border-[var(--border)] bg-[var(--surface)] shadow-2xl md:max-h-[85vh] md:max-w-lg md:rounded-2xl"
      >
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-5 md:px-6 md:pt-6">
          <h2 id={titleId} className="text-base font-semibold leading-snug">
            {spec.title}
          </h2>
          <p
            id={bodyId}
            className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]"
          >
            {spec.description}
          </p>
          {spec.count !== undefined ? (
            <p className="mt-3 text-sm font-medium tabular-nums">
              Số lượng: {spec.count} nội dung
            </p>
          ) : null}
          {spec.details ? (
            <div className="mt-3 rounded-lg bg-[var(--surface-muted)] p-3 text-xs leading-relaxed whitespace-pre-line break-words text-[var(--text-muted)]">
              {spec.details}
            </div>
          ) : null}
          {error ? (
            <div className="mt-3">
              <ErrorBox error={error} />
            </div>
          ) : null}
          {/* The gap the footer's own border would otherwise sit flush against.
              Inside the scroll area rather than on the footer, so a short
              dialog has breathing room and a long one scrolls right up to the
              rule. */}
          <div className="h-5 md:h-6" />
        </div>
        {/*
          A real footer: its own row, separated by a rule, and never part of the
          scrolling body. On a phone the two controls are full width and stacked
          with the confirm on top (`flex-col-reverse` keeps the DOM order
          cancel-first, so tab order and the `data-confirm-*` hooks are
          unchanged); from `sm` up they sit side by side, right-aligned, cancel
          then confirm.

          The bottom padding clears the home indicator on a phone. From `md` it
          drops back to an ordinary 16px, because a centred card has no edge to
          be swallowed by.
        */}
        <footer className="flex flex-col-reverse gap-2 border-t border-[var(--border)] px-5 py-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] sm:flex-row sm:justify-end md:px-6 md:pb-4">
          <SecondaryButton
            type="button"
            data-confirm-cancel=""
            disabled={pending}
            onClick={onCancel}
            className="w-full sm:w-auto"
          >
            {spec.cancelLabel ?? "Thôi"}
          </SecondaryButton>
          <Accept
            type="button"
            data-confirm-accept=""
            disabled={pending || confirmDisabled}
            onClick={() => {
              if (pending || confirmDisabled || fired.current) return;
              fired.current = true;
              onConfirm();
            }}
            className="w-full sm:w-auto"
          >
            {pending ? "Đang xử lý…" : spec.confirmLabel}
          </Accept>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

/**
 * A button that owns its confirmation. The shape most call sites want.
 *
 * Using this rather than wiring a `ConfirmDialog` by hand is what makes the
 * policy hold as the panel grows: a new state-changing button reaches for a
 * component that already asks, instead of an `onClick` that does not. The
 * inventory test in `tests/confirmation.test.tsx` reads the same list this is
 * built for.
 *
 * `pending` and `error` come from the caller's mutation, so the dialog stays
 * open and locked while the request is in flight and shows the failure in place
 * rather than behind itself.
 */
export function ConfirmButton({
  spec,
  onConfirm,
  pending = false,
  error,
  disabled = false,
  children,
  tone,
  ariaLabel,
  className = "",
  onOpenChange,
  confirmDisabled = false,
}: {
  spec: ConfirmSpec;
  onConfirm: () => void;
  pending?: boolean;
  error?: unknown;
  disabled?: boolean;
  /**
   * The dialog's confirm cannot be pressed yet - `spec.details` collects a
   * required field that is still empty. See `ConfirmDialog`.
   */
  confirmDisabled?: boolean;
  /** The trigger's label. Defaults to the confirm label when omitted. */
  children?: React.ReactNode;
  /** The trigger's styling. The dialog's own styling is `spec.variant`. */
  tone?: "primary" | "secondary" | "danger";
  /**
   * An accessible name for the trigger when its visible text is not enough -
   * a list of rows whose buttons all say "Xóa", where "Xóa" alone tells a
   * screen-reader user nothing about which row they are on.
   */
  ariaLabel?: string;
  className?: string;
  /** Told when the dialog opens or closes, for a caller that clears an error. */
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const change = (next: boolean) => {
    setOpen(next);
    if (!next) setSubmitted(false);
    onOpenChange?.(next);
  };

  // The dialog stays up while the request is in flight - that is where the
  // loading state and any failure belong, and closing first would drop somebody
  // back onto the board with nothing to read. It closes itself when the request
  // finishes without an error; a failure leaves it open, unlatched and
  // retryable.
  useEffect(() => {
    if (!submitted || pending) return;
    if (error) return;
    setSubmitted(false);
    setOpen(false);
    onOpenChange?.(false);
    // `onOpenChange` is intentionally excluded: a caller passing an inline
    // arrow would otherwise re-run this on every render of theirs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitted, pending, error]);
  const Trigger =
    tone === "danger"
      ? DangerButton
      : tone === "secondary"
        ? SecondaryButton
        : PrimaryButton;

  return (
    <>
      <Trigger
        type="button"
        aria-label={ariaLabel}
        disabled={disabled || pending}
        onClick={() => change(true)}
        className={className}
      >
        {children ?? spec.confirmLabel}
      </Trigger>
      <ConfirmDialog
        open={open}
        spec={spec}
        pending={pending}
        error={error}
        confirmDisabled={confirmDisabled}
        onCancel={() => change(false)}
        onConfirm={() => {
          setSubmitted(true);
          onConfirm();
        }}
      />
    </>
  );
}
