/**
 * The unit a screen is looking at, and the words for it.
 *
 * Every shared screen (dashboard, task table) takes `?unit=PR|ADS|ALL` on the
 * URL - the filter state lives there, like every other filter - and falls back
 * to the person's default unit from `/api/units/me`. Nothing here decides
 * what a person may see: the server answers 404 for a unit they are not tagged
 * into, and the switch only lists the units it returned.
 */

import type { UnitsMe } from "@/lib/api";

export const ALL_UNITS = "ALL";

/** The unit the URL names, or the server's default for this person. */
export function currentUnit(search: URLSearchParams | null, me: UnitsMe | undefined): string {
  const asked = search?.get("unit")?.trim().toUpperCase() ?? "";
  if (asked) return asked;
  return me?.default_unit ?? "PR";
}

/**
 * A stream's name as a person reads it. The codes stay `PR` / `ADS` in URLs,
 * params and the API; only the words changed ("Luồng", and Ads became ORD).
 * Known codes use these words whatever an older API still sends.
 */
export const STREAM_NAMES: Record<string, string> = {
  PR: "Luồng PR",
  ADS: "Luồng Order (ORD)",
};

/** The stream's name: ours for a known code, else the server's label. */
export function unitName(code: string, label?: string | null): string {
  return STREAM_NAMES[code] ?? label ?? code;
}

/** The chip text: the server's `short_label`, else ORD for ADS, else the code. */
export function unitShortLabel(code: string, short?: string | null): string {
  if (short) return short;
  if (code === "ADS") return "ORD";
  if (code === ALL_UNITS) return "PR + ORD";
  return code;
}

/** The chip class: the CSS names keep the codes. */
export function unitTagClass(code: string): string {
  return code === "ADS" ? "unit-tag-ads" : "unit-tag-pr";
}

/**
 * No stream at all: a new account nobody has tagged yet. OWNER and ADMIN see
 * every stream whatever their tags, so they are never "untagged" here.
 */
export function isUntagged(me: UnitsMe | undefined): boolean {
  if (!me || me.can_view_all) return false;
  return me.is_untagged === true || me.units.length === 0;
}

/**
 * Whether this person may tag / untag members in a stream. An API that does
 * not send `can_tag` yet is read as before: the unit's administrators.
 */
export function canTagIn(me: UnitsMe | undefined, code: string): boolean {
  if (!me) return false;
  return (me.can_tag ?? me.can_admin).includes(code);
}

/** The streams this person may tag in (see `canTagIn`). */
export function taggableUnits(me: UnitsMe | undefined): string[] {
  if (!me) return [];
  return me.can_tag ?? me.can_admin;
}

/** The choices the switch offers, in order: tagged streams, then "Tất cả". */
export function unitChoices(me: UnitsMe | undefined): Array<{ value: string; label: string }> {
  if (!me) return [];
  const choices = me.units.map((unit) => ({
    value: unit.code,
    label: unitName(unit.code, unit.label),
  }));
  if (me.can_view_all && choices.length > 1) {
    choices.push({ value: ALL_UNITS, label: "Tất cả" });
  }
  return choices;
}

/**
 * A plain staff member in this stream: base role EMPLOYEE, and in the stream
 * neither its head nor a function's lead (OWNER / ADMIN never are). Their task
 * table opens on "Task của tôi", the rows waiting on them first.
 */
export function isPlainStaff(
  me: UnitsMe | undefined,
  sessionRole: string | undefined,
  code: string,
): boolean {
  if (!me || !sessionRole || me.can_view_all) return false;
  if (sessionRole !== "EMPLOYEE") return false;
  const entry = me.units.find((unit) => unit.code === code);
  if (!entry) return false;
  return entry.role !== "HEAD" && !entry.is_lead;
}

/** Whether this person is tagged into the unit (the OWNER: every unit). */
export function hasUnit(me: UnitsMe | undefined, code: string): boolean {
  return Boolean(me?.units.some((unit) => unit.code === code));
}

/** The person's tag in one unit, if any. */
export function unitEntry(me: UnitsMe | undefined, code: string) {
  return me?.units.find((unit) => unit.code === code) ?? null;
}

/** `YYYY-MM-DD` for a date input, in the browser's local calendar. */
export function isoDay(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function firstOfMonth(value: Date): string {
  return isoDay(new Date(value.getFullYear(), value.getMonth(), 1));
}

export function lastOfMonth(value: Date): string {
  return isoDay(new Date(value.getFullYear(), value.getMonth() + 1, 0));
}
