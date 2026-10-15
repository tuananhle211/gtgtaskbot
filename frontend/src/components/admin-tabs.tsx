"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type UnitsMe } from "@/lib/api";
import { taggableUnits } from "@/lib/units";

/**
 * "Quản trị": one menu entry, three screens - each a route of its own, so a
 * link can point at one and the back button moves between them.
 *
 * * **Phòng ban** (`/admin/departments`) - the one place people are seated
 *   (department, Trưởng phòng / Nhân viên, Trưởng quản lý) and permissions
 *   granted. OWNER edits, ADMIN reads;
 * * **Thành viên luồng** (`/admin/units`) - the stream rosters and what a
 *   row carries besides its seat: member code, NAS, token/ngày, account status;
 * * **Thiết lập ORD** (`/admin/settings`) - the order flow's settings,
 *   catalogues and the Quản trị viên column;
 * * **Kết nối AI** (`/admin/ai`, OWNER only) - the AI services the app calls
 *   (ChatGPT, Gemini, DeepSeek, 9router...) and which serves which purpose.
 *
 * The PR team's own "Thành viên & Phân quyền (PR)" stays in the PR menu, untouched.
 * Who sees which tab is a convenience; every route and write is checked again
 * on the server.
 */
export type AdminTabKey = "departments" | "units" | "settings" | "ai";

export const ADMIN_TABS: ReadonlyArray<{ key: AdminTabKey; href: string; label: string }> = [
  { key: "departments", href: "/admin/departments", label: "Phòng ban" },
  { key: "units", href: "/admin/units", label: "Thành viên luồng" },
  { key: "settings", href: "/admin/settings", label: "Thiết lập ORD" },
  { key: "ai", href: "/admin/ai", label: "Kết nối AI" },
];

/** The "Quản trị" tabs this person may open, in menu order. */
export function adminTabsFor(
  me: UnitsMe | undefined,
  sessionRole?: string,
): Array<(typeof ADMIN_TABS)[number]> {
  const manages = sessionRole === "OWNER" || sessionRole === "ADMIN";
  const streams = Boolean(
    me && (me.can_admin.length > 0 || taggableUnits(me).length > 0),
  );
  const ordSettings = Boolean(me?.can_admin.includes("ADS"));
  const shown: Record<AdminTabKey, boolean> = {
    departments: manages,
    units: streams,
    settings: ordSettings,
    ai: sessionRole === "OWNER",
  };
  return ADMIN_TABS.filter((tab) => shown[tab.key]);
}

/** The tab strip at the top of every "Quản trị" screen. */
export function AdminTabs({ active }: { active: AdminTabKey }) {
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const tabs = adminTabsFor(me.data, session.data?.role);
  // The current screen always shows, even before the queries answer.
  const shown = tabs.some((tab) => tab.key === active)
    ? tabs
    : ADMIN_TABS.filter((tab) => tab.key === active || tabs.includes(tab));
  return (
    <nav
      aria-label="Quản trị"
      className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none]"
    >
      {shown.map((tab) => {
        const selected = tab.key === active;
        return (
          <Link
            key={tab.key}
            href={tab.href}
            aria-current={selected ? "page" : undefined}
            className={`inline-flex min-h-11 shrink-0 items-center rounded-lg px-3.5 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              selected
                ? "bg-[var(--text)] font-medium text-[var(--surface)]"
                : "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
