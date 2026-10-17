import { useId } from "react";

/**
 * The APM Management identity, drawn from the Apexmed guideline: the "A" -
 * a peak, an arrow pointing up ("khát vọng vươn lên") - with the medical cross
 * at its heart ("con người và sức khoẻ là trọng tâm").
 *
 * - The badge is the Apexmed navy (#132863), fixed rather than `var(--accent)`
 *   so the mark looks the same in light and dark and matches the favicon.
 * - The cross runs blue pastel (PR, #afe8ff) into the brand magenta (ORD,
 *   #e86a8e): the two streams one tool serves.
 * - Drawn on a 32-unit grid with heavy shapes so it still reads at 16px.
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
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="14" y1="20.6" x2="18" y2="24.6">
          <stop offset="0" stopColor="#afe8ff" />
          <stop offset="1" stopColor="#e86a8e" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="#132863" />
      <polygon points="16,5.5 27,26.5 21.6,26.5 16,15.8 10.4,26.5 5,26.5" fill="#fff" />
      <path
        d="M16 20.8v3.6M14.2 22.6h3.6"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * The mark with the "APM Management" wordmark and an optional subtitle line.
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
        <span className="truncate font-display text-sm font-semibold tracking-tight text-[var(--text)]">
          APM Management
        </span>
        {subtitle ? (
          <span className="truncate text-xs text-[var(--text-muted)]">{subtitle}</span>
        ) : null}
      </span>
    </span>
  );
}
