"use client";

import { BoltIcon, TokenBadge } from "@/components/token-badge";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  api,
  ApiError,
  type AccountMe,
  type AccountMembers,
  type MemberRow,
  type MemberStats,
} from "@/lib/api";
import { Avatar } from "@/components/avatar";
import { AvatarDialog, RemoveAvatarButton } from "@/components/avatar-editor";
import { ConfirmButton } from "@/components/confirm";
import { PrimaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import {
  AccountStatusButton,
  DeactivatedPill,
  DeleteAccountButton,
  mayChangeAccountStatus,
  mayDeleteAccount,
  UnitTags,
} from "@/components/unit-panel";
import { resetMemberPasswordConfirmation } from "@/lib/confirmations";
import { formatAgo, formatWhen, monthLabel } from "@/lib/labels";
import {
  EmploymentStatusField,
  EmploymentStatusPill,
  MemberCodeField,
  PasswordStatus,
} from "@/components/account-fields";
import { DepartmentTags, SeedingTag } from "@/components/department-tags";
import { PersonLink } from "@/components/person-link";
import { Glyph, StatCards, formatPoints, formatRate } from "@/components/stat-cards";
import { PasswordDialog, PasswordForm } from "./password";
import { InvitePanel, mayInvite as mayInviteFor } from "./invites";
import { formatTokens } from "@/lib/deadline";
import { dayHeading, useEffortEnabled } from "@/lib/effort";

const FIELD =
  "min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] transition-colors focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/15";

/** The quiet bordered button of this page: header actions, row actions. */
const QUIET_BUTTON =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3.5 text-sm font-medium shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50";

type Tab = "hieu-suat" | "tai-khoan" | "thanh-vien" | "moi-thanh-vien";

/** The "Phòng ban" filter's value for people with no seat. */
const NO_DEPARTMENT = "__none__";

/** `YYYY-MM` of a date, in the browser's own calendar. */
function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}


/** A server failure's own sentence; a dropped connection gets one of ours. */
const failureText = (error: unknown) =>
  error instanceof ApiError ? error.message : "Không kết nối được máy chủ. Bạn thử lại nhé.";

/**
 * "Tài khoản": a profile header (picture, name, role, units, the password and
 * sign-out actions), then three tabs - my month's figures, my account settings
 * and, for somebody the server lets see the roster, every member's figures.
 *
 * An account still on the default (or a temporary) password lands here from
 * every screen (the Shell sends it) and sees one focused card: the password
 * form and "Đăng xuất", nothing else. The API refuses the rest until it changes.
 */
export default function AccountPage() {
  const me = useQuery({ queryKey: ["account", "me"], queryFn: api.accountMe });

  if (me.isPending) return <Loading label="Đang tải tài khoản…" />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  return <Account me={me.data} />;
}

