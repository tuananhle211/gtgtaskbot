"use client";

/**
 * "Kết nối page" (0102, CSKH MANAGE): the Pancake accounts (user tokens, 90
 * days), the POS Cake keys and their shops, connecting pages (a job with
 * progress), each page's marketer / labels / Chủ page & Nhân viên trực (who
 * sees it), and "Cài đặt hộp chat" (work hours, out-of-hours answer, order
 * statuses, how long a marketer keeps a repeated number).
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cskhApi,
  PAGE_COLOR_NAMES,
  PAGE_TONES,
  pageShort,
  pageTone,
  WEEKDAYS,
  type ConnectJob,
  type Connections,
  type ConnectionPage,
  type InboxSettings,
  type Label,
  type MemberItem,
} from "@/lib/cskh";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { ErrorLine, LabelChip, fieldClass, smallButton } from "@/components/cskh-common";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

const KEY = ["cskh", "connections"] as const;

export function CskhPages() {
  const client = useQueryClient();
  const data = useQuery({
    queryKey: KEY,
    queryFn: cskhApi.connections,
    refetchInterval: (query) => (query.state.data?.job?.running ? 3000 : 30000),
  });
  const [connectOpen, setConnectOpen] = useState(false);
  const refresh = () => void client.invalidateQueries({ queryKey: KEY });
  const syncAll = useMutation({
    mutationFn: () =>
      cskhApi.connectPages({ page_ids: null, label_ids: [], members: [], import_history: true }),
    onSuccess: refresh,
  });
  const meta = useMutation({ mutationFn: cskhApi.refreshPages, onSuccess: refresh });

  if (data.isLoading) return <Loading />;
  if (data.error || !data.data) return <ErrorBox error={data.error} onRetry={() => void data.refetch()} />;
  const c = data.data;
  const running = Boolean(c.job?.running);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="brand-eyebrow">Chăm sóc khách hàng</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Kết nối page</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {c.pages.filter((p) => p.connected).length}/{c.pages.length} page đang đọc qua Pancake ·{" "}
            {c.pos_shops.length} cửa hàng POS
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <SecondaryButton type="button" disabled={meta.isPending} onClick={() => meta.mutate()}>
            Đồng bộ thẻ &amp; nhân viên
          </SecondaryButton>
          <ConfirmButton
            spec={{
              title: "Đồng bộ toàn bộ page trên Pancake?",
              description:
                "Mọi page đang kích hoạt trên các tài khoản Pancake (trừ page có từ khoá loại trừ) được nối: page chưa có token sẽ được tạo token mới (token cũ trên Pancake hết hiệu lực), rồi tải 60 hội thoại gần nhất.",
              confirmLabel: "Đồng bộ toàn bộ",
            }}
            onConfirm={() => syncAll.mutate()}
            pending={syncAll.isPending}
            error={syncAll.error}
            disabled={running || c.pancake_accounts.length === 0}
            tone="primary"
          >
            Đồng bộ toàn bộ page
          </ConfirmButton>
        </div>
      </div>
      {!c.secret_ready ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Máy chủ chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được token Pancake / API key
          POS.
        </div>
      ) : null}
      {!c.qr_ready ? (
        <p className="text-xs text-[var(--text-muted)]">
          Máy chủ chưa có thư viện đọc mã QR: tắt nhận diện QR trong ảnh.
        </p>
      ) : null}
      <ErrorLine error={meta.error} />
      {c.job ? <JobCard job={c.job} /> : null}
      {c.new_pages ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--accent)]/30 bg-[var(--accent-soft)] p-3 text-sm">
          <span>
            <strong>{c.new_pages}</strong> page mới đang kích hoạt trên Pancake chưa nối vào hộp chat.
          </span>
          <button type="button" className={smallButton} onClick={() => setConnectOpen(true)}>
            Xem và kết nối
          </button>
        </div>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <PancakeAccounts c={c} onChanged={refresh} />
        <PosKeys c={c} onChanged={refresh} />
      </div>
      <PagesTable c={c} onChanged={refresh} onConnect={() => setConnectOpen(true)} running={running} />
      <SettingsForm settings={c.settings} onChanged={refresh} />
      {connectOpen ? <ConnectDialog c={c} onClose={() => setConnectOpen(false)} onStarted={refresh} /> : null}
    </div>
  );
}

function JobCard({ job }: { job: ConnectJob }) {
  const share = job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <section className="panel space-y-2 p-4 text-sm" data-testid="cskh-job">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{job.label || "Kết nối page"}</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {job.running
            ? `Đang chạy${job.current ? `: ${job.current}` : ""}`
            : job.stale
              ? "Đã dừng giữa chừng"
              : `Xong ${formatDateTime(job.finished_at)}`}
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-[var(--surface-muted)]"
        role="progressbar"
        aria-valuenow={share}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full rounded-full" style={{ width: `${share}%`, background: "var(--accent)" }} />
      </div>
      <p>
        {job.done}/{job.total} page · nối được {job.connected} · tạo {job.generated} token ·{" "}
        {job.conversations} hội thoại
        {job.pos_on
          ? ` · POS Cake: ${job.pos_linked}/${job.connected} page có cửa hàng`
          : " · POS Cake: chưa khai báo key"}
      </p>
      {job.pos_missing.length ? (
        <p className="text-xs text-[var(--warn)]">Thiếu cửa hàng POS: {job.pos_missing.join(", ")}</p>
      ) : null}
      {job.errors.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-[var(--bad)]">{job.errors.length} lỗi</summary>
          <ul className="mt-1 list-disc pl-5">
            {job.errors.map((error, index) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function PancakeAccounts({ c, onChanged }: { c: Connections; onChanged: () => void }) {
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const [expires, setExpires] = useState("");
  const [editing, setEditing] = useState<Connections["pancake_accounts"][number] | null>(null);
  const add = useMutation({
    mutationFn: () =>
      cskhApi.addPancakeAccount({
        label: label.trim() || undefined,
        token: token.trim(),
        expires_on: expires || null,
      }),
    onSuccess: () => {
      setLabel("");
      setToken("");
      setExpires("");
      onChanged();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => cskhApi.removePancakeAccount(id),
    onSuccess: onChanged,
  });
  return (
    <section className="panel space-y-3 p-4">
      <div>
        <h2 className="text-base font-semibold">1 · Tài khoản Pancake</h2>
        <p className="text-xs text-[var(--text-muted)]">
          pancake.vn › Tài khoản › Cài đặt cá nhân › API Access Token. Token hết hạn sau 90 ngày. Nhiều tài
          khoản: page trùng tự lọc, tài khoản trên cùng được ưu tiên.
        </p>
      </div>
      <ul className="space-y-2">
        {c.pancake_accounts.map((account) => (
          <li
            key={account.id}
            className="rounded-lg border border-[var(--border)] p-2 text-sm"
            data-testid="cskh-pancake-account"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">
                {account.label} <span className="text-xs text-[var(--text-muted)]">{account.token_hint}</span>
              </span>
              <span className="flex gap-1">
                <button type="button" className={smallButton} onClick={() => setEditing(account)}>
                  Sửa
                </button>
                <ConfirmButton
                  spec={{
                    title: `Gỡ tài khoản Pancake ${account.label}?`,
                    description:
                      "Page đã nối vẫn đọc tin bằng token page của nó; chỉ không tìm / nối page mới bằng tài khoản này nữa.",
                    confirmLabel: "Gỡ tài khoản",
                    variant: "destructive",
                  }}
                  onConfirm={() => remove.mutate(account.id)}
                  pending={remove.isPending}
                  tone="danger"
                  className={smallButton}
                >
                  Gỡ
                </ConfirmButton>
              </span>
            </div>
            <p className="text-xs text-[var(--text-muted)]">
              {account.page_count} page ({account.active_count} đang kích hoạt)
              {account.expires_on ? ` · hết hạn ${formatDate(account.expires_on)}` : ""}
              {account.checked_at ? ` · kiểm tra ${formatDateTime(account.checked_at)}` : ""}
            </p>
            {account.last_error ? <p className="text-xs text-[var(--bad)]">{account.last_error}</p> : null}
          </li>
        ))}
      </ul>
      <form
        className="grid gap-2 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (token.trim()) add.mutate();
        }}
      >
        <input
          className={fieldClass}
          placeholder="Tên gợi nhớ (vd Tài khoản Ngô)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          aria-label="Tên tài khoản Pancake"
        />
        <DateInput
          value={expires}
          onChange={(e) => setExpires(e.target.value)}
          aria-label="Ngày hết hạn token"
          className={fieldClass}
        />
        <input
          className={`${fieldClass} sm:col-span-2`}
          placeholder="Dán API Access Token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          aria-label="Token Pancake"
          autoComplete="off"
        />
        <div className="sm:col-span-2">
          <PrimaryButton type="submit" disabled={!token.trim() || add.isPending}>
            {add.isPending ? "Đang kiểm tra…" : "Thêm tài khoản"}
          </PrimaryButton>
        </div>
      </form>
      <ErrorLine error={add.error ?? remove.error} />
      {editing ? (
        <EditAccount account={editing} onClose={() => setEditing(null)} onChanged={onChanged} />
      ) : null}
    </section>
  );
}

function EditAccount({
  account,
  onClose,
  onChanged,
}: {
  account: Connections["pancake_accounts"][number];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [label, setLabel] = useState(account.label);
  const [token, setToken] = useState("");
  const [expires, setExpires] = useState(account.expires_on?.slice(0, 10) ?? "");
  const save = useMutation({
    mutationFn: () =>
      cskhApi.updatePancakeAccount(account.id, {
        label: label.trim(),
        expires_on: expires || null,
        ...(token.trim() ? { token: token.trim() } : {}),
      }),
    onSuccess: () => {
      onChanged();
      onClose();
    },
  });
  return (
    <Modal open onClose={onClose} title={`Sửa ${account.label}`} busy={save.isPending}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Tên gợi nhớ</span>
            <input className={fieldClass} value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Ngày hết hạn token</span>
            <DateInput value={expires} onChange={(e) => setExpires(e.target.value)} className={fieldClass} />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Token mới (để trống = giữ token cũ)</span>
            <input
              className={fieldClass}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
            />
          </label>
          <ErrorLine error={save.error} />
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose}>
            Huỷ
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={save.isPending}>
            Lưu
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

function PosKeys({ c, onChanged }: { c: Connections; onChanged: () => void }) {
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const add = useMutation({
    mutationFn: () => cskhApi.addPosKey({ label: label.trim() || undefined, key: key.trim() }),
    onSuccess: () => {
      setLabel("");
      setKey("");
      onChanged();
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => cskhApi.removePosKey(id), onSuccess: onChanged });
  const reload = useMutation({ mutationFn: cskhApi.reloadPos, onSuccess: onChanged });
  return (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">2 · Pancake POS (POS Cake)</h2>
          <p className="text-xs text-[var(--text-muted)]">
            POS Cake › Cài đặt › Ứng dụng › Webhook/API › API Key. Mỗi page tự tìm cửa hàng của nó để xác nhận
            đơn.
          </p>
        </div>
        {c.pos_keys.length ? (
          <button
            type="button"
            className={smallButton}
            disabled={reload.isPending}
            onClick={() => reload.mutate()}
          >
            Tải lại cửa hàng
          </button>
        ) : null}
      </div>
      <ul className="space-y-2">
        {c.pos_keys.map((posKey) => (
          <li
            key={posKey.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--border)] p-2 text-sm"
          >
            <span>
              <span className="font-medium">{posKey.label}</span>{" "}
              <span className="text-xs text-[var(--text-muted)]">
                {posKey.key_hint} · {posKey.shop_count} cửa hàng
              </span>
              {posKey.last_error ? (
                <span className="block text-xs text-[var(--bad)]">{posKey.last_error}</span>
              ) : null}
            </span>
            <ConfirmButton
              spec={{
                title: `Gỡ API key ${posKey.label}?`,
                description:
                  "Các cửa hàng chỉ key này thấy sẽ không còn dùng để xác nhận đơn và kiểm trùng số.",
                confirmLabel: "Gỡ key",
                variant: "destructive",
              }}
              onConfirm={() => remove.mutate(posKey.id)}
              pending={remove.isPending}
              tone="danger"
              className={smallButton}
            >
              Gỡ
            </ConfirmButton>
          </li>
        ))}
      </ul>
      {c.pos_shops.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer">{c.pos_shops.length} cửa hàng</summary>
          <ul className="mt-1 space-y-0.5">
            {c.pos_shops.map((shop) => (
              <li key={shop.id}>
                {shop.name} · {shop.pages} page{shop.dup_check ? "" : " · không dùng để kiểm trùng"}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <form
        className="grid gap-2 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (key.trim()) add.mutate();
        }}
      >
        <input
          className={fieldClass}
          placeholder="Tên gợi nhớ"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          aria-label="Tên API key POS"
        />
        <input
          className={fieldClass}
          placeholder="Dán API key POS"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          aria-label="API key POS"
          autoComplete="off"
        />
        <div className="sm:col-span-2">
          <SecondaryButton type="submit" disabled={!key.trim() || add.isPending}>
            {add.isPending ? "Đang kiểm tra…" : "Thêm key"}
          </SecondaryButton>
        </div>
      </form>
      <ErrorLine error={add.error ?? remove.error ?? reload.error} />
    </section>
  );
}

function MembersPicker({
  people,
  value,
  onChange,
}: {
  people: Connections["people"];
  value: MemberItem[];
  onChange: (value: MemberItem[]) => void;
}) {
  const [person, setPerson] = useState("");
  const [role, setRole] = useState<MemberItem["role"]>("STAFF");
  const name = (id: string) => people.find((p) => p.user_id === id)?.name ?? id;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {value.map((member) => (
          <span
            key={member.user_id}
            className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs"
          >
            {member.role === "OWNER" ? "Chủ page" : "Trực"}: {name(member.user_id)}
            <button
              type="button"
              aria-label={`Bỏ ${name(member.user_id)}`}
              onClick={() => onChange(value.filter((m) => m.user_id !== member.user_id))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Select aria-label="Chọn người" value={person} onChange={(e) => setPerson(e.target.value)}>
          <option value="">Chọn người…</option>
          {people.map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.name}
              {p.member_code ? ` (${p.member_code})` : ""}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Vai trò"
          value={role}
          onChange={(e) => setRole(e.target.value as MemberItem["role"])}
        >
          <option value="STAFF">Nhân viên trực</option>
          <option value="OWNER">Chủ page</option>
        </Select>
        <button
          type="button"
          className={smallButton}
          disabled={!person}
          onClick={() => {
            onChange([...value.filter((m) => m.user_id !== person), { user_id: person, role }]);
            setPerson("");
          }}
        >
          + Thêm
        </button>
      </div>
    </div>
  );
}

function LabelsPicker({
  labels,
  value,
  onChange,
}: {
  labels: Label[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {labels.length === 0 ? (
        <span className="text-xs text-[var(--text-muted)]">Chưa có nhãn (tạo ở Nhãn &amp; câu mẫu).</span>
      ) : null}
      {labels.map((label) => {
        const on = value.includes(label.id);
        return (
          <button
            key={label.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((id) => id !== label.id) : [...value, label.id])}
            className={`rounded-full ${on ? "ring-2 ring-[var(--accent)]" : "opacity-70"}`}
          >
            <LabelChip label={label} />
          </button>
        );
      })}
    </div>
  );
}

function ConnectDialog({
  c,
  onClose,
  onStarted,
}: {
  c: Connections;
  onClose: () => void;
  onStarted: () => void;
}) {
  const listing = useQuery({ queryKey: ["cskh", "pancake-pages"], queryFn: cskhApi.pancakePages });
  const [picked, setPicked] = useState<string[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [members, setMembers] = useState<MemberItem[]>([]);
  const [history, setHistory] = useState(true);
  const [filter, setFilter] = useState("");
  const connect = useMutation({
    mutationFn: () =>
      cskhApi.connectPages({ page_ids: picked, label_ids: labels, members, import_history: history }),
    onSuccess: () => {
      onStarted();
      onClose();
    },
  });
  const pages = (listing.data?.pages ?? []).filter(
    (p) => !filter || `${p.name} ${p.id} ${p.username}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const ready = (p: (typeof pages)[number]) => p.status === "activated" && !p.connected && !p.skipped;
  return (
    <Modal open onClose={onClose} title="Kết nối page từ Pancake" size="xl" busy={connect.isPending}>
      <ModalBody className="space-y-3">
        <input
          className={fieldClass}
          placeholder="Lọc theo tên, ID hoặc link page"
          value={filter}
          onChange={(e) =>
            setFilter(e.target.value.replace(/^https?:\/\/(www\.)?facebook\.com\//, "").replace(/\/$/, ""))
          }
          aria-label="Lọc page Pancake"
        />
        {listing.isLoading ? <Loading /> : null}
        {listing.error ? <ErrorBox error={listing.error} /> : null}
        <ul className="max-h-72 space-y-1 overflow-y-auto" data-testid="cskh-pancake-listing">
          {pages.map((page) => {
            const status = page.connected
              ? "Đã nối"
              : page.status !== "activated"
                ? "Chưa kích hoạt trên Pancake"
                : page.skipped
                  ? "Bị loại trừ"
                  : "Sẵn sàng";
            return (
              <li key={page.id}>
                <label
                  className={`flex items-center gap-2 rounded-lg border border-[var(--border)] p-2 text-sm ${ready(page) ? "" : "opacity-60"}`}
                >
                  <input
                    type="checkbox"
                    disabled={!ready(page)}
                    checked={picked.includes(page.id)}
                    onChange={(e) =>
                      setPicked((ids) =>
                        e.target.checked ? [...ids, page.id] : ids.filter((x) => x !== page.id),
                      )
                    }
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{page.name}</span>
                    <span className="block truncate text-xs text-[var(--text-muted)]">
                      {page.id} · {page.account_label}
                    </span>
                  </span>
                  <span className="text-xs">{status}</span>
                </label>
              </li>
            );
          })}
        </ul>
        <div className="space-y-1">
          <p className="text-sm font-medium">Nhãn gắn cho page</p>
          <LabelsPicker labels={c.labels} value={labels} onChange={setLabels} />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-medium">Chủ page và nhân viên trực (được xem page trong hộp chat)</p>
          <MembersPicker people={c.people} value={members} onChange={setMembers} />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} />
          Tải 60 hội thoại gần nhất của mỗi page
        </label>
        <p className="text-xs text-[var(--text-muted)]">
          Page chưa có token sẽ được tạo token mới trên Pancake (token cũ hết hiệu lực). Pancake không cho
          thêm / kích hoạt page qua API: làm trên Pancake trước.
        </p>
        <ErrorLine error={connect.error} />
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: `Kết nối ${picked.length} page?`,
            description:
              "Mỗi page được tạo token Pancake (nếu chưa có), gắn nhãn / người trực đã chọn rồi tải hội thoại; chạy ngầm, có thanh tiến độ.",
            confirmLabel: "Kết nối",
            count: picked.length,
          }}
          onConfirm={() => connect.mutate()}
          pending={connect.isPending}
          error={connect.error}
          disabled={picked.length === 0}
          tone="primary"
        >
          Kết nối {picked.length ? `(${picked.length})` : ""}
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function PagesTable({
  c,
  onChanged,
  onConnect,
  running,
}: {
  c: Connections;
  onChanged: () => void;
  onConnect: () => void;
  running: boolean;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [bulk, setBulk] = useState<null | "marketer" | "labels" | "members">(null);
  const update = useMutation({
    mutationFn: (body: Parameters<typeof cskhApi.updatePages>[0]) => cskhApi.updatePages(body),
    onSuccess: onChanged,
  });
  const disconnect = useMutation({
    mutationFn: (id: string) => cskhApi.disconnectPage(id),
    onSuccess: onChanged,
  });
  const labelOf = useMemo(() => new Map(c.labels.map((label) => [label.id, label])), [c.labels]);
  const pages = c.pages.filter(
    (p) =>
      !filter || `${p.name} ${p.id} ${p.marketer_name ?? ""}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const allPicked = pages.length > 0 && pages.every((p) => picked.includes(p.id));
  return (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">3 · Page trong hộp chat</h2>
        <div className="flex flex-wrap gap-2">
          <input
            className={`${fieldClass} w-56`}
            placeholder="Lọc page"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Lọc page"
          />
          <SecondaryButton
            type="button"
            onClick={onConnect}
            disabled={running || c.pancake_accounts.length === 0}
          >
            + Tạo kết nối
          </SecondaryButton>
        </div>
      </div>
      {picked.length ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-[var(--surface-muted)] p-2 text-sm">
          <span>{picked.length} page đã chọn</span>
          <button type="button" className={smallButton} onClick={() => setBulk("marketer")}>
            Gán marketing
          </button>
          <button type="button" className={smallButton} onClick={() => setBulk("labels")}>
            Gắn nhãn
          </button>
          <button type="button" className={smallButton} onClick={() => setBulk("members")}>
            Thêm người trực
          </button>
          <button type="button" className={smallButton} onClick={() => setPicked([])}>
            Bỏ chọn
          </button>
        </div>
      ) : null}
      <div className="table-scroll-x">
        <table className="table-dense w-full">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Chọn tất cả page"
                  checked={allPicked}
                  onChange={(e) => setPicked(e.target.checked ? pages.map((p) => p.id) : [])}
                />
              </th>
              <th>Page</th>
              <th>Trạng thái</th>
              <th>Marketing</th>
              <th>Nhãn</th>
              <th>Chủ page / Người trực</th>
              <th>POS Cake</th>
              <th>Hội thoại</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pages.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-sm text-[var(--text-muted)]">
                  Chưa có page nào. Thêm tài khoản Pancake rồi bấm “Đồng bộ toàn bộ page” hoặc “+ Tạo kết
                  nối”.
                </td>
              </tr>
            ) : null}
            {pages.map((page) => (
              <PageRow
                key={page.id}
                page={page}
                c={c}
                picked={picked.includes(page.id)}
                onPick={(on) =>
                  setPicked((ids) => (on ? [...ids, page.id] : ids.filter((x) => x !== page.id)))
                }
                labelOf={labelOf}
                onUpdate={(body) => update.mutate({ page_ids: [page.id], ...body })}
                onDisconnect={() => disconnect.mutate(page.id)}
                pending={update.isPending || disconnect.isPending}
              />
            ))}
          </tbody>
        </table>
      </div>
      <ErrorLine error={update.error ?? disconnect.error} />
      {bulk ? (
        <BulkDialog
          kind={bulk}
          c={c}
          count={picked.length}
          onClose={() => setBulk(null)}
          onApply={(body) =>
            update.mutate(
              { page_ids: picked, ...body },
              {
                onSuccess: () => {
                  setBulk(null);
                  setPicked([]);
                },
              },
            )
          }
          pending={update.isPending}
          error={update.error}
        />
      ) : null}
    </section>
  );
}

type PageUpdate = Omit<Parameters<typeof cskhApi.updatePages>[0], "page_ids">;

function PageRow({
  page,
  c,
  picked,
  onPick,
  labelOf,
  onUpdate,
  onDisconnect,
  pending,
}: {
  page: ConnectionPage;
  c: Connections;
  picked: boolean;
  onPick: (on: boolean) => void;
  labelOf: Map<string, Label>;
  onUpdate: (body: PageUpdate) => void;
  onDisconnect: () => void;
  pending: boolean;
}) {
  const [members, setMembers] = useState(false);
  const [look, setLook] = useState(false);
  const [fill, ink] = pageTone(page.id, page.color);
  return (
    <tr data-testid="cskh-page-row">
      <td>
        <input
          type="checkbox"
          aria-label={`Chọn ${page.name}`}
          checked={picked}
          onChange={(e) => onPick(e.target.checked)}
        />
      </td>
      <td>
        <p className="font-medium">{page.name}</p>
        <p className="text-xs text-[var(--text-muted)]">
          {page.id}
          {page.skipped ? " · bị loại trừ" : ""}
        </p>
        <button
          type="button"
          onClick={() => setLook(true)}
          className="mt-1 inline-flex items-center gap-1 text-xs"
          aria-label={`Nhãn ngắn ${page.name}`}
          title="Tên ngắn và màu hiện trong hộp chat"
        >
          <span
            className="rounded-md px-1.5 py-px font-semibold"
            style={{ background: fill, color: ink }}
          >
            {pageShort(page.name, page.short_name)}
          </span>
          <span className="text-[var(--text-muted)] underline-offset-2 hover:underline">
            {page.short_name ? "Sửa" : "Đặt tên ngắn"}
          </span>
        </button>
        {look ? (
          <PageLookDialog
            page={page}
            onClose={() => setLook(false)}
            onSave={(value) => {
              onUpdate(value);
              setLook(false);
            }}
          />
        ) : null}
      </td>
      <td>
        <span className={`st ${page.connected ? "st-green" : page.token_hint ? "st-amber" : "st-slate"}`}>
          {page.connected ? "Đang đọc" : page.token_hint ? "Đang tắt" : "Chưa nối"}
        </span>
        {page.poll_error ? <p className="max-w-48 text-xs text-[var(--bad)]">{page.poll_error}</p> : null}
        {page.polled_at ? (
          <p className="text-xs text-[var(--text-muted)]">Đọc {formatDateTime(page.polled_at)}</p>
        ) : null}
      </td>
      <td>
        <Select
          aria-label={`Marketing ${page.name}`}
          value={page.marketer_user_id ?? ""}
          disabled={pending}
          onChange={(e) => onUpdate({ marketer_user_id: e.target.value || null })}
        >
          <option value="">Chưa gán</option>
          {c.people.map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.name}
            </option>
          ))}
        </Select>
      </td>
      <td>
        <div className="flex max-w-56 flex-wrap gap-1">
          {page.label_ids.map((id) =>
            labelOf.get(id) ? <LabelChip key={id} label={labelOf.get(id)!} /> : null,
          )}
        </div>
      </td>
      <td>
        <button
          type="button"
          className="text-left text-xs underline-offset-2 hover:underline"
          onClick={() => setMembers(true)}
        >
          {page.members.length
            ? page.members.map((m) => `${m.role === "OWNER" ? "★ " : ""}${m.name}`).join(", ")
            : "Chưa có (bấm để thêm)"}
        </button>
        {members ? (
          <MembersDialog
            page={page}
            c={c}
            onClose={() => setMembers(false)}
            onSave={(value) => {
              onUpdate({ members: value, member_mode: "set" });
              setMembers(false);
            }}
          />
        ) : null}
      </td>
      <td className="text-xs">{page.pos_shop_name ?? <span className="text-[var(--warn)]">Thiếu</span>}</td>
      <td className="tabular-nums">
        {page.conversations}
        {page.unread ? <span className="text-[var(--magenta)]"> · {page.unread} chưa đọc</span> : null}
      </td>
      <td>
        <div className="flex flex-wrap gap-1">
          {page.token_hint ? (
            <button
              type="button"
              className={smallButton}
              disabled={pending}
              onClick={() => onUpdate({ active: !page.active })}
            >
              {page.active ? "Tắt" : "Bật"}
            </button>
          ) : null}
          {page.token_hint ? (
            <ConfirmButton
              spec={{
                title: `Ngắt kết nối page ${page.name}?`,
                description:
                  "Xoá token Pancake của page trong hộp chat và ngừng đọc tin. Hội thoại cũ vẫn giữ; nối lại bằng + Tạo kết nối.",
                confirmLabel: "Ngắt kết nối",
                variant: "destructive",
              }}
              onConfirm={onDisconnect}
              pending={pending}
              tone="danger"
              className={smallButton}
            >
              Ngắt
            </ConfirmButton>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/** A page's short label on the inbox ("Tiểu Eva") and its colour. */
