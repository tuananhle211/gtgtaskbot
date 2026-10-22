import { formatDateTime } from "@/lib/datetime";
/**
 * ORD deadlines: the status words, how a deadline is written, and the
 * conversions an `<input type="datetime-local">` needs.
 *
 * The server sends ISO-8601 with an offset and a `deadline_status`; the
 * client never decides a status itself. The one thing it computes is the
 * warning in the assign dialog - a node deadline after the orderer's wished
 * one is allowed, only counted, so the person choosing it is told by how much.
 */

/** `deadline_status` in words. */
export const DEADLINE_STATUS_LABELS: Record<string, string> = {
  ON_TRACK: "Còn hạn",
  DUE_SOON: "Sắp tới hạn",
  OVERDUE: "Quá hạn",
  MET: "Đúng hạn",
  MISSED: "Trễ hạn",
};

export function deadlineStatusLabel(status: string | null | undefined): string {
  if (!status) return "";
  return DEADLINE_STATUS_LABELS[status] ?? status;
}

/** A deadline as "dd/mm/yyyy HH:mm", Vietnam time; "—" when there is none. */
export function formatDeadline(value: string | null | undefined): string {
  return formatDateTime(value);
}

/**
 * A `datetime-local` value (the browser's own clock, "YYYY-MM-DDTHH:mm") as
 * ISO-8601 for the API; null when empty or unreadable.
 */
export function toIsoFromLocal(value: string | null | undefined): string | null {
  const time = readTime(value);
  return time === null ? null : new Date(time).toISOString();
}

/**
 * What a deadline box starts on when nothing is set yet: today, so only the
 * day (or the hour) needs changing. 17:30 - the end of the working day -
 * while that is still ahead, else 23:59, so the default is never in the past.
 */
export function defaultDeadlineInput(now: Date = new Date()): string {
  const day = new Date(now);
  day.setHours(17, 30, 0, 0);
  if (day.getTime() <= now.getTime()) day.setHours(23, 59, 0, 0);
  return toLocalInput(day.toISOString());
}

/** An ISO datetime as a `datetime-local` value in the browser's clock. */
export function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}` +
    `T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  );
}

/**
 * Milliseconds since the epoch for an ISO string (with an offset) or a
 * `datetime-local` value (no offset: the browser's clock); null when empty.
 */
function readTime(value: string | null | undefined): number | null {
  if (!value || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
}

/** Whether a `datetime-local` value (or ISO string) lies before `now`. */
export function isPast(value: string | null | undefined, now: Date = new Date()): boolean {
  const time = readTime(value);
  return time !== null && time < now.getTime();
}

/** A length of time in words: "1 ngày 3 giờ", "5 giờ 20 phút", "45 phút". */
export function formatSpan(ms: number): string {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return hours > 0 ? `${days} ngày ${hours} giờ` : `${days} ngày`;
  if (hours > 0) return mins > 0 ? `${hours} giờ ${mins} phút` : `${hours} giờ`;
  return `${mins} phút`;
}

/**
 * The warning when a chosen deadline is after the desired one, e.g.
 * "Vượt deadline mong muốn 1 ngày 3 giờ"; null when it is not (or either is
 * missing). Both are ISO strings or `datetime-local` values.
 */
export function overDesired(
  deadline: string | null | undefined,
  desired: string | null | undefined,
): string | null {
  const chosen = readTime(deadline);
  const wished = readTime(desired);
  if (chosen === null || wished === null || chosen <= wished) return null;
  return `Vượt deadline mong muốn ${formatSpan(chosen - wished)}`;
}

/** A token amount as written: up to 2 decimals, no trailing zeros. */
export function formatTokens(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return String(Math.round(number * 100) / 100);
}

/** What an assignee option carries about a person's load (all optional). */
export interface AssigneeLoad {
  name: string;
  tokens_budget_today?: number | null;
  tokens_left_today?: number | null;
  tokens_open?: number | null;
  open_tasks?: number | null;
  assigned_count?: number;
  doing_count?: number;
}

/**
 * Everyone's workload, shown on every assignee picker in the app (0069):
 * "· giao 2 · đang làm 3" (ORD + PR). Empty string when both are zero, so a
 * fresh person reads just their name.
 */
export function workloadLabel(person: AssigneeLoad): string {
  const assigned = person.assigned_count ?? 0;
  const doing = person.doing_count ?? 0;
  if (assigned === 0 && doing === 0) return "";
  return `giao ${assigned} · đang làm ${doing}`;
}

/**
 * An assignee as the assign dialog lists them:
 * "Quỳnh Như · còn 3/8 hôm nay · đang ôm 5 (2 task)", or "… · vượt 2/8 hôm
 * nay · …" once today's budget is spent past zero. Just the name when the
 * server sent no load.
 */
export function assigneeLoadLabel(person: AssigneeLoad): string {
  const parts = [person.name];
  const work = workloadLabel(person);
  if (work) parts.push(work);
  const left = person.tokens_left_today;
  if (left !== null && left !== undefined) {
    const budget =
      person.tokens_budget_today !== null && person.tokens_budget_today !== undefined
        ? `/${formatTokens(person.tokens_budget_today)}`
        : "";
    parts.push(
      left < 0
        ? `⚡ vượt ${formatTokens(-left)}${budget} hôm nay`
        : `⚡ còn ${formatTokens(left)}${budget} hôm nay`,
    );
  }
  // null = the server sent no load (effort is off), like a missing field.
  if (person.tokens_open != null || person.open_tasks != null) {
    parts.push(
      `⚡ đang ôm ${formatTokens(person.tokens_open ?? 0)} (${person.open_tasks ?? 0} task)`,
    );
  }
  return parts.join(" · ");
}

/** Whether a person is past today's budget (shown red). */
export function isOverBudget(person: AssigneeLoad): boolean {
  return (person.tokens_left_today ?? 0) < 0;
}

/** A token input's text as a number for the API; null when empty or invalid (0..999.99). */
export function parseTokens(value: string | null | undefined): number | null {
  if (value === null || value === undefined || !value.trim()) return null;
  const number = Number(value.replace(",", "."));
  if (!Number.isFinite(number) || number < 0 || number > 999.99) return null;
  return Math.round(number * 100) / 100;
}
