"use client";

/**
 * Pieces shared by the "Chăm sóc khách hàng" screens (0102): the access gate,
 * message bubbles with every attachment kind, chips, small inputs.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type UnitsMe } from "@/lib/api";
import {
  ATTACHMENT_LABELS,
  LABEL_TONES,
  safeUrl,
  splitPhones,
  type Attachment,
  type Label,
  type Message,
} from "@/lib/cskh";
import { formatShortDateTime, formatDateTime } from "@/lib/datetime";
import { Loading } from "@/components/states";

export type CskhMenu = NonNullable<UnitsMe["cskh"]>;

/** `/api/units/me`'s CSKH block (the shell asks for it too: one request). */
export function useCskhMenu(): { menu: CskhMenu | null; loading: boolean } {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  return { menu: me.data?.cskh ?? null, loading: me.isLoading };
}

/** Renders `children` only for whoever `allowed(menu)` says; else a sentence. */
export function CskhGate({
  allowed,
  children,
}: {
  allowed: (menu: CskhMenu) => boolean;
  children: React.ReactNode;
}) {
  const { menu, loading } = useCskhMenu();
  if (loading) return <Loading />;
  if (!menu || !allowed(menu)) {
    return (
      <div
        className="panel mx-auto mt-8 max-w-lg p-6 text-sm text-[var(--text-muted)]"
        data-testid="cskh-forbidden"
      >
        Bạn chưa có quyền ở mục này. Nhờ người quản trị hộp chat cấp quyền (Phòng ban hoặc Quyền cá nhân, mục
        &quot;Chăm sóc khách hàng&quot;).
      </div>
    );
  }
  return <>{children}</>;
}

export const fieldClass =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";
export const smallButton =
  "inline-flex min-h-9 items-center gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-xs font-medium hover:bg-[var(--surface-muted)] disabled:opacity-50";

/** The first letters of a name in a round chip (no customer photos are loaded). */
export function Initials({ name, size = 36 }: { name: string; size?: number }) {
  const letters =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(-2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "?";
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-xs font-semibold text-[var(--accent-strong)]"
    >
      {letters}
    </span>
  );
}

export function LabelChip({ label }: { label: Pick<Label, "name" | "color"> }) {
  const tone = LABEL_TONES[label.color] ?? LABEL_TONES.gray;
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-xs"
      data-testid="cskh-label"
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: tone }} />
      {label.name}
    </span>
  );
}

/** A phone number (or QR text) that copies itself on click. */
export function CopyChip({ text, display, title }: { text: string; display?: string; title?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      title={title ?? "Bấm để sao chép"}
      onClick={() => {
        void navigator.clipboard?.writeText(text).catch(() => undefined);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      className="inline-flex min-h-8 max-w-full items-center gap-1 truncate rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 text-xs font-medium tabular-nums hover:bg-[var(--surface-muted)]"
    >
      {copied ? "Đã chép" : (display ?? text)}
    </button>
  );
}

function AttachmentView({ item }: { item: Attachment }) {
  const url = safeUrl(item.url);
  const image = safeUrl(item.image);
  switch (item.kind) {
    case "image":
    case "sticker":
      return url ? (
        <a href={url} target="_blank" rel="noreferrer noopener" className="block">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={ATTACHMENT_LABELS[item.kind]}
            loading="lazy"
            className={`rounded-lg ${item.kind === "sticker" ? "max-h-24" : "max-h-64 max-w-full"}`}
          />
        </a>
      ) : null;
    case "video":
      return url ? (
        <video controls preload="none" poster={image ?? undefined} className="max-h-64 max-w-full rounded-lg">
          <source src={url} />
        </video>
      ) : null;
    case "audio":
      return url ? <audio controls preload="none" src={url} className="max-w-full" /> : null;
    case "card":
      return (
        <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text)]">
          {image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={image} alt="" loading="lazy" className="max-h-40 w-full object-cover" />
          ) : null}
          <div className="p-2 text-xs">
            {item.title ? <p className="font-semibold">{item.title}</p> : null}
            {item.subtitle ? <p className="text-[var(--text-muted)]">{item.subtitle}</p> : null}
            {item.buttons?.length ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {item.buttons.map((button, index) =>
                  safeUrl(button.url) ? (
                    <a
                      key={index}
                      href={safeUrl(button.url)!}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="rounded border border-[var(--border)] px-2 py-0.5 text-[var(--accent)]"
                    >
                      {button.title || "Mở"}
                    </a>
                  ) : (
                    <span key={index} className="rounded border border-[var(--border)] px-2 py-0.5">
                      {button.title}
                    </span>
                  ),
                )}
              </div>
            ) : null}
          </div>
        </div>
      );
    case "ad":
      return (
        <div className="flex gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-xs text-[var(--text)]">
          {image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={image} alt="" loading="lazy" className="h-14 w-14 shrink-0 rounded object-cover" />
          ) : null}
          <div className="min-w-0">
            <p className="font-semibold">📢 Khách bấm quảng cáo</p>
            {item.subtitle ? <p className="line-clamp-3 text-[var(--text-muted)]">{item.subtitle}</p> : null}
          </div>
        </div>
      );
    case "address":
      return <p className="text-xs">📍 {item.title}</p>;
    case "link":
      return url ? (
        <a href={url} target="_blank" rel="noreferrer noopener" className="break-all text-xs underline">
          🔗 {item.title || url}
        </a>
      ) : (
        <p className="text-xs">🔗 {item.title}</p>
      );
    default:
      return url ? (
        <a href={url} target="_blank" rel="noreferrer noopener" className="break-all text-xs underline">
          📎 {item.title || "Tệp"}
        </a>
      ) : (
        <p className="text-xs">📎 {item.title || "Tệp"}</p>
      );
  }
}

