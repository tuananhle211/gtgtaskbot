/**
 * The ORD token grid's calendar: weeks run Monday..Sunday and every date is a
 * `YYYY-MM-DD` Vietnam calendar day, as `GET /api/units/{code}/effort` sends
 * them. The arithmetic is done in UTC so the browser's own zone never shifts
 * a day.
 */

const WEEKDAYS = ["CN", "T2", "T3", "T4", "T5", "T6", "T7"];

function parseDay(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, (month ?? 1) - 1, date ?? 1));
}

function formatDay(value: Date): string {
  return value.toISOString().slice(0, 10);
}

/** `day` moved by `count` days. */
export function addDays(day: string, count: number): string {
  const value = parseDay(day);
  value.setUTCDate(value.getUTCDate() + count);
  return formatDay(value);
}

/** The Monday of the week `day` falls in. */
export function mondayOf(day: string): string {
  const weekday = parseDay(day).getUTCDay();
  return addDays(day, weekday === 0 ? -6 : 1 - weekday);
}

/** "T2 05/10" (CN for Sunday). */
export function dayHeading(day: string): string {
  const value = parseDay(day);
  const dd = String(value.getUTCDate()).padStart(2, "0");
  const mm = String(value.getUTCMonth() + 1).padStart(2, "0");
  return `${WEEKDAYS[value.getUTCDay()]} ${dd}/${mm}`;
}

/** Whether a well-formed `YYYY-MM-DD`. */
export function isDay(value: string | null | undefined): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value));
}
