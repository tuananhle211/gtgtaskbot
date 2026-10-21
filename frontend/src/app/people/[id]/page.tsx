"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, ApiError, type MemberRow } from "@/lib/api";
import { Avatar } from "@/components/avatar";
import { DepartmentTags } from "@/components/department-tags";
import { StatCards } from "@/components/stat-cards";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { formatWhen, monthLabel } from "@/lib/labels";
import { useEffortEnabled } from "@/lib/effort";
import { employmentTone } from "@/lib/resources";

/**
 * Somebody's page (2026-10-19): who they are and how their month went -
 * two tabs, "Hiệu suất" and "Thông tin tài khoản", nothing to edit here.
 *
 * Reached by clicking a name (`PersonLink`) wherever people are listed.
 * The server decides who may open it: the OWNER / ADMIN anyone's, a
 * department head their own department's people, everybody their own -
 * the rest get a refusal (403 `account_profile_forbidden`) drawn as a
 * sentence, never the figures.
 */
type Tab = "hieu-suat" | "tai-khoan";

function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export default function PersonPage() {
  const params = useParams<{ id: string }>();
  const userId = params.id;
  const [month, setMonth] = useState(() => monthOf(new Date()));
  const [tab, setTab] = useState<Tab>("hieu-suat");
  const me = useQuery({ queryKey: ["account", "me"], queryFn: api.accountMe, retry: false });
  const page = useQuery({
    queryKey: ["people", userId, month],
    queryFn: () => api.memberProfile(userId, month),
    // Another month keeps the card on screen while its figures load.
    placeholderData: keepPreviousData,
    retry: false,
  });
  const effortOn = useEffortEnabled();

  if (page.isPending) return <Loading label="Đang tải trang cá nhân…" />;
  if (page.isError) {
    const refused = page.error instanceof ApiError && page.error.status === 403;
    return (
      <div className="space-y-4">
        {refused ? (
          <section role="alert" className="panel p-6">
            <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              {page.error.message} Trưởng phòng xem được người trong phòng mình; Quản trị
              viên và Chủ sở hữu xem được tất cả.
            </p>
            <Link href="/tasks" className="mt-4 inline-block text-sm text-[var(--accent)] underline">
              Về bảng task
            </Link>
          </section>
        ) : (
          <ErrorBox error={page.error} onRetry={() => page.refetch()} />
        )}
      </div>
    );
  }
  const person = page.data;
  const mine = me.data?.user_id === person.user_id;
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "hieu-suat", label: "Hiệu suất" },
    { key: "tai-khoan", label: "Thông tin tài khoản" },
  ];
  return (
    <div className="space-y-6">
      <ProfileHero person={person} tabs={tabs} activeTab={tab} onTab={setTab} mine={mine} />
      {tab === "hieu-suat" ? (
        <section className="space-y-4" aria-labelledby="person-performance">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 id="person-performance" className="text-lg font-semibold tracking-tight">
                Hiệu suất
              </h2>
              <p className="mt-0.5 text-sm text-[var(--text-muted)]">{monthLabel(month)}</p>
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
              Tháng
              <input
                type="month"
                value={month}
                onChange={(event) => {
                  if (event.target.value) setMonth(event.target.value);
                }}
                className="min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
              />
            </label>
          </div>
          {page.isFetching ? <Loading label="Đang tải số liệu…" /> : null}
          <StatCards stats={person.stats} showEffort={effortOn} />
        </section>
      ) : (
        <AccountInfo person={person} />
      )}
    </div>
  );
}

function ProfileHero({
  person,
  tabs,
  activeTab,
  onTab,
  mine,
}: {
  person: MemberRow;
  tabs: Array<{ key: Tab; label: string }>;
  activeTab: Tab;
  onTab: (tab: Tab) => void;
  mine: boolean;
}) {
  const active = person.active ?? true;
  return (
    <section aria-label={`Trang cá nhân của ${person.full_name}`} className="panel overflow-hidden">
      <div aria-hidden="true" className="profile-cover h-24 sm:h-28" />
      <div className="px-5 pb-5 sm:px-7">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-end sm:gap-5">
            <Avatar
              name={person.full_name}
              src={person.avatar_url}
              size={96}
              className="-mt-12 shrink-0 ring-4 ring-[var(--surface)] shadow-[0_2px_8px_rgb(0_0_0/0.12)] sm:-mt-14"
            />
            <div className="min-w-0 pb-0.5">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{person.full_name}</h1>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--text-muted)]">
                <span>{person.role_label}</span>
                {person.member_code ? (
                  <>
                    <span aria-hidden="true" className="text-[var(--border)]">
                      •
                    </span>
                    <span>
                      Mã nhân viên{" "}
                      <span className="font-mono font-medium text-[var(--text)]">{person.member_code}</span>
                    </span>
                  </>
                ) : null}
                {person.employment_status_label ? (
                  <Pill tone={employmentTone(person.employment_status)}>{person.employment_status_label}</Pill>
                ) : null}
                {active ? null : <Pill tone="bad">Đã vô hiệu hoá</Pill>}
              </p>
            </div>
          </div>
          {mine ? (
            <Link
              href="/account"
              className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm leading-[2.75rem] hover:bg-[var(--surface-muted)]"
            >
              Tài khoản của tôi
            </Link>
          ) : null}
        </div>
        <div className="mt-4">
          <DepartmentTags seats={person.departments ?? []} />
        </div>
      </div>
      <div
        role="tablist"
        aria-label="Phần của trang cá nhân"
        className="flex gap-1 overflow-x-auto border-t border-[var(--border)] px-2 [scrollbar-width:none] sm:px-4"
      >
        {tabs.map((item) => {
          const selected = item.key === activeTab;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => onTab(item.key)}
              className={`relative min-h-12 shrink-0 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--accent)] ${selected ? "text-[var(--text)]" : "text-[var(--text-muted)] hover:text-[var(--text)]"
                }`}
            >
              {item.label}
              {selected ? (
                <span aria-hidden="true" className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-[var(--accent)]" />
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

/** "Thông tin tài khoản": the facts, read-only. */
function AccountInfo({ person }: { person: MemberRow }) {
  const rows: Array<{ label: string; value: React.ReactNode }> = [
    { label: "Họ tên", value: person.full_name },
    { label: "Vai trò", value: person.role_label },
    { label: "ID Telegram", value: <span className="tabular-nums">{String(person.telegram_user_id)}</span> },
    { label: "Mã nhân viên", value: person.member_code ? <span className="font-mono">{person.member_code}</span> : "–" },
    { label: "Trạng thái nhân sự", value: person.employment_status_label ?? "–" },
    {
      label: "Phòng ban",
      value: <DepartmentTags seats={person.departments ?? []} />,
    },
    { label: "Luồng", value: person.units.length ? person.units.join(", ") : "–" },
    { label: "Đăng nhập gần nhất", value: person.last_login_at ? formatWhen(person.last_login_at) : "Chưa đăng nhập" },
    { label: "Tài khoản", value: (person.active ?? true) ? "Đang hoạt động" : "Đã vô hiệu hoá" },
  ];
  return (
    <section className="panel overflow-hidden" aria-labelledby="person-account">
      <header className="border-b border-[var(--border)] px-5 py-3">
        <h2 id="person-account" className="text-base font-semibold">
          Thông tin tài khoản
        </h2>
      </header>
      <dl className="divide-y divide-[var(--border)]">
        {rows.map((row) => (
          <div key={row.label} className="grid gap-1 px-5 py-3 sm:grid-cols-[12rem_1fr] sm:gap-4">
            <dt className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">{row.label}</dt>
            <dd className="text-sm">{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
