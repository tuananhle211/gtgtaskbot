"use client";

/**
 * "Nhân sự" đợt 3-4 (0100): the shared pieces of "Đơn từ", "Phép năm" and
 * "Bảng công" - the request form, the approval strip, the decide buttons, the
 * annual-leave card and the timesheet's symbol cell.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  absenceFileUrl,
  attendanceApi,
  days,
  dropAbsenceFile,
  uploadAbsenceFile,
  describeAbsence,
  statusTone,
  type Absence,
  type AbsenceBody,
  type AbsenceFile,
  type AnnualBalance,
  type DayCell,
} from "@/lib/hr-attendance";
import { formatDateTime } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";
const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;

function invalidate(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
  void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
}

// --- annual leave --------------------------------------------------------------------------

export function BalanceCard({ balance, title = "Phép năm của tôi" }: { balance: AnnualBalance; title?: string }) {
  const items: { label: string; value: string; hint?: string }[] = [
    {
      label: "Được hưởng cả năm",
      value: days(balance.entitlement),
      hint: balance.seniority_days ? `gồm +${balance.seniority_days} ngày thâm niên` : "12 ngày theo luật",
    },
    { label: "Đã tích luỹ đến nay", value: days(balance.earned), hint: `${balance.months} tháng chính thức` },
    { label: "Đã nghỉ", value: days(balance.used), hint: balance.pending ? `đang chờ duyệt ${days(balance.pending)}` : undefined },
    {
      label: "Còn lại",
      value: days(balance.remaining),
      hint: balance.adjustment ? `điều chỉnh ${balance.adjustment > 0 ? "+" : ""}${days(balance.adjustment)}` : undefined,
    },
  ];
  return (
    <section className="panel p-4" data-testid="balance-card">
      <h2 className="text-base font-semibold">
        {title} {balance.year}
      </h2>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className="rounded-lg bg-[var(--surface-muted)] p-3">
            <dt className="text-xs text-[var(--text-muted)]">{item.label}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">{item.value}</dd>
            {item.hint ? <dd className="text-xs text-[var(--text-muted)]">{item.hint}</dd> : null}
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        Phép năm cộng dần mỗi tháng từ ngày chính thức (thử việc không có phép), không cộng dồn sang năm sau.
      </p>
    </section>
  );
}

// --- the request form ----------------------------------------------------------------------

type Kind = AbsenceBody["kind"];
const KINDS: { code: Kind; label: string; hint: string }[] = [
  { code: "LEAVE", label: "Xin nghỉ", hint: "Cả ngày, nửa ngày hoặc nhiều ngày" },
  { code: "LATE", label: "Đi muộn", hint: "Đến muộn hơn giờ vào ca" },
  { code: "EARLY", label: "Về sớm", hint: "Về trước giờ hết ca" },
  { code: "EXPLAIN", label: "Giải trình", hint: "Quên chấm vào / ra, kèm ảnh xác minh" },
];

const MAX_PICTURES = 5;

/** 0101: the pictures proving a "Giải trình chấm công" - uploaded as picked. */
function EvidencePicker({
  files,
  onChange,
  onBusy,
}: {
  files: AbsenceFile[];
  onChange: (files: AbsenceFile[]) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);
  const add = async (picked: FileList | null) => {
    if (!picked?.length) return;
    setError(null);
    const room = MAX_PICTURES - files.length;
    const chosen = Array.from(picked).slice(0, Math.max(0, room));
    if (picked.length > room) setError(`Tối đa ${MAX_PICTURES} ảnh mỗi đơn.`);
    setUploading(chosen.length);
    onBusy(true);
    const done: AbsenceFile[] = [];
    for (const file of chosen) {
      try {
        done.push(await uploadAbsenceFile(file));
      } catch (problem) {
        setError(problem instanceof Error ? problem.message : "Không tải ảnh lên được.");
      }
    }
    setUploading(0);
    onBusy(false);
    onChange([...files, ...done]);
  };
  const remove = (item: AbsenceFile) => {
    onChange(files.filter((file) => file.id !== item.id));
    void dropAbsenceFile(item.id).catch(() => undefined);
  };
  return (
    <fieldset className="space-y-2" data-testid="evidence-picker">
      <legend className="text-xs font-medium text-[var(--text-muted)]">
        Ảnh xác minh (bắt buộc, tối đa {MAX_PICTURES}): ảnh lịch sử duyệt web, tin nhắn, email công việc lúc đó…
      </legend>
      <div className="flex flex-wrap gap-2">
        {files.map((item) => (
          <figure key={item.id} className="relative h-20 w-20 overflow-hidden rounded-lg border border-[var(--border)]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={absenceFileUrl(item.id)} alt={item.name} className="h-full w-full object-cover" />
            <button
              type="button"
              onClick={() => remove(item)}
              aria-label={`Bỏ ảnh ${item.name}`}
              className="absolute right-1 top-1 h-6 w-6 rounded-full bg-[var(--surface)] text-xs shadow"
            >
              ×
            </button>
          </figure>
        ))}
        {files.length < MAX_PICTURES ? (
          <label className="flex h-20 w-20 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[var(--border)] text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)]">
            {uploading ? "Đang tải…" : "+ Thêm ảnh"}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              className="sr-only"
              aria-label="Chọn ảnh xác minh"
              onChange={(event) => {
                void add(event.target.files);
                event.target.value = "";
              }}
            />
          </label>
        ) : null}
      </div>
      {error ? <p className="text-xs text-[var(--bad)]">{error}</p> : null}
    </fieldset>
  );
}

