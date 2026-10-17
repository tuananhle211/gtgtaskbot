"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type PerfWeights, type UnitSettingsInfo } from "@/lib/api";
import { formatTokens } from "@/lib/deadline";
import { ConfirmButton } from "@/components/confirm";
import { NoticeBox, Pill } from "@/components/states";
import {
  deactivateAccountConfirmation,
  deleteAccountConfirmation,
  reactivateAccountConfirmation,
} from "@/lib/confirmations";
import { byStreamOrder, unitShortLabel, unitTagClass } from "@/lib/units";

/** What the short ORD function tags stand for, for the chip's tooltip. */
const FUNCTION_NAMES: Record<string, string> = {
  BT: "Biên kịch",
  TK: "Design",
  D: "Dựng",
};

/**
 * Stream chips: PR teal, ORD orange - the same colours everywhere. Beside the
 * ORD chip, the person's function there ([BT] / [TK] / [D], ★ for its lead).
 */
export function UnitTags({
  units,
  functionTag,
  isLead = false,
}: {
  units: string[];
  /** ORD only: "BT" / "TK" / "D". */
  functionTag?: string | null;
  isLead?: boolean;
}) {
  if (units.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {byStreamOrder(units).map((unit) => (
        <span key={unit} className="inline-flex gap-1">
          <span className={`unit-tag ${unitTagClass(unit)}`}>{unitShortLabel(unit)}</span>
          {unit === "ADS" && functionTag ? (
            <FunctionTag tag={functionTag} isLead={isLead} />
          ) : null}
        </span>
      ))}
    </span>
  );
}

/** [BT] / [TK] / [D], and [BT★] for the function's lead ("Trưởng"). */
export function FunctionTag({ tag, isLead = false }: { tag: string; isLead?: boolean }) {
  const name = FUNCTION_NAMES[tag] ?? tag;
  const title = isLead ? `Trưởng phòng ${name}` : name;
  return (
    <span
      title={title}
      aria-label={title}
      data-function-tag={tag}
      className="unit-tag unit-tag-ads"
    >
      {tag}
      {isLead ? <span aria-hidden="true">★</span> : null}
    </span>
  );
}

/**
 * Whether the viewer may deactivate / reactivate this account: OWNER and
 * ADMIN only, never themselves, and an ADMIN never an OWNER. A convenience -
 * the server refuses the rest anyway (403 `account_status_forbidden`).
 */
export function mayChangeAccountStatus(
  viewer: { role?: string; userId: string | null },
  target: { user_id: string; role?: string | null },
): boolean {
  if (viewer.role !== "OWNER" && viewer.role !== "ADMIN") return false;
  if (viewer.userId && viewer.userId === target.user_id) return false;
  if (viewer.role === "ADMIN" && target.role === "OWNER") return false;
  return true;
}

/**
 * "Vô hiệu hoá" (destructive, confirmed) or "Kích hoạt lại" for one account.
 * Stream-neutral: `POST /api/account/members/{id}/deactivate|reactivate`.
 */
export function AccountStatusButton({
  userId,
  name,
  active,
  onDone,
}: {
  userId: string;
  name: string;
  active: boolean;
  onDone: () => void;
}) {
  const change = useMutation({
    mutationFn: () =>
      active ? api.deactivateAccount(userId) : api.reactivateAccount(userId),
    onSuccess: onDone,
  });
  return (
    <ConfirmButton
      spec={active ? deactivateAccountConfirmation(name) : reactivateAccountConfirmation(name)}
      onConfirm={() => change.mutate()}
      pending={change.isPending}
      error={change.error}
      tone={active ? "danger" : "secondary"}
      ariaLabel={active ? `Vô hiệu hoá tài khoản ${name}` : `Kích hoạt lại tài khoản ${name}`}
      className="min-h-9 px-3 text-xs"
      onOpenChange={(open) => {
        if (open) change.reset();
      }}
    >
      {active ? "Vô hiệu hoá" : "Kích hoạt lại"}
    </ConfirmButton>
  );
}

/**
 * Whether the viewer may delete this account for good: the OWNER only, never
 * themselves nor another OWNER. A convenience - the server refuses the rest
 * anyway (403 `member_delete_forbidden`).
 */
