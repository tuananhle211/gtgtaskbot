"use client";

/**
 * "Hộp chat page" (0102): every Pancake page in one inbox. Three columns - the
 * conversations (filters, tabs, search), the chat (attachments, ads, phones,
 * QR codes, Pancake tags + assignee, quick replies, the phone / QR
 * confirmation) and the customer (orders, page staff) / "Zalo telesale".
 * The URL holds the open conversation (`?c=`); on a phone one column at a time.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cskhApi,
  fillReply,
  MAX_MESSAGE_CHARS,
  repliesFor,
  type ConversationDetail,
  type ConversationFilters,
  type ConversationRow,
  type InboxState,
  type QuickReply,
  type ZaloThread,
} from "@/lib/cskh";
import { formatDateTime, formatShortDateTime } from "@/lib/datetime";
import {
  ColumnHead,
  CopyChip,
  ErrorLine,
  Initials,
  LabelChip,
  MessageBubble,
  fieldClass,
  smallButton,
} from "@/components/cskh-common";
import { OrderConfirmDialog } from "@/components/cskh-confirm";
import { ConfirmButton } from "@/components/confirm";
import { PrimaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

const POLL_MS = 4000;
type Tab = NonNullable<ConversationFilters["tab"]>;
const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "Tất cả" },
  { key: "unread", label: "Chưa đọc" },
  { key: "callback", label: "Chờ gọi lại" },
  { key: "phone", label: "Có SĐT" },
];

export function CskhInbox() {
  const router = useRouter();
  const params = useSearchParams();
  const openId = params.get("c");
  const [filters, setFilters] = useState<ConversationFilters>({ tab: "all" });
  const [search, setSearch] = useState("");
  const [older, setOlder] = useState<ConversationRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [showSide, setShowSide] = useState(false);

  const state = useQuery({ queryKey: ["cskh", "state"], queryFn: cskhApi.state, refetchInterval: 15000 });
  const listKey = ["cskh", "conversations", filters] as const;
  const list = useQuery({
    queryKey: listKey,
    queryFn: () => cskhApi.conversations(filters),
    refetchInterval: POLL_MS,
  });
  useEffect(() => {
    setOlder([]);
    setCursor(null);
  }, [filters]);

  const loadMore = useMutation({
    mutationFn: () =>
      cskhApi.conversations({ ...filters, before: cursor ?? list.data?.next_before ?? undefined }),
    onSuccess: (data) => {
      setOlder((rows) => [...rows, ...data.conversations]);
      setCursor(data.next_before ?? "");
    },
  });

  const rows = useMemo(() => {
    const first = list.data?.conversations ?? [];
    const seen = new Set(first.map((row) => row.id));
    return [...first, ...older.filter((row) => !seen.has(row.id))];
  }, [list.data, older]);
  const more = cursor === null ? Boolean(list.data?.next_before) : Boolean(cursor);

  const open = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("c", id);
    else next.delete("c");
    router.replace(`/cskh${next.toString() ? `?${next}` : ""}`, { scroll: false });
  };

  if (state.isLoading) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} onRetry={() => void state.refetch()} />;
  const frame = state.data!;
  const pageOf = (id: string) => frame.pages.find((page) => page.id === id);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="brand-eyebrow">Chăm sóc khách hàng</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Hộp chat page</h1>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          {frame.pages.length} page · {frame.unread_total} tin chưa đọc ·{" "}
          {frame.work_now
            ? "Trong giờ làm"
            : frame.auto_reply_enabled
              ? "Ngoài giờ: đang tự trả lời"
              : "Ngoài giờ"}
        </p>
      </div>
      <div className="grid gap-3 lg:h-[calc(100vh-11rem)] lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_360px]">
        <section
          className={`panel flex min-h-0 flex-col overflow-hidden ${openId ? "hidden lg:flex" : "flex"}`}
          aria-label="Danh sách hội thoại"
        >
          <ListFilters
            frame={frame}
            filters={filters}
            setFilters={setFilters}
            search={search}
            setSearch={setSearch}
          />
          <div className="min-h-0 flex-1 overflow-y-auto" data-testid="cskh-conversations">
            {list.isLoading ? <Loading /> : null}
            {list.error ? <ErrorBox error={list.error} /> : null}
            {!list.isLoading && rows.length === 0 ? (
              <p className="p-4 text-sm text-[var(--text-muted)]">Chưa có hội thoại nào.</p>
            ) : null}
            <ul>
              {rows.map((row) => (
                <li key={row.id}>
                  <ConversationItem
                    row={row}
                    selected={row.id === openId}
                    frame={frame}
                    onOpen={() => open(row.id)}
                  />
                </li>
              ))}
            </ul>
            {more ? (
              <div className="p-3">
                <button
                  type="button"
                  className={smallButton}
                  disabled={loadMore.isPending}
                  onClick={() => loadMore.mutate()}
                >
                  {loadMore.isPending ? "Đang tải…" : "Tải thêm"}
                </button>
              </div>
            ) : null}
          </div>
        </section>
        {openId ? (
          <ChatColumn
            key={openId}
            id={openId}
            frame={frame}
            onBack={() => open(null)}
            onInfo={() => setShowSide((v) => !v)}
            showSide={showSide}
            pageOf={pageOf}
          />
        ) : (
          <section className="panel hidden items-center justify-center p-6 text-sm text-[var(--text-muted)] lg:flex">
            Chọn một hội thoại để xem tin nhắn.
          </section>
        )}
      </div>
    </div>
  );
}

function ListFilters({
  frame,
  filters,
  setFilters,
  search,
  setSearch,
}: {
  frame: InboxState;
  filters: ConversationFilters;
  setFilters: (value: ConversationFilters) => void;
  search: string;
  setSearch: (value: string) => void;
}) {
  const page = frame.pages.find((p) => p.id === filters.page_id);
  return (
    <div className="space-y-2 border-b border-[var(--border)] p-3">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setFilters({ ...filters, q: search.trim() || undefined });
        }}
      >
        <input
          type="search"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            if (!event.target.value) setFilters({ ...filters, q: undefined });
          }}
          placeholder="Tìm mã KH, SĐT, tên, nội dung tin…"
          aria-label="Tìm hội thoại"
          className={fieldClass}
        />
      </form>
      <div className="grid grid-cols-2 gap-2">
        <Select
          aria-label="Lọc theo page"
          value={filters.page_id ?? ""}
          onChange={(event) =>
            setFilters({ ...filters, page_id: event.target.value || undefined, pancake_tag: undefined })
          }
        >
          <option value="">Mọi page</option>
          {frame.pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.unread ? ` (${p.unread})` : ""}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Lọc theo nhãn, thẻ, chiến dịch"
          value={
            filters.label_id
              ? `l:${filters.label_id}`
              : filters.pancake_tag
                ? `t:${filters.pancake_tag}`
                : filters.campaign_id
                  ? `c:${filters.campaign_id}`
                  : ""
          }
          onChange={(event) => {
            const [kind, value] = [event.target.value.slice(0, 1), event.target.value.slice(2)];
            setFilters({
              ...filters,
              label_id: kind === "l" ? value : undefined,
              pancake_tag: kind === "t" ? value : undefined,
              campaign_id: kind === "c" ? value : undefined,
            });
          }}
        >
          <option value="">Mọi nhãn / thẻ</option>
          {frame.labels.length ? (
            <optgroup label="Nhãn page">
              {frame.labels.map((label) => (
                <option key={label.id} value={`l:${label.id}`}>
                  {label.name}
                </option>
              ))}
            </optgroup>
          ) : null}
          {page?.pancake_tags.length ? (
            <optgroup label="Thẻ Pancake">
              {page.pancake_tags.map((tag) => (
                <option key={tag.id} value={`t:${tag.id}`}>
                  {tag.text}
                </option>
              ))}
            </optgroup>
          ) : null}
          {frame.campaigns.length ? (
            <optgroup label="Chiến dịch">
              {frame.campaigns.map((c) => (
                <option key={c.id} value={`c:${c.id}`}>
                  {c.name}
                </option>
              ))}
            </optgroup>
          ) : null}
        </Select>
      </div>
      <div role="tablist" aria-label="Lọc hội thoại" className="flex flex-wrap gap-1">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={(filters.tab ?? "all") === tab.key}
            onClick={() => setFilters({ ...filters, tab: tab.key })}
            className={`min-h-8 rounded-full px-3 text-xs ${
              (filters.tab ?? "all") === tab.key
                ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function ConversationItem({
  row,
  selected,
  frame,
  onOpen,
}: {
  row: ConversationRow;
  selected: boolean;
  frame: InboxState;
  onOpen: () => void;
}) {
  const page = frame.pages.find((p) => p.id === row.page_id);
  const tags = (page?.pancake_tags ?? []).filter((tag) => row.pancake_tags.includes(tag.id));
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full gap-2 border-b border-[var(--border)] px-3 py-2 text-left ${
        selected ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-muted)]"
      }`}
    >
      <Initials name={row.customer_name} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className={`truncate text-sm ${row.unread ? "font-semibold" : ""}`}>
            {row.customer_name}
            {row.phones.length ? <span title="Có số điện thoại"> 📞</span> : null}
            {row.has_qr ? <span title="Có mã QR"> ▦</span> : null}
            {row.zalo_linked ? <span title="Đã ghép Zalo"> 💬</span> : null}
          </span>
          <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
            {formatShortDateTime(row.last_at)}
          </span>
        </span>
        <span className="block truncate text-xs text-[var(--text-muted)]">
          {row.last_out ? "Bạn: " : ""}
          {row.last_text}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-muted)]">
          <span className="truncate">{row.page_name}</span>
          {row.unread ? (
            <span className="rounded-full bg-[var(--magenta)] px-1.5 font-semibold text-white">
              {row.unread}
            </span>
          ) : null}
          {row.callback ? <span className="text-[var(--warn)]">Chờ gọi lại</span> : null}
          {row.ad_name ? <span className="truncate">📢 {row.ad_name}</span> : null}
          {tags.map((tag) => (
            <span key={tag.id} className="rounded px-1" style={{ background: tag.color, color: "#fff" }}>
              {tag.text}
            </span>
          ))}
        </span>
        {row.match ? (
          <span
            className="mt-1 block truncate rounded bg-[var(--warn-soft)] px-1.5 text-[11px]"
            data-testid="cskh-match"
          >
            {row.match.why}: {row.match.snippet}
          </span>
        ) : null}
      </span>
    </button>
  );
}

function ChatColumn({
  id,
  frame,
  onBack,
  onInfo,
  showSide,
  pageOf,
}: {
  id: string;
  frame: InboxState;
  onBack: () => void;
  onInfo: () => void;
  showSide: boolean;
  pageOf: (id: string) => InboxState["pages"][number] | undefined;
}) {
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["cskh", "conversation", id],
    queryFn: () => cskhApi.conversation(id),
    refetchInterval: POLL_MS,
  });
  const [confirmMode, setConfirmMode] = useState<"phone" | "qr" | null>(null);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["cskh", "conversation", id] });
    void client.invalidateQueries({ queryKey: ["cskh", "conversations"] });
  };
  const tag = useMutation({
    mutationFn: ({ tagId, add }: { tagId: string; add: boolean }) => cskhApi.pancakeTag(id, tagId, add),
    onSuccess: refresh,
  });
  const assign = useMutation({
    mutationFn: (userId: string | null) => cskhApi.assign(id, userId),
    onSuccess: refresh,
  });
  const callback = useMutation({
    mutationFn: (on: boolean) => cskhApi.setCallback(id, on),
    onSuccess: refresh,
  });
  const scan = useMutation({ mutationFn: () => cskhApi.scanQr(id), onSuccess: refresh });

  if (detail.isLoading)
    return (
      <section className="panel">
        <Loading />
      </section>
    );
  if (detail.error || !detail.data)
    return (
      <section className="panel p-4">
        <button type="button" className={`${smallButton} mb-2 lg:hidden`} onClick={onBack}>
          ‹ Danh sách
        </button>
        <ErrorBox error={detail.error} />
      </section>
    );
  const data = detail.data;
  const conv = data.conversation;
  const page = pageOf(conv.page_id);
  const tags = page?.pancake_tags ?? [];
  const users = page?.pancake_users ?? [];
  const ads = conv.ads;

  return (
    <>
      <section
        className="panel flex min-h-[70vh] min-w-0 flex-col overflow-hidden lg:min-h-0"
        aria-label="Hội thoại"
      >
        <ColumnHead>
          <button type="button" className={`${smallButton} lg:hidden`} onClick={onBack}>
            ‹
          </button>
          <Initials name={conv.customer_name} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{conv.customer_name}</p>
            <p className="truncate text-xs text-[var(--text-muted)]">
              {conv.page_name}
              {!data.page.connected ? " · page chưa nối Pancake" : ""}
            </p>
          </div>
          <button type="button" className={`${smallButton} xl:hidden`} onClick={onInfo}>
            {showSide ? "Ẩn thông tin" : "Thông tin"}
          </button>
        </ColumnHead>
        <div className="space-y-2 border-b border-[var(--border)] px-3 py-2">
          <div className="flex flex-wrap items-center gap-1">
            {conv.phones.map((phone) => (
              <CopyChip key={phone} text={phone} display={`📞 ${phone}`} />
            ))}
            {conv.qrs.map((qr, index) => (
              <CopyChip
                key={`${qr.text}-${index}`}
                text={qr.text}
                display={`QR ${qr.kind}`}
                title={qr.text}
              />
            ))}
            {ads.length ? (
              <span className="rounded-full bg-[var(--magenta-soft)] px-2 py-0.5 text-xs text-[var(--magenta)]">
                Từ quảng cáo
              </span>
            ) : null}
          </div>
          {ads.length ? <AdCard ads={ads} /> : null}
        </div>
        <MessageList messages={data.messages} />
        <div className="space-y-2 border-t border-[var(--border)] p-3">
          {tags.length || users.length ? (
            <div className="flex flex-wrap items-center gap-1" aria-label="Thẻ Pancake">
              {tags.map((t) => {
                const on = conv.pancake_tags.includes(t.id);
                return (
                  <button
                    key={t.id}
                    type="button"
                    aria-pressed={on}
                    disabled={tag.isPending || !data.page.connected}
                    onClick={() => tag.mutate({ tagId: t.id, add: !on })}
                    className="min-h-8 rounded-full border px-2.5 text-xs disabled:opacity-50"
                    style={
                      on
                        ? { background: t.color, borderColor: t.color, color: "#fff" }
                        : { borderColor: "var(--border)" }
                    }
                  >
                    {t.text}
                  </button>
                );
              })}
              {users.length ? (
                <select
                  aria-label="Người phụ trách"
                  value={conv.assignee_ids[0] ?? ""}
                  disabled={assign.isPending || !data.page.connected}
                  onChange={(event) => assign.mutate(event.target.value || null)}
                  className="min-h-8 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
                >
                  <option value="">Chưa giao người phụ trách</option>
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.name}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-1">
            <button
              type="button"
              className={smallButton}
              aria-pressed={conv.callback}
              disabled={callback.isPending}
              onClick={() => callback.mutate(!conv.callback)}
            >
              {conv.callback ? "✓ Chờ gọi lại" : "Chờ gọi lại"}
            </button>
            <button type="button" className={smallButton} onClick={() => setConfirmMode("phone")}>
              ✓ Xác nhận số điện thoại
            </button>
            {conv.qrs.length ? (
              <button type="button" className={smallButton} onClick={() => setConfirmMode("qr")}>
                Xác nhận QR
              </button>
            ) : null}
            <button
              type="button"
              className={smallButton}
              disabled={scan.isPending}
              onClick={() => scan.mutate()}
              title="Đọc lại mã QR trong ảnh khách gửi"
            >
              {scan.isPending ? "Đang quét…" : "Quét lại QR"}
            </button>
          </div>
          <ErrorLine error={tag.error ?? assign.error ?? callback.error ?? scan.error} />
          <Composer
            id={id}
            pageId={conv.page_id}
            customerName={conv.customer_name}
            callback={conv.callback}
            replies={frame.quick_replies}
            disabled={!data.page.connected}
            onSent={refresh}
          />
        </div>
      </section>
      <aside className={`panel min-h-0 overflow-y-auto ${showSide ? "block" : "hidden"} xl:block`}>
        <SidePanel detail={data} />
      </aside>
      {confirmMode ? (
        <OrderConfirmDialog
          mode={confirmMode}
          detail={data}
          frame={frame}
          onClose={() => setConfirmMode(null)}
          onDone={refresh}
        />
      ) : null}
    </>
  );
}

function AdCard({ ads }: { ads: ConversationDetail["conversation"]["ads"] }) {
  const ad = ads[0];
  return (
    <div className="flex gap-2 rounded-lg border border-[var(--border)] p-2 text-xs" data-testid="cskh-ad">
      {ad.image_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={ad.image_url} alt="" loading="lazy" className="h-12 w-12 shrink-0 rounded object-cover" />
      ) : null}
      <div className="min-w-0">
        <p className="truncate font-semibold">📢 {ad.name || `Quảng cáo ${ad.ad_id}`}</p>
        <p className="truncate text-[var(--text-muted)]">
          {ad.campaign_name ? `Chiến dịch: ${ad.campaign_name} · ` : ""}
          {ad.at ? `Bấm lúc ${formatShortDateTime(ad.at)} · ` : ""}
          Mã QC {ad.ad_id}
        </p>
        <p className="text-[var(--text-muted)]">
          {ad.post_id ? (
            <a
              href={`https://facebook.com/${encodeURIComponent(ad.post_id)}`}
              target="_blank"
              rel="noreferrer noopener"
              className="underline"
            >
              Xem bài viết
            </a>
          ) : null}
          {ads.length > 1 ? ` · +${ads.length - 1} quảng cáo khác` : ""}
        </p>
      </div>
    </div>
  );
}

function MessageList({ messages }: { messages: ConversationDetail["messages"] }) {
  const end = useRef<HTMLDivElement | null>(null);
  const count = messages.length;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [count]);
  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto bg-[var(--surface-muted)] p-3">
      {messages.length === 0 ? (
        <p className="text-center text-sm text-[var(--text-muted)]">Chưa có tin nhắn.</p>
      ) : null}
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={end} />
    </div>
  );
}

/** The send box: Enter sends, Shift+Enter breaks a line, "/" opens the quick replies. */
function Composer({
  id,
  pageId,
  customerName,
  callback,
  replies,
  disabled,
  onSent,
}: {
  id: string;
  pageId: string;
  customerName: string;
  callback: boolean;
  replies: QuickReply[];
  disabled: boolean;
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [picker, setPicker] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLTextAreaElement | null>(null);
  const options = repliesFor(replies, pageId);
  const slash = text.match(/(?:^|\s)\/(\S*)$/);
  const query = picker ? (slash?.[1] ?? "") : "";
  const shown = options.filter(
    (reply) => !query || reply.shortcut.toLowerCase().includes(query.toLowerCase()),
  );
  const send = useMutation({
    mutationFn: (body: string) => cskhApi.send(id, body, callback),
    onSuccess: () => {
      setText("");
      onSent();
    },
  });
  const insert = (reply: QuickReply) => {
    const filled = fillReply(reply.text, customerName);
    setText((current) => current.replace(/(^|\s)\/\S*$/, (_m, lead: string) => lead) + filled);
    setPicker(false);
    box.current?.focus();
  };
  const submit = () => {
    const body = text.trim();
    if (body && !send.isPending) send.mutate(body);
  };
  return (
    <div className="relative">
      {picker && shown.length ? (
        <ul
          role="listbox"
          aria-label="Câu trả lời mẫu"
          className="absolute bottom-full left-0 z-20 mb-1 max-h-60 w-full overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-pop)]"
        >
          {shown.map((reply, index) => (
            <li key={reply.id}>
              <button
                type="button"
                role="option"
                aria-selected={index === active}
                onMouseDown={(event) => {
                  event.preventDefault();
                  insert(reply);
                }}
                className={`block w-full px-3 py-2 text-left text-sm ${
                  index === active ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-muted)]"
                }`}
              >
                <span className="font-semibold">/{reply.shortcut}</span>{" "}
                <span className="text-[var(--text-muted)]">
                  {fillReply(reply.text, customerName).slice(0, 90)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex items-end gap-2">
        <textarea
          ref={box}
          value={text}
          rows={2}
          maxLength={MAX_MESSAGE_CHARS}
          disabled={disabled}
          aria-label="Nội dung tin nhắn"
          placeholder={disabled ? "Page chưa nối Pancake" : "Nhập tin… (gõ / để chèn câu mẫu)"}
          onChange={(event) => {
            const value = event.target.value;
            setText(value);
            const open = /(?:^|\s)\/\S*$/.test(value);
            setPicker(open);
            if (open) setActive(0);
          }}
          onKeyDown={(event) => {
            if (picker && shown.length) {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((i) => (i + 1) % shown.length);
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((i) => (i - 1 + shown.length) % shown.length);
                return;
              }
              if (event.key === "Enter") {
                event.preventDefault();
                insert(shown[Math.min(active, shown.length - 1)]);
                return;
              }
              if (event.key === "Escape") {
                setPicker(false);
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className="min-h-11 w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[var(--text)]"
        />
        <div className="flex flex-col gap-1">
          <button
            type="button"
            className={smallButton}
            disabled={disabled}
            onClick={() => {
              setPicker((v) => !v);
              setActive(0);
            }}
          >
            / Câu mẫu
          </button>
          <PrimaryButton type="button" onClick={submit} disabled={disabled || !text.trim() || send.isPending}>
            {send.isPending ? "Đang gửi…" : "Gửi"}
          </PrimaryButton>
        </div>
      </div>
      <ErrorLine error={send.error} />
    </div>
  );
}

function SidePanel({ detail }: { detail: ConversationDetail }) {
  const [tab, setTab] = useState<"customer" | "zalo">(detail.conversation.zalo_linked ? "zalo" : "customer");
  useEffect(() => {
    if (detail.conversation.zalo_linked) setTab("zalo");
  }, [detail.conversation.zalo_linked]);
  return (
    <div>
      <div role="tablist" aria-label="Thông tin khách" className="flex border-b border-[var(--border)]">
        {(
          [
            ["customer", "Khách hàng"],
            ["zalo", `Zalo telesale${detail.zalo_threads ? ` (${detail.zalo_threads})` : ""}`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`min-h-11 flex-1 text-sm ${
              tab === key ? "border-b-2 border-[var(--accent)] font-semibold" : "text-[var(--text-muted)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "customer" ? (
        <CustomerTab detail={detail} />
      ) : (
        <ZaloTab conversationId={detail.conversation.id} />
      )}
    </div>
  );
}

function CustomerTab({ detail }: { detail: ConversationDetail }) {
  const { conversation: conv, page, orders } = detail;
  return (
    <div className="space-y-4 p-3 text-sm">
      <div>
        <p className="font-semibold">{conv.customer_name}</p>
        <p className="text-xs text-[var(--text-muted)]">
          PSID {conv.psid} · lần cuối khách nhắn {formatDateTime(conv.last_in_at)}
        </p>
      </div>
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase text-[var(--text-muted)]">Đơn đã xác nhận</h3>
        {orders.length === 0 ? <p className="text-xs text-[var(--text-muted)]">Chưa có.</p> : null}
        <ul className="space-y-2">
          {orders.map((order) => (
            <li
              key={order.id}
              className="rounded-lg border border-[var(--border)] p-2 text-xs"
              data-testid="cskh-order"
            >
              <p className="font-medium">
                {order.confirm_type === "qr" ? "Xác nhận QR" : "Xác nhận SĐT"} · đơn POS #
                {order.pos_order_no ?? "—"} ({order.action === "create" ? "tạo mới" : "chuyển Đã xác nhận"})
              </p>
              <p className="text-[var(--text-muted)]">
                {formatDateTime(order.created_at)}
                {order.phone ? ` · ${order.phone}` : ""}
                {order.customer_code ? ` · Mã KH ${order.customer_code}` : ""}
              </p>
              <p className="text-[var(--text-muted)]">
                {[
                  order.content_name,
                  order.order_status,
                  order.marketer_name && `MKT ${order.marketer_name}`,
                  order.telesale_name && `Tele ${order.telesale_name}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {order.dup_info ? <p className="text-[var(--warn)]">⚠ {order.dup_info}</p> : null}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase text-[var(--text-muted)]">Page</h3>
        <p>{page.name}</p>
        <p className="text-xs text-[var(--text-muted)]">
          Marketing: {page.marketer_name ?? "chưa gán"} · Cửa hàng POS: {page.pos_shop_name ?? "chưa có"}
        </p>
        <ul className="mt-1 flex flex-wrap gap-1">
          {page.members.map((m) => (
            <li key={m.user_id}>
              <LabelChip
                label={{ name: `${m.role_label}: ${m.name}`, color: m.role === "OWNER" ? "blue" : "teal" }}
              />
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** "Zalo telesale": the threads matched to this customer, else search + "Ghép". */
function ZaloTab({ conversationId }: { conversationId: string }) {
  const client = useQueryClient();
  const zalo = useQuery({
    queryKey: ["cskh", "conversation-zalo", conversationId],
    queryFn: () => cskhApi.conversationZalo(conversationId),
    refetchInterval: 8000,
  });
  const [term, setTerm] = useState("");
  const search = useMutation({ mutationFn: (q: string) => cskhApi.zaloSearch(q, conversationId) });
  const link = useMutation({
    mutationFn: (thread: ZaloThread) =>
      cskhApi.zaloLink(thread.id, { conversation_id: conversationId, term, match: thread.match ?? "" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cskh", "conversation-zalo", conversationId] });
      void client.invalidateQueries({ queryKey: ["cskh", "conversation", conversationId] });
    },
  });
  const unlink = useMutation({
    mutationFn: (threadId: string) => cskhApi.zaloUnlink(threadId),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["cskh", "conversation-zalo", conversationId] }),
  });
  if (zalo.isLoading) return <Loading />;
  if (zalo.error) return <ErrorBox error={zalo.error} />;
  const data = zalo.data!;
  const results = search.data?.results ?? data.suggest;
  return (
    <div className="space-y-3 p-3 text-sm">
      {data.codes.length ? (
        <p className="text-xs text-[var(--text-muted)]">Mã KH của khách: {data.codes.join(", ")}</p>
      ) : null}
      {data.threads.map((thread) => (
        <div
          key={thread.id}
          className="rounded-lg border border-[var(--border)]"
          data-testid="cskh-zalo-thread"
        >
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] p-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{thread.peer_name}</p>
              <p className="truncate text-xs text-[var(--text-muted)]">
                Nick {thread.account_name} · {thread.sale_name ?? "chưa giao sale"} · {thread.link_how}
              </p>
            </div>
            {data.may_link ? (
              <ConfirmButton
                spec={{
                  title: `Bỏ ghép Zalo ${thread.peer_name}?`,
                  description: "Hội thoại Zalo này không còn gắn với khách trên page; ghép lại được ngay.",
                  confirmLabel: "Bỏ ghép",
                  variant: "destructive",
                }}
                onConfirm={() => unlink.mutate(thread.id)}
                pending={unlink.isPending}
                error={unlink.error}
                tone="secondary"
                className={smallButton}
              >
                Bỏ ghép
              </ConfirmButton>
            ) : null}
          </div>
          <ZaloMessages messages={thread.messages ?? []} />
        </div>
      ))}
      {data.threads.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">Khách này chưa ghép hội thoại Zalo nào.</p>
      ) : null}
      {data.may_link ? (
        <div className="space-y-2">
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              search.mutate(term.trim());
            }}
          >
            <input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="Mã KH, SĐT hoặc tên Zalo"
              aria-label="Tìm hội thoại Zalo"
              className={fieldClass}
            />
            <button type="submit" className={smallButton}>
              Tìm
            </button>
          </form>
          {results.length ? (
            <p className="text-xs text-[var(--text-muted)]">
              {search.data ? "Kết quả" : "Gợi ý theo mã KH / SĐT"}
            </p>
          ) : null}
          <ul className="space-y-1">
            {results.map((thread) => (
              <li
                key={thread.id}
                className="flex items-center gap-2 rounded-lg border border-[var(--border)] p-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{thread.peer_name}</p>
                  <p className="truncate text-xs text-[var(--text-muted)]">
                    {thread.match ? `${thread.match} · ` : ""}
                    {thread.account_name}
                    {thread.conversation_id ? " · đang ghép khách khác" : ""}
                  </p>
                </div>
                <button
                  type="button"
                  className={smallButton}
                  disabled={link.isPending}
                  onClick={() => link.mutate(thread)}
                >
                  Ghép
                </button>
              </li>
            ))}
          </ul>
          <ErrorLine error={search.error ?? link.error} />
        </div>
      ) : null}
    </div>
  );
}

export function ZaloMessages({
  messages,
}: {
  messages: { id: string; direction: "in" | "out"; text: string; at: string | null; sender_name: string }[];
}) {
  return (
    <div className="max-h-80 space-y-1 overflow-y-auto bg-[var(--surface-muted)] p-2">
      {messages.length === 0 ? <p className="text-xs text-[var(--text-muted)]">Chưa có tin.</p> : null}
      {messages.map((m) => (
        <div key={m.id} className={`flex ${m.direction === "out" ? "justify-end" : ""}`}>
          <div
            className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-2.5 py-1.5 text-xs ${
              m.direction === "out"
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "border border-[var(--border)] bg-[var(--surface)]"
            }`}
          >
            {m.text}
            <span className="block text-[10px] text-[var(--text-muted)]">{formatShortDateTime(m.at)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
