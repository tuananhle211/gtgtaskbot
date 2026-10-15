"use client";

import { Suspense, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loading } from "@/components/states";
import { TabStrip } from "@/components/pr";
import { GrantsTab } from "./grants";
import { MembersTab, usePrRoster } from "./members";
import { RolesTab } from "./roles";

/**
 * *Thành viên & Phân quyền.* Three tabs, three concepts, kept apart on purpose:
 *
 * * **Thành viên** - who belongs to the PR workspace, in what state, with which
 *   base role. A `users` row *is* the membership; there is no team table;
 * * **Vai trò & quyền** - what each base role carries. Read-only in phase 1:
 *   roles are code, not rows, and nothing here edits them;
 * * **Quyền duyệt cấp thêm** - the scoped approval grants, exactly as before.
 *
 * `?tab=members|roles|grants` in the URL, like every other view in the admin,
 * so the back button moves between tabs and a link can point at one. An
 * unknown value lands on the roster rather than on nothing.
 *
 * PR only: the ORD roster, its settings and every department's permissions
 * live under "Quản trị" (`/admin/*`). An old `?team=ads` link lands on
 * "Quản trị › Thành viên luồng".
 *
 * No authorization happens in this file or its tabs. The roster carries the
 * server's `may_*` flags for what to draw; every write is a route that calls
 * `UserService` - the Telegram commands' service - and answers with the same
 * refusal codes the bot prints.
 */
export default function PermissionsPage() {
  // `useSearchParams` suspends during prerender and Next refuses to build a
  // page that reads it outside a boundary.
  return (
    <Suspense fallback={<Loading label="Đang tải thành viên…" />}>
      <PermissionsWorkspace />
    </Suspense>
  );
}

type Tab = "members" | "roles" | "grants";

function PermissionsWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const requested = params.get("tab");
  // `?team=ads` / `?tab=ads`: the ORD side that moved to "Quản trị".
  const legacyAds = params.get("team") === "ads" || requested === "ads";
  const tab: Tab = requested === "roles" ? "roles" : requested === "grants" ? "grants" : "members";

  useEffect(() => {
    if (legacyAds) router.replace("/admin/units");
  }, [legacyAds, router]);

  // The count on the first tab: the PR stream's active people. Cheap: the
  // tab's own queries, shared through the React Query cache.
  const roster = usePrRoster();

  const go = (next: { tab: Tab }) => {
    const search = new URLSearchParams(params.toString());
    if (next.tab === "members") search.delete("tab");
    else search.set("tab", next.tab);
    const rendered = search.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname);
  };

  return (
    <div className="space-y-4">
      <TabStrip
        label="Thành viên & Phân quyền (PR)"
        active={tab}
        onSelect={(next) => go({ tab: next as Tab })}
        tabs={[
          {
            key: "members",
            label: "Thành viên",
            count: roster.data?.counts.active,
          },
          { key: "roles", label: "Vai trò & quyền" },
          { key: "grants", label: "Quyền duyệt cấp thêm" },
        ]}
      />
      {tab === "members" ? <MembersTab /> : null}
      {tab === "roles" ? <RolesTab /> : null}
      {tab === "grants" ? <GrantsTab /> : null}
    </div>
  );
}

