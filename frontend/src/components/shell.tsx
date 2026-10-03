"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { roleLabel } from "@/lib/labels";
import { ErrorBox, Loading } from "@/components/states";
import { NotificationBell } from "@/components/notifications";

const NAV = [
  { href: "/pr", label: "Tổng quan" },
  { href: "/pr/content", label: "Nội dung" },
  // M1. "Công việc" is the Work Ledger; "Task" is the legacy module and stays
  // until the bridge milestone decides its future. Two entries is acceptable
  // for two milestones and not for ever - renaming Task now would tell people
  // the two are the same thing when the whole point is that they are not.
  { href: "/pr/work", label: "Công việc" },
  { href: "/pr/tasks", label: "Task" },
  { href: "/pr/channels", label: "Kênh" },
  { href: "/pr/permissions", label: "Thành viên & Phân quyền" },
  { href: "/pr/reports", label: "Báo cáo" },
];

/**
 * The public legal pages. Deliberately *not* in `NAV` - see the footer below.
 */
const LEGAL_LINKS = [
  { href: "/terms", label: "Điều khoản sử dụng" },
  { href: "/privacy", label: "Chính sách quyền riêng tư" },
];

/**
 * The frame every PR page sits in: navigation, the signed-in person, sign out.
 *
 * It owns the session query, which is what makes an unauthenticated visit render
 * the sign-in prompt once rather than six failing panels. The children are not
 * rendered at all until a session resolves - not because that is a security
 * control (the API refuses regardless) but because a page that renders and then
 * fails everywhere is worse to read than one that says what is wrong.
 *
 * ## What the header does not say any more
 *
 * Until Step 1E.2 it printed the session's raw capability codes -
 * `PR_CONTENT_CANCEL · PR_CONTENT_EDIT · PR_CONTENT_TRANSITION` - across the top
 * of every screen. That is an implementation detail of the authorization model
 * shown to somebody who wanted to know whose account they were in. The identity
 * line is now name and role; the grants themselves are what the Permissions page
 * is *for*, and it shows them with their Vietnamese labels.
 *
 * Nothing about authority changed: the header never gated anything, and every
 * write is still checked in the Python services.
 *
 * ## Mobile
 *
 * The nav is a horizontally scrolling strip rather than a wrapping row, and the
 * strip scrolls - not the page. Six links wrapping to three lines pushed the
 * content of every screen below the fold on a phone.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const session = useQuery({ queryKey: ["session"], queryFn: api.session });
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      queryClient.clear();
      window.location.href = "/auth/failed";
    },
  });

  const isActive = (href: string) =>
    pathname === href || (href !== "/pr" && pathname.startsWith(href));

  return (
    <div className="mx-auto max-w-6xl px-4 pb-10 pt-4 sm:px-6 sm:pt-6">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          {/*
            The same file the tab icon comes from, served from /public so the
            browser can cache one copy for the whole panel. `next/image` rather
            than a bare <img> because the source is a 1024px app icon and this is
            a 32px mark - unoptimised, every page load would pull ~800KB to draw
            a favicon-sized square.

            The `width`/`height` props and the `h-8 w-8` classes must stay in
            step: the props are what Next resizes the source to, the classes are
            what the browser lays out. Change one and the mark is either blurry
            or downloaded larger than it is drawn.

            `alt=""` on purpose: the product name is already the text beside it,
            and a screen reader announcing "MeoChat" twice is worse than once.
          */}
          <Image
            src="/meochat-icon.png"
            alt=""
            width={32}
            height={32}
            priority
            className="h-8 w-8 shrink-0 rounded-md"
          />
          <div className="min-w-0">
            <p className="text-base font-semibold tracking-tight">MeoChat · PR Admin</p>
            {session.data ? (
              <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">
                {session.data.full_name} · {roleLabel(session.data.role)}
              </p>
            ) : null}
          </div>
        </div>
        {session.data ? (
          <div className="flex shrink-0 items-center gap-2">
            {/*
              Step 1F.2.3d. Inside the `session.data` branch, so the bell exists
              only for somebody signed in - it has nothing to show otherwise, and
              its queries would 401 on every page load.
            */}
            <NotificationBell />
            <button
              type="button"
              aria-label="Menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
              className="flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-lg sm:hidden"
            >
              {menuOpen ? "✕" : "☰"}
            </button>
            <button
              type="button"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="hidden min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-xs hover:bg-[var(--surface-muted)] disabled:opacity-50 sm:block"
            >
              {logout.isPending ? "Đang thoát…" : "Đăng xuất"}
            </button>
          </div>
        ) : null}
      </header>

      <nav
        aria-label="Điều hướng chính"
        // Scrolls itself. `overflow-x: hidden` on the body means a nav that grew
        // past the viewport would otherwise become unreachable rather than
        // draggable.
        className="-mx-4 mb-5 flex gap-1 overflow-x-auto border-b border-[var(--border)] px-4 pb-2 sm:mx-0 sm:px-0 [scrollbar-width:none]"
      >
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={isActive(item.href) ? "page" : undefined}
            className={`flex min-h-11 shrink-0 items-center rounded-lg px-3 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              isActive(item.href)
                ? "bg-[var(--surface)] font-medium text-[var(--text)] shadow-sm"
                : "text-[var(--text-muted)] hover:bg-[var(--surface)]"
            }`}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      {menuOpen && session.data ? (
        <div className="mb-5 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 sm:hidden">
          <p className="text-xs text-[var(--text-muted)]">
            Đang đăng nhập: {session.data.full_name}
          </p>
          <button
            type="button"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="mt-2 min-h-11 w-full rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)] disabled:opacity-50"
          >
            {logout.isPending ? "Đang thoát…" : "Đăng xuất"}
          </button>
        </div>
      ) : null}

      {session.isPending ? <Loading label="Đang kiểm tra phiên đăng nhập…" /> : null}
      {session.isError ? <ErrorBox error={session.error} /> : null}
      {session.data ? <main>{children}</main> : null}

      {/*
        The legal pages, kept out of the primary nav on purpose.

        `NAV` above is the six places somebody goes to do their job, and a
        seventh and eighth entry nobody clicks twice a year would cost every one
        of them room on the phone-width strip that already scrolls. A footer is
        where a reader looks for these, and it costs the working nav nothing.

        Rendered outside the `session.data` branch, unlike `<main>`: /terms and
        /privacy are public, so somebody staring at the sign-in prompt because
        their link expired can still open them. That is the visit most likely to
        need them.
      */}
      <footer className="mt-8 border-t border-[var(--border)] pt-4">
        <nav
          aria-label="Thông tin pháp lý"
          className="flex flex-wrap items-center gap-x-4 text-xs text-[var(--text-muted)]"
        >
          {LEGAL_LINKS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              // `min-h-11` for the same reason every other link in this file has
              // it - a 16px-tall tap target on a phone is a miss, and low
              // emphasis is a matter of colour and size, not of being hard to
              // hit.
              className="inline-flex min-h-11 items-center underline-offset-2 hover:text-[var(--text)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </footer>
    </div>
  );
}
