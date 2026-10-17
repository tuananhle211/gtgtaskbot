"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type MemberRow } from "@/lib/api";
import { Select } from "@/components/pr";

/**
 * The roster's per-person fields, shared by "Tài khoản › Thành viên" and
 * "Quản trị › Quản lý tài khoản" (2026-10-17). Each saves one field of one
 * account through its own route; the server refuses whoever may not.
 */

export function PasswordStatus({ row }: { row: MemberRow }) {
  if (row.locked) return <span className="st st-red">Đang khoá</span>;
  if (row.password_temporary) return <span className="st st-amber">Mật khẩu tạm</span>;
  return row.has_custom_password ? (
    <span className="st st-green">Đã đổi</span>
  ) : (
    <span className="st st-amber">Mặc định</span>
  );
}

/**
 * "Mã thành viên" (0063): the person's code, in a stream or not - the prefix
 * of their order codes. OWNER / ADMIN only (the server refuses anybody else:
 * 403 `member_code_forbidden`); the server folds what is typed (A-Z0-9, at
 * most 12) and a blank clears it.
 */
export function MemberCodeField({
  row,
  onSaved,
}: {
  row: MemberRow;
  onSaved: (code: string | null) => void;
}) {
  const [value, setValue] = useState(row.member_code ?? "");
  const save = useMutation({
    mutationFn: () => api.setMemberCode(row.user_id, value.trim() || null),
    onSuccess: (result) => {
      setValue(result.member_code ?? "");
      onSaved(result.member_code);
    },
  });
  const changed = value.trim() !== (row.member_code ?? "");
  return (
    <div className="flex items-center gap-1.5">
      <input
        aria-label={`Mã thành viên của ${row.full_name}`}
        value={value}
        maxLength={40}
        placeholder="Chưa có"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && changed && !save.isPending) save.mutate();
        }}
        className="min-h-9 w-24 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 font-mono text-xs uppercase"
      />
      <button
        type="button"
        aria-label={`Lưu mã thành viên của ${row.full_name}`}
        disabled={!changed || save.isPending}
        onClick={() => save.mutate()}
        className="min-h-9 rounded-md border border-[var(--border)] px-2 text-xs font-medium disabled:opacity-40"
      >
        Lưu
      </button>
      {save.isError ? (
        <span role="alert" className="text-xs text-[var(--bad)]">
          {save.error instanceof Error ? save.error.message : "Không lưu được."}
        </span>
      ) : null}
    </div>
  );
}

/**
 * "Nhân sự" (0065): the person's employment status - Đang làm / Thử việc /
 * Tạm nghỉ / Chuyển ban / Đã nghỉ việc. OWNER / ADMIN and whoever holds the resources
 * module's HR_STATUS (the Phòng Tài nguyên's head); the server refuses the
 * rest (403 `employment_status_forbidden`). A leaver's resources turn red at once.
 */
const EMPLOYMENT_STATUSES: ReadonlyArray<[string, string]> = [
  ["WORKING", "Đang làm"],
  ["PROBATION", "Thử việc"],
  ["ON_LEAVE", "Tạm nghỉ"],
  ["MOVED", "Chuyển ban"],
  ["LEFT", "Đã nghỉ việc"],
];

export function EmploymentStatusField({
  row,
  onSaved,
}: {
  row: MemberRow;
  onSaved: (label: string) => void;
}) {
  const save = useMutation({
    mutationFn: (status: string) => api.setEmploymentStatus(row.user_id, status),
    onSuccess: (result) => onSaved(result.employment_status_label),
  });
  return (
    <div className="flex flex-col gap-1">
      <Select
        aria-label={`Trạng thái nhân sự của ${row.full_name}`}
        value={row.employment_status ?? "WORKING"}
        disabled={save.isPending}
        onChange={(event) => save.mutate(event.target.value)}
        className="min-h-9 text-xs"
      >
        {EMPLOYMENT_STATUSES.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      {save.isError ? (
        <span role="alert" className="text-xs text-[var(--bad)]">
          {save.error instanceof Error ? save.error.message : "Không lưu được."}
        </span>
      ) : null}
    </div>
  );
}

export function EmploymentStatusPill({ row }: { row: MemberRow }) {
  const status = row.employment_status ?? "WORKING";
  const tone = status === "LEFT" ? "st-red" : status === "WORKING" ? "st-green" : "st-amber";
  return <span className={`st ${tone}`}>{row.employment_status_label ?? "Đang làm"}</span>;
}

