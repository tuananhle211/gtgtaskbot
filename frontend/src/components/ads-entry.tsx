"use client";

import type { AdsEntryLine } from "@/lib/api";

/**
 * The amount grid shared by "Nhập doanh thu" (one day) and "KPI" (one month):
 * one line per person (and the ◆ sources / the team), an amount typed in the
 * Vietnamese format ("1.500.000"). A blank cell means "not entered", which is
 * not the same as 0.
 *
 * With `split`, a person's line has two cells: "Doanh số SG" (customers served
 * at the HCM site) and "Doanh số HN" (Sài Gòn customers served in Hà Nội - it
 * counts for their KPI, not for the HCM site). The HN cell travels as
 * `<key>:hn`.
 *
 * A line `from_crm` (0083) shows the CRM's thực thu, locked, tagged "CRM".
 */

export const HN_SUFFIX = ":hn";

/** Digits typed -> "1.500.000"; "" stays "". */
export function formatTyped(value: string): string {
  const digits = value.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
  return digits ? Number(digits).toLocaleString("vi-VN") : "";
}

function shown(amount: number | null | undefined): string {
  return amount === null || amount === undefined
    ? ""
    : Math.round(amount).toLocaleString("vi-VN");
}

export function draftOf(lines: AdsEntryLine[]): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const line of lines) {
    draft[line.key] = shown(line.amount);
    if (line.user_id) draft[`${line.key}${HN_SUFFIX}`] = shown(line.amount_hn);
  }
  return draft;
}

/** The cells whose typed value differs from what is stored, as the API wants them. */
export function changesOf(
  lines: AdsEntryLine[],
  draft: Record<string, string>,
  split = false,
): Record<string, string | null> {
  const changes: Record<string, string | null> = {};
  const compare = (key: string, stored: number | null | undefined) => {
    const typed = (draft[key] ?? "").replace(/\D/g, "");
    const before =
      stored === null || stored === undefined ? "" : String(Math.round(stored));
    if (typed !== before) changes[key] = typed === "" ? null : typed;
  };
  for (const line of lines) {
    if (line.from_crm) continue;
    compare(line.key, line.amount);
    if (split && line.user_id)
      compare(`${line.key}${HN_SUFFIX}`, line.amount_hn);
  }
  return changes;
}

const INPUT =
  "min-h-11 w-44 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-right text-sm tabular-nums text-[var(--text)] disabled:bg-[var(--surface-muted)]";

export function EntryGrid({
  lines,
  draft,
  onChange,
  disabled,
  amountLabel,
  split = false,
}: {
  lines: AdsEntryLine[];
  draft: Record<string, string>;
  onChange: (key: string, value: string) => void;
  disabled: boolean;
  amountLabel: string;
  split?: boolean;
}) {
  const cell = (key: string, label: string, locked = false) => (
    <input
      aria-label={label}
      inputMode="numeric"
      value={draft[key] ?? ""}
      disabled={disabled || locked}
      title={locked ? "Thực thu lấy từ CRM, không nhập tay" : undefined}
      placeholder="Chưa nhập"
      onChange={(event) => onChange(key, formatTyped(event.target.value))}
      className={INPUT}
    />
  );
  return (
    <div className="panel table-scroll-x">
      <table
        className={`table-dense w-full ${split ? "min-w-[680px]" : "min-w-[520px]"}`}
      >
        <thead>
          <tr>
            <th className="text-left">Người / nguồn</th>
            <th className="text-right">
              {split ? "Doanh số SG" : amountLabel}
            </th>
            {split ? <th className="text-right">Doanh số HN</th> : null}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.key} data-testid={`entry-${line.key}`}>
              <td>
                <div
                  className={
                    line.source || line.key === "team"
                      ? "font-semibold"
                      : "font-medium"
                  }
                >
                  {line.label}
                  {line.from_crm ? (
                    <span className="st st-blue ml-2 align-middle">CRM</span>
                  ) : null}
                </div>
                {line.member_code ? (
                  <div className="text-xs text-[var(--text-muted)]">
                    {line.member_code}
                  </div>
                ) : null}
              </td>
              <td className="text-right">
                {cell(
                  line.key,
                  split && line.user_id
                    ? `Doanh số SG ${line.label}`
                    : `${amountLabel} ${line.label}`,
                  line.from_crm,
                )}
              </td>
              {split ? (
                <td className="text-right">
                  {line.user_id ? (
                    cell(
                      `${line.key}${HN_SUFFIX}`,
                      `Doanh số HN ${line.label}`,
                      line.from_crm,
                    )
                  ) : (
                    <span className="text-[var(--text-muted)]">–</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
