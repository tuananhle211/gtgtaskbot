"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsConnection, type AdsConnections } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { formatShortDateTime } from "@/lib/datetime";

/**
 * "Phòng Ads › Kết nối BM" (0078; moved from Quản trị on 27/10), the OWNER's alone: the Meta Business
 * Managers Phòng Ads reads, each tied to a cơ sở. A BM is added with its
 * System User token (quyền ads_read + business_management); the token goes to
 * the server encrypted and never comes back - the screen shows "••••abcd".
 * "Test kết nối" asks Meta for the BM and its ad accounts.
 *
 * The schedule pulls yesterday's spend at 08:00 and again at 23:00, and the
 * charges at 08:05; "Đồng bộ ngay" / "Kéo lại khoảng ngày" queue the same job
 * now. Every run shows in Quản trị › Tác vụ định kỳ.
 */
export default function AdsConnectionsPage() {
  const list = useQuery({
    queryKey: ["ads", "connections"],
    queryFn: api.adsConnections,
    retry: false,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Kết nối BM"
        subtitle="Các Business Manager của Meta mà Phòng Ads đọc số liệu, mỗi BM gắn với một cơ sở. Chi phí đồng bộ lúc 08:00 (hôm qua) và 23:00 (quét lại hôm qua); hoá đơn lúc 08:05."
      />
      {list.isPending ? <Loading label="Đang tải kết nối BM…" /> : null}
      {list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : null}
      {list.data ? <Workspace data={list.data} /> : null}
    </div>
  );
}

const SECTION =
  "rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4";
const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
const SECONDARY =
  "min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]";

function useRefresh() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ["ads"] });
}

function Workspace({ data }: { data: AdsConnections }) {
  return (
    <>
      {data.secret_ready ? null : (
        <p
          role="alert"
          className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] px-3 py-2 text-sm"
        >
          Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu
          được token. Cần cấu hình khoá này trên server trước.
        </p>
      )}
      <section aria-label="Danh sách BM" className={SECTION}>
        <h2 className="text-sm font-semibold">Business Manager</h2>
        {data.connections.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            Chưa có BM nào. Thêm BM ở khung bên dưới.
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {data.connections.map((connection) => (
              <ConnectionRow
                key={`${connection.id}:${connection.token_hint}:${connection.name}:${connection.site}`}
                connection={connection}
                sites={data.sites}
              />
            ))}
          </ul>
        )}
      </section>
      <SyncPanel hasConnections={data.connections.length > 0} />
      <AddConnection sites={data.sites} disabled={!data.secret_ready} />
      <p className="text-xs text-[var(--text-muted)]">
        Token lấy ở Business Settings › Người dùng › Người dùng hệ thống (System
        User) › Tạo token, chọn quyền ads_read và business_management. Kết quả
        mỗi lượt đồng bộ xem ở{" "}
        <Link href="/ads/jobs" className="text-[var(--accent)] underline">
          Tác vụ định kỳ
        </Link>
        .
      </p>
    </>
  );
}

function SyncStatus({
  label,
  at,
  ok,
  message,
}: {
  label: string;
  at: string | null;
  ok: boolean | null;
  message: string | null;
}) {
  if (!at)
    return (
      <div className="text-xs text-[var(--text-muted)]">
        {label}: chưa đồng bộ
      </div>
    );
  return (
    <div className="text-xs">
      <span className="text-[var(--text-muted)]">{label}: </span>
      <span className={ok ? "text-[var(--good)]" : "text-[var(--bad)]"}>
        {ok ? "✓" : "✗"}
      </span>{" "}
      {formatShortDateTime(at)}
      {message ? (
        <span className="text-[var(--text-muted)]"> · {message}</span>
      ) : null}
    </div>
  );
}

