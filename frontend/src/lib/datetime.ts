/**
 * The one way the app writes a date (2026-10-21): `dd/mm/yyyy HH:mm`, 24-hour
 * clock, Vietnam time - whatever the browser's own locale or zone. Every
 * formatter in `lib/` goes through these two.
 *
 * `toLocaleString("vi-VN")` is not used for this on purpose: it puts the time
 * before the date ("14:30 15/10/2026").
 */

const ZONE = "Asia/Ho_Chi_Minh";

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: ZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function parts(format: Intl.DateTimeFormat, value: Date): Record<string, string> {
  const found: Record<string, string> = {};
  for (const part of format.formatToParts(value)) found[part.type] = part.value;
  return found;
}

/** A date-only value (`YYYY-MM-DD`) is a calendar day, not an instant. */
function dayOnly(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  return match ? `${match[3]}/${match[2]}/${match[1]}` : null;
}

function read(value: string | Date): Date | null {
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** `dd/mm/yyyy HH:mm` (Vietnam time); "—" when empty, the input when unreadable. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  if (typeof value === "string" && dayOnly(value)) return dayOnly(value) as string;
  const parsed = read(value);
  if (!parsed) return String(value);
  const p = parts(DATE_TIME, parsed);
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`;
}

/**
 * `dd/mm HH:mm` - no year, for tight table cells (the order board); the full
 * {@link formatDateTime} goes in the tooltip.
 */
export function formatShortDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  if (typeof value === "string" && dayOnly(value)) return formatShortDate(value);
  const parsed = read(value);
  if (!parsed) return String(value);
  const p = parts(DATE_TIME, parsed);
  return `${p.day}/${p.month} ${p.hour}:${p.minute}`;
}

/** `dd/mm` - a day with no year, for tight table cells. */
export function formatShortDate(value: string | Date | null | undefined): string {
  const full = formatDate(value);
  const match = /^(\d{2}\/\d{2})\/\d{4}$/.exec(full);
  return match ? match[1] : full;
}

/** `dd/mm/yyyy` (the Vietnam calendar day of an instant, or a bare day as is). */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  if (typeof value === "string" && dayOnly(value)) return dayOnly(value) as string;
  const parsed = read(value);
  if (!parsed) return String(value);
  const p = parts(DATE_TIME, parsed);
  return `${p.day}/${p.month}/${p.year}`;
}
