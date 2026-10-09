"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type UnitsMe } from "@/lib/api";
import { roleLabel } from "@/lib/labels";
import { unitShortLabel, unitTagClass } from "@/lib/units";
import { ErrorBox, Loading } from "@/components/states";
import { NotificationBell } from "@/components/notifications";
import { Avatar } from "@/components/avatar";
import { Logo, LogoMark } from "@/components/logo";

type NavItem = { href: string; label: string; icon: NavIcon };

type NavIcon =
  | "dashboard"
  | "table"
  | "plus"
  | "content"
  | "work"
  | "task"
  | "channel"
  | "people"
  | "report"
  | "admin"
  | "account";

/** The screens every unit shares. */
const COMMON_NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { href: "/tasks", label: "Quản lý task", icon: "table" },
  { href: "/orders/new", label: "Tạo order", icon: "plus" },
];

/**
 * Where an account still on the default password is sent, from every screen.
 * The API refuses everything else until the password changes (403
 * `password_change_required`); this only spares the person a page of refusals.
 */
const FORCED_PASSWORD_CHANGE = "/account?doi-mat-khau=1";

/**
 * The PR module's own screens. Unchanged, and shown only to somebody tagged
 * PR, or to an OWNER / ADMIN: an ORD member or an untagged account has
 * nothing behind these routes (the API answers 404).
 */
const PR_NAV: NavItem[] = [
  // No "Nội dung PR" entry: PR pieces are created from the shared "Tạo order"
  // screen and followed on the shared task table. The board at /pr/content
  // stays reachable from a row and from the table's "bảng chi tiết" link.
  // M1. "Công việc" is the Work Ledger; "Giao việc" is the legacy task module
  // and stays until the bridge milestone decides its future.
  { href: "/pr/work", label: "Công việc", icon: "work" },
  { href: "/pr/tasks", label: "Giao việc", icon: "task" },
  { href: "/pr/channels", label: "Kênh", icon: "channel" },
  { href: "/pr/permissions", label: "Thành viên & Phân quyền", icon: "people" },
  { href: "/pr/reports", label: "Báo cáo", icon: "report" },
];

const ADMIN_NAV: NavItem[] = [
  { href: "/admin/units", label: "Quản trị đơn vị", icon: "admin" },
];

/** Whether the sidebar is folded; shared by every Shell mount in this tab. */
let sidebarCollapsed = false;

/**
 * The public legal pages. Deliberately *not* in the nav - see the footer below.
 */
const LEGAL_LINKS = [
  { href: "/terms", label: "Điều khoản sử dụng" },
  { href: "/privacy", label: "Chính sách quyền riêng tư" },
];

/**
 * What the nav holds for this person.
 *
 * Built from `/api/units/me`. While that is loading, or if it fails, only the
 * shared entries are shown: an account with no stream tag is *not* PR any
 * more, so the PR screens wait until the server says the person is in PR (or
 * sees every stream). The shared screens are always there, so the frame never
 * strands anybody on a blank sidebar.
 *
 * "Quản trị đơn vị" is for whoever administers a unit or may tag members in
 * one (a team lead tags in their own stream there).
 */
export function navFor(me: UnitsMe | undefined, sessionRole?: string): NavItem[] {
  const seesAll =
    Boolean(me?.can_view_all) || sessionRole === "OWNER" || sessionRole === "ADMIN";
  const pr = Boolean(me) && (seesAll || me!.units.some((unit) => unit.code === "PR"));
  const admin = Boolean(
    me && (me.can_admin.length > 0 || (me.can_tag ?? []).length > 0),
  );
  return [...COMMON_NAV, ...(pr ? PR_NAV : []), ...(admin ? ADMIN_NAV : [])];
}