function PageLookDialog({
  page,
  onClose,
  onSave,
}: {
  page: ConnectionPage;
  onClose: () => void;
  onSave: (value: { short_name: string; color: string }) => void;
}) {
  const [short, setShort] = useState(page.short_name ?? "");
  const [color, setColor] = useState(page.color ?? "");
  const [fill, ink] = pageTone(page.id, color);
  return (
    <Modal open onClose={onClose} title="Nhãn ngắn của page">
      <ModalBody className="space-y-3 text-sm">
        <p className="text-[var(--text-muted)]">{page.name}</p>
        <label className="block space-y-1">
          <span className="font-medium">Tên ngắn (tối đa 24 ký tự)</span>
          <input
            className={fieldClass}
            value={short}
            maxLength={24}
            onChange={(e) => setShort(e.target.value)}
            placeholder={pageShort(page.name)}
          />
        </label>
        <fieldset className="space-y-1">
          <legend className="font-medium">Màu</legend>
          <div className="flex flex-wrap gap-2">
            {[["", "Tự chọn"], ...Object.entries(PAGE_COLOR_NAMES)].map(([key, label]) => {
              const [bg, fg] = key ? PAGE_TONES[key] : ["var(--surface)", "var(--text)"];
              return (
                <label
                  key={key || "auto"}
                  className={`inline-flex min-h-9 cursor-pointer items-center rounded-lg border px-2.5 text-xs font-semibold ${
                    color === key ? "border-[var(--accent)] shadow-[0_0_0_2px_var(--ring)]" : "border-[var(--border)]"
                  }`}
                  style={{ background: bg, color: fg }}
                >
                  <input
                    type="radio"
                    name="page-color"
                    className="sr-only"
                    checked={color === key}
                    onChange={() => setColor(key)}
                  />
                  {label}
                </label>
              );
            })}
          </div>
        </fieldset>
        <p className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          Xem trước:
          <span className="rounded-md px-1.5 py-px font-semibold" style={{ background: fill, color: ink }}>
            {pageShort(page.name, short.trim())}
          </span>
        </p>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <PrimaryButton type="button" onClick={() => onSave({ short_name: short.trim(), color })}>
          Lưu
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}

function MembersDialog({
  page,
  c,
  onClose,
  onSave,
}: {
  page: ConnectionPage;
  c: Connections;
  onClose: () => void;
  onSave: (value: MemberItem[]) => void;
}) {
  const [value, setValue] = useState<MemberItem[]>(
    page.members.map((m) => ({ user_id: m.user_id, role: m.role })),
  );
  return (
    <Modal
      open
      onClose={onClose}
      title={`Người của page ${page.name}`}
      description="Chủ page và nhân viên trực thấy page này trong Hộp chat (quyền INBOX OWN)."
    >
      <ModalBody>
        <MembersPicker people={c.people} value={value} onChange={setValue} />
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <PrimaryButton type="button" onClick={() => onSave(value)}>
          Lưu
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}

function BulkDialog({
  kind,
  c,
  count,
  onClose,
  onApply,
  pending,
  error,
}: {
  kind: "marketer" | "labels" | "members";
  c: Connections;
  count: number;
  onClose: () => void;
  onApply: (body: PageUpdate) => void;
  pending: boolean;
  error: unknown;
}) {
  const [marketer, setMarketer] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [labelMode, setLabelMode] = useState<"add" | "remove" | "set">("add");
  const [members, setMembers] = useState<MemberItem[]>([]);
  const [memberMode, setMemberMode] = useState<"add" | "remove">("add");
  const titles = { marketer: "Gán marketing", labels: "Gắn nhãn", members: "Người trực" } as const;
  const body: PageUpdate =
    kind === "marketer"
      ? { marketer_user_id: marketer || null }
      : kind === "labels"
        ? { label_ids: labels, label_mode: labelMode }
        : { members, member_mode: memberMode };
  return (
    <Modal open onClose={onClose} title={`${titles[kind]} cho ${count} page`} busy={pending}>
      <ModalBody className="space-y-3">
        {kind === "marketer" ? (
          <Select
            aria-label="Marketing"
            value={marketer}
            onChange={(e) => setMarketer(e.target.value)}
            className="w-full"
          >
            <option value="">Bỏ gán</option>
            {c.people.map((p) => (
              <option key={p.user_id} value={p.user_id}>
                {p.name}
              </option>
            ))}
          </Select>
        ) : null}
        {kind === "labels" ? (
          <>
            <Select
              aria-label="Cách gắn nhãn"
              value={labelMode}
              onChange={(e) => setLabelMode(e.target.value as typeof labelMode)}
            >
              <option value="add">Thêm các nhãn này</option>
              <option value="remove">Gỡ các nhãn này</option>
              <option value="set">Thay toàn bộ nhãn bằng các nhãn này</option>
            </Select>
            <LabelsPicker labels={c.labels} value={labels} onChange={setLabels} />
          </>
        ) : null}
        {kind === "members" ? (
          <>
            <Select
              aria-label="Cách gắn người"
              value={memberMode}
              onChange={(e) => setMemberMode(e.target.value as typeof memberMode)}
            >
              <option value="add">Thêm những người này</option>
              <option value="remove">Gỡ những người này</option>
            </Select>
            <MembersPicker people={c.people} value={members} onChange={setMembers} />
          </>
        ) : null}
        <ErrorLine error={error} />
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <PrimaryButton type="button" disabled={pending} onClick={() => onApply(body)}>
          Áp dụng
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}

function SettingsForm({ settings, onChanged }: { settings: InboxSettings; onChanged: () => void }) {
  const [form, setForm] = useState({
    ...settings,
    order_statuses_text: settings.order_statuses.join("\n"),
    skip_text: settings.skip_keywords.join("\n"),
    dup_skip_text: settings.pos_dup_skip_keywords.join("\n"),
  });
  const lines = (text: string) =>
    text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  const save = useMutation({
    mutationFn: () =>
      cskhApi.saveSettings({
        auto_reply_enabled: form.auto_reply_enabled,
        work_start: form.work_start,
        work_end: form.work_end,
        work_days: form.work_days,
        auto_reply_text: form.auto_reply_text,
        order_statuses: lines(form.order_statuses_text),
        mkt_lock_days: Number(form.mkt_lock_days) || 0,
        wait_alert_minutes: Number(form.wait_alert_minutes) || 8,
        skip_keywords: lines(form.skip_text),
        pos_dup_skip_keywords: lines(form.dup_skip_text),
      }),
    onSuccess: onChanged,
  });
  return (
    <section className="panel space-y-3 p-4" aria-label="Cài đặt hộp chat">
      <h2 className="text-base font-semibold">Cài đặt hộp chat</h2>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            checked={form.auto_reply_enabled}
            onChange={(e) => setForm({ ...form, auto_reply_enabled: e.target.checked })}
          />
          Tự động trả lời ngoài giờ (mỗi khách tối đa 1 tin / 12 giờ, hội thoại gắn “Chờ gọi lại”)
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Giờ bắt đầu</span>
          <input
            className={fieldClass}
            value={form.work_start}
            onChange={(e) => setForm({ ...form, work_start: e.target.value })}
            placeholder="08:00"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Giờ kết thúc</span>
          <input
            className={fieldClass}
            value={form.work_end}
            onChange={(e) => setForm({ ...form, work_end: e.target.value })}
            placeholder="21:00"
          />
        </label>
        <fieldset className="space-y-1 text-sm sm:col-span-2">
          <legend className="font-medium">Ngày làm việc</legend>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((day, index) => (
              <label key={day} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={form.work_days.includes(index)}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      work_days: e.target.checked
                        ? [...form.work_days, index].sort()
                        : form.work_days.filter((d) => d !== index),
                    })
                  }
                />
                {day}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="font-medium">Tin tự động ngoài giờ</span>
          <textarea
            className={`${fieldClass} py-2`}
            rows={3}
            value={form.auto_reply_text}
            onChange={(e) => setForm({ ...form, auto_reply_text: e.target.value })}
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Trạng thái đơn (mỗi dòng một trạng thái)</span>
          <textarea
            className={`${fieldClass} py-2`}
            rows={5}
            value={form.order_statuses_text}
            onChange={(e) => setForm({ ...form, order_statuses_text: e.target.value })}
          />
        </label>
        <div className="space-y-3">
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Số ngày giữ marketing cũ khi trùng số</span>
            <input
              className={fieldClass}
              type="number"
              min={0}
              max={3650}
              value={form.mkt_lock_days}
              onChange={(e) => setForm({ ...form, mkt_lock_days: Number(e.target.value) })}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Khách chờ quá bao nhiêu phút thì báo đỏ</span>
            <input
              className={fieldClass}
              type="number"
              min={1}
              max={240}
              value={form.wait_alert_minutes ?? 8}
              onChange={(e) => setForm({ ...form, wait_alert_minutes: Number(e.target.value) })}
              aria-label="Ngưỡng khách chờ (phút)"
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Từ khoá loại page (không thuộc Apexmed)</span>
            <textarea
              className={`${fieldClass} py-2`}
              rows={2}
              value={form.skip_text}
              onChange={(e) => setForm({ ...form, skip_text: e.target.value })}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Cửa hàng POS không dùng kiểm trùng (từ khoá)</span>
            <textarea
              className={`${fieldClass} py-2`}
              rows={2}
              value={form.dup_skip_text}
              onChange={(e) => setForm({ ...form, dup_skip_text: e.target.value })}
            />
          </label>
        </div>
        <div className="sm:col-span-2">
          <PrimaryButton type="submit" disabled={save.isPending}>
            {save.isPending ? "Đang lưu…" : "Lưu cài đặt"}
          </PrimaryButton>
          {save.isSuccess ? <span className="ml-2 text-sm text-[var(--good)]">Đã lưu.</span> : null}
        </div>
      </form>
      <ErrorLine error={save.error} />
    </section>
  );
}
