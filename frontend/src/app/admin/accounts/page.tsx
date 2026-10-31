"use client";

import { PersonLink } from "@/components/person-link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type Department,
  type DirectoryUser,
  type MemberRow,
  type MemberSeat,
  type UnitMember,
  type UnitsMe,
  type Uuid,
} from "@/lib/api";
import { AdminTabs, headsDepartments } from "@/components/admin-tabs";
import {
  EmploymentStatusField,
  EmploymentStatusPill,
  MemberCodeField,
  PasswordStatus,
} from "@/components/account-fields";
import { Avatar } from "@/components/avatar";
import { ConfirmDialog } from "@/components/confirm";
import { DepartmentTag, DepartmentTags, SeedingTag } from "@/components/department-tags";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import {
  PageHeader,
  PrimaryButton,
  SecondaryButton,
  Select,
} from "@/components/pr";
import { RowMenu, type RowMenuItem } from "@/components/row-menu";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import {
  DeactivatedPill,
  mayChangeAccountStatus,
  mayDeleteAccount,
} from "@/components/unit-panel";
import {
  deactivateAccountConfirmation,
  deleteAccountConfirmation,
  reactivateAccountConfirmation,
  restoreAccountConfirmation,
  resetMemberPasswordConfirmation,
} from "@/lib/confirmations";
import { formatTokens, parseTokens } from "@/lib/deadline";
import { useEffortEnabled } from "@/lib/effort";
import { formatAgo, roleLabel } from "@/lib/labels";
import { canEditMembersIn } from "@/lib/units";

/**
 * "Quản trị › Quản lý tài khoản" (2026-10-17): every account on one flat
 * table, filtered by department - no stream rosters any more. Streams follow
 * the seats on their own, so nobody is "tagged into a stream" here: a person
 * is put into a department and gets that department's permissions.
 *
 * Who sees what: the OWNER and an ADMIN see every account; a department head
 * sees the people of the departments they head (the server lists them).
 * Writes, each checked again on the server:
 * - seats: the OWNER anywhere, a head *members* of their own department
 *   (`PUT/DELETE /api/departments/{id}/members`), from the chip's × and the
 *   "Xếp vào phòng" dialog;
 * - the row's "⋯" menu: Quyền, system role (OWNER, a dialog), password reset
 *   / deactivate / reactivate (OWNER / ADMIN), delete (OWNER) - each behind
 *   its confirmation;
 * - inline: member code (OWNER / ADMIN), employment status (OWNER / ADMIN /
 *   HR_STATUS), and for the people with an ORD seat the NAS link and
 *   token/ngày, for whoever may edit ORD member rows.
 */
