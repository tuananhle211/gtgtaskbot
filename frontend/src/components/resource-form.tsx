"use client";

import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type ResourceBody,
  type ResourceDetail,
  type ResourceOptions,
  type ResourceRow,
  type Uuid,
} from "@/lib/api";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { NoticeBox } from "@/components/states";
import { SECRET_WARNING, looksLikeSecret, optionsOf } from "@/lib/resources";

/**
 * The "Thêm / Sửa tài nguyên" dialog (Phòng Tài nguyên).
 *
 * Every field of the requirement's section 3.1, and none for a password or a
 * 2FA code: there is no such field, and a note or a purpose that reads like
 * one gets a warning under it (rule 1). The id is text: a long number and a
 * leading zero stay as typed (rule 2). A duplicate id within the type is
 * refused by the server and shown under the field (rule 3). On an edit the
 * holder and the status are not here - they have their own buttons on the
 * profile, which write the histories.
 */
export const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

type Draft = {
  department_id: string;
  type_id: string;
  external_id: string;
  name: string;
  link: string;
  holder_user_id: string;
  role_id: string;
  email: string;
  phone: string;
  two_factor_id: string;
  two_factor_holder_user_id: string;
  origin_id: string;
  started_on: string;
  purpose: string;
  handover_id: string;
  status_id: string;
  note: string;
};

function draftOf(row: ResourceRow | null, options: ResourceOptions | undefined): Draft {
  const first = (kind: string, code: string) =>
    optionsOf(options, kind).find((option) => option.code === code)?.id ??
    optionsOf(options, kind)[0]?.id ??
    "";
  return {
    department_id: row?.department.id ?? options?.departments[0]?.id ?? "",
    type_id: row?.type.id ?? "",
    external_id: row?.external_id ?? "",
    name: row?.name ?? "",
    link: row?.link ?? "",
    holder_user_id: row?.holder?.user_id ?? "",
    role_id: row?.role?.id ?? "",
    email: row?.email ?? "",
    phone: row?.phone ?? "",
    two_factor_id: row?.two_factor?.id ?? "",
    two_factor_holder_user_id: row?.two_factor_holder?.user_id ?? "",
    origin_id: row?.origin?.id ?? "",
    started_on: row?.started_on ?? "",
    purpose: row?.purpose ?? "",
    handover_id: row?.handover.id ?? first("HANDOVER", "NOT_YET"),
    status_id: row?.status.id ?? first("STATUS", "ACTIVE"),
    note: row?.note ?? "",
  };
}

function bodyOf(draft: Draft): ResourceBody {
  const orNull = (value: string) => (value.trim() ? value.trim() : null);
  return {
    department_id: draft.department_id,
    type_id: draft.type_id,
    external_id: draft.external_id.trim(),
    name: draft.name.trim(),
    link: orNull(draft.link),
    holder_user_id: orNull(draft.holder_user_id),
    role_id: orNull(draft.role_id),
    email: orNull(draft.email),
    phone: orNull(draft.phone),
    two_factor_id: orNull(draft.two_factor_id),
    two_factor_holder_user_id: orNull(draft.two_factor_holder_user_id),
    origin_id: orNull(draft.origin_id),
    started_on: orNull(draft.started_on),
    purpose: orNull(draft.purpose),
    handover_id: orNull(draft.handover_id),
    status_id: orNull(draft.status_id),
    note: orNull(draft.note),
  };
}

/** The field a 422 names (`details.field`), or null. */
export function refusedField(error: unknown): string | null {
  return error instanceof ApiError && typeof error.details?.field === "string"
    ? error.details.field
    : null;
}

