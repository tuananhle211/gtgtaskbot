"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DesignLibraryLink, type UnitsMe } from "@/lib/api";
import { effortEnabled } from "@/lib/effort";
import { adminTabsFor } from "@/components/admin-tabs";
import { roleLabel } from "@/lib/labels";
import { resourceScreens } from "@/lib/resources";
import { seesAdsSalary } from "@/lib/ads-salary";
import { ErrorBox, Loading } from "@/components/states";
import { NotificationBell } from "@/components/notifications";
import { ThemeToggle } from "@/components/theme-toggle";
import { Avatar } from "@/components/avatar";
import { DepartmentTags } from "@/components/department-tags";
import { Logo, LogoMark, PRODUCT_SUBTITLE } from "@/components/logo";
import { LibraryIcon, isLibraryUrl } from "@/components/library-icon";

type NavItem = {
  href: string;
  label: string;
  icon: NavIcon;
  /** A count drawn beside the label ("việc chờ bạn"); nothing when 0 or absent. */
  badge?: number;
};

/**
 * A branch of the nav tree: a heading that folds, and what sits under it -
 * app screens (`children`) and, for "Thư viện", links out of the app
 * (`external`), which come first.
 */
type NavGroup = {
  /** Stable id, used by "Ghim menu" (`nav_pins` on `/api/units/me`). */
  key: string;
  label: string;
  icon: NavIcon;
  children: NavItem[];
  /** Links that leave the app: never "the current screen", never in `navLinks`. */
  external?: DesignLibraryLink[];
  /**
   * `section`: a small uppercase heading set apart from the entries above it
   * ("THƯ VIỆN"), instead of a heading that reads as one more entry.
   */
  variant?: "section";
};

type NavEntry = NavItem | NavGroup;

const isGroup = (entry: NavEntry): entry is NavGroup => "children" in entry;

/**
 * Every screen of the nav, in order, whatever branch it sits in. App screens
 * only: a branch's external links are not screens and are left out.
 */
export const navLinks = (nav: NavEntry[]): NavItem[] =>
  nav.flatMap((entry) => (isGroup(entry) ? entry.children : [entry]));

type NavIcon =
  | "dashboard"
  | "table"
  | "board"
  | "birthday"
  | "codes"
  | "library"
  | "content"
  | "work"
  | "task"
  | "channel"
  | "people"
  | "report"
  | "admin"
  | "department"
  | "effort"
  | "resource"
  | "inventory"
  | "mine"
  | "request"
  | "lookup"
  | "import"
  | "settings"
  | "salary"
  | "performance"
  | "timeline"
  | "ads"
  | "media"
  | "agents"
  | "cskh"
  | "hr"
  | "account";

/** The screen every unit shares besides the order boards. */
const DASHBOARD_NAV: NavItem = { href: "/dashboard", label: "Dashboard", icon: "dashboard" };

/** "Đội agent" (0091): for whoever has at least one Hermes agent to talk to. */
const AGENTS_NAV: NavItem = { href: "/agents", label: "Đội agent", icon: "agents" };

/**
 * "Chăm sóc khách hàng" (0102): Hộp chat page (CSKH INBOX, badge = unread
 * customer messages), Hộp chat Zalo (ZALO, badge = unread Zalo messages),
 * Kết nối Zalo (ZALO or MANAGE), Kết nối page + Nhãn & câu mẫu (MANAGE).
 */
function cskhBranch(me: UnitsMe | undefined): NavGroup | null {
  const cskh = me?.cskh;
  if (!cskh || !(cskh.inbox || cskh.zalo || cskh.manage)) return null;
  return {
    key: "cskh",
    label: "Chăm sóc khách hàng",
    icon: "cskh",
    children: [
      ...(cskh.inbox
        ? [{ href: "/cskh", label: "Hộp chat page", icon: "cskh" as NavIcon, badge: cskh.unread || undefined }]
        : []),
      ...(cskh.zalo
        ? [
            {
              href: "/cskh/zalo",
              label: "Hộp chat Zalo",
              icon: "agents" as NavIcon,
              badge: cskh.zalo_unread || undefined,
            },
          ]
        : []),
      ...(cskh.zalo || cskh.manage
        ? [{ href: "/cskh/zalo/accounts", label: "Kết nối Zalo", icon: "people" as NavIcon }]
        : []),
      ...(cskh.manage
        ? [
            { href: "/cskh/pages", label: "Kết nối page", icon: "settings" as NavIcon },
            { href: "/cskh/replies", label: "Nhãn & câu mẫu", icon: "library" as NavIcon },
          ]
        : []),
    ],
  };
}

/**
 * "Bảng order": the task tables, one per kind of order. "Order tổng" is the
 * shared table every stream has; "Order sinh nhật" is the Design ban's and
 * shows only when `/api/units/me` says so (`design.birthday`); "Danh mục điểm"
 * (0074) is everybody's in the order flow.
 *
 * There is no "Tạo order" entry: an order is created from the board of its own
 * kind ("+ Tạo order" on /tasks, and on the birthday board).
 */