export default function AccountsAdminPage() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: api.session,
    retry: false,
  });
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const role = session.data?.role;
  const manages = role === "OWNER" || role === "ADMIN";
  const [includeInactive, setIncludeInactive] = useState(false);
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const month = monthOf(new Date());
  const members = useQuery({
    queryKey: ["account", "members", month, "ALL", includeInactive, false],
    queryFn: () => api.accountMembers(month, "ALL", includeInactive, false),
    retry: false,
  });
  // The department list: the filter and the "Xếp vào phòng" picker. Readable
  // by OWNER / ADMIN and department heads; nobody else gets this screen.
  const departments = useQuery({
    queryKey: ["departments"],
    queryFn: api.departments,
    enabled: manages || headsDepartments(me.data),
    retry: false,
  });
  // The ORD rows carry what the account roster does not: NAS link, token/ngày.
  const canEditOrd = canEditMembersIn(me.data, "ADS");
  const ord = useQuery({
    queryKey: ["units", "ADS", "members"],
    queryFn: () => api.unitMembers("ADS"),
    enabled: canEditOrd,
    retry: false,
  });
  const ordOf = useMemo(
    () =>
      new Map(
        (ord.data?.members ?? []).map((member) => [member.user_id, member]),
      ),
    [ord.data],
  );
  const effortOn = useEffortEnabled();
  const [addingAnyone, setAddingAnyone] = useState(false);
  // Every account, for "Thêm người vào phòng": a head's roster lists only
  // their own people, so the picker reads the directory instead.
  const placeable = placeableDepartments(
    role,
    me.data,
    departments.data?.departments,
  );
  const directory = useQuery({
    queryKey: ["units", "directory"],
    queryFn: api.unitDirectory,
    enabled: placeable.length > 0,
    retry: false,
  });

  const options = useMemo(() => {
    if (departments.data) {
      return departments.data.departments
        .filter((item) => item.active)
        .map((item) => ({ id: item.id, name: item.name }));
    }
    const seen = new Map<string, string>();
    for (const row of members.data?.members ?? []) {
      for (const seat of row.departments ?? [])
        seen.set(seat.department_id, seat.name);
    }
    return Array.from(seen, ([id, name]) => ({ id, name }));
  }, [departments.data, members.data]);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (members.data?.members ?? []).filter((row) => {
      const seats = row.departments ?? [];
      if (department === NO_DEPARTMENT && seats.length > 0) return false;
      if (
        department &&
        department !== NO_DEPARTMENT &&
        !seats.some((seat) => seat.department_id === department)
      ) {
        return false;
      }
      if (!needle) return true;
      return (
        row.full_name.toLowerCase().includes(needle) ||
        String(row.telegram_user_id ?? "").includes(needle) ||
        (row.member_code ?? "").toLowerCase().includes(needle)
      );
    });
  }, [members.data, search, department]);

  if (members.isPending || session.isPending)
    return <Loading label="Đang tải tài khoản…" />;
  if (members.isError) {
    return (
      <div className="space-y-4">
        <AdminTabs active="accounts" />
        <PageHeader title="Quản lý tài khoản" />
        <ErrorBox error={members.error} onRetry={() => members.refetch()} />
      </div>
    );
  }

  const viewer = { role, userId: session.data?.user_id ?? null };
  const showOrd = canEditOrd;

  return (
    <div className="space-y-4">
      <AdminTabs active="accounts" />
      <PageHeader
        title="Quản lý tài khoản"
        subtitle="Mọi tài khoản trên một bảng, lọc theo phòng ban. Xếp người vào phòng ở đây; quyền đi theo phòng và chức vụ (★ = trưởng phòng)."
      />

      <section
        aria-label="Bộ lọc tài khoản"
        className="panel flex flex-wrap items-end gap-3 p-3 sm:p-4"
      >
        <label className="flex min-w-[14rem] flex-1 flex-col gap-1 text-xs text-[var(--text-muted)]">
          Tìm theo tên, Telegram ID hoặc mã nhân viên
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Ví dụ: Quỳnh"
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        </label>
        <label className="flex min-w-[12rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Phòng ban
          <Select
            value={department}
            onChange={(event) => setDepartment(event.target.value)}
            aria-label="Lọc theo phòng ban"
          >
            <option value="">Tất cả phòng ban</option>
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
            <option value={NO_DEPARTMENT}>Chưa có phòng ban</option>
          </Select>
        </label>
        {manages ? (
          <label className="inline-flex min-h-11 items-center gap-2 text-sm text-[var(--text-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(event) => setIncludeInactive(event.target.checked)}
            />
            Hiện cả tài khoản đã vô hiệu hoá
          </label>
        ) : null}
        <p className="ml-auto self-center text-sm text-[var(--text-muted)]">
          {rows.length} tài khoản
        </p>
        {placeable.length > 0 ? (
          <PrimaryButton type="button" onClick={() => setAddingAnyone(true)}>
            Thêm người vào phòng
          </PrimaryButton>
        ) : null}
      </section>
      <AddToDepartmentDialog
        open={addingAnyone}
        viewer={viewer}
        placeable={placeable}
        people={directory.data ?? []}
        onClose={() => setAddingAnyone(false)}
        onDone={(text) => {
          setAddingAnyone(false);
          setNotice(text);
          void members.refetch();
          void directory.refetch();
        }}
      />

      {notice ? (
        <div
          role="status"
          className="flex items-center gap-3 rounded-xl border border-[var(--good)]/25 bg-[var(--good-soft)] px-4 py-3 text-sm font-medium text-[var(--good)]"
        >
          <p className="min-w-0 flex-1">{notice}</p>
          <button
            type="button"
            onClick={() => setNotice(null)}
            aria-label="Ẩn thông báo"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-black/5 dark:hover:bg-white/10"
          >
            ×
          </button>
        </div>
      ) : null}

      <div className="panel">
        <div className="table-scroll-x">
          <table className="table-dense min-w-[68rem] [&_td]:align-middle">
            <thead>
              <tr>
                <th className="w-[18rem]">Tài khoản</th>
                <th className="w-[16rem]">Phòng ban</th>
                <th className="w-[9rem]">Mã nhân viên</th>
                <th className="w-[9rem]">Nhân sự</th>
                {showOrd ? (
                  <th className="w-[16rem]">
                    Kho cá nhân{effortOn ? " · Token/ngày" : ""}
                  </th>
                ) : null}
                <th className="w-[10rem]">Đăng nhập</th>
                <th className="w-12">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={showOrd ? 7 : 6}
                    className="text-[var(--text-muted)]"
                  >
                    Không có tài khoản nào trong bộ lọc này.
                  </td>
                </tr>
              ) : null}
              {rows.map((row) => (
                <AccountRow
                  key={row.user_id}
                  row={row}
                  viewer={viewer}
                  manages={manages}
                  mayHr={manages || Boolean(me.data?.resources?.hr)}
                  placeable={placeable}
                  ordRow={showOrd ? ordOf.get(row.user_id) : undefined}
                  showOrd={showOrd}
                  showTokens={showOrd && effortOn}
                  defaultTokens={
                    me.data?.units.find((unit) => unit.code === "ADS")?.settings
                      .default_daily_tokens ?? DEFAULT_DAILY_TOKENS
                  }
                  onNotice={setNotice}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Tài khoản mới vào bằng mã mời (Tài khoản › Mời thành viên) hoặc do
        trưởng phòng PR thêm ở Phòng PR › Thành viên &amp; Phân quyền. Phòng
        ban, chức vụ và quyền của từng phòng: tab Phòng ban.
      </p>
    </div>
  );
}

/** The "Phòng ban" filter's value for people with no seat. */
const NO_DEPARTMENT = "__none__";
/** The server's own default for `default_daily_tokens`, until settings load. */
const DEFAULT_DAILY_TOKENS = 80;
/** The system roles the OWNER may give (an OWNER is made elsewhere). */
const ROLE_CHOICES = ["EMPLOYEE", "TEAM_LEAD", "ADMIN"];

function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/** A department this viewer may seat people in, and as what. */
type Placeable = {
  id: Uuid;
  name: string;
  positions: Array<"HEAD" | "MEMBER">;
};

/**
 * Where the viewer may put people: the OWNER in every active department, as
 * head or member; a department head in the departments they head, as member.
 * An ADMIN reads only (the server says so too).
 */
function placeableDepartments(
  role: string | undefined,
  me: UnitsMe | undefined,
  all: Department[] | undefined,
): Placeable[] {
  if (role === "OWNER") {
    return (all ?? [])
      .filter((item) => item.active)
      .map((item) => ({
        id: item.id,
        name: item.name,
        positions: ["HEAD", "MEMBER"],
      }));
  }
  return (me?.departments ?? [])
    .filter((seat) => seat.position === "HEAD")
    .map((seat) => ({
      id: seat.department_id,
      name: seat.name,
      positions: ["MEMBER"],
    }));
}

type Viewer = { role?: string; userId: string | null };

/** What the "⋯" menu may open for this row, besides the links. */
type RowAction = "reset" | "status" | "restore" | "delete" | "role" | "impersonate" | null;

function AccountRow({
  row,
  viewer,
  manages,
  mayHr,
  placeable,
  ordRow,
  showOrd,
  showTokens,
  defaultTokens,
  onNotice,
}: {
  row: MemberRow;
  viewer: Viewer;
  /** OWNER / ADMIN: the account controls. */
  manages: boolean;
  /** May set the employment status. */
  mayHr: boolean;
  placeable: Placeable[];
  /** The person's ORD member row, when the viewer may edit ORD rows and they have one. */
  ordRow: UnitMember | undefined;
  showOrd: boolean;
  showTokens: boolean;
  defaultTokens: number;
  onNotice: (text: string) => void;
}) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
    void queryClient.invalidateQueries({ queryKey: ["units"] });
    void queryClient.invalidateQueries({ queryKey: ["departments"] });
  };
  const active = row.active ?? true;
  const revoked = row.account_status === "revoked";
  const isSelf = viewer.userId === row.user_id;
  const [action, setAction] = useState<RowAction>(null);
  const done = (text: string) => {
    setAction(null);
    onNotice(text);
    refresh();
  };
  const reset = useMutation({
    mutationFn: () => api.resetMemberPassword(row.user_id),
    onSuccess: () =>
      done(`Đã gửi mật khẩu tạm qua Telegram cho ${row.full_name}.`),
  });
  const status = useMutation({
    mutationFn: () =>
      active
        ? api.deactivateAccount(row.user_id)
        : api.reactivateAccount(row.user_id),
    onSuccess: () =>
      done(
        active
          ? `Đã vô hiệu hoá tài khoản ${row.full_name}.`
          : `Đã kích hoạt lại tài khoản ${row.full_name}.`,
      ),
  });
  const restore = useMutation({
    mutationFn: () => api.restoreAccount(row.user_id),
    onSuccess: () => done(`Đã khôi phục tài khoản ${row.full_name}.`),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteMember(row.user_id),
    onSuccess: () => done(`Đã xoá tài khoản ${row.full_name}.`),
  });

  const items: RowMenuItem[] = [];
  // A person's "Quyền" page is the OWNER's alone.
  if (viewer.role === "OWNER")
    items.push({
      key: "grants",
      label: "Quyền",
      href: `/admin/people/${row.user_id}`,
    });
  if (viewer.role === "OWNER" && !isSelf && row.role !== "OWNER") {
    items.push({
      key: "role",
      label: "Đổi vai trò hệ thống…",
      onSelect: () => setAction("role"),
    });
  }
  if (manages && active && !isSelf) {
    items.push({
      key: "reset",
      label: "Đặt lại mật khẩu…",
      onSelect: () => setAction("reset"),
    });
  }
  // "Đăng nhập thay": OWNER only, into an active non-OWNER account.
  if (viewer.role === "OWNER" && active && !isSelf && row.role !== "OWNER") {
    items.push({
      key: "impersonate",
      label: "Đăng nhập thay…",
      onSelect: () => setAction("impersonate"),
    });
  }
  // A revoked account ("thu hồi") is the OWNER's to restore, never reactivated.
  if (revoked) {
    if (viewer.role === "OWNER" && !isSelf && row.role !== "OWNER") {
      items.push({
        key: "restore",
        label: "Khôi phục tài khoản…",
        onSelect: () => setAction("restore"),
      });
    }
  } else if (
    mayChangeAccountStatus(viewer, { user_id: row.user_id, role: row.role })
  ) {
    items.push({
      key: "status",
      label: active ? "Vô hiệu hoá…" : "Kích hoạt lại…",
      onSelect: () => setAction("status"),
      tone: active ? "danger" : undefined,
    });
  }
  if (mayDeleteAccount(viewer, { user_id: row.user_id, role: row.role })) {
    items.push({
      key: "delete",
      label: "Xoá tài khoản…",
      onSelect: () => setAction("delete"),
      tone: "danger",
    });
  }
  const dialog =
    action === "reset"
      ? { spec: resetMemberPasswordConfirmation(row.full_name), run: reset }
      : action === "status"
        ? {
          spec: active
            ? deactivateAccountConfirmation(row.full_name)
            : reactivateAccountConfirmation(row.full_name),
          run: status,
        }
        : action === "restore"
          ? { spec: restoreAccountConfirmation(row.full_name), run: restore }
          : action === "delete"
            ? { spec: deleteAccountConfirmation(row.full_name), run: remove }
            : null;

  return (
    <tr
      data-inactive={active ? undefined : "true"}
      className={active ? undefined : "opacity-60"}
    >
      <td>
        <div className="flex items-center gap-3">
          <Avatar name={row.full_name} src={row.avatar_url} size={36} />
          <div className="min-w-0">
            <p className="truncate font-medium leading-tight">
              <PersonLink userId={row.user_id} name={row.full_name} />
            </p>
            <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">
              {row.role_label} · {String(row.telegram_user_id)}
            </p>
            {active ? null : (
              <span className="mt-1 block">
                {revoked ? <Pill tone="bad">Đã thu hồi</Pill> : <DeactivatedPill />}
              </span>
            )}
          </div>
        </div>
      </td>
      <td>
        <SeatsCell
          row={row}
          placeable={placeable}
          viewer={viewer}
          onDone={refresh}
        />
      </td>
      <td>
        {manages ? (
          <MemberCodeField
            key={`${row.user_id}:${row.member_code ?? ""}`}
            row={row}
            onSaved={(code) => {
              onNotice(
                code
                  ? `Đã đặt mã nhân viên ${code} cho ${row.full_name}.`
                  : `Đã xoá mã nhân viên của ${row.full_name}.`,
              );
              refresh();
            }}
          />
        ) : (
          <span className="font-mono text-xs">{row.member_code || "–"}</span>
        )}
      </td>
      <td>
        {mayHr ? (
          <EmploymentStatusField
            key={`${row.user_id}:${row.employment_status ?? ""}`}
            row={row}
            onSaved={(label) => {
              onNotice(
                `Đã đặt trạng thái nhân sự của ${row.full_name}: ${label}.`,
              );
              refresh();
            }}
          />
        ) : (
          <EmploymentStatusPill row={row} />
        )}
      </td>
      {showOrd ? (
        <td>
          {ordRow ? (
            <OrdCell
              key={`${ordRow.user_id}:${ordRow.personal_nas_url ?? ""}:${ordRow.daily_tokens ?? ""}`}
              member={ordRow}
              showTokens={showTokens}
              defaultTokens={defaultTokens}
              onDone={refresh}
            />
          ) : (
            <span className="text-xs text-[var(--text-muted)]">–</span>
          )}
        </td>
      ) : null}
      <td>
        <div className="flex flex-col items-start gap-1">
          <PasswordStatus row={row} />
          <span className="whitespace-nowrap text-xs text-[var(--text-muted)]">
            {row.last_login_at
              ? formatAgo(row.last_login_at)
              : "Chưa đăng nhập"}
          </span>
        </div>
      </td>
      <td className="text-right">
        <RowMenu label={`Thao tác với ${row.full_name}`} items={items} />
        {dialog ? (
          <ConfirmDialog
            open
            spec={dialog.spec}
            pending={dialog.run.isPending}
            error={dialog.run.error}
            onConfirm={() => dialog.run.mutate()}
            onCancel={() => {
              dialog.run.reset();
              setAction(null);
            }}
          />
        ) : null}
        {action === "impersonate" ? (
          <LoginLinkDialog row={row} onClose={() => setAction(null)} />
        ) : null}
        <RoleDialog
          open={action === "role"}
          row={row}
          onClose={() => setAction(null)}
          onDone={(label) =>
            done(`Đã đổi vai trò hệ thống của ${row.full_name}: ${label}.`)
          }
        />
      </td>
    </tr>
  );
}

