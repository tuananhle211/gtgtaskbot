"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type LegalEntity, type MemberRow, type WorkInfoInput } from "@/lib/api";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmDialog } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";

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
 * "Mã nhân viên" (0063): the person's code, in a stream or not - the prefix
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
        aria-label={`Mã nhân viên của ${row.full_name}`}
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
        aria-label={`Lưu mã nhân viên của ${row.full_name}`}
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
  // "Đã nghỉ việc" locks the person out of the web app at once (no login,
  // every session closed), so it is confirmed first; the others save directly.
  const [leaving, setLeaving] = useState(false);
  const save = useMutation({
    mutationFn: (status: string) => api.setEmploymentStatus(row.user_id, status),
    onSuccess: (result) => {
      setLeaving(false);
      onSaved(result.employment_status_label);
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <Select
        aria-label={`Trạng thái nhân sự của ${row.full_name}`}
        value={row.employment_status ?? "WORKING"}
        disabled={save.isPending}
        onChange={(event) =>
          event.target.value === "LEFT" ? setLeaving(true) : save.mutate(event.target.value)
        }
        className="min-h-9 text-xs"
      >
        {EMPLOYMENT_STATUSES.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      {save.isError && !leaving ? (
        <span role="alert" className="text-xs text-[var(--bad)]">
          {save.error instanceof Error ? save.error.message : "Không lưu được."}
        </span>
      ) : null}
      <ConfirmDialog
        open={leaving}
        spec={{
          title: `Đánh dấu ${row.full_name} đã nghỉ việc?`,
          description:
            "Tài khoản này sẽ không đăng nhập web được nữa và bị đăng xuất khỏi mọi thiết bị ngay. Dữ liệu và lịch sử vẫn giữ; đổi lại trạng thái khác thì đăng nhập được lại.",
          confirmLabel: "Đánh dấu đã nghỉ việc",
          variant: "destructive",
        }}
        pending={save.isPending}
        error={save.isError ? save.error : undefined}
        onConfirm={() => save.mutate("LEFT")}
        onCancel={() => {
          setLeaving(false);
          save.reset();
        }}
      />
    </div>
  );
}

export function EmploymentStatusPill({ row }: { row: MemberRow }) {
  const status = row.employment_status ?? "WORKING";
  const tone = status === "LEFT" ? "st-red" : status === "WORKING" ? "st-green" : "st-amber";
  return <span className={`st ${tone}`}>{row.employment_status_label ?? "Đang làm"}</span>;
}


/**
 * "Pháp nhân" (0095): the company that employs the person - picked straight
 * on the roster by the OWNER / an ADMIN (`PATCH .../work-info`; the server
 * refuses anybody else, 403 `work_info_forbidden`).
 */
export function LegalEntityField({
  row,
  entities,
  onSaved,
}: {
  row: MemberRow;
  entities: LegalEntity[];
  onSaved: (code: string | null) => void;
}) {
  const save = useMutation({
    mutationFn: (id: string) => api.setWorkInfo(row.user_id, { legal_entity_id: id || null }),
    onSuccess: (result) =>
      onSaved(entities.find((item) => item.id === result.legal_entity_id)?.code ?? null),
  });
  const current = row.legal_entity_id ?? "";
  // A switched-off entity a person still names stays visible on their row.
  const shown =
    current && !entities.some((item) => item.id === current)
      ? [...entities, { id: current, code: row.legal_entity_code ?? "?", name: "" }]
      : entities;
  return (
    <div className="flex flex-col gap-1">
      <Select
        aria-label={`Pháp nhân của ${row.full_name}`}
        value={current}
        disabled={save.isPending}
        onChange={(event) => save.mutate(event.target.value)}
        className="min-h-9 text-xs"
      >
        <option value="">Chưa chọn</option>
        {shown.map((item) => (
          <option key={item.id} value={item.id}>
            {item.code}
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

/**
 * "Thông tin công việc" (0095): job title, branch, company and Telegram id of
 * one account. A Telegram id links the account to the bot - a change is
 * confirmed first, since the old chat stops receiving the person's notices.
 */
export function WorkInfoDialog({
  open,
  row,
  entities,
  branches,
  onClose,
  onDone,
}: {
  open: boolean;
  row: MemberRow;
  entities: LegalEntity[];
  branches: { code: string; label: string }[];
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const [title, setTitle] = useState(row.job_title ?? "");
  const [branch, setBranch] = useState(row.branch ?? "");
  const [entity, setEntity] = useState(row.legal_entity_id ?? "");
  const [telegram, setTelegram] = useState(
    row.telegram_user_id == null ? "" : String(row.telegram_user_id),
  );
  const [confirming, setConfirming] = useState(false);
  const before = row.telegram_user_id == null ? "" : String(row.telegram_user_id);
  const telegramChanged = telegram.trim() !== before;
  const save = useMutation({
    mutationFn: () => {
      const fields: WorkInfoInput = {
        job_title: title.trim() || null,
        branch: branch || null,
        legal_entity_id: entity || null,
      };
      if (telegramChanged) fields.telegram_user_id = telegram.trim() || null;
      return api.setWorkInfo(row.user_id, fields);
    },
    onSuccess: () => {
      setConfirming(false);
      onDone(`Đã lưu thông tin công việc của ${row.full_name}.`);
    },
  });
  const bad = telegram.trim() !== "" && !/^\d{1,19}$/.test(telegram.trim());
  const submit = () => (telegramChanged ? setConfirming(true) : save.mutate());
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={save.isPending}
      title="Thông tin công việc"
      description={row.full_name}
    >
      <ModalBody className="space-y-4">
        <label className="block text-sm font-medium">
          Chức danh
          <input
            value={title}
            maxLength={150}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Ví dụ: Chuyên viên Telesales"
            className={`${field} mt-1.5`}
            data-autofocus
          />
        </label>
        <label className="block text-sm font-medium">
          Chi nhánh
          <Select
            value={branch}
            onChange={(event) => setBranch(event.target.value)}
            className="mt-1.5"
          >
            <option value="">Chưa chọn</option>
            {branches.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block text-sm font-medium">
          Pháp nhân
          <Select
            value={entity}
            onChange={(event) => setEntity(event.target.value)}
            className="mt-1.5"
          >
            <option value="">Chưa chọn</option>
            {entities.map((item) => (
              <option key={item.id} value={item.id}>
                {item.code} · {item.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="block text-sm font-medium">
          Telegram ID
          <input
            value={telegram}
            inputMode="numeric"
            maxLength={19}
            onChange={(event) => setTelegram(event.target.value)}
            placeholder="Chưa liên kết"
            className={`${field} mt-1.5 tabular-nums`}
          />
          <span className="mt-1 block text-xs font-normal text-[var(--text-muted)]">
            Dãy số của tài khoản Telegram. Có ID thì người này nhắn /start là bot nhận ra và
            gửi được thông báo; để trống là chưa liên kết bot.
          </span>
          {bad ? (
            <span role="alert" className="mt-1 block text-xs text-[var(--bad)]">
              Telegram ID chỉ gồm chữ số.
            </span>
          ) : null}
        </label>
        {save.isError && !confirming ? (
          <p role="alert" className="text-sm text-[var(--bad)]">
            {save.error instanceof Error ? save.error.message : "Không lưu được."}
          </p>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
          Huỷ
        </SecondaryButton>
        <PrimaryButton type="button" onClick={submit} disabled={bad || save.isPending}>
          Lưu
        </PrimaryButton>
      </ModalFooter>
      <ConfirmDialog
        open={confirming}
        spec={telegramLinkConfirmation(row.full_name, telegram.trim(), before)}
        pending={save.isPending}
        error={save.isError ? save.error : undefined}
        onConfirm={() => save.mutate()}
        onCancel={() => {
          setConfirming(false);
          save.reset();
        }}
      />
    </Modal>
  );
}

function telegramLinkConfirmation(name: string, next: string, before: string) {
  if (!next)
    return {
      title: `Bỏ liên kết Telegram của ${name}?`,
      description:
        "Bot sẽ không nhận ra người này nữa và không gửi thông báo qua Telegram. Đăng nhập web bằng mã nhân viên vẫn dùng được.",
      confirmLabel: "Bỏ liên kết",
      variant: "destructive" as const,
    };
  return {
    title: `Liên kết Telegram ${next} cho ${name}?`,
    description: before
      ? `Thay cho Telegram ${before}: tài khoản Telegram cũ không nhận thông báo của người này nữa.`
      : "Người này nhắn /start cho bot là dùng được bot và nhận thông báo qua Telegram.",
    confirmLabel: "Liên kết",
  };
}

/**
 * "Pháp nhân" list (0095): the OWNER / an ADMIN adds, renames and switches
 * entries off (never deletes - the people who name one keep it).
 */
export function LegalEntitiesDialog({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: (text: string) => void;
}) {
  const list = useQuery({
    queryKey: ["legal-entities", "all"],
    queryFn: () => api.legalEntities(true),
    enabled: open,
  });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [taxCode, setTaxCode] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api.createLegalEntity({ code: code.trim(), name: name.trim(), tax_code: taxCode.trim() || null }),
    onSuccess: (made) => {
      setCode("");
      setName("");
      setTaxCode("");
      void list.refetch();
      onChanged(`Đã thêm pháp nhân ${made.code}.`);
    },
  });
  const toggle = useMutation({
    mutationFn: (item: LegalEntity) => api.updateLegalEntity(item.id, { active: !item.active }),
    onSuccess: (item) => {
      void list.refetch();
      onChanged(item.active ? `Đã bật lại pháp nhân ${item.code}.` : `Đã tắt pháp nhân ${item.code}.`);
    },
  });
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={add.isPending}
      size="lg"
      title="Pháp nhân"
      description="Công ty ký hợp đồng / đóng bảo hiểm cho nhân viên. Tắt thay vì xoá: người đang gắn vẫn giữ."
    >
      <ModalBody className="space-y-4">
        {list.isError ? (
          <p role="alert" className="text-sm text-[var(--bad)]">
            Không tải được danh sách pháp nhân.
          </p>
        ) : null}
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {(list.data?.entities ?? []).map((item) => (
            <li key={item.id} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className="w-20 shrink-0 font-mono text-xs font-semibold">{item.code}</span>
              <span className="min-w-0 flex-1">
                <span className={item.active ? "" : "text-[var(--text-muted)] line-through"}>
                  {item.name}
                </span>
                <span className="block text-xs text-[var(--text-muted)]">
                  {item.tax_code ? `MST ${item.tax_code} · ` : ""}
                  {item.people} người
                </span>
              </span>
              <SecondaryButton
                type="button"
                onClick={() => toggle.mutate(item)}
                disabled={toggle.isPending}
              >
                {item.active ? "Tắt" : "Bật lại"}
              </SecondaryButton>
            </li>
          ))}
        </ul>
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold">Thêm pháp nhân</legend>
          <div className="grid gap-2 sm:grid-cols-[8rem_1fr_9rem]">
            <input
              aria-label="Mã pháp nhân"
              placeholder="Mã, vd. APM"
              value={code}
              maxLength={40}
              onChange={(event) => setCode(event.target.value)}
              className={`${field} font-mono uppercase`}
            />
            <input
              aria-label="Tên pháp nhân"
              placeholder="Tên đầy đủ"
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
              className={field}
            />
            <input
              aria-label="Mã số thuế"
              placeholder="MST (nếu có)"
              value={taxCode}
              maxLength={20}
              onChange={(event) => setTaxCode(event.target.value)}
              className={field}
            />
          </div>
          {add.isError || toggle.isError ? (
            <p role="alert" className="text-sm text-[var(--bad)]">
              {(add.error ?? toggle.error) instanceof Error
                ? (add.error ?? toggle.error)?.message
                : "Không lưu được."}
            </p>
          ) : null}
        </fieldset>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Đóng
        </SecondaryButton>
        <PrimaryButton
          type="button"
          onClick={() => add.mutate()}
          disabled={!code.trim() || !name.trim() || add.isPending}
        >
          Thêm
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}
