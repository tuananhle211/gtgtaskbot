"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type UnitMember, type UnitSettingsInfo, type UntaggedUser } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { DurationManager } from "@/components/durations";
import { PlatformManager } from "@/components/platforms";
import { VideoKindsManager } from "@/components/video-kinds";
import {
  deactivateAccountConfirmation,
  reactivateAccountConfirmation,
  makeAdminConfirmation,
  tagIntoStreamConfirmation,
} from "@/lib/confirmations";
import { formatAgo } from "@/lib/labels";
import {
  canTagIn,
  taggableUnits,
  unitName,
  unitShortLabel,
  unitTagClass,
  byStreamOrder,
} from "@/lib/units";

/** A role in the unit, with ``is_lead`` making a function role its head. */
type Position = { role: string; label: string; is_lead?: boolean };

/** One select value per position: "BIEN_TAP:LEAD" is Trưởng phòng Biên kịch. */
const positionKey = (role: string, isLead?: boolean) => (isLead ? `${role}:LEAD` : role);
const fromPositionKey = (key: string) => ({
  role: key.replace(/:LEAD$/, ""),
  is_lead: key.endsWith(":LEAD"),
});

/** What the short ORD function tags stand for, for the chip's tooltip. */
const FUNCTION_NAMES: Record<string, string> = {
  BT: "Biên kịch",
  TK: "Design",
  D: "Dựng",
};

/** The base roles a team lead may not tag, untag or deactivate. */
const PROTECTED_ROLES = new Set(["OWNER", "ADMIN"]);

/**
 * Stream chips: PR teal, ORD orange - the same colours everywhere. Beside the
 * ORD chip, the person's function there ([BT] / [TK] / [D], ★ for its lead).
 */
export function UnitTags({
  units,
  functionTag,
  isLead = false,
}: {
  units: string[];
  /** ORD only: "BT" / "TK" / "D". */
  functionTag?: string | null;
  isLead?: boolean;
}) {
  if (units.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {byStreamOrder(units).map((unit) => (
        <span key={unit} className="inline-flex gap-1">
          <span className={`unit-tag ${unitTagClass(unit)}`}>{unitShortLabel(unit)}</span>
          {unit === "ADS" && functionTag ? (
            <FunctionTag tag={functionTag} isLead={isLead} />
          ) : null}
        </span>
      ))}
    </span>
  );
}

/** [BT] / [TK] / [D], and [BT★] for the function's lead ("Trưởng"). */
export function FunctionTag({ tag, isLead = false }: { tag: string; isLead?: boolean }) {
  const name = FUNCTION_NAMES[tag] ?? tag;
  const title = isLead ? `Trưởng phòng ${name}` : name;
  return (
    <span
      title={title}
      aria-label={title}
      data-function-tag={tag}
      className="unit-tag unit-tag-ads"
    >
      {tag}
      {isLead ? <span aria-hidden="true">★</span> : null}
    </span>
  );
}

/**
 * "Quản trị viên" in the stream pickers (OWNER only): not a tag but the
 * account's system role - an ADMIN sees every task of both streams.
 */
const ADMIN_POSITION = "SYSTEM:ADMIN";
const ADMIN_LABEL = "Quản trị viên · xem toàn bộ task cả 2 luồng";

/** The signed-in person: their base role decides the account-status controls. */
function useViewer() {
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const role = session.data?.role;
  return {
    userId: session.data?.user_id ?? null,
    role,
    manages: role === "OWNER" || role === "ADMIN",
  };
}

/**
 * Whether the viewer may deactivate / reactivate this account: OWNER and
 * ADMIN only, never themselves, and an ADMIN never an OWNER. A convenience -
 * the server refuses the rest anyway (403 `account_status_forbidden`).
 */
export function mayChangeAccountStatus(
  viewer: { role?: string; userId: string | null },
  target: { user_id: string; role?: string | null },
): boolean {
  if (viewer.role !== "OWNER" && viewer.role !== "ADMIN") return false;
  if (viewer.userId && viewer.userId === target.user_id) return false;
  if (viewer.role === "ADMIN" && target.role === "OWNER") return false;
  return true;
}

