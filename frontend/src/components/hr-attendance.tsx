"use client";

/**
 * "Nhân sự" đợt 3-4 (0100): the shared pieces of "Đơn từ", "Phép năm" and
 * "Bảng công" - the request form, the approval strip, the decide buttons, the
 * annual-leave card and the timesheet's symbol cell.
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attendanceApi,
  days,
  describeAbsence,
  statusTone,
  type Absence,
  type AbsenceBody,
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
];

export function AbsenceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const form = useQuery({
    queryKey: ["hr-attendance", "form"],
    queryFn: attendanceApi.form,
    enabled: open,
  });
  const [kind, setKind] = useState<Kind>("LEAVE");
  const [leaveType, setLeaveType] = useState("ANNUAL");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [part, setPart] = useState<AbsenceBody["part"]>("FULL");
  const [at, setAt] = useState("");
  const [reason, setReason] = useState("");
  const [sent, setSent] = useState<Absence | null>(null);

  const reset = () => {
    setKind("LEAVE");
    setStart("");
    setEnd("");
    setPart("FULL");
    setAt("");
    setReason("");
    setSent(null);
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
        at_time: kind === "LEAVE" ? null : at,
        reason: reason.trim(),
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
    (kind === "LEAVE" ? Boolean(leaveType) && (!end || end >= start) : CLOCK.test(at));

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
              <div role="radiogroup" aria-label="Loại đơn" className="mt-1 grid grid-cols-3 gap-2">
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

/** The colour of a day's symbol: worked, leave, holiday, absent, missing. */
export function symbolTone(cell: Pick<DayCell, "symbol" | "missing" | "late" | "early">): string {
  if (cell.missing || cell.symbol === "!") return "bg-[var(--bad-soft)] text-[var(--bad)] font-semibold";
  if (cell.symbol === "V") return "bg-[var(--bad-soft)] text-[var(--bad)]";
  if (cell.symbol === "L") return "bg-[var(--accent-soft)] text-[var(--accent-strong)]";
  if (cell.symbol === "X" || cell.symbol === "X/2") {
    return cell.late || cell.early ? "bg-[var(--warn-soft)] text-[var(--text)]" : "text-[var(--good)]";
  }
  if (cell.symbol === "-" || cell.symbol === "") return "text-[var(--text-muted)]";
  return "bg-[var(--magenta-soft)] text-[var(--magenta)]";
}

export function useMonthLabel(month: string): string {
  return useMemo(() => {
    const [year, value] = month.split("-");
    return `Tháng ${value}/${year}`;
  }, [month]);
}
