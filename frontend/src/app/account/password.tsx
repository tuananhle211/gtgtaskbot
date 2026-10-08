"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type AccountMe } from "@/lib/api";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton } from "@/components/pr";

/**
 * Changing the signed-in person's own password.
 *
 * One form, two homes: a modal opened from "Đổi mật khẩu" on the account page,
 * and - while the change is required - the only card on that page (see
 * `ForcedPasswordChange` in page.tsx).
 *
 * No password rules here. The server has only three (not empty, not too long,
 * not the default) and says so in its own words; this form checks only that
 * the fields are filled and the retype matches. The strength meter is a hint,
 * never a gate.
 */

const FIELD =
  "min-h-11 w-full rounded-lg border bg-[var(--surface)] px-3 text-[var(--text)] transition-colors placeholder:text-[var(--text-muted)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/15";

const failureText = (error: unknown) =>
  error instanceof ApiError ? error.message : "Không kết nối được máy chủ. Bạn thử lại nhé.";

/** 0 for empty, else 1 (weak) to 4 (strong). Informational only. */
export function passwordStrength(password: string): 0 | 1 | 2 | 3 | 4 {
  if (!password) return 0;
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((pattern) => pattern.test(password)).length;
  let score = 0;
  if (password.length >= 8) score += 1;
  if (password.length >= 12) score += 1;
  if (kinds >= 2) score += 1;
  if (kinds >= 3) score += 1;
  if (password.length < 6) score = Math.min(score, 1);
  return Math.max(1, score) as 1 | 2 | 3 | 4;
}

const STRENGTH: Record<1 | 2 | 3 | 4, { label: string; bar: string; text: string }> = {
  1: { label: "Yếu", bar: "bg-[var(--bad)]", text: "text-[var(--bad)]" },
  2: { label: "Trung bình", bar: "bg-[var(--warn)]", text: "text-[var(--warn)]" },
  3: { label: "Khá", bar: "bg-[var(--accent)]", text: "text-[var(--accent)]" },
  4: { label: "Mạnh", bar: "bg-[var(--good)]", text: "text-[var(--good)]" },
};

export function PasswordDialog({
  open,
  onClose,
  me,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  me: AccountMe;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={busy}
      title="Đổi mật khẩu"
      description="Sau khi đổi, các phiên đăng nhập khác của bạn sẽ bị đăng xuất. Phiên này vẫn giữ."
    >
      {/* Rendered only while open, so closing forgets whatever was typed. */}
      <PasswordForm
        me={me}
        layout="dialog"
        onCancel={onClose}
        onBusy={setBusy}
        onChanged={() => {
          onChanged();
          onClose();
        }}
      />
    </Modal>
  );
}