export function ResourceFormDialog({
  open,
  onClose,
  options,
  resource = null,
  onSaved,
  limited = false,
}: {
  open: boolean;
  onClose: () => void;
  options: ResourceOptions | undefined;
  /** The resource being edited; null = a new one. */
  resource?: ResourceRow | null;
  onSaved: (detail: ResourceDetail) => void;
  /**
   * The editor is the holder or their head, not the Phòng Tài nguyên: the
   * "Ban quản lý", the type and the ID cannot change (the server refuses
   * them, 2026-10-21).
   */
  limited?: boolean;
}) {
  const queryClient = useQueryClient();
  const ids = {
    department: useId(),
    type: useId(),
    external: useId(),
    name: useId(),
    link: useId(),
    holder: useId(),
    role: useId(),
    email: useId(),
    phone: useId(),
    twoFactor: useId(),
    twoFactorHolder: useId(),
    origin: useId(),
    started: useId(),
    purpose: useId(),
    handover: useId(),
    status: useId(),
    note: useId(),
  };
  const people = useQuery({
    queryKey: ["resources", "people"],
    queryFn: () => api.resourcePeople(),
    enabled: open,
  });
  const [draft, setDraft] = useState<Draft>(() => draftOf(resource, options));
  const editing = resource !== null;
  const save = useMutation({
    mutationFn: () => {
      const body = bodyOf(draft);
      if (editing) {
        // The holder and the status are the profile's own buttons.
        const { holder_user_id: _holder, status_id: _status, ...rest } = body;
        return api.updateResource(resource.id, rest);
      }
      return api.createResource(body);
    },
    onSuccess: (detail) => {
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
      onSaved(detail);
    },
  });
  const field = refusedField(save.error);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const candidates = useMemo(
    () => (people.data?.people ?? []).filter((person) => person.active),
    [people.data],
  );
  const ready = draft.department_id && draft.type_id && draft.name.trim();
  const errorOf = (name: string) =>
    field === name && save.error instanceof ApiError ? save.error.message : null;
  const select = (
    id: string,
    label: string,
    key: keyof Draft,
    kind: string,
    { required = false, keep, disabled = false }: { required?: boolean; keep?: string | null; disabled?: boolean } = {},
  ) => (
    <label htmlFor={id} className="block text-xs text-[var(--text-muted)]">
      {label}
      {required ? " *" : ""}
      <Select
        id={id}
        value={draft[key]}
        onChange={(event) => set(key, event.target.value)}
        className="mt-1 w-full text-sm"
        aria-invalid={field === key || undefined}
        disabled={disabled}
      >
        {required ? null : <option value="">— Không —</option>}
        {required && !draft[key] ? <option value="">— chọn —</option> : null}
        {optionsOf(options, kind, keep).map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </Select>
      {errorOf(key) ? (
        <span role="alert" className="mt-1 block text-xs font-medium text-[var(--bad)]">
          {errorOf(key)}
        </span>
      ) : null}
    </label>
  );
  const personSelect = (id: string, label: string, key: keyof Draft, hint: string) => (
    <label htmlFor={id} className="block text-xs text-[var(--text-muted)]">
      {label}
      <Select
        id={id}
        value={draft[key]}
        onChange={(event) => set(key, event.target.value)}
        className="mt-1 w-full text-sm"
        aria-invalid={field === key || undefined}
      >
        <option value="">{hint}</option>
        {candidates.map((person) => (
          <option key={person.user_id} value={person.user_id}>
            {person.full_name}
            {person.departments.length > 0 ? ` · ${person.departments.map((d) => d.code).join(", ")}` : ""}
          </option>
        ))}
      </Select>
      {errorOf(key) ? (
        <span role="alert" className="mt-1 block text-xs font-medium text-[var(--bad)]">
          {errorOf(key)}
        </span>
      ) : null}
    </label>
  );
  const text = (
    id: string,
    label: string,
    key: keyof Draft,
    { required = false, type = "text", placeholder = "", mono = false, disabled = false }: { required?: boolean; type?: string; placeholder?: string; mono?: boolean; disabled?: boolean } = {},
  ) => (
    <label htmlFor={id} className="block text-xs text-[var(--text-muted)]">
      {label}
      {required ? " *" : ""}
      <input
        id={id}
        type={type}
        value={draft[key]}
        placeholder={placeholder}
        onChange={(event) => set(key, event.target.value)}
        aria-invalid={field === key || undefined}
        disabled={disabled}
        className={`mt-1 ${FIELD} ${mono ? "font-mono" : ""} disabled:opacity-60`}
        {...(type === "text" && mono ? { inputMode: "text" as const, autoComplete: "off" } : {})}
      />
      {errorOf(key) ? (
        <span role="alert" className="mt-1 block text-xs font-medium text-[var(--bad)]">
          {errorOf(key)}
        </span>
      ) : null}
    </label>
  );
  const area = (id: string, label: string, key: "purpose" | "note") => (
    <label htmlFor={id} className="block text-xs text-[var(--text-muted)] sm:col-span-2">
      {label}
      <textarea
        id={id}
        value={draft[key]}
        rows={2}
        onChange={(event) => set(key, event.target.value)}
        className={`mt-1 ${FIELD} min-h-[4.5rem] py-2`}
      />
      {looksLikeSecret(draft[key]) ? (
        <span role="alert" className="mt-1 block text-xs font-medium text-[var(--warn)]">
          {SECRET_WARNING}
        </span>
      ) : null}
    </label>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? `Sửa ${resource.code}` : "Thêm tài nguyên"}
      description={
        editing
          ? "Mã tài nguyên không đổi. Người giữ và trạng thái có nút riêng trên hồ sơ."
          : "Mã tài nguyên được cấp tự động theo ban (ADS-001, BT-001…)."
      }
      busy={save.isPending}
      size="xl"
    >
      <form
        aria-label={editing ? "Sửa tài nguyên" : "Thêm tài nguyên"}
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !save.isPending) save.mutate();
        }}
      >
        <ModalBody className="grid gap-3 sm:grid-cols-2">
          <label htmlFor={ids.department} className="block text-xs text-[var(--text-muted)]">
            Ban quản lý *
            <Select
              id={ids.department}
              value={draft.department_id}
              onChange={(event) => set("department_id", event.target.value)}
              className="mt-1 w-full text-sm"
              aria-invalid={field === "department_id" || undefined}
              disabled={limited}
            >
              {(options?.departments ?? []).map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </Select>
            {limited ? (
              <span className="mt-1 block text-[11px]">
                Chỉ Phòng Tài nguyên đổi được ban quản lý, loại và ID.
              </span>
            ) : null}
          </label>
          {select(ids.type, "Loại", "type_id", "TYPE", { required: true, keep: resource?.type.id, disabled: limited })}
          {text(ids.external, "ID tài nguyên", "external_id", {
            mono: true,
            disabled: limited,
            placeholder: "Để trống: hệ thống lấy mã tài nguyên làm ID",
          })}
          {text(ids.name, "Tên tài nguyên", "name", { required: true })}
          {text(ids.link, "Link", "link", { placeholder: "Nhập tuỳ ý: đường dẫn, ghi chú…" })}
          {editing ? null : personSelect(ids.holder, "Người được bàn giao", "holder_user_id", "— Phòng Tài nguyên giữ —")}
          {select(ids.role, "Quyền đang có", "role_id", "ROLE", { keep: resource?.role?.id })}
          {text(ids.email, "Email đăng ký", "email", { type: "email" })}
          {text(ids.phone, "SĐT đăng ký", "phone", { mono: true, placeholder: "0912…" })}
          {select(ids.twoFactor, "Bảo mật 2 lớp", "two_factor_id", "TWO_FACTOR", {
            keep: resource?.two_factor?.id,
          })}
          {personSelect(ids.twoFactorHolder, "Người giữ mã 2 lớp", "two_factor_holder_user_id", "— Không —")}
          {select(ids.origin, "Nguồn gốc", "origin_id", "ORIGIN", { keep: resource?.origin?.id })}
          {text(ids.started, "Ngày bắt đầu dùng", "started_on", { type: "date" })}
          {select(ids.handover, "Bàn giao về Tài nguyên", "handover_id", "HANDOVER", {
            required: true,
            keep: resource?.handover.id,
          })}
          {editing
            ? null
            : select(ids.status, "Trạng thái hiện tại", "status_id", "STATUS", {
                required: true,
              })}
          {area(ids.purpose, "Mục đích sử dụng", "purpose")}
          {area(ids.note, "Ghi chú", "note")}
          {save.isError && !field ? (
            <div className="sm:col-span-2">
              <NoticeBox error={save.error} onDismiss={() => save.reset()} />
            </div>
          ) : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose} disabled={save.isPending} data-modal-close>
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={!ready || save.isPending}>
            {save.isPending ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Thêm tài nguyên"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}