const KIND_LABELS: Record<Message["kind"], string> = {
  customer: "Khách",
  staff: "Nhân viên",
  page: "Page",
  auto: "Tự động ngoài giờ",
};

/**
 * One page message: customer on the left (white, bordered), the page on the right
 * (navy). A phone number the customer typed is bold, with "Xác nhận số" (when
 * `onConfirmPhone`) and "Chép" right in the bubble.
 */
export function MessageBubble({
  message,
  onConfirmPhone,
}: {
  message: Message;
  onConfirmPhone?: (phone: string) => void;
}) {
  const out = message.direction === "out";
  const failed = message.status && message.status !== "ok" && !message.status.startsWith("ok");
  const parts = !out && message.text ? splitPhones(message.text) : null;
  const phones = parts?.filter((part) => part.phone).map((part) => part.phone!) ?? [];
  return (
    <div className={`flex ${out ? "justify-end" : "justify-start"}`} data-testid="cskh-message">
      <div className={`max-w-[85%] space-y-1 ${out ? "items-end text-right" : ""}`}>
        <div
          className={`inline-block whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-left text-sm leading-relaxed ${
            out
              ? "rounded-br-md bg-[var(--accent-strong)] text-[var(--surface)]"
              : "rounded-bl-md border border-[var(--border)] bg-[var(--surface)] text-[var(--text)]"
          }`}
        >
          {parts ? (
            <p>
              {parts.map((part, index) =>
                part.phone ? (
                  <strong key={index} className="font-semibold tabular-nums">
                    {part.text}
                  </strong>
                ) : (
                  <span key={index}>{part.text}</span>
                ),
              )}
            </p>
          ) : message.text ? (
            <p>{message.text}</p>
          ) : null}
          {phones.length ? (
            <span className="mt-2 flex flex-wrap gap-1.5" data-testid="cskh-phone-actions">
              {[...new Set(phones)].map((phone) => (
                <span key={phone} className="inline-flex flex-wrap items-center gap-1.5">
                  {onConfirmPhone ? (
                    <button
                      type="button"
                      onClick={() => onConfirmPhone(phone)}
                      className="inline-flex min-h-8 items-center rounded-md bg-[var(--accent-strong)] px-2.5 text-xs font-semibold text-[var(--surface)] hover:opacity-90"
                      aria-label={`Xác nhận số ${phone}`}
                    >
                      Xác nhận số
                    </button>
                  ) : null}
                  <CopyChip text={phone} display="Chép" title={`Chép số ${phone}`} />
                </span>
              ))}
            </span>
          ) : null}
          {message.attachments.length ? (
            <div className={`space-y-1 ${message.text ? "mt-1" : ""}`}>
              {message.attachments.map((item, index) => (
                <AttachmentView key={index} item={item} />
              ))}
            </div>
          ) : null}
          {message.qr?.length ? (
            <p className="mt-1 text-xs font-medium" data-testid="cskh-qr-label">
              Mã QR: {message.qr.join(" · ")}
            </p>
          ) : null}
        </div>
        <p className="text-[11px] text-[var(--text-muted)]" title={formatDateTime(message.at)}>
          {out ? `${message.sender_name || KIND_LABELS[message.kind]} · ` : ""}
          {formatShortDateTime(message.at)}
          {failed ? <span className="text-[var(--bad)]"> · {message.status}</span> : null}
        </p>
      </div>
    </div>
  );
}

/** A small error sentence under a box. */
export function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof Error ? error.message : "Có lỗi, thử lại.";
  return (
    <p role="alert" className="text-xs text-[var(--bad)]">
      {text}
    </p>
  );
}

/** The column header used on the 3-column screens. */
export function ColumnHead({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-12 flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2">
      {children}
    </div>
  );
}
