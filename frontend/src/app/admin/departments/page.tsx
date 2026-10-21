"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type Department,
  type DepartmentBody,
  type DepartmentCatalog,
  type DepartmentGrantCells,
  type DepartmentSeat,
  type Uuid,
} from "@/lib/api";
import { errorMessage } from "@/lib/labels";
import { ConfirmButton } from "@/components/confirm";
import { AdminTabs } from "@/components/admin-tabs";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

/**
 * "Phòng ban": the departments (ADS, Biên Tập, Thiết Kế, Dựng, PR Thương
 * Hiệu, Tài nguyên, Ban Giám đốc… and any added later), who sits in each and
 * as what, and what each position may do in the order flow.
 *
 * The OWNER edits everything here; an ADMIN reads it (no input, no button
 * but the list to pick a department). Anybody else is refused by the server
 * (403 `department_forbidden`), shown as the page's error. Every write is
 * checked again there; after one, the department queries and `["units"]`
 * (the streams' rosters and `/api/units/me`, which follow the seats) are
 * read again.
 */
export default function DepartmentsPage() {
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const list = useQuery({ queryKey: ["departments"], queryFn: api.departments, retry: false });
  const catalog = useQuery({
    queryKey: ["departments", "catalog"],
    queryFn: api.departmentCatalog,
    retry: false,
  });
  const [chosen, setChosen] = useState<Uuid | null>(null);

  if (list.isPending || session.isPending) return <Loading label="Đang tải phòng ban…" />;
  if (list.isError) return <ErrorBox error={list.error} onRetry={() => list.refetch()} />;
  const owner = session.data?.role === "OWNER";
  const departments = [...list.data.departments].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name),
  );
  const selected =
    departments.find((item) => item.id === chosen) ?? departments[0] ?? null;

  return (
    <div className="space-y-4">
      <AdminTabs active="departments" />
      <PageHeader
        title="Phòng ban"
        subtitle="Ai thuộc phòng nào, là Trưởng phòng hay Nhân viên, báo cáo cho ai, và mỗi chức vụ được làm gì trong luồng order."
      />
      {owner ? null : (
        <p className="rounded-lg bg-[var(--surface-muted)] px-3 py-2 text-sm text-[var(--text-muted)]">
          Chỉ chủ sở hữu chỉnh phòng ban.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-3">
          <nav
            aria-label="Danh sách phòng ban"
            className="rounded-xl border border-[var(--border)] bg-[var(--surface)]"
          >
            {departments.length === 0 ? (
              <p className="p-3 text-sm text-[var(--text-muted)]">Chưa có phòng ban nào.</p>
            ) : (
              <ul className="divide-y divide-[var(--border)]">
                {departments.map((department) => (
                  <li key={department.id}>
                    <button
                      type="button"
                      onClick={() => setChosen(department.id)}
                      aria-current={selected?.id === department.id ? "true" : undefined}
                      data-inactive={department.active ? undefined : "true"}
                      className={`flex min-h-11 w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                        selected?.id === department.id
                          ? "bg-[var(--accent-soft)] font-medium"
                          : "hover:bg-[var(--surface-muted)]"
                      } ${department.active ? "" : "opacity-60"}`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate">{department.name}</span>
                        <span className="block text-xs text-[var(--text-muted)]">
                          <span className="font-mono">{department.code}</span>
                          {functionLabel(department, catalog.data) ? (
                            <> · {functionLabel(department, catalog.data)}</>
                          ) : null}
                          {department.active ? null : " · Ngừng hoạt động"}
                        </span>
                      </span>
                      <span
                        className="shrink-0 text-xs tabular-nums text-[var(--text-muted)]"
                        title="Số người"
                      >
                        {department.member_count ?? 0} người
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
          {owner ? (
            <CreateDepartment
              catalog={catalog.data}
              onCreated={(department) => setChosen(department.id)}
            />
          ) : null}
        </div>
        {selected ? (
          <div className="min-w-0 space-y-4">
            <DepartmentHeader
              key={`${selected.id}:${selected.code}:${selected.name}:${selected.function}:${selected.active}`}
              department={selected}
              catalog={catalog.data}
              owner={owner}
            />
            <Members key={`members:${selected.id}`} department={selected} catalog={catalog.data} owner={owner} />
            {modulesOf(catalog.data).map((module) => (
              <Grants
                key={`grants:${selected.id}:${module.module}`}
                department={selected}
                catalog={catalog.data}
                module={module}
                owner={owner}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

const POSITION_FALLBACK: Record<string, string> = { HEAD: "Trưởng phòng", MEMBER: "Nhân viên" };

/** The function's words: the catalogue's, else what the row carries. */
function functionLabel(department: Department, catalog: DepartmentCatalog | undefined) {
  if (department.function === "NONE") return null;
  return (
    catalog?.functions.find((item) => item.value === department.function)?.label ??
    department.function_label ??
    department.function
  );
}

function positionLabel(position: string, catalog: DepartmentCatalog | undefined) {
  return (
    catalog?.positions.find((item) => item.value === position)?.label ??
    POSITION_FALLBACK[position] ??
    position
  );
}

/** The field a 422 names (`details.field`), or null. */
function refusedField(error: unknown): string | null {
  return error instanceof ApiError && typeof error.details?.field === "string"
    ? error.details.field
    : null;
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return errorMessage(error.code, error.details) ?? error.message;
  return error instanceof Error ? error.message : "Thao tác không thực hiện được.";
}

/**
 * The server's sentence under the control it is about. A refusal that names
 * no field (or another one) is the caller's to show as a NoticeBox.
 */
function FieldError({ id, error, field }: { id: string; error: unknown; field: string }) {
  if (refusedField(error) !== field) return null;
  return (
    <span id={id} role="alert" className="mt-1 block text-xs font-medium text-[var(--bad)]">
      {messageOf(error)}
    </span>
  );
}

function useRefreshDepartments() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["departments"] });
    void queryClient.invalidateQueries({ queryKey: ["units"] });
  };
}

function FunctionOptions({ catalog }: { catalog: DepartmentCatalog | undefined }) {
  const functions = catalog?.functions ?? [{ value: "NONE", label: "Không" }];
  return (
    <>
      {functions.map((item) => (
        <option key={item.value} value={item.value}>
          {item.label}
        </option>
      ))}
    </>
  );
}

/** "Thêm phòng" (OWNER): code, name, function. */
function CreateDepartment({
  catalog,
  onCreated,
}: {
  catalog: DepartmentCatalog | undefined;
  onCreated: (department: Department) => void;
}) {
  const refresh = useRefreshDepartments();
  const ids = { code: useId(), name: useId(), codeError: useId(), nameError: useId() };
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [fn, setFn] = useState("NONE");
  const create = useMutation({
    mutationFn: () =>
      api.createDepartment({ code: code.trim(), name: name.trim(), function: fn }),
    onSuccess: (department) => {
      setCode("");
      setName("");
      setFn("NONE");
      refresh();
      onCreated(department);
    },
  });
  const field = refusedField(create.error);
  return (
    <form
      aria-label="Thêm phòng"
      className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3"
      onSubmit={(event) => event.preventDefault()}
    >
      <h2 className="text-sm font-semibold">Thêm phòng</h2>
      <div>
        <label htmlFor={ids.code} className="text-xs text-[var(--text-muted)]">
          Mã phòng
        </label>
        <input
          id={ids.code}
          type="text"
          value={code}
          maxLength={40}
          onChange={(event) => setCode(event.target.value.toUpperCase())}
          placeholder="HR"
          aria-invalid={field === "code" || undefined}
          aria-describedby={field === "code" ? ids.codeError : undefined}
          className={`mt-1 ${FIELD} font-mono`}
        />
        <span className="mt-0.5 block text-[11px] text-[var(--text-muted)]">
          Chữ A-Z và số, tối đa 12 ký tự; dùng làm tiền tố mã.
        </span>
        <FieldError id={ids.codeError} error={create.error} field="code" />
      </div>
      <div>
        <label htmlFor={ids.name} className="text-xs text-[var(--text-muted)]">
          Tên phòng
        </label>
        <input
          id={ids.name}
          type="text"
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          placeholder="Hành chính nhân sự"
          aria-invalid={field === "name" || undefined}
          aria-describedby={field === "name" ? ids.nameError : undefined}
          className={`mt-1 ${FIELD}`}
        />
        <FieldError id={ids.nameError} error={create.error} field="name" />
      </div>
      <label className="block text-xs text-[var(--text-muted)]">
        Chức năng
        <Select
          value={fn}
          onChange={(event) => setFn(event.target.value)}
          className="mt-1 w-full text-sm"
        >
          <FunctionOptions catalog={catalog} />
        </Select>
      </label>
      <ConfirmButton
        spec={{
          title: `Thêm phòng ${name.trim() || code.trim()}?`,
          description:
            "Phòng mới chưa có ai và chưa có quyền gì trong luồng order cho đến khi được gắn người và cấp quyền.",
          confirmLabel: "Thêm phòng",
        }}
        onConfirm={() => create.mutate()}
        pending={create.isPending}
        error={create.error}
        disabled={!code.trim() || !name.trim()}
        tone="primary"
        className="w-full"
        onOpenChange={(open) => {
          if (open) create.reset();
        }}
      >
        Thêm phòng
      </ConfirmButton>
      {create.isError && field !== "code" && field !== "name" ? (
        <NoticeBox error={create.error} />
      ) : null}
    </form>
  );
}

/** The department's name, code, function and whether it is active. */
function DepartmentHeader({
  department,
  catalog,
  owner,
}: {
  department: Department;
  catalog: DepartmentCatalog | undefined;
  owner: boolean;
}) {
  const refresh = useRefreshDepartments();
  const ids = { code: useId(), name: useId(), codeError: useId(), nameError: useId() };
  const [draft, setDraft] = useState<DepartmentBody>({});
  const save = useMutation({
    mutationFn: () => api.updateDepartment(department.id, draft),
    onSuccess: () => {
      setDraft({});
      refresh();
    },
  });
  const field = refusedField(save.error);
  const value = {
    code: draft.code ?? department.code,
    name: draft.name ?? department.name,
    function: draft.function ?? department.function,
    active: draft.active ?? department.active,
  };
  const change = <K extends keyof DepartmentBody>(key: K, next: DepartmentBody[K]) =>
    setDraft((current) => {
      const copy = { ...current };
      if (next === department[key as keyof Department]) delete copy[key];
      else copy[key] = next;
      return copy;
    });

  if (!owner) {
    return (
      <section
        aria-label="Thông tin phòng"
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <h2 className="text-lg font-semibold">
          {department.name}{" "}
          <span className="font-mono text-sm text-[var(--text-muted)]">{department.code}</span>
        </h2>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Chức năng: {functionLabel(department, catalog) ?? "Không"}
          {department.active ? "" : " · "}
          {department.active ? null : <Pill tone="bad">Ngừng hoạt động</Pill>}
        </p>
      </section>
    );
  }
  const dirty = Object.keys(draft).length > 0;
  return (
    <form
      aria-label="Thông tin phòng"
      className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 sm:grid-cols-2"
      onSubmit={(event) => event.preventDefault()}
    >
      <div>
        <label htmlFor={ids.name} className="text-xs text-[var(--text-muted)]">
          Tên phòng
        </label>
        <input
          id={ids.name}
          type="text"
          value={value.name}
          maxLength={200}
          onChange={(event) => change("name", event.target.value)}
          aria-invalid={field === "name" || undefined}
          aria-describedby={field === "name" ? ids.nameError : undefined}
          className={`mt-1 ${FIELD}`}
        />
        <FieldError id={ids.nameError} error={save.error} field="name" />
      </div>
      <div>
        <label htmlFor={ids.code} className="text-xs text-[var(--text-muted)]">
          Mã phòng
        </label>
        <input
          id={ids.code}
          type="text"
          value={value.code}
          maxLength={40}
          onChange={(event) => change("code", event.target.value.toUpperCase())}
          aria-invalid={field === "code" || undefined}
          aria-describedby={field === "code" ? ids.codeError : undefined}
          className={`mt-1 ${FIELD} font-mono`}
        />
        <FieldError id={ids.codeError} error={save.error} field="code" />
      </div>
      <label className="block text-xs text-[var(--text-muted)]">
        Chức năng
        <Select
          value={value.function}
          onChange={(event) => change("function", event.target.value)}
          className="mt-1 w-full text-sm"
        >
          <FunctionOptions catalog={catalog} />
        </Select>
      </label>
      <label className="inline-flex min-h-11 items-center gap-2 self-end text-sm">
        <input
          type="checkbox"
          checked={value.active}
          onChange={(event) => change("active", event.target.checked)}
        />
        Đang hoạt động
      </label>
      <div className="flex flex-wrap items-center justify-end gap-2 sm:col-span-2">
        {dirty ? (
          <button
            type="button"
            onClick={() => {
              setDraft({});
              save.reset();
            }}
            className="min-h-11 rounded-lg border border-[var(--border)] px-4 text-sm"
          >
            Bỏ thay đổi
          </button>
        ) : null}
        <ConfirmButton
          spec={{
            title: `Lưu phòng ${value.name}?`,
            description:
              draft.active === false
                ? "Phòng ngừng hoạt động: người trong phòng không còn nhận quyền của phòng trong luồng order. Bật lại được sau."
                : "Tên, mã và chức năng mới áp dụng ngay.",
            confirmLabel: "Lưu",
            variant: draft.active === false ? "destructive" : "primary",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!dirty || !value.name.trim() || !value.code.trim()}
          tone="primary"
          onOpenChange={(open) => {
            if (open) save.reset();
          }}
        >
          Lưu phòng
        </ConfirmButton>
      </div>
      {save.isError && field !== "code" && field !== "name" ? (
        <div className="sm:col-span-2">
          <NoticeBox error={save.error} />
        </div>
      ) : null}
    </form>
  );
}

/** "Thành viên": the seats, heads first, and "Thêm người" (OWNER). */
function Members({
  department,
  catalog,
  owner,
}: {
  department: Department;
  catalog: DepartmentCatalog | undefined;
  owner: boolean;
}) {
  const members = useQuery({
    queryKey: ["departments", department.id, "members"],
    queryFn: () => api.departmentMembers(department.id),
  });
  const directory = useQuery({
    queryKey: ["units", "directory"],
    queryFn: api.unitDirectory,
    enabled: owner,
  });
  const seats = members.data?.members ?? [];
  const heads = seats.filter((seat) => seat.position === "HEAD" && seat.active !== false);
  const seated = new Set(seats.map((seat) => seat.user_id));
  const candidates = (directory.data ?? []).filter(
    (user) => user.active && !seated.has(user.user_id),
  );
  return (
    <section
      aria-label="Thành viên"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)]"
    >
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold">Thành viên</h2>
        <p className="mt-0.5 text-xs text-[var(--text-muted)]">
          Một người có thể thuộc nhiều phòng. Phòng có thể có nhiều Trưởng phòng; mỗi Nhân viên
          báo cáo cho một Trưởng quản lý trong phòng.
        </p>
      </div>
      {owner ? (
        <AddSeat department={department} catalog={catalog} candidates={candidates} />
      ) : null}
      {members.isPending ? <Loading label="Đang tải thành viên…" /> : null}
      {members.isError ? (
        <div className="p-3">
          <ErrorBox error={members.error} onRetry={() => members.refetch()} />
        </div>
      ) : null}
      {members.data && seats.length === 0 ? (
        <p className="p-4 text-sm text-[var(--text-muted)]">Phòng chưa có ai.</p>
      ) : null}
      {seats.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
                <th className="px-3 py-2 font-medium">Họ tên</th>
                <th className="px-3 py-2 font-medium">Chức vụ</th>
                <th className="px-3 py-2 font-medium">Trưởng quản lý</th>
                {owner ? (
                  <th className="px-3 py-2 font-medium">
                    <span className="sr-only">Thao tác</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {seats.map((seat) => (
                <SeatRow
                  key={seat.user_id}
                  department={department}
                  seat={seat}
                  heads={heads}
                  catalog={catalog}
                  owner={owner}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function AddSeat({
  department,
  catalog,
  candidates,
}: {
  department: Department;
  catalog: DepartmentCatalog | undefined;
  candidates: Array<{ user_id: Uuid; full_name: string }>;
}) {
  const refresh = useRefreshDepartments();
  const [userId, setUserId] = useState("");
  const [position, setPosition] = useState("MEMBER");
  const add = useMutation({
    mutationFn: () => api.seatDepartmentMember(department.id, { user_id: userId, position }),
    onSuccess: () => {
      setUserId("");
      refresh();
    },
  });
  const positions = catalog?.positions ?? [
    { value: "HEAD", label: POSITION_FALLBACK.HEAD },
    { value: "MEMBER", label: POSITION_FALLBACK.MEMBER },
  ];
  const person = candidates.find((user) => user.user_id === userId)?.full_name ?? "người này";
  return (
    <form
      aria-label="Thêm người"
      className="flex flex-wrap items-end gap-3 border-b border-[var(--border)] px-4 py-3"
      onSubmit={(event) => event.preventDefault()}
    >
      <label className="flex min-w-[14rem] flex-1 flex-col gap-1 text-xs text-[var(--text-muted)]">
        Thêm người
        <Select value={userId} onChange={(event) => setUserId(event.target.value)}>
          <option value="">— chọn tài khoản —</option>
          {candidates.map((user) => (
            <option key={user.user_id} value={user.user_id}>
              {user.full_name}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
        Chức vụ
        <Select
          value={position}
          onChange={(event) => setPosition(event.target.value)}
          aria-label="Chức vụ của người thêm"
        >
          {positions.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </Select>
      </label>
      <ConfirmButton
        spec={{
          title: `Thêm ${person} vào ${department.name}?`,
          description: `${person} vào phòng với chức vụ ${positionLabel(position, catalog)} và nhận quyền của chức vụ đó trong luồng order. Chỗ ở phòng khác (nếu có) giữ nguyên.`,
          confirmLabel: "Thêm vào phòng",
        }}
        onConfirm={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
        disabled={!userId}
        tone="primary"
        onOpenChange={(open) => {
          if (open) add.reset();
        }}
      >
        Thêm vào phòng
      </ConfirmButton>
      {add.isError ? (
        <div className="basis-full">
          <NoticeBox error={add.error} onDismiss={() => add.reset()} />
        </div>
      ) : null}
    </form>
  );
}

function SeatRow({
  department,
  seat,
  heads,
  catalog,
  owner,
}: {
  department: Department;
  seat: DepartmentSeat;
  heads: DepartmentSeat[];
  catalog: DepartmentCatalog | undefined;
  owner: boolean;
}) {
  const refresh = useRefreshDepartments();
  const managerErrorId = useId();
  const update = useMutation({
    mutationFn: (body: { position?: string; manager_user_id?: Uuid | null }) =>
      api.seatDepartmentMember(department.id, { user_id: seat.user_id, ...body }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.unseatDepartmentMember(department.id, seat.user_id),
    onSuccess: refresh,
  });
  const positions = catalog?.positions ?? [
    { value: "HEAD", label: POSITION_FALLBACK.HEAD },
    { value: "MEMBER", label: POSITION_FALLBACK.MEMBER },
  ];
  const bosses = heads.filter((head) => head.user_id !== seat.user_id);
  const managerName = heads.find((head) => head.user_id === seat.manager_user_id)?.full_name;
  const field = refusedField(update.error);
  const active = seat.active !== false;
  return (
    <tr
      data-inactive={active ? undefined : "true"}
      className={`border-b border-[var(--border)] align-top ${active ? "" : "opacity-60"}`}
    >
      <td className="px-3 py-2 font-medium">
        <Link href={`/admin/people/${seat.user_id}`} className="hover:underline">
          {seat.full_name}
        </Link>
        {active ? null : (
          <span className="mt-1 block">
            <Pill tone="bad">Đã vô hiệu hoá</Pill>
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        {owner ? (
          <Select
            value={seat.position}
            onChange={(event) => update.mutate({ position: event.target.value })}
            disabled={update.isPending}
            aria-label={`Chức vụ của ${seat.full_name}`}
          >
            {positions.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        ) : (
          <span>{seat.position_label ?? positionLabel(seat.position, catalog)}</span>
        )}
        {update.isError && field !== "manager_user_id" ? (
          <div className="mt-1.5 min-w-[14rem]">
            <NoticeBox error={update.error} onDismiss={() => update.reset()} />
          </div>
        ) : null}
      </td>
      <td className="px-3 py-2">
        {seat.position === "HEAD" ? (
          <span className="text-xs text-[var(--text-muted)]">–</span>
        ) : owner ? (
          <>
            <Select
              value={seat.manager_user_id ?? ""}
              onChange={(event) =>
                update.mutate({ manager_user_id: event.target.value || null })
              }
              disabled={update.isPending}
              aria-label={`Trưởng quản lý của ${seat.full_name}`}
              aria-invalid={field === "manager_user_id" || undefined}
              aria-describedby={field === "manager_user_id" ? managerErrorId : undefined}
            >
              <option value="">— Không có —</option>
              {bosses.map((head) => (
                <option key={head.user_id} value={head.user_id}>
                  {head.full_name}
                </option>
              ))}
            </Select>
            <FieldError id={managerErrorId} error={update.error} field="manager_user_id" />
          </>
        ) : (
          <span>{managerName ?? "–"}</span>
        )}
      </td>
      {owner ? (
        <td className="px-3 py-2">
          <div className="flex justify-end">
            <ConfirmButton
              spec={{
                title: `Gỡ ${seat.full_name} khỏi ${department.name}?`,
                description:
                  "Người này mất quyền của phòng trong luồng order; ai đang báo cáo cho họ trong phòng không còn trưởng quản lý. Lịch sử được giữ; thêm lại được sau.",
                confirmLabel: "Gỡ khỏi phòng",
                variant: "destructive",
              }}
              onConfirm={() => remove.mutate()}
              pending={remove.isPending}
              error={remove.error}
              tone="danger"
              ariaLabel={`Gỡ ${seat.full_name} khỏi phòng`}
              className="min-h-9 px-3 text-xs"
              onOpenChange={(open) => {
                if (open) remove.reset();
              }}
            >
              Gỡ
            </ConfirmButton>
          </div>
          {remove.isError ? <NoticeBox error={remove.error} /> : null}
        </td>
      ) : null}
    </tr>
  );
}

/** One module a department grants permissions in: the order flow, Tài nguyên (0065)... */
type GrantModule = {
  module: string;
  module_label: string;
  permissions: DepartmentCatalog["permissions"];
};

/** The modules of the catalogue - an older API lists only the order flow's. */
function modulesOf(catalog: DepartmentCatalog | undefined): GrantModule[] {
  if (!catalog) return [];
  if (catalog.modules && catalog.modules.length > 0) return catalog.modules;
  return [
    {
      module: catalog.module ?? "ORDER",
      module_label: catalog.module_label ?? "Luồng order",
      permissions: catalog.permissions,
    },
  ];
}

/**
 * "Quyền trong luồng order" / "Quyền trong quản lý tài nguyên": permission ×
 * position, one scope per cell, among the scopes that permission allows.
 * "Lưu quyền" sends only the cells changed, for this module.
 */
function Grants({
  department,
  catalog,
  module,
  owner,
}: {
  department: Department;
  catalog: DepartmentCatalog | undefined;
  module: GrantModule;
  owner: boolean;
}) {
  const refresh = useRefreshDepartments();
  const isOrder = module.module === "ORDER";
  const grants = useQuery({
    queryKey: ["departments", department.id, "grants", module.module],
    queryFn: () => api.departmentGrants(department.id, isOrder ? undefined : module.module),
  });
  const [draft, setDraft] = useState<DepartmentGrantCells>({});
  const save = useMutation({
    mutationFn: () =>
      api.saveDepartmentGrants(department.id, draft, isOrder ? undefined : module.module),
    onSuccess: () => {
      setDraft({});
      refresh();
    },
  });
  const title = `Quyền trong ${module.module_label.toLowerCase()}`;
  // The PR review gates are held per person over a scope, never a cell here.
  const cells = module.permissions.filter((permission) => !permission.personal_only);
  const personalOnly = module.permissions.filter((permission) => permission.personal_only);
  if (!catalog) return null;
  const labels = catalog.scope_labels ?? {};
  const saved = grants.data?.cells ?? {};
  const scopeOf = (position: string, permission: string) =>
    draft[position]?.[permission] ?? saved[position]?.[permission] ?? "NONE";
  const setCell = (position: string, permission: string, scope: string) =>
    setDraft((current) => {
      const row = { ...(current[position] ?? {}) };
      if ((saved[position]?.[permission] ?? "NONE") === scope) delete row[permission];
      else row[permission] = scope;
      const next = { ...current, [position]: row };
      if (Object.keys(row).length === 0) delete next[position];
      return next;
    });
  const changed = Object.values(draft).reduce((sum, row) => sum + Object.keys(row).length, 0);
  const tone = (scope: string) =>
    scope === "ALL"
      ? "bg-[var(--good-soft)] text-[var(--good)]"
      : scope === "OWN"
        ? "bg-[var(--warn-soft)] text-[var(--warn)]"
        : "bg-[var(--surface-muted)] text-[var(--text-muted)]";
  return (
    <section
      aria-label={title}
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            {isOrder
              ? `Mỗi chức vụ của ${department.name} được làm gì với order. Một người ở nhiều phòng được cộng quyền của các phòng đó. Chủ sở hữu luôn có mọi quyền.`
              : `Mỗi chức vụ của ${department.name} được làm gì trong mục Tài nguyên. Phòng Tài nguyên quản lý toàn bộ; Ban Giám đốc xem toàn bộ; phòng khác xem tài nguyên của mình.`}
          </p>
        </div>
        {owner ? (
          <ConfirmButton
            spec={{
              title: `Lưu quyền của ${department.name}?`,
              description: isOrder
                ? "Áp dụng ngay cho mọi order: ai trong phòng được tạo, duyệt, giao việc, huỷ, xem."
                : "Áp dụng ngay cho mục Tài nguyên: ai trong phòng được xem, sửa, nhập Excel, cài đặt, xem báo cáo.",
              confirmLabel: "Lưu quyền",
              count: changed > 1 ? changed : undefined,
            }}
            onConfirm={() => save.mutate()}
            pending={save.isPending}
            error={save.error}
            disabled={changed === 0}
            tone="primary"
            onOpenChange={(open) => {
              if (open) save.reset();
            }}
          >
            Lưu quyền
          </ConfirmButton>
        ) : null}
      </div>
      {grants.isPending ? <Loading label="Đang tải quyền…" /> : null}
      {grants.isError ? <ErrorBox error={grants.error} onRetry={() => grants.refetch()} /> : null}
      {grants.data ? (
        <div className="mt-3 overflow-x-auto">
          <table className="table-dense">
            <thead>
              <tr>
                <th scope="col">Quyền</th>
                {catalog.positions.map((position) => (
                  <th key={position.value} scope="col">
                    {position.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cells.map((permission) => (
                <tr key={permission.key}>
                  <th scope="row" className="text-left font-medium">
                    {permission.label}
                    {permission.own_meaning && permission.scopes.includes("OWN") ? (
                      <span className="block text-[11px] font-normal text-[var(--text-muted)]">
                        {(permission.scope_labels ?? labels).OWN ?? "Của mình"}: {permission.own_meaning}
                      </span>
                    ) : null}
                  </th>
                  {catalog.positions.map((position) => {
                    // A permission whose lowest scope is not "Không" (Tài nguyên's
                    // VIEW: everybody's floor) reads an empty cell as that floor.
                    const stored = scopeOf(position.value, permission.key);
                    const scope = permission.scopes.includes(stored) ? stored : permission.scopes[0];
                    const wordOf = (value: string) =>
                      permission.scope_labels?.[value] ?? labels[value] ?? value;
                    const edited = draft[position.value]?.[permission.key] !== undefined;
                    return (
                      <td key={position.value}>
                        {owner ? (
                          <select
                            aria-label={`${permission.label} · ${position.label}`}
                            value={scope}
                            data-edited={edited ? "true" : undefined}
                            onChange={(event) =>
                              setCell(position.value, permission.key, event.target.value)
                            }
                            className={`min-h-9 rounded-md border px-2 text-xs font-medium ${tone(scope)} ${
                              edited ? "border-[var(--accent)]" : "border-[var(--border)]"
                            }`}
                          >
                            {permission.scopes.map((option) => (
                              <option key={option} value={option}>
                                {wordOf(option)}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span
                            className={`inline-block rounded-md px-2 py-1 text-xs font-medium ${tone(scope)}`}
                          >
                            {wordOf(scope)}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {personalOnly.length > 0 ? (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Cấp theo từng người, theo kênh và loại nội dung (không phải ô phòng ban):{" "}
          {personalOnly.map((permission) => permission.label).join(", ")} — ở{" "}
          <Link href="/pr/permissions?tab=grants" className="text-[var(--accent)] underline">
            Thành viên &amp; Phân quyền (PR) › Quyền duyệt cấp thêm
          </Link>
          .
        </p>
      ) : null}
      {save.isError ? (
        <div className="mt-3">
          <NoticeBox error={save.error} />
        </div>
      ) : null}
    </section>
  );
}