/**
 * "Vô hiệu hoá" (destructive, confirmed) or "Kích hoạt lại" for one account.
 * Stream-neutral: `POST /api/account/members/{id}/deactivate|reactivate`.
 */
export function AccountStatusButton({
  userId,
  name,
  active,
  onDone,
}: {
  userId: string;
  name: string;
  active: boolean;
  onDone: () => void;
}) {
  const change = useMutation({
    mutationFn: () =>
      active ? api.deactivateAccount(userId) : api.reactivateAccount(userId),
    onSuccess: onDone,
  });
  return (
    <ConfirmButton
      spec={active ? deactivateAccountConfirmation(name) : reactivateAccountConfirmation(name)}
      onConfirm={() => change.mutate()}
      pending={change.isPending}
      error={change.error}
      tone={active ? "danger" : "secondary"}
      ariaLabel={active ? `Vô hiệu hoá tài khoản ${name}` : `Kích hoạt lại tài khoản ${name}`}
      className="min-h-9 px-3 text-xs"
      onOpenChange={(open) => {
        if (open) change.reset();
      }}
    >
      {active ? "Vô hiệu hoá" : "Kích hoạt lại"}
    </ConfirmButton>
  );
}

/** "Đã vô hiệu hoá": a deactivated account, on any roster. */
export function DeactivatedPill() {
  return <Pill tone="bad">Đã vô hiệu hoá</Pill>;
}

/**
 * One unit's team: its members with every stream tag they hold, the picker
 * that adds anybody - a member of the other stream included, who then carries
 * both tags - the accounts that have no stream yet and, for ORD, the unit's
 * settings and permission matrix. Used by "Quản trị đơn vị" and by the "Luồng
 * Order (ORD)" tab of "Thành viên & Phân quyền".
 *
 * Tag controls are drawn only for a stream in `can_tag` (OWNER / ADMIN, or a
 * team lead tagged in that stream); account status controls only for OWNER /
 * ADMIN. Every write is checked again on the server.
 */