export function PasswordForm({
  me,
  layout,
  onCancel,
  onBusy,
  onChanged,
}: {
  me: AccountMe;
  /** `dialog`: body + footer of a `Modal`. `page`: a plain stacked form. */
  layout: "dialog" | "page";
  onCancel?: () => void;
  onBusy?: (busy: boolean) => void;
  onChanged?: () => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const ids = { current: useId(), next: useId(), confirm: useId() };
  const refs = {
    current: useRef<HTMLInputElement | null>(null),
    next: useRef<HTMLInputElement | null>(null),
    confirm: useRef<HTMLInputElement | null>(null),
  };
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [shown, setShown] = useState({ current: false, next: false, confirm: false });
  const [tried, setTried] = useState(false);

  const missingCurrent = current.length === 0;
  const missingNext = next.length === 0;
  const mismatch = confirm !== next;
  const ready = !missingCurrent && !missingNext && !mismatch;

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: async () => {
      const wasRequired = me.must_change_password;
      onChanged?.();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["session"] }),
        queryClient.invalidateQueries({ queryKey: ["account"] }),
      ]);
      if (wasRequired) router.replace("/account");
    },
  });
  useEffect(() => {
    onBusy?.(change.isPending);
  }, [change.isPending, onBusy]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (ready) {
      change.mutate();
      return;
    }
    // Take the person to the first thing to fix.
    (missingCurrent ? refs.current : missingNext ? refs.next : refs.confirm).current?.focus();
  };

  const strength = passwordStrength(next);
  const toggle = (key: keyof typeof shown) => setShown((was) => ({ ...was, [key]: !was[key] }));

  const fields = (
    <div className="space-y-5">
      {/* For password managers: which account this new password belongs to. */}
      <input type="text" name="username" autoComplete="username" value={String(me.telegram_user_id)} readOnly hidden />

      {change.isError ? (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] px-3 py-2.5 text-sm text-[var(--bad)]"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="mt-px shrink-0">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16.5v.01" />
          </svg>
          <span className="font-medium">{failureText(change.error)}</span>
        </div>
      ) : null}

      <PasswordField
        id={ids.current}
        inputRef={refs.current}
        label="Mật khẩu hiện tại"
        noun="mật khẩu hiện tại"
        autoComplete="current-password"
        value={current}
        onChange={setCurrent}
        shown={shown.current}
        onToggle={() => toggle("current")}
        autoFocus
        invalid={tried && missingCurrent}
      >
        {tried && missingCurrent ? <FieldNote tone="bad" alert>Nhập mật khẩu hiện tại.</FieldNote> : null}
      </PasswordField>

      <PasswordField
        id={ids.next}
        inputRef={refs.next}
        label="Mật khẩu mới"
        noun="mật khẩu mới"
        autoComplete="new-password"
        value={next}
        onChange={setNext}
        shown={shown.next}
        onToggle={() => toggle("next")}
        invalid={tried && missingNext}
      >
        {tried && missingNext ? (
          <FieldNote tone="bad" alert>
            Nhập mật khẩu mới.
          </FieldNote>
        ) : strength === 0 ? (
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Không có quy định bắt buộc. Mật khẩu dài, có chữ hoa, số và ký hiệu sẽ khó đoán hơn.
          </p>
        ) : (
          <div className="mt-2.5" aria-live="polite">
            <div className="flex items-center gap-3">
              <div className="grid flex-1 grid-cols-4 gap-1.5" aria-hidden="true">
                {[1, 2, 3, 4].map((step) => (
                  <span
                    key={step}
                    className={`h-1.5 rounded-full transition-colors ${
                      step <= strength ? STRENGTH[strength].bar : "bg-[var(--border)]"
                    }`}
                  />
                ))}
              </div>
              <span className={`w-20 text-right text-xs font-semibold ${STRENGTH[strength].text}`}>
                {STRENGTH[strength].label}
              </span>
            </div>
            <p className="mt-1.5 text-xs text-[var(--text-muted)]">Độ mạnh chỉ để tham khảo, không bắt buộc.</p>
          </div>
        )}
      </PasswordField>

      <PasswordField
        id={ids.confirm}
        inputRef={refs.confirm}
        label="Nhập lại mật khẩu mới"
        noun="mật khẩu nhập lại"
        autoComplete="new-password"
        value={confirm}
        onChange={setConfirm}
        shown={shown.confirm}
        onToggle={() => toggle("confirm")}
        invalid={tried && mismatch}
      >
        {confirm.length === 0 && !tried ? null : mismatch ? (
          <FieldNote tone={tried ? "bad" : "warn"} alert={tried}>
            Nhập lại chưa khớp mật khẩu mới.
          </FieldNote>
        ) : next.length > 0 ? (
          <FieldNote tone="good">Đã khớp mật khẩu mới.</FieldNote>
        ) : null}
      </PasswordField>
    </div>
  );

  const submitLabel = change.isPending ? "Đang đổi…" : "Đổi mật khẩu";

  if (layout === "dialog") {
    return (
      <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <ModalBody>{fields}</ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onCancel} disabled={change.isPending} className="w-full sm:w-auto">
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={change.isPending} className="w-full sm:w-auto">
            {submitLabel}
          </PrimaryButton>
        </ModalFooter>
      </form>
    );
  }
  return (
    <form noValidate onSubmit={submit} className="space-y-6">
      {fields}
      <PrimaryButton type="submit" disabled={change.isPending} className="w-full">
        {submitLabel}
      </PrimaryButton>
    </form>
  );
}

function PasswordField({
  id,
  inputRef,
  label,
  noun,
  autoComplete,
  value,
  onChange,
  shown,
  onToggle,
  autoFocus = false,
  invalid,
  children,
}: {
  id: string;
  inputRef: React.RefObject<HTMLInputElement | null>;
  label: string;
  /** For the eye button: "Hiện <noun>". */
  noun: string;
  autoComplete: string;
  value: string;
  onChange: (value: string) => void;
  shown: boolean;
  onToggle: () => void;
  autoFocus?: boolean;
  invalid: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <div className="relative mt-1.5">
        <input
          ref={inputRef}
          id={id}
          type={shown ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={invalid || undefined}
          data-autofocus={autoFocus ? "" : undefined}
          spellCheck={false}
          className={`${FIELD} pr-12 ${invalid ? "border-[var(--bad)]" : "border-[var(--border)]"}`}
        />
        <button
          type="button"
          onClick={onToggle}
          aria-label={shown ? `Ẩn ${noun}` : `Hiện ${noun}`}
          aria-pressed={shown}
          aria-controls={id}
          title={shown ? "Ẩn" : "Hiện"}
          className="absolute inset-y-1 right-1 inline-flex w-10 items-center justify-center rounded-md text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          <EyeIcon off={shown} />
        </button>
      </div>
      {children}
    </div>
  );
}

function FieldNote({
  tone,
  alert = false,
  children,
}: {
  tone: "good" | "warn" | "bad";
  alert?: boolean;
  children: React.ReactNode;
}) {
  const color = tone === "good" ? "text-[var(--good)]" : tone === "warn" ? "text-[var(--warn)]" : "text-[var(--bad)]";
  return (
    <p role={alert ? "alert" : undefined} className={`mt-2 flex items-center gap-1.5 text-xs font-medium ${color}`}>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
        {tone === "good" ? <path d="M5 12.5l4.5 4.5L19 7.5" /> : <path d="M7 7l10 10M17 7L7 17" />}
      </svg>
      {children}
    </p>
  );
}

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {off ? <path d="M4 4l16 16" /> : null}
    </svg>
  );
}
