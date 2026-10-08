"use client";

import Link from "next/link";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/labels";

/**
 * Loading, error and empty states, in one place.
 *
 * Every screen uses these three rather than inventing its own, so that a person
 * who has learned what "Chưa có gì ở đây" looks like recognises it everywhere.
 *
 * The error component renders the server's message, and writes its own only
 * where the server cannot: the sign-in case, and the PR failures whose message
 * is structural English because the service raising it is shared with the
 * Telegram bot. Those go through one table - `errorMessage` in `lib/labels` -
 * keyed on the stable error code, so there is one Vietnamese wording per failure
 * rather than one per screen.
 */

export function Loading({ label = "Đang tải…" }: { label?: string }) {
  return (
    <div className="animate-pulse rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--text-muted)]">
      {label}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--text-muted)]">
      {message}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;

  if (api?.isUnauthenticated) {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm sm:p-6">
        <p className="font-medium">Bạn cần đăng nhập lại.</p>
        {/*
          The instruction names the *command*, not the bot. Telling somebody to
          message "TasksBot on Telegram" makes this sentence wrong the moment the
          bot's display name differs from the product's - and the person reading
          it is already locked out, so a wrong name here costs them the one route
          back in. `/web` is the part that does not drift.
        */}
        <p className="mt-1 text-[var(--text-muted)]">
          Gửi lệnh <code className="rounded bg-[var(--surface-muted)] px-1">/web</code> trong bot
          Telegram để đăng nhập TasksBot. Liên kết chỉ dùng được một lần.
        </p>
        <p className="mt-1 text-[var(--text-muted)]">
          Muốn phiên đăng nhập nằm trong Chrome/Safari thì sao chép liên kết rồi mở trực tiếp bằng
          trình duyệt đó.
        </p>
        <p className="mt-2">
          Hoặc{" "}
          <Link className="font-medium text-[var(--accent)] underline" href="/login">
            đăng nhập bằng ID Telegram và mật khẩu
          </Link>
          .
        </p>
      </div>
    );
  }

  // Step 1F.2.3: the PR services speak structural English, on purpose - they are
  // shared by Telegram and this panel. `errorMessage` is the browser's half of
  // that boundary, and it falls back to the server's own sentence for every code
  // it has nothing better to say about.
  const mapped = api ? errorMessage(api.code, api.details) : null;
  const message =
    mapped ?? api?.message ?? (error instanceof Error ? error.message : "Có lỗi xảy ra.");
  return (
    <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-6 text-sm">
      <p className="font-medium">{message}</p>
      {/* The "reload and retry" hint is for a 409 that means the record moved.
          A code this file has its own sentence for is a rule - published
          content, recorded work - and the sentence already says what to do;
          telling somebody to reload and retry a rule is the wrong advice. */}
      {api?.isConflict && !mapped ? (
        <p className="mt-1 text-[var(--text-muted)]">
          Dữ liệu có thể đã thay đổi. Bạn tải lại trang rồi thử lại nhé.
        </p>
      ) : null}
      {api?.isForbidden ? (
        <p className="mt-1 text-[var(--text-muted)]">
          Quyền được cấp trong{" "}
          <Link className="underline" href="/pr/permissions?tab=grants">
            Thành viên & Phân quyền
          </Link>
          .
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 min-h-11 rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]"
        >
          Thử lại
        </button>
      ) : null}
    </div>
  );
}

/**
 * A small pill for a stage, status or capability code.
 *
 * `critical` is the only **solid** tone, added by Step 1F.2.3d for "Rất gấp".
 * The other four are tinted backgrounds at the same weight, which is right for
 * a row of badges that should read as one texture; the point of this one is
 * that it does not, because it is the level that has to be noticed across a
 * board of sixty cards. It is deliberately the only such tone - a second would
 * put it back in a crowd.
 */
/**
 * A deterministic refusal, shown beside the control that was refused and
 * **never** in place of anything.
 *
 * The other half of `ErrorBox`. That one is for a failure the person may do
 * something about - retry a lost request, reload a moved record, ask for a
 * right. This is for a business rule that answered "no" and will answer "no"
 * again: the content is exactly as it was, every other control still works,
 * and the only thing to offer is the reason and a way to put the message away.
 * Amber rather than red, no retry, and `role="status"` so a screen reader hears
 * it without being trapped in it.
 */
export function NoticeBox({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  const mapped = api ? errorMessage(api.code, api.details) : null;
  const message =
    mapped ?? api?.message ?? (error instanceof Error ? error.message : "Thao tác không thực hiện được.");
  return (
    <div
      role="status"
      className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm"
    >
      <p className="font-medium">{message}</p>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="min-h-11 rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]"
        >
          Đóng
        </button>
      ) : null}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "warn" | "good" | "bad" | "critical";
}) {
  const tones = {
    neutral: "bg-[var(--surface-muted)] text-[var(--text-muted)]",
    warn: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
    good: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    bad: "bg-red-500/15 text-red-700 dark:text-red-300",
    // White on red-600 rather than a tint, and the same pair in both schemes:
    // this contrast is legible either way, and a dark-mode variant would make
    // the loudest badge quieter in the theme people read at night.
    critical: "bg-red-600 text-white",
  };
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}
