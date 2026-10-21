import { formatShortDateTime } from "@/lib/datetime";
import { deadlineStatusLabel, formatDeadline } from "@/lib/deadline";
import { deadlineColor } from "@/lib/status-colors";

/**
 * A deadline, coloured by its status (amber close, red past or missed, green
 * met), with the status in words for a screen reader and on hover.
 */
export function DeadlineBadge({
  at,
  status,
  prefix = "Hạn",
  short = false,
}: {
  at: string | null | undefined;
  status?: string | null;
  prefix?: string;
  /** Table cells: "dd/mm HH:mm" (no year), the full date on hover. */
  short?: boolean;
}) {
  if (!at) return null;
  const words = deadlineStatusLabel(status);
  const title = short
    ? [formatDeadline(at), words].filter(Boolean).join(" · ")
    : words || undefined;
  return (
    <span
      className={`st st-nowrap st-${deadlineColor(status)}`}
      title={title}
      data-deadline-status={status ?? undefined}
    >
      <span>
        {prefix} {short ? formatShortDateTime(at) : formatDeadline(at)}
        {words ? <span className="sr-only"> · {words}</span> : null}
      </span>
    </span>
  );
}
