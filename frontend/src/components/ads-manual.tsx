"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type AdsAccountLine,
  type AdsDashboard,
  type AdsManualEntry,
} from "@/lib/api";
import { money, platformLabel } from "@/components/ads";
import { formatTyped } from "@/components/ads-entry";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import {
  PrimaryButton,
  SecondaryButton,
  Select,
} from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";
import {
  formatDate,
  formatDateTime,
  formatShortDate,
} from "@/lib/datetime";

/**
 * Google Ads and TikTok Ads typed by hand (0081): there is no API for them
 * here, so the marketer adds the ad account ("+ Thêm tài khoản") and types
 * each day's spend and "Số tiền lập hoá đơn" in Khối 3 - one day at a time
 * ("Nhập theo ngày") or straight into the per-day tables. A Trưởng phòng ADS,
 * the OWNER or an ADMIN may type anybody's. A blank cell is "not entered",
 * not 0. Every change is kept and shown in "Lịch sử sửa" (who, when, the
 * number before and after).
 */

type Field = "spend" | "invoice";

const FIELD_LABEL: Record<Field, string> = {
  spend: "Chi tiêu",
  invoice: "Lập hoá đơn",
};

const LABEL =
  "flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]";
const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
const CELL =
  "min-h-9 w-28 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-right text-sm tabular-nums text-[var(--text)]";

/** Today in Vietnam, `YYYY-MM-DD`: a day after it cannot be typed. */
export function vnToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
  }).format(now);
}

function cellKey(accountId: string, day: string, field: Field): string {
  return `${accountId}|${day}|${field}`;
}

/** What is stored for a cell, as typed text ("" = not entered). */
function storedText(
  line: AdsAccountLine,
  index: number,
  field: Field,
): string {
  const entered =
    field === "spend" ? line.spend_entered : line.invoice_entered;
  if (!entered?.[index]) return "";
  const values = field === "spend" ? line.spend_by_day : line.invoice_by_day;
  return Math.round(values?.[index] ?? 0).toLocaleString("vi-VN");
}

const digits = (text: string) => text.replace(/\D/g, "");

/**
 * The typed cells of one form, against what the dashboard says is stored:
 * only the cells that differ travel.
 */
function useDraft(data: AdsDashboard) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  // A new answer (another period, a save) starts from what is stored.
  useEffect(() => setDraft({}), [data]);
  const days = useMemo(() => data.days ?? [], [data]);
  const text = (line: AdsAccountLine, index: number, field: Field) => {
    const key = cellKey(line.account_id, days[index], field);
    return key in draft ? draft[key] : storedText(line, index, field);
  };
  const changes: AdsManualEntry[] = [];
  for (const [key, value] of Object.entries(draft)) {
    const [accountId, day, field] = key.split("|") as [string, string, Field];
    const line = data.accounts.find((item) => item.account_id === accountId);
    const index = days.indexOf(day);
    if (!line || index < 0) continue;
    if (digits(value) !== digits(storedText(line, index, field))) {
      changes.push({
        account_id: accountId,
        day,
        field,
        amount: digits(value) === "" ? null : digits(value),
      });
    }
  }
  const set = (key: string, value: string) =>
    setDraft((current) => ({ ...current, [key]: formatTyped(value) }));
  return { days, text, set, changes };
}

/** The draft clears itself when the refetched dashboard arrives. */
function useSave() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entries: AdsManualEntry[]) => api.saveAdsManualDays(entries),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ads"] });
    },
  });
}

function SaveBar({
  changes,
  onSave,
  pending,
  error,
  reset,
}: {
  changes: AdsManualEntry[];
  onSave: () => void;
  pending: boolean;
  error: unknown;
  reset: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-3 pb-3">
      <span className="text-xs text-[var(--text-muted)]">
        Ô để trống là chưa nhập (khác 0). Mỗi lần sửa được ghi vào “Lịch sử
        sửa”.
      </span>
      <ConfirmButton
        tone="primary"
        disabled={changes.length === 0}
        pending={pending}
        error={error}
        onOpenChange={(open) => (open ? reset() : undefined)}
        onConfirm={onSave}
        spec={{
          title: "Lưu số đã nhập?",
          description:
            "Chi tiêu và số tiền lập hoá đơn được ghi theo từng ngày; ô để trống là xoá số đã nhập. Mỗi thay đổi được giữ trong Lịch sử sửa (ai sửa, lúc nào, số cũ và số mới).",
          confirmLabel: "Lưu",
          count: changes.length > 1 ? changes.length : undefined,
        }}
      >
        {changes.length ? `Lưu (${changes.length} ô thay đổi)` : "Lưu"}
      </ConfirmButton>
    </div>
  );
}

