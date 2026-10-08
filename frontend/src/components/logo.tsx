import { useId } from "react";

/**
 * The TasksBot identity: a friendly robot head on a rounded-square badge, whose
 * visor shows a check-mark - a task done.
 *
 * - The badge is the app accent blue (#1f6feb), fixed rather than `var(--accent)`
 *   so the mark looks the same in light and dark and matches the favicon.
 * - The check runs teal (PR, #14b8a6) into orange (Ads, #ea580c): the two units
 *   one tool serves. The antenna tip is the teal end.
 * - Drawn on a 32-unit grid with heavy strokes so it still reads at 16px.
 *   `src/app/icon.svg` is the same drawing; change both together.
 *
 * Inline SVG on purpose: no request, nothing for the CSP to allow, and it
 * scales with `size`. The gradient id comes from `useId`, so several marks on
 * one page (sidebar + mobile bar) never point at a gradient inside a hidden one.
 */
export function LogoMark({
  size = 32,
  className,
  title,
}: {
  size?: number;
  className?: string;
  /** Gives the mark an accessible name. Without it the mark is decorative. */
  title?: string;
}) {
  const gradientId = `tb-accent-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const labelled = Boolean(title);
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={className}
      aria-hidden={labelled ? undefined : true}
      role={labelled ? "img" : undefined}
      aria-label={labelled ? title : undefined}
      focusable="false"
      data-logo-mark=""
    >
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="11.4" y1="0" x2="21" y2="0">
          <stop offset="0.15" stopColor="#14b8a6" />
          <stop offset="0.85" stopColor="#ea580c" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="#1f6feb" />
      <rect x="15.1" y="6" width="1.8" height="4.5" fill="#fff" />
      <circle cx="16" cy="5.4" r="2.2" fill="#14b8a6" />
      <rect x="2.8" y="15" width="3.2" height="7.5" rx="1.6" fill="#fff" />
      <rect x="26" y="15" width="3.2" height="7.5" rx="1.6" fill="#fff" />
      <rect x="5" y="9.6" width="22" height="17.6" rx="5.6" fill="#fff" />
      <rect x="7.6" y="12.6" width="16.8" height="11.6" rx="4.6" fill="#0b1f4a" />
      <path
        d="M11.6 18.6 L14.9 21.7 L20.6 15.3"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="3.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * The mark with the "TasksBot" wordmark and an optional subtitle line.
 *
 * The wordmark uses `var(--text)`, so it follows the light/dark theme while the
 * mark keeps its own colours. `textClassName` lets a caller hide the words (the
 * collapsed sidebar) without hiding the mark.
 */
export function Logo({
  size = 32,
  subtitle,
  className,
  textClassName,
}: {
  size?: number;
  subtitle?: string;
  className?: string;
  textClassName?: string;
}) {
  return (
    <span className={`inline-flex min-w-0 items-center gap-2.5 ${className ?? ""}`}>
      <LogoMark size={size} className="shrink-0" />
      <span className={`flex min-w-0 flex-col leading-tight ${textClassName ?? ""}`}>
        <span className="truncate text-sm font-semibold tracking-tight text-[var(--text)]">
          TasksBot
        </span>
        {subtitle ? (
          <span className="truncate text-xs text-[var(--text-muted)]">{subtitle}</span>
        ) : null}
      </span>
    </span>
  );
}