const ORDER_TOTAL_NAV: NavItem = { href: "/tasks", label: "Order tổng", icon: "table" };
const ORDER_BIRTHDAY_NAV: NavItem = {
  href: "/tasks/birthday",
  label: "Order sinh nhật",
  icon: "birthday",
};
/** 0074: "Danh mục điểm" - every step's task codes and points, read by the
 * whole order flow, kept by each step's Trưởng phòng. */
const TASK_CODES_NAV: NavItem = {
  href: "/tasks/task-codes",
  label: "Danh mục điểm",
  icon: "codes",
};

/**
 * "Tính lương Ads" (2026-10-20): the Ads MKT pay calculator, formulas fixed
 * in `lib/ads-salary.ts`; for people seated in the ADS department and the
 * OWNER / ADMIN (`seesAdsSalary`).
 */
const ADS_SALARY_NAV: NavItem = { href: "/tasks/salary", label: "Tính lương Ads", icon: "salary" };

/** The order boards this person has, as one branch of the nav. */
function orderBoards(me: UnitsMe | undefined, sessionRole?: string): NavGroup {
  return {
    key: "orders",
    label: "Bảng order",
    icon: "board",
    children: [
      ORDER_TOTAL_NAV,
      ...(me?.design?.birthday ? [ORDER_BIRTHDAY_NAV] : []),
      TASK_CODES_NAV,
      ...(seesAdsSalary(me, sessionRole) ? [ADS_SALARY_NAV] : []),
    ],
  };
}

/** The screen that edits the library, for whoever may (`design.manage_library`). */
const LIBRARY_MANAGE_NAV: NavItem = {
  href: "/tasks/library",
  label: "Quản lý thư viện",
  icon: "library",
};

/**
 * "Thư viện": the Design team's own links (a name, a link, a round icon), kept
 * by the Design Leader. They open outside the app, in a new tab; only an
 * `http(s)` link is ever drawn. Whoever may manage the list gets "Quản lý thư
 * viện" at its end - and the branch itself even while the list is empty.
 * Nothing for anybody else: the server sends `library: []` outside the team.
 */
function libraryBranch(me: UnitsMe | undefined): NavGroup | null {
  const links = (me?.design?.library ?? []).filter((link) => isLibraryUrl(link.url));
  const manages = Boolean(me?.design?.manage_library);
  if (links.length === 0 && !manages) return null;
  return {
    key: "library",
    label: "Thư viện",
    icon: "library",
    variant: "section",
    external: links,
    children: manages ? [LIBRARY_MANAGE_NAV] : [],
  };
}

/**
 * "Tài nguyên" (0065): the resources module's screens, one branch, for
 * whoever the departments grant something there (`resources` on
 * `/api/units/me`). Nothing for anybody else: the branch does not exist.
 */
function resourcesBranch(me: UnitsMe | undefined): NavGroup | null {
  const screens = resourceScreens(me);
  if (screens.length === 0) return null;
  return {
    key: "resources",
    label: "Phòng Tài nguyên",
    icon: "resource",
    children: screens.map((screen) => ({
      href: screen.href,
      label: screen.label,
      icon: screen.icon,
      // 2026-10-22: the requests waiting on this person.
      badge: screen.href === "/resources/requests" ? me?.resources?.requests_awaiting : undefined,
    })),
  };
}

/**
 * "Hiệu suất" (the points reports): everybody's own month; "Phòng của tôi"
 * for a Trưởng phòng (their direct reports) or a company viewer; "Toàn công
 * ty" for the OWNER / ADMIN / a `PERFORMANCE` grant; "Hệ số điểm" for the
 * OWNER (`performance` on `/api/units/me`).
 */
function performanceBranch(me: UnitsMe | undefined): NavGroup {
  return {
    key: "performance",
    label: "Hiệu suất",
    icon: "performance",
    children: [
      { href: "/performance/me", label: "Điểm của tôi", icon: "mine" },
      { href: "/performance/timeline", label: "Tiến độ (Gantt)", icon: "timeline" },
      ...(me?.performance?.view_team
        ? [{ href: "/performance/team", label: "Phòng của tôi", icon: "people" as NavIcon }]
        : []),
      ...(me?.performance?.view_all
        ? [{ href: "/performance/company", label: "Toàn công ty", icon: "report" as NavIcon }]
        : []),
      ...(me?.performance?.review_pr
        ? [{ href: "/performance/pr-claims", label: "Điểm PR", icon: "task" as NavIcon }]
        : []),
      ...(me?.performance?.manage_coefficients
        ? [{ href: "/performance/coefficients", label: "Hệ số & định mức", icon: "settings" as NavIcon }]
        : []),
    ],
  };
}

/**
 * "Nhân sự" (0096): everybody's own profile and the org chart; "Tổng quan"
 * for HCNS (`hr.overview`), "Quản lý hồ sơ" for HCNS and the Trưởng phòng
 * (`hr.lists`), "Duyệt hồ sơ" + "Danh mục" for HCNS (`hr.edit`, the badge =
 * change requests waiting).
 */