function Account({ me }: { me: AccountMe }) {
  const mustChange = me.must_change_password;
  const [tab, setTab] = useState<Tab>("hieu-suat");
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [month, setMonth] = useState<string>(me.stats.month || monthOf(new Date()));
  const [unit, setUnit] = useState("ALL");
  // OWNER / ADMIN may list deactivated accounts too, to bring them back.
  const [includeInactive, setIncludeInactive] = useState(false);
  // "Thành viên nhóm tôi": on when the tab opens. The server falls back to the
  // full roster for somebody with no team of their own.
  const [teamOnly, setTeamOnly] = useState(true);

  // The roster is offered only to somebody the server shows it to: a 403 hides
  // the tab. Once it has loaded, a later failure (a filter the server refuses)
  // is shown inside the tab instead of taking the tab away mid-use.
  const members = useQuery({
    queryKey: ["account", "members", month, unit, includeInactive, teamOnly],
    queryFn: () => api.accountMembers(month, unit, includeInactive, teamOnly),
    enabled: !mustChange,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const [membersSeen, setMembersSeen] = useState(false);
  useEffect(() => {
    if (members.isSuccess) setMembersSeen(true);
  }, [members.isSuccess]);

  const passwordChanged = () =>
    setNotice("Đã đổi mật khẩu. Các phiên đăng nhập khác của bạn đã được đăng xuất.");

  if (mustChange) return <ForcedPasswordChange me={me} onChanged={passwordChanged} />;

  // Invites are for system team leads (and ADMIN / OWNER) and ORD's heads and
  // Leaders; the server refuses anybody else, so the tab is not offered to them.
  const mayInvite = mayInviteFor(me);
  const activeTab: Tab =
    (tab === "thanh-vien" && !membersSeen) || (tab === "moi-thanh-vien" && !mayInvite)
      ? "hieu-suat"
      : tab;
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "hieu-suat", label: "Hiệu suất của tôi" },
    { key: "tai-khoan", label: "Thông tin tài khoản" },
    ...(membersSeen ? [{ key: "thanh-vien" as const, label: "Thành viên" }] : []),
    ...(mayInvite ? [{ key: "moi-thanh-vien" as const, label: "Mời thành viên" }] : []),
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {notice ? <Notice onDismiss={() => setNotice(null)}>{notice}</Notice> : null}

      <ProfileHero
        me={me}
        tabs={tabs}
        activeTab={activeTab}
        onTab={setTab}
        onChangePassword={() => setPasswordOpen(true)}
        onChangeAvatar={() => setAvatarOpen(true)}
      />

      {activeTab === "hieu-suat" ? <MyPerformance me={me} month={month} onMonth={setMonth} /> : null}
      {activeTab === "tai-khoan" ? (
        <AccountSettings
          me={me}
          onChangePassword={() => setPasswordOpen(true)}
          onChangeAvatar={() => setAvatarOpen(true)}
          onNotice={setNotice}
        />
      ) : null}
      {activeTab === "thanh-vien" ? (
        <Members
          me={me}
          month={month}
          onMonth={setMonth}
          unit={unit}
          onUnit={setUnit}
          includeInactive={includeInactive}
          onIncludeInactive={setIncludeInactive}
          teamOnly={teamOnly}
          onTeamOnly={setTeamOnly}
          query={members}
        />
      ) : null}
      {activeTab === "moi-thanh-vien" ? <InvitePanel role={me.role} /> : null}

      <PasswordDialog
        open={passwordOpen}
        onClose={() => setPasswordOpen(false)}
        me={me}
        onChanged={passwordChanged}
      />
      <AvatarDialog
        open={avatarOpen}
        onClose={() => setAvatarOpen(false)}
        name={me.full_name}
        current={me.avatar_url}
        onSaved={() => setNotice("Đã cập nhật ảnh đại diện.")}
      />
    </div>
  );
}

// --- The forced change ----------------------------------------------------------

function ForcedPasswordChange({ me, onChanged }: { me: AccountMe; onChanged: () => void }) {
  return (
    <div className="flex justify-center py-4 sm:py-10">
      <section
        aria-labelledby="forced-password-title"
        className="panel w-full max-w-md overflow-hidden shadow-[0_1px_3px_rgb(0_0_0/0.05),0_12px_32px_-12px_rgb(0_0_0/0.12)]"
      >
        <div className="px-5 pb-7 pt-7 sm:px-8 sm:pt-8">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--warn-soft)] text-[var(--warn)]">
            <Glyph name="lock" size={22} />
          </span>
          <h1 id="forced-password-title" className="mt-5 text-xl font-semibold tracking-tight">
            Đặt mật khẩu mới
          </h1>
          <div role="alert" className="mt-2 space-y-1 text-sm leading-relaxed text-[var(--text-muted)]">
            <p className="font-medium text-[var(--text)]">Bạn cần đổi mật khẩu trước khi dùng APM Management.</p>
            <p>
              {me.password_temporary
                ? "Bạn vừa đăng nhập bằng mật khẩu tạm gửi qua Telegram."
                : "Tài khoản đang dùng mật khẩu mặc định."}{" "}
              Các màn hình khác sẽ mở lại ngay sau khi đổi xong.
            </p>
          </div>
          <div className="mt-7">
            <PasswordForm me={me} layout="page" onChanged={onChanged} />
          </div>
        </div>
        <footer className="flex items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--surface-muted)] px-5 py-3 text-sm sm:px-8">
          <span className="flex min-w-0 items-center gap-2 text-[var(--text-muted)]">
            <Avatar name={me.full_name} src={me.avatar_url} size={24} />
            <span className="truncate">{me.full_name}</span>
          </span>
          <LogoutButton variant="link" />
        </footer>
      </section>
    </div>
  );
}

// --- Profile header -------------------------------------------------------------

