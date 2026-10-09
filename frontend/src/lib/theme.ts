/**
 * Light / dark mode. No choice = follow the system. The choice is a cookie the
 * server sets (`POST /theme`), so the root layout renders `<html data-theme>`
 * before the first paint - no flash, and no script ever touches a cookie.
 */
export type Theme = "light" | "dark";

export const THEME_COOKIE = "meobot_theme";

export function parseTheme(value: string | undefined | null): Theme | null {
  return value === "light" || value === "dark" ? value : null;
}