function hrBranch(me: UnitsMe | undefined): NavGroup | null {
  if (!me) return null;
  const hr = me.hr;
  return {
    key: "hr",
    label: "Nhân sự",
    icon: "hr",
    children: [
      ...(hr?.overview ? [{ href: "/hr", label: "Tổng quan", icon: "report" as NavIcon }] : []),
      ...(hr?.lists
        ? [{ href: "/hr/profiles", label: "Quản lý hồ sơ", icon: "people" as NavIcon }]
        : []),
      ...(hr?.edit
        ? [
            {
              href: "/hr/requests",
              label: "Duyệt hồ sơ",
              icon: "request" as NavIcon,
              badge: hr.pending || undefined,
            },
          ]
        : []),
      { href: "/hr/me", label: "Hồ sơ của tôi", icon: "mine" },
      // 0100: đơn từ, bảng công, phép năm, ca & ngày lễ.
      {
        href: "/hr/leave",
        label: "Đơn từ",
        icon: "request",
        badge: hr?.absences_awaiting || undefined,
      },
      { href: "/hr/timesheet", label: "Bảng công", icon: "timeline" },
      ...(hr?.lists_attendance || hr?.timesheet
        ? [{ href: "/hr/leave/balances", label: "Phép năm", icon: "report" as NavIcon }]
        : []),
      ...(hr?.timesheet
        ? [{ href: "/hr/attendance-settings", label: "Ca & ngày lễ", icon: "settings" as NavIcon }]
        : []),
      { href: "/hr/org", label: "Sơ đồ tổ chức", icon: "department" },
      ...(hr?.edit ? [{ href: "/hr/settings", label: "Danh mục", icon: "settings" as NavIcon }] : []),
    ],
  };
}

/**
 * "Phòng Ads" (0078): the ad-spend dashboard and the ad accounts, for the ADS
 * department and the OWNER / ADMIN (`ads.view` on `/api/units/me`), "Hiệu
 * suất Marketing" (`view_all`), "Nhập doanh thu" / "KPI" / "Ngân sách tuần" / "Tài khoản CRM" (`edit_revenue`); its
 * settings sit in the same branch (27/10): "Kết nối BM" for the OWNER
 * (`ads.manage_connections`), "Tác vụ định kỳ" for the OWNER / ADMIN.
 */
function adsBranch(me: UnitsMe | undefined, sessionRole?: string): NavGroup | null {
  if (!me?.ads?.view) return null;
  const manages = sessionRole === "OWNER" || sessionRole === "ADMIN";
  return {
    key: "ads",
    label: "Phòng Ads",
    icon: "ads",
    children: [
      { href: "/ads", label: "Dashboard Ads", icon: "dashboard" },
      ...(me.ads.edit_revenue
        ? [
            { href: "/ads/revenue", label: "Nhập doanh thu", icon: "request" as NavIcon },
            { href: "/ads/kpi", label: "KPI doanh thu", icon: "mine" as NavIcon },
            { href: "/ads/budget", label: "Ngân sách tuần", icon: "timeline" as NavIcon },
            { href: "/ads/crm", label: "Tài khoản CRM", icon: "people" as NavIcon },
          ]
        : []),
      { href: "/ads/accounts", label: "Tài khoản QC", icon: "inventory" },
      ...(me.ads.manage_connections
        ? [{ href: "/ads/connections", label: "Kết nối BM", icon: "settings" as NavIcon }]
        : []),
      ...(manages ? [{ href: "/ads/jobs", label: "Tác vụ định kỳ", icon: "timeline" as NavIcon }] : []),
    ],
  };
}

/**
 * Where an account still on the default password is sent, from every screen.
 * The API refuses everything else until the password changes (403
 * `password_change_required`); this only spares the person a page of refusals.
 */
const FORCED_PASSWORD_CHANGE = "/account?doi-mat-khau=1";

/**
 * "Phòng PR": the PR module's screens as one branch, like "Bảng order". Shown
 * only to somebody tagged PR, or to an OWNER / ADMIN: an ORD member or an
 * untagged account has nothing behind these routes (the API answers 404).
 */
const PR_GROUP: NavGroup = {
  key: "pr",
  label: "Phòng PR",
  icon: "content",
  children: [
    // "Quản lý task" is PR's own board by stage (/pr/content); PR pieces are
    // still created from the shared "Tạo order" screen (/orders/new) and also
    // followed on the shared task table.
    { href: "/pr/content", label: "Quản lý task", icon: "board" },
    // M1. "Công việc" is the Work Ledger; "Giao việc" is the legacy task module
    // and stays until the bridge milestone decides its future.
    { href: "/pr/work", label: "Công việc", icon: "work" },
    { href: "/pr/tasks", label: "Giao việc", icon: "task" },
    { href: "/pr/channels", label: "Kênh", icon: "channel" },
    { href: "/pr/permissions", label: "Thành viên & Phân quyền", icon: "people" },
    { href: "/pr/reports", label: "Báo cáo", icon: "report" },
  ],
};

/**
 * "Phòng PR", with "Báo cáo công việc/Seeding" (0085) for the seeding group,
 * the Trưởng phòng PR and - since 2026-10-31 - everybody seated in PR
 * (`submit`), its badge = what waits for the Trưởng phòng.
 */
