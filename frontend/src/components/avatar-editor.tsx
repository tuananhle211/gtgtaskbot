"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type AccountMe, type AvatarContentType, type Session } from "@/lib/api";
import { Avatar } from "@/components/avatar";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { removeAvatarConfirmation } from "@/lib/confirmations";

/**
 * Changing and removing the signed-in person's profile picture.
 *
 * The browser does the image work and the server only validates (see the
 * avatar contract): the person picks a file, positions it in a square frame by
 * dragging and zooming, and the visible square is drawn onto a 256x256 canvas
 * and exported as WebP (JPEG where the browser cannot encode WebP) at 0.85.
 * What travels is that small file as base64 in JSON - never the original.
 *
 * The preview reads the file through an object URL (`blob:`), which the CSP's
 * `img-src 'self' data: blob:` already allows; it is revoked when the dialog
 * lets go of it.
 */

const ACCEPTED = ["image/png", "image/jpeg", "image/webp", "image/gif"];
const MAX_FILE_BYTES = 10 * 1024 * 1024;
/** The crop frame's side on screen, CSS px. Fits a 320px phone with gutters. */
const FRAME = 280;
/** The exported picture's side. */
export const AVATAR_OUTPUT = 256;
const QUALITY = 0.85;
/** The server refuses more than 300 KB decoded; stay clear of it. */
const MAX_UPLOAD_BYTES = 290 * 1024;
const MIN_ZOOM = 1;
const MAX_ZOOM = 4;

type Natural = { w: number; h: number };
type Offset = { x: number; y: number };

/** The image's on-screen scale in the frame: "cover" at zoom 1. */
const scaleOf = (natural: Natural, zoom: number) => (FRAME / Math.min(natural.w, natural.h)) * zoom;

/** Keep the image covering the whole frame: no empty edge may show. */
function clampOffset(offset: Offset, natural: Natural, zoom: number): Offset {
  const scale = scaleOf(natural, zoom);
  const clamp = (value: number, size: number) => Math.min(0, Math.max(FRAME - size * scale, value));
  return { x: clamp(offset.x, natural.w), y: clamp(offset.y, natural.h) };
}

/** Bytes a base64 string decodes to. */
const decodedBytes = (base64: string) =>
  Math.floor((base64.length * 3) / 4) - (base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0);

/**
 * Draw the framed square onto a 256x256 canvas and export it.
 *
 * WebP first; a browser that cannot encode it hands back a PNG data URL
 * instead, which is how the fallback to JPEG is detected. JPEG has no alpha,
 * so it is drawn on white (a transparent PNG would otherwise turn black).
 */
export function exportAvatar(
  image: CanvasImageSource,
  natural: Natural,
  zoom: number,
  offset: Offset,
): { contentType: AvatarContentType; data: string } {
  const canvas = document.createElement("canvas");
  canvas.width = AVATAR_OUTPUT;
  canvas.height = AVATAR_OUTPUT;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("canvas_unavailable");
  const scale = scaleOf(natural, zoom);
  const side = FRAME / scale;
  const draw = (background?: string) => {
    context.clearRect(0, 0, AVATAR_OUTPUT, AVATAR_OUTPUT);
    if (background) {
      context.fillStyle = background;
      context.fillRect(0, 0, AVATAR_OUTPUT, AVATAR_OUTPUT);
    }
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(image, -offset.x / scale, -offset.y / scale, side, side, 0, 0, AVATAR_OUTPUT, AVATAR_OUTPUT);
  };
  const encode = (type: AvatarContentType, quality: number) => {
    const url = canvas.toDataURL(type, quality);
    return url.startsWith(`data:${type}`) ? url.slice(url.indexOf(",") + 1) : null;
  };

  draw();
  let contentType: AvatarContentType = "image/webp";
  let data = encode("image/webp", QUALITY);
  if (data === null) {
    draw("#ffffff");
    contentType = "image/jpeg";
    data = encode("image/jpeg", QUALITY);
  }
  if (data !== null && decodedBytes(data) > MAX_UPLOAD_BYTES) {
    // Never at 256px in practice; a last guard rather than a 422.
    draw("#ffffff");
    contentType = "image/jpeg";
    data = encode("image/jpeg", 0.7);
  }
  if (data === null) throw new Error("encode_failed");
  return { contentType, data };
}