/** "Nhập theo ngày": one day, every account the viewer types, both numbers. */
export function ManualDayForm({ data }: { data: AdsDashboard }) {
  const draft = useDraft(data);
  const today = vnToday();
  const open = draft.days.filter((day) => day <= today);
  const [day, setDay] = useState("");
  const chosen = open.includes(day) ? day : (open[open.length - 1] ?? "");
  const index = draft.days.indexOf(chosen);
  const lines = data.accounts.filter((line) => line.can_edit);
  const save = useSave();
  if (!draft.days.length) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        Chọn khoảng tối đa 62 ngày để nhập theo từng ngày.
      </p>
    );
  }
  if (!lines.length) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        Không có tài khoản nào bạn nhập được số ở đây.
      </p>
    );
  }
  const dayChanges = draft.changes.filter((entry) => entry.day === chosen);
  return (
    <div className="panel space-y-3 pt-3" data-testid="manual-day-form">
      <div className="flex flex-wrap items-end gap-3 px-3">
        <label className={LABEL}>
          <span>Ngày</span>
          <Select
            aria-label="Ngày nhập"
            value={chosen}
            onChange={(event) => setDay(event.target.value)}
          >
            {[...open].reverse().map((item) => (
              <option key={item} value={item}>
                {formatDate(item)}
              </option>
            ))}
          </Select>
        </label>
        <p className="pb-3 text-xs text-[var(--text-muted)]">
          Nhập số trên trang {platformLabel(data.platform)} của ngày{" "}
          {formatDate(chosen)}.
        </p>
      </div>
      <div className="table-scroll-x">
        <table className="table-dense w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="text-left">Tài khoản · ID</th>
              <th className="text-right">Số tiền chi tiêu</th>
              <th className="text-right">Số tiền lập hoá đơn</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.account_id}>
                <td>
                  <div className="font-medium">{line.name}</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {line.external_id ?? line.account_id}
                    {line.user_name ? ` · ${line.user_name}` : ""}
                  </div>
                </td>
                {(["spend", "invoice"] as const).map((field) => (
                  <td key={field} className="text-right">
                    <input
                      inputMode="numeric"
                      aria-label={`${FIELD_LABEL[field]} ${line.name} ngày ${formatShortDate(chosen)}`}
                      value={draft.text(line, index, field)}
                      disabled={save.isPending}
                      onChange={(event) =>
                        draft.set(
                          cellKey(line.account_id, chosen, field),
                          event.target.value,
                        )
                      }
                      placeholder="chưa nhập"
                      className={`${CELL} w-40`}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SaveBar
        changes={dayChanges}
        onSave={() => save.mutate(dayChanges)}
        pending={save.isPending}
        error={save.error}
        reset={() => save.reset()}
      />
    </div>
  );
}

/**
 * The per-day table of a typed-in platform: one row per account, one column
 * per day; the cells the viewer types are fields (in ₫), the others plain.
 */
export function ManualDailyTable({
  data,
  kind,
}: {
  data: AdsDashboard;
  kind: Field;
}) {
  const draft = useDraft(data);
  const save = useSave();
  const today = vnToday();
  const { days } = draft;
  if (!days.length) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        Chọn khoảng tối đa 62 ngày để xem và nhập theo từng ngày.
      </p>
    );
  }
  const amount = (line: AdsAccountLine, index: number) =>
    Number(digits(draft.text(line, index, kind)) || 0);
  const dayTotals = days.map((_, index) =>
    data.accounts.reduce((sum, line) => sum + amount(line, index), 0),
  );
  const changes = draft.changes.filter((entry) => entry.field === kind);
  const STICKY = "sticky left-0 z-[1] bg-[var(--surface)]";
  return (
    <div className="panel" data-testid={`manual-table-${kind}`}>
      <div className="table-scroll-x">
        <table
          className="table-dense w-full"
          style={{ minWidth: `${360 + days.length * 128}px` }}
        >
          <thead>
            <tr>
              <th className={`text-left ${STICKY}`}>Tài khoản · ID</th>
              <th className="text-right">Tổng</th>
              {days.map((day) => (
                <th key={day} className="text-right">
                  {formatShortDate(day)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.accounts.map((line) => (
              <tr key={line.account_id}>
                <td className={STICKY}>
                  <div className="font-medium">{line.name}</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {line.external_id ?? line.account_id}
                  </div>
                </td>
                <td className="text-right tabular-nums">
                  {money(
                    days.reduce((sum, _, index) => sum + amount(line, index), 0),
                  )}
                </td>
                {days.map((day, index) =>
                  line.can_edit && day <= today ? (
                    <td key={day} className="text-right">
                      <input
                        inputMode="numeric"
                        aria-label={`${FIELD_LABEL[kind]} ${line.name} ngày ${formatShortDate(day)}`}
                        value={draft.text(line, index, kind)}
                        disabled={save.isPending}
                        onChange={(event) =>
                          draft.set(
                            cellKey(line.account_id, day, kind),
                            event.target.value,
                          )
                        }
                        className={CELL}
                      />
                    </td>
                  ) : (
                    <td key={day} className="text-right tabular-nums">
                      {draft.text(line, index, kind) || "–"}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td className={STICKY}>TỔNG NGÀY</td>
              <td className="text-right tabular-nums">
                {money(dayTotals.reduce((sum, value) => sum + value, 0))}
              </td>
              {dayTotals.map((value, index) => (
                <td key={days[index]} className="text-right tabular-nums">
                  {value ? value.toLocaleString("vi-VN") : "–"}
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
      {data.accounts.some((line) => line.can_edit) ? (
        <div className="pt-3">
          <SaveBar
            changes={changes}
            onSave={() => save.mutate(changes)}
            pending={save.isPending}
            error={save.error}
            reset={() => save.reset()}
          />
        </div>
      ) : null}
    </div>
  );
}

/** "+ Thêm tài khoản" and "Lịch sử sửa" above a typed-in platform's Khối 3. */
export function ManualToolbar({
  data,
  userId,
}: {
  data: AdsDashboard;
  /** The marketer in view (a manager's pick). */
  userId?: string;
}) {
  const [adding, setAdding] = useState(false);
  const [history, setHistory] = useState(false);
  return (
    <div className="flex flex-wrap gap-2">
      {data.can_add_account ? (
        <PrimaryButton type="button" onClick={() => setAdding(true)}>
          + Thêm tài khoản {platformLabel(data.platform)}
        </PrimaryButton>
      ) : null}
      <SecondaryButton type="button" onClick={() => setHistory(true)}>
        Lịch sử sửa
      </SecondaryButton>
      {adding ? (
        <AccountDialog
          data={data}
          userId={userId}
          onClose={() => setAdding(false)}
        />
      ) : null}
      {history ? (
        <ChangesDialog
          data={data}
          userId={userId}
          onClose={() => setHistory(false)}
        />
      ) : null}
    </div>
  );
}

/** "Sửa" on a typed-in account's row of "Tổng hợp & đối soát". */
export function ManualAccountButton({
  data,
  line,
}: {
  data: AdsDashboard;
  line: AdsAccountLine;
}) {
  const [open, setOpen] = useState(false);
  if (!line.can_edit) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Sửa tài khoản ${line.name}`}
        className="text-xs text-[var(--accent)] hover:underline"
      >
        Sửa
      </button>
      {open ? (
        <AccountDialog data={data} line={line} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

/** Add an account, or (`line`) edit / remove one. */
function AccountDialog({
  data,
  line,
  userId,
  onClose,
}: {
  data: AdsDashboard;
  line?: AdsAccountLine;
  userId?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const platform = data.platform ?? "GOOGLE";
  const [externalId, setExternalId] = useState(line?.external_id ?? "");
  const [name, setName] = useState(line?.name ?? "");
  const [site, setSite] = useState(line?.site ?? "");
  const [card, setCard] = useState(line?.card_last4 ?? "");
  const [holder, setHolder] = useState(userId ?? "");
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ["ads"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: () =>
      line
        ? api.updateAdsManualAccount(line.account_id, {
            name,
            site,
            card_last4: card,
          })
        : api.createAdsManualAccount({
            platform,
            external_id: externalId,
            name,
            site: site || undefined,
            card_last4: card || undefined,
            user_id: holder || undefined,
          }),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteAdsManualAccount(line?.account_id ?? ""),
    onSuccess: done,
  });
  const label = platformLabel(platform);
  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending || remove.isPending}
      title={line ? `Sửa tài khoản ${label}` : `Thêm tài khoản ${label}`}
      description={
        line
          ? "ID tài khoản không đổi được; tài khoản nhập nhầm mà chưa có số nào thì xoá được."
          : "Tài khoản bạn chạy trên " +
            label +
            ": sau khi thêm, nhập chi tiêu và số tiền lập hoá đơn theo ngày ở Khối 3."
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className={LABEL}>
            <span>ID tài khoản</span>
            <input
              aria-label="ID tài khoản"
              value={externalId}
              disabled={Boolean(line)}
              onChange={(event) => setExternalId(event.target.value)}
              placeholder={platform === "GOOGLE" ? "123-456-7890" : "7123456789012345678"}
              required
              className={FIELD}
            />
          </label>
          <label className={LABEL}>
            <span>Tên tài khoản</span>
            <input
              aria-label="Tên tài khoản"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="VD: APEX Google SG 01"
              required
              maxLength={300}
              className={FIELD}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={LABEL}>
              <span>Cơ sở</span>
              <input
                aria-label="Cơ sở"
                list="ads-manual-sites"
                value={site}
                onChange={(event) => setSite(event.target.value)}
                placeholder="Sài Gòn / Hà Nội"
                maxLength={60}
                className={FIELD}
              />
              <datalist id="ads-manual-sites">
                {data.sites.map((item) => (
                  <option key={item} value={item} />
                ))}
              </datalist>
            </label>
            <label className={LABEL}>
              <span>4 số cuối thẻ</span>
              <input
                aria-label="4 số cuối thẻ"
                inputMode="numeric"
                value={card}
                onChange={(event) =>
                  setCard(event.target.value.replace(/\D/g, "").slice(0, 4))
                }
                placeholder="không bắt buộc"
                className={FIELD}
              />
            </label>
          </div>
          {!line && data.people.length ? (
            <label className={LABEL}>
              <span>Người chạy</span>
              <Select
                aria-label="Người chạy"
                value={holder}
                onChange={(event) => setHolder(event.target.value)}
              >
                <option value="">Tôi</option>
                {data.people.map((person) => (
                  <option key={person.user_id} value={person.user_id}>
                    {person.name}
                    {person.member_code ? ` (${person.member_code})` : ""}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {save.isError ? (
            <NoticeBox error={save.error} onDismiss={() => save.reset()} />
          ) : null}
          {remove.isError ? (
            <NoticeBox error={remove.error} onDismiss={() => remove.reset()} />
          ) : null}
        </ModalBody>
        <ModalFooter
          start={
            line ? (
              <ConfirmButton
                tone="danger"
                pending={remove.isPending}
                error={remove.error}
                onOpenChange={(open) => (open ? remove.reset() : undefined)}
                onConfirm={() => remove.mutate()}
                spec={{
                  title: `Xoá tài khoản ${line.name}?`,
                  description:
                    "Chỉ xoá được tài khoản chưa từng có số nhập. Tài khoản đã có số thì giữ lại để không mất lịch sử.",
                  confirmLabel: "Xoá tài khoản",
                  variant: "destructive",
                }}
              >
                Xoá
              </ConfirmButton>
            ) : undefined
          }
        >
          <SecondaryButton type="button" onClick={onClose}>
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={save.isPending}>
            {line ? "Lưu" : "Thêm tài khoản"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

/** "Lịch sử sửa": the newest changes of the period's days first. */
function ChangesDialog({
  data,
  userId,
  onClose,
}: {
  data: AdsDashboard;
  userId?: string;
  onClose: () => void;
}) {
  const params = {
    start: data.start,
    end: data.end,
    platform: data.platform,
    user_id: userId || undefined,
  };
  const changes = useQuery({
    queryKey: ["ads", "manual-changes", params],
    queryFn: () => api.adsManualChanges(params),
    retry: false,
  });
  const amount = (value: number | null) =>
    value === null ? "chưa nhập" : money(value);
  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title={`Lịch sử sửa · ${platformLabel(data.platform)}`}
      description={`Các ô đã sửa của ngày ${formatDate(data.start)} – ${formatDate(data.end)}, mới nhất trước.`}
    >
      <ModalBody>
        {changes.isPending ? <Loading label="Đang tải lịch sử…" /> : null}
        {changes.isError ? (
          <ErrorBox error={changes.error} onRetry={() => changes.refetch()} />
        ) : null}
        {changes.data && changes.data.changes.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            Chưa có lần sửa nào trong khoảng này.
          </p>
        ) : null}
        {changes.data && changes.data.changes.length ? (
          <div className="table-scroll-x">
            <table className="table-dense w-full min-w-[640px]">
              <thead>
                <tr>
                  <th className="text-left">Lúc</th>
                  <th className="text-left">Người sửa</th>
                  <th className="text-left">Tài khoản</th>
                  <th className="text-left">Ngày · cột</th>
                  <th className="text-right">Trước → sau</th>
                </tr>
              </thead>
              <tbody>
                {changes.data.changes.map((change) => (
                  <tr key={change.id} data-testid={`manual-change-${change.id}`}>
                    <td className="tabular-nums">
                      {formatDateTime(change.changed_at)}
                    </td>
                    <td>{change.changed_by ?? "—"}</td>
                    <td>
                      <div>{change.account_name}</div>
                      <div className="text-xs text-[var(--text-muted)]">
                        {change.external_id ?? change.account_id}
                      </div>
                    </td>
                    <td>
                      {formatDate(change.day)} · {FIELD_LABEL[change.field]}
                    </td>
                    <td className="text-right tabular-nums">
                      <span className="text-[var(--text-muted)]">
                        {amount(change.old_amount)}
                      </span>{" "}
                      → <span className="font-medium">{amount(change.new_amount)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Đóng
        </SecondaryButton>
      </ModalFooter>
    </Modal>
  );
}