export function mayDeleteAccount(
  viewer: { role?: string; userId: string | null },
  target: { user_id: string; role?: string | null },
): boolean {
  if (viewer.role !== "OWNER") return false;
  if (viewer.userId && viewer.userId === target.user_id) return false;
  return target.role !== "OWNER";
}

/**
 * "Xoá" / "Xoá thành viên" (destructive, confirmed) for one account:
 * `DELETE /api/account/members/{id}`. Somebody who still holds open work is
 * refused (422 `member_has_open_work`): the server's sentence is shown on its
 * own line under the row's buttons (the caller's box is a wrapping flex row).
 */
export function DeleteAccountButton({
  userId,
  name,
  onDone,
  children = "Xoá",
}: {
  userId: string;
  name: string;
  onDone: () => void;
  children?: React.ReactNode;
}) {
  const remove = useMutation({
    mutationFn: () => api.deleteMember(userId),
    onSuccess: onDone,
  });
  return (
    <>
      <ConfirmButton
        spec={deleteAccountConfirmation(name)}
        onConfirm={() => remove.mutate()}
        pending={remove.isPending}
        error={remove.error}
        tone="danger"
        ariaLabel={`Xoá hẳn tài khoản ${name}`}
        className="min-h-9 px-3 text-xs"
        onOpenChange={(open) => {
          if (open) remove.reset();
        }}
      >
        {children}
      </ConfirmButton>
      {remove.isError ? (
        <div className="min-w-[16rem] basis-full whitespace-normal text-left">
          <NoticeBox error={remove.error} onDismiss={() => remove.reset()} />
        </div>
      ) : null}
    </>
  );
}

/** "Đã vô hiệu hoá": a deactivated account, on any roster. */
export function DeactivatedPill() {
  return <Pill tone="bad">Đã vô hiệu hoá</Pill>;
}

/** The server's own default for `default_daily_tokens`, until settings load. */
const DEFAULT_DAILY_TOKENS = 80;
/** The server's own default performance weights. */
const DEFAULT_WEIGHTS: PerfWeights = { output: 0.5, on_time: 0.3, quality: 0.2 };

/** The signed-in person: their base role decides what the settings form offers. */
function useViewer() {
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const role = session.data?.role;
  return {
    userId: session.data?.user_id ?? null,
    role,
    manages: role === "OWNER" || role === "ADMIN",
  };
}

