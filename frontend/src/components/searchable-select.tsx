"use client";

import {
  Children,
  Fragment,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/**
 * A long drop-down with a search box (2026-10-22): "các dropdown thêm tính
 * năng nhập text vào tìm kiếm" - scrolling through dozens of people was slow.
 *
 * `Select` (components/pr.tsx) hands over here when a list has
 * `SEARCH_THRESHOLD` options or more. The native `<select>` stays in the page,
 * visually hidden: it keeps the id the `<label>` points at, the value, the
 * `onChange`, the form semantics and every test that drives it. What shows is
 * a button with the current choice; it opens a panel (portalled, fixed under
 * the button, above any Modal) with a text box that filters the options -
 * accents and case ignored, "hoang" finds "Hoàng" - and the list. A choice is
 * written into the native select and announced with a real `change` event, so
 * the caller's `onChange` runs as before.
 *
 * Keyboard: the native select keeps the focus (and is what the label and a
 * screen reader see); Enter / Space / ↓ or typing a letter there opens the
 * panel (the letter starts the search); in the panel ↑ ↓ move, Enter picks,
 * Escape and Tab close it, and the focus goes back to the select.
 */
export const SEARCH_THRESHOLD = 8;

export type SelectOption = {
  value: string;
  label: string;
  disabled: boolean;
  group?: string;
};

function textOf(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: React.ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/** The `<option>`s inside `children`, through arrays, fragments and `<optgroup>`s. */
export function collectOptions(children: React.ReactNode, group?: string): SelectOption[] {
  const found: SelectOption[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement<Record<string, unknown>>(child)) return;
    if (child.type === Fragment) {
      found.push(...collectOptions(child.props.children as React.ReactNode, group));
    } else if (child.type === "optgroup") {
      found.push(
        ...collectOptions(child.props.children as React.ReactNode, String(child.props.label ?? "")),
      );
    } else if (child.type === "option") {
      const label = textOf(child.props.children as React.ReactNode);
      found.push({
        value: child.props.value === undefined ? label : String(child.props.value),
        label,
        disabled: Boolean(child.props.disabled),
        group,
      });
    }
  });
  return found;
}

/** Lower case, no accents, đ → d: what the search compares. */
export function foldText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .trim();
}

const setNativeValue = (select: HTMLSelectElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
};

