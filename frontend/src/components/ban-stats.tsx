"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type BanStat } from "@/lib/api";
import { formatTokens } from "@/lib/deadline";
import { BoltIcon, TokenBadge } from "@/components/token-badge";
import { ErrorBox, Loading } from "@/components/states";

const ALL = "ALL";

/**
 * The dashboard's "Theo ban": tokens (budget, used, left, held) and work
 * (done, in progress, overdue) for Biên kịch, Design and Dựng over the
 * dashboard's dates. A ban's member opens on their own ban (`my_ban`); the
 * switch goes back to "Tất cả". `ban` is the URL's choice, if any.
 */
export function BanStatsPanel({
  dateFrom,
  dateTo,
  ban,
  onBan,
}: {
  dateFrom: string;
  dateTo: string;
  ban: string | null;
  onBan: (next: string) => void;
}) {
  const stats = useQuery({
    queryKey: ["units", "ADS", "ban-stats", dateFrom, dateTo],
    queryFn: () => api.unitBanStats("ADS", { date_from: dateFrom, date_to: dateTo }),
    retry: false,
  });
  if (stats.isPending) return <Loading label="Đang tải thống kê theo ban…" />;
  if (stats.isError) return <ErrorBox error={stats.error} onRetry={() => stats.refetch()} />;
  const data = stats.data;
  const chosen = ban ?? data.my_ban ?? ALL;
  const current = data.bans.find((item) => item.role === chosen);

  return (
    <section className="panel space-y-4 p-4" aria-labelledby="ban-stats-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="ban-stats-title" className="text-sm font-semibold">
          Theo ban
        </h2>
        <div role="tablist" aria-label="Chọn ban" className="flex flex-wrap gap-1.5">
          {[{ role: ALL, label: "Tất cả" }, ...data.bans].map((item) => (
            <button
              key={item.role}
              type="button"
              role="tab"
              aria-selected={chosen === item.role}
              onClick={() => onBan(item.role)}
              className={`min-h-9 rounded-lg border px-3 text-sm font-medium ${
                chosen === item.role
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] hover:bg-[var(--surface-muted)]"
              }`}
            >
              {item.label}
              {item.role === data.my_ban ? " (ban của bạn)" : ""}
            </button>
          ))}
        </div>
      </div>
      {current ? <OneBan ban={current} /> : <AllBans bans={data.bans} />}
    </section>
  );
}

function Figure({
  label,
  value,
  tone,
  token,
}: {
  label: string;
  value: string | number;
  tone?: "bad" | "good" | "warn";
  token?: boolean;
}) {
  const colour =
    tone === "bad"
      ? "text-[var(--bad)]"
      : tone === "good"
        ? "text-[var(--good)]"
        : token
          ? "text-[var(--warn)]"
          : "";
  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <p className="text-xs font-medium text-[var(--text-muted)]">{label}</p>
      <p
        className={`mt-1 inline-flex items-center gap-1 text-2xl font-bold tabular-nums ${colour}`}
      >
        {token ? <BoltIcon className="size-5" /> : null}
        {value}
      </p>
    </div>
  );
}

