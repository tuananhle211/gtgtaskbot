"use client";

import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type BirthdayOrderBody, type Gender, type TaskRow } from "@/lib/api";
import { GENDERS } from "@/lib/birthday";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { NoticeBox } from "@/components/states";
import { DateInput } from "@/components/date-input";

/**
 * "Order sinh nhật": the form that creates one, and edits one already on the
 * board. A modal over the birthday board (`/tasks/birthday`).
 *
 * A birthday order is an ORD order for the Design ban alone: no approval by
 * the Trưởng phòng ADS and no final review - sending the form puts it straight
 * on the Design step, and the designer's hand-in finishes it. So the form asks
 * only who the artwork is for: name, branch, gender, birthday, a picture to
 * work from (a link) and a note.
 *
 * The form's own button is the confirmation (the "parameter-modal" policy of
 * `tests/confirmation.test.tsx`): nothing is sent before it is pressed, and an
 * edit carries the row's `version`, so somebody else's change in between is a
 * 409 shown here instead of being overwritten.
 */

const FIELD =
  "min-h-11 w-full rounded-lg border bg-[var(--surface)] px-3 text-sm text-[var(--text)] placeholder:text-[var(--text-muted)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/15";

type FieldKey = "full_name" | "branch" | "gender" | "birthday_on";

interface Draft {
  full_name: string;
  branch: string;
  gender: Gender | "";
  birthday_on: string;
  /** "Ảnh minh hoạ": one input per link, always at least one (maybe empty). */
  reference_links: string[];
  note: string;
}

/** How many "Ảnh minh hoạ" links one order takes (the server's limit). */
export const MAX_REFERENCE_LINKS = 20;

/** The row's links: the list, else the single link of an older API. */
const linksOf = (row: TaskRow | null): string[] => {
  const links = row?.design?.reference_links?.length
    ? row.design.reference_links
    : row?.design?.reference_link
      ? [row.design.reference_link]
      : [];
  return links.length ? [...links] : [""];
};

const draftOf = (row: TaskRow | null): Draft => ({
  full_name: row?.birthday?.full_name ?? "",
  branch: row?.birthday?.branch ?? "",
  gender:
    row?.birthday?.gender === "MALE" || row?.birthday?.gender === "FEMALE"
      ? row.birthday.gender
      : "",
  birthday_on: row?.birthday?.birthday_on?.slice(0, 10) ?? "",
  reference_links: linksOf(row),
  note: row?.design?.note ?? "",
});

/** What each empty required field says, in the order they are checked. */
const REQUIRED: Array<[FieldKey, string]> = [
  ["full_name", "Nhập họ và tên."],
  ["branch", "Nhập chi nhánh."],
  ["gender", "Chọn giới tính."],
  ["birthday_on", "Chọn ngày sinh."],
];

export function BirthdayOrderDialog({
  open,
  onClose,
  row = null,
}: {
  open: boolean;
  onClose: () => void;
  /** The board row being edited; none = a new order. */
  row?: TaskRow | null;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={busy}
      size="lg"
      title={row ? `Chỉnh sửa order sinh nhật · ${row.code}` : "Tạo order sinh nhật"}
      description={
        row
          ? "Thông tin mới được lưu vào order; bước đang làm và người đang giữ không đổi."
          : "Order được chuyển thẳng tới ban Design, không qua bước duyệt order."
      }
    >
      {/* Rendered only while open, so closing forgets whatever was typed. */}
      <BirthdayOrderForm row={row} onCancel={onClose} onBusy={setBusy} onSaved={onClose} />
    </Modal>
  );
}

