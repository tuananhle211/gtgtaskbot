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
}: {
  at: string | null | undefined;
  status?: string | null;
  prefix?: string;
}) {
  if (!at) return null;
  const words = deadlineStatusLabel(status);
  return (
    <span
      className={`st st-${deadlineColor(status)}`}
      title={words || undefined}
      data-deadline-status={status ?? undefined}
    >
      <span>
        {prefix} {formatDeadline(at)}
        {words ? <span className="sr-only"> · {words}</span> : null}
      </span>
    </span>
  );
}