function OneBan({ ban }: { ban: BanStat }) {
  return (
    <div className="space-y-4" data-testid={`ban-${ban.role}`}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
        <Figure label="Quỹ token" value={formatTokens(ban.budget)} token />
        <Figure label="Đã dùng" value={formatTokens(ban.used)} token />
        <Figure
          label="Còn lại"
          value={formatTokens(ban.left)}
          tone={ban.left < 0 ? "bad" : "good"}
          token
        />
        <Figure label={`Đang ôm (${ban.open_tasks} task)`} value={formatTokens(ban.open_tokens)} token />
        <Figure label="Việc xong" value={ban.done} />
        <Figure label="Đang làm" value={ban.in_progress} />
        <Figure label="Trễ hạn" value={ban.overdue + ban.late} tone={ban.overdue + ban.late ? "bad" : undefined} />
      </div>
      {ban.members.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">Ban chưa có thành viên.</p>
      ) : (
        <div className="table-scroll-x">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
                <th className="py-2 pr-3 font-medium">Thành viên</th>
                <th className="py-2 pr-3 text-right font-medium">Còn hôm nay</th>
                <th className="py-2 pr-3 text-right font-medium">Đã dùng / Quỹ</th>
                <th className="py-2 pr-3 text-right font-medium">Đang ôm</th>
                <th className="py-2 pr-3 text-right font-medium">Xong</th>
              </tr>
            </thead>
            <tbody>
              {ban.members.map((member) => (
                <tr key={member.user_id} className="border-b border-[var(--border)] last:border-0">
                  <td className="py-2 pr-3">
                    {member.full_name}
                    {member.is_lead ? (
                      <span className="ml-1 text-xs text-[var(--text-muted)]">(trưởng ban)</span>
                    ) : null}
                  </td>
                  <td
                    className={`py-2 pr-3 text-right font-bold tabular-nums ${
                      member.today_left < 0 ? "text-[var(--bad)]" : "text-[var(--good)]"
                    }`}
                  >
                    {member.today_left < 0
                      ? `vượt ${formatTokens(-member.today_left)}`
                      : formatTokens(member.today_left)}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {formatTokens(member.used)}/{formatTokens(member.budget)}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <TokenBadge value={member.open_tokens} suffix="" size="sm" />{" "}
                    <span className="text-xs text-[var(--text-muted)]">
                      ({member.open_tasks} task)
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{member.done}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AllBans({ bans }: { bans: BanStat[] }) {
  const sum = (pick: (ban: BanStat) => number) => bans.reduce((total, ban) => total + pick(ban), 0);
  const rows = [
    ...bans.map((ban) => ({ key: ban.role, label: ban.label, members: ban.members.length, ban })),
  ];
  return (
    <div className="table-scroll-x" data-testid="ban-all">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
            <th className="py-2 pr-3 font-medium">Ban</th>
            <th className="py-2 pr-3 text-right font-medium">Thành viên</th>
            <th className="py-2 pr-3 text-right font-medium">Quỹ</th>
            <th className="py-2 pr-3 text-right font-medium">Đã dùng</th>
            <th className="py-2 pr-3 text-right font-medium">Còn lại</th>
            <th className="py-2 pr-3 text-right font-medium">Đang ôm</th>
            <th className="py-2 pr-3 text-right font-medium">Xong</th>
            <th className="py-2 pr-3 text-right font-medium">Đang làm</th>
            <th className="py-2 pr-3 text-right font-medium">Trễ hạn</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label, members, ban }) => (
            <tr key={key} className="border-b border-[var(--border)]">
              <td className="py-2 pr-3 font-medium">{label}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{members}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(ban.budget)}</td>
              <td className="py-2 pr-3 text-right font-bold tabular-nums text-[var(--warn)]">
                {formatTokens(ban.used)}
              </td>
              <td
                className={`py-2 pr-3 text-right font-bold tabular-nums ${
                  ban.left < 0 ? "text-[var(--bad)]" : "text-[var(--good)]"
                }`}
              >
                {formatTokens(ban.left)}
              </td>
              <td className="py-2 pr-3 text-right">
                <TokenBadge value={ban.open_tokens} suffix="" size="sm" />
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{ban.done}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{ban.in_progress}</td>
              <td
                className={`py-2 pr-3 text-right tabular-nums ${ban.overdue + ban.late ? "font-bold text-[var(--bad)]" : ""}`}
              >
                {ban.overdue + ban.late}
              </td>
            </tr>
          ))}
          <tr className="font-semibold">
            <td className="py-2 pr-3">Tổng</td>
            <td className="py-2 pr-3 text-right tabular-nums">{sum((ban) => ban.members.length)}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(sum((ban) => ban.budget))}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(sum((ban) => ban.used))}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(sum((ban) => ban.left))}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(sum((ban) => ban.open_tokens))}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{sum((ban) => ban.done)}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{sum((ban) => ban.in_progress)}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{sum((ban) => ban.overdue + ban.late)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