export function UnitPanel({
  code,
  preselect,
  settings = true,
}: {
  code: string;
  /** A user id to put in the "add member" picker, e.g. from a PR member card. */
  preselect?: string;
  /** Show the unit's settings and permission matrix (ORD). */
  settings?: boolean;
}) {
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const [showInactive, setShowInactive] = useState(false);
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const members = useQuery({
    queryKey: showInactive
      ? ["units", code, "members", "include-inactive"]
      : ["units", code, "members"],
    queryFn: () => api.unitMembers(code, showInactive),
  });
  const health = useQuery({
    queryKey: ["units", code, "health"],
    queryFn: () => api.unitHealth(code),
  });
  const canTag = canTagIn(me.data, code);
  const directory = useQuery({
    queryKey: ["units", "directory"],
    queryFn: api.unitDirectory,
    enabled: canTag,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["units"] });
    void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
  };
  if (members.isPending) return <Loading label="Đang tải thành viên…" />;
  if (members.isError)
    return <ErrorBox error={members.error} onRetry={() => members.refetch()} />;
  const administers = Boolean(me.data?.can_admin.includes(code));
  const accountActive = (member: UnitMember) => member.account_active ?? member.active;
  // OWNER / ADMIN choose whether deactivated accounts are listed (to bring
  // them back); everyone else sees what the server lists.
  const rows = members.data.members.filter(
    (member) => !viewer.manages || showInactive || accountActive(member),
  );
  const tagged = new Set(members.data.members.map((member) => member.user_id));
  const tagsOf = new Map(
    (directory.data ?? []).map((user) => [user.user_id, user.units]),
  );
  const candidates = (directory.data ?? []).filter(
    (user) =>
      user.active &&
      !tagged.has(user.user_id) &&
      (viewer.manages || !PROTECTED_ROLES.has(user.base_role)),
  );

  return (
    <div className="space-y-4">
      {health.data && health.data.warnings.length > 0 ? (
        <ul className="space-y-1 rounded-xl border border-amber-400 bg-amber-500/10 p-3 text-sm">
          {health.data.warnings.map((warning) => (
            <li key={warning.code}>{warning.message}</li>
          ))}
        </ul>
      ) : null}
      {canTag ? (
        <AddMember
          key={preselect ?? ""}
          code={code}
          roles={members.data.assignable_roles}
          candidates={candidates}
          initialUser={preselect && !tagged.has(preselect) ? preselect : ""}
          onDone={refresh}
        />
      ) : null}
      {canTag ? <UntaggedPanel defaultStream={code} /> : null}
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)]">
        {viewer.manages ? (
          <div className="flex justify-end border-b border-[var(--border)] px-3 py-2">
            <label className="inline-flex min-h-9 items-center gap-2 text-xs text-[var(--text-muted)]">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(event) => setShowInactive(event.target.checked)}
              />
              Hiện cả tài khoản đã vô hiệu hoá
            </label>
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[56rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
                <th className="px-3 py-2 font-medium">Thành viên</th>
                <th className="px-3 py-2 font-medium">Luồng</th>
                <th className="px-3 py-2 font-medium">Vai trò hệ thống</th>
                <th className="px-3 py-2 font-medium">Vai trò trong luồng</th>
                <th className="px-3 py-2 font-medium">Mã thành viên</th>
                <th className="px-3 py-2 font-medium">Kho cá nhân</th>
                <th className="px-3 py-2 font-medium">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((member) => (
                <MemberRow
                  key={member.user_id}
                  code={code}
                  member={member}
                  units={tagsOf.get(member.user_id) ?? [code]}
                  roles={members.data.assignable_roles}
                  bosses={managerChoices(code, member, members.data.members)}
                  editable={
                    canTag && (viewer.manages || !PROTECTED_ROLES.has(member.base_role))
                  }
                  mayUntag={
                    canTag &&
                    (viewer.manages ||
                      (!PROTECTED_ROLES.has(member.base_role) &&
                        member.user_id !== viewer.userId))
                  }
                  mayChangeStatus={mayChangeAccountStatus(viewer, {
                    user_id: member.user_id,
                    role: member.base_role,
                  })}
                  accountActive={accountActive(member)}
                  onDone={refresh}
                />
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 ? (
          <p className="p-3 text-sm text-[var(--text-muted)]">
            Luồng chưa có thành viên nào.
          </p>
        ) : null}
      </section>
      {code === "ADS" && settings && administers ? (
        <SettingsForm code={code} onDone={refresh} />
      ) : null}
      {code === "ADS" && settings && administers ? <VideoKindsManager code={code} /> : null}
      {code === "ADS" && settings && administers ? <PlatformManager code={code} /> : null}
      {code === "ADS" && settings && administers ? <DurationManager code={code} /> : null}
      {code === "ADS" && settings && administers ? (
        <PermissionMatrix code={code} onDone={refresh} />
      ) : null}
    </div>
  );
}

/**
 * "Chưa có luồng": active accounts nobody has tagged yet (a new invite lands
 * here), each with "Gắn vào luồng…" - pick the stream and the role in the
 * dialog. Only the streams this person may tag in are offered; the section is
 * not drawn for anybody who may tag nowhere.
 */
export function UntaggedPanel({ defaultStream }: { defaultStream?: string }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const streams = taggableUnits(me.data);
  const untagged = useQuery({
    queryKey: ["units", "untagged"],
    queryFn: api.unitsUntagged,
    enabled: streams.length > 0,
    retry: false,
  });
  if (streams.length === 0) return null;
  // A refusal (an API without the route, a lead who lost the tag) hides the
  // section rather than putting an error in the middle of the roster.
  if (untagged.isError) return null;
  const users = untagged.data?.users ?? [];
  return (
    <section
      aria-label="Chưa có luồng"
      className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">
          Chưa có luồng{untagged.data ? ` · ${users.length}` : ""}
        </h2>
        <p className="text-xs text-[var(--text-muted)]">
          Tài khoản mới (vào bằng mã mời) chưa thuộc luồng nào cho đến khi được gắn.
        </p>
      </div>
      {untagged.isPending ? <Loading label="Đang tải…" /> : null}
      {untagged.data && users.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          Không có tài khoản nào đang chờ gắn luồng.
        </p>
      ) : null}
      {users.length > 0 ? (
        <ul className="mt-2 divide-y divide-[var(--border)]">
          {users.map((user) => (
            <li
              key={user.user_id}
              className="flex flex-wrap items-center justify-between gap-2 py-2"
            >
              <span className="min-w-0">
                <span className="block font-medium">{user.full_name}</span>
                <span className="block text-xs text-[var(--text-muted)]">
                  {user.role_label}
                  {user.telegram_username ? ` · @${user.telegram_username}` : ""}
                  {user.created_at ? ` · tạo ${formatAgo(user.created_at)}` : ""}
                </span>
              </span>
              <TagIntoStream
                user={user}
                streams={streams}
                defaultStream={
                  defaultStream && streams.includes(defaultStream) ? defaultStream : streams[0]
                }
              />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function TagIntoStream({
  user,
  streams,
  defaultStream,
}: {
  user: UntaggedUser;
  streams: string[];
  defaultStream: string;
}) {
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const [stream, setStream] = useState(defaultStream);
  const roster = useQuery({
    queryKey: ["units", stream, "members"],
    queryFn: () => api.unitMembers(stream),
  });
  const roles = roster.data?.assignable_roles ?? [];
  const [chosen, setChosen] = useState("");
  const position =
    chosen === ADMIN_POSITION && viewer.role === "OWNER"
      ? chosen
      : chosen && roles.some((option) => positionKey(option.role, option.is_lead) === chosen)
      ? chosen
      : roles[0]
        ? positionKey(roles[0].role, roles[0].is_lead)
        : "";
  const roleText =
    roles.find((option) => positionKey(option.role, option.is_lead) === position)?.label ??
    "đã chọn";
  const admin = position === ADMIN_POSITION;
  const tag = useMutation({
    mutationFn: async (): Promise<unknown> =>
      admin
        ? api.setSystemRole(user.user_id, "ADMIN")
        : api.tagUnitMember(stream, { user_id: user.user_id, ...fromPositionKey(position) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["units"] });
      void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
    },
  });
  const spec = {
    ...(admin
      ? makeAdminConfirmation(user.full_name)
      : tagIntoStreamConfirmation(user.full_name, unitName(stream), roleText)),
    details: (
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs">
          Luồng
          <Select
            value={stream}
            onChange={(event) => {
              setStream(event.target.value);
              setChosen("");
            }}
          >
            {streams.map((code) => (
              <option key={code} value={code}>
                {unitName(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          Vai trò trong luồng
          <Select value={position} onChange={(event) => setChosen(event.target.value)}>
            {roles.map((option) => (
              <option
                key={positionKey(option.role, option.is_lead)}
                value={positionKey(option.role, option.is_lead)}
              >
                {option.label}
              </option>
            ))}
            {viewer.role === "OWNER" ? (
              <option value={ADMIN_POSITION}>{ADMIN_LABEL}</option>
            ) : null}
          </Select>
        </label>
      </div>
    ),
  };
  return (
    <ConfirmButton
      spec={spec}
      onConfirm={() => tag.mutate()}
      pending={tag.isPending}
      error={tag.error}
      confirmDisabled={!position}
      tone="secondary"
      ariaLabel={`Gắn ${user.full_name} vào luồng`}
      className="min-h-9 px-3 text-xs"
      onOpenChange={(open) => {
        if (open) tag.reset();
      }}
    >
      Gắn vào luồng…
    </ConfirmButton>
  );
}

function AddMember({
  code,
  roles,
  candidates,
  initialUser = "",
  onDone,
}: {
  code: string;
  roles: Position[];
  candidates: Array<{ user_id: string; full_name: string; units: string[] }>;
  initialUser?: string;
  onDone: () => void;
}) {
  const [userId, setUserId] = useState(initialUser);
  const [position, setPosition] = useState(
    roles[0] ? positionKey(roles[0].role, roles[0].is_lead) : "",
  );
  const [memberCode, setMemberCode] = useState("");
  const viewer = useViewer();
  const admin = position === ADMIN_POSITION;
  const tag = useMutation({
    mutationFn: async (): Promise<unknown> =>
      admin
        ? api.setSystemRole(userId, "ADMIN")
        : api.tagUnitMember(code, {
          user_id: userId,
          ...fromPositionKey(position),
          member_code: memberCode || null,
        }),
    onSuccess: () => {
      setUserId("");
      setMemberCode("");
      onDone();
    },
  });
  return (
    <form
      className="flex flex-wrap items-end gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3"
      onSubmit={(event) => event.preventDefault()}
    >
      <label className="flex min-w-[14rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
        Thêm thành viên
        <Select
          value={userId}
          onChange={(event) => setUserId(event.target.value)}
        >
          <option value="">— chọn tài khoản —</option>
          {candidates.map((user) => (
            <option key={user.user_id} value={user.user_id}>
              {user.full_name}
              {user.units.length > 0
                ? ` · đang ở ${user.units.map((unit) => unitShortLabel(unit)).join(", ")}`
                : " · chưa có luồng"}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
        Vai trò
        <Select value={position} onChange={(event) => setPosition(event.target.value)}>
          {roles.map((option) => (
            <option
              key={positionKey(option.role, option.is_lead)}
              value={positionKey(option.role, option.is_lead)}
            >
              {option.label}
            </option>
          ))}
          {viewer.role === "OWNER" ? (
            <option value={ADMIN_POSITION}>{ADMIN_LABEL}</option>
          ) : null}
        </Select>
      </label>
      {code === "ADS" && !admin ? (
        <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Mã thành viên
          <input
            type="text"
            value={memberCode}
            onChange={(event) =>
              setMemberCode(event.target.value.toUpperCase())
            }
            placeholder="TUAN"
            className="min-h-11 w-28 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        </label>
      ) : null}
      <ConfirmButton
        spec={
          admin
            ? makeAdminConfirmation(
              candidates.find((user) => user.user_id === userId)?.full_name ?? "người này",
            )
            : {
              title: `Gắn thành viên vào ${unitName(code)}?`,
              description:
                "Người này sẽ thấy task của luồng và xuất hiện trong chuỗi sản xuất theo vai trò. Tag ở luồng khác (nếu có) vẫn giữ nguyên.",
              confirmLabel: "Gắn tag",
            }
        }
        onConfirm={() => tag.mutate()}
        pending={tag.isPending}
        error={tag.error}
        disabled={!userId || !position}
        tone="primary"
      >
        Gắn tag
      </ConfirmButton>
      {tag.isError ? <NoticeBox error={tag.error} /> : null}
    </form>
  );
}

const FUNCTION_ROLES = new Set(["BIEN_TAP", "THIET_KE", "DUNG"]);

/**
 * Who may be this member's own Leader ("Trưởng quản lý"), for a ban with
 * several: a staff member of Biên kịch / Design / Dựng picks a Leader of the
 * same ban, an orderer a Trưởng phòng ORD. `null` = the member has none to pick.
 */
function managerChoices(
  code: string,
  member: UnitMember,
  all: UnitMember[],
): UnitMember[] | null {
  if (code !== "ADS") return null;
  const fits =
    member.role === "ORDERER"
      ? (boss: UnitMember) => boss.role === "HEAD"
      : FUNCTION_ROLES.has(member.role) && !member.is_lead
        ? (boss: UnitMember) => boss.role === member.role && boss.is_lead
        : null;
  if (fits === null) return null;
  return all.filter(
    (boss) => boss.user_id !== member.user_id && boss.active && fits(boss),
  );
}

function MemberRow({
  code,
  member,
  units,
  roles,
  bosses,
  editable,
  mayUntag,
  mayChangeStatus,
  accountActive,
  onDone,
}: {
  code: string;
  member: UnitMember;
  units: string[];
  roles: Position[];
  /** Who may be picked as the member's own Leader; null = not for this member. */
  bosses: UnitMember[] | null;
  /** Role, member code and NAS may be changed (a stream in `can_tag`). */
  editable: boolean;
  mayUntag: boolean;
  /** OWNER / ADMIN: "Vô hiệu hoá" / "Kích hoạt lại". */
  mayChangeStatus: boolean;
  accountActive: boolean;
  onDone: () => void;
}) {
  const [memberCode, setMemberCode] = useState(member.member_code ?? "");
  const [nas, setNas] = useState(member.personal_nas_url ?? "");
  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.updateUnitMember>[2]) =>
      api.updateUnitMember(code, member.user_id, body),
    onSuccess: onDone,
  });
  const untag = useMutation({
    mutationFn: () => api.untagUnitMember(code, member.user_id),
    onSuccess: onDone,
  });
  const dirty =
    memberCode !== (member.member_code ?? "") ||
    nas !== (member.personal_nas_url ?? "");
  return (
    <tr
      data-inactive={accountActive ? undefined : "true"}
      className={`border-b border-[var(--border)] align-top ${accountActive ? "" : "opacity-60"}`}
    >
      <td className="px-3 py-2 font-medium">
        {member.full_name}
        {accountActive ? null : (
          <span className="mt-1 block">
            <DeactivatedPill />
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <UnitTags units={units} functionTag={member.function_tag} isLead={member.is_lead} />
      </td>
      <td className="px-3 py-2">{member.base_role_label}</td>
      <td className="px-3 py-2">
        {editable ? (
          <Select
            value={positionKey(member.role, member.is_lead)}
            onChange={(event) => update.mutate(fromPositionKey(event.target.value))}
            aria-label={`Vai trò của ${member.full_name}`}
          >
            {roles.map((option) => (
              <option
                key={positionKey(option.role, option.is_lead)}
                value={positionKey(option.role, option.is_lead)}
              >
                {option.label}
              </option>
            ))}
          </Select>
        ) : (
          <span>{member.role_label}</span>
        )}
        {member.is_lead ? (
          <span className="mt-1 block">
            <Pill tone="good">Nhận việc của luồng để phân công</Pill>
          </span>
        ) : null}
        {bosses !== null ? (
          <ManagerPicker
            member={member}
            bosses={bosses}
            editable={editable}
            pending={update.isPending}
            onPick={(managerId) => update.mutate({ manager_user_id: managerId })}
          />
        ) : null}
      </td>
      <td className="px-3 py-2">
        {code === "ADS" && editable ? (
          <input
            type="text"
            value={memberCode}
            onChange={(event) =>
              setMemberCode(event.target.value.toUpperCase())
            }
            aria-label={`Mã thành viên của ${member.full_name}`}
            className="min-h-11 w-28 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        ) : (
          (member.member_code ?? "–")
        )}
      </td>
      <td className="px-3 py-2">
        {editable ? (
          <input
            type="text"
            value={nas}
            onChange={(event) => setNas(event.target.value)}
            aria-label={`Kho cá nhân của ${member.full_name}`}
            className="min-h-11 w-40 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        ) : (
          <span className="break-all text-xs">{member.personal_nas_url ?? "–"}</span>
        )}
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap justify-end gap-1.5">
          {editable && dirty ? (
            <button
              type="button"
              onClick={() =>
                update.mutate({
                  member_code: memberCode || null,
                  personal_nas_url: nas || null,
                })
              }
              disabled={update.isPending}
              className="min-h-11 rounded-lg bg-[var(--accent)] px-3 text-xs font-medium text-[var(--accent-text)] disabled:opacity-50"
            >
              Lưu
            </button>
          ) : null}
          {mayUntag ? (
            <ConfirmButton
              spec={{
                title: `Gỡ ${member.full_name} khỏi ${unitName(code)}?`,
                description:
                  "Người này không còn thấy task của luồng. Lịch sử được giữ; gắn lại được sau.",
                confirmLabel: "Gỡ tag",
                variant: "destructive",
              }}
              onConfirm={() => untag.mutate()}
              pending={untag.isPending}
              error={untag.error}
              tone="danger"
            >
              Gỡ
            </ConfirmButton>
          ) : null}
          {mayChangeStatus ? (
            <AccountStatusButton
              userId={member.user_id}
              name={member.full_name}
              active={accountActive}
              onDone={onDone}
            />
          ) : null}
        </div>
        {update.isError ? <NoticeBox error={update.error} /> : null}
        {untag.isError ? <NoticeBox error={untag.error} /> : null}
      </td>
    </tr>
  );
}

/**
 * "Trưởng quản lý": the one Leader (head) this member's hand-ins (orders)
 * go to. Unset = every Leader of the ban, as before.
 */
function ManagerPicker({
  member,
  bosses,
  editable,
  pending,
  onPick,
}: {
  member: UnitMember;
  bosses: UnitMember[];
  editable: boolean;
  pending: boolean;
  onPick: (managerId: string | null) => void;
}) {
  const current = member.manager_user_id ?? "";
  const everyone =
    member.role === "ORDERER" ? "Mọi Trưởng phòng ORD" : "Mọi trưởng ban";
  const name = bosses.find((boss) => boss.user_id === current)?.full_name;
  if (!editable) {
    return (
      <span className="mt-1.5 block text-xs text-[var(--text-muted)]">
        Trưởng quản lý: {name ?? everyone}
      </span>
    );
  }
  return (
    <label className="mt-1.5 block text-xs text-[var(--text-muted)]">
      Trưởng quản lý
      <Select
        value={current}
        onChange={(event) => onPick(event.target.value || null)}
        disabled={pending}
        aria-label={`Trưởng quản lý của ${member.full_name}`}
        className="mt-0.5 w-full"
      >
        <option value="">{`Mặc định · ${everyone}`}</option>
        {bosses.map((boss) => (
          <option key={boss.user_id} value={boss.user_id}>
            {boss.full_name}
          </option>
        ))}
      </Select>
    </label>
  );
}

export function SettingsForm({ code, onDone }: { code: string; onDone: () => void }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const current = me.data?.units.find((unit) => unit.code === code)?.settings;
  const [draft, setDraft] = useState<Partial<UnitSettingsInfo>>({});
  const save = useMutation({
    mutationFn: () => api.updateUnitSettings(code, draft),
    onSuccess: () => {
      setDraft({});
      onDone();
    },
  });
  if (!current) return null;
  const value = <K extends keyof UnitSettingsInfo>(
    key: K,
  ): UnitSettingsInfo[K] => (draft[key] ?? current[key]) as UnitSettingsInfo[K];
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
  return (
    <form
      className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 sm:grid-cols-2"
      onSubmit={(event) => event.preventDefault()}
    >
      <h2 className="text-sm font-semibold sm:col-span-2">Thiết lập luồng</h2>
      <label className="text-xs text-[var(--text-muted)]">
        Mốc Gấp (ngày)
        <input
          type="number"
          min={1}
          max={365}
          value={value("urgent_days")}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              urgent_days: Number(event.target.value),
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      {/* No "người gắn link" setting: the last production node hands the
          product link in with its work (btd_link_attacher is obsolete). */}
      <label className="text-xs text-[var(--text-muted)]">
        Link Kho Media
        <input
          type="url"
          value={value("media_nas_url") ?? ""}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              media_nas_url: event.target.value || null,
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      <label className="text-xs text-[var(--text-muted)]">
        Link Kho Thiết kế
        <input
          type="url"
          value={value("design_nas_url") ?? ""}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              design_nas_url: event.target.value || null,
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      <label className="inline-flex min-h-11 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value("telegram_enabled")}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              telegram_enabled: event.target.checked,
            }))
          }
        />
        Gửi thêm thông báo qua Telegram
      </label>
      <fieldset className="sm:col-span-2">
        <legend className="text-xs text-[var(--text-muted)]">
          Trưởng phòng duyệt bài nộp trước khi chuyển bước (bỏ chọn: nộp xong tự
          chuyển bước tiếp theo)
        </legend>
        <div className="mt-1 flex flex-wrap gap-x-5">
          {(
            [
              ["review_bien_tap", "Biên kịch"],
              ["review_thiet_ke", "Design"],
              ["review_dung", "Dựng"],
              ["review_video_by_script_lead", "Trưởng phòng Biên kịch xem video (quy trình có Biên kịch)"],
            ] as const
          ).map(([key, label]) => (
            <label
              key={key}
              className="inline-flex min-h-11 items-center gap-2 text-sm"
            >
              <input
                type="checkbox"
                checked={Boolean(value(key))}
                onChange={(event) =>
                  setDraft((all) => ({ ...all, [key]: event.target.checked }))
                }
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex justify-end sm:col-span-2">
        <ConfirmButton
          spec={{
            title: "Lưu thiết lập luồng?",
            description: "Áp dụng cho mọi order của luồng từ giờ.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={Object.keys(draft).length === 0}
          tone="primary"
        >
          Lưu thiết lập
        </ConfirmButton>
      </div>
      {save.isError ? <NoticeBox error={save.error} /> : null}
    </form>
  );
}

/**
 * The ORD permission matrix: one row per permission, one column per role.
 * The OWNER is not a column - they hold everything. "Trong ban mình" means
 * the person's own function (a Leader's nodes), or the orderer's own orders.
 */
export function PermissionMatrix({
  code,
  onDone,
}: {
  code: string;
  onDone: () => void;
}) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const current = me.data?.units.find((unit) => unit.code === code)?.settings;
  const [draft, setDraft] = useState<Record<string, Record<string, string>> | null>(
    null,
  );
  const save = useMutation({
    mutationFn: () =>
      api.updateUnitSettings(code, { permissions: draft ?? undefined }),
    onSuccess: () => {
      setDraft(null);
      onDone();
    },
  });
  if (!current?.permissions || !current.permission_catalog || !current.permission_roles) {
    return null;
  }
  const matrix = draft ?? current.permissions;
  const labels = current.scope_labels ?? {};
  const setCell = (role: string, permission: string, scope: string) =>
    setDraft({
      ...matrix,
      [role]: { ...matrix[role], [permission]: scope },
    });
  const tone = (scope: string) =>
    scope === "ALL"
      ? "bg-[var(--good-soft)] text-[var(--good)]"
      : scope === "OWN"
        ? "bg-[var(--warn-soft)] text-[var(--warn)]"
        : "bg-[var(--surface-muted)] text-[var(--text-muted)]";
  return (
    <section
      aria-label="Phân quyền luồng ORD"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Phân quyền luồng ORD</h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Quyền theo vai trò. Chủ sở hữu luôn có mọi quyền. Một người có nhiều
            vai trò (ví dụ Admin kiêm Trưởng phòng Dựng) được cộng quyền của các vai trò đó.
          </p>
        </div>
        <ConfirmButton
          spec={{
            title: "Lưu bảng phân quyền?",
            description:
              "Áp dụng ngay cho mọi order của luồng: ai được duyệt, giao việc, huỷ, xem.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={draft === null}
          tone="primary"
        >
          Lưu phân quyền
        </ConfirmButton>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="table-dense">
          <thead>
            <tr>
              <th scope="col">Quyền</th>
              {current.permission_roles.map((role) => (
                <th key={role.key} scope="col">
                  {role.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {current.permission_catalog.map((permission) => (
              <tr key={permission.key}>
                <th scope="row" className="text-left font-medium">
                  {permission.label}
                  {permission.own_meaning ? (
                    <span className="block text-[11px] font-normal text-[var(--text-muted)]">
                      Trong ban mình: {permission.own_meaning}
                    </span>
                  ) : null}
                </th>
                {current.permission_roles!.map((role) => {
                  const scope = matrix[role.key]?.[permission.key] ?? "NONE";
                  return (
                    <td key={role.key}>
                      <select
                        aria-label={`${permission.label} · ${role.label}`}
                        value={scope}
                        onChange={(event) =>
                          setCell(role.key, permission.key, event.target.value)
                        }
                        className={`min-h-9 rounded-md border border-[var(--border)] px-2 text-xs font-medium ${tone(scope)}`}
                      >
                        {permission.scopes.map((option) => (
                          <option key={option} value={option}>
                            {labels[option] ?? option}
                          </option>
                        ))}
                      </select>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {save.isError ? <NoticeBox error={save.error} /> : null}
    </section>
  );
}
