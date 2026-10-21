import { formatDate, formatDateTime } from "@/lib/datetime";
import type { ResourceAlert, ResourceOptions, ResourceRow, UnitsMe } from "@/lib/api";

/**
 * "Tài nguyên" (0065): the words and small rules every resources screen shares.
 *
 * The server decides everything that matters (scope, who may write, what an
 * alert is); this file only draws the same thing the same way on every screen.
 */

/** The screens of the module, in menu order, and the access flag each needs. */
export type ResourceScreenIcon =
  | "inventory"
  | "mine"
  | "request"
  | "lookup"
  | "report"
  | "import"
  | "settings";

export const RESOURCE_SCREENS: ReadonlyArray<{
  href: string;
  label: string;
  flag: "view" | "edit" | "import_excel" | "settings" | "report";
  /** The sidebar glyph (`Icon` in `components/shell.tsx`), one per screen. */
  icon: ResourceScreenIcon;
}> = [
  { href: "/resources", label: "Danh sách tài nguyên", flag: "view", icon: "inventory" },
  { href: "/resources/mine", label: "Tài nguyên của tôi", flag: "view", icon: "mine" },
  { href: "/resources/requests", label: "Đề xuất tài nguyên", flag: "view", icon: "request" },
  { href: "/resources/people", label: "Tra cứu nhân viên", flag: "report", icon: "lookup" },
  { href: "/resources/reports", label: "Báo cáo tài nguyên", flag: "report", icon: "report" },
  { href: "/resources/import", label: "Nhập Excel", flag: "import_excel", icon: "import" },
  { href: "/resources/settings", label: "Cài đặt tài nguyên", flag: "settings", icon: "settings" },
];

/** The module's screens this person may open. */
export function resourceScreens(me: UnitsMe | undefined) {
  const access = me?.resources;
  if (!access?.view) return [];
  return RESOURCE_SCREENS.filter((screen) => access[screen.flag]);
}

/** A row's tint: red for a `bad` alert, amber for a `warn` one, none otherwise. */
export function rowTone(row: Pick<ResourceRow, "flagged" | "alerts">): "bad" | "warn" | null {
  if (row.flagged) return "bad";
  return row.alerts.length > 0 ? "warn" : null;
}

export function alertTone(alert: ResourceAlert): "bad" | "warn" {
  return alert.level === "bad" ? "bad" : "warn";
}

/** The pill tone of a status, by its group. */
export function statusTone(group: string | null | undefined): "good" | "warn" | "bad" | "neutral" {
  switch (group) {
    case "NORMAL":
      return "good";
    case "ATTENTION":
      return "warn";
    case "LOST":
      return "bad";
    default:
      return "neutral";
  }
}

export function employmentTone(status: string | null | undefined): "good" | "warn" | "bad" | "neutral" {
  switch (status) {
    case "WORKING":
      return "good";
    case "PROBATION":
    case "ON_LEAVE":
    case "MOVED":
      return "warn";
    case "LEFT":
      return "bad";
    default:
      return "neutral";
  }
}

/** The entries of one pick-list, active ones (plus the one named by `keep`). */
export function optionsOf(
  options: ResourceOptions | undefined,
  kind: string,
  keep?: string | null,
) {
  const list = options?.kinds.find((item) => item.kind === kind)?.options ?? [];
  return list.filter((option) => option.active || option.id === keep);
}

export function optionKindLabel(options: ResourceOptions | undefined, kind: string): string {
  return options?.kinds.find((item) => item.kind === kind)?.label ?? kind;
}

const SECRET_LIKE =
  /(pass\s*[:=]|password|passwd|m[aậ]t\s*kh[aẩ]u|mk\s*[:=]|m[aã]\s*kh[oô]i\s*ph[uụ]c|recovery\s*code|backup\s*code|2fa\s*[:=]|otp\s*[:=]|secret)/i;

/**
 * Whether free text reads like a password, a 2FA or a recovery code - the one
 * thing the module must never hold. The same rule as the server's; a warning
 * under the field, never a refusal.
 */
export function looksLikeSecret(text: string | null | undefined): boolean {
  return Boolean(text) && SECRET_LIKE.test(text ?? "");
}

export const SECRET_WARNING =
  "Nội dung này giống mật khẩu / mã khôi phục. Phần mềm không lưu thông tin đăng nhập: hãy giữ ở trình quản lý mật khẩu.";

/** `2026-10-15T...` → `15/10/2026`; a bare day the same; nothing → "—". */
export function formatResourceDay(value: string | null | undefined): string {
  return formatDate(value);
}

export function formatResourceWhen(value: string | null | undefined): string {
  return formatDateTime(value);
}

/** `YYYY-MM` of a date, in local time. */
export function monthOf(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

/** The current filter as `?key=value` for the export link and the URL. */
export function cleanFilters<T extends Record<string, unknown>>(filters: T): Partial<T> {
  const out: Partial<T> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      (out as Record<string, unknown>)[key] = value;
    }
  }
  return out;
}

/** The "Link" column is free text (2026-10-20): an anchor only when it is a web address. */
export function isWebLink(value: string | null | undefined): boolean {
  return /^https?:\/\//i.test((value ?? "").trim());
}

/** A request stage's pill tone. */
export function requestTone(stage: string): "good" | "warn" | "bad" | "neutral" {
  if (stage === "COMPLETED") return "good";
  if (stage === "REJECTED") return "bad";
  if (stage === "CANCELLED") return "neutral";
  return "warn";
}

/** "6.000.000 đ". */
export function formatVnd(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${Math.round(value).toLocaleString("vi-VN")} đ`;
}
