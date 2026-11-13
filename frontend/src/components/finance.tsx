"use client";

/** Small pieces shared by the Tài chính screens. */

import { SecondaryButton } from "@/components/pr";
import { shiftMonth } from "@/lib/hr-attendance";

export function TabStrip<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: readonly (readonly [T, string])[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label={label}>
      {tabs.map(([key, text]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          onClick={() => onChange(key)}
          className={`min-h-11 rounded-lg border px-3 text-sm ${
            value === key
              ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
              : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function MonthSwitch({ month, onChange }: { month: string; onChange: (month: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <SecondaryButton type="button" onClick={() => onChange(shiftMonth(month, -1))} aria-label="Tháng trước">
        ‹
      </SecondaryButton>
      <span className="min-w-28 text-center font-semibold">
        Tháng {month.slice(5, 7)}/{month.slice(0, 4)}
      </span>
      <SecondaryButton type="button" onClick={() => onChange(shiftMonth(month, 1))} aria-label="Tháng sau">
        ›
      </SecondaryButton>
    </div>
  );
}

export const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

export function statusTone(status: string): "good" | "bad" | "warn" | "neutral" {
  if (status === "PAID") return "good";
  if (status === "REJECTED" || status === "CANCELLED") return "bad";
  if (status === "RETURNED") return "warn";
  return "neutral";
}