/**
 * The person's seats as chips, each with × for whoever may take them out of
 * that department, and "+ Phòng" opening the "Xếp vào phòng" dialog.
 */
function SeatsCell({
  row,
  placeable,
  viewer,
  onDone,
}: {
  row: MemberRow;
  placeable: Placeable[];
  viewer: Viewer;
  onDone: () => void;
}) {
  const seats = row.departments ?? [];
  const [adding, setAdding] = useState(false);
  const [leaving, setLeaving] = useState<MemberSeat | null>(null);
  const seated = new Set(seats.map((seat) => seat.department_id));
  const choices = placeable.filter((item) => !seated.has(item.id));
  const remove = useMutation({
    mutationFn: (seat: MemberSeat) =>
      api.unseatDepartmentMember(seat.department_id, row.user_id),
    onSuccess: () => {
      setLeaving(null);
      onDone();
    },
  });
  /** A head takes out their own members (the ones reporting to them); the OWNER anyone. */
  const mayRemove = (seat: MemberSeat) => {
    if (viewer.userId === row.user_id) return false;
    if (!placeable.some((item) => item.id === seat.department_id)) return false;
    if (viewer.role === "OWNER") return true;
    return (
      seat.position !== "HEAD" &&
      (!seat.manager_user_id || seat.manager_user_id === viewer.userId)
    );
  };
  const protectedAccount = row.role === "OWNER" || row.role === "ADMIN";
  // One person in several departments is the OWNER's call: a head adds only
  // somebody seated nowhere yet.
  const mayAdd =
    choices.length > 0 &&
    (viewer.role === "OWNER" || (!protectedAccount && seats.length === 0));
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {seats.length === 0 ? <DepartmentTags seats={[]} /> : null}
      {row.seeding ? <SeedingTag /> : null}
      {seats.map((seat) =>
        mayRemove(seat) ? (
          <span key={seat.department_id} className="dept-chip">
            <DepartmentTag seat={seat} />
            <button
              type="button"
              className="dept-remove"
              aria-label={`Đưa ${row.full_name} ra khỏi ${seat.name}`}
              title={`Đưa ra khỏi ${seat.name}`}
              onClick={() => {
                remove.reset();
                setLeaving(seat);
              }}
            >
              <svg
                viewBox="0 0 24 24"
                width="12"
                height="12"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </span>
        ) : (
          <DepartmentTag key={seat.department_id} seat={seat} />
        ),
      )}
      {mayAdd ? (
        <button
          type="button"
          onClick={() => setAdding(true)}
          aria-label={`Xếp ${row.full_name} vào phòng`}
          className="inline-flex min-h-7 items-center gap-1 rounded-lg border border-dashed border-[var(--border)] px-2 text-xs font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
        >
          <span aria-hidden="true">+</span> Phòng
        </button>
      ) : null}
      {leaving ? (
        <ConfirmDialog
          open
          spec={{
            title: `Đưa ${row.full_name} ra khỏi ${leaving.name}?`,
            description:
              "Họ mất quyền của phòng này (các phòng khác giữ nguyên). Bị từ chối nếu họ còn đang giữ việc trong luồng order.",
            confirmLabel: "Đưa ra khỏi phòng",
            variant: "destructive",
          }}
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate(leaving)}
          onCancel={() => setLeaving(null)}
        />
      ) : null}
      <SeatDialog
        open={adding}
        row={row}
        choices={choices}
        onClose={() => setAdding(false)}
        onDone={() => {
          setAdding(false);
          onDone();
        }}
      />
    </div>
  );
}

