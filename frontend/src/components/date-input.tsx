"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Date boxes that always read `dd/mm/yyyy` / `dd/mm/yyyy HH:mm` (24h), on every
 * machine (2026-10-21).
 *
 * The browser's own `<input type="date">` / `datetime-local` draws its text in
 * the operating system's language - `10/21/2026, 07:32 AM` on an English Mac -
 * and no attribute changes that. So the box is a text field with the app's
 * format, the digits typed are laid out as they come (`2110202617` →
 * `21/10/2026 17:`), and the calendar button opens the app's own picker
 * (`DatePopover`) right under the box: a Monday-first month, 24h hour and
 * minute, quick times, "Hôm nay" / "Xoá" - in the brand tokens, both themes.
 *
 * The value in and out is the native one - `YYYY-MM-DD` or `YYYY-MM-DDTHH:mm`
 * (empty = nothing) - so the callers' state, `toIsoFromLocal` and the API are
 * unchanged. A half-typed box sends nothing until it reads as a whole date;
 * leaving it puts back the last good one. Pasting `2026-10-21T17:30` works too.
 */

type Kind = "date" | "datetime";

export interface DateFieldProps {
  value: string;
  onChange: (event: { target: { value: string } }) => void;
  className?: string;
  id?: string;
  required?: boolean;
  disabled?: boolean;
  min?: string;
  max?: string;
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

export function DateInput(props: DateFieldProps) {
  return <DateField kind="date" {...props} />;
}

export function DateTimeInput(props: DateFieldProps) {
  return <DateField kind="datetime" {...props} />;
}

const PLACEHOLDER: Record<Kind, string> = {
  date: "dd/mm/yyyy",
  datetime: "dd/mm/yyyy HH:mm",
};
const DIGITS: Record<Kind, number> = { date: 8, datetime: 12 };

/** `YYYY-MM-DD[THH:mm]` → `dd/mm/yyyy[ HH:mm]`; anything else → "". */
export function displayDate(value: string, kind: Kind): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(value ?? "");
  if (!match) return "";
  const day = `${match[3]}/${match[2]}/${match[1]}`;
  if (kind === "date") return day;
  return `${day} ${match[4] ?? "00"}:${match[5] ?? "00"}`;
}

/** Digits laid out as `dd/mm/yyyy HH:mm`, as far as they go. */
export function maskDate(digits: string, kind: Kind): string {
  const d = digits.replace(/\D/g, "").slice(0, DIGITS[kind]);
  let out = d.slice(0, 2);
  if (d.length > 2) out += `/${d.slice(2, 4)}`;
  if (d.length > 4) out += `/${d.slice(4, 8)}`;
  if (kind === "datetime") {
    if (d.length > 8) out += ` ${d.slice(8, 10)}`;
    if (d.length > 10) out += `:${d.slice(10, 12)}`;
  }
  return out;
}

/** A whole, real `dd/mm/yyyy[ HH:mm]` as the native value; else null. */
export function parseDate(text: string, kind: Kind): string | null {
  const pattern =
    kind === "date"
      ? /^(\d{2})\/(\d{2})\/(\d{4})$/
      : /^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})$/;
  const match = pattern.exec(text.trim());
  if (!match) return null;
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(year, month - 1, day);
  if (
    year < 1900 ||
    check.getFullYear() !== year ||
    check.getMonth() !== month - 1 ||
    check.getDate() !== day
  )
    return null;
  const iso = `${match[3]}-${match[2]}-${match[1]}`;
  if (kind === "date") return iso;
  if (Number(match[4]) > 23 || Number(match[5]) > 59) return null;
  return `${iso}T${match[4]}:${match[5]}`;
}

/** Utility classes that place the box (margins, widths) go on its wrapper. */
function split(className = ""): { outer: string; inner: string } {
  const outer: string[] = [];
  const inner: string[] = [];
  for (const token of className.split(/\s+/).filter(Boolean)) {
    if (/^((sm|md|lg|xl):)?(m[tbxylr]?-|max-w-|w-|min-w-|flex-|self-)/.test(token)) outer.push(token);
    else inner.push(token);
  }
  return { outer: outer.join(" "), inner: inner.join(" ") };
}