function prBranch(me: UnitsMe | undefined): NavGroup {
  const access = me?.seeding;
  const seeding = Boolean(access?.member || access?.manage || access?.submit);
  if (!seeding) return PR_GROUP;
  const item: NavItem = {
    href: "/seeding",
    label: "Báo cáo công việc/Seeding",
    icon: "people",
    badge: access?.manage ? access.awaiting || undefined : undefined,
  };
  return { ...PR_GROUP, children: [...PR_GROUP.children, item] };
}

/**
 * The ORD token grid (people × days). For whoever hands out ORD work: OWNER /
 * ADMIN, the Trưởng phòng ADS and a function's Leader. Staff see their own
 * effort on /account instead.
 */
const EFFORT_NAV: NavItem[] = [
  { href: "/tasks/effort", label: "Effort", icon: "effort" },
];

/**
 * Whether this person may open the ORD effort grid of others. Nobody while
 * effort (tokens) is switched off for ORD.
 */
export function seesOrdEffort(me: UnitsMe | undefined, sessionRole?: string): boolean {
  if (!me || !effortEnabled(me)) return false;
  if (me.can_view_all || sessionRole === "OWNER" || sessionRole === "ADMIN") return true;
  const ads = me.units.find((unit) => unit.code === "ADS");
  return Boolean(ads && (ads.role === "HEAD" || ads.is_lead));
}

/**
 * "Quản trị": one entry for Phòng ban, Thành viên luồng and Thiết lập ORD
 * (`components/admin-tabs.tsx`); `/admin` goes on to the first one this
 * person may open, and the entry stays lit on all three.
 */
const ADMIN_NAV: NavItem = { href: "/admin", label: "Quản trị", icon: "admin" };

/** Whether the sidebar is folded; shared by every Shell mount in this tab. */
let sidebarCollapsed = false;

/**
 * The branches someone opened ("Bảng order", "Phòng PR", "Thư viện"), by label;
 * shared the same way. Every branch starts folded (2026-11-09: the open menu
 * was too long), so a branch shows its entries only once it is pressed.
 */
const openBranches: Record<string, boolean> = {};

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
 * strands anybody on a blank sidebar. The modules follow the person's
 * permissions (2026-10-17): the order boards are one branch ("Bảng order",
 * for whoever has an ORD seat or grant), whose Design entries follow the
 * `design` flags; the PR
 * screens are another ("Phòng PR"); the Design team's "Thư viện" is the last,
 * after everything else.
 *
 * "Quản trị" is for whoever administers a unit or may tag members in one,
 * and for the OWNER and an ADMIN by their system role (its "Phòng ban" tab).
 */
export function navFor(me: UnitsMe | undefined, sessionRole?: string): NavEntry[] {
  const seesAll =
    Boolean(me?.can_view_all) || sessionRole === "OWNER" || sessionRole === "ADMIN";
  const pr = Boolean(me) && (seesAll || me!.units.some((unit) => unit.code === "PR"));
  const admin =
    adminTabsFor(me, sessionRole).length > 0 ||
    sessionRole === "OWNER" ||
    sessionRole === "ADMIN";
  const effort = seesOrdEffort(me, sessionRole);
  const library = libraryBranch(me);
  const resources = resourcesBranch(me);
  const ads = adsBranch(me, sessionRole);
  const hr = hrBranch(me);
  const cskh = cskhBranch(me);
  // "Bảng order" follows the order module's permissions (an ORD seat or grant
  // gives the ADS entry), like every other branch; it stays while `me` loads.
  const orders = !me || seesAll || me.units.some((unit) => unit.code === "ADS");
  // The order (2026-10-26): Dashboard, Hiệu suất, Bảng order (+ Effort),
  // Phòng Ads (0078), Phòng PR, Phòng Tài nguyên, Thư viện - and Quản trị last.
  return [
    DASHBOARD_NAV,
    performanceBranch(me),
    ...(hr ? [hr] : []),
    ...(me?.agents?.chat ? [AGENTS_NAV] : []),
    ...(cskh ? [cskh] : []),
    ...(orders ? [orderBoards(me, sessionRole)] : []),
    ...(effort ? EFFORT_NAV : []),
    ...(ads ? [ads] : []),
    ...(pr ? [prBranch(me)] : []),
    ...(resources ? [resources] : []),
    ...(library ? [library] : []),
    ...(admin ? [ADMIN_NAV] : []),
  ];
}

/**
 * "Phòng Media" (2026-11-12): a heading over the media branches - "Bảng order"
 * (+ Effort), "Phòng Ads", "Phòng PR" - drawn where the first of them was.
 * Only when the person has two or more of them: a single one sits on its own.
 */
type NavFolder = { key: string; label: string; icon: NavIcon; entries: NavEntry[] };
export type DisplayEntry = NavEntry | NavFolder;
export const isFolder = (entry: DisplayEntry): entry is NavFolder => "entries" in entry;

export const MEDIA_KEY = "media";
const MEDIA_LABEL = "Phòng Media";
const MEDIA_BRANCHES = new Set(["orders", "ads", "pr"]);
const inMedia = (entry: NavEntry): boolean =>
  isGroup(entry) ? MEDIA_BRANCHES.has(entry.key) : entry.href === "/tasks/effort";

