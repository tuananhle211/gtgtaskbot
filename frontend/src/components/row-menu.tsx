"use client";

import Link from "next/link";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** One entry of a row's "⋯" menu: a screen to open, or something to do. */
export type RowMenuItem = {
  key: string;
  label: string;
  href?: string;
  onSelect?: () => void;
  /** A destructive entry: drawn in the danger colour. */
  tone?: "danger";
};

/**
 * The "⋯" menu at the end of a table row (2026-10-17): the row's actions in one
 * place instead of a strip of buttons. The list is portalled to `document.body`
 * and positioned under the trigger, so the table's horizontal scroller cannot
 * clip it; it closes on Escape, an outside press, a scroll or a resize. Draws
 * nothing when there is no entry.
 */
export function RowMenu({
  label,
  items,
}: {
  label: string;
  items: RowMenuItem[];
}) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState({ top: 0, right: 0 });
  const trigger = useRef<HTMLButtonElement | null>(null);
  const panel = useRef<HTMLDivElement | null>(null);
  const id = useId();

  useLayoutEffect(() => {
    if (!open || !trigger.current) return;
    const box = trigger.current.getBoundingClientRect();
    setAt({
      top: box.bottom + 4,
      right: Math.max(8, window.innerWidth - box.right),
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || trigger.current?.contains(target))
        return;
      setOpen(false);
    };
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    const close = () => setOpen(false);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", keys);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    panel.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", keys);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  if (items.length === 0) return null;
  const itemClass = (tone?: "danger") =>
    `flex min-h-10 w-full items-center rounded-lg px-3 text-left text-sm transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)] ${
      tone === "danger" ? "text-[var(--bad)]" : "text-[var(--text)]"
    }`;
  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((was) => !was)}
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="currentColor"
          aria-hidden="true"
        >
          <circle cx="5" cy="12" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <circle cx="19" cy="12" r="1.8" />
        </svg>
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panel}
              id={id}
              role="menu"
              aria-label={label}
              style={{ position: "fixed", top: at.top, right: at.right }}
              className="z-50 min-w-[13rem] rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1 shadow-[var(--shadow-pop)]"
            >
              {items.map((item) =>
                item.href ? (
                  <Link
                    key={item.key}
                    role="menuitem"
                    href={item.href}
                    onClick={() => setOpen(false)}
                    className={itemClass(item.tone)}
                  >
                    {item.label}
                  </Link>
                ) : (
                  <button
                    key={item.key}
                    role="menuitem"
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      item.onSelect?.();
                    }}
                    className={itemClass(item.tone)}
                  >
                    {item.label}
                  </button>
                ),
              )}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
