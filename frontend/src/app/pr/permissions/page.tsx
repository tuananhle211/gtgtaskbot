"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Loading } from "@/components/states";
import { TabStrip } from "@/components/pr";
import { GrantsTab } from "./grants";
import { MembersTab } from "./members";
import { RolesTab } from "./roles";
import { PermissionMatrix, SettingsForm, UnitPanel } from "@/components/unit-panel";

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
type Team = "pr" | "ads";

function PermissionsWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const requested = params.get("tab");
  const units = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  // The Ads side: for whoever may see Ads (the OWNER, an Ads member).
  const seesAds = Boolean(units.data?.units.some((unit) => unit.code === "ADS"));
  // `?tab=ads` was the Ads team's own tab before it moved under "Thành viên".
  const legacyAds = requested === "ads";
  const tab: Tab = requested === "roles" ? "roles" : requested === "grants" ? "grants" : "members";
  const team: Team = seesAds && (params.get("team") === "ads" || legacyAds) ? "ads" : "pr";

  // The count on the first tab. Cheap: the roster is the tab's own query and
  // React Query shares the cache, so the strip and the tab make one request.
  const roster = useQuery({ queryKey: ["members"], queryFn: api.listMembers });

  const go = (next: { tab?: Tab; team?: Team; add?: string }) => {
    const search = new URLSearchParams(params.toString());
    const nextTab = next.tab ?? tab;
    if (nextTab === "members") search.delete("tab");
    else search.set("tab", nextTab);
    const nextTeam = next.team ?? team;
    if (nextTeam === "ads" && nextTab !== "roles") search.set("team", "ads");
    else search.delete("team");
    if (next.add) search.set("add", next.add);
    else search.delete("add");
    const rendered = search.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname);
  };

  return (
    <div className="space-y-4">
      <TabStrip
        label="Thành viên & Phân quyền"
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
      {tab !== "roles" && seesAds ? (
        <TeamTabs active={team} onSelect={(next) => go({ team: next })} />
      ) : null}
      {tab === "members" && team === "pr" ? (
        <MembersTab
          onAddToAds={seesAds ? (userId) => go({ team: "ads", add: userId }) : undefined}
        />
      ) : null}
      {tab === "members" && team === "ads" ? (
        <UnitPanel code="ADS" preselect={params.get("add") ?? undefined} settings={false} />
      ) : null}
      {tab === "roles" ? <RolesTab /> : null}
      {tab === "grants" && team === "pr" ? <GrantsTab /> : null}
      {tab === "grants" && team === "ads" ? (
        <div className="space-y-4">
          <PermissionMatrix
            code="ADS"
            onDone={() => void queryClient.invalidateQueries({ queryKey: ["units"] })}
          />
          <SettingsForm
            code="ADS"
            onDone={() => void queryClient.invalidateQueries({ queryKey: ["units"] })}
          />
        </div>
      ) : null}
    </div>
  );
}

/** Phòng PR / Phòng Ads under "Thành viên" and "Quyền duyệt cấp thêm". */
function TeamTabs({ active, onSelect }: { active: Team; onSelect: (team: Team) => void }) {
  const teams: Array<{ key: Team; unit: string; label: string }> = [
    { key: "pr", unit: "PR", label: "Phòng PR" },
    { key: "ads", unit: "ADS", label: "Phòng Ads" },
  ];
  return (
    <div role="tablist" aria-label="Phòng" className="unit-switch">
      {teams.map((team) => (
        <button
          key={team.key}
          type="button"
          role="tab"
          data-unit={team.unit}
          aria-selected={active === team.key}
          aria-current={active === team.key ? "true" : undefined}
          onClick={() => onSelect(team.key)}
        >
          {team.label}
        </button>
      ))}
    </div>
  );
}
