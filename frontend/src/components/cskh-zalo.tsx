"use client";

/**
 * Zalo telesale (0102): "Hộp chat Zalo" (the nicks' threads, read only, matched
 * to the page conversation whose side shows on the right - phones masked when
 * matched by the customer code - with a box to answer the customer ON THE PAGE)
 * and "Kết nối Zalo" (the nicks, their telesale, the QR login).
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cskhApi,
  MAX_MESSAGE_CHARS,
  safeUrl,
  type QrState,
  type ZaloAccount,
  type ZaloThread,
} from "@/lib/cskh";
import { formatDateTime, formatShortDateTime } from "@/lib/datetime";
import {
  ColumnHead,
  ErrorLine,
  Initials,
  MessageBubble,
  fieldClass,
  smallButton,
} from "@/components/cskh-common";
import { ZaloMessages } from "@/components/cskh-inbox";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

const POLL_MS = 4000;
type Tab = "all" | "unread" | "unlinked";

// --- Hộp chat Zalo -----------------------------------------------------------------------------

export function CskhZaloInbox() {
  const router = useRouter();
  const params = useSearchParams();
  const openId = params.get("t");
  const [tab, setTab] = useState<Tab>("all");
  const [accountId, setAccountId] = useState("");
  const [saleId, setSaleId] = useState("");
  const [term, setTerm] = useState("");
  const [q, setQ] = useState("");

  const accounts = useQuery({ queryKey: ["cskh", "zalo-accounts"], queryFn: cskhApi.zaloAccounts });
  const threads = useQuery({
    queryKey: ["cskh", "zalo-threads", tab, accountId, saleId, q],
    queryFn: () =>
      cskhApi.zaloThreads({
        tab,
        account_id: accountId || undefined,
        user_id: saleId || undefined,
        q: q || undefined,
      }),
    refetchInterval: POLL_MS,
  });
  const open = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("t", id);
    else next.delete("t");
    router.replace(`/cskh/zalo${next.toString() ? `?${next}` : ""}`, { scroll: false });
  };
  const sales = useMemo(() => {
    const map = new Map<string, string>();
    for (const account of accounts.data?.accounts ?? [])
      if (account.user_id && account.sale_name) map.set(account.user_id, account.sale_name);
    return [...map.entries()];
  }, [accounts.data]);

  return (
    <div className="space-y-3">
      <div>
        <p className="brand-eyebrow">Chăm sóc khách hàng</p>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Hộp chat Zalo</h1>
        <p className="text-xs text-[var(--text-muted)]">
          Chỉ xem tin Zalo. Trả lời khách trên Zalo bằng điện thoại; ô bên phải gửi tin cho khách trên page.
        </p>
      </div>
      <div className="grid gap-3 lg:h-[calc(100vh-12rem)] lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[300px_minmax(0,1fr)_380px]">
        <section
          className={`panel flex min-h-0 flex-col overflow-hidden ${openId ? "hidden lg:flex" : "flex"}`}
        >
          <div className="space-y-2 border-b border-[var(--border)] p-3">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setQ(term.trim());
              }}
            >
              <input
                type="search"
                value={term}
                onChange={(event) => {
                  setTerm(event.target.value);
                  if (!event.target.value) setQ("");
                }}
                placeholder="Tìm mã KH, SĐT, tên Zalo, nội dung…"
                aria-label="Tìm hội thoại Zalo"
                className={fieldClass}
              />
            </form>
            <div className="grid grid-cols-2 gap-2">
              <Select
                aria-label="Lọc theo nick"
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">Mọi nick</option>
                {(accounts.data?.accounts ?? []).map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </Select>
              <Select aria-label="Lọc theo sale" value={saleId} onChange={(e) => setSaleId(e.target.value)}>
                <option value="">Mọi sale</option>
                {sales.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </Select>
            </div>
            <div role="tablist" aria-label="Lọc hội thoại Zalo" className="flex flex-wrap gap-1">
              {(
                [
                  ["all", "Tất cả"],
                  ["unread", "Chưa đọc"],
                  ["unlinked", "Chưa ghép page"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={`min-h-8 rounded-full px-3 text-xs ${
                    tab === key
                      ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                      : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto" data-testid="cskh-zalo-threads">
            {threads.isLoading ? <Loading /> : null}
            {threads.error ? <ErrorBox error={threads.error} /> : null}
            {threads.data && threads.data.threads.length === 0 ? (
              <p className="p-4 text-sm text-[var(--text-muted)]">Chưa có hội thoại Zalo nào.</p>
            ) : null}
            <ul>
              {(threads.data?.threads ?? []).map((thread) => (
                <li key={thread.id}>
                  <ThreadItem
                    thread={thread}
                    selected={thread.id === openId}
                    onOpen={() => open(thread.id)}
                  />
                </li>
              ))}
            </ul>
          </div>
        </section>
        {openId ? (
          <ThreadView key={openId} id={openId} onBack={() => open(null)} />
        ) : (
          <section className="panel hidden items-center justify-center p-6 text-sm text-[var(--text-muted)] lg:flex">
            Chọn một hội thoại Zalo.
          </section>
        )}
      </div>
    </div>
  );
}

function ThreadItem({
  thread,
  selected,
  onOpen,
}: {
  thread: ZaloThread;
  selected: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full gap-2 border-b border-[var(--border)] px-3 py-2 text-left ${
        selected ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-muted)]"
      }`}
    >
      <ZaloAvatar url={thread.peer_avatar} name={thread.peer_name} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className={`truncate text-sm ${thread.unread ? "font-semibold" : ""}`}>
            {thread.peer_name}
          </span>
          <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
            {formatShortDateTime(thread.last_at)}
          </span>
        </span>
        <span className="block truncate text-xs text-[var(--text-muted)]">
          {thread.last_out ? "Sale: " : ""}
          {thread.last_text}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-muted)]">
          <span className="truncate">{thread.account_name}</span>
          {thread.unread ? (
            <span className="rounded-full bg-[var(--magenta)] px-1.5 font-semibold text-white">
              {thread.unread}
            </span>
          ) : null}
          {thread.conversation_id ? (
            <span className="text-[var(--good)]">
              ✓ {thread.customer_name}
              {thread.customer_code ? ` · ${thread.customer_code}` : ""}
            </span>
          ) : thread.is_group ? (
            <span>Nhóm</span>
          ) : (
            <span className="text-[var(--warn)]">Chưa ghép page</span>
          )}
        </span>
      </span>
    </button>
  );
}

function ZaloAvatar({ url, name }: { url: string; name: string }) {
  const src = safeUrl(url);
  if (!src) return <Initials name={name} />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" className="h-9 w-9 shrink-0 rounded-full object-cover" />;
}

function ThreadView({ id, onBack }: { id: string; onBack: () => void }) {
  const client = useQueryClient();
  const thread = useQuery({
    queryKey: ["cskh", "zalo-thread", id],
    queryFn: () => cskhApi.zaloThread(id),
    refetchInterval: POLL_MS,
  });
  const [term, setTerm] = useState("");
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["cskh", "zalo-thread", id] });
    void client.invalidateQueries({ queryKey: ["cskh", "zalo-thread-page", id] });
    void client.invalidateQueries({ queryKey: ["cskh", "zalo-threads"] });
  };
  const link = useMutation({
    mutationFn: () => cskhApi.zaloLink(id, { term: term.trim() }),
    onSuccess: refresh,
  });
  const unlink = useMutation({ mutationFn: () => cskhApi.zaloUnlink(id), onSuccess: refresh });
  if (thread.isLoading)
    return (
      <section className="panel">
        <Loading />
      </section>
    );
  if (thread.error || !thread.data)
    return (
      <section className="panel p-4">
        <ErrorBox error={thread.error} />
      </section>
    );
  const t = thread.data.thread;
  return (
    <>
      <section
        className="panel flex min-h-[60vh] min-w-0 flex-col overflow-hidden lg:min-h-0"
        aria-label="Hội thoại Zalo"
      >
        <ColumnHead>
          <button type="button" className={`${smallButton} lg:hidden`} onClick={onBack}>
            ‹
          </button>
          <ZaloAvatar url={t.peer_avatar} name={t.peer_name} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{t.peer_name}</p>
            <p className="truncate text-xs text-[var(--text-muted)]">
              Nick {t.account_name} · {t.sale_name ?? "chưa giao sale"}
            </p>
          </div>
        </ColumnHead>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ZaloMessages messages={thread.data.messages} />
        </div>
        <div className="space-y-2 border-t border-[var(--border)] p-3 text-sm">
          {t.conversation_id ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[var(--good)]" data-testid="cskh-zalo-linked">
                ✓ {t.customer_name} · {t.page_name}
                {t.customer_code ? ` · ${t.customer_code}` : ""}
                <span className="block text-xs text-[var(--text-muted)]">{t.link_how}</span>
              </span>
              <ConfirmButton
                spec={{
                  title: `Bỏ ghép ${t.peer_name} khỏi khách trên page?`,
                  description: "Hội thoại Zalo không còn gắn với hội thoại page; ghép lại được ngay.",
                  confirmLabel: "Bỏ ghép",
                  variant: "destructive",
                }}
                onConfirm={() => unlink.mutate()}
                pending={unlink.isPending}
                error={unlink.error}
                tone="secondary"
                className={smallButton}
              >
                Bỏ ghép
              </ConfirmButton>
            </div>
          ) : t.is_group ? (
            <p className="text-xs text-[var(--text-muted)]">Nhóm Zalo không ghép với page.</p>
          ) : (
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (term.trim()) link.mutate();
              }}
            >
              <input
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder="Mã KH hoặc SĐT của khách"
                aria-label="Ghép theo mã KH hoặc SĐT"
                className={fieldClass}
              />
              <button type="submit" className={smallButton} disabled={link.isPending || !term.trim()}>
                Ghép
              </button>
            </form>
          )}
          <ErrorLine error={link.error ?? unlink.error} />
        </div>
      </section>
      <aside className="panel flex min-h-[40vh] min-w-0 flex-col overflow-hidden lg:col-span-2 xl:col-span-1 xl:min-h-0">
        <PageSide id={id} linked={Boolean(t.conversation_id)} />
      </aside>
    </>
  );
}

/** The matched page conversation, with a box to answer the customer on the page. */
function PageSide({ id, linked }: { id: string; linked: boolean }) {
  const client = useQueryClient();
  const page = useQuery({
    queryKey: ["cskh", "zalo-thread-page", id],
    queryFn: () => cskhApi.zaloThreadPage(id),
    refetchInterval: POLL_MS,
    enabled: linked,
  });
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => cskhApi.zaloPageSend(id, text.trim()),
    onSuccess: () => {
      setText("");
      void client.invalidateQueries({ queryKey: ["cskh", "zalo-thread-page", id] });
    },
  });
  if (!linked) {
    return (
      <p className="p-4 text-sm text-[var(--text-muted)]">
        Chưa ghép với khách trên page. Gõ mã KH hoặc SĐT ở ô dưới khung Zalo rồi bấm Ghép.
      </p>
    );
  }
  if (page.isLoading) return <Loading />;
  if (page.error || !page.data) return <ErrorBox error={page.error} />;
  const conv = page.data.conversation;
  return (
    <>
      <ColumnHead>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{conv?.customer_name ?? "Khách"}</p>
          <p className="truncate text-xs text-[var(--text-muted)]">
            {conv?.page_name}
            {conv?.phones.length ? ` · ${conv.phones.join(", ")}` : ""}
          </p>
        </div>
        {page.data.masked ? (
          <span
            className="rounded-full bg-[var(--surface-muted)] px-2 py-0.5 text-xs"
            data-testid="cskh-masked"
          >
            🔒 Ẩn 4 số cuối SĐT (ghép theo mã KH)
          </span>
        ) : null}
      </ColumnHead>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto bg-[var(--surface-muted)] p-3">
        {page.data.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </div>
      <form
        className="space-y-1 border-t border-[var(--border)] p-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) send.mutate();
        }}
      >
        <div className="flex items-end gap-2">
          <textarea
            value={text}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS}
            disabled={!conv?.connected}
            aria-label="Nhắn khách trên page"
            placeholder={conv?.connected ? "Nhắn khách trên page… (Enter để gửi)" : "Page chưa nối Pancake"}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (text.trim() && !send.isPending) send.mutate();
              }
            }}
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[var(--text)]"
          />
          <PrimaryButton type="submit" disabled={!text.trim() || send.isPending || !conv?.connected}>
            Gửi
          </PrimaryButton>
        </div>
        <ErrorLine error={send.error} />
      </form>
    </>
  );
}