function ProfileHero({
  me,
  tabs,
  activeTab,
  onTab,
  onChangePassword,
  onChangeAvatar,
}: {
  me: AccountMe;
  tabs: Array<{ key: Tab; label: string }>;
  activeTab: Tab;
  onTab: (tab: Tab) => void;
  onChangePassword: () => void;
  onChangeAvatar: () => void;
}) {
  return (
    <section aria-label="Hồ sơ của tôi" className="panel overflow-hidden">
      <div aria-hidden="true" className="profile-cover h-24 sm:h-28" />
      <div className="px-5 pb-5 sm:px-7">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-end sm:gap-5">
            <button
              type="button"
              onClick={onChangeAvatar}
              aria-label="Đổi ảnh đại diện"
              title="Đổi ảnh"
              className="group relative -mt-12 w-fit shrink-0 rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)] sm:-mt-14"
            >
              <Avatar
                name={me.full_name}
                src={me.avatar_url}
                size={96}
                className="ring-4 ring-[var(--surface)] shadow-[0_2px_8px_rgb(0_0_0/0.12)]"
              />
              <span className="absolute inset-0 flex flex-col items-center justify-center rounded-full bg-black/45 text-[11px] font-medium text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                <Glyph name="camera" size={20} />
                <span className="mt-0.5">Đổi ảnh</span>
              </span>
              <span className="absolute bottom-0.5 right-0.5 inline-flex h-8 w-8 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface)] text-[var(--text)] shadow-sm transition-transform group-hover:scale-105">
                <Glyph name="camera" size={15} />
              </span>
            </button>
            <div className="min-w-0 pb-0.5">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{me.full_name}</h1>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--text-muted)]">
                <span>{me.role_label}</span>
                <span aria-hidden="true" className="text-[var(--border)]">
                  •
                </span>
                <span>
                  ID Telegram <span className="font-medium tabular-nums text-[var(--text)]">{String(me.telegram_user_id)}</span>
                </span>
                {me.telegram_username ? (
                  <>
                    <span aria-hidden="true" className="text-[var(--border)]">
                      •
                    </span>
                    <span>@{me.telegram_username}</span>
                  </>
                ) : null}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:pb-1">
            <button type="button" onClick={onChangePassword} className={QUIET_BUTTON}>
              <Glyph name="key" size={16} />
              Đổi mật khẩu
            </button>
            <LogoutButton variant="button" />
          </div>
        </div>
        <MySeats memberCode={me.units.find((item) => item.member_code)?.member_code ?? null} />
      </div>
      <div
        role="tablist"
        aria-label="Phần của trang tài khoản"
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

function Notice({ children, onDismiss }: { children: React.ReactNode; onDismiss: () => void }) {
  return (
    <div
      role="status"
      className="flex items-center gap-3 rounded-xl border border-[var(--good)]/25 bg-[var(--good-soft)] px-4 py-3 text-sm text-[var(--good)]"
    >
      <Glyph name="check-circle" size={18} />
      <p className="min-w-0 flex-1 font-medium">{children}</p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Ẩn thông báo"
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-black/5 dark:hover:bg-white/10"
      >
        <Glyph name="x" size={16} />
      </button>
    </div>
  );
}

// --- Thông tin tài khoản ----------------------------------------------------------

function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="panel overflow-hidden">
      <header className="border-b border-[var(--border)] px-5 py-4 sm:px-6">
        <h2 id={id} className="text-base font-semibold tracking-tight">
          {title}
        </h2>
        <p className="mt-0.5 text-sm text-[var(--text-muted)]">{description}</p>
      </header>
      <div className="divide-y divide-[var(--border)]">{children}</div>
    </section>
  );
}