export function SearchableSelect({
  options,
  className = "",
  selectProps,
  children,
}: {
  options: SelectOption[];
  /** The caller's classes: on the wrapper (width, margins, height). */
  className?: string;
  selectProps: React.SelectHTMLAttributes<HTMLSelectElement>;
  children: React.ReactNode;
}) {
  const selectRef = useRef<HTMLSelectElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const panel = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  const controlled = selectProps.value !== undefined;
  const [own, setOwn] = useState<string>(
    String(selectProps.defaultValue ?? options.find((option) => !option.disabled)?.value ?? ""),
  );
  const value = controlled ? String(selectProps.value ?? "") : own;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [at, setAt] = useState({ top: 0, left: 0, width: 240, above: false });
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const current = options.find((option) => option.value === value) ?? null;
  const shown = useMemo(() => {
    const wanted = foldText(query);
    if (!wanted) return options;
    return options.filter((option) => foldText(option.label).includes(wanted));
  }, [options, query]);

  const place = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect();
    if (!box) return;
    const height = Math.min(360, window.innerHeight - 16);
    const above = box.bottom + height > window.innerHeight && box.top > window.innerHeight - box.bottom;
    const width = Math.max(box.width, 240);
    setAt({
      top: above ? Math.max(8, box.top - 4) : box.bottom + 4,
      left: Math.min(box.left, Math.max(8, window.innerWidth - width - 8)),
      width,
      above,
    });
  }, []);

  const openWith = (seed = "") => {
    if (selectProps.disabled) return;
    place();
    setQuery(seed);
    const index = options.findIndex((option) => option.value === value);
    setActive(seed ? 0 : Math.max(0, index));
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    setQuery("");
    if (refocus) selectRef.current?.focus();
  };
  const pick = (option: SelectOption) => {
    if (option.disabled) return;
    if (!controlled) setOwn(option.value);
    if (selectRef.current) setNativeValue(selectRef.current, option.value);
    close();
  };

  useLayoutEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node | null;
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return;
      close(false);
    };
    const away = (event: Event) => {
      if (panel.current?.contains(event.target as Node | null)) return;
      close(false);
    };
    document.addEventListener("mousedown", outside, true);
    window.addEventListener("resize", away);
    window.addEventListener("scroll", away, true);
    return () => {
      document.removeEventListener("mousedown", outside, true);
      window.removeEventListener("resize", away);
      window.removeEventListener("scroll", away, true);
    };
    // `close` only sets state: re-subscribing per render would be noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => {
    if (!open) return;
    panel.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [active, open]);

  const onPanelKey = (event: React.KeyboardEvent) => {
    // The panel sits in a portal but React bubbles through it: a surrounding
    // Modal must not see this Escape.
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      close(false);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(shown.length - 1, index + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = shown[active];
      if (option) pick(option);
    }
  };

  const { className: _ignored, onChange, onKeyDown, ...rest } = selectProps;
  void _ignored;
  const minHeight = /\bmin-h-/.test(className) ? "" : "min-h-11";

  return (
    <span className={`relative inline-flex max-w-full ${minHeight} ${className}`}>
      <select
        {...rest}
        ref={selectRef}
        data-select="pr"
        data-searchable="true"
        className="peer sr-only"
        onChange={(event) => {
          if (!controlled) setOwn(event.target.value);
          onChange?.(event);
        }}
        onKeyDown={(event) => {
          onKeyDown?.(event);
          if (event.defaultPrevented) return;
          // The native select keeps the keyboard focus (and the screen
          // reader's view); the keys that would open its own list open ours.
          if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openWith();
          } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            openWith(event.key);
          }
        }}
      >
        {children}
      </select>
      {/* The visible face, for the pointer only: the native select above is
          what the keyboard, the label and assistive tech use. Its text is
          drawn by CSS so it never doubles an option's text in the DOM. */}
      <button
        ref={trigger}
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        disabled={selectProps.disabled}
        data-select-trigger=""
        onClick={() => (open ? close() : openWith())}
        className={`flex min-h-[inherit] w-full min-w-0 items-center justify-between gap-2 rounded-lg border bg-[var(--surface)] px-3 text-left text-[var(--text)] disabled:opacity-50 peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--ring)] ${
          selectProps["aria-invalid"] ? "border-[var(--bad)]" : "border-[var(--border)]"
        } ${open ? "ring-2 ring-[var(--ring)]" : ""}`}
      >
        <span className="truncate before:content-[attr(data-label)]" data-label={current?.label ?? ""} />
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="shrink-0 text-[var(--text-muted)]">
          <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
      {open && mounted
        ? createPortal(
            <div
              ref={panel}
              onKeyDown={onPanelKey}
              className="fixed z-[120] flex max-h-[360px] flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-pop)]"
              style={{
                left: at.left,
                width: at.width,
                ...(at.above ? { bottom: window.innerHeight - at.top } : { top: at.top }),
              }}
            >
              <div className="border-b border-[var(--border)] p-2">
                <input
                  ref={input}
                  type="text"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setActive(0);
                  }}
                  placeholder="Gõ để tìm…"
                  aria-label="Tìm trong danh sách"
                  aria-controls={listId}
                  className="min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
                />
              </div>
              <ul id={listId} role="listbox" className="min-h-0 flex-1 overflow-y-auto py-1 text-sm">
                {shown.length === 0 ? (
                  <li className="px-3 py-2 text-[var(--text-muted)]">Không tìm thấy</li>
                ) : (
                  shown.map((option, index) => (
                    <li
                      key={`${option.group ?? ""}:${option.value}:${index}`}
                      role="option"
                      data-index={index}
                      aria-selected={option.value === value}
                      aria-disabled={option.disabled || undefined}
                      onMouseEnter={() => setActive(index)}
                      onMouseDown={(event) => {
                        // Keep the focus in the box until the pick is made.
                        event.preventDefault();
                        pick(option);
                      }}
                      className={`cursor-pointer px-3 py-2 ${
                        option.disabled ? "cursor-not-allowed opacity-50" : ""
                      } ${index === active ? "bg-[var(--surface-muted)]" : ""} ${
                        option.value === value ? "font-semibold text-[var(--accent-strong)]" : ""
                      }`}
                    >
                      {option.group ? (
                        <span className="mr-1 text-xs text-[var(--text-muted)]">{option.group} ·</span>
                      ) : null}
                      {option.label}
                    </li>
                  ))
                )}
              </ul>
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