export function AbsenceDialog({
  open,
  onClose,
  initialKind = "LEAVE",
  initialDay = "",
}: {
  open: boolean;
  onClose: () => void;
  /** Opened from a "!" day of the timesheet: "Giải trình" that day. */
  initialKind?: Kind;
  initialDay?: string;
}) {
  const queryClient = useQueryClient();
  const form = useQuery({
    queryKey: ["hr-attendance", "form"],
    queryFn: attendanceApi.form,
    enabled: open,
  });
  const [kind, setKind] = useState<Kind>(initialKind);
  const [leaveType, setLeaveType] = useState("ANNUAL");
  const [start, setStart] = useState(initialDay);
  const [end, setEnd] = useState("");
  const [part, setPart] = useState<AbsenceBody["part"]>("FULL");
  const [at, setAt] = useState("");
  const [reason, setReason] = useState("");
  const [sent, setSent] = useState<Absence | null>(null);
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [files, setFiles] = useState<AbsenceFile[]>([]);
  const [uploading, setUploading] = useState(false);

  const reset = () => {
    setKind("LEAVE");
    setStart("");
    setEnd("");
    setPart("FULL");
    setAt("");
    setReason("");
    setSent(null);
    setCheckIn("");
    setCheckOut("");
    setFiles([]);
  };
  const close = () => {
    reset();
    onClose();
  };

  const submit = useMutation({
    mutationFn: () =>
      attendanceApi.submit({
        kind,
        leave_type: kind === "LEAVE" ? leaveType : null,
        start_on: start,
        end_on: kind === "LEAVE" ? end || start : null,
        part: kind === "LEAVE" && (!end || end === start) ? part : "FULL",
        at_time: kind === "LATE" || kind === "EARLY" ? at : null,
        reason: reason.trim(),
        ...(kind === "EXPLAIN"
          ? {
              check_in: checkIn || null,
              check_out: checkOut || null,
              file_ids: files.map((item) => item.id),
            }
          : {}),
      }),
    onSuccess: (row) => {
      setSent(row);
      invalidate(queryClient);
    },
  });

  const single = !end || end === start;
  const chain = form.data?.chain[kind] ?? [];
  const chosen = form.data?.leave_types.find((item) => item.code === leaveType);
  const ready =
    Boolean(start) &&
    Boolean(reason.trim()) &&
    (kind === "LEAVE"
      ? Boolean(leaveType) && (!end || end >= start)
      : kind === "EXPLAIN"
        ? (Boolean(checkIn) || Boolean(checkOut)) &&
          (!checkIn || CLOCK.test(checkIn)) &&
          (!checkOut || CLOCK.test(checkOut)) &&
          files.length > 0 &&
          !uploading
        : CLOCK.test(at));

  return (
    <Modal open={open} onClose={close} title={sent ? "Đã gửi đơn" : "Tạo đơn"} size="lg" busy={submit.isPending}>
      {sent ? (
        <>
          <ModalBody>
            <p className="text-sm">
              Đơn <strong>{describeAbsence(sent)}</strong> đã gửi.{" "}
              {sent.status === "APPROVED"
                ? "Đơn không cần ai duyệt nên đã được duyệt."
                : `Đang chờ ${sent.current_label}: ${sent.waiting_on.map((p) => p.name).join(", ") || "—"}.`}
            </p>
          </ModalBody>
          <ModalFooter>
            <Link href={`/hr/leave/${sent.id}`} className="text-sm font-medium text-[var(--accent)] hover:underline">
              Xem đơn
            </Link>
            <PrimaryButton type="button" onClick={close}>
              Xong
            </PrimaryButton>
          </ModalFooter>
        </>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) submit.mutate();
          }}
        >
          <ModalBody className="space-y-4">
            {form.isPending ? <Loading label="Đang tải…" /> : null}
            {form.isError ? <ErrorBox error={form.error} onRetry={() => form.refetch()} /> : null}
            <fieldset>
              <legend className="text-xs font-medium text-[var(--text-muted)]">Loại đơn</legend>
              <div role="radiogroup" aria-label="Loại đơn" className="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {KINDS.map((item) => (
                  <button
                    key={item.code}
                    type="button"
                    role="radio"
                    aria-checked={kind === item.code}
                    onClick={() => setKind(item.code)}
                    className={`min-h-11 rounded-lg border px-3 py-2 text-left text-sm ${
                      kind === item.code
                        ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                        : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
                    }`}
                  >
                    {item.label}
                    <span className="block text-xs font-normal text-[var(--text-muted)]">{item.hint}</span>
                  </button>
                ))}
              </div>
            </fieldset>
            {kind === "LEAVE" ? (
              <>
                <label className="block text-xs font-medium text-[var(--text-muted)]">
                  Loại nghỉ
                  <Select
                    value={leaveType}
                    onChange={(event) => setLeaveType(event.target.value)}
                    className="mt-1 w-full"
                    aria-label="Loại nghỉ"
                  >
                    {(form.data?.leave_types ?? []).map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.name}
                        {item.paid ? "" : " (không lương)"}
                      </option>
                    ))}
                  </Select>
                </label>
                {chosen?.annual && form.data ? (
                  <p className="text-xs text-[var(--text-muted)]">
                    Phép năm còn lại: <strong className="text-[var(--text)]">{days(form.data.balance.remaining)}</strong> ngày
                    (đã tích luỹ {days(form.data.balance.earned)}, đã nghỉ {days(form.data.balance.used)}).
                  </p>
                ) : null}
                {chosen?.max_days ? (
                  <p className="text-xs text-[var(--text-muted)]">Thường tối đa {chosen.max_days} ngày.</p>
                ) : null}
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block text-xs font-medium text-[var(--text-muted)]">
                    Từ ngày
                    <DateInput value={start} onChange={(event) => setStart(event.target.value)} className="mt-1" aria-label="Từ ngày" />
                  </label>
                  <label className="block text-xs font-medium text-[var(--text-muted)]">
                    Đến ngày (bỏ trống nếu nghỉ 1 ngày)
                    <DateInput value={end} onChange={(event) => setEnd(event.target.value)} className="mt-1" aria-label="Đến ngày" />
                  </label>
                </div>
                {single ? (
                  <div role="radiogroup" aria-label="Buổi nghỉ" className="flex flex-wrap gap-2">
                    {(
                      [
                        ["FULL", "Cả ngày"],
                        ["MORNING", "Buổi sáng"],
                        ["AFTERNOON", "Buổi chiều"],
                      ] as const
                    ).map(([code, label]) => (
                      <button
                        key={code}
                        type="button"
                        role="radio"
                        aria-checked={part === code}
                        onClick={() => setPart(code)}
                        className={`min-h-11 rounded-full border px-4 text-sm ${
                          part === code
                            ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                            : "border-[var(--border)] bg-[var(--surface)]"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            ) : kind === "EXPLAIN" ? (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="block text-xs font-medium text-[var(--text-muted)]">
                    Ngày quên chấm
                    <DateInput value={start} onChange={(event) => setStart(event.target.value)} className="mt-1" aria-label="Ngày quên chấm" />
                  </label>
                  <label className="block text-xs font-medium text-[var(--text-muted)]">
                    Giờ vào thực tế
                    <input
                      value={checkIn}
                      onChange={(event) => setCheckIn(event.target.value)}
                      placeholder="08:00"
                      inputMode="numeric"
                      className={`${FIELD} mt-1`}
                      aria-invalid={Boolean(checkIn) && !CLOCK.test(checkIn)}
                    />
                  </label>
                  <label className="block text-xs font-medium text-[var(--text-muted)]">
                    Giờ ra thực tế
                    <input
                      value={checkOut}
                      onChange={(event) => setCheckOut(event.target.value)}
                      placeholder="17:30"
                      inputMode="numeric"
                      className={`${FIELD} mt-1`}
                      aria-invalid={Boolean(checkOut) && !CLOCK.test(checkOut)}
                    />
                  </label>
                </div>
                <p className="text-xs text-[var(--text-muted)]">
                  Nhập giờ bạn quên chấm (vào, ra hoặc cả hai). Duyệt xong, bảng công tính theo giờ này.
                </p>
                <EvidencePicker files={files} onChange={setFiles} onBusy={setUploading} />
              </>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-xs font-medium text-[var(--text-muted)]">
                  Ngày
                  <DateInput value={start} onChange={(event) => setStart(event.target.value)} className="mt-1" aria-label="Ngày" />
                </label>
                <label className="block text-xs font-medium text-[var(--text-muted)]">
                  {kind === "LATE" ? "Giờ đến (HH:mm)" : "Giờ về (HH:mm)"}
                  <input
                    value={at}
                    onChange={(event) => setAt(event.target.value)}
                    placeholder={kind === "LATE" ? "09:00" : "16:30"}
                    inputMode="numeric"
                    className={`${FIELD} mt-1`}
                    aria-invalid={Boolean(at) && !CLOCK.test(at)}
                  />
                </label>
              </div>
            )}
            <label className="block text-xs font-medium text-[var(--text-muted)]">
              Lý do
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                maxLength={1000}
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-sm text-[var(--text)]"
              />
            </label>
            {form.data ? (
              <div className="rounded-lg bg-[var(--surface-muted)] p-3 text-xs" data-testid="chain-preview">
                <p className="font-medium text-[var(--text)]">Người duyệt lần lượt</p>
                <ol className="mt-1 space-y-1">
                  {chain.map((step, index) => (
                    <li key={step.level}>
                      {index + 1}. {step.label}:{" "}
                      <span className="text-[var(--text-muted)]">
                        {step.people.length ? step.people.map((p) => p.name).join(", ") : "không có người, bỏ qua"}
                      </span>
                    </li>
                  ))}
                </ol>
                <p className="mt-1 text-[var(--text-muted)]">
                  Ca của bạn: {form.data.shift.name}, cho phép trễ {form.data.shift.grace_minutes} phút.
                </p>
              </div>
            ) : null}
            {submit.isError ? <NoticeBox error={submit.error} /> : null}
          </ModalBody>
          <ModalFooter>
            <SecondaryButton type="button" onClick={close}>
              Huỷ
            </SecondaryButton>
            <PrimaryButton type="submit" disabled={!ready || submit.isPending}>
              {submit.isPending ? "Đang gửi…" : "Gửi đơn"}
            </PrimaryButton>
          </ModalFooter>
        </form>
      )}
    </Modal>
  );
}

/** 0101: the pictures of a "Giải trình chấm công", each opening full size. */
export function EvidenceGallery({ files }: { files: AbsenceFile[] }) {
  if (!files.length) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Ảnh xác minh">
      {files.map((item) => (
        <li key={item.id}>
          <a
            href={absenceFileUrl(item.id)}
            target="_blank"
            rel="noreferrer"
            className="block h-28 w-28 overflow-hidden rounded-lg border border-[var(--border)] hover:opacity-90"
            title={item.name}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={absenceFileUrl(item.id)} alt={item.name} className="h-full w-full object-cover" />
          </a>
        </li>
      ))}
    </ul>
  );
}

// --- the chain -----------------------------------------------------------------------------

const STEP_TONE: Record<string, string> = {
  APPROVED: "border-[var(--good)] bg-[var(--good-soft)]",
  REJECTED: "border-[var(--bad)] bg-[var(--bad-soft)]",
  PENDING: "border-[var(--warn)] bg-[var(--warn-soft)]",
};

export function StepStrip({ item }: { item: Absence }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="Các bước duyệt">
      {item.steps.map((step) => (
        <li
          key={step.id}
          className={`rounded-xl border p-3 text-sm ${STEP_TONE[step.status] ?? "border-[var(--border)] bg-[var(--surface)]"} ${
            step.status === "SKIPPED" || step.status === "CLOSED" ? "opacity-60" : ""
          }`}
        >
          <p className="text-xs font-medium text-[var(--text-muted)]">
            Bước {step.position + 1} · {step.level_label}
          </p>
          <p className="mt-1 font-semibold">{step.status_label}</p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {step.decided_by
              ? `${step.decided_by.name}${step.decided_at ? ` · ${formatDateTime(step.decided_at)}` : ""}`
              : step.approvers.length
                ? step.approvers.map((p) => p.name).join(", ")
                : "Không có người"}
          </p>
          {step.note ? <p className="mt-1 text-xs">“{step.note}”</p> : null}
        </li>
      ))}
    </ol>
  );
}

export function StatusCell({ item }: { item: Absence }) {
  return (
    <div>
      <Pill tone={statusTone(item.status)}>{item.status_label}</Pill>
      {item.status === "PENDING" && item.current_label ? (
        <span className="mt-1 block text-xs text-[var(--text-muted)]">
          Chờ {item.current_label}
          {item.waiting_on.length ? `: ${item.waiting_on.map((p) => p.name).join(", ")}` : ""}
        </span>
      ) : null}
    </div>
  );
}

export function DecideButtons({ item, compact = false }: { item: Absence; compact?: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const run = useMutation({
    mutationFn: (approve: boolean) => attendanceApi.decide(item.id, approve, item.current_step_id, note || undefined),
    onSuccess: () => invalidate(queryClient),
  });
  if (!item.can_decide) return null;
  const who = item.user?.name ?? "nhân viên";
  return (
    <div className={`flex flex-wrap gap-2 ${compact ? "" : "mt-2"}`}>
      <ConfirmButton
        spec={{
          title: `Duyệt đơn của ${who}?`,
          description: `${describeAbsence(item)} - bước ${item.current_label}.`,
          confirmLabel: "Duyệt",
          details: (
            <label className="block text-xs text-[var(--text-muted)]">
              Ghi chú (không bắt buộc)
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={2}
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
              />
            </label>
          ),
        }}
        onConfirm={() => run.mutate(true)}
        pending={run.isPending}
        error={run.error}
        tone="primary"
      >
        Duyệt
      </ConfirmButton>
      <ConfirmButton
        spec={{
          title: `Không duyệt đơn của ${who}?`,
          description: "Đơn dừng lại ở bước này; người gửi thấy lý do.",
          confirmLabel: "Không duyệt",
          variant: "destructive",
          details: (
            <label className="block text-xs text-[var(--text-muted)]">
              Lý do (bắt buộc)
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={2}
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
              />
            </label>
          ),
        }}
        confirmDisabled={!note.trim()}
        onConfirm={() => run.mutate(false)}
        pending={run.isPending}
        error={run.error}
        tone="danger"
      >
        Không duyệt
      </ConfirmButton>
    </div>
  );
}

export function CancelButton({ item, own }: { item: Absence; own: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const run = useMutation({
    mutationFn: () => attendanceApi.cancel(item.id, note || undefined),
    onSuccess: () => invalidate(queryClient),
  });
  if (!item.can_cancel) return null;
  const needsNote = !own;
  return (
    <ConfirmButton
      spec={{
        title: "Huỷ đơn này?",
        description: `${describeAbsence(item)}. ${item.status === "APPROVED" ? "Đơn đã duyệt sẽ không còn tính vào bảng công." : ""}`,
        confirmLabel: "Huỷ đơn",
        variant: "destructive",
        details: needsNote ? (
          <label className="block text-xs text-[var(--text-muted)]">
            Lý do (bắt buộc)
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={2}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
            />
          </label>
        ) : undefined,
      }}
      confirmDisabled={needsNote && !note.trim()}
      onConfirm={() => run.mutate()}
      pending={run.isPending}
      error={run.error}
      tone="secondary"
    >
      Huỷ đơn
    </ConfirmButton>
  );
}

// --- the timesheet's cells -----------------------------------------------------------------

/** A soft cell tint behind a day's mark: bad days red, holidays blue. */
export function symbolTone(cell: Pick<DayCell, "symbol" | "missing" | "late" | "early">): string {
  if (cell.missing || cell.symbol === "!" || cell.symbol === "V" || cell.symbol.endsWith("/V")) {
    return "bg-[var(--bad-soft)]";
  }
  if (cell.symbol === "L") return "bg-[var(--accent-soft)]";
  // Late / early: the amber clock says it; a tint would cover most of the sheet.
  return "";
}

type MarkKind = "work" | "half" | "late" | "absent" | "missing" | "holiday" | "off" | "leave" | "none";

function markKind(cell: Pick<DayCell, "symbol" | "missing" | "late" | "early">): MarkKind {
  const symbol = cell.symbol;
  if (cell.missing || symbol === "!") return "missing";
  if (symbol === "") return "none";
  if (symbol === "-") return "off";
  if (symbol === "L") return "holiday";
  if (symbol === "V") return "absent";
  if (symbol === "X" || symbol === "X/2") {
    if (cell.late || cell.early) return "late";
    return symbol === "X" ? "work" : "half";
  }
  return "leave";
}

const MARK_COLOR: Record<MarkKind, string> = {
  work: "text-[var(--good)]",
  half: "text-[var(--good)]",
  late: "text-[var(--warn)]",
  absent: "text-[var(--bad)]",
  missing: "text-[var(--bad)]",
  holiday: "text-[var(--accent)]",
  off: "text-[var(--text-muted)]",
  leave: "text-[var(--magenta)]",
  none: "text-[var(--text-muted)]",
};

/** The picture of each kind of day: 20-grid strokes, `currentColor`. */
function MarkIcon({ kind }: { kind: MarkKind }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (kind) {
    case "work":
      return (
        <svg {...common}>
          <circle cx="10" cy="10" r="7.5" fill="currentColor" fillOpacity="0.15" />
          <path d="m6.5 10.2 2.3 2.3 4.7-4.9" />
        </svg>
      );
    case "half":
      return (
        <svg {...common}>
          <circle cx="10" cy="10" r="7.5" />
          <path d="M10 2.5a7.5 7.5 0 0 1 0 15Z" fill="currentColor" stroke="none" />
        </svg>
      );
    case "late":
      return (
        <svg {...common}>
          <circle cx="10" cy="10" r="7.5" fill="currentColor" fillOpacity="0.15" />
          <path d="M10 6v4.2l2.8 1.8" />
        </svg>
      );
    case "absent":
      return (
        <svg {...common}>
          <circle cx="10" cy="10" r="7.5" fill="currentColor" fillOpacity="0.15" />
          <path d="m7.3 7.3 5.4 5.4m0-5.4-5.4 5.4" />
        </svg>
      );
    case "missing":
      return (
        <svg {...common}>
          <path d="M10 2.8 18 16.5H2Z" fill="currentColor" fillOpacity="0.15" />
          <path d="M10 8v3.8m0 2.4v.1" />
        </svg>
      );
    case "holiday":
      return (
        <svg {...common}>
          <path d="m10 2.8 2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8l5-.7Z" fill="currentColor" fillOpacity="0.2" />
        </svg>
      );
    case "off":
      return (
        <svg {...common}>
          <path d="M7 10h6" />
        </svg>
      );
    default:
      return null;
  }
}

/**
 * A day of the timesheet as a coloured mark: ✓ đi làm (green), half circle
 * nửa ngày, clock đi muộn / về sớm (amber), ✕ vắng (red), ⚠ thiếu giờ (red),
 * ★ lễ (blue), – không có ca; a leave keeps its letter (P, KL, Ô…) in a
 * magenta pill. The letter stays for screen readers and the Excel file.
 */
export function DayMark({
  cell,
  label,
}: {
  cell: Pick<DayCell, "symbol" | "missing" | "late" | "early">;
  label?: string;
}) {
  const kind = markKind(cell);
  if (kind === "none") return <span className="sr-only">{label ?? ""}</span>;
  const text =
    label ?? (kind === "late" ? "Đi làm, có đi muộn / về sớm" : (SYMBOL_TEXT[cell.symbol] ?? cell.symbol));
  if (kind === "leave") {
    const letter = cell.symbol.split("/")[0];
    return (
      <span
        className="inline-flex min-w-6 items-center justify-center rounded-full bg-[var(--magenta-soft)] px-1.5 py-0.5 text-[10px] font-semibold leading-none text-[var(--magenta)]"
        aria-label={text}
      >
        {letter}
        {cell.symbol.includes("/") ? <span className="ml-0.5 opacity-70">½</span> : null}
        <span className="sr-only">{cell.symbol}</span>
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center justify-center ${MARK_COLOR[kind]}`} aria-label={text}>
      <MarkIcon kind={kind} />
      <span className="sr-only">{cell.symbol}</span>
    </span>
  );
}

const SYMBOL_TEXT: Record<string, string> = {
  X: "Đi làm đủ",
  "X/2": "Đi làm nửa ngày",
  L: "Nghỉ lễ",
  V: "Vắng không phép",
  "!": "Thiếu giờ vào / ra",
  "-": "Không có ca",
};

/** One line of the legend: the mark and its meaning. */
export function MarkLegend({ symbols }: { symbols: { symbol: string; label: string }[] }) {
  const extra = [{ symbol: "X", label: "Đi muộn / về sớm", late: 1 }];
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--text-muted)]" data-testid="mark-legend">
      {symbols
        .filter((item) => item.symbol !== "")
        .map((item) => (
          <span key={item.symbol} className="inline-flex items-center gap-1">
            <DayMark cell={{ symbol: item.symbol, missing: false, late: 0, early: 0 }} label={item.label} />
            {item.label}
          </span>
        ))}
      {extra.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1">
          <DayMark cell={{ symbol: item.symbol, missing: false, late: item.late, early: 0 }} label={item.label} />
          {item.label}
        </span>
      ))}
    </p>
  );
}

