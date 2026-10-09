"use client";

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
  mayChangeAccountStatus,
  UnitTags,
  UntaggedPanel,
} from "@/components/unit-panel";
import { resetMemberPasswordConfirmation } from "@/lib/confirmations";
import { formatAgo, formatWhen, monthLabel } from "@/lib/labels";
import { PasswordDialog, PasswordForm } from "./password";
import { INVITER_ROLES, InvitePanel } from "./invites";
import { STREAM_NAMES, byStreamOrder } from "@/lib/units";

const FIELD =
  "min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] transition-colors focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/15";

/** The quiet bordered button of this page: header actions, row actions. */
const QUIET_BUTTON =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3.5 text-sm font-medium shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50";

type Tab = "hieu-suat" | "tai-khoan" | "thanh-vien" | "moi-thanh-vien";

/** `YYYY-MM` of a date, in the browser's own calendar. */
function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

const POINTS = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
/** `points` is numeric on the server and may arrive as a decimal string. */
const formatPoints = (value: number | string) => POINTS.format(Number(value) || 0);
const formatRate = (value: number | null) =>
  value === null || value === undefined ? "–" : `${Math.round(Number(value) * 100)}%`;

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

  // The roster is offered only to somebody the server shows it to: a 403 hides
  // the tab. Once it has loaded, a later failure (a filter the server refuses)
  // is shown inside the tab instead of taking the tab away mid-use.
  const members = useQuery({
    queryKey: ["account", "members", month, unit, includeInactive],
    queryFn: () => api.accountMembers(month, unit, includeInactive),
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

  // Invites are for system team leads (and ADMIN / OWNER); the server
  // refuses anybody else, so the tab is not offered to them.
  const mayInvite = INVITER_ROLES.has(me.role);
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
            <p className="font-medium text-[var(--text)]">Bạn cần đổi mật khẩu trước khi dùng TasksBot.</p>
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
        {me.units.length > 0 ? (
          <ul aria-label="Luồng của tôi" className="mt-4 flex flex-wrap gap-2">
            {byStreamOrder(me.units).map((item) => (
              <li
                key={item.code}
                className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] py-1 pl-1 pr-3 text-xs"
              >
                <UnitTags units={[item.code]} functionTag={item.function_tag} isLead={item.is_lead} />
                <span className="font-medium">{item.role_label}</span>
                {item.member_code ? (
                  <span className="text-[var(--text-muted)]">
                    Mã <span className="font-semibold text-[var(--text)]">{item.member_code}</span>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
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
              className={`relative min-h-12 shrink-0 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--accent)] ${
                selected ? "text-[var(--text)]" : "text-[var(--text-muted)] hover:text-[var(--text)]"
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
      <SettingsSection id="account-profile" title="Hồ sơ" description="Mọi người trong TasksBot thấy ảnh và tên này.">
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
        <SettingRow label="Luồng">
          {me.units.length === 0 ? (
            <span className="text-[var(--text-muted)]">
              Chưa thuộc luồng nào. Trưởng nhóm sẽ gắn luồng cho bạn.
            </span>
          ) : (
            <ul className="space-y-2">
              {byStreamOrder(me.units).map((item) => (
                <li key={item.code} className="flex flex-wrap items-center gap-2">
                  <UnitTags units={[item.code]} functionTag={item.function_tag} isLead={item.is_lead} />
                  <span>{item.role_label}</span>
                  {item.member_code ? (
                    <span className="text-xs text-[var(--text-muted)]">
                      Mã: <span className="font-semibold text-[var(--text)]">{item.member_code}</span>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </SettingRow>
      </SettingsSection>

      <SettingsSection id="account-security" title="Bảo mật" description="Mật khẩu đăng nhập TasksBot trên web.">
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
      {data ? <StatCards stats={data} /> : null}
    </section>
  );
}

type Tone = "green" | "blue" | "violet" | "amber";
const TONE_ICON: Record<Tone, string> = {
  green: "bg-[var(--good-soft)] text-[var(--good)]",
  blue: "bg-[var(--accent-soft)] text-[var(--accent-strong)]",
  violet: "tone-violet",
  amber: "bg-[var(--warn-soft)] text-[var(--warn)]",
};

function StatCards({ stats }: { stats: MemberStats }) {
  const rate = stats.on_time_rate === null || stats.on_time_rate === undefined ? null : Number(stats.on_time_rate);
  const headline: Array<{ label: string; value: string; hint: string; icon: GlyphName; tone: Tone; meter?: number | null }> = [
    {
      label: "Điểm hiệu suất",
      value: formatPoints(stats.points),
      hint: "Điểm loại video của các công đoạn đã xong",
      icon: "star",
      tone: "green",
    },
    {
      label: "Công đoạn hoàn thành",
      value: String(stats.nodes_done),
      hint: "Công đoạn sản xuất được duyệt trong tháng",
      icon: "check-circle",
      tone: "blue",
    },
    {
      label: "Tỷ lệ đạt ngay",
      value: formatRate(stats.on_time_rate),
      hint: "Công đoạn được duyệt không bị trả",
      icon: "target",
      tone: "violet",
      meter: rate,
    },
    {
      label: "Mục KPI được tính",
      value: String(stats.work_items_counted),
      hint: "Mục đã tính KPI trong tháng, cả PR và ORD",
      icon: "list",
      tone: "amber",
    },
  ];
  const groups: Array<{
    unit: string;
    title: string;
    items: Array<{ label: string; value: string; hint?: string; warn?: boolean }>;
  }> = [
    {
      unit: "ADS",
      title: "Order ORD",
      items: [
        { label: "Đang làm", value: String(stats.nodes_in_progress), hint: "Đang giao cho bạn, mọi tháng" },
        { label: "Bị trả sửa", value: String(stats.revisions), warn: stats.revisions > 0 },
        { label: "Order đã tạo / hoàn thành", value: `${stats.orders_created} / ${stats.orders_completed}` },
      ],
    },
    {
      unit: "PR",
      title: "Nội dung PR",
      items: [
        { label: "Nội dung phụ trách", value: String(stats.pr_contents_owned) },
        { label: "Sản xuất", value: String(stats.pr_productions_done) },
        { label: "Lượt duyệt", value: String(stats.pr_approvals) },
      ],
    },
  ];

  return (
    <div className="space-y-4">
      <ul aria-label="Chỉ số chính" className="grid grid-cols-1 gap-4 min-[420px]:grid-cols-2 lg:grid-cols-4">
        {headline.map((card) => (
          <li key={card.label} className="panel flex flex-col p-5">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-medium text-[var(--text-muted)]">{card.label}</p>
              <span className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONE_ICON[card.tone]}`}>
                <Glyph name={card.icon} size={18} />
              </span>
            </div>
            <p className="mt-2 text-3xl font-semibold tabular-nums tracking-tight">{card.value}</p>
            {card.meter !== undefined ? (
              <div
                aria-hidden="true"
                className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--surface-muted)] ring-1 ring-inset ring-[var(--border)]"
              >
                <div
                  className="h-full rounded-full bg-[var(--good)] transition-[width]"
                  style={{ width: `${Math.round((card.meter ?? 0) * 100)}%` }}
                />
              </div>
            ) : null}
            <p className="mt-auto pt-2 text-xs leading-relaxed text-[var(--text-muted)]">{card.hint}</p>
          </li>
        ))}
      </ul>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.map((group) => (
          <section key={group.unit} aria-label={group.title} className="panel overflow-hidden">
            <header className="flex items-center gap-2 border-b border-[var(--border)] px-5 py-3">
              <UnitTags units={[group.unit]} />
              <h3 className="text-sm font-semibold">{group.title}</h3>
            </header>
            <ul aria-label={`Số liệu ${group.title}`} className="grid grid-cols-3 divide-x divide-[var(--border)]">
              {group.items.map((item) => (
                <li key={item.label} className="min-w-0 px-4 py-4 sm:px-5">
                  <p className="text-xs font-medium leading-snug text-[var(--text-muted)]">{item.label}</p>
                  <p
                    className={`mt-1.5 text-xl font-semibold tabular-nums tracking-tight ${
                      item.warn ? "text-[var(--warn)]" : ""
                    }`}
                  >
                    {item.value}
                  </p>
                  {item.hint ? <p className="mt-1 text-[11px] leading-snug text-[var(--text-muted)]">{item.hint}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

// --- Thành viên ---------------------------------------------------------------

function PasswordStatus({ row }: { row: MemberRow }) {
  if (row.locked) return <span className="st st-red">Đang khoá</span>;
  if (row.password_temporary) return <span className="st st-amber">Mật khẩu tạm</span>;
  return row.has_custom_password ? (
    <span className="st st-green">Đã đổi</span>
  ) : (
    <span className="st st-amber">Mặc định</span>
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
  query,
}: {
  me: AccountMe;
  month: string;
  onMonth: (value: string) => void;
  unit: string;
  onUnit: (value: string) => void;
  includeInactive: boolean;
  onIncludeInactive: (value: boolean) => void;
  query: UseQueryResult<AccountMembers>;
}) {
  const queryClient = useQueryClient();
  const [sort, setSort] = useState<"none" | "desc" | "asc">("none");
  const [notice, setNotice] = useState<string | null>(null);
  // Who sees the reset control. A convenience only: the server checks every
  // reset again (ADMIN may not reset an OWNER, for one).
  const mayReset = me.role === "OWNER" || me.role === "ADMIN";
  const viewer = { role: me.role, userId: me.user_id };
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

  const rows = useMemo(() => {
    const list = [...(query.data?.members ?? [])];
    if (sort === "none") return list;
    const sign = sort === "desc" ? -1 : 1;
    return list.sort((a, b) => sign * (Number(a.stats.points) - Number(b.stats.points)));
  }, [query.data, sort]);

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
            {monthLabel(month)} · {rows.length} người
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <MonthField month={month} onMonth={onMonth} />
          <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
            Luồng
            <Select value={unit} onChange={(event) => onUnit(event.target.value)} className="min-h-10 text-sm">
              <option value="ALL">Tất cả</option>
              <option value="ADS">{STREAM_NAMES.ADS}</option>
              <option value="PR">{STREAM_NAMES.PR}</option>
            </Select>
          </label>
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

      <UntaggedPanel />

      {notice ? <Notice onDismiss={() => setNotice(null)}>{notice}</Notice> : null}
      {query.isError ? <ErrorBox error={query.error} onRetry={() => query.refetch()} /> : null}
      {query.isPending ? <Loading label="Đang tải thành viên…" /> : null}

      {query.data ? (
        <div className="panel">
          <div className="table-scroll-x">
            <table className="table-dense min-w-[1100px]">
              <thead>
                <tr>
                  <th>Thành viên</th>
                  <th>Luồng</th>
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
                    <td colSpan={mayReset ? 14 : 13} className="text-[var(--text-muted)]">
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
                            <span className="person">{row.full_name}</span>
                            <span className="block text-xs text-[var(--text-muted)]">
                              {row.role_label} · {String(row.telegram_user_id)}
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
                        {row.units.length > 0 ? (
                          <UnitTags units={row.units} functionTag={row.function_tag} isLead={row.is_lead} />
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">Chưa có luồng</span>
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

// --- Icons ---------------------------------------------------------------------

type GlyphName =
  | "camera"
  | "key"
  | "logout"
  | "lock"
  | "star"
  | "check-circle"
  | "target"
  | "list"
  | "x";

/** Stroke icons for this page. Decorative: the text beside them carries the meaning. */
function Glyph({ name, size = 16 }: { name: GlyphName; size?: number }) {
  const paths: Record<GlyphName, React.ReactNode> = {
    camera: (
      <>
        <path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2.5h6L16.5 7h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />
        <circle cx="12" cy="13" r="3.5" />
      </>
    ),
    key: (
      <>
        <circle cx="8" cy="15" r="4" />
        <path d="M10.85 12.15L19 4M16 7l2.5 2.5M14 9l1.5 1.5" />
      </>
    ),
    logout: (
      <>
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
        <path d="M16 17l5-5-5-5M21 12H9" />
      </>
    ),
    lock: (
      <>
        <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
        <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5M12 14.5v2" />
      </>
    ),
    star: <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />,
    "check-circle": (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12.5l2.7 2.7L16 9.8" />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.2" />
      </>
    ),
    list: (
      <>
        <path d="M9 6h11M9 12h11M9 18h11" />
        <path d="M4 6h.01M4 12h.01M4 18h.01" />
      </>
    ),
    x: <path d="M6 6l12 12M18 6L6 18" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {paths[name]}
    </svg>
  );
}
