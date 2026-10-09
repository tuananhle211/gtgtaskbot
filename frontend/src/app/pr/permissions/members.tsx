"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  EffectiveCapability,
  GrantScope,
  Member,
  MemberList,
  MemberResponsibilities,
} from "@/lib/api";
import { api } from "@/lib/api";
import {
  UNASSIGNED_CHANNEL_SCOPE_LABEL,
  UNCLASSIFIED_SCOPE_LABEL,
  contentTypeLabel,
  formatDay,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { UnitTags } from "@/components/unit-panel";
import { taggableUnits } from "@/lib/units";
import {
  changeRoleConfirmation,
  deactivateMemberConfirmation,
  reactivateMemberConfirmation,
  revokeMemberConfirmation,
} from "@/lib/confirmations";

/**
 * *Thành viên.* Everyone with an account, in every state, as cards.
 *
 * What the tab draws is decided by the server's `may_*` flags on the roster
 * and by each row's `status`, and nothing else - a control that would be
 * refused is not drawn, and a control that is drawn is one the same check on
 * the server will admit. Every action here is a route that calls the Telegram
 * commands' `UserService`; the refusals it can still get (a race, a stale
 * screen) arrive as the bot's own reason codes and are worded in `labels.ts`.
 *
 * Vocabulary, because the wrong word on a button is a promise the server does
 * not keep: **Vô hiệu hóa** is `suspend` (reversible: **Kích hoạt lại**);
 * **Loại khỏi PR** is `revoke`, terminal in phase 1 - no button undoes it,
 * because no Telegram command does either. There is no delete. A member who
 * owned content, contributed work or approved something is history, and
 * history keeps its people.
 */
/** The two streams' tags, and who may tag into which, for the roster. */
function useTeams() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  // The streams this person may tag in (`can_tag`; `can_admin` on an older API).
  const admins = taggableUnits(me.data);
  const directory = useQuery({
    queryKey: ["units", "directory"],
    queryFn: api.unitDirectory,
    enabled: admins.length > 0 || (me.data?.can_admin.length ?? 0) > 0,
  });
  const tagsOf = new Map(
    (directory.data ?? []).map((user) => [user.user_id, user.units]),
  );
  return { admins, tagsOf, known: directory.isSuccess };
}