/** One settings line: what it is on the left, the value or control on the right. */
function SettingRow({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  /** When the control is a single field, the label points at it. */
  htmlFor?: string;
  children: React.ReactNode;
}) {
  const labelId = useId();
  const Title = htmlFor ? "label" : "p";
  return (
    <div
      role="group"
      aria-labelledby={labelId}
      className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] sm:gap-6 sm:px-6 sm:py-5"
    >
      <div>
        <Title id={labelId} htmlFor={htmlFor} className="block text-sm font-medium">
          {label}
        </Title>
        {hint ? <p className="mt-0.5 text-xs leading-relaxed text-[var(--text-muted)]">{hint}</p> : null}
      </div>
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

function AccountSettings({
  me,
  onChangePassword,
  onChangeAvatar,
  onNotice,
}: {
  me: AccountMe;
  onChangePassword: () => void;
  onChangeAvatar: () => void;
  onNotice: (text: string) => void;
}) {
  return (
    <div className="space-y-6">
      <SettingsSection id="account-profile" title="Hồ sơ" description="Mọi người trong APM Management thấy ảnh và tên này.">
        <SettingRow label="Ảnh đại diện" hint="PNG, JPG, WEBP hoặc GIF. Ảnh được cắt vuông.">
          <div className="flex flex-wrap items-center gap-4">
            <Avatar name={me.full_name} src={me.avatar_url} size={56} />
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={onChangeAvatar} className={`${QUIET_BUTTON} min-h-9`}>
                <Glyph name="camera" size={15} />
                {me.avatar_url ? "Đổi ảnh" : "Tải ảnh lên"}
              </button>
              {me.avatar_url ? <RemoveAvatarButton onRemoved={() => onNotice("Đã xoá ảnh đại diện.")} /> : null}
            </div>
          </div>
        </SettingRow>
        <DisplayNameRow me={me} />
      </SettingsSection>

      <SettingsSection
        id="account-identity"
        title="Thông tin tài khoản"
        description="Do quản trị viên quản lý; liên hệ họ nếu cần đổi."
      >
        <SettingRow label="ID Telegram" hint="Dùng làm tên đăng nhập.">
          <span className="font-medium tabular-nums">{String(me.telegram_user_id)}</span>
          {me.telegram_username ? (
            <span className="ml-2 text-[var(--text-muted)]">@{me.telegram_username}</span>
          ) : null}
        </SettingRow>
        <SettingRow label="Vai trò">{me.role_label}</SettingRow>
        <DepartmentsRow />
        {me.units.some((item) => item.member_code) ? (
          <SettingRow label="Mã nhân viên" hint="Tiền tố mã order của bạn.">
            <span className="font-semibold">
              {me.units.find((item) => item.member_code)?.member_code}
            </span>
          </SettingRow>
        ) : null}
      </SettingsSection>

      <SettingsSection id="account-security" title="Bảo mật" description="Mật khẩu đăng nhập APM Management trên web.">
        <SettingRow label="Mật khẩu">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {me.password_temporary ? (
                <span className="st st-amber">Mật khẩu tạm</span>
              ) : me.has_custom_password === false ? (
                <span className="st st-amber">Mặc định</span>
              ) : (
                <span className="st st-green">Đã đổi</span>
              )}
              <span className="text-xs text-[var(--text-muted)]">
                {me.password_changed_at ? `Đổi lần cuối ${formatWhen(me.password_changed_at)}` : "Chưa đổi mật khẩu"}
              </span>
            </div>
            <button type="button" onClick={onChangePassword} className={`${QUIET_BUTTON} min-h-9`}>
              <Glyph name="key" size={15} />
              Đổi mật khẩu
            </button>
          </div>
        </SettingRow>
      </SettingsSection>
    </div>
  );
}

/**
 * "Phòng ban": the viewer's own seats, from `/api/units/me` `departments`,
 * as chips (★ = Trưởng phòng). Not drawn while it loads, if it fails, or for
 * an API that does not send the field.
 */
function DepartmentsRow() {
  const units = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe, retry: false });
  const seats = units.data?.departments;
  if (!seats) return null;
  return (
    <SettingRow label="Phòng ban" hint="Do chủ sở hữu hoặc trưởng phòng xếp.">
      {seats.length === 0 ? (
        <span className="text-[var(--text-muted)]">
          Chưa thuộc phòng ban nào. Trưởng phòng hoặc quản trị viên sẽ xếp bạn vào phòng.
        </span>
      ) : (
        <div data-testid="my-departments" className="flex flex-wrap items-center gap-1.5">
          <DepartmentTags seats={seats} />
          <span className="text-xs text-[var(--text-muted)]">
            {seats
              .map(
                (seat) =>
                  `${seat.name} — ${seat.position_label ?? (seat.position === "HEAD" ? "Trưởng phòng" : "Nhân viên")}`,
              )
              .join(" · ")}
          </span>
        </div>
      )}
    </SettingRow>
  );
}

/** The hero's chips: the viewer's departments (★ = head) and their member code. */
function MySeats({ memberCode }: { memberCode: string | null }) {
  const units = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe, retry: false });
  const seats = units.data?.departments;
  if (!seats && !memberCode) return null;
  return (
    <div aria-label="Phòng ban của tôi" className="mt-4 flex flex-wrap items-center gap-2 text-xs">
      {seats ? <DepartmentTags seats={seats} /> : null}
      {memberCode ? (
        <span className="text-[var(--text-muted)]">
          Mã <span className="font-semibold text-[var(--text)]">{memberCode}</span>
        </span>
      ) : null}
    </div>
  );
}