function ConnectionRow({
  connection,
  sites,
}: {
  connection: AdsConnection;
  sites: string[];
}) {
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const test = useMutation({
    mutationFn: () => api.testAdsConnection(connection.id),
    onSuccess: refresh,
  });
  const toggle = useMutation({
    mutationFn: () =>
      api.updateAdsConnection(connection.id, { active: !connection.active }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteAdsConnection(connection.id),
    onSuccess: refresh,
  });
  const tested = test.data ?? null;
  return (
    <li
      data-testid="ads-connection"
      className="rounded-lg border border-[var(--border)] p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{connection.name}</span>
            {connection.site ? <Pill>{connection.site}</Pill> : null}
            {connection.active ? null : <Pill tone="warn">Đang tắt</Pill>}
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            Business ID {connection.business_id} · token {connection.token_hint}
            {connection.meta_name
              ? ` · trên Meta: ${connection.meta_name}`
              : ""}{" "}
            · {connection.account_count} tài khoản
          </div>
          {connection.last_tested_at ? (
            <div className="text-xs">
              <span
                className={
                  connection.last_test_ok
                    ? "text-[var(--good)]"
                    : "text-[var(--bad)]"
                }
              >
                {connection.last_test_ok ? "Kết nối được" : "Lỗi kết nối"}
              </span>
              <span className="text-[var(--text-muted)]">
                {" "}
                · {formatShortDateTime(connection.last_tested_at)}
                {connection.last_test_message
                  ? ` · ${connection.last_test_message}`
                  : ""}
              </span>
            </div>
          ) : (
            <div className="text-xs text-[var(--text-muted)]">
              Chưa kiểm tra kết nối
            </div>
          )}
          <SyncStatus
            label="Chi phí"
            at={connection.last_spend_sync_at}
            ok={connection.last_spend_sync_ok}
            message={connection.last_spend_sync_message}
          />
          <SyncStatus
            label="Hoá đơn"
            at={connection.last_charge_sync_at}
            ok={connection.last_charge_sync_ok}
            message={connection.last_charge_sync_message}
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => test.mutate()}
            disabled={test.isPending}
            className="min-h-11 rounded-lg bg-[var(--accent)] px-3 text-sm font-medium text-[var(--accent-text)] disabled:opacity-50"
          >
            {test.isPending ? "Đang kiểm tra…" : "Test kết nối"}
          </button>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className={SECONDARY}
          >
            Sửa
          </button>
          <ConfirmButton
            tone="secondary"
            ariaLabel={`${connection.active ? "Tắt" : "Bật"} ${connection.name}`}
            pending={toggle.isPending}
            error={toggle.error}
            onConfirm={() => toggle.mutate()}
            spec={{
              title: connection.active
                ? `Tắt BM “${connection.name}”?`
                : `Bật lại BM “${connection.name}”?`,
              description: connection.active
                ? "Hệ thống ngừng đồng bộ BM này; số liệu đã có vẫn giữ nguyên."
                : "BM được đồng bộ lại từ lượt kế tiếp.",
              confirmLabel: connection.active ? "Tắt BM" : "Bật BM",
            }}
          >
            {connection.active ? "Tắt" : "Bật"}
          </ConfirmButton>
          {connection.account_count === 0 ? (
            <ConfirmButton
              tone="danger"
              ariaLabel={`Xoá ${connection.name}`}
              pending={remove.isPending}
              error={remove.error}
              onConfirm={() => remove.mutate()}
              spec={{
                title: `Xoá BM “${connection.name}”?`,
                description:
                  "Xoá cấu hình và token của BM này. BM chưa có tài khoản nào nên không mất số liệu.",
                confirmLabel: "Xoá BM",
                variant: "destructive",
              }}
            >
              Xoá
            </ConfirmButton>
          ) : null}
        </div>
      </div>
      {tested ? (
        <p
          role="status"
          className={`mt-2 rounded-lg px-3 py-2 text-sm ${tested.ok ? "bg-[var(--good-soft)]" : "bg-[var(--bad-soft)]"}`}
        >
          {tested.message}
        </p>
      ) : null}
      {test.isError ? <NoticeBox error={test.error} /> : null}
      {editing ? (
        <EditConnection
          connection={connection}
          sites={sites}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </li>
  );
}

function ConnectionFields({
  draft,
  setDraft,
  sites,
  editing,
}: {
  draft: {
    name: string;
    business_id: string;
    site: string;
    token: string;
    note: string;
  };
  setDraft: (draft: {
    name: string;
    business_id: string;
    site: string;
    token: string;
    note: string;
  }) => void;
  sites: string[];
  editing: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="space-y-1 text-sm">
        <span className="text-[var(--text-muted)]">Tên BM</span>
        <input
          aria-label="Tên BM"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          placeholder="BM Apex Sài Gòn"
          className={FIELD}
        />
      </label>
      <label className="space-y-1 text-sm">
        <span className="text-[var(--text-muted)]">Business ID</span>
        <input
          aria-label="Business ID"
          inputMode="numeric"
          value={draft.business_id}
          onChange={(event) =>
            setDraft({ ...draft, business_id: event.target.value })
          }
          placeholder="Dãy số trong Business Settings"
          className={FIELD}
        />
      </label>
      <label className="space-y-1 text-sm">
        <span className="text-[var(--text-muted)]">Cơ sở</span>
        <input
          aria-label="Cơ sở"
          list="ads-sites"
          value={draft.site}
          onChange={(event) => setDraft({ ...draft, site: event.target.value })}
          placeholder="Sài Gòn, Hà Nội…"
          className={FIELD}
        />
        <datalist id="ads-sites">
          {sites.map((site) => (
            <option key={site} value={site} />
          ))}
        </datalist>
      </label>
      <label className="space-y-1 text-sm">
        <span className="text-[var(--text-muted)]">Token System User</span>
        <input
          aria-label="Token"
          type="password"
          autoComplete="off"
          value={draft.token}
          onChange={(event) =>
            setDraft({ ...draft, token: event.target.value })
          }
          placeholder={editing ? "Để trống = giữ token cũ" : "EAA…"}
          className={FIELD}
        />
      </label>
      <label className="space-y-1 text-sm sm:col-span-2">
        <span className="text-[var(--text-muted)]">Ghi chú</span>
        <input
          aria-label="Ghi chú"
          value={draft.note}
          onChange={(event) => setDraft({ ...draft, note: event.target.value })}
          className={FIELD}
        />
      </label>
    </div>
  );
}

const EMPTY = { name: "", business_id: "", site: "", token: "", note: "" };

function AddConnection({
  sites,
  disabled,
}: {
  sites: string[];
  disabled: boolean;
}) {
  const refresh = useRefresh();
  const [draft, setDraft] = useState(EMPTY);
  const add = useMutation({
    mutationFn: () =>
      api.createAdsConnection({ ...draft, note: draft.note || null }),
    onSuccess: () => {
      setDraft(EMPTY);
      refresh();
    },
  });
  const ready =
    draft.name.trim() && draft.business_id.trim() && draft.token.trim();
  return (
    <section aria-label="Thêm BM" className={SECTION}>
      <h2 className="mb-3 text-sm font-semibold">Thêm BM</h2>
      {add.isError ? (
        <NoticeBox error={add.error} onDismiss={() => add.reset()} />
      ) : null}
      <ConnectionFields
        draft={draft}
        setDraft={setDraft}
        sites={sites}
        editing={false}
      />
      <div className="mt-3">
        <button
          type="button"
          onClick={() => add.mutate()}
          disabled={disabled || !ready || add.isPending}
          className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] disabled:opacity-50"
        >
          {add.isPending ? "Đang thêm…" : "Thêm BM"}
        </button>
      </div>
    </section>
  );
}

function EditConnection({
  connection,
  sites,
  onClose,
}: {
  connection: AdsConnection;
  sites: string[];
  onClose: () => void;
}) {
  const refresh = useRefresh();
  const [draft, setDraft] = useState({
    name: connection.name,
    business_id: connection.business_id,
    site: connection.site,
    token: "",
    note: connection.note ?? "",
  });
  const save = useMutation({
    mutationFn: () => api.updateAdsConnection(connection.id, draft),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      title={`Sửa BM · ${connection.name}`}
      size="lg"
    >
      <ModalBody className="space-y-3">
        {save.isError ? <NoticeBox error={save.error} /> : null}
        <ConnectionFields
          draft={draft}
          setDraft={setDraft}
          sites={sites}
          editing
        />
      </ModalBody>
      <ModalFooter>
        <button
          type="button"
          onClick={onClose}
          disabled={save.isPending}
          className={SECONDARY}
        >
          Thôi
        </button>
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] disabled:opacity-50"
        >
          {save.isPending ? "Đang lưu…" : "Lưu"}
        </button>
      </ModalFooter>
    </Modal>
  );
}

function SyncPanel({ hasConnections }: { hasConnections: boolean }) {
  const [kind, setKind] = useState<"spend" | "charges">("spend");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const sync = useMutation({
    mutationFn: (range: boolean) =>
      api.adsSync(range ? { kind, since, until } : { kind }),
  });
  const label = kind === "spend" ? "chi phí" : "hoá đơn";
  return (
    <section aria-label="Đồng bộ" className={SECTION}>
      <h2 className="text-sm font-semibold">Đồng bộ</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Chạy ngay thay vì đợi lịch, hoặc kéo lại một khoảng ngày (tối đa 93
        ngày) để bù số.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Loại</span>
          <Select
            aria-label="Loại đồng bộ"
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as "spend" | "charges")
            }
          >
            <option value="spend">Chi phí (chi tiêu, mess, SĐT)</option>
            <option value="charges">Hoá đơn Meta</option>
          </Select>
        </label>
        <ConfirmButton
          tone="primary"
          disabled={!hasConnections}
          pending={sync.isPending}
          error={sync.error}
          onOpenChange={(open) => (open ? sync.reset() : undefined)}
          onConfirm={() => sync.mutate(false)}
          spec={{
            title: `Đồng bộ ${label} ngay?`,
            description:
              kind === "spend"
                ? "Kéo chi tiêu, mess, SĐT của hôm qua từ mọi BM đang bật, như lượt 08:00."
                : "Kéo hoá đơn Meta 5 ngày gần nhất từ mọi BM đang bật, như lượt 08:05.",
            confirmLabel: "Đồng bộ ngay",
          }}
        >
          Đồng bộ ngay
        </ConfirmButton>
        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Từ ngày</span>
          <DateInput
            value={since}
            onChange={(event) => setSince(event.target.value)}
            aria-label="Kéo từ ngày"
            className={`${FIELD} w-40`}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Đến ngày</span>
          <DateInput
            value={until}
            onChange={(event) => setUntil(event.target.value)}
            aria-label="Kéo đến ngày"
            className={`${FIELD} w-40`}
          />
        </label>
        <ConfirmButton
          tone="secondary"
          disabled={!hasConnections || !since || !until}
          pending={sync.isPending}
          error={sync.error}
          onOpenChange={(open) => (open ? sync.reset() : undefined)}
          onConfirm={() => sync.mutate(true)}
          spec={{
            title: `Kéo lại ${label} trong khoảng ngày đã chọn?`,
            description:
              "Số liệu các ngày trong khoảng được thay bằng số Meta trả về; ngày ngoài khoảng giữ nguyên.",
            confirmLabel: "Kéo lại",
          }}
        >
          Kéo lại khoảng ngày
        </ConfirmButton>
      </div>
      {sync.isSuccess ? (
        <p role="status" className="mt-2 text-sm text-[var(--good)]">
          Đã đưa vào hàng đợi. Theo dõi kết quả ở Tác vụ định kỳ.
        </p>
      ) : null}
    </section>
  );
}
