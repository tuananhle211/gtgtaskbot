"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsBudgetMonth } from "@/lib/api";
import { formatTyped } from "@/components/ads-entry";
import { BudgetBoard, weekDays } from "@/components/ads-budget";
import { money } from "@/components/ads";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Phòng Ads › Ngân sách tuần" (0082): the Trưởng phòng sets each
 * marketer's ad budget at the start of the month, in four slots - days 1-7,
 * 8-14, 15-21 and the 22nd to the month's end. Under the form, the same
 * month against what was spent ("Vượt ngưỡng" / "Chưa đến ngưỡng").
 */
export default function AdsBudgetPage() {
  const [month, setMonth] = useState("");
  useEffect(() => {
    const picked = new URLSearchParams(window.location.search).get("month");
    if (picked && /^\d{4}-\d{2}$/.test(picked)) setMonth(picked);
  }, []);
  const budget = useQuery({
    queryKey: ["ads", "budget", month, "edit"],
    queryFn: () => api.adsBudget(month || undefined),
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title="Ngân sách tuần"
        subtitle="Ngân sách chạy ads của từng người theo 4 tuần trong tháng: 3 tuần đầu 7 ngày, tuần 4 là các ngày còn lại."
      />
      <label className="flex w-fit flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
        <span>Tháng</span>
        <input
          type="month"
          aria-label="Tháng"
          value={month || budget.data?.month || ""}
          onChange={(event) => setMonth(event.target.value)}
          className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
        />
      </label>
      {budget.isPending ? <Loading label="Đang tải ngân sách…" /> : null}
      {budget.isError ? (
        <ErrorBox error={budget.error} onRetry={() => budget.refetch()} />
      ) : null}
      {budget.data ? (
        <>
          <Editor key={budget.data.month} data={budget.data} />
          <BudgetBoard
            month={budget.data.month}
            title="Đối chiếu chi tiêu với ngân sách"
            editLink={false}
          />
        </>
      ) : null}
    </div>
  );
}

const key = (userId: string, week: number) => `${userId}:${week}`;

function shown(amount: number | null): string {
  return amount === null ? "" : Math.round(amount).toLocaleString("vi-VN");
}

function draftOf(data: AdsBudgetMonth): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const line of data.lines)
    for (const cell of line.cells)
      if (line.user_id) draft[key(line.user_id, cell.week)] = shown(cell.budget);
  return draft;
}

function digits(value: string | undefined): string {
  return (value ?? "").replace(/\D/g, "");
}

function changesOf(
  data: AdsBudgetMonth,
  draft: Record<string, string>,
): Record<string, string | null> {
  const changes: Record<string, string | null> = {};
  for (const line of data.lines) {
    if (!line.user_id) continue;
    for (const cell of line.cells) {
      const name = key(line.user_id, cell.week);
      const typed = digits(draft[name]);
      const before = cell.budget === null ? "" : String(Math.round(cell.budget));
      if (typed !== before) changes[name] = typed === "" ? null : typed;
    }
  }
  return changes;
}

const INPUT =
  "min-h-11 w-36 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-right text-sm tabular-nums text-[var(--text)]";

function Editor({ data }: { data: AdsBudgetMonth }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => draftOf(data));
  useEffect(() => setDraft(draftOf(data)), [data]);
  const changes = changesOf(data, draft);
  const changed = Object.keys(changes).length;
  const save = useMutation({
    mutationFn: () => api.saveAdsBudget(data.month, changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(["ads", "budget", saved.month, "edit"], saved);
      void queryClient.invalidateQueries({ queryKey: ["ads", "budget"] });
    },
  });
  const [year, monthNumber] = data.month.split("-");
  const sum = (keys: string[]) => {
    const typed = keys.map((name) => digits(draft[name])).filter(Boolean);
    return typed.length
      ? typed.reduce((total, value) => total + Number(value), 0)
      : null;
  };
  const people = data.lines.filter((line) => line.user_id);
  const disabled = !data.can_edit || save.isPending;
  return (
    <section aria-label="Nhập ngân sách" className="space-y-3">
      <div className="panel table-scroll-x">
        <table className="table-dense w-full min-w-[860px]">
          <thead>
            <tr>
              <th className="text-left">Người</th>
              {data.weeks.map((week) => (
                <th key={week.index} className="text-right">
                  <div>Tuần {week.index}</div>
                  <div className="font-normal normal-case tracking-normal">
                    {weekDays(week)}
                  </div>
                </th>
              ))}
              <th className="text-right">Cả tháng</th>
            </tr>
          </thead>
          <tbody>
            {people.map((line) => {
              const userId = line.user_id as string;
              const keys = data.weeks.map((week) => key(userId, week.index));
              return (
                <tr key={userId} data-testid={`budget-entry-${userId}`}>
                  <td>
                    <div className="font-medium">{line.name}</div>
                    {line.member_code ? (
                      <div className="text-xs text-[var(--text-muted)]">
                        {line.member_code}
                      </div>
                    ) : null}
                  </td>
                  {data.weeks.map((week, index) => (
                    <td key={week.index} className="text-right">
                      <input
                        aria-label={`Ngân sách ${line.name} tuần ${week.index}`}
                        inputMode="numeric"
                        value={draft[keys[index]] ?? ""}
                        disabled={disabled}
                        placeholder="Chưa đặt"
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            [keys[index]]: formatTyped(event.target.value),
                          }))
                        }
                        className={INPUT}
                      />
                    </td>
                  ))}
                  <td className="text-right font-semibold tabular-nums">
                    {money(sum(keys))}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="font-semibold">
              <th scope="row" className="text-left">
                Cả phòng
              </th>
              {data.weeks.map((week) => (
                <td key={week.index} className="text-right tabular-nums">
                  {money(
                    sum(
                      people.map((line) =>
                        key(line.user_id as string, week.index),
                      ),
                    ),
                  )}
                </td>
              ))}
              <td className="text-right tabular-nums">
                {money(sum(Object.keys(draft)))}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {data.can_edit ? (
        <div className="flex justify-end">
          <ConfirmButton
            tone="primary"
            disabled={changed === 0}
            pending={save.isPending}
            error={save.error}
            onOpenChange={(open) => (open ? save.reset() : undefined)}
            onConfirm={() => save.mutate()}
            spec={{
              title: `Lưu ngân sách tuần tháng ${monthNumber}/${year}?`,
              description:
                "Ngân sách mới áp dụng ngay trên Dashboard Ads; ô để trống là bỏ ngân sách tuần đó.",
              confirmLabel: "Lưu ngân sách",
            }}
          >
            {changed ? `Lưu ngân sách (${changed} ô thay đổi)` : "Lưu ngân sách"}
          </ConfirmButton>
        </div>
      ) : (
        <p className="text-sm text-[var(--text-muted)]">
          Chỉ Trưởng phòng Ads đặt được ngân sách.
        </p>
      )}
    </section>
  );
}