function DisplayNameRow({ me }: { me: AccountMe }) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [name, setName] = useState(me.full_name);
  const [saved, setSaved] = useState(false);
  const trimmed = name.trim();
  const valid = trimmed.length >= 2 && trimmed.length <= 80;
  const save = useMutation({
    mutationFn: () => api.updateProfile(trimmed),
    onSuccess: (data) => {
      queryClient.setQueryData(["account", "me"], data);
      // The top bar's "name · role" line reads the session.
      void queryClient.invalidateQueries({ queryKey: ["session"] });
      setSaved(true);
    },
  });

  return (
    <SettingRow label="Tên hiển thị" hint="Hiện ở thanh trên cùng, trên task và thông báo." htmlFor={inputId}>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid && trimmed !== me.full_name) save.mutate();
        }}
      >
        <input
          id={inputId}
          value={name}
          maxLength={80}
          autoComplete="name"
          onChange={(event) => {
            setName(event.target.value);
            setSaved(false);
          }}
          className={`${FIELD} min-w-0 flex-1 basis-56`}
        />
        <PrimaryButton
          type="submit"
          disabled={!valid || trimmed === me.full_name || save.isPending}
          className="min-h-10"
        >
          {save.isPending ? "Đang lưu…" : "Lưu tên"}
        </PrimaryButton>
      </form>
      {!valid ? <p className="mt-2 text-xs text-[var(--warn)]">Tên dài từ 2 đến 80 ký tự.</p> : null}
      {save.isError ? (
        <p role="alert" className="mt-2 text-sm text-[var(--bad)]">
          {failureText(save.error)}
        </p>
      ) : null}
      {saved && !save.isError ? (
        <p role="status" className="mt-2 text-sm text-[var(--good)]">
          Đã lưu tên hiển thị.
        </p>
      ) : null}
    </SettingRow>
  );
}

// --- Hiệu suất của tôi --------------------------------------------------------

function MonthField({ month, onMonth }: { month: string; onMonth: (value: string) => void }) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
      Tháng
      <input
        type="month"
        value={month}
        onChange={(event) => {
          if (event.target.value) onMonth(event.target.value);
        }}
        className="min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)] shadow-[0_1px_2px_rgb(0_0_0/0.04)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/15"
      />
    </label>
  );
}

function MyPerformance({
  me,
  month,
  onMonth,
}: {
  me: AccountMe;
  month: string;
  onMonth: (value: string) => void;
}) {
  // The current month arrived with /me; any other month is its own request.
  const other = month !== me.stats.month;
  const stats = useQuery({
    queryKey: ["account", "stats", month],
    queryFn: () => api.accountStats(month),
    enabled: other,
  });
  const data: MemberStats | undefined = other ? stats.data : me.stats;
  // Off = ORD does not count tokens: no "Effort" card, no "Effort tuần này".
  const effortOn = useEffortEnabled();

  return (
    <section className="space-y-4" aria-labelledby="account-performance">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="account-performance" className="text-lg font-semibold tracking-tight">
            Hiệu suất của tôi
          </h2>
          <p className="mt-0.5 text-sm text-[var(--text-muted)]">{monthLabel(month)}</p>
        </div>
        <MonthField month={month} onMonth={onMonth} />
      </div>
      {other && stats.isPending ? <Loading label="Đang tải số liệu…" /> : null}
      {other && stats.isError ? <ErrorBox error={stats.error} onRetry={() => stats.refetch()} /> : null}
      {data ? <StatCards stats={data} showEffort={effortOn} /> : null}
      {effortOn && me.units.some((unit) => unit.code === "ADS") ? (
        <MyEffortWeek userId={me.user_id} />
      ) : null}
    </section>
  );
}

/**
 * This week's ORD tokens, day by day: used / budget and what is left (red
 * once over). The server's grid narrowed to oneself; nothing is drawn when it
 * has no row for this person (or refuses).
 */