/** The lines a day's hover card shows: when, the punches, late / early, notes. */
export function dayTipLines(cell: DayCell & { weekday?: string; leave_names?: string[] }, weekday?: string): string[] {
  const date = `${cell.day.slice(8, 10)}/${cell.day.slice(5, 7)}/${cell.day.slice(0, 4)}`;
  const lines = [`${weekday ?? cell.weekday ?? ""} ${date}`.trim()];
  if (cell.in || cell.out) {
    lines.push(`Check in: ${cell.in ?? "chưa chấm"}`);
    lines.push(`Check out: ${cell.out ?? "chưa chấm"}`);
  } else {
    lines.push("Không có giờ chấm công");
  }
  if (cell.late) lines.push(`Đi muộn ${cell.late} phút`);
  if (cell.early) lines.push(`Về sớm ${cell.early} phút`);
  if (cell.missing) lines.push("Thiếu giờ vào / ra");
  if (cell.leave_names?.length) lines.push(cell.leave_names.join(", "));
  if (cell.explained) lines.push("Giải trình đã duyệt");
  if (cell.edited) lines.push("HCNS đã sửa tay");
  return lines;
}

type Tip = { lines: string[]; x: number; top: number; bottom: number };

/**
 * One hover card for a whole grid: cells carry `data-tip` (lines joined by
 * "\n"); the card is portalled with `position: fixed`, so a scrolling table
 * never clips it, and sits above the cell (below when there is no room).
 */
