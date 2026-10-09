"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { UnitSwitch, UntaggedState } from "@/components/unit-switch";
import { formatAgo } from "@/lib/labels";
import { currentUnit, firstOfMonth, isUntagged, lastOfMonth, unitName, byStreamOrder } from "@/lib/units";
import { Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";

export default function DashboardPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải dashboard…" />}>
      <Dashboard />
    </Suspense>
  );
}

const field =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/**
 * Five tiles, the phase strip, what waits on this person, and who did what -
 * for one unit, one date range.
 *
 * Every figure comes from `/api/board/dashboard`, which reads the same rows
 * `/tasks` lists with the same filters, so a tile and the table it links to
 * always agree. "Chờ tôi xử lý" is the task table's own `awaiting_me` query,
 * eight rows of it. Nothing is computed here.
 */
function Dashboard() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const unit = currentUnit(params, me.data);
  // No stream yet: nothing to ask the board for (it answers 404 per stream).
  const untagged = isUntagged(me.data);
  const today = new Date();
  const dateFrom = params.get("from") || firstOfMonth(today);
  const dateTo = params.get("to") || lastOfMonth(today);
  const owner = params.get("owner") || "";
  const person = params.get("person") || "";

  const summary = useQuery({
    queryKey: ["board", "dashboard", unit, dateFrom, dateTo, owner, person],
    queryFn: () =>
      api.boardDashboard({
        unit,
        date_from: dateFrom,
        date_to: dateTo,
        owner: owner || undefined,
        person: person || undefined,
      }),
    enabled: me.isSuccess && !untagged,
  });
  // The people of the unit(s) on screen, for the "Người" dropdown.
  const unitCodes = unit === "ALL" ? (me.data?.units ?? []).map((item) => item.code) : [unit];
  const roster = useQuery({
    queryKey: ["dashboard", "people", unitCodes.join(",")],
    queryFn: async () => {
      const lists = await Promise.all(unitCodes.map((code) => api.unitMembers(code)));
      const seen = new Map<string, string>();
      for (const list of lists) {
        for (const member of list.members) {
          if (member.active && !seen.has(member.user_id)) seen.set(member.user_id, member.full_name);
        }
      }
      return [...seen.entries()]
        .map(([user_id, full_name]) => ({ user_id, full_name }))
        .sort((a, b) => a.full_name.localeCompare(b.full_name, "vi"));
    },
    enabled: me.isSuccess && !untagged && unitCodes.length > 0,
    retry: false,
  });
  const inbox = useQuery({
    queryKey: ["board", "tasks", "awaiting", unit],
    queryFn: () => api.boardTasks({ unit, awaiting_me: true, limit: 8 }),
    enabled: me.isSuccess && !untagged,
  });

  const setParams = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, { scroll: false });
  };

  const tasksHref = (extra: Record<string, string>) => {
    const search = new URLSearchParams({
      unit,
      from: dateFrom,
      to: dateTo,
      ...(person ? { person } : {}),
      ...extra,
    });
    return `/tasks?${search.toString()}`;
  };

  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (untagged) return <UntaggedState title="Dashboard" />;

  const unitLabel =
    unit === "ALL"
      ? "Cả hai luồng"
      : unitName(unit, me.data.units.find((item) => item.code === unit)?.label);
  const data = summary.data;
  const phases = data?.by_phase.filter((item) => item.phase !== "CANCELLED") ?? [];
  const maxPhase = Math.max(1, ...phases.map((item) => item.count));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Dashboard</h1>
            <UnitSwitch me={me.data} />
          </div>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {unitLabel}
            {person ? ` · của ${(roster.data ?? []).find((item) => item.user_id === person)?.full_name ?? "một người"}` : ""}
            {" "}· số liệu theo ngày lên order trong khoảng đang lọc · bấm một ô để mở danh sách
          </p>
        </div>
        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => event.preventDefault()}>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Từ ngày
            <input type="date" value={dateFrom} onChange={(event) => setParams({ from: event.target.value })} className={field} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Đến ngày
            <input type="date" value={dateTo} onChange={(event) => setParams({ to: event.target.value })} className={field} />
          </label>
          {me.data.units.length > 1 || me.data.can_view_all ? (
            <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
              Luồng
              <Select value={unit} onChange={(event) => setParams({ unit: event.target.value })} className="min-h-10">
                {byStreamOrder(me.data.units).map((item) => (
                  <option key={item.code} value={item.code}>
                    {unitName(item.code, item.label)}
                  </option>
                ))}
                {me.data.can_view_all ? <option value="ALL">Tất cả</option> : null}
              </Select>
            </label>
          ) : null}
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Người
            <Select
              value={person}
              onChange={(event) => setParams({ person: event.target.value })}
              className="min-h-10 min-w-[12rem]"
              aria-label="Xem số liệu của một người"
            >
              <option value="">Tất cả mọi người</option>
              {(roster.data ?? []).map((item) => (
                <option key={item.user_id} value={item.user_id}>
                  {item.full_name}
                </option>
              ))}
            </Select>
          </label>
          <button type="button" onClick={() => setParams({ from: "", to: "", owner: "", person: "" })} className={field}>
            Tháng này
          </button>
          <Link
            href="/orders/new"
            className="inline-flex min-h-10 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            + Tạo order
          </Link>
        </form>
      </div>

      {summary.isPending ? <Loading label="Đang tải số liệu…" /> : null}
      {summary.isError ? <ErrorBox error={summary.error} onRetry={() => summary.refetch()} /> : null}
      {data ? (
        <>
          <section aria-label="Số liệu tổng" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            <Tile label="Tổng số order" value={data.total} href={tasksHref({})} hint="lên order trong khoảng lọc" />
            <Tile
              label="Đã hoàn thành"
              value={data.completed}
              href={tasksHref({ phase: "DONE" })}
              hint="Trưởng phòng đã duyệt Final"
              tone="good"
            />
            <Tile
              label="Chờ duyệt"
              value={data.pending_review}
              href={tasksHref({ phase: "REVIEW" })}
              hint="đang chờ một quyết định"
              tone="warn"
            />
            <Tile
              label="Gấp"
              value={data.urgent}
              href={tasksHref({ urgent: "true" })}
              hint="quá hạn mốc Gấp của luồng, chưa xong"
              tone={data.urgent > 0 ? "bad" : undefined}
            />
            <Tile
              label="Tiến độ hoàn thành"
              value={data.progress_percent === null ? "–" : `${data.progress_percent}%`}
              href={tasksHref({})}
              hint="Hoàn thành ÷ Tổng"
              progress={data.progress_percent}
            />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <section className="panel p-4">
              <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-sm font-semibold">Order đang ở từng pha</h2>
                <span className="text-xs text-[var(--text-muted)]">
                  {data.total - data.completed} chưa hoàn thành
                </span>
              </div>
              <ul className="mt-4 space-y-3">
                {phases.map((item) => (
                  <li key={item.phase}>
                    <Link
                      href={tasksHref({ phase: item.phase })}
                      className="grid grid-cols-[8rem_minmax(0,1fr)_3rem] items-center gap-3 rounded-lg px-1 py-0.5 hover:bg-[var(--surface-muted)]"
                    >
                      <span className="text-sm">{item.label}</span>
                      <span className="h-3 overflow-hidden rounded-full bg-[var(--surface-muted)]">
                        <span
                          className="block h-full rounded-full bg-[var(--accent)]"
                          style={{ width: `${Math.round((item.count / maxPhase) * 100)}%` }}
                        />
                      </span>
                      <span className="text-right text-sm font-semibold tabular-nums">{item.count}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>

            <section className="panel p-4">
              <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-sm font-semibold">Chờ tôi xử lý</h2>
                <Link href={tasksHref({ awaiting_me: "true" })} className="text-xs font-medium text-[var(--accent)]">
                  Xem tất cả
                </Link>
              </div>
              {inbox.isPending ? <Loading label="Đang tải…" /> : null}
              {inbox.data && inbox.data.items.length === 0 ? (
                <p className="mt-3 text-sm text-[var(--text-muted)]">Không có gì chờ bạn lúc này.</p>
              ) : null}
              {inbox.data && inbox.data.items.length > 0 ? (
                <ul className="mt-3 divide-y divide-[var(--border)]">
                  {inbox.data.items.map((row) => (
                    <li key={`${row.unit}:${row.id}`}>
                      <Link href={row.detail_path} className="flex items-start justify-between gap-3 py-2 hover:bg-[var(--surface-muted)]">
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{row.title}</span>
                          <span className="block text-xs text-[var(--text-muted)]">
                            <span className="font-mono">{row.code}</span> · {row.owner_name}
                            {row.stage_since ? ` · ${formatAgo(row.stage_since)}` : ""}
                          </span>
                        </span>
                        <span className="flex shrink-0 flex-col items-end gap-1">
                          <Pill tone={row.phase === "REVIEW" || row.phase === "FINAL_REVIEW" ? "warn" : "neutral"}>
                            {row.status_label}
                          </Pill>
                          {row.urgent ? <Pill tone="critical">Gấp</Pill> : null}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <PeopleTable
              title={unit === "PR" ? "Theo người phụ trách" : "Theo người order"}
              columns={["Lên", "Xong", "Gấp"]}
              rows={data.by_owner}
              href={(id) => tasksHref({ owner: id })}
            />
            <PeopleTable
              title="Theo người làm"
              columns={["Công đoạn giữ", "Xong", "Đang gấp"]}
              rows={data.by_worker}
              href={(id) => tasksHref({ assignee: id })}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

function Tile({
  label,
  value,
  href,
  hint,
  tone,
  progress,
}: {
  label: string;
  value: number | string;
  href: string;
  hint?: string;
  tone?: "bad" | "warn" | "good";
  progress?: number | null;
}) {
  const border =
    tone === "bad" ? "border-[var(--bad)]" : tone === "warn" ? "border-[var(--warn)]/40" : "border-[var(--border)]";
  const colour = tone === "bad" ? "text-[var(--bad)]" : tone === "good" ? "text-[var(--good)]" : "";
  return (
    <Link href={href} className={`panel block border p-4 transition-colors hover:bg-[var(--surface-muted)] ${border}`}>
      <p className="text-xs font-medium text-[var(--text-muted)]">{label}</p>
      <p className={`mt-1 text-3xl font-semibold tabular-nums tracking-tight ${colour}`}>{value}</p>
      {typeof progress === "number" ? (
        <span className="mt-2 block h-1.5 overflow-hidden rounded-full bg-[var(--surface-muted)]">
          <span className="block h-full rounded-full bg-[var(--good)]" style={{ width: `${progress}%` }} />
        </span>
      ) : null}
      {hint ? <p className="mt-1 text-[11px] text-[var(--text-muted)]">{hint}</p> : null}
    </Link>
  );
}

function PeopleTable({
  title,
  columns,
  rows,
  href,
}: {
  title: string;
  columns: [string, string, string];
  rows: Array<{ user_id: string; name: string; opened: number; done: number; late: number }>;
  href: (userId: string) => string;
}) {
  return (
    <section className="panel p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--text-muted)]">Chưa có số liệu trong khoảng này.</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
              <th className="py-2 pr-3 font-medium">Người</th>
              {columns.map((column) => (
                <th key={column} className="py-2 pr-3 text-right font-medium">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.user_id} className="border-b border-[var(--border)]">
                <td className="py-2 pr-3">
                  <Link href={href(row.user_id)} className="hover:underline">
                    {row.name}
                  </Link>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{row.opened}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{row.done}</td>
                <td className={`py-2 pr-3 text-right tabular-nums ${row.late > 0 ? "font-semibold text-[var(--bad)]" : ""}`}>
                  {row.late}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