/** Keep the avatar in step on every screen that already holds it. */
function useAvatarCache() {
  const queryClient = useQueryClient();
  return (avatarUrl: string | null) => {
    queryClient.setQueryData<AccountMe>(["account", "me"], (old) =>
      old ? { ...old, avatar_url: avatarUrl } : old,
    );
    queryClient.setQueryData<Session>(["session"], (old) => (old ? { ...old, avatar_url: avatarUrl } : old));
    void queryClient.invalidateQueries({ queryKey: ["session"] });
    void queryClient.invalidateQueries({ queryKey: ["account", "members"] });
  };
}

const failureText = (error: unknown) =>
  error instanceof ApiError ? error.message : "Không kết nối được máy chủ. Bạn thử lại nhé.";

export function AvatarDialog({
  open,
  onClose,
  name,
  current,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** Whose picture: draws the initials preview and the alt-free previews. */
  name: string;
  current?: string | null;
  onSaved?: () => void;
}) {
  const inputId = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const image = useRef<HTMLImageElement | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [natural, setNatural] = useState<Natural | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 });
  const [problem, setProblem] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const drag = useRef<{ x: number; y: number; from: Offset } | null>(null);
  const updateCache = useAvatarCache();

  const save = useMutation({
    mutationFn: (picture: { contentType: AvatarContentType; data: string }) =>
      api.uploadAvatar(picture.contentType, picture.data),
    onSuccess: (result) => {
      updateCache(result.avatar_url);
      onSaved?.();
      onClose();
    },
  });

  // Let go of the picked file whenever it is replaced or the dialog closes.
  useEffect(() => {
    if (!source) return;
    return () => {
      if (source.startsWith("blob:")) URL.revokeObjectURL?.(source);
    };
  }, [source]);
  useEffect(() => {
    if (open) return;
    setSource(null);
    setNatural(null);
    setZoom(1);
    setProblem(null);
    save.reset();
    // `save` is a fresh object per render; only `open` matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const choose = (file: File | undefined) => {
    if (!file) return;
    save.reset();
    if (!ACCEPTED.includes(file.type)) {
      setProblem("Chỉ nhận ảnh PNG, JPG, WEBP hoặc GIF.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setProblem("Ảnh lớn hơn 10 MB. Bạn chọn ảnh nhỏ hơn nhé.");
      return;
    }
    setProblem(null);
    setNatural(null);
    setZoom(1);
    setSource(URL.createObjectURL(file));
  };

  const loaded = (event: React.SyntheticEvent<HTMLImageElement>) => {
    const { naturalWidth: w, naturalHeight: h } = event.currentTarget;
    if (!w || !h) {
      setProblem("Không đọc được ảnh này. Bạn thử ảnh khác nhé.");
      setSource(null);
      return;
    }
    const next = { w, h };
    const scale = scaleOf(next, 1);
    setNatural(next);
    setZoom(1);
    setOffset({ x: (FRAME - w * scale) / 2, y: (FRAME - h * scale) / 2 });
  };

  /** Zoom about the frame's centre, so what is in the middle stays there. */
  const zoomTo = (value: number) => {
    if (!natural) return;
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
    const before = scaleOf(natural, zoom);
    const after = scaleOf(natural, next);
    const cx = (FRAME / 2 - offset.x) / before;
    const cy = (FRAME / 2 - offset.y) / before;
    setZoom(next);
    setOffset(clampOffset({ x: FRAME / 2 - cx * after, y: FRAME / 2 - cy * after }, natural, next));
  };

  const nudge = (dx: number, dy: number) => {
    if (natural) setOffset((was) => clampOffset({ x: was.x + dx, y: was.y + dy }, natural, zoom));
  };

  const submit = () => {
    if (!natural || !image.current || save.isPending) return;
    let picture: { contentType: AvatarContentType; data: string };
    try {
      picture = exportAvatar(image.current, natural, zoom, offset);
    } catch {
      setProblem("Trình duyệt không xử lý được ảnh này. Bạn thử ảnh PNG hoặc JPG khác nhé.");
      return;
    }
    setProblem(null);
    save.mutate(picture);
  };

  const scale = natural ? scaleOf(natural, zoom) : 1;
  const placed = natural
    ? { left: offset.x, top: offset.y, width: natural.w * scale, height: natural.h * scale }
    : undefined;
  const error = problem ?? (save.isError ? failureText(save.error) : null);

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={save.isPending}
      size="lg"
      title="Ảnh đại diện"
      description={
        source
          ? "Kéo ảnh để căn giữa, dùng thanh trượt để phóng to. Phần trong vòng tròn là phần mọi người thấy."
          : "Ảnh hiện cạnh tên bạn ở thanh trên cùng, bảng thành viên và các danh sách."
      }
    >
      <ModalBody>
        {/* One file input for both "Chọn ảnh" and "Chọn ảnh khác". */}
        <input
          ref={input}
          id={inputId}
          type="file"
          accept={ACCEPTED.join(",")}
          className="peer sr-only"
          onChange={(event) => {
            choose(event.target.files?.[0]);
            // Picking the same file again must fire `change` again.
            event.target.value = "";
          }}
        />
        {source ? (
          <div className="grid gap-6 sm:grid-cols-[auto_minmax(0,1fr)]">
            <div className="mx-auto w-[280px] max-w-full">
              <div
                role="group"
                aria-label="Khung cắt ảnh: kéo hoặc dùng phím mũi tên để căn, phím + và - để phóng to"
                tabIndex={0}
                className="crop-frame relative h-[280px] w-[280px] cursor-grab touch-none select-none overflow-hidden rounded-2xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:cursor-grabbing"
                onPointerDown={(event) => {
                  if (!natural) return;
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                  drag.current = { x: event.clientX, y: event.clientY, from: offset };
                }}
                onPointerMove={(event) => {
                  const start = drag.current;
                  if (!start || !natural) return;
                  setOffset(
                    clampOffset(
                      { x: start.from.x + event.clientX - start.x, y: start.from.y + event.clientY - start.y },
                      natural,
                      zoom,
                    ),
                  );
                }}
                onPointerUp={() => {
                  drag.current = null;
                }}
                onPointerCancel={() => {
                  drag.current = null;
                }}
                onKeyDown={(event) => {
                  const step = event.shiftKey ? 32 : 8;
                  const moves: Record<string, [number, number]> = {
                    ArrowLeft: [step, 0],
                    ArrowRight: [-step, 0],
                    ArrowUp: [0, step],
                    ArrowDown: [0, -step],
                  };
                  if (event.key in moves) {
                    event.preventDefault();
                    nudge(...moves[event.key]);
                  } else if (event.key === "+" || event.key === "=") {
                    event.preventDefault();
                    zoomTo(zoom + 0.25);
                  } else if (event.key === "-") {
                    event.preventDefault();
                    zoomTo(zoom - 0.25);
                  }
                }}
              >
                <img
                  ref={image}
                  src={source}
                  alt="Ảnh đang chọn"
                  draggable={false}
                  onLoad={loaded}
                  onError={() => {
                    setProblem("Không đọc được ảnh này. Bạn thử ảnh khác nhé.");
                    setSource(null);
                  }}
                  className="pointer-events-none absolute max-w-none"
                  style={placed ?? { opacity: 0 }}
                />
                <div aria-hidden="true" className="crop-mask pointer-events-none absolute inset-0 rounded-full" />
              </div>
              <div className="mt-4 flex items-center gap-2">
                <ZoomButton label="Thu nhỏ" disabled={!natural || zoom <= MIN_ZOOM} onClick={() => zoomTo(zoom - 0.25)}>
                  <path d="M5 12h14" />
                </ZoomButton>
                <input
                  type="range"
                  min={MIN_ZOOM}
                  max={MAX_ZOOM}
                  step={0.01}
                  value={zoom}
                  disabled={!natural}
                  aria-label="Thu phóng"
                  onChange={(event) => zoomTo(Number(event.target.value))}
                  className="h-1.5 min-w-0 flex-1 cursor-pointer accent-[var(--accent)]"
                />
                <ZoomButton label="Phóng to" disabled={!natural || zoom >= MAX_ZOOM} onClick={() => zoomTo(zoom + 0.25)}>
                  <path d="M5 12h14M12 5v14" />
                </ZoomButton>
              </div>
            </div>

            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">Xem trước</p>
              <div className="mt-3 flex items-end gap-4" aria-label="Xem trước ảnh đại diện" role="group">
                {[96, 48, 32].map((size) => (
                  <span
                    key={size}
                    className="relative inline-block shrink-0 overflow-hidden rounded-full bg-[var(--surface-muted)] ring-1 ring-[var(--border)]"
                    style={{ width: size, height: size }}
                  >
                    {placed ? (
                      <img
                        src={source}
                        alt=""
                        draggable={false}
                        className="absolute max-w-none"
                        style={{
                          left: (placed.left * size) / FRAME,
                          top: (placed.top * size) / FRAME,
                          width: (placed.width * size) / FRAME,
                          height: (placed.height * size) / FRAME,
                        }}
                      />
                    ) : null}
                  </span>
                ))}
              </div>
              <p className="mt-4 text-sm font-medium">{name}</p>
              <ul className="mt-3 space-y-1.5 text-xs leading-relaxed text-[var(--text-muted)]">
                <li>Ảnh được cắt vuông và thu về 256×256 ngay trên máy bạn.</li>
                <li>Ảnh GIF động chỉ giữ khung hình đầu tiên.</li>
              </ul>
            </div>
          </div>
        ) : (
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOver(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragOver(false);
              choose(event.dataTransfer.files?.[0]);
            }}
            className={`flex flex-col items-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
              dragOver
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-[var(--border)] bg-[var(--surface-muted)]"
            }`}
          >
            <div className="relative">
              <Avatar name={name} src={current} size={72} className="ring-4 ring-[var(--surface)]" />
              <span className="absolute -bottom-1 -right-1 inline-flex h-8 w-8 items-center justify-center rounded-full bg-[var(--accent)] text-[var(--accent-text)] ring-4 ring-[var(--surface-muted)]">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 16V4M7 9l5-5 5 5M5 20h14" />
                </svg>
              </span>
            </div>
            <p className="mt-5 text-sm font-medium">Kéo thả ảnh vào đây</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">hoặc</p>
            <label
              htmlFor={inputId}
              className="mt-2 inline-flex min-h-10 cursor-pointer items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] transition-opacity hover:opacity-90 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--accent)]"
            >
              Chọn ảnh
            </label>
            <p className="mt-4 text-xs text-[var(--text-muted)]">PNG, JPG, WEBP hoặc GIF · tối đa 10 MB</p>
          </div>
        )}
        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] px-3 py-2 text-sm text-[var(--bad)]"
          >
            {error}
          </p>
        ) : null}
      </ModalBody>
      <ModalFooter
        start={
          source ? (
            <label
              htmlFor={inputId}
              className="inline-flex min-h-10 cursor-pointer items-center rounded-lg px-2 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent-soft)]"
            >
              Chọn ảnh khác
            </label>
          ) : null
        }
      >
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending} className="w-full sm:w-auto">
          Thôi
        </SecondaryButton>
        <PrimaryButton
          type="button"
          onClick={submit}
          disabled={!natural || save.isPending}
          className="w-full sm:w-auto"
        >
          {save.isPending ? "Đang lưu…" : "Lưu ảnh"}
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}

function ZoomButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] disabled:opacity-40"
    >
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        {children}
      </svg>
    </button>
  );
}

/** "Xoá ảnh": behind a confirmation, back to the initials. */
export function RemoveAvatarButton({ onRemoved }: { onRemoved?: () => void }) {
  const updateCache = useAvatarCache();
  const remove = useMutation({
    mutationFn: api.removeAvatar,
    onSuccess: () => {
      updateCache(null);
      onRemoved?.();
    },
  });
  return (
    <ConfirmButton
      spec={removeAvatarConfirmation()}
      tone="secondary"
      className="min-h-9 px-3 text-sm"
      pending={remove.isPending}
      error={remove.error ?? undefined}
      onOpenChange={(isOpen) => {
        if (isOpen) remove.reset();
      }}
      onConfirm={() => remove.mutate()}
    >
      Xoá ảnh
    </ConfirmButton>
  );
}