/**
 * The menu as drawn: "Ghim menu" (2026-11-12) puts the pinned branches first,
 * in pin order, out of "Phòng Media" and always open; the rest keeps
 * `navFor`'s order with the media branches gathered under "Phòng Media". A pin
 * the menu does not have (a branch this person cannot see) is ignored.
 */
export function arrangeNav(
  nav: NavEntry[],
  pins: readonly string[] = [],
): { pinned: DisplayEntry[]; rest: DisplayEntry[] } {
  const wanted = new Set(pins);
  const pinnedGroups = new Map<string, NavGroup>();
  const media: NavEntry[] = [];
  const rest: DisplayEntry[] = [];
  let mediaAt = -1;
  for (const entry of nav) {
    if (isGroup(entry) && wanted.has(entry.key)) {
      pinnedGroups.set(entry.key, entry);
    } else if (inMedia(entry)) {
      if (mediaAt < 0) mediaAt = rest.length;
      media.push(entry);
    } else {
      rest.push(entry);
    }
  }
  let folder: DisplayEntry | null =
    media.length >= 2
      ? { key: MEDIA_KEY, label: MEDIA_LABEL, icon: "media", entries: media }
      : (media[0] ?? null);
  const pinned: DisplayEntry[] = [];
  for (const key of pins) {
    if (key === MEDIA_KEY) {
      if (folder && isFolder(folder)) {
        pinned.push(folder);
        folder = null;
      }
      continue;
    }
    const group = pinnedGroups.get(key);
    if (group) pinned.push(group);
  }
  if (folder) rest.splice(mediaAt, 0, folder);
  return { pinned, rest };
}

