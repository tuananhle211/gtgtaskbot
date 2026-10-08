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

/** The choices the switch offers, in order. */
export function unitChoices(me: UnitsMe | undefined): Array<{ value: string; label: string }> {
  if (!me) return [];
  const choices = me.units.map((unit) => ({ value: unit.code, label: unit.label }));
  if (me.can_view_all && choices.length > 1) {
    choices.push({ value: ALL_UNITS, label: "Tất cả" });
  }
  return choices;
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