// --- Kết nối Zalo -----------------------------------------------------------------------------

export function CskhZaloAccounts() {
  const client = useQueryClient();
  const data = useQuery({
    queryKey: ["cskh", "zalo-accounts"],
    queryFn: cskhApi.zaloAccounts,
    refetchInterval: 15000,
  });
  const [picked, setPicked] = useState<string[]>([]);
  const [bulkSale, setBulkSale] = useState("");
  const [scanFor, setScanFor] = useState<string | null | undefined>(undefined);
  const refresh = () => void client.invalidateQueries({ queryKey: ["cskh", "zalo-accounts"] });
  const assign = useMutation({
    mutationFn: ({ ids, userId }: { ids: string[]; userId: string | null }) =>
      cskhApi.zaloAssign(ids, userId),
    onSuccess: () => {
      setPicked([]);
      refresh();
    },
  });
  const logout = useMutation({ mutationFn: (id: string) => cskhApi.zaloLogout(id), onSuccess: refresh });

  if (data.isLoading) return <Loading />;
  if (data.error || !data.data) return <ErrorBox error={data.error} onRetry={() => void data.refetch()} />;
  const { accounts, bridge, may_manage: manage, may_scan: scan, sales } = data.data;
  const lost = accounts.filter((a) => a.status !== "connected" && a.status !== "logout");
  const totals = {
    connected: accounts.filter((a) => a.status === "connected").length,
    unassigned: accounts.filter((a) => !a.user_id).length,
    unlinked: accounts.reduce((sum, a) => sum + a.unlinked, 0),
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="brand-eyebrow">Chăm sóc khách hàng</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Kết nối Zalo</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Chỉ dùng nick Zalo công ty. Hệ thống chỉ đọc tin, không nhắn thay telesale, và chỉ nhận tin từ lúc
            kết nối.
          </p>
        </div>
        {scan ? (
          <PrimaryButton type="button" onClick={() => setScanFor(null)} disabled={!bridge.configured}>
            + Kết nối nick
          </PrimaryButton>
        ) : null}
      </div>
      {!bridge.configured ? (
        <div
          className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm"
          data-testid="cskh-bridge"
        >
          Chưa cấu hình dịch vụ Zalo (ZALO_BRIDGE_URL / ZALO_BRIDGE_SECRET trên máy chủ).
        </div>
      ) : !bridge.alive ? (
        <div
          className="rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] p-3 text-sm"
          data-testid="cskh-bridge"
        >
          Dịch vụ quét QR Zalo đang không chạy: không nhận được tin mới.
        </div>
      ) : null}
      {lost.length ? (
        <div className="rounded-lg border border-[var(--magenta)]/30 bg-[var(--magenta-soft)] p-3 text-sm">
          {lost.length} nick mất kết nối: {lost.map((a) => a.name).join(", ")}. Đóng Zalo Web / Zalo PC của
          nick đó ở máy khác rồi bấm Quét lại.
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(
          [
            ["Nick", accounts.length],
            ["Đang kết nối", totals.connected],
            ["Chưa giao sale", totals.unassigned],
            ["Hội thoại chưa ghép", totals.unlinked],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="panel p-4">
            <p className="text-xs text-[var(--text-muted)]">{label}</p>
            <p className="text-2xl font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>
      {manage && picked.length ? (
        <div className="panel flex flex-wrap items-center gap-2 p-3 text-sm">
          <span>{picked.length} nick đã chọn</span>
          <Select
            aria-label="Giao sale cho nick đã chọn"
            value={bulkSale}
            onChange={(e) => setBulkSale(e.target.value)}
          >
            <option value="">Chưa giao</option>
            {sales.map((sale) => (
              <option key={sale.user_id} value={sale.user_id}>
                {sale.name}
              </option>
            ))}
          </Select>
          <ConfirmButton
            spec={{
              title: `Giao ${picked.length} nick cho ${sales.find((s) => s.user_id === bulkSale)?.name ?? "không ai"}?`,
              description: "Sale được giao thấy hội thoại Zalo của các nick này; sale cũ không còn thấy.",
              confirmLabel: "Giao sale",
              count: picked.length,
            }}
            onConfirm={() => assign.mutate({ ids: picked, userId: bulkSale || null })}
            pending={assign.isPending}
            error={assign.error}
            tone="primary"
          >
            Giao sale
          </ConfirmButton>
        </div>
      ) : null}
      <div className="panel table-scroll-x">
        <table className="table-dense w-full">
          <thead>
            <tr>
              {manage ? <th aria-label="Chọn" /> : null}
              <th>Nick</th>
              <th>Trạng thái</th>
              <th>Sale phụ trách</th>
              <th>Hội thoại</th>
              <th>Tin gần nhất</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-sm text-[var(--text-muted)]">
                  Chưa có nick nào. Bấm “+ Kết nối nick”.
                </td>
              </tr>
            ) : null}
            {accounts.map((account) => (
              <AccountRow
                key={account.id}
                account={account}
                manage={manage}
                picked={picked.includes(account.id)}
                onPick={(on) =>
                  setPicked((ids) => (on ? [...ids, account.id] : ids.filter((x) => x !== account.id)))
                }
                sales={sales}
                onAssign={(userId) => assign.mutate({ ids: [account.id], userId })}
                onRescan={() => setScanFor(account.user_id)}
                onLogout={() => logout.mutate(account.id)}
                logoutPending={logout.isPending}
                scan={scan}
              />
            ))}
          </tbody>
        </table>
      </div>
      <ErrorLine error={assign.error ?? logout.error} />
      {scanFor !== undefined ? (
        <QrDialog
          manage={manage}
          sales={sales}
          initialSale={scanFor}
          onClose={() => {
            setScanFor(undefined);
            refresh();
          }}
        />
      ) : null}
    </div>
  );
}

function AccountRow({
  account,
  manage,
  picked,
  onPick,
  sales,
  onAssign,
  onRescan,
  onLogout,
  logoutPending,
  scan,
}: {
  account: ZaloAccount;
  manage: boolean;
  picked: boolean;
  onPick: (on: boolean) => void;
  sales: { user_id: string; name: string }[];
  onAssign: (userId: string | null) => void;
  onRescan: () => void;
  onLogout: () => void;
  logoutPending: boolean;
  scan: boolean;
}) {
  const connected = account.status === "connected";
  return (
    <tr data-testid="cskh-zalo-account">
      {manage ? (
        <td>
          <input
            type="checkbox"
            aria-label={`Chọn ${account.name}`}
            checked={picked}
            onChange={(e) => onPick(e.target.checked)}
          />
        </td>
      ) : null}
      <td>
        <div className="flex items-center gap-2">
          <ZaloAvatar url={account.avatar} name={account.name} />
          <div>
            <p className="font-medium">{account.name}</p>
            <p className="text-xs text-[var(--text-muted)]">{account.phone || account.id}</p>
          </div>
        </div>
      </td>
      <td>
        <span className={`st ${connected ? "st-green" : "st-red"}`}>{account.status_label}</span>
        {account.status_note ? (
          <p className="text-xs text-[var(--text-muted)]">{account.status_note}</p>
        ) : null}
      </td>
      <td>
        {manage ? (
          <Select
            aria-label={`Sale phụ trách ${account.name}`}
            value={account.user_id ?? ""}
            onChange={(e) => onAssign(e.target.value || null)}
          >
            <option value="">Chưa giao</option>
            {sales.map((sale) => (
              <option key={sale.user_id} value={sale.user_id}>
                {sale.name}
              </option>
            ))}
          </Select>
        ) : (
          (account.sale_name ?? "Chưa giao")
        )}
      </td>
      <td className="tabular-nums">
        {account.threads}
        {account.unlinked ? (
          <span className="text-[var(--magenta)]"> · {account.unlinked} chưa ghép</span>
        ) : null}
        {account.unread ? (
          <span className="text-[var(--text-muted)]"> · {account.unread} chưa đọc</span>
        ) : null}
      </td>
      <td title={formatDateTime(account.last_at)}>{formatShortDateTime(account.last_at)}</td>
      <td>
        <div className="flex flex-wrap gap-1">
          <a href={`/cskh/zalo`} className={smallButton}>
            Xem hội thoại
          </a>
          {scan && !connected ? (
            <button type="button" className={smallButton} onClick={onRescan}>
              Quét lại
            </button>
          ) : null}
          {manage && account.status !== "logout" ? (
            <ConfirmButton
              spec={{
                title: `Ngắt kết nối nick ${account.name}?`,
                description:
                  "Hệ thống ngừng nhận tin của nick này và xoá phiên đăng nhập; muốn nối lại phải quét QR.",
                confirmLabel: "Ngắt kết nối",
                variant: "destructive",
              }}
              onConfirm={onLogout}
              pending={logoutPending}
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

const QR_STATES: Record<string, string> = {
  starting: "Đang tạo mã…",
  qr: "Mở Zalo trên điện thoại của nick, bấm Quét mã QR rồi quét mã này. Mã hết hạn sau khoảng 60 giây.",
  scanned: "Đã quét. Bấm Đăng nhập trên điện thoại để xác nhận.",
  ok: "Đã kết nối",
  expired: "Mã đã hết hạn. Bấm Lấy mã mới.",
  declined: "Điện thoại đã từ chối đăng nhập.",
  error: "Không kết nối được.",
};

function QrDialog({
  manage,
  sales,
  initialSale,
  onClose,
}: {
  manage: boolean;
  sales: { user_id: string; name: string }[];
  initialSale: string | null;
  onClose: () => void;
}) {
  const [sale, setSale] = useState(initialSale ?? "");
  const [session, setSession] = useState<string | null>(null);
  const [state, setState] = useState<QrState | null>(null);
  const start = useMutation({
    mutationFn: () => cskhApi.zaloQrStart(manage ? sale || null : null),
    onSuccess: (result) => {
      setState(result);
      setSession(result.session ?? null);
    },
  });
  const done = state?.state && ["ok", "expired", "declined", "error"].includes(state.state);
  const poll = useQuery({
    queryKey: ["cskh", "zalo-qr", session],
    queryFn: () => cskhApi.zaloQrStatus(session!),
    enabled: Boolean(session) && !done,
    refetchInterval: 2000,
  });
  useEffect(() => {
    if (poll.data)
      setState((current) => ({ ...current, ...poll.data, image: poll.data.image ?? current?.image }));
  }, [poll.data]);

  const image = state?.image
    ? `data:image/png;base64,${state.image.replace(/^data:image\/png;base64,/, "")}`
    : null;
  return (
    <Modal open onClose={onClose} title="Kết nối nick Zalo" busy={start.isPending}>
      <ModalBody className="space-y-3">
        {manage ? (
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Sale phụ trách nick này</span>
            <Select
              value={sale}
              onChange={(e) => setSale(e.target.value)}
              disabled={Boolean(session)}
              className="w-full"
            >
              <option value="">Chưa giao</option>
              {sales.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
            </Select>
          </label>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">Nick quét xong sẽ được giao cho bạn.</p>
        )}
        {state?.state ? (
          <div className="space-y-2 text-center" data-testid="cskh-qr-state">
            {image && (state.state === "qr" || state.state === "starting") ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={image}
                alt="Mã QR đăng nhập Zalo"
                className="mx-auto h-56 w-56 rounded-lg bg-white p-2"
              />
            ) : null}
            <p className={`text-sm ${state.state === "ok" ? "font-semibold text-[var(--good)]" : ""}`}>
              {state.state === "ok"
                ? `Đã kết nối nick ${state.name ?? state.accountId ?? ""}`
                : (QR_STATES[state.state] ?? state.state)}
            </p>
            {state.error ? <p className="text-xs text-[var(--bad)]">{state.error}</p> : null}
          </div>
        ) : null}
        <ErrorLine error={start.error ?? poll.error} />
      </ModalBody>
      <ModalFooter>
        <button type="button" onClick={onClose} className={smallButton}>
          {state?.state === "ok" ? "Xong" : "Đóng"}
        </button>
        {state?.state !== "ok" ? (
          <PrimaryButton
            type="button"
            disabled={start.isPending || (Boolean(session) && !done)}
            onClick={() => {
              setSession(null);
              setState(null);
              start.mutate();
            }}
          >
            {session ? "Lấy mã mới" : "Tạo mã QR"}
          </PrimaryButton>
        ) : null}
      </ModalFooter>
    </Modal>
  );
}