function MyEffortWeek({ userId }: { userId: string }) {
  const grid = useQuery({
    queryKey: ["units", "ADS", "effort", "me", userId],
    queryFn: () => api.unitEffort("ADS", { user_id: userId }),
    retry: false,
  });
  const person = grid.data?.people.find((item) => item.user_id === userId);
  if (!grid.data || !person) return null;
  const today = grid.data.today;
  return (
    <section aria-label="Effort tuần này" className="panel overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-5 py-3">
        <div className="flex items-center gap-2">
          <UnitTags units={["ADS"]} />
          <h3 className="text-sm font-semibold">Effort tuần này</h3>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          Đang ôm <TokenBadge value={person.open_tokens} size="sm" /> ({person.open_tasks}{" "}
          task) · <TokenBadge value={person.daily_tokens} suffix="token/ngày" size="sm" />
        </p>
      </header>
      <ul className="grid grid-cols-4 sm:grid-cols-7" aria-label="Token theo ngày">
        {person.days.map((day) => {
          const over = day.left < 0;
          const off = day.budget === 0 && day.used === 0;
          return (
            <li
              key={day.date}
              aria-current={day.date === today ? "date" : undefined}
              className={`min-w-0 px-3 py-3 text-center ${day.date === today ? "bg-[var(--accent-soft)]" : ""}`}
            >
              <p className="text-[11px] font-medium text-[var(--text-muted)]">{dayHeading(day.date)}</p>
              {off ? (
                <p className="mt-1 text-xs text-[var(--text-muted)]">nghỉ</p>
              ) : (
                <>
                  <p className="mt-1 inline-flex items-center gap-1 text-base font-bold tabular-nums text-[var(--warn)]">
                    <BoltIcon />
                    {formatTokens(day.used)}/{formatTokens(day.budget)}
                  </p>
                  <p className={`text-sm font-bold ${over ? "text-[var(--bad)]" : "text-[var(--good)]"}`}>
                    {over ? `vượt ${formatTokens(-day.left)}` : `còn ${formatTokens(day.left)}`}
                  </p>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Members({
  me,
  month,
  onMonth,
  unit,
  onUnit,
  includeInactive,
  onIncludeInactive,
  teamOnly,
  onTeamOnly,
  query,
}: {
  me: AccountMe;
  month: string;
  onMonth: (value: string) => void;
  unit: string;
  onUnit: (value: string) => void;
  includeInactive: boolean;
  onIncludeInactive: (value: boolean) => void;
  /** "Thành viên nhóm tôi" (the default) or everybody the viewer is shown. */
  teamOnly: boolean;
  onTeamOnly: (value: boolean) => void;
  query: UseQueryResult<AccountMembers>;
}) {
  // What the server actually listed: the viewer's team, or the full roster.
  const showingTeam = query.data?.team ?? false;
  const hasTeam = query.data?.has_team ?? false;
  const canViewAll = query.data?.can_view_all ?? true;
  const queryClient = useQueryClient();
  const [sort, setSort] = useState<"none" | "desc" | "asc">("none");
  const [notice, setNotice] = useState<string | null>(null);
  // Who sees the reset control. A convenience only: the server checks every
  // reset again (ADMIN may not reset an OWNER, for one).
  const mayReset = me.role === "OWNER" || me.role === "ADMIN";
  // 0065: the employment status is editable by OWNER / ADMIN and the HR_STATUS holders.
  const unitsMe = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const mayHr = mayReset || Boolean(unitsMe.data?.resources?.hr);
  const viewer = { role: me.role, userId: me.user_id };
  // Off = ORD does not count tokens: no "Effort" column.
  const effortOn = useEffortEnabled();
  const refreshMembers = () => {
    void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
    void queryClient.invalidateQueries({ queryKey: ["units"] });
  };

  const reset = useMutation({
    mutationFn: (row: MemberRow) => api.resetMemberPassword(row.user_id),
    onSuccess: (_data, row) => {
      setNotice(`Đã gửi mật khẩu tạm qua Telegram cho ${row.full_name}.`);
      void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
    },
  });

  // "Phòng ban" filter: the departments the listed people sit in (client-side).
  const [department, setDepartment] = useState("");
  const departmentOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of query.data?.members ?? []) {
      for (const seat of row.departments ?? []) seen.set(seat.department_id, seat.name);
    }
    return Array.from(seen, ([id, name]) => ({ id, name }));
  }, [query.data]);
  const rows = useMemo(() => {
    const list = [...(query.data?.members ?? [])].filter((row) => {
      if (!department) return true;
      const seats = row.departments ?? [];
      if (department === NO_DEPARTMENT) return seats.length === 0;
      return seats.some((seat) => seat.department_id === department);
    });
    if (sort === "none") return list;
    const sign = sort === "desc" ? -1 : 1;
    return list.sort((a, b) => sign * (Number(a.stats.points) - Number(b.stats.points)));
  }, [query.data, sort, department]);

  const nextSort = () => setSort((was) => (was === "desc" ? "asc" : "desc"));
  const th = "text-right";

  return (
    <section className="space-y-4" aria-labelledby="account-members">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="account-members" className="text-lg font-semibold tracking-tight">
            Thành viên
          </h2>
          <p className="mt-0.5 text-sm text-[var(--text-muted)]">
            {showingTeam ? "Nhóm của tôi · " : ""}
            {monthLabel(month)} · {rows.length} người
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <MonthField month={month} onMonth={onMonth} />
          {hasTeam && canViewAll ? (
            <div role="tablist" aria-label="Phạm vi thành viên" className="flex gap-1.5">
              {[
                { team: true, label: "Thành viên nhóm tôi" },
                { team: false, label: "Tất cả" },
              ].map((item) => (
                <button
                  key={item.label}
                  type="button"
                  role="tab"
                  aria-selected={teamOnly === item.team}
                  onClick={() => onTeamOnly(item.team)}
                  className={`min-h-10 rounded-lg border px-3 text-sm font-medium ${teamOnly === item.team
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                    : "border-[var(--border)] hover:bg-[var(--surface-muted)]"
                    }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
          {showingTeam || departmentOptions.length === 0 ? null : (
            <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
              Phòng ban
              <Select
                value={department}
                onChange={(event) => setDepartment(event.target.value)}
                className="min-h-10 text-sm"
              >
                <option value="">Tất cả</option>
                {departmentOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
                <option value={NO_DEPARTMENT}>Chưa có phòng ban</option>
              </Select>
            </label>
          )}
          {mayReset ? (
            <label className="inline-flex min-h-10 items-center gap-2 text-sm text-[var(--text-muted)]">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(event) => onIncludeInactive(event.target.checked)}
              />
              Hiện cả tài khoản đã vô hiệu hoá
            </label>
          ) : null}
        </div>
      </div>

      {notice ? <Notice onDismiss={() => setNotice(null)}>{notice}</Notice> : null}
      {query.isError ? <ErrorBox error={query.error} onRetry={() => query.refetch()} /> : null}
      {query.isPending ? <Loading label="Đang tải thành viên…" /> : null}

      {query.data ? (
        <div className="panel">
          <div className="table-scroll-x">
            <table className="table-dense min-w-[1400px]">
              <thead>
                <tr>
                  <th>Thành viên</th>
                  <th>Phòng ban</th>
                  <th>Mã nhân viên</th>
                  <th>Nhân sự</th>
                  <th
                    className={th}
                    aria-sort={sort === "desc" ? "descending" : sort === "asc" ? "ascending" : "none"}
                  >
                    <button
                      type="button"
                      onClick={nextSort}
                      className="inline-flex items-center gap-1 uppercase hover:text-[var(--text)]"
                    >
                      Điểm
                      <span aria-hidden="true">{sort === "desc" ? "↓" : sort === "asc" ? "↑" : "↕"}</span>
                    </button>
                  </th>
                  <th className={th}>Công đoạn xong</th>
                  <th className={th}>Đang làm</th>
                  <th className={th}>Trả sửa</th>
                  <th className={th}>Đúng hạn</th>
                  <th className={th}>Không bị trả</th>
                  {effortOn ? <th className={th}>Effort</th> : null}
                  <th className={th}>Điểm HS</th>
                  <th className={th}>Order tạo / xong</th>
                  <th className={th}>PR phụ trách</th>
                  <th className={th}>PR sản xuất</th>
                  <th className={th}>PR duyệt</th>
                  <th className={th}>KPI tính</th>
                  <th>Đăng nhập gần nhất</th>
                  <th>Mật khẩu</th>
                  {mayReset ? <th aria-label="Thao tác" /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td
                      colSpan={(mayReset ? 20 : 19) - (effortOn ? 0 : 1)}
                      className="text-[var(--text-muted)]"
                    >
                      Không có thành viên nào trong bộ lọc này.
                    </td>
                  </tr>
                ) : null}
                {rows.map((row) => {
                  const active = row.active ?? true;
                  return (
                    <tr
                      key={row.user_id}
                      data-inactive={active ? undefined : "true"}
                      className={active ? undefined : "opacity-60"}
                    >
                      <td>
                        <div className="flex items-center gap-2.5">
                          <Avatar name={row.full_name} src={row.avatar_url} size={32} />
                          <div className="min-w-0">
                            <PersonLink userId={row.user_id} name={row.full_name} className="person" />
                            <span className="block text-xs text-[var(--text-muted)]">
                              {row.role_label} · {String(row.telegram_user_id)}
                              {me.role === "OWNER" ? (
                                <>
                                  {" · "}
                                  <Link
                                    href={`/admin/people/${row.user_id}`}
                                    className="text-[var(--accent)] underline"
                                  >
                                    Quyền
                                  </Link>
                                </>
                              ) : null}
                            </span>
                            {active ? null : (
                              <span className="mt-0.5 block">
                                <DeactivatedPill />
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <DepartmentTags seats={row.departments ?? []} />
                        {row.seeding ? <SeedingTag /> : null}
                      </td>
                      <td>
                        {mayReset ? (
                          <MemberCodeField
                            key={`${row.user_id}:${row.member_code ?? ""}`}
                            row={row}
                            onSaved={(code) => {
                              setNotice(
                                code
                                  ? `Đã đặt mã nhân viên ${code} cho ${row.full_name}.`
                                  : `Đã xoá mã nhân viên của ${row.full_name}.`,
                              );
                              refreshMembers();
                            }}
                          />
                        ) : (
                          <span className="font-mono text-xs">{row.member_code || "–"}</span>
                        )}
                      </td>
                      <td>
                        {mayHr ? (
                          <EmploymentStatusField
                            key={`${row.user_id}:${row.employment_status ?? ""}`}
                            row={row}
                            onSaved={(label) => {
                              setNotice(`Đã đặt trạng thái nhân sự của ${row.full_name}: ${label}.`);
                              refreshMembers();
                            }}
                          />
                        ) : (
                          <EmploymentStatusPill row={row} />
                        )}
                      </td>
                      <td className="text-right font-semibold tabular-nums">{formatPoints(row.stats.points)}</td>
                      <td className="text-right tabular-nums">{row.stats.nodes_done}</td>
                      <td className="text-right tabular-nums">{row.stats.nodes_in_progress}</td>
                      <td
                        className={`text-right tabular-nums ${row.stats.revisions > 0 ? "text-[var(--warn)]" : ""}`}
                      >
                        {row.stats.revisions}
                      </td>
                      <td className="text-right tabular-nums">
                        {formatRate(row.stats.on_time_rate)}
                        {(row.stats.late_count ?? 0) > 0 ? (
                          <span className="block text-xs text-[var(--bad)]">trễ {row.stats.late_count}</span>
                        ) : null}
                      </td>
                      <td className="text-right tabular-nums">{formatRate(row.stats.first_pass_rate ?? null)}</td>
                      {effortOn ? (
                        <td
                          className={`text-right tabular-nums ${(row.stats.effort_rate ?? 0) > 1 ? "text-[var(--bad)]" : ""
                            }`}
                        >
                          {row.stats.tokens_used === undefined
                            ? "–"
                            : `${formatPoints(row.stats.tokens_used)}/${formatPoints(row.stats.tokens_budget ?? 0)}`}
                        </td>
                      ) : null}
                      <td className="text-right font-semibold tabular-nums">
                        {row.stats.performance_score === null || row.stats.performance_score === undefined
                          ? "–"
                          : formatPoints(row.stats.performance_score)}
                      </td>
                      <td className="text-right tabular-nums">
                        {row.stats.orders_created} / {row.stats.orders_completed}
                      </td>
                      <td className="text-right tabular-nums">{row.stats.pr_contents_owned}</td>
                      <td className="text-right tabular-nums">{row.stats.pr_productions_done}</td>
                      <td className="text-right tabular-nums">{row.stats.pr_approvals}</td>
                      <td className="text-right tabular-nums">{row.stats.work_items_counted}</td>
                      <td className="whitespace-nowrap text-[var(--text-muted)]">
                        {row.last_login_at ? formatAgo(row.last_login_at) : "Chưa đăng nhập"}
                      </td>
                      <td>
                        <PasswordStatus row={row} />
                      </td>
                      {mayReset ? (
                        <td className="whitespace-nowrap">
                          <div className="flex flex-wrap items-center justify-end gap-1.5">
                            {row.user_id === me.user_id || !active ? null : (
                              <ConfirmButton
                                spec={resetMemberPasswordConfirmation(row.full_name)}
                                tone="danger"
                                ariaLabel={`Đặt lại mật khẩu của ${row.full_name}`}
                                className="min-h-9 px-3 text-xs"
                                pending={reset.isPending && reset.variables?.user_id === row.user_id}
                                error={reset.variables?.user_id === row.user_id ? reset.error : undefined}
                                onOpenChange={(open) => {
                                  if (open) {
                                    reset.reset();
                                    setNotice(null);
                                  }
                                }}
                                onConfirm={() => reset.mutate(row)}
                              >
                                Đặt lại mật khẩu
                              </ConfirmButton>
                            )}
                            {mayChangeAccountStatus(viewer, row) ? (
                              <AccountStatusButton
                                userId={row.user_id}
                                name={row.full_name}
                                active={active}
                                onDone={refreshMembers}
                              />
                            ) : null}
                            {mayDeleteAccount(viewer, row) ? (
                              <DeleteAccountButton
                                userId={row.user_id}
                                name={row.full_name}
                                onDone={refreshMembers}
                              >
                                Xoá thành viên
                              </DeleteAccountButton>
                            ) : null}
                          </div>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/** Sign this browser out and go to the login page. Lives here, not in the top bar. */
function LogoutButton({ variant }: { variant: "button" | "link" }) {
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      queryClient.clear();
      window.location.href = "/login";
    },
  });
  const label = logout.isPending ? "Đang thoát…" : "Đăng xuất";
  if (variant === "link") {
    return (
      <button
        type="button"
        onClick={() => logout.mutate()}
        disabled={logout.isPending}
        className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 font-medium text-[var(--text-muted)] transition-colors hover:bg-[var(--bad-soft)] hover:text-[var(--bad)] disabled:opacity-50"
      >
        <Glyph name="logout" size={15} />
        {label}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={() => logout.mutate()}
      disabled={logout.isPending}
      className={`${QUIET_BUTTON} hover:border-[var(--bad)]/40 hover:bg-[var(--bad-soft)] hover:text-[var(--bad)]`}
    >
      <Glyph name="logout" size={16} />
      {label}
    </button>
  );
}
