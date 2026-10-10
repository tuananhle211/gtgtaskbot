import { formatTokens } from "@/lib/deadline";

/** The lightning bolt every token figure carries. */
export function BoltIcon({ className = "size-4" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      <path d="M13.5 2 4 13.5h6.5L9.5 22 20 9.5h-6.5L13.5 2Z" />
    </svg>
  );
}

/**
 * A token figure, made to stand out: a bolt, bold and larger, on the amber
 * "effort" colour. `size="sm"` for dense cells, `md` elsewhere.
 */
export function TokenBadge({
  value,
  suffix = "token",
  size = "md",
}: {
  value: number | string | null | undefined;
  suffix?: string;
  size?: "sm" | "md";
}) {
  if (value === null || value === undefined) return null;
  const small = size === "sm";
  return (
    <span
      data-token-badge
      className={`inline-flex items-center gap-1 rounded-full border border-[var(--warn)]/40 bg-[var(--warn-soft)] font-bold tabular-nums text-[var(--warn)] ${
        small ? "px-2 py-0.5 text-[13px]" : "px-2.5 py-1 text-[15px]"
      }`}
    >
      <BoltIcon className={small ? "size-3.5" : "size-4"} />
      {formatTokens(value)}
      {suffix ? <span className="font-semibold"> {suffix}</span> : null}
    </span>
  );
}