export function MembersTab({
  onAddToAds,
}: {
  /** Open the "Luồng Order (ORD)" tab with this person in its add-member picker. */
  onAddToAds?: (userId: string) => void;
} = {}) {
  const roster = useQuery({ queryKey: ["members"], queryFn: api.listMembers });
  const teams = useTeams();
  const [teamFilter, setTeamFilter] = useState("");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [adding, setAdding] = useState(false);

  const visible = useMemo(() => {
    const rows = roster.data?.members ?? [];
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (teamFilter) {
        const units = teams.tagsOf.get(row.user_id) ?? [];
        if (teamFilter === "BOTH" ? units.length < 2 : !units.includes(teamFilter)) return false;
      }
      if (roleFilter && row.role !== roleFilter) return false;
      if (statusFilter && row.status !== statusFilter) return false;
      if (!needle) return true;
      return (
        row.full_name.toLowerCase().includes(needle) ||
        (row.telegram_username ?? "").toLowerCase().includes(needle) ||
        String(row.telegram_user_id ?? "").includes(needle)
      );
    });
  }, [roster.data, search, roleFilter, statusFilter, teamFilter, teams.tagsOf]);

  if (roster.isPending) return <Loading label="Đang tải thành viên…" />;
  if (roster.isError) return <ErrorBox error={roster.error} onRetry={() => roster.refetch()} />;
  const data = roster.data;
  // Every role the roster contains, for the filter: the assignable three plus
  // the owner, who exists but is never offered by the *assignment* picker.
  const roleOptions = new Map(data.assignable_roles.map((option) => [option.role, option.label]));
  for (const row of data.members) roleOptions.set(row.role, row.role_label);

  return (
    <div className="space-y-4">
      <section className="flex flex-wrap items-center gap-2 text-sm">
        <Pill tone="good">Đang hoạt động: {data.counts.active}</Pill>
        <Pill tone="warn">Tạm khoá: {data.counts.suspended}</Pill>
        <Pill tone="bad">Đã loại khỏi PR: {data.counts.revoked}</Pill>
        {data.counts.pending > 0 ? (
          <Pill tone="neutral">Chờ kích hoạt: {data.counts.pending}</Pill>
        ) : null}
        <span className="text-[var(--text-muted)]">Tổng: {data.counts.total}</span>
        {data.may_add ? (
          <PrimaryButton
            type="button"
            className="ml-auto"
            onClick={() => setAdding((open) => !open)}
            aria-expanded={adding}
          >
            {adding ? "Đóng form thêm" : "Thêm thành viên"}
          </PrimaryButton>
        ) : null}
      </section>

      {adding && data.may_add ? (
        <AddMemberForm roster={data} onDone={() => setAdding(false)} />
      ) : null}

      <section className="grid gap-2 sm:grid-cols-4">
        {teams.known ? (
          <label className="text-sm">
            Luồng
            <Select
              value={teamFilter}
              onChange={(event) => setTeamFilter(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            >
              <option value="">Tất cả</option>
              <option value="PR">Luồng PR</option>
              <option value="ADS">Luồng ORD</option>
              <option value="BOTH">Cả hai luồng</option>
            </Select>
          </label>
        ) : null}
        <label className="text-sm">
          Tìm theo tên, username hoặc Telegram ID
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            placeholder="Ví dụ: Hảo"
          />
        </label>
        <label className="text-sm">
          Vai trò
          <Select
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          >
            <option value="">Tất cả vai trò</option>
            {Array.from(roleOptions.entries()).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Trạng thái
          <Select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="active">Đang hoạt động</option>
            <option value="suspended">Tạm khoá</option>
            <option value="revoked">Đã loại khỏi PR</option>
            <option value="pending">Chờ kích hoạt</option>
          </Select>
        </label>
      </section>

      {visible.length === 0 ? (
        <Empty
          message={
            data.members.length === 0
              ? "Chưa có thành viên nào."
              : "Không có thành viên nào khớp bộ lọc."
          }
        />
      ) : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {visible.map((row) => (
            <MemberCard
              key={row.user_id}
              member={row}
              roster={data}
              units={teams.known ? (teams.tagsOf.get(row.user_id) ?? []) : null}
              admins={teams.admins}
              onAddToAds={onAddToAds}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Every query a membership change can have made stale. */
function useInvalidateMembership() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ["members", "roles", "people", "capabilities", "session", "dashboard"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

/**
 * Registering somebody by Telegram id - `/add_user`, over HTTP.
 *
 * A Telegram id is required because it is the only identity the workspace has:
 * every login link, notification and command is addressed to it. The form says
 * so, and says what happens next (they press Start), rather than offering an
 * email field the backend could not do anything with.
 *
 * The form is the confirmation - see `ACTION_INVENTORY`. Its button names the
 * role it will assign, so the last thing read before pressing is the decision.
 */
function AddMemberForm({ roster, onDone }: { roster: MemberList; onDone: () => void }) {
  const invalidate = useInvalidateMembership();
  const [telegramId, setTelegramId] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState(roster.assignable_roles[0]?.role ?? "EMPLOYEE");
  const add = useMutation({
    mutationFn: () =>
      api.addMember({
        telegram_user_id: Number(telegramId),
        role,
        full_name: fullName.trim() || undefined,
      }),
    onSuccess: () => {
      setTelegramId("");
      setFullName("");
      invalidate();
      onDone();
    },
  });
  const roleLabel = roster.assignable_roles.find((option) => option.role === role)?.label ?? role;
  const idIsValid = /^\d+$/.test(telegramId.trim()) && Number(telegramId) > 0;

  return (
    <form
      data-testid="add-member-form"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
      className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Thêm thành viên</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          Telegram ID
          <input
            required
            inputMode="numeric"
            value={telegramId}
            onChange={(event) => setTelegramId(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            placeholder="Ví dụ: 123456789"
          />
        </label>
        <label className="text-sm">
          Họ tên (tuỳ chọn)
          <input
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          />
        </label>
        <label className="text-sm">
          Vai trò
          <Select
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          >
            {roster.assignable_roles.map((option) => (
              <option key={option.role} value={option.role}>
                {option.label}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Thành viên được nhận diện bằng Telegram ID — đây là cách duy nhất TasksBot biết ai là ai. Sau
        khi thêm, họ gõ <code className="rounded bg-[var(--surface-muted)] px-1">/start</code> trong
        bot Telegram để bắt đầu; tên sẽ tự lấy từ Telegram nếu để trống. Thêm ở đây giống hệt lệnh{" "}
        <code className="rounded bg-[var(--surface-muted)] px-1">/add_user</code> của bot.
      </p>
      {add.isError ? <ErrorBox error={add.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={add.isPending || !idIsValid}>
          {add.isPending ? "Đang thêm…" : `Thêm với vai trò ${roleLabel}`}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onDone}>
          Thôi
        </SecondaryButton>
      </div>
    </form>
  );
}

type Panel = "permissions" | "role" | "deactivate" | "revoke" | null;

function statusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (status === "active") return "good";
  if (status === "suspended") return "warn";
  if (status === "revoked") return "bad";
  return "neutral";
}

function MemberCard({
  member,
  roster,
  units,
  admins,
  onAddToAds,
}: {
  member: Member;
  roster: MemberList;
  /** The person's stream tags; ``null`` while unknown (not a unit admin). */
  units: string[] | null;
  admins: string[];
  onAddToAds?: (userId: string) => void;
}) {
  const invalidate = useInvalidateMembership();
  const queryClient = useQueryClient();
  const joinPr = useMutation({
    mutationFn: () => api.tagUnitMember("PR", { user_id: member.user_id, role: "MEMBER" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["units"] });
    },
  });
  const [panel, setPanel] = useState<Panel>(null);
  const toggle = (next: Panel) => setPanel((current) => (current === next ? null : next));

  const reactivate = useMutation({
    mutationFn: () => api.reactivateMember(member.user_id),
    onSuccess: invalidate,
  });

  const isOwner = member.role === "OWNER";
  const live = member.status === "active" || member.status === "suspended";
  const mayChangeRole = roster.may_change_role && live && !isOwner;
  const mayDeactivate = roster.may_change_status && member.status === "active" && !isOwner;
  const mayReactivate = roster.may_change_status && member.status === "suspended";
  const mayRevoke = roster.may_change_status && live && !isOwner;

  return (
    <li
      data-testid="member-card"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <span className="truncate">{member.full_name || "(chưa có tên)"}</span>
            {units && units.length > 0 ? <UnitTags units={units} /> : null}
            {units && units.length === 0 ? (
              <span
                title="Chưa được gắn luồng nào: chưa thấy task của luồng nào."
                className="unit-tag bg-[var(--surface-muted)] text-[var(--text-muted)]"
              >
                Chưa có luồng
              </span>
            ) : null}
          </p>
          {units && live && !isOwner ? (
            <p className="mt-1.5 flex flex-wrap gap-1.5">
              {admins.includes("PR") && !units.includes("PR") ? (
                <ConfirmButton
                  spec={{
                    title: `Thêm ${member.full_name} vào Luồng PR?`,
                    description:
                      "Người này giữ luồng hiện có và có thêm Luồng PR: thấy task và màn hình của PR.",
                    confirmLabel: "Thêm vào Luồng PR",
                  }}
                  onConfirm={() => joinPr.mutate()}
                  pending={joinPr.isPending}
                  error={joinPr.error}
                >
                  + Luồng PR
                </ConfirmButton>
              ) : null}
              {admins.includes("ADS") && !units.includes("ADS") && onAddToAds ? (
                <SecondaryButton type="button" onClick={() => onAddToAds(member.user_id)}>
                  + Luồng ORD
                </SecondaryButton>
              ) : null}
            </p>
          ) : null}
          <p className="mt-1 flex flex-wrap gap-1.5">
            <Pill tone="neutral">{member.role_label}</Pill>
            <Pill tone={statusTone(member.status)}>{member.status_label}</Pill>
            {member.active_grant_count > 0 ? (
              <Pill tone="good">{member.active_grant_count} quyền duyệt cấp thêm</Pill>
            ) : null}
          </p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {member.telegram_linked
              ? `Telegram: ${member.telegram_username ? `@${member.telegram_username} · ` : ""}${member.telegram_user_id}`
              : "Chưa liên kết Telegram"}
            {member.last_status_changed_at
              ? ` · Trạng thái từ ${formatDay(member.last_status_changed_at)}`
              : ""}
          </p>
          {isOwner ? (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Chủ sở hữu được cấu hình khi triển khai; không vô hiệu hóa, loại khỏi PR hay đổi vai
              trò được.
            </p>
          ) : null}
          {member.status === "revoked" ? (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Đã loại khỏi PR. Lịch sử và quyền trước đây vẫn xem được; không kích hoạt lại được
              trong giai đoạn này.
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <SecondaryButton
          type="button"
          onClick={() => toggle("permissions")}
          aria-expanded={panel === "permissions"}
        >
          Xem quyền hiện tại
        </SecondaryButton>
        {mayChangeRole ? (
          <SecondaryButton
            type="button"
            onClick={() => toggle("role")}
            aria-expanded={panel === "role"}
          >
            Đổi vai trò
          </SecondaryButton>
        ) : null}
        {mayDeactivate ? (
          <SecondaryButton
            type="button"
            onClick={() => toggle("deactivate")}
            aria-expanded={panel === "deactivate"}
          >
            Vô hiệu hóa
          </SecondaryButton>
        ) : null}
        {mayReactivate ? (
          <ConfirmButton
            spec={reactivateMemberConfirmation(member.full_name)}
            tone="primary"
            pending={reactivate.isPending}
            error={reactivate.error}
            onConfirm={() => reactivate.mutate()}
          >
            Kích hoạt lại
          </ConfirmButton>
        ) : null}
        {mayRevoke ? (
          <SecondaryButton
            type="button"
            onClick={() => toggle("revoke")}
            aria-expanded={panel === "revoke"}
            className="border-red-500/50 text-red-700 dark:text-red-300"
          >
            Loại khỏi PR
          </SecondaryButton>
        ) : null}
      </div>

      {panel === "permissions" ? <EffectivePermissionsPanel member={member} /> : null}
      {panel === "role" && mayChangeRole ? (
        <ChangeRolePanel member={member} roster={roster} onDone={() => setPanel(null)} />
      ) : null}
      {panel === "deactivate" && mayDeactivate ? (
        <DeactivatePanel member={member} onDone={() => setPanel(null)} />
      ) : null}
      {panel === "revoke" && mayRevoke ? (
        <RevokePanel member={member} onDone={() => setPanel(null)} />
      ) : null}
    </li>
  );
}

/**
 * *Xem quyền hiện tại.* Fetched when opened, never for every card: twenty
 * cards would otherwise be twenty requests for a question nobody asked yet.
 *
 * Rendered as the server answered - each capability with its provenance. The
 * panel never derives "allowed" from the role or the grant list itself.
 */
function EffectivePermissionsPanel({ member }: { member: Member }) {
  const view = useQuery({
    queryKey: ["effective-permissions", member.user_id],
    queryFn: () => api.effectivePermissions(member.user_id),
  });
  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api.listChannels(),
  });
  const channelNames = new Map((channels.data ?? []).map((row) => [row.id, row.name]));

  if (view.isPending) return <Loading label="Đang tải quyền…" />;
  if (view.isError) return <ErrorBox error={view.error} onRetry={() => view.refetch()} />;
  const data = view.data;
  const groups = new Map<string, EffectiveCapability[]>();
  for (const row of data.capabilities) {
    const list = groups.get(row.domain_label) ?? [];
    list.push(row);
    groups.set(row.domain_label, list);
  }
  return (
    <div
      data-testid="effective-permissions"
      className="mt-3 space-y-3 rounded bg-[var(--surface-muted)] p-3 text-sm"
    >
      <p className="text-xs text-[var(--text-muted)]">
        Quyền hiện tại của {data.full_name} ({data.role_label}), tính đến {formatDay(data.as_of)}.
        Nguồn: <strong>Vai trò</strong> = đi theo vai trò nền; <strong>Quyền cấp thêm</strong> =
        quyền duyệt được cấp trong phạm vi.
      </p>
      {data.is_active ? null : (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Tài khoản đang {data.status_label.toLowerCase()}: không quyền nào dưới đây dùng được cho
          đến khi được kích hoạt lại. Danh sách này là những gì sẽ trở lại khi đó.
        </p>
      )}
      {Array.from(groups.entries()).map(([domain, rows]) => (
        <div key={domain}>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">{domain}</p>
          <ul className="mt-1 space-y-1">
            {rows.map((row) => (
              <li
                key={row.capability}
                data-testid="effective-row"
                data-source={row.source}
                className="flex flex-wrap items-baseline gap-2"
              >
                <span className={row.allowed ? "" : "text-[var(--text-muted)] line-through"}>
                  {row.label}
                </span>
                {row.source === "ROLE" ? <Pill tone="neutral">Vai trò</Pill> : null}
                {row.source === "SCOPED_GRANT" ? <Pill tone="good">Quyền cấp thêm</Pill> : null}
                {row.source === "NONE" ? (
                  <span className="text-xs text-[var(--text-muted)]">Không có</span>
                ) : null}
                {row.grants.map((grant) => (
                  <span key={grant.id} className="basis-full text-xs text-[var(--text-muted)]">
                    Phạm vi: {scopeText(grant.scope, channelNames)}
                    {grant.effective_to ? ` · đến ${formatDay(grant.effective_to)}` : ""}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** One grant's scope as a sentence: classifications, then channels. */
function scopeText(scope: GrantScope, channelNames: Map<string, string>): string {
  const types =
    scope.content_type_scope === "ALL"
      ? "tất cả phân loại"
      : [
          ...scope.content_types.map((code) => contentTypeLabel(code)),
          ...(scope.include_unclassified_content ? [UNCLASSIFIED_SCOPE_LABEL] : []),
        ].join(", ") || "—";
  const channels =
    scope.channel_scope === "ALL"
      ? "tất cả kênh"
      : [
          ...scope.channel_ids.map((id) => channelNames.get(id) ?? id),
          ...(scope.include_unassigned_channel ? [UNASSIGNED_CHANNEL_SCOPE_LABEL] : []),
        ].join(", ") || "—";
  return `${types} · ${channels}`;
}

/** The one base role, changed through the same rule `/change_user_role` obeys. */
function ChangeRolePanel({
  member,
  roster,
  onDone,
}: {
  member: Member;
  roster: MemberList;
  onDone: () => void;
}) {
  const invalidate = useInvalidateMembership();
  const options = roster.assignable_roles.filter((option) => option.role !== member.role);
  const [role, setRole] = useState(options[0]?.role ?? "");
  const change = useMutation({
    mutationFn: () => api.changeMemberRole(member.user_id, { role }),
    onSuccess: () => {
      invalidate();
      onDone();
    },
  });
  const toLabel = options.find((option) => option.role === role)?.label ?? role;
  return (
    <div
      data-testid="change-role-panel"
      className="mt-3 space-y-2 rounded bg-[var(--surface-muted)] p-3"
    >
      <label className="block text-sm">
        Vai trò mới
        <Select
          value={role}
          onChange={(event) => setRole(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        >
          {options.map((option) => (
            <option key={option.role} value={option.role}>
              {option.label}
            </option>
          ))}
        </Select>
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Quyền duyệt cấp thêm của {member.full_name} giữ nguyên; chỉ vai trò nền thay đổi.
      </p>
      <div className="flex flex-wrap gap-2">
        <ConfirmButton
          spec={changeRoleConfirmation(member.full_name, member.role_label, toLabel)}
          tone="primary"
          disabled={!role}
          pending={change.isPending}
          error={change.error}
          onConfirm={() => change.mutate()}
        >
          Đổi vai trò
        </ConfirmButton>
        <SecondaryButton type="button" onClick={onDone}>
          Thôi
        </SecondaryButton>
      </div>
    </div>
  );
}

/**
 * *Vô hiệu hóa thành viên.* Before asking, the panel fetches what the person
 * still holds and puts the counts in the dialog - and says that none of it is
 * reassigned. The counts are the server's; this panel does not compute them.
 */
function DeactivatePanel({ member, onDone }: { member: Member; onDone: () => void }) {
  const invalidate = useInvalidateMembership();
  const held = useQuery({
    queryKey: ["responsibilities", member.user_id],
    queryFn: () => api.memberResponsibilities(member.user_id),
  });
  const [reason, setReason] = useState("");
  const deactivate = useMutation({
    mutationFn: () => api.deactivateMember(member.user_id, { reason: reason.trim() || null }),
    onSuccess: () => {
      invalidate();
      onDone();
    },
  });
  return (
    <div
      data-testid="deactivate-panel"
      className="mt-3 space-y-2 rounded bg-[var(--surface-muted)] p-3"
    >
      {held.isPending ? <Loading label="Đang kiểm tra công việc đang giữ…" /> : null}
      {held.isError ? <ErrorBox error={held.error} onRetry={() => held.refetch()} /> : null}
      {held.data ? <ResponsibilitiesSummary held={held.data} name={member.full_name} /> : null}
      <label className="block text-sm">
        Lý do (vào lịch sử, không hiện cho thành viên)
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <ConfirmButton
          spec={deactivateMemberConfirmation(member.full_name, held.data ?? null)}
          tone="danger"
          disabled={held.isPending}
          pending={deactivate.isPending}
          error={deactivate.error}
          onConfirm={() => deactivate.mutate()}
        >
          Vô hiệu hóa thành viên
        </ConfirmButton>
        <SecondaryButton type="button" onClick={onDone}>
          Thôi
        </SecondaryButton>
      </div>
    </div>
  );
}

function ResponsibilitiesSummary({ held, name }: { held: MemberResponsibilities; name: string }) {
  if (held.total === 0) {
    return (
      <p className="text-sm text-[var(--text-muted)]">
        {name} hiện không giữ nội dung, công việc, task hay quyền duyệt nào đang mở.
      </p>
    );
  }
  return (
    <div data-testid="responsibilities" className="text-sm">
      <p className="font-medium">{name} đang giữ:</p>
      <ul className="mt-1 list-inside list-disc text-[var(--text-muted)]">
        {held.content_owned > 0 ? <li>{held.content_owned} nội dung đang phụ trách</li> : null}
        {held.open_work > 0 ? <li>{held.open_work} công việc đang mở</li> : null}
        {held.open_tasks > 0 ? <li>{held.open_tasks} task chưa hoàn thành</li> : null}
        {held.kpi_drafts > 0 ? (
          <li>
            {held.kpi_drafts} bản nháp KPI
            {held.kpi_awaiting_review > 0 ? ` (${held.kpi_awaiting_review} đang chờ duyệt)` : ""}
          </li>
        ) : null}
        {held.active_grants > 0 ? <li>{held.active_grants} quyền duyệt cấp thêm</li> : null}
      </ul>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Những mục này giữ nguyên và không tự chuyển cho ai khác. Hãy phân công lại nếu cần trước khi
        vô hiệu hóa.
      </p>
    </div>
  );
}

/** *Loại khỏi PR.* `revoke`, with an audit reason. */
function RevokePanel({ member, onDone }: { member: Member; onDone: () => void }) {
  const invalidate = useInvalidateMembership();
  const [reason, setReason] = useState("");
  const revoke = useMutation({
    mutationFn: () => api.revokeMember(member.user_id, { reason: reason.trim() || null }),
    onSuccess: () => {
      invalidate();
      onDone();
    },
  });
  return (
    <div
      data-testid="revoke-panel"
      className="mt-3 space-y-2 rounded bg-[var(--surface-muted)] p-3"
    >
      <p className="text-sm text-[var(--text-muted)]">
        Loại khỏi PR là thu hồi quyền truy cập lâu dài và không kích hoạt lại được trong giai đoạn
        này. Tài khoản và toàn bộ lịch sử vẫn còn. Nếu chỉ cần tạm ngưng, dùng “Vô hiệu hóa”.
      </p>
      <label className="block text-sm">
        Lý do (vào lịch sử, không hiện cho thành viên)
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <ConfirmButton
          spec={revokeMemberConfirmation(member.full_name)}
          tone="danger"
          pending={revoke.isPending}
          error={revoke.error}
          onConfirm={() => revoke.mutate()}
        >
          Loại khỏi PR
        </ConfirmButton>
        <SecondaryButton type="button" onClick={onDone}>
          Thôi
        </SecondaryButton>
      </div>
    </div>
  );
}