function BirthdayOrderForm({
  row,
  onCancel,
  onBusy,
  onSaved,
}: {
  row: TaskRow | null;
  onCancel: () => void;
  onBusy: (busy: boolean) => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const ids = {
    full_name: useId(),
    branch: useId(),
    gender: useId(),
    birthday_on: useId(),
    reference_link: useId(),
    note: useId(),
    branches: useId(),
  };
  const [draft, setDraft] = useState<Draft>(() => draftOf(row));
  const [tried, setTried] = useState(false);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((all) => ({ ...all, [key]: value }));

  // Branches already used, as suggestions; the field stays free text.
  const options = useQuery({
    queryKey: ["orders", "birthday", "options"],
    queryFn: api.birthdayOptions,
  });

  const setLink = (index: number, value: string) =>
    set(
      "reference_links",
      draft.reference_links.map((link, at) => (at === index ? value : link)),
    );
  const removeLink = (index: number) => {
    const rest = draft.reference_links.filter((_, at) => at !== index);
    set("reference_links", rest.length ? rest : [""]);
  };

  const missing = REQUIRED.filter(([key]) => draft[key].trim() === "");
  const missingKeys = new Set(missing.map(([key]) => key));

  const save = useMutation({
    mutationFn: () => {
      const body: BirthdayOrderBody = {
        full_name: draft.full_name.trim(),
        branch: draft.branch.trim(),
        gender: draft.gender as Gender,
        birthday_on: draft.birthday_on,
        reference_links: draft.reference_links
          .map((link) => link.trim())
          .filter(Boolean),
        note: draft.note.trim() || null,
      };
      return row
        ? api.updateBirthdayOrder(row.id, { version: row.version, ...body })
        : api.createBirthdayOrder(body);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["board"] }),
        queryClient.invalidateQueries({ queryKey: ["orders", "birthday", "options"] }),
        queryClient.invalidateQueries({ queryKey: ["task"] }),
      ]);
      onSaved();
    },
    onError: (error) => {
      // The row moved since it was read: fetch it again, so the next press
      // carries the version on screen.
      if (error instanceof ApiError && error.status === 409)
        void queryClient.invalidateQueries({ queryKey: ["board"] });
    },
  });
  useEffect(() => {
    onBusy(save.isPending);
  }, [save.isPending, onBusy]);

  // The field the server refused, when it names one (`details.field`).
  const refused =
    save.error instanceof ApiError && typeof save.error.details?.field === "string"
      ? save.error.details.field
      : null;
  const invalid = (key: FieldKey) => (tried && missingKeys.has(key)) || refused === key;
  const border = (key: FieldKey) =>
    invalid(key) ? "border-[var(--bad)]" : "border-[var(--border)]";
  const note = (key: FieldKey) => {
    const message = tried ? missing.find(([name]) => name === key)?.[1] : undefined;
    return message ? (
      <p role="alert" className="mt-1 text-xs font-medium text-[var(--bad)]">
        {message}
      </p>
    ) : null;
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (missing.length === 0) {
      save.mutate();
      return;
    }
    // Take the person to the first thing to fix.
    document.getElementById(ids[missing[0][0]])?.focus();
  };

  return (
    <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
      <ModalBody>
        <div className="space-y-4">
          {save.isError ? (
            <NoticeBox error={save.error} onDismiss={() => save.reset()} />
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor={ids.full_name} className="block text-sm font-medium">
                Họ và tên
              </label>
              <input
                id={ids.full_name}
                type="text"
                data-autofocus=""
                value={draft.full_name}
                onChange={(event) => set("full_name", event.target.value)}
                aria-invalid={invalid("full_name") || undefined}
                className={`mt-1.5 ${FIELD} ${border("full_name")}`}
              />
              {note("full_name")}
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={ids.branch} className="block text-sm font-medium">
                Chi nhánh
              </label>
              <input
                id={ids.branch}
                type="text"
                list={ids.branches}
                value={draft.branch}
                onChange={(event) => set("branch", event.target.value)}
                aria-invalid={invalid("branch") || undefined}
                className={`mt-1.5 ${FIELD} ${border("branch")}`}
              />
              <datalist id={ids.branches}>
                {(options.data?.branches ?? []).map((branch) => (
                  <option key={branch} value={branch} />
                ))}
              </datalist>
              {note("branch")}
            </div>
            <div>
              <label htmlFor={ids.gender} className="block text-sm font-medium">
                Giới tính
              </label>
              <Select
                id={ids.gender}
                value={draft.gender}
                onChange={(event) => set("gender", event.target.value as Gender | "")}
                aria-invalid={invalid("gender") || undefined}
                className={`mt-1.5 w-full text-sm ${invalid("gender") ? "border-[var(--bad)]" : ""}`}
              >
                <option value="">— chọn —</option>
                {GENDERS.map((gender) => (
                  <option key={gender.value} value={gender.value}>
                    {gender.label}
                  </option>
                ))}
              </Select>
              {note("gender")}
            </div>
            <div>
              <label htmlFor={ids.birthday_on} className="block text-sm font-medium">
                Ngày sinh
              </label>
              <DateInput
                id={ids.birthday_on}
               
                value={draft.birthday_on}
                onChange={(event) => set("birthday_on", event.target.value)}
                aria-invalid={invalid("birthday_on") || undefined}
                className={`mt-1.5 ${FIELD} ${border("birthday_on")}`}
              />
              {note("birthday_on")}
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={ids.reference_link} className="block text-sm font-medium">
                Ảnh minh hoạ
              </label>
              <div className="mt-1.5 space-y-2">
                {draft.reference_links.map((link, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <input
                      id={index === 0 ? ids.reference_link : undefined}
                      aria-label={index === 0 ? undefined : `Ảnh minh hoạ ${index + 1}`}
                      type="text"
                      inputMode="url"
                      placeholder="Gắn link drive vào đây"
                      value={link}
                      onChange={(event) => setLink(index, event.target.value)}
                      className={`${FIELD} border-[var(--border)]`}
                    />
                    {draft.reference_links.length > 1 ? (
                      <button
                        type="button"
                        onClick={() => removeLink(index)}
                        aria-label={`Xoá link ${index + 1}`}
                        className="inline-flex min-h-9 shrink-0 items-center rounded-lg border border-[var(--border)] px-2.5 text-xs hover:bg-[var(--surface-muted)]"
                      >
                        Xoá
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              {draft.reference_links.length < MAX_REFERENCE_LINKS ? (
                <button
                  type="button"
                  onClick={() => set("reference_links", [...draft.reference_links, ""])}
                  className="mt-2 inline-flex min-h-9 items-center rounded-lg border border-dashed border-[var(--border)] px-2.5 text-xs hover:bg-[var(--surface-muted)]"
                >
                  + Thêm link
                </button>
              ) : (
                <p className="mt-2 text-xs text-[var(--text-muted)]">
                  Tối đa {MAX_REFERENCE_LINKS} link.
                </p>
              )}
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={ids.note} className="block text-sm font-medium">
                Note yêu cầu
              </label>
              <textarea
                id={ids.note}
                value={draft.note}
                onChange={(event) => set("note", event.target.value)}
                className={`mt-1.5 ${FIELD} min-h-24 border-[var(--border)] py-2`}
              />
            </div>
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton
          type="button"
          onClick={onCancel}
          disabled={save.isPending}
          className="w-full sm:w-auto"
        >
          Thôi
        </SecondaryButton>
        <PrimaryButton type="submit" disabled={save.isPending} className="w-full sm:w-auto">
          {save.isPending ? "Đang lưu…" : row ? "Chỉnh sửa" : "Tạo order"}
        </PrimaryButton>
      </ModalFooter>
    </form>
  );
}
