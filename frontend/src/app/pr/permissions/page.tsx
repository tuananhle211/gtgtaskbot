"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Loading } from "@/components/states";
import { TabStrip } from "@/components/pr";
import { GrantsTab } from "./grants";
import { MembersTab } from "./members";
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
  const tab: Tab = requested === "roles" ? "roles" : requested === "grants" ? "grants" : "members";

  // The count on the first tab. Cheap: the roster is the tab's own query and
  // React Query shares the cache, so the strip and the tab make one request.
  const roster = useQuery({ queryKey: ["members"], queryFn: api.listMembers });

  const move = (next: string) => {
    const search = new URLSearchParams(params.toString());
    if (next === "members") search.delete("tab");
    else search.set("tab", next);
    const rendered = search.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname);
  };

  return (
    <div className="space-y-4">
      <TabStrip
        label="Thành viên & Phân quyền"
        active={tab}
        onSelect={move}
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