function DateField({
  kind,
  value,
  onChange,
  className,
  min,
  max,
  disabled,
  ...rest
}: DateFieldProps & { kind: Kind }) {
  const shown = displayDate(value, kind);
  const [text, setText] = useState(shown);
  const [editing, setEditing] = useState(false);
  const wrapper = useRef<HTMLSpanElement>(null);
  // Somebody else changed the value (a default, a reset, the picker).
  useEffect(() => {
    if (!editing) setText(shown);
  }, [shown, editing]);

  const emit = (next: string) => onChange({ target: { value: next } });

  const type = (raw: string) => {
    // A native value, pasted or typed (`2026-10-21T17:30`): kept as typed
    // until whole, then shown the app's way.
    const trimmed = raw.trim();
    if (/^\d{4}-/.test(trimmed)) {
      const native = trimmed.slice(0, kind === "date" ? 10 : 16);
      if (displayDate(native, kind) && native.length === (kind === "date" ? 10 : 16)) {
        setText(displayDate(native, kind));
        emit(native);
      } else setText(trimmed);
      return;
    }
    let digits = raw.replace(/\D/g, "");
    // Backspace over a "/", " " or ":": take the digit before it too.
    if (raw.length < text.length && digits === text.replace(/\D/g, "")) {
      digits = digits.slice(0, -1);
    }
    const masked = maskDate(digits, kind);
    setText(masked);
    if (masked === "") emit("");
    else {
      const parsed = parseDate(masked, kind);
      if (parsed) emit(parsed);
    }
  };

  const [open, setOpen] = useState(false);
  const pick = (next: string) => {
    // Shown at once, even while the text box still has the focus.
    setText(displayDate(next, kind));
    emit(next);
  };

  const { outer, inner } = split(className);
  return (
    <span ref={wrapper} className={`relative block ${outer}`}>
      <input
        {...rest}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder={PLACEHOLDER[kind]}
        value={text}
        disabled={disabled}
        onFocus={() => setEditing(true)}
        onBlur={() => {
          setEditing(false);
          setText(shown);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && event.altKey) {
            event.preventDefault();
            setOpen(true);
          }
        }}
        onChange={(event) => type(event.target.value)}
        className={`${inner} w-full pr-9 tabular-nums sm:text-sm`}
      />
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        disabled={disabled}
        aria-label={kind === "date" ? "Chọn ngày" : "Chọn ngày giờ"}
        aria-expanded={open}
        className={`absolute right-1 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-md transition-colors disabled:opacity-50 ${
          open
            ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
            : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--accent)]"
        }`}
      >
        <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
          <rect x="3.5" y="5" width="17" height="15" rx="2" />
          <path d="M3.5 9.5h17M8 3v4M16 3v4" strokeLinecap="round" />
        </svg>
      </button>
      {open ? (
        <DatePopover
          anchor={wrapper}
          kind={kind}
          value={value}
          min={min}
          max={max}
          clearable={!rest.required}
          onPick={pick}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </span>
  );
}

// --- the picker ----------------------------------------------------------------

const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];
const WEEKDAY_NAMES = ["Chủ nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];
const QUICK_TIMES = ["08:00", "12:00", "17:30", "23:59"];
const DEFAULT_TIME = "17:30";
const pad = (n: number) => String(n).padStart(2, "0");
const isoDay = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/** Where the panel sits: under the box, or above it when there is no room -
 * and never past the edge of the window. */
function placement(anchor: HTMLElement, height: number, width: number) {
  const box = anchor.getBoundingClientRect();
  const gap = 6;
  const below = window.innerHeight - box.bottom;
  const up = below < height + gap && box.top > below;
  const left = Math.max(8, Math.min(box.left, window.innerWidth - width - 8));
  const wanted = up ? box.top - gap - height : box.bottom + gap;
  const top = Math.max(8, Math.min(wanted, window.innerHeight - height - 8));
  return { left, top };
}

function DatePopover({
  anchor,
  kind,
  value,
  min,
  max,
  clearable,
  onPick,
  onClose,
}: {
  anchor: React.RefObject<HTMLSpanElement | null>;
  kind: Kind;
  value: string;
  min?: string;
  max?: string;
  clearable: boolean;
  onPick: (next: string) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const today = new Date();
  const day = value.slice(0, 10);
  const time = kind === "datetime" && value.length >= 16 ? value.slice(11, 16) : DEFAULT_TIME;
  const shownDay = day ? new Date(`${day}T00:00:00`) : today;
  const [view, setView] = useState({ year: shownDay.getFullYear(), month: shownDay.getMonth() });
  const width = 312;
  const [where, setWhere] = useState<{ left: number; top: number } | null>(null);

  // Follow the box: fixed to the page, so a dialog's scroller never clips it.
  // Measured once drawn (first off-screen), so the real height decides.
  useLayoutEffect(() => {
    const place = () => {
      if (!anchor.current) return;
      const height = panel.current?.offsetHeight || (kind === "datetime" ? 470 : 380);
      setWhere(placement(anchor.current, height, width));
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor, kind]);

  // A press outside the box and the panel closes it.
  useEffect(() => {
    const away = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || anchor.current?.contains(target)) return;
      onClose();
    };
    // Escape closes the picker only - caught before a dialog the box sits in
    // hears it and closes too.
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
      anchor.current?.querySelector("input")?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape, true);
    };
  }, [anchor, onClose]);

  const first = new Date(view.year, view.month, 1);
  const offset = (first.getDay() + 6) % 7;
  const cells = Array.from({ length: 42 }, (_, index) => new Date(view.year, view.month, index - offset + 1));
  const outOfRange = (iso: string) =>
    (min !== undefined && min !== "" && iso < min.slice(0, 10)) ||
    (max !== undefined && max !== "" && iso > max.slice(0, 10));
  const move = (delta: number) =>
    setView(({ year, month }) => {
      const next = new Date(year, month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  const setDay = (iso: string) => {
    if (kind === "date") {
      onPick(iso);
      onClose();
    } else onPick(`${iso}T${time}`);
  };
  const setTime = (next: string) => onPick(`${day || isoDay(today)}T${next}`);
  const [hour, minute] = time.split(":");

  const panelClass =
    "fixed z-[120] rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 text-sm text-[var(--text)] shadow-[var(--shadow-pop)]";
  const selectClass =
    "min-h-9 flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-base tabular-nums text-[var(--text)] sm:text-sm";

  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={kind === "date" ? "Chọn ngày" : "Chọn ngày giờ"}
      data-testid="date-popover"
      className={panelClass}
      style={{ width, maxWidth: "calc(100vw - 16px)", left: where?.left ?? -9999, top: where?.top ?? 0 }}
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => move(-1)}
          aria-label="Tháng trước"
          className="flex size-9 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
        >
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path d="M15 6l-6 6 6 6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <p className="font-display text-[15px] font-semibold" aria-live="polite">
          Tháng {view.month + 1}, {view.year}
        </p>
        <button
          type="button"
          onClick={() => move(1)}
          aria-label="Tháng sau"
          className="flex size-9 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
        >
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <div className="mt-1 grid grid-cols-7 gap-0.5 text-center">
        {WEEKDAYS.map((name) => (
          <span key={name} className="brand-eyebrow pb-1 text-[11px]">
            {name}
          </span>
        ))}
        {cells.map((date) => {
          const iso = isoDay(date);
          const inMonth = date.getMonth() === view.month;
          const selected = iso === day;
          const isToday = iso === isoDay(today);
          const blocked = outOfRange(iso);
          return (
            <button
              key={iso}
              type="button"
              disabled={blocked}
              onClick={() => setDay(iso)}
              aria-pressed={selected}
              aria-label={`${WEEKDAY_NAMES[date.getDay()]}, ${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()}`}
              className={`flex h-8 items-center justify-center rounded-lg tabular-nums transition-colors disabled:cursor-not-allowed disabled:opacity-30 ${
                selected
                  ? "bg-[var(--accent)] font-semibold text-[var(--accent-text)]"
                  : isToday
                    ? "font-semibold text-[var(--accent)] ring-1 ring-inset ring-[var(--accent)] hover:bg-[var(--accent-soft)]"
                    : inMonth
                      ? "hover:bg-[var(--accent-soft)] hover:text-[var(--accent-strong)]"
                      : "text-[var(--text-muted)] opacity-60 hover:bg-[var(--surface-muted)]"
              }`}
            >
              {date.getDate()}
            </button>
          );
        })}
      </div>
      {kind === "datetime" ? (
        <div className="mt-2 border-t border-[var(--border)] pt-2">
          <div className="flex items-center gap-1.5">
            <span className="w-9 text-xs font-medium text-[var(--text-muted)]">Giờ</span>
            <select
              aria-label="Giờ"
              value={hour}
              onChange={(event) => setTime(`${event.target.value}:${minute}`)}
              className={selectClass}
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={pad(h)}>
                  {pad(h)}
                </option>
              ))}
            </select>
            <span className="text-base font-semibold text-[var(--text-muted)]">:</span>
            <select
              aria-label="Phút"
              value={minute}
              onChange={(event) => setTime(`${hour}:${event.target.value}`)}
              className={selectClass}
            >
              {Array.from({ length: 60 }, (_, m) => (
                <option key={m} value={pad(m)}>
                  {pad(m)}
                </option>
              ))}
            </select>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5 pl-[42px]">
            {QUICK_TIMES.map((quick) => (
              <button
                key={quick}
                type="button"
                onClick={() => setTime(quick)}
                aria-pressed={quick === time}
                className={`min-h-7 rounded-full border px-2.5 text-xs tabular-nums transition-colors ${
                  quick === time
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                    : "border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
                }`}
              >
                {quick}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--border)] pt-2">
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => {
              const now = isoDay(today);
              setView({ year: today.getFullYear(), month: today.getMonth() });
              setDay(now);
            }}
            className="min-h-9 rounded-lg px-2.5 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent-soft)]"
          >
            Hôm nay
          </button>
          {clearable && value ? (
            <button
              type="button"
              onClick={() => {
                onPick("");
                onClose();
              }}
              className="min-h-9 rounded-lg px-2.5 text-sm text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--bad)]"
            >
              Xoá
            </button>
          ) : null}
        </div>
        {kind === "datetime" ? (
          <button
            type="button"
            onClick={onClose}
            className="min-h-9 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            Xong
          </button>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