export function SettingsForm({ code, onDone }: { code: string; onDone: () => void }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const viewer = useViewer();
  const current = me.data?.units.find((unit) => unit.code === code)?.settings;
  const [draft, setDraft] = useState<Partial<UnitSettingsInfo>>({});
  const [texts, setTexts] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => api.updateUnitSettings(code, draft),
    onSuccess: () => {
      setDraft({});
      setTexts({});
      onDone();
    },
  });
  if (!current) return null;
  // The saved switch decides what is drawn; a draft only changes on save.
  const effortOn = current.effort_enabled !== false;
  const isOwner = viewer.role === "OWNER";
  const value = <K extends keyof UnitSettingsInfo>(
    key: K,
  ): UnitSettingsInfo[K] => (draft[key] ?? current[key]) as UnitSettingsInfo[K];
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
  // Number inputs keep what is typed (an empty box included) until saved; the
  // draft holds the parsed number, or null for an empty optional one.
  const numberText = (
    key: "default_daily_tokens" | "output_target",
    saved: number | null,
  ) =>
    texts[key] ?? (saved === null || saved === undefined ? "" : String(saved));
  const setNumber = (
    key: "default_daily_tokens" | "output_target",
    text: string,
    optional = false,
  ) => {
    setTexts((all) => ({ ...all, [key]: text }));
    const parsed = text.trim() === "" ? null : Number(text);
    setDraft((all) => {
      const next = { ...all };
      if (parsed === null ? optional : Number.isFinite(parsed) && parsed >= 0) {
        (next as Record<string, unknown>)[key] = parsed;
      } else {
        delete (next as Record<string, unknown>)[key];
      }
      return next;
    });
  };
  const weights = { ...DEFAULT_WEIGHTS, ...current.perf_weights, ...draft.perf_weights };
  const weightText = (key: keyof PerfWeights) =>
    texts[`w_${key}`] ?? String(weights[key]);
  const setWeight = (key: keyof PerfWeights, text: string) => {
    setTexts((all) => ({ ...all, [`w_${key}`]: text }));
    const parsed = Number(text);
    if (text.trim() === "" || !Number.isFinite(parsed) || parsed < 0 || parsed > 1) return;
    setDraft((all) => ({
      ...all,
      perf_weights: { ...weights, [key]: parsed },
    }));
  };
  const weightsSum = (["output", "on_time", "quality"] as const).every((key) =>
    Number.isFinite(Number(weightText(key))),
  )
    ? (["output", "on_time", "quality"] as const).reduce(
        (sum, key) => sum + Number(weightText(key)),
        0,
      )
    : null;
  return (
    <form
      className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 sm:grid-cols-2"
      onSubmit={(event) => event.preventDefault()}
    >
      <h2 className="text-sm font-semibold sm:col-span-2">Thiết lập luồng</h2>
      {/* Effort (tokens) on / off for the whole stream. The OWNER's alone:
          anybody else sees where it stands (403 `effort_setting_forbidden`). */}
      <div className="sm:col-span-2">
        <label className="inline-flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            role="switch"
            checked={draft.effort_enabled ?? effortOn}
            disabled={!isOwner}
            onChange={(event) =>
              setDraft((all) => ({
                ...all,
                effort_enabled: event.target.checked,
              }))
            }
          />
          Tính effort (token)
        </label>
        {isOwner ? null : (
          <span className="block text-xs text-[var(--text-muted)]">
            Chỉ chủ sở hữu đổi được
          </span>
        )}
      </div>
      <label className="text-xs text-[var(--text-muted)]">
        Mốc Gấp (ngày)
        <input
          type="number"
          min={1}
          max={365}
          value={value("urgent_days")}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              urgent_days: Number(event.target.value),
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      {/* No "người gắn link" setting: the last production node hands the
          product link in with its work (btd_link_attacher is obsolete). */}
      <label className="text-xs text-[var(--text-muted)]">
        Link Kho Media
        <input
          type="url"
          value={value("media_nas_url") ?? ""}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              media_nas_url: event.target.value || null,
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      <label className="text-xs text-[var(--text-muted)]">
        Link Kho Thiết kế
        <input
          type="url"
          value={value("design_nas_url") ?? ""}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              design_nas_url: event.target.value || null,
            }))
          }
          className={`mt-1 ${field}`}
        />
      </label>
      <label className="inline-flex min-h-11 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value("telegram_enabled")}
          onChange={(event) =>
            setDraft((all) => ({
              ...all,
              telegram_enabled: event.target.checked,
            }))
          }
        />
        Gửi thêm thông báo qua Telegram
      </label>
      {effortOn ? (
        <label className="text-xs text-[var(--text-muted)]">
          Token/ngày mặc định
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={999.99}
            step={0.5}
            value={numberText("default_daily_tokens", current.default_daily_tokens ?? DEFAULT_DAILY_TOKENS)}
            onChange={(event) => setNumber("default_daily_tokens", event.target.value)}
            className={`mt-1 ${field}`}
          />
          <span className="mt-0.5 block">
            Quỹ mỗi ngày làm việc của một người, khi người đó chưa có quỹ riêng
          </span>
        </label>
      ) : null}
      {effortOn ? (
        <label className="inline-flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={(value("half_weekdays") ?? [5]).includes(5)}
            onChange={(event) =>
              setDraft((all) => ({
                ...all,
                half_weekdays: event.target.checked ? [5] : [],
              }))
            }
          />
          Thứ 7 làm buổi sáng (½ quỹ token/ngày)
        </label>
      ) : null}
      <label className="text-xs text-[var(--text-muted)]">
        Mốc sản lượng (công đoạn/tháng)
        <input
          type="number"
          inputMode="numeric"
          min={1}
          step={1}
          value={numberText("output_target", current.output_target ?? null)}
          onChange={(event) => setNumber("output_target", event.target.value, true)}
          placeholder="Người làm nhiều nhất"
          className={`mt-1 ${field}`}
        />
        <span className="mt-0.5 block">
          Để trống = người làm nhiều nhất trong ban tháng đó
        </span>
      </label>
      <fieldset className="sm:col-span-2">
        <legend className="text-xs text-[var(--text-muted)]">
          Trọng số điểm hiệu suất (tổng nên bằng 1)
        </legend>
        <div className="mt-1 grid gap-3 sm:grid-cols-3">
          {(
            [
              ["output", "Sản lượng"],
              ["on_time", "Đúng hạn"],
              ["quality", "Không bị trả"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="text-xs text-[var(--text-muted)]">
              {label}
              <input
                type="number"
                inputMode="decimal"
                min={0}
                max={1}
                step={0.05}
                value={weightText(key)}
                onChange={(event) => setWeight(key, event.target.value)}
                aria-label={`Trọng số ${label}`}
                className={`mt-1 ${field}`}
              />
            </label>
          ))}
        </div>
        {weightsSum !== null && Math.abs(weightsSum - 1) > 0.001 ? (
          <p className="mt-1 text-xs text-[var(--warn)]">
            Tổng trọng số đang là {formatTokens(weightsSum)} (nên bằng 1).
          </p>
        ) : null}
      </fieldset>
      {code === "ADS" && current.script_ai_review !== undefined ? (
        <fieldset className="sm:col-span-2">
          <legend className="text-xs text-[var(--text-muted)]">
            AI duyệt “Khung nhập kịch bản” khi người order gửi order: không đạt thì
            trả lại ngay cho người order, đạt mới chuyển cho trưởng phòng
          </legend>
          <label className="inline-flex min-h-11 items-center gap-2 text-sm">
            <input
              type="checkbox"
              role="switch"
              checked={Boolean(value("script_ai_review"))}
              onChange={(event) =>
                setDraft((all) => ({ ...all, script_ai_review: event.target.checked }))
              }
            />
            AI duyệt kịch bản
          </label>
          <label className="mt-1 block text-xs text-[var(--text-muted)]">
            Bộ quy tắc (chỉ các mục cấm được đánh số mới tính là lỗi)
            <textarea
              aria-label="Bộ quy tắc AI duyệt kịch bản"
              value={value("script_review_rules") ?? ""}
              maxLength={8000}
              onChange={(event) =>
                setDraft((all) => ({ ...all, script_review_rules: event.target.value }))
              }
              className={`mt-1 min-h-48 ${field}`}
            />
          </label>
          {current.script_footer !== undefined ? (
            <label className="mt-1 block text-xs text-[var(--text-muted)]">
              Hashtag & chân trang chuẩn (AI thêm vào bản sửa gợi ý khi bài thiếu; không tính là lỗi)
              <textarea
                aria-label="Hashtag và chân trang chuẩn"
                value={value("script_footer") ?? ""}
                maxLength={2000}
                onChange={(event) =>
                  setDraft((all) => ({ ...all, script_footer: event.target.value }))
                }
                className={`mt-1 min-h-32 ${field}`}
              />
            </label>
          ) : null}
        </fieldset>
      ) : null}
      <fieldset className="sm:col-span-2">
        <legend className="text-xs text-[var(--text-muted)]">
          Trưởng phòng duyệt bài nộp trước khi chuyển bước (bỏ chọn: nộp xong tự
          chuyển bước tiếp theo)
        </legend>
        <div className="mt-1 flex flex-wrap gap-x-5">
          {(
            [
              ["review_bien_tap", "Biên kịch"],
              ["review_thiet_ke", "Design"],
              ["review_dung", "Dựng"],
              ["review_video_by_script_lead", "Trưởng phòng Biên kịch xem video (quy trình có Biên kịch)"],
            ] as const
          ).map(([key, label]) => (
            <label
              key={key}
              className="inline-flex min-h-11 items-center gap-2 text-sm"
            >
              <input
                type="checkbox"
                checked={Boolean(value(key))}
                onChange={(event) =>
                  setDraft((all) => ({ ...all, [key]: event.target.checked }))
                }
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex justify-end sm:col-span-2">
        <ConfirmButton
          spec={{
            title: "Lưu thiết lập luồng?",
            description: "Áp dụng cho mọi order của luồng từ giờ.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={Object.keys(draft).length === 0}
          tone="primary"
        >
          Lưu thiết lập
        </ConfirmButton>
      </div>
      {save.isError ? <NoticeBox error={save.error} /> : null}
    </form>
  );
}