/** "Ghim menu": a push pin, filled once pinned. */
function PinIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z" />
      <path d="M12 14v7" fill="none" />
    </svg>
  );
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
    media: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M10 9.5v5l4.5-2.5z" />
      </>
    ),
    effort: (
      <>
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </>
    ),
    board: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 10h18M9 10v10" />
      </>
    ),
    birthday: (
      <>
        <path d="M4 21h16M5 21v-7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7" />
        <path d="M5 16c1.5 1.3 3 1.3 4.5 0s3-1.3 5 0 3 1.3 4.5 0" />
        <path d="M12 12V8M12 5.5v-.5" />
      </>
    ),
    codes: (
      <>
        <path d="M3 12V4h8l10 10-8 8z" />
        <circle cx="7.5" cy="8.5" r="1.2" />
      </>
    ),
    library: (
      <>
        <path d="M4 5v14M9 5v14" />
        <path d="M13.5 6.5l5 12.5" />
        <path d="M3 19h18" />
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
    resource: (
      <>
        <rect x="3" y="4" width="18" height="6" rx="1.5" />
        <rect x="3" y="14" width="18" height="6" rx="1.5" />
        <path d="M7 7h.01M7 17h.01" />
      </>
    ),
    timeline: (
      <>
        <path d="M3 5h18M3 19h18" />
        <rect x="4" y="8" width="8" height="3" rx="1" />
        <rect x="9" y="13" width="10" height="3" rx="1" />
      </>
    ),
    ads: (
      <>
        <path d="M3 10v4a1 1 0 0 0 1 1h3l6 4V5L7 9H4a1 1 0 0 0-1 1z" />
        <path d="M17 9a4 4 0 0 1 0 6" />
        <path d="M20 6.5a8 8 0 0 1 0 11" />
      </>
    ),
    hr: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <circle cx="9" cy="11" r="2.2" />
        <path d="M5.8 16.5a3.4 3.4 0 0 1 6.4 0" />
        <path d="M14.5 10h4M14.5 13.5h3" />
      </>
    ),
    cskh: (
      <>
        <path d="M4 13v-1a8 8 0 0 1 16 0v1" />
        <rect x="2.5" y="13" width="4" height="6" rx="1.5" />
        <rect x="17.5" y="13" width="4" height="6" rx="1.5" />
        <path d="M19.5 19v.5a2.5 2.5 0 0 1-2.5 2.5h-3" />
      </>
    ),
    agents: (
      <>
        <path d="M4 5h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3v-3H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />
        <path d="M19 9h1a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-1v3l-3.5-3H12" />
        <path d="M6.5 10h.01M9.5 10h.01M12.5 10h.01" />
      </>
    ),
    performance: (
      <>
        <path d="M3 20h18" />
        <path d="M4 16l5-5 4 3 7-8" />
        <path d="M15 6h5v5" />
      </>
    ),
    salary: (
      <>
        <rect x="4" y="3" width="16" height="18" rx="2" />
        <path d="M8 7h8" />
        <path d="M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15v2.5M8 18.5h.01M12 18.5h.01" strokeWidth="2.4" />
      </>
    ),
    // The resources module's screens (2026-10-20), one glyph each.
    inventory: (
      <>
        <path d="M4 6h16M4 12h16M4 18h16" />
        <path d="M8 6h.01M8 12h.01M8 18h.01" strokeWidth="2.6" />
      </>
    ),
    mine: (
      <>
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 20a7 7 0 0 1 14 0" />
        <path d="M15.5 3.5l1.2 2.4 2.6.4-1.9 1.8.5 2.6-2.4-1.2-2.3 1.2.4-2.6-1.9-1.8 2.6-.4z" />
      </>
    ),
    request: (
      <>
        <path d="M6 3h9l4 4v14H6z" />
        <path d="M14 3v5h5" />
        <path d="M12 11v6M9 14h6" />
      </>
    ),
    lookup: (
      <>
        <circle cx="10.5" cy="10.5" r="6" />
        <path d="M15 15l5.5 5.5" />
        <circle cx="10.5" cy="9" r="1.8" />
        <path d="M7.5 14a3.5 3.5 0 0 1 6 0" />
      </>
    ),
    import: (
      <>
        <path d="M12 3v11M7.5 9.5L12 14l4.5-4.5" />
        <path d="M4 17v2.5A1.5 1.5 0 0 0 5.5 21h13a1.5 1.5 0 0 0 1.5-1.5V17" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" />
      </>
    ),
    department: (
      <>
        <rect x="9" y="2.5" width="6" height="5" rx="1" />
        <rect x="2.5" y="16.5" width="6" height="5" rx="1" />
        <rect x="15.5" y="16.5" width="6" height="5" rx="1" />
        <path d="M12 7.5v4.5M5.5 16.5V12h13v4.5" />
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
  // Below `lg` the sidebar is a drawer that slides in from the left (the ☰
  // button). It closes on its own when the screen changes, on Escape and on a
  // tap outside it; while open the page behind does not scroll.
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = before;
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);
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
  const queryClient = useQueryClient();
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
    // The menu's badges ("Đề xuất tài nguyên" waiting on me) follow other
    // people's actions too: look again every minute while the tab is open.
    refetchInterval: 60_000,
  });

  const [openNow, setOpenNow] = useState<Record<string, boolean>>(() => ({
    ...openBranches,
  }));
  // `shown` = what the heading shows now, which in the phone drawer may be
  // the current page's branch opened without a press (below).
  const toggleBranch = (label: string, shown: boolean) =>
    setOpenNow((was) => {
      openBranches[label] = !shown;
      return { ...was, [label]: !shown };
    });
  const nav = navFor(me.data, session.data?.role);
  const links = navLinks(nav);
  const matches = (href: string) =>
    pathname === href || (href !== "/pr" && pathname.startsWith(`${href}/`));
  // The most specific entry wins: /tasks/effort is "Effort" and /tasks/birthday
  // is "Order sinh nhật", not "Order tổng" (which keeps a task's own page).
  const isActive = (href: string) =>
    matches(href) &&
    !links.some(
      (other) =>
        other.href !== href &&
        other.href.startsWith(`${href}/`) &&
        matches(other.href),
    );
  const linkClass = (active: boolean) =>
    `flex min-h-11 shrink-0 items-center gap-2.5 rounded-lg px-3 text-sm ${collapsed ? "lg:justify-center lg:px-0" : ""} transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
      active
        ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
        : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
    }`;
  const navLink = (item: NavItem) => (
    <Link
      key={item.href}
      href={item.href}
      aria-current={isActive(item.href) ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      className={linkClass(isActive(item.href))}
    >
      <span className="relative inline-flex">
        <Icon name={item.icon} />
        {item.badge && collapsed ? (
          // Folded sidebar: a dot on the icon says there is something waiting.
          <span aria-hidden="true" className="absolute -right-1 -top-1 hidden h-2 w-2 rounded-full bg-[var(--brand-magenta)] lg:block" />
        ) : null}
      </span>
      {/* A long label ("Thành viên & Phân quyền (PR)") wraps inside the
          sidebar / drawer instead of running out of it. */}
      <span
        className={`min-w-0 py-1.5 leading-snug ${collapsed ? "lg:sr-only" : ""}`}
      >
        {item.label}
      </span>
      {item.badge ? (
        <span
          className={`ml-auto inline-flex min-w-5 shrink-0 items-center justify-center rounded-full bg-[var(--brand-magenta)] px-1.5 text-[11px] font-semibold leading-5 text-white ${collapsed ? "lg:hidden" : ""}`}
          title={`${item.badge} việc chờ bạn xử lý`}
        >
          {item.badge > 99 ? "99+" : item.badge}
          <span className="sr-only"> việc chờ bạn xử lý</span>
        </span>
      ) : null}
    </Link>
  );
  // A library entry: out of the app, in a new tab, led by its round chip.
  const libraryLink = (link: DesignLibraryLink) => (
    <a
      key={link.id}
      href={link.url}
      target="_blank"
      rel="noopener noreferrer"
      title={collapsed ? link.name : undefined}
      className={linkClass(false)}
    >
      <LibraryIcon name={link.name} icon={link.icon} />
      <span className={`min-w-0 truncate whitespace-nowrap ${collapsed ? "lg:sr-only" : ""}`}>
        {link.name}
      </span>
    </a>
  );

  // Ghim menu (2026-11-12): the person's pins, saved on the server and
  // mirrored at once in the cached /api/units/me (put back if the save fails).
  const pins = me.data?.nav_pins ?? [];
  const { pinned, rest } = arrangeNav(nav, pins);
  const togglePin = (key: string) => {
    const before = pins;
    const next = before.includes(key) ? before.filter((pin) => pin !== key) : [...before, key];
    const write = (value: string[]) =>
      queryClient.setQueryData<UnitsMe>(["units", "me"], (old) =>
        old ? { ...old, nav_pins: value } : old,
      );
    write(next);
    api.setNavPins(next).then(
      (saved) => write(saved.pins),
      () => {
        write(before);
        void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
      },
    );
  };
  const pinButton = (key: string, label: string, isPinned: boolean) => (
    <button
      type="button"
      onClick={() => togglePin(key)}
      aria-pressed={isPinned}
      aria-label={isPinned ? `Bỏ ghim ${label}` : `Ghim ${label}`}
      title={isPinned ? "Bỏ ghim" : "Ghim lên đầu menu"}
      // Always there in the phone drawer; on a wide screen it shows on hover /
      // focus of the heading, and stays lit once pinned.
      className={`inline-flex h-11 w-9 shrink-0 items-center justify-center rounded-lg transition-[opacity,color] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
        isPinned
          ? "text-[var(--accent)]"
          : "text-[var(--text-muted)] lg:opacity-0 lg:group-hover/head:opacity-100 lg:group-focus-within/head:opacity-100"
      }`}
    >
      <PinIcon filled={isPinned} />
    </button>
  );
  /** A branch heading: a fold button, or - pinned - a plain title (always open). */
  const heading = (
    entry: { key: string; label: string; icon: NavIcon },
    opts: { open: boolean; folded: boolean; holdsCurrent: boolean; section: boolean; pinned: boolean },
  ) => {
    const look = `flex min-w-0 flex-1 items-center rounded-lg px-3 text-left ${
      opts.section
        ? "min-h-9 gap-1.5 text-[11px] font-semibold uppercase tracking-wider"
        : "min-h-11 gap-2.5 text-sm"
    }`;
    const content = (
      <>
        {opts.section ? null : <Icon name={entry.icon} />}
        <span className={`whitespace-nowrap ${opts.section ? "" : "flex-1"}`}>{entry.label}</span>
      </>
    );
    return (
      <div className={`group/head flex items-center ${collapsed ? "lg:hidden" : ""}`}>
        {opts.open ? (
          <div className={`${look} font-semibold text-[var(--text)]`}>{content}</div>
        ) : (
          <button
            type="button"
            onClick={() => toggleBranch(entry.label, !opts.folded)}
            aria-expanded={!opts.folded}
            className={`${look} transition-colors hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              opts.folded && opts.holdsCurrent
                ? "font-semibold text-[var(--accent-strong)]"
                : "text-[var(--text-muted)]"
            }`}
          >
            {content}
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              className="shrink-0"
            >
              <path d={opts.folded ? "M9 6l6 6-6 6" : "M6 9l6 6 6-6"} />
            </svg>
          </button>
        )}
        {pinButton(entry.key, entry.label, opts.pinned)}
      </div>
    );
  };
  // Every branch starts folded on the sidebar; in the phone drawer the branch
  // holding the current screen opens until someone folds it. A pinned branch
  // (or one inside a pinned "Phòng Media") is always open.
  const foldedOf = (label: string, holdsCurrent: boolean, open: boolean) =>
    open ? false : label in openNow ? !openNow[label] : !(menuOpen && holdsCurrent);
  const renderGroup = (entry: NavGroup, open: boolean, isPinned: boolean) => {
    const section = entry.variant === "section" && !open;
    const holdsCurrent = entry.children.some((child) => isActive(child.href));
    const folded = foldedOf(entry.label, holdsCurrent, open);
    // With the sidebar folded to icons (lg only) there is no heading to
    // press, so its entries always show.
    return (
      <div
        key={entry.label}
        role="group"
        aria-label={entry.label}
        className={`flex flex-col gap-1 ${section ? "mt-3 border-t border-[var(--border)] pt-3" : ""}`}
      >
        {heading(entry, { open, folded, holdsCurrent, section, pinned: isPinned })}
        <div
          className={`flex-col gap-1 ${folded ? "hidden" : "flex"} ${
            section ? "" : "ml-4 border-l border-[var(--border)] pl-2"
          } ${collapsed ? "lg:ml-0 lg:flex lg:border-l-0 lg:pl-0" : ""}`}
        >
          {(entry.external ?? []).map(libraryLink)}
          {entry.children.map(navLink)}
        </div>
      </div>
    );
  };
  const renderEntry = (entry: DisplayEntry, isPinned: boolean): React.ReactNode => {
    if (isFolder(entry)) {
      const holdsCurrent = entry.entries.some((inner) =>
        isGroup(inner) ? inner.children.some((child) => isActive(child.href)) : isActive(inner.href),
      );
      const folded = foldedOf(entry.label, holdsCurrent, isPinned);
      return (
        <div key={entry.key} role="group" aria-label={entry.label} className="flex flex-col gap-1">
          {heading(entry, { open: isPinned, folded, holdsCurrent, section: false, pinned: isPinned })}
          <div
            className={`flex-col gap-1 ${folded ? "hidden" : "flex"} ml-3 border-l border-[var(--border)] pl-1.5 ${
              collapsed ? "lg:ml-0 lg:flex lg:border-l-0 lg:pl-0" : ""
            }`}
          >
            {entry.entries.map((inner) =>
              isGroup(inner) ? renderGroup(inner, isPinned, false) : navLink(inner),
            )}
          </div>
        </div>
      );
    }
    if (isGroup(entry)) return renderGroup(entry, isPinned, isPinned);
    return navLink(entry);
  };

  return (
    <div className="min-h-screen lg:flex">
      {menuOpen ? (
        // The drawer's backdrop (phones and tablets): a tap outside closes it.
        <div
          aria-hidden="true"
          onClick={() => setMenuOpen(false)}
          className="fixed inset-0 z-40 bg-[var(--brand-navy)]/55 backdrop-blur-[2px] lg:hidden"
        />
      ) : null}
      <aside
        id="app-sidebar"
        data-collapsed={collapsed ? "true" : undefined}
        data-open={menuOpen ? "true" : undefined}
        // `brand-rail` (globals.css): the navy Apexmed rail, in both schemes.
        // Below `lg` it is a drawer off the left edge (invisible while shut,
        // so its links are not tabbed into); from `lg` the sticky sidebar.
        className={`brand-rail fixed inset-y-0 left-0 z-50 flex w-[min(19rem,86vw)] flex-col border-r border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-pop)] transition-[transform,width] duration-200 ease-out lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:shrink-0 lg:translate-x-0 lg:shadow-none ${
          menuOpen ? "translate-x-0" : "-translate-x-full max-lg:invisible"
        } ${collapsed ? "lg:w-[4.5rem]" : "lg:w-60"}`}
      >
        <div className={`flex items-center gap-2.5 pb-4 pt-5 ${collapsed ? "px-4 lg:justify-center lg:px-2" : "px-4"}`}>
          <Logo size={34} subtitle={PRODUCT_SUBTITLE} className={`min-w-0 flex-1 ${collapsed ? "lg:flex-none" : ""}`} textClassName={collapsed ? "lg:hidden" : ""} />
          <button
            type="button"
            onClick={() => setMenuOpen(false)}
            aria-label="Đóng menu"
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] lg:hidden"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        <nav
          aria-label="Điều hướng chính"
          // The list scrolls inside the sidebar / drawer: with a library of up
          // to 30 links it can be taller than the screen. `relative` keeps the
          // badges' absolutely placed screen-reader text inside it.
          className="relative flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden overscroll-contain px-3 py-1"
        >
          {pinned.length > 0 ? (
            // "Ghim menu": the pinned branches first, always open, then a rule.
            <div
              role="group"
              aria-label="Đã ghim"
              className="mb-1 flex flex-col gap-1 border-b border-[var(--border)] pb-2"
            >
              <p
                aria-hidden="true"
                className={`px-3 pt-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)] ${collapsed ? "lg:hidden" : ""}`}
              >
                Đã ghim
              </p>
              {pinned.map((entry) => renderEntry(entry, true))}
            </div>
          ) : null}
          {rest.map((entry) => renderEntry(entry, false))}
        </nav>
        {session.data ? (
          <div className={`mt-auto border-t border-[var(--border)] px-4 py-4 ${collapsed ? "lg:hidden" : ""}`}>
            {/* In the phone drawer: who is signed in, and the way to the account
                screen (sign out lives there). The top bar has room for it from `sm`;
                drawn only while the drawer is open. */}
{menuOpen ? (
            <Link
              href="/account"
              className="mb-3 flex min-h-11 items-center gap-2.5 rounded-lg border border-[var(--border)] px-2.5 text-sm hover:bg-[var(--surface-muted)] sm:hidden"
            >
              <Avatar name={session.data.full_name} src={session.data.avatar_url} size={28} />
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate font-medium">{session.data.full_name}</span>
                <span className="block text-xs text-[var(--text-muted)]">Tài khoản · Đăng xuất</span>
              </span>
            </Link>
            ) : null}
            <p className="text-xs text-[var(--text-muted)]">Phòng ban của bạn</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {me.isSuccess ? (
                <DepartmentTags seats={me.data.departments ?? []} />
              ) : null}
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
        <header className="brand-topbar sticky top-0 z-20 flex min-h-14 items-center justify-between gap-3 border-b border-[var(--border)] px-4 sm:px-6 xl:px-8">
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
            <button
              type="button"
              aria-label="Menu"
              aria-controls="app-sidebar"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
              className="-ml-1 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text)] hover:bg-[var(--surface-muted)] lg:hidden"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <LogoMark size={28} className="hidden shrink-0 sm:block lg:hidden" />
            <div className="min-w-0 leading-tight">
              <p className="truncate font-display text-sm font-semibold tracking-tight">
                APM Management<span className="hidden sm:inline"> <span className="text-[var(--accent)]">·</span> {PRODUCT_SUBTITLE}</span>
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
              <ThemeToggle />
              {mustChangePassword ? null : <NotificationBell />}
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