/**
 * "Thêm người vào phòng", from the toolbar: any account from the directory
 * (not already in the chosen department; a head may not pick an OWNER /
 * ADMIN account) into one of the departments the viewer may seat in.
 */
function AddToDepartmentDialog({
  open,
  viewer,
  placeable,
  people,
  onClose,
  onDone,
}: {
  open: boolean;
  viewer: Viewer;
  placeable: Placeable[];
  people: DirectoryUser[];
  onClose: () => void;
  onDone: (notice: string) => void;
}) {
  const [target, setTarget] = useState("");
  const [person, setPerson] = useState("");
  const [position, setPosition] = useState<"HEAD" | "MEMBER">("MEMBER");
  const chosen = placeable.find((item) => item.id === target) ?? placeable[0];
  const candidates = people.filter(
    (user) =>
      user.active &&
      user.user_id !== viewer.userId &&
      (viewer.role === "OWNER"
        ? !(user.departments ?? []).some(
          (seat) => seat.department_id === chosen?.id,
        )
        : user.base_role !== "OWNER" &&
        user.base_role !== "ADMIN" &&
        (user.departments ?? []).length === 0),
  );
  const picked = candidates.find((user) => user.user_id === person);
  const picksPosition = Boolean(chosen && chosen.positions.length > 1);
  const add = useMutation({
    mutationFn: () =>
      api.seatDepartmentMember(chosen!.id, {
        user_id: picked!.user_id,
        position: picksPosition ? position : "MEMBER",
      }),
    onSuccess: () => {
      setPerson("");
      onDone(`Đã xếp ${picked!.full_name} vào ${chosen!.name}.`);
    },
  });
  if (!open || !chosen) return null;
  return (
    <Modal
      open
      onClose={onClose}
      busy={add.isPending}
      title="Thêm người vào phòng"
      description={
        viewer.role === "OWNER"
          ? "Chọn tài khoản đã có và phòng cần xếp. Người vào phòng nhận quyền mặc định của phòng theo chức vụ; chỗ ở phòng khác (nếu có) giữ nguyên."
          : "Chỉ tài khoản chưa thuộc phòng nào được chọn; người vào phòng nhận quyền Nhân viên của phòng và bạn là Trưởng quản lý của họ."
      }
    >
      <form
        aria-label="Thêm người vào phòng"
        onSubmit={(event) => {
          event.preventDefault();
          if (picked) add.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className="block text-sm">
            Phòng ban
            <Select
              value={chosen.id}
              onChange={(event) => {
                setTarget(event.target.value);
                setPerson("");
              }}
              aria-label="Phòng ban"
              className="mt-1 w-full"
              data-autofocus
            >
              {placeable.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-sm">
            Tài khoản
            <Select
              value={person}
              onChange={(event) => setPerson(event.target.value)}
              aria-label="Tài khoản"
              className="mt-1 w-full"
            >
              <option value="">— chọn tài khoản —</option>
              {candidates.map((user) => (
                <option key={user.user_id} value={user.user_id}>
                  {user.full_name}
                  {user.departments && user.departments.length > 0
                    ? ` (${user.departments.map((seat) => seat.name).join(", ")})`
                    : " (chưa có phòng ban)"}
                </option>
              ))}
            </Select>
          </label>
          {candidates.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">
              {viewer.role === "OWNER"
                ? `Mọi tài khoản đang hoạt động đều đã ở ${chosen.name}.`
                : "Không còn tài khoản nào chưa thuộc phòng ban (xếp một người vào nhiều phòng là việc của chủ sở hữu)."}{" "}
              Người chưa có tài khoản vào bằng mã mời (Tài khoản › Mời thành
              viên).
            </p>
          ) : null}
          {picksPosition ? (
            <label className="block text-sm">
              Chức vụ
              <Select
                value={position}
                onChange={(event) =>
                  setPosition(event.target.value as "HEAD" | "MEMBER")
                }
                aria-label="Chức vụ"
                className="mt-1 w-full"
              >
                <option value="MEMBER">Nhân viên</option>
                <option value="HEAD">Trưởng phòng</option>
              </Select>
            </label>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">
              Chức vụ: Nhân viên.
            </p>
          )}
          {add.isError ? (
            <NoticeBox error={add.error} onDismiss={() => add.reset()} />
          ) : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton
            type="button"
            onClick={onClose}
            disabled={add.isPending}
          >
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={add.isPending || !picked}>
            {add.isPending ? "Đang xếp…" : "Xếp vào phòng"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

/** "Xếp vào phòng": the department (among those the viewer may seat in) and, for the OWNER, the position. */
function SeatDialog({
  open,
  row,
  choices,
  onClose,
  onDone,
}: {
  open: boolean;
  row: MemberRow;
  choices: Placeable[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [target, setTarget] = useState("");
  const [position, setPosition] = useState<"HEAD" | "MEMBER">("MEMBER");
  const chosen = choices.find((item) => item.id === target) ?? choices[0];
  const picksPosition = Boolean(chosen && chosen.positions.length > 1);
  const finalPosition = picksPosition ? position : "MEMBER";
  const add = useMutation({
    mutationFn: () =>
      api.seatDepartmentMember(chosen!.id, {
        user_id: row.user_id,
        position: finalPosition,
      }),
    onSuccess: onDone,
  });
  if (!open || !chosen) return null;
  return (
    <Modal
      open
      onClose={onClose}
      busy={add.isPending}
      title={`Xếp ${row.full_name} vào phòng`}
      description="Người vào phòng nhận quyền mặc định của phòng theo chức vụ. Chỗ ở phòng khác (nếu có) giữ nguyên."
    >
      <form
        aria-label={`Xếp ${row.full_name} vào phòng`}
        onSubmit={(event) => {
          event.preventDefault();
          add.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className="block text-sm">
            Phòng ban
            <Select
              value={chosen.id}
              onChange={(event) => setTarget(event.target.value)}
              aria-label="Phòng ban"
              className="mt-1 w-full"
              data-autofocus
            >
              {choices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </label>
          {picksPosition ? (
            <label className="block text-sm">
              Chức vụ
              <Select
                value={position}
                onChange={(event) =>
                  setPosition(event.target.value as "HEAD" | "MEMBER")
                }
                aria-label="Chức vụ"
                className="mt-1 w-full"
              >
                <option value="MEMBER">Nhân viên</option>
                <option value="HEAD">Trưởng phòng</option>
              </Select>
            </label>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">
              Chức vụ: Nhân viên.
            </p>
          )}
          {add.isError ? (
            <NoticeBox error={add.error} onDismiss={() => add.reset()} />
          ) : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton
            type="button"
            onClick={onClose}
            disabled={add.isPending}
          >
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={add.isPending}>
            {add.isPending ? "Đang xếp…" : "Xếp vào phòng"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

/** "Đổi vai trò hệ thống": the OWNER's, in a dialog whose submit is the confirmation. */
function RoleDialog({
  open,
  row,
  onClose,
  onDone,
}: {
  open: boolean;
  row: MemberRow;
  onClose: () => void;
  onDone: (label: string) => void;
}) {
  const current = row.role ?? "EMPLOYEE";
  const [picked, setPicked] = useState(current);
  const change = useMutation({
    mutationFn: () => api.setAccountRole(row.user_id, picked),
    onSuccess: () => onDone(roleLabel(picked)),
  });
  if (!open) return null;
  return (
    <Modal
      open
      onClose={onClose}
      busy={change.isPending}
      title={`Đổi vai trò hệ thống của ${row.full_name}`}
      description="Vai trò mới có hiệu lực ngay ở lần thao tác tiếp theo của họ. Quyền theo phòng ban và quyền cấp thêm giữ nguyên."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          change.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className="block text-sm">
            Vai trò hệ thống
            <Select
              value={picked}
              onChange={(event) => setPicked(event.target.value)}
              aria-label={`Vai trò hệ thống của ${row.full_name}`}
              className="mt-1 w-full"
              data-autofocus
            >
              {ROLE_CHOICES.map((value) => (
                <option key={value} value={value}>
                  {roleLabel(value)}
                </option>
              ))}
            </Select>
          </label>
          {change.isError ? (
            <NoticeBox error={change.error} onDismiss={() => change.reset()} />
          ) : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton
            type="button"
            onClick={onClose}
            disabled={change.isPending}
          >
            Thôi
          </SecondaryButton>
          <PrimaryButton
            type="submit"
            disabled={change.isPending || picked === current}
          >
            {change.isPending ? "Đang đổi…" : "Đổi vai trò"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

/**
 * "Đăng nhập thay" (2026-10-21): the OWNER mints a single-use link to sign in
 * as this member, to support or fix something on their behalf. The link is
 * fetched when the dialog opens; "Mở tài khoản này" opens it in a new tab,
 * "Sao chép liên kết" copies it. Open it in a private window so it does not
 * replace the OWNER's own session.
 */
function LoginLinkDialog({ row, onClose }: { row: MemberRow; onClose: () => void }) {
  const link = useMutation({ mutationFn: () => api.issueLoginLink(row.user_id) });
  const run = link.mutate;
  useEffect(() => {
    run();
  }, [run]);
  const url = link.data?.url ?? null;
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (!url) return;
    void navigator.clipboard?.writeText(url).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };
  return (
    <Modal
      open
      onClose={onClose}
      busy={link.isPending}
      title={`Đăng nhập thay ${row.full_name}`}
      description="Liên kết đăng nhập một lần vào tài khoản này, để bạn hỗ trợ hoặc sửa giúp họ. Mỗi lần tạo đều được ghi lại."
    >
      <ModalBody className="space-y-3">
        {link.isPending ? (
          <p className="text-sm text-[var(--text-muted)]">Đang tạo liên kết…</p>
        ) : link.isError ? (
          <NoticeBox error={link.error} />
        ) : url ? (
          <>
            <p className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-xs text-[var(--text)]">
              Nên mở ở <b>cửa sổ ẩn danh</b> để không ghi đè phiên đăng nhập của bạn. Liên kết chỉ
              dùng được <b>một lần</b> và hết hạn sau vài phút.
            </p>
            <label className="block text-xs text-[var(--text-muted)]">
              Liên kết
              <input
                readOnly
                value={url}
                onFocus={(event) => event.currentTarget.select()}
                aria-label={`Liên kết đăng nhập thay ${row.full_name}`}
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-mono text-xs"
              />
            </label>
          </>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Đóng
        </SecondaryButton>
        {url ? (
          <>
            <SecondaryButton type="button" onClick={copy}>
              {copied ? "Đã sao chép" : "Sao chép liên kết"}
            </SecondaryButton>
            <PrimaryButton
              type="button"
              onClick={() => window.open(url, "_blank", "noopener")}
            >
              Mở tài khoản này
            </PrimaryButton>
          </>
        ) : null}
      </ModalFooter>
    </Modal>
  );
}

/** The ORD per-person settings: NAS link and, with effort on, token/ngày. */
function OrdCell({
  member,
  showTokens,
  defaultTokens,
  onDone,
}: {
  member: UnitMember;
  showTokens: boolean;
  defaultTokens: number;
  onDone: () => void;
}) {
  const [nas, setNas] = useState(member.personal_nas_url ?? "");
  const savedTokens =
    member.daily_tokens === null || member.daily_tokens === undefined
      ? ""
      : String(member.daily_tokens);
  const [tokens, setTokens] = useState(savedTokens);
  const tokensChanged = showTokens && tokens.trim() !== savedTokens;
  const tokensInvalid = tokens.trim() !== "" && parseTokens(tokens) === null;
  const dirty = nas !== (member.personal_nas_url ?? "") || tokensChanged;
  const update = useMutation({
    mutationFn: () =>
      api.updateUnitMember("ADS", member.user_id, {
        personal_nas_url: nas || null,
        ...(tokensChanged ? { daily_tokens: parseTokens(tokens) } : {}),
      }),
    onSuccess: onDone,
  });
  const field =
    "min-h-9 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-xs text-[var(--text)]";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div className="flex flex-nowrap items-center gap-1.5">
        <input
          type="text"
          value={nas}
          onChange={(event) => setNas(event.target.value)}
          aria-label={`Kho cá nhân của ${member.full_name}`}
          placeholder="Link NAS"
          className={`${field} w-28`}
        />
        {showTokens ? (
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={999.99}
            step={0.5}
            value={tokens}
            onChange={(event) => setTokens(event.target.value)}
            placeholder={formatTokens(defaultTokens)}
            aria-label={`Token/ngày của ${member.full_name}`}
            aria-invalid={tokensInvalid || undefined}
            title="Để trống = mặc định của luồng order"
            className={`${field} w-14`}
          />
        ) : null}
        <button
          type="button"
          onClick={() => update.mutate()}
          disabled={!dirty || update.isPending || tokensInvalid}
          className="min-h-9 shrink-0 rounded-md border border-[var(--border)] px-2 text-xs font-medium disabled:opacity-40"
        >
          Lưu
        </button>
      </div>
      {update.isError ? (
        <div className="basis-full">
          <NoticeBox error={update.error} onDismiss={() => update.reset()} />
        </div>
      ) : null}
    </div>
  );
}
