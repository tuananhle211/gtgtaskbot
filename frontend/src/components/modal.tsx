"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * A modal that holds a form: the change-password dialog, the avatar cropper.
 *
 * `ConfirmDialog` (components/confirm.tsx) is the panel's yes/no question and
 * stays that; this is its sibling for dialogs whose body is the thing being
 * done. Same rules, for the same reasons that file spells out:
 *
 * - portalled into `document.body`, so no blurred or transformed ancestor can
 *   capture the `fixed` overlay;
 * - `role="dialog"` + `aria-modal`, labelled by its title (and described by its
 *   description when there is one);
 * - focus moves in on open (the first `[data-autofocus]`, else the first field
 *   or button), Tab cycles inside, and focus goes back to the opener on close;
 * - Escape and a click on the scrim close it - except while `busy`, because
 *   dismissing a request in flight leaves nobody knowing whether it happened;
 * - a bottom sheet on a phone, a centred card from `md` up; the body scrolls
 *   under a footer that stays put.
 *
 * The caller lays out the inside with `ModalBody` and `ModalFooter`, so a form
 * can wrap both and its submit button can sit in the footer.
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  busy = false,
  size = "md",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: React.ReactNode;
  /** A request is in flight: Escape, the scrim and the close button do nothing. */
  busy?: boolean;
  /** `md` = 448px, `lg` = 576px. */
  size?: "md" | "lg";
  children: React.ReactNode;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panel = useRef<HTMLDivElement | null>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!open || !mounted) return;
    const opener = document.activeElement as HTMLElement | null;
    const root = panel.current;
    const target =
      root?.querySelector<HTMLElement>("[data-autofocus]") ??
      (root ? focusableWithin(root).find((node) => !node.hasAttribute("data-modal-close")) : null);
    (target ?? root)?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      opener?.focus?.();
    };
  }, [open, mounted]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        if (!busy) onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = focusableWithin(panel.current);
      if (focusable.length === 0) {
        event.preventDefault();
        panel.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !panel.current.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [busy, onClose],
  );

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className="modal-scrim fixed inset-0 z-[100] flex items-end justify-center md:items-center md:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        onKeyDown={handleKeyDown}
        className={`modal-panel flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl border border-[var(--border)] bg-[var(--surface)] shadow-2xl outline-none md:max-h-[88vh] md:rounded-2xl ${
          size === "lg" ? "md:max-w-xl" : "md:max-w-md"
        }`}
      >
        <header className="flex items-start gap-3 px-5 pb-1 pt-5 md:px-6 md:pt-6">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold leading-snug tracking-tight">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-1 text-sm leading-relaxed text-[var(--text-muted)]">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            data-modal-close=""
            onClick={onClose}
            disabled={busy}
            aria-label="Đóng"
            className="-mr-2 -mt-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] disabled:opacity-40"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** The scrolling middle of a `Modal`. */
export function ModalBody({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`min-h-0 flex-1 overflow-y-auto px-5 pb-5 pt-4 md:px-6 md:pb-6 ${className}`}>
      {children}
    </div>
  );
}

/** The fixed footer of a `Modal`: stacked full-width on a phone, right-aligned from `sm`. */
export function ModalFooter({ children, start }: { children: React.ReactNode; start?: React.ReactNode }) {
  return (
    <footer className="flex flex-col-reverse gap-2 border-t border-[var(--border)] bg-[var(--surface-muted)]/60 px-5 py-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] sm:flex-row sm:items-center sm:justify-end md:px-6 md:pb-4">
      {start ? <div className="sm:mr-auto">{start}</div> : null}
      {children}
    </footer>
  );
}

function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]):not([hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}