/** Inline stroke icons, one per nav entry. Decorative: the label carries the name. */
function Icon({ name }: { name: NavIcon }) {
  const paths: Record<NavIcon, React.ReactNode> = {
    dashboard: (
      <>
        <rect x="3" y="3" width="7" height="9" rx="1.5" />
        <rect x="14" y="3" width="7" height="5" rx="1.5" />
        <rect x="14" y="12" width="7" height="9" rx="1.5" />
        <rect x="3" y="16" width="7" height="5" rx="1.5" />
      </>
    ),
    table: (
      <>
        <path d="M3 6h18M3 12h18M3 18h18" />
        <path d="M8 3v18" />
      </>
    ),
    plus: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v8M8 12h8" />
      </>
    ),
    content: (
      <>
        <path d="M6 3h9l4 4v14H6z" />
        <path d="M9 12h6M9 16h6" />
      </>
    ),
    work: (
      <>
        <path d="M4 7h16v13H4z" />
        <path d="M9 7V4h6v3" />
      </>
    ),
    task: (
      <>
        <path d="M5 12l4 4L19 6" />
      </>
    ),
    channel: (
      <>
        <circle cx="12" cy="12" r="2" />
        <path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5" />
      </>
    ),
    people: (
      <>
        <circle cx="9" cy="8" r="3.5" />
        <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
        <path d="M17 11h5M19.5 8.5v5" />
      </>
    ),
    report: (
      <>
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </>
    ),
    account: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21a8 8 0 0 1 16 0" />
      </>
    ),
    admin: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
      </>
    ),
  };
  return (
    <svg
      width="18"
      height="18"
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

/**
 * The frame every page sits in: a sidebar of screens, a top bar with the unit
 * switch and the signed-in person, and the page itself using the whole width.
 *
 * It owns the session query, which is what makes an unauthenticated visit render
 * the sign-in prompt once rather than six failing panels. The children are not
 * rendered at all until a session resolves - not because that is a security
 * control (the API refuses regardless) but because a page that renders and then
 * fails everywhere is worse to read than one that says what is wrong.
 *
 * The identity line is name and role; grants are what the Permissions page is
 * for. Nothing in the header gates anything - every write is checked in the
 * Python services.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  // The sidebar folds to icons on lg+. Held in a module-level value, not in
  // browser storage (the security suite forbids any in src/): it survives
  // moving between screens, whose layouts each mount their own Shell, and
  // resets on a full reload.
  const [collapsed, setCollapsed] = useState(sidebarCollapsed);
  const toggleSidebar = () =>
    setCollapsed((was) => {
      sidebarCollapsed = !was;
      return !was;
    });
  const session = useQuery({ queryKey: ["session"], queryFn: api.session });
  // Still on the default password: only /account works until it changes, so
  // every other screen is left before it renders (and before it asks the API
  // for anything the API would refuse).
  const mustChangePassword = Boolean(session.data?.must_change_password);
  const leavingForPasswordChange = mustChangePassword && pathname !== "/account";
  useEffect(() => {
    if (leavingForPasswordChange) router.replace(FORCED_PASSWORD_CHANGE);
    // `router` is left out: a test double hands back a new object per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leavingForPasswordChange]);
  const me = useQuery({
    queryKey: ["units", "me"],
    queryFn: api.unitsMe,
    enabled: Boolean(session.data) && !mustChangePassword,
  });

  const isActive = (href: string) =>
    pathname === href ||
    (href !== "/pr" && pathname.startsWith(`${href}/`)) ||
    pathname === href;
  const nav = navFor(me.data, session.data?.role);
  const tags = me.data?.units ?? [];


  return (
    <div className="min-h-screen lg:flex">
      <aside
        data-collapsed={collapsed ? "true" : undefined}
        className={`border-b border-[var(--border)] bg-[var(--surface)] transition-[width] duration-150 lg:sticky lg:top-0 lg:flex lg:h-screen lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r ${
          collapsed ? "lg:w-[4.5rem]" : "lg:w-60"
        }`}
      >
        <div className={`hidden items-center gap-2.5 pb-4 pt-5 lg:flex ${collapsed ? "justify-center px-2" : "px-4"}`}>
          <Logo size={34} subtitle="Creative Ops" textClassName={collapsed ? "lg:hidden" : ""} />
        </div>
        <nav
          aria-label="Điều hướng chính"
          className="flex gap-1 overflow-x-auto px-3 py-2 [scrollbar-width:none] lg:flex-col lg:overflow-visible lg:px-3 lg:py-0"
        >
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              title={collapsed ? item.label : undefined}
              className={`flex min-h-11 shrink-0 items-center gap-2.5 rounded-lg px-3 text-sm ${collapsed ? "lg:justify-center lg:px-0" : ""} transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
                isActive(item.href)
                  ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                  : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
              }`}
            >
              <Icon name={item.icon} />
              <span className={`whitespace-nowrap ${collapsed ? "lg:sr-only" : ""}`}>{item.label}</span>
            </Link>
          ))}
        </nav>
        {session.data ? (
          <div className={`mt-auto hidden border-t border-[var(--border)] px-4 py-4 ${collapsed ? "" : "lg:block"}`}>
            <p className="text-xs text-[var(--text-muted)]">Luồng của bạn</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {me.isSuccess && tags.length === 0 ? (
                <span className="unit-tag bg-[var(--surface-muted)] text-[var(--text-muted)]">
                  Chưa có luồng
                </span>
              ) : (
                tags.map((tag) => (
                  <span key={tag.code} className={`unit-tag ${unitTagClass(tag.code)}`}>
                    {unitShortLabel(tag.code, tag.short_label)}
                    {tag.function_tag ? ` · ${tag.function_tag}${tag.is_lead ? "★" : ""}` : ""}
                  </span>
                ))
              )}
            </div>
            {me.data?.units.some((tag) => tag.member_code) ? (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Mã:{" "}
                {me.data.units
                  .filter((tag) => tag.member_code)
                  .map((tag) => tag.member_code)
                  .join(" · ")}
              </p>
            ) : null}
          </div>
        ) : null}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex min-h-14 items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--surface)]/95 px-4 backdrop-blur sm:px-6 xl:px-8">
          <div className="flex min-w-0 items-center gap-2.5">
            <button
              type="button"
              onClick={toggleSidebar}
              aria-label={collapsed ? "Mở rộng menu" : "Thu gọn menu"}
              aria-expanded={!collapsed}
              title={collapsed ? "Mở rộng menu" : "Thu gọn menu"}
              className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] lg:inline-flex"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M9 4v16" />
                <path d={collapsed ? "M13 10l2 2-2 2" : "M16 10l-2 2 2 2"} />
              </svg>
            </button>
            <LogoMark size={28} className="shrink-0 lg:hidden" />
            <div className="min-w-0 leading-tight">
              <p className="truncate text-sm font-semibold tracking-tight">
                TasksBot · Creative Ops
              </p>
              {session.data ? (
                <p className="truncate text-xs text-[var(--text-muted)]">
                  {session.data.full_name} · {roleLabel(session.data.role)}
                </p>
              ) : null}
            </div>
          </div>
          {session.data ? (
            <div className="flex shrink-0 items-center gap-2">
              {mustChangePassword ? null : <NotificationBell />}
              <button
                type="button"
                aria-label="Menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((open) => !open)}
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-lg sm:hidden"
              >
                {menuOpen ? "✕" : "☰"}
              </button>
              <Link
                href="/account"
                aria-label="Tài khoản"
                aria-current={pathname === "/account" ? "page" : undefined}
                title="Tài khoản"
                className={`hidden min-h-10 items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-xs hover:bg-[var(--surface-muted)] sm:inline-flex ${
                  pathname === "/account"
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "border-[var(--border)] bg-[var(--surface)]"
                }`}
              >
                <Avatar name={session.data.full_name} src={session.data.avatar_url} size={28} />
                <span className="font-medium">Tài khoản</span>
              </Link>
            </div>
          ) : null}
        </header>

        {menuOpen && session.data ? (
          <div className="border-b border-[var(--border)] bg-[var(--surface)] p-3 sm:hidden">
            <p className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
              <Avatar name={session.data.full_name} src={session.data.avatar_url} size={24} />
              Đang đăng nhập: {session.data.full_name}
            </p>
            <Link
              href="/account"
              onClick={() => setMenuOpen(false)}
              className="mt-2 flex min-h-11 w-full items-center justify-center rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
            >
              Tài khoản · Đăng xuất
            </Link>
          </div>
        ) : null}

        <div className="flex-1 px-4 py-5 sm:px-6 xl:px-8">
          {session.isPending ? (
            <Loading label="Đang kiểm tra phiên đăng nhập…" />
          ) : null}
          {session.isError ? <ErrorBox error={session.error} /> : null}
          {session.data && leavingForPasswordChange ? (
            <Loading label="Đang chuyển tới trang đổi mật khẩu…" />
          ) : null}
          {session.data && !leavingForPasswordChange ? <main>{children}</main> : null}
        </div>

        <footer className="border-t border-[var(--border)] px-4 py-3 sm:px-6 xl:px-8">
          <nav
            aria-label="Thông tin pháp lý"
            className="flex flex-wrap items-center gap-x-4 text-xs text-[var(--text-muted)]"
          >
            {LEGAL_LINKS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="inline-flex min-h-11 items-center underline-offset-2 hover:text-[var(--text)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </footer>
      </div>
    </div>
  );
}