export function useDayTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = useCallback((event: React.SyntheticEvent) => {
    const cell = (event.target as HTMLElement).closest<HTMLElement>("[data-tip]");
    if (!cell) return setTip(null);
    const rect = cell.getBoundingClientRect();
    setTip({
      lines: (cell.dataset.tip ?? "").split("\n"),
      x: rect.left + rect.width / 2,
      top: rect.top,
      bottom: rect.bottom,
    });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  useEffect(() => {
    if (!tip) return;
    window.addEventListener("scroll", hide, true);
    return () => window.removeEventListener("scroll", hide, true);
  }, [tip, hide]);
  const above = tip ? tip.top > 140 : true;
  const card =
    tip && typeof document !== "undefined"
      ? createPortal(
          <div
            role="tooltip"
            data-testid="day-tip"
            className="pointer-events-none fixed z-[130] min-w-40 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs text-[var(--text)] shadow-[var(--shadow-pop)]"
            style={{
              left: Math.min(Math.max(tip.x, 90), window.innerWidth - 90),
              top: above ? tip.top - 8 : tip.bottom + 8,
              transform: `translate(-50%, ${above ? "-100%" : "0"})`,
            }}
          >
            <p className="font-semibold">{tip.lines[0]}</p>
            {tip.lines.slice(1).map((line) => (
              <p key={line} className={line.startsWith("Check") ? "tabular-nums" : "text-[var(--text-muted)]"}>
                {line}
              </p>
            ))}
          </div>,
          document.body,
        )
      : null;
  return { bind: { onMouseOver: show, onMouseLeave: hide, onFocus: show, onBlur: hide }, card };
}

/** "Bảng công của tôi" as a month calendar (Monday first), each day with its hover card. */
export function MonthCalendar({ days: cells }: { days: (DayCell & { weekday: string; leave_names?: string[] })[] }) {
  const { bind, card } = useDayTip();
  if (!cells.length) return null;
  const first = new Date(`${cells[0].day}T00:00:00`);
  const lead = (first.getDay() + 6) % 7;
  return (
    <section className="panel p-4" data-testid="month-calendar">
      <div className="grid grid-cols-7 gap-1 text-center text-xs" {...bind}>
        {["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map((name) => (
          <span key={name} className="py-1 font-medium text-[var(--text-muted)]">
            {name}
          </span>
        ))}
        {Array.from({ length: lead }, (_, index) => (
          <span key={`lead-${index}`} />
        ))}
        {cells.map((cell) => (
          <div
            key={cell.day}
            tabIndex={0}
            data-tip={dayTipLines(cell).join("\n")}
            aria-label={dayTipLines(cell).join(", ")}
            className={`flex min-h-14 flex-col items-center justify-center rounded-lg border border-[var(--border)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${symbolTone(cell)}`}
          >
            <span className="text-[11px] text-[var(--text-muted)]">{Number(cell.day.slice(8))}</span>
            <DayMark cell={cell} />
            {cell.in || cell.out ? (
              <span className="hidden text-[10px] tabular-nums text-[var(--text-muted)] sm:block">
                {cell.in ?? "--"}–{cell.out ?? "--"}
              </span>
            ) : null}
          </div>
        ))}
      </div>
      {card}
    </section>
  );
}

export function useMonthLabel(month: string): string {
  return useMemo(() => {
    const [year, value] = month.split("-");
    return `Tháng ${value}/${year}`;
  }, [month]);
}
