import type { Gender } from "@/lib/api";

/**
 * Words and dates for "Order sinh nhật": the gender codes the API speaks
 * (`MALE` / `FEMALE`) and a birthday as people read it on the board (dd/mm -
 * the year is the person's, not the order's).
 */

export const GENDERS: Array<{ value: Gender; label: string }> = [
  { value: "MALE", label: "Nam" },
  { value: "FEMALE", label: "Nữ" },
];

/** "Nam" / "Nữ"; an unknown code is shown as it came, nothing as a dash. */
export function genderLabel(value: string | null | undefined): string {
  if (!value) return "–";
  return GENDERS.find((gender) => gender.value === value)?.label ?? value;
}

/**
 * `YYYY-MM-DD` as `dd/mm`. Read from the text, not through a `Date`: a
 * birthday is a calendar day and must not move with the browser's time zone.
 */
export function formatBirthday(value: string | null | undefined): string {
  if (!value) return "–";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[3]}/${match[2]}` : value;
}

/** `YYYY-MM-DD` as `dd/mm/yyyy`, read the same way. */
export function formatBirthdayFull(value: string | null | undefined): string {
  if (!value) return "–";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value;
}
