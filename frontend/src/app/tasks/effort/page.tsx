"use client";

import { BoltIcon, TokenBadge } from "@/components/token-badge";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, type EffortDay, type EffortPerson } from "@/lib/api";
import { formatTokens } from "@/lib/deadline";
import { addDays, dayHeading, isDay, mondayOf } from "@/lib/effort";
import { ErrorBox, Loading } from "@/components/states";

export default function EffortPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải effort…" />}>
      <EffortGridScreen />
    </Suspense>
  );
}

/** The bans, by the function tag the server puts on each person. */
const BANS: Array<{ tag: string; label: string }> = [
  { tag: "BT", label: "Biên kịch" },
  { tag: "TK", label: "Design" },
  { tag: "D", label: "Dựng" },
];

const BUTTON =
  "inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm hover:bg-[var(--surface-muted)] disabled:opacity-50";

/**
 * "Effort": ORD tokens per person and day, one Monday..Sunday week at a time.
 *
 * Every number is the server's (`GET /api/units/ADS/effort`): a day's budget
 * (0 on weekends), the tokens deducted that day and what is left, which goes
 * negative once somebody is over budget. "Đang ôm" is the work handed out and
 * not finished yet, the load a Leader weighs before handing out more. Who is
 * listed is the server's decision too: the whole stream for its head, a ban
 * for its Leader, only oneself for anybody else.
 *
 * The week is on the URL (`?from=<Monday>`); without it the server answers
 * with the current week. The ban filter is on the URL too (`?ban=BT|TK|D`);
 * none = everybody the viewer may see.
 */
function EffortGridScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const fromParam = params.get("from");
  const from = isDay(fromParam) ? mondayOf(fromParam) : null;
  const banParam = params.get("ban");
  const ban = BANS.some((item) => item.tag === banParam) ? banParam : null;
  const grid = useQuery({
    queryKey: ["units", "ADS", "effort", from],
    queryFn: () =>
      api.unitEffort(
        "ADS",
        from ? { date_from: from, date_to: addDays(from, 6) } : {},
      ),
    placeholderData: keepPreviousData,
  });
  const data = grid.data;
  const shown = from ?? data?.date_from ?? null;
  const navigate = (changes: { from?: string | null; ban?: string | null }) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, { scroll: false });
  };
  const goTo = (monday: string | null) => navigate({ from: monday });
  // Only the bans somebody on screen belongs to; a Leader sees just theirs.
  const present = BANS.filter((item) =>
    (data?.people ?? []).some((person) => person.function_tag === item.tag),
  );
  const people = (data?.people ?? []).filter(
    (person) => !ban || person.function_tag === ban,
  );
  const thisWeek = data ? mondayOf(data.today) : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            Effort
          </h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Luồng Order (ORD) · token đã dùng / quỹ mỗi ngày · số dư âm là vượt
            effort
          </p>
        </div>
        <div
          role="group"
          aria-label="Chọn tuần"
          className="flex flex-wrap items-center gap-2"
        >
          <button
            type="button"
            className={BUTTON}
            disabled={!shown}
            onClick={() => shown && goTo(addDays(shown, -7))}
          >
            ‹ Tuần trước
          </button>
          <span className="min-w-[9rem] text-center text-sm font-medium tabular-nums">
            {data
              ? `${dayHeading(data.date_from).slice(-5)} – ${dayHeading(data.date_to).slice(-5)}`
              : "…"}
          </span>
          <button
            type="button"
            className={BUTTON}
            disabled={!shown}
            onClick={() => shown && goTo(addDays(shown, 7))}
          >
            Tuần sau ›
          </button>
          <button
            type="button"
            className={BUTTON}
            aria-pressed={Boolean(data && shown === thisWeek)}
            onClick={() => goTo(null)}
          >
            Tuần này
          </button>
        </div>
      </div>

      {present.length > 1 ? (
        <div role="tablist" aria-label="Lọc theo ban" className="flex flex-wrap gap-1.5">
          {[{ tag: null as string | null, label: "Tất cả" }, ...present].map((item) => (
            <button
              key={item.tag ?? "ALL"}
              type="button"
              role="tab"
              aria-selected={ban === item.tag}
              onClick={() => navigate({ ban: item.tag })}
              className={`min-h-9 rounded-lg border px-3 text-sm font-medium ${
                ban === item.tag
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] hover:bg-[var(--surface-muted)]"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}

      {grid.isPending ? <Loading label="Đang tải effort…" /> : null}
      {grid.isError ? (
        <ErrorBox error={grid.error} onRetry={() => grid.refetch()} />
      ) : null}
      {data && people.length === 0 ? (
        <p className="panel p-4 text-sm text-[var(--text-muted)]">
          Không có ai trong phạm vi bạn được xem.
        </p>
      ) : null}
      {data && people.length > 0 ? (
        <section className="panel" aria-label="Bảng effort">
          <div className="table-scroll-x">
            <table className="table-dense min-w-[60rem]">
              <thead>
                <tr>
                  <th scope="col">Người</th>
                  {data.days.map((day) => (
                    <th
                      key={day}
                      scope="col"
                      aria-current={day === data.today ? "date" : undefined}
                      className={`text-center ${day === data.today ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : ""}`}
                    >
                      {dayHeading(day)}
                      {day === data.today ? (
                        <span className="block text-[10px] font-normal normal-case">
                          hôm nay
                        </span>
                      ) : null}
                    </th>
                  ))}
                  <th scope="col" className="text-right">
                    Đang ôm
                  </th>
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <PersonRow
                    key={person.user_id}
                    person={person}
                    days={data.days}
                    today={data.today}
                  />
                ))}
              </tbody>
              {people.length > 1 ? (
                <tfoot>
                  <TotalRow people={people} days={data.days} today={data.today} />
                </tfoot>
              ) : null}
            </table>
          </div>
        </section>
      ) : null}

      <p className="text-xs text-[var(--text-muted)]">
        Token bị trừ vào ngày công đoạn được duyệt (token sửa: ngày bản sửa
        được duyệt). Quỹ token/ngày của từng người do quản trị luồng đặt ở
        Quản trị đơn vị.
      </p>
    </div>
  );
}

/** "Tổng": the people on screen added up, day by day. */
function TotalRow({
  people,
  days,
  today,
}: {
  people: EffortPerson[];
  days: string[];
  today: string;
}) {
  const open = people.reduce((total, person) => total + person.open_tokens, 0);
  const tasks = people.reduce((total, person) => total + person.open_tasks, 0);
  return (
    <tr className="border-t-2 border-[var(--border)] font-semibold" data-testid="effort-total">
      <th scope="row">Tổng ({people.length} người)</th>
      {days.map((date) => {
        let budget = 0;
        let used = 0;
        for (const person of people) {
          const day = person.days.find((item) => item.date === date);
          budget += day?.budget ?? 0;
          used += day?.used ?? 0;
        }
        return (
          <DayCell
            key={date}
            day={{ date, budget, used, left: Math.round((budget - used) * 100) / 100 }}
            today={date === today}
          />
        );
      })}
      <td className="whitespace-nowrap text-right tabular-nums">
        <TokenBadge value={open} />
        <span className="block text-xs text-[var(--text-muted)]">{tasks} task</span>
      </td>
    </tr>
  );
}

function PersonRow({
  person,
  days,
  today,
}: {
  person: EffortPerson;
  days: string[];
  today: string;
}) {
  const byDate = new Map(person.days.map((day) => [day.date, day]));
  return (
    <tr>
      <th scope="row" className="text-left font-normal">
        <span className="person block">{person.full_name}</span>
        <span className="block text-xs text-[var(--text-muted)]">
          {person.role_label} · {formatTokens(person.daily_tokens)} token/ngày
        </span>
      </th>
      {days.map((date) => (
        <DayCell
          key={date}
          day={byDate.get(date)}
          today={date === today}
        />
      ))}
      <td className="whitespace-nowrap text-right tabular-nums" data-testid="open-load">
        <TokenBadge value={person.open_tokens} />
        <span className="block text-xs text-[var(--text-muted)]">
          {person.open_tasks} task
        </span>
      </td>
    </tr>
  );
}

function DayCell({ day, today }: { day: EffortDay | undefined; today: boolean }) {
  const highlight = today ? "bg-[var(--accent-soft)]/60" : "";
  if (!day || (day.budget === 0 && day.used === 0)) {
    return (
      <td className={`text-center text-xs text-[var(--text-muted)] ${highlight}`}>
        {day ? "nghỉ" : "–"}
      </td>
    );
  }
  const over = day.left < 0;
  return (
    <td
      className={`text-center tabular-nums ${highlight}`}
      data-testid={`effort-${day.date}`}
    >
      <span className="inline-flex items-center gap-1 text-base font-bold text-[var(--warn)]">
        <BoltIcon />
        {formatTokens(day.used)}/{formatTokens(day.budget)}
      </span>
      <span
        data-left={day.left}
        className={`block text-sm font-bold ${over ? "text-[var(--bad)]" : "text-[var(--good)]"}`}
      >
        {over ? `vượt ${formatTokens(-day.left)}` : `còn ${formatTokens(day.left)}`}
      </span>
    </td>
  );
}
