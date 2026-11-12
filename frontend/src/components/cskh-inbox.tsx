"use client";

/**
 * "Hộp chat page" (0102; redesigned 12/11 from `THIET_KE_HOP_CHAT.md`): every
 * Pancake page in one inbox that fits the screen, three columns scrolling inside.
 *
 * - left: search + "Lọc" (page, label / tag / campaign, stage, "Của tôi") with the
 *   active filters as chips, "Xếp: Chờ lâu nhất / Mới nhất", tabs with counts; each
 *   customer in 3 lines (name · time · unread; last message; page label · phone ·
 *   person in charge · how long they have waited, red past the threshold);
 * - middle: the chat - header with the page, the wait, the ad on one line and
 *   "Trạng thái"; navy page bubbles; a phone the customer typed can be confirmed
 *   or copied in the bubble; one toolbar row; Ctrl+Enter sends, "/" quick replies;
 * - right: the customer card (Mã KH from orders or Pancake notes, phone, person in
 *   charge, Pancake notes, orders + "Xác nhận đơn", the page) / "Zalo telesale".
 *
 * The URL holds the open conversation (`?c=`); ↑ / ↓ move between customers when
 * not typing. On a phone one column at a time.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cskhApi,
  fillReply,
  MAX_MESSAGE_CHARS,
  nameTone,
  pageShort,
  pageTone,
  repliesFor,
  STAGES,
  waitLabel,
  waitMinutes,
  type ConversationDetail,
  type ConversationFilters,
  type ConversationRow,
  type InboxPage,
  type InboxState,
  type QuickReply,
  type Stage,
  type ZaloThread,
} from "@/lib/cskh";
import { formatDateTime, formatShortDateTime } from "@/lib/datetime";
import { CopyChip, ErrorLine, MessageBubble, fieldClass, smallButton } from "@/components/cskh-common";
import { OrderConfirmDialog } from "@/components/cskh-confirm";
import { ConfirmButton } from "@/components/confirm";
import { Select } from "@/components/pr";
import { RowMenu } from "@/components/row-menu";
import { ErrorBox, Loading } from "@/components/states";

const POLL_MS = 4000;
const DEFAULT_WAIT_ALERT = 8;
type Tab = NonNullable<ConversationFilters["tab"]>;

// --- small pieces ---------------------------------------------------------------------------

const ICONS = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35",
  filter: "M3 5h18M6 12h12M10 19h4",
  tag: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8ZM7.5 7.5h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2",
  qr: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 18h2v2h-2zM14 18h2",
  phone:
    "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8.1 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2Z",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4 20-7Z",
  check: "M20 6 9 17l-5-5",
  back: "M15 18l-6-6 6-6",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5v-4m0-4h.01",
  ad: "M3 11l15-6v14L3 13zM11.6 16.8a3 3 0 1 1-5.8-1.6",
  close: "M18 6 6 18M6 6l12 12",
} as const;

function Icon({ name, size = 16 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
    >
      <path d={ICONS[name]} />
    </svg>
  );
}

/** The customer's initials on a tint picked from the name. */
function Avatar({ name, size = 40 }: { name: string; size?: number }) {
  const [fill, ink] = nameTone(name);
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
      style={{ width: size, height: size, background: fill, color: ink }}
      className="inline-flex shrink-0 items-center justify-center rounded-full text-xs font-bold"
    >
      {letters}
    </span>
  );
}

/** The page's short coloured label. */
function PageTag({ pageId, name, short, color }: { pageId: string; name: string; short?: string; color?: string }) {
  const [fill, ink] = pageTone(pageId, color);
  return (
    <span
      title={name}
      style={{ background: fill, color: ink }}
      className="inline-flex max-w-[11rem] items-center truncate rounded-md px-1.5 py-px text-[11px] font-semibold"
      data-testid="cskh-page-tag"
    >
      {pageShort(name, short)}
    </span>
  );
}

/** "N phút" the customer has waited: amber, red from the threshold on. */
function WaitClock({ minutes, alert, long = false }: { minutes: number; alert: number; long?: boolean }) {
  const late = minutes >= alert;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-px text-[11px] font-semibold tabular-nums ${
        late ? "bg-[var(--bad)] text-[var(--surface)]" : "bg-[var(--warn-soft)] text-[var(--warn)]"
      }`}
      data-testid="cskh-wait"
      data-late={late ? "true" : undefined}
      title={`Khách chờ ${waitLabel(minutes)} chưa được trả lời`}
    >
      <Icon name="clock" size={12} />
      {long ? `Khách chờ ${waitLabel(minutes)}` : waitLabel(minutes)}
    </span>
  );
}

function UnreadBadge({ count }: { count: number }) {
  return (
    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-[var(--magenta)] px-1.5 text-[11px] font-bold leading-5 text-[var(--surface)] tabular-nums">
      {count > 99 ? "99+" : count}
    </span>
  );
}

/** The list's time: today = "HH:mm", another day = "dd/mm". */
function listTime(at: string | null, now: number): string {
  const when = formatShortDateTime(at);
  if (!when) return "";
  const [day, time] = when.split(" ");
  return day === formatShortDateTime(new Date(now).toISOString()).split(" ")[0] ? (time ?? when) : day;
}

/** A clock that moves every 30 s (the wait timers). */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

const toolButton =
  "inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm font-medium text-[var(--text)] hover:bg-[var(--surface-muted)] disabled:opacity-50";

// --- the screen -----------------------------------------------------------------------------

export function CskhInbox() {
  const router = useRouter();
  const params = useSearchParams();
  const openId = params.get("c");
  const [filters, setFilters] = useState<ConversationFilters>({ tab: "all", sort: "wait" });
  const [search, setSearch] = useState("");
  const [older, setOlder] = useState<ConversationRow[]>([]);
  const [cursor, setCursor] = useState<{ before?: string; offset?: number } | null>(null);
  const [showSide, setShowSide] = useState(false);
  const now = useNow();

  const state = useQuery({ queryKey: ["cskh", "state"], queryFn: cskhApi.state, refetchInterval: 15000 });
  const list = useQuery({
    queryKey: ["cskh", "conversations", filters] as const,
    queryFn: () => cskhApi.conversations(filters),
    refetchInterval: POLL_MS,
  });
  useEffect(() => {
    setOlder([]);
    setCursor(null);
  }, [filters]);

  const next = cursor ?? {
    before: list.data?.next_before ?? undefined,
    offset: list.data?.next_offset ?? undefined,
  };
  const more = Boolean(next.before) || next.offset != null;
  const loadMore = useMutation({
    mutationFn: () => cskhApi.conversations({ ...filters, before: next.before, offset: next.offset }),
    onSuccess: (data) => {
      setOlder((rows) => [...rows, ...data.conversations]);
      setCursor({ before: data.next_before ?? undefined, offset: data.next_offset ?? undefined });
    },
  });

  const rows = useMemo(() => {
    const first = list.data?.conversations ?? [];
    const seen = new Set(first.map((row) => row.id));
    return [...first, ...older.filter((row) => !seen.has(row.id))];
  }, [list.data, older]);

  const open = (id: string | null) => {
    const query = new URLSearchParams(params.toString());
    if (id) query.set("c", id);
    else query.delete("c");
    router.replace(`/cskh${query.toString() ? `?${query}` : ""}`, { scroll: false });
  };

  // ↑ / ↓ move between customers (never while typing in a field).
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true'], [role='listbox']")) return;
      if (!rows.length) return;
      const at = rows.findIndex((row) => row.id === openId);
      const to = event.key === "ArrowDown" ? Math.min(rows.length - 1, at + 1) : Math.max(0, at - 1);
      if (to !== at && rows[to]) {
        event.preventDefault();
        openRef.current(rows[to].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, openId]);

  if (state.isLoading) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} onRetry={() => void state.refetch()} />;
  const frame = state.data!;
  const alert = frame.wait_alert_minutes ?? DEFAULT_WAIT_ALERT;
  const pageOf = (id: string) => frame.pages.find((page) => page.id === id);

  return (
    // The columns follow the room the content really has (the APM sidebar open or
    // folded), not the window: 2 columns from 768 px, 3 from 1024 px of content.
    <div className="@container flex flex-col gap-3 lg:h-[calc(100dvh-9.5rem)] lg:min-h-[560px]">
      <InboxBar frame={frame} />
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] gap-3 @3xl:grid-cols-[minmax(300px,360px)_minmax(0,1fr)] @5xl:grid-cols-[minmax(290px,350px)_minmax(0,1fr)_minmax(290px,350px)] @7xl:grid-cols-[390px_minmax(0,1fr)_400px]">
        <section
          className={`panel min-h-0 flex-col overflow-hidden ${openId ? "hidden @3xl:flex" : "flex"} max-lg:h-[calc(100dvh-10rem)]`}
          aria-label="Danh sách hội thoại"
        >
          <ListHead
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
                    page={pageOf(row.page_id)}
                    now={now}
                    alert={alert}
                    onOpen={() => open(row.id)}
                  />
                </li>
              ))}
            </ul>
            {more && rows.length ? (
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
            now={now}
            alert={alert}
            onBack={() => open(null)}
            onInfo={() => setShowSide((v) => !v)}
            showSide={showSide}
            page={(pageId) => pageOf(pageId)}
          />
        ) : (
          <section className="panel hidden flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[var(--text-muted)] @3xl:flex @5xl:col-span-2">
            <span className="text-[var(--accent)]">
              <Icon name="send" size={28} />
            </span>
            Chọn một khách bên trái để xem tin nhắn.
            <span className="text-xs">Phím ↑ / ↓ để chuyển khách.</span>
          </section>
        )}
      </div>
    </div>
  );
}

/** One line on top: what this is and the numbers that matter (replaces the big title). */
function InboxBar({ frame }: { frame: InboxState }) {
  const status = frame.work_now
    ? "Trong giờ làm"
    : frame.auto_reply_enabled
      ? "Ngoài giờ · tự trả lời bật"
      : "Ngoài giờ";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h1 className="mr-1 text-lg font-semibold tracking-tight">Hộp chat page</h1>
      <span className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-0.5 text-xs font-semibold">
        {frame.pages.length} page
      </span>
      <span
        className="rounded-full bg-[var(--magenta)] px-2.5 py-0.5 text-xs font-bold text-[var(--surface)]"
        data-testid="cskh-unread-total"
      >
        {frame.unread_total} chưa đọc
      </span>
      <span
        className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
          frame.work_now
            ? "bg-[var(--good-soft)] text-[var(--good)]"
            : "bg-[var(--warn-soft)] text-[var(--warn)]"
        }`}
      >
        {status}
      </span>
    </div>
  );
}

// --- the list -------------------------------------------------------------------------------

function ListHead({
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
  const [panel, setPanel] = useState(false);
  const page = frame.pages.find((p) => p.id === filters.page_id);
  const label = frame.labels.find((l) => l.id === filters.label_id);
  const tag = page?.pancake_tags.find((t) => t.id === filters.pancake_tag);
  const campaign = frame.campaigns.find((c) => c.id === filters.campaign_id);
  const stages = frame.stages ?? STAGES;
  const chips: { key: string; text: string; clear: Partial<ConversationFilters> }[] = [];
  if (page)
    chips.push({
      key: "page",
      text: `Page: ${pageShort(page.name, page.short_name)}`,
      clear: { page_id: undefined, pancake_tag: undefined },
    });
  if (label) chips.push({ key: "label", text: `Nhãn: ${label.name}`, clear: { label_id: undefined } });
  if (tag) chips.push({ key: "tag", text: `Thẻ: ${tag.text}`, clear: { pancake_tag: undefined } });
  if (campaign)
    chips.push({ key: "campaign", text: `Chiến dịch: ${campaign.name}`, clear: { campaign_id: undefined } });
  if (filters.stage)
    chips.push({
      key: "stage",
      text: stages.find((s) => s.id === filters.stage)?.label ?? filters.stage,
      clear: { stage: undefined },
    });
  if (filters.mine) chips.push({ key: "mine", text: "Của tôi", clear: { mine: undefined } });
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "all", label: "Tất cả" },
    { key: "unread", label: "Chưa đọc", count: frame.unread_total },
    { key: "callback", label: "Gọi lại", count: frame.callback_total },
    { key: "phone", label: "Có SĐT" },
  ];
  const tab = filters.tab ?? "all";

  return (
    <div className="space-y-2 border-b border-[var(--border)] p-3">
      <div className="relative flex gap-2">
        <form
          className="relative min-w-0 flex-1"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            setFilters({ ...filters, q: search.trim() || undefined });
          }}
        >
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]">
            <Icon name="search" />
          </span>
          <input
            type="search"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              if (!event.target.value) setFilters({ ...filters, q: undefined });
            }}
            placeholder="Mã KH, SĐT, tên, nội dung…"
            aria-label="Tìm hội thoại"
            className={`${fieldClass} pl-9`}
          />
        </form>
        <button
          type="button"
          className={`${toolButton} min-h-11`}
          aria-expanded={panel}
          aria-haspopup="dialog"
          onClick={() => setPanel((v) => !v)}
        >
          <Icon name="filter" />
          Lọc
          {chips.length ? (
            <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-[var(--accent-strong)] px-1 text-[11px] font-bold leading-5 text-[var(--surface)]">
              {chips.length}
            </span>
          ) : null}
        </button>
        {panel ? (
          <FilterPanel frame={frame} filters={filters} setFilters={setFilters} onClose={() => setPanel(false)} />
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {chips.map((chip) => (
          <button
            key={chip.key}
            type="button"
            onClick={() => setFilters({ ...filters, ...chip.clear })}
            className="inline-flex min-h-7 max-w-[14rem] items-center gap-1 truncate rounded-full bg-[var(--accent-soft)] px-2.5 text-xs font-semibold text-[var(--accent-strong)]"
            aria-label={`Bỏ lọc ${chip.text}`}
            data-testid="cskh-filter-chip"
          >
            <span className="truncate">{chip.text}</span>
            <Icon name="close" size={12} />
          </button>
        ))}
        <label className="ml-auto inline-flex items-center gap-1 text-xs text-[var(--text-muted)]">
          Xếp:
          <select
            value={filters.sort ?? "wait"}
            onChange={(event) =>
              setFilters({ ...filters, sort: event.target.value as ConversationFilters["sort"] })
            }
            className="min-h-7 rounded-md bg-transparent pr-1 text-xs font-semibold text-[var(--text)]"
            aria-label="Xếp hội thoại"
          >
            <option value="wait">Chờ lâu nhất</option>
            <option value="recent">Mới nhất</option>
          </select>
        </label>
      </div>
      <div
        role="tablist"
        aria-label="Lọc hội thoại"
        className="grid grid-cols-4 gap-1 rounded-lg bg-[var(--surface-muted)] p-1"
      >
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setFilters({ ...filters, tab: item.key })}
            className={`inline-flex min-h-8 items-center justify-center gap-1 whitespace-nowrap rounded-md px-1 text-xs ${
              tab === item.key
                ? "bg-[var(--surface)] font-semibold text-[var(--accent-strong)] shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {item.label}
            {item.count ? (
              <span
                className={`rounded-full px-1.5 text-[10px] font-bold leading-4 tabular-nums ${
                  item.key === "unread"
                    ? "bg-[var(--magenta)] text-[var(--surface)]"
                    : "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                }`}
              >
                {item.count > 999 ? "999+" : item.count}
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}

/** "Lọc": page, label / Pancake tag / campaign, stage, "Của tôi". */
function FilterPanel({
  frame,
  filters,
  setFilters,
  onClose,
}: {
  frame: InboxState;
  filters: ConversationFilters;
  setFilters: (value: ConversationFilters) => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (box.current && !box.current.contains(target) && !(target as HTMLElement).closest?.("[data-select-panel]"))
        onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);
  const page = frame.pages.find((p) => p.id === filters.page_id);
  const field = "min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";
  return (
    <div
      ref={box}
      role="dialog"
      aria-label="Lọc hội thoại"
      className="absolute right-0 top-full z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] space-y-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 shadow-[var(--shadow-pop)]"
    >
      <label className="block space-y-1 text-xs font-semibold text-[var(--text-muted)]">
        Page
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
              {p.short_name ? `${p.short_name} · ` : ""}
              {p.name}
              {p.unread ? ` (${p.unread})` : ""}
            </option>
          ))}
        </Select>
      </label>
      <label className="block space-y-1 text-xs font-semibold text-[var(--text-muted)]">
        Nhãn, thẻ, chiến dịch
        <select
          className={field}
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
          <option value="">Tất cả</option>
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
        </select>
      </label>
      <label className="block space-y-1 text-xs font-semibold text-[var(--text-muted)]">
        Trạng thái
        <select
          className={field}
          aria-label="Lọc theo trạng thái"
          value={filters.stage ?? ""}
          onChange={(event) => setFilters({ ...filters, stage: (event.target.value || undefined) as Stage })}
        >
          <option value="">Mọi trạng thái</option>
          {(frame.stages ?? STAGES).map((stage) => (
            <option key={stage.id} value={stage.id}>
              {stage.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex min-h-10 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(filters.mine)}
          onChange={(event) => setFilters({ ...filters, mine: event.target.checked || undefined })}
          className="h-4 w-4"
        />
        Của tôi <span className="text-xs text-[var(--text-muted)]">(mình đã trả lời hoặc được giao)</span>
      </label>
      <div className="flex justify-between gap-2">
        <button
          type="button"
          className={smallButton}
          onClick={() =>
            setFilters({ tab: filters.tab, sort: filters.sort, q: filters.q })
          }
        >
          Bỏ lọc
        </button>
        <button type="button" className={smallButton} onClick={onClose}>
          Xong
        </button>
      </div>
    </div>
  );
}

function ConversationItem({
  row,
  selected,
  page,
  now,
  alert,
  onOpen,
}: {
  row: ConversationRow;
  selected: boolean;
  page: InboxPage | undefined;
  now: number;
  alert: number;
  onOpen: () => void;
}) {
  const wait = waitMinutes(row, now);
  const staff = page?.pancake_users.find((user) => user.id === row.assignee_ids[0])?.name;
  const unread = row.unread > 0;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={selected ? "true" : undefined}
      className={`relative flex w-full gap-3 border-b border-[var(--border)] py-2.5 pl-3 pr-3 text-left ${
        selected
          ? "bg-[var(--accent-soft)] before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:bg-[var(--accent-strong)]"
          : "hover:bg-[var(--surface-muted)]"
      }`}
      data-testid="cskh-row"
    >
      <Avatar name={row.customer_name} />
      <span className="min-w-0 flex-1 space-y-0.5">
        <span className="flex items-center gap-2">
          <span
            className={`min-w-0 flex-1 truncate text-sm ${
              unread ? "font-bold text-[var(--accent-strong)]" : "font-medium text-[var(--text)]"
            }`}
          >
            {row.customer_name}
          </span>
          <span
            className={`shrink-0 text-xs tabular-nums ${
              unread ? "font-semibold text-[var(--accent-strong)]" : "text-[var(--text-muted)]"
            }`}
          >
            {listTime(row.last_at, now)}
          </span>
          {unread ? <UnreadBadge count={row.unread} /> : null}
        </span>
        <span
          className={`block truncate text-[13px] ${
            unread ? "font-semibold text-[var(--text)]" : "text-[var(--text-muted)]"
          }`}
        >
          {row.last_out ? <span className="font-normal text-[var(--text-muted)]">Bạn: </span> : null}
          {row.last_text}
        </span>
        <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <PageTag
            pageId={row.page_id}
            name={row.page_name}
            short={row.page_short || page?.short_name}
            color={row.page_color || page?.color}
          />
          {row.phones.length ? (
            <span className="inline-flex items-center gap-0.5" title="Có số điện thoại">
              <Icon name="phone" size={12} />
              SĐT
            </span>
          ) : null}
          {row.has_qr ? (
            <span className="inline-flex items-center" title="Có mã QR">
              <Icon name="qr" size={12} />
            </span>
          ) : null}
          {staff ? <span className="truncate">· {staff}</span> : null}
          <span className="ml-auto" />
          {wait !== null ? <WaitClock minutes={wait} alert={alert} /> : null}
        </span>
        {row.match ? (
          <span
            className="mt-1 block truncate rounded bg-[var(--warn-soft)] px-1.5 text-[11px] text-[var(--text)]"
            data-testid="cskh-match"
          >
            {row.match.why}: {row.match.snippet}
          </span>
        ) : null}
      </span>
    </button>
  );
}

// --- the chat -------------------------------------------------------------------------------

function ChatColumn({
  id,
  frame,
  now,
  alert,
  onBack,
  onInfo,
  showSide,
  page: pageOf,
}: {
  id: string;
  frame: InboxState;
  now: number;
  alert: number;
  onBack: () => void;
  onInfo: () => void;
  showSide: boolean;
  page: (id: string) => InboxPage | undefined;
}) {
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["cskh", "conversation", id],
    queryFn: () => cskhApi.conversation(id),
    refetchInterval: POLL_MS,
  });
  const [confirm, setConfirm] = useState<{ mode: "phone" | "qr"; phone?: string } | null>(null);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["cskh", "conversation", id] });
    void client.invalidateQueries({ queryKey: ["cskh", "conversations"] });
    void client.invalidateQueries({ queryKey: ["cskh", "state"] });
  };
  const callback = useMutation({ mutationFn: (on: boolean) => cskhApi.setCallback(id, on), onSuccess: refresh });
  const stage = useMutation({ mutationFn: (value: Stage) => cskhApi.setStage(id, value), onSuccess: refresh });
  const scan = useMutation({ mutationFn: () => cskhApi.scanQr(id), onSuccess: refresh });
  const unread = useMutation({ mutationFn: () => cskhApi.markUnread(id), onSuccess: refresh });

  if (detail.isLoading)
    return (
      <section className="panel">
        <Loading />
      </section>
    );
  if (detail.error || !detail.data)
    return (
      <section className="panel p-4">
        <button type="button" className={`${smallButton} mb-2 @3xl:hidden`} onClick={onBack}>
          ‹ Danh sách
        </button>
        <ErrorBox error={detail.error} />
      </section>
    );
  const data = detail.data;
  const conv = data.conversation;
  const page = pageOf(conv.page_id);
  const wait = waitMinutes(conv, now);
  const ad = conv.ads[0];
  const stages = frame.stages ?? STAGES;
  const currentStage = conv.stage ?? (conv.callback ? "CALLBACK" : "NEW");

  return (
    <>
      <section
        className="panel flex min-h-[70vh] min-w-0 flex-col overflow-hidden lg:min-h-0"
        aria-label="Hội thoại"
      >
        <header className="flex flex-wrap items-start gap-x-3 gap-y-2 border-b border-[var(--border)] px-4 py-3">
          <button
            type="button"
            className={`${smallButton} @3xl:hidden`}
            onClick={onBack}
            aria-label="Về danh sách"
          >
            <Icon name="back" />
          </button>
          <Avatar name={conv.customer_name} size={44} />
          <div className="min-w-[11rem] flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <p className="max-w-full truncate text-base font-bold text-[var(--accent-strong)]">
                {conv.customer_name}
              </p>
              <PageTag
                pageId={conv.page_id}
                name={data.page.name}
                short={data.page.short_name}
                color={data.page.color}
              />
              {wait !== null ? <WaitClock minutes={wait} alert={alert} long /> : null}
              {!data.page.connected ? (
                <span className="whitespace-nowrap text-xs text-[var(--bad)]">Page chưa nối Pancake</span>
              ) : null}
            </div>
            {ad ? (
              <p
                className="flex min-w-0 items-center gap-1 text-xs text-[var(--text-muted)]"
                data-testid="cskh-ad"
                title={[ad.name, ad.campaign_name, `Mã QC ${ad.ad_id}`].filter(Boolean).join(" · ")}
              >
                <Icon name="ad" size={13} />
                <span className="truncate">
                  Từ quảng cáo: {ad.name || ad.campaign_name || ad.ad_id}
                  {ad.at ? ` · bấm lúc ${formatShortDateTime(ad.at)}` : ""}
                  {conv.ads.length > 1 ? ` · +${conv.ads.length - 1} QC` : ""}
                </span>
                {ad.post_id ? (
                  <a
                    href={`https://facebook.com/${encodeURIComponent(ad.post_id)}`}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="shrink-0 font-semibold text-[var(--accent)] underline"
                  >
                    Xem bài viết
                  </a>
                ) : null}
              </p>
            ) : null}
          </div>
          <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-[var(--text-muted)]">
            Trạng thái
            <select
              value={currentStage}
              disabled={stage.isPending}
              onChange={(event) => stage.mutate(event.target.value as Stage)}
              aria-label="Trạng thái hội thoại"
              className="min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm font-semibold text-[var(--text)]"
            >
              {stages.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className={`${smallButton} @5xl:hidden`} onClick={onInfo} aria-expanded={showSide}>
            <Icon name="info" />
            {showSide ? "Ẩn thẻ khách" : "Thẻ khách"}
          </button>
        </header>
        <MessageList
          messages={data.messages}
          onConfirmPhone={(phone) => setConfirm({ mode: "phone", phone })}
        />
        <div className="space-y-2 border-t border-[var(--border)] p-3">
          <div className="flex flex-wrap items-center gap-2" aria-label="Công cụ hội thoại">
            <TagButton id={id} detail={data} page={page} onDone={refresh} />
            <button
              type="button"
              className={`${toolButton} ${conv.callback ? "border-[var(--warn)] bg-[var(--warn-soft)] text-[var(--warn)]" : ""}`}
              aria-pressed={conv.callback}
              disabled={callback.isPending}
              onClick={() => callback.mutate(!conv.callback)}
            >
              <Icon name="clock" />
              Chờ gọi lại
            </button>
            {conv.qrs.length ? (
              <button type="button" className={toolButton} onClick={() => setConfirm({ mode: "qr" })}>
                <Icon name="qr" />
                Mã QR Zalo
              </button>
            ) : null}
            <RowMenu
              label="Thêm thao tác"
              items={[
                {
                  key: "confirm",
                  label: "Xác nhận số điện thoại",
                  onSelect: () => setConfirm({ mode: "phone" }),
                },
                ...(conv.qrs.length
                  ? [{ key: "qr", label: "Xác nhận QR", onSelect: () => setConfirm({ mode: "qr" as const }) }]
                  : []),
                { key: "scan", label: "Quét lại QR trong ảnh", onSelect: () => scan.mutate() },
                { key: "unread", label: "Đánh dấu chưa đọc", onSelect: () => unread.mutate() },
              ]}
            />
            {scan.isPending ? <span className="text-xs text-[var(--text-muted)]">Đang quét QR…</span> : null}
            {scan.data ? (
              <span className="text-xs text-[var(--text-muted)]">
                Đã quét {scan.data.scanned} ảnh, thấy {scan.data.found} mã
              </span>
            ) : null}
          </div>
          <ErrorLine error={callback.error ?? stage.error ?? scan.error ?? unread.error} />
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
      <aside
        className={`panel min-h-0 flex-col overflow-hidden ${
          showSide
            ? "fixed inset-y-3 right-3 z-40 flex w-[min(400px,calc(100vw-1.5rem))] shadow-[var(--shadow-pop)]"
            : "hidden"
        } @5xl:static @5xl:z-auto @5xl:flex @5xl:w-auto`}
        aria-label="Thẻ khách hàng"
      >
        <SidePanel
          detail={data}
          page={page}
          onClose={onInfo}
          onConfirm={() => setConfirm({ mode: "phone" })}
          onChanged={refresh}
        />
      </aside>
      {confirm ? (
        <OrderConfirmDialog
          mode={confirm.mode}
          phone={confirm.phone}
          detail={data}
          frame={frame}
          onClose={() => setConfirm(null)}
          onDone={refresh}
        />
      ) : null}
    </>
  );
}

/** "Gắn thẻ": the page's Pancake tags to tick, the ones on shown beside the button. */
function TagButton({
  id,
  detail,
  page,
  onDone,
}: {
  id: string;
  detail: ConversationDetail;
  page: InboxPage | undefined;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const tags = page?.pancake_tags ?? [];
  const on = tags.filter((t) => detail.conversation.pancake_tags.includes(t.id));
  const tag = useMutation({
    mutationFn: ({ tagId, add }: { tagId: string; add: boolean }) => cskhApi.pancakeTag(id, tagId, add),
    onSuccess: onDone,
  });
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  if (!tags.length) return null;
  return (
    <div ref={box} className="relative flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        className={toolButton}
        aria-expanded={open}
        disabled={!detail.page.connected}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="tag" />
        Gắn thẻ
      </button>
      {on.map((t) => (
        <span
          key={t.id}
          className="inline-flex min-h-7 items-center rounded-full bg-[var(--accent-soft)] px-2.5 text-xs font-semibold text-[var(--accent-strong)]"
        >
          <span aria-hidden="true" className="mr-1 h-2 w-2 rounded-full" style={{ background: t.color }} />
          {t.text}
        </span>
      ))}
      {open ? (
        <ul
          aria-label="Thẻ Pancake"
          className="absolute bottom-full left-0 z-30 mb-2 max-h-72 w-60 overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1 shadow-[var(--shadow-pop)]"
        >
          {tags.map((t) => {
            const active = detail.conversation.pancake_tags.includes(t.id);
            return (
              <li key={t.id}>
                <button
                  type="button"
                  aria-pressed={active}
                  disabled={tag.isPending}
                  onClick={() => tag.mutate({ tagId: t.id, add: !active })}
                  className="flex min-h-10 w-full items-center gap-2 rounded-lg px-2 text-left text-sm hover:bg-[var(--surface-muted)] disabled:opacity-50"
                >
                  <span aria-hidden="true" className="h-2.5 w-2.5 rounded-full" style={{ background: t.color }} />
                  <span className="flex-1 truncate">{t.text}</span>
                  {active ? (
                    <span className="text-[var(--accent)]">
                      <Icon name="check" />
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      <ErrorLine error={tag.error} />
    </div>
  );
}

function MessageList({
  messages,
  onConfirmPhone,
}: {
  messages: ConversationDetail["messages"];
  onConfirmPhone: (phone: string) => void;
}) {
  const end = useRef<HTMLDivElement | null>(null);
  const count = messages.length;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [count]);
  let lastDay = "";
  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-[var(--surface-muted)] px-4 py-3">
      {messages.length === 0 ? (
        <p className="text-center text-sm text-[var(--text-muted)]">Chưa có tin nhắn.</p>
      ) : null}
      {messages.map((message) => {
        const day = formatDateTime(message.at).split(" ")[0] ?? "";
        const divider = day && day !== lastDay ? day : null;
        lastDay = day || lastDay;
        return (
          <div key={message.id} className="space-y-3">
            {divider ? (
              <p className="text-center">
                <span className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-0.5 text-xs text-[var(--text-muted)]">
                  {divider}
                </span>
              </p>
            ) : null}
            <MessageBubble message={message} onConfirmPhone={onConfirmPhone} />
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}

/** The send box: Ctrl/⌘+Enter sends, Enter breaks a line, "/" opens the quick replies. */
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
      <div className="flex items-stretch gap-2">
        <div className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--surface)] focus-within:border-[var(--accent)] focus-within:shadow-[0_0_0_3px_var(--ring)]">
          <textarea
            ref={box}
            value={text}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS}
            disabled={disabled}
            aria-label="Nội dung tin nhắn"
            placeholder={disabled ? "Page chưa nối Pancake" : "Nhập tin cho khách…"}
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
                if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
                  event.preventDefault();
                  insert(shown[Math.min(active, shown.length - 1)]);
                  return;
                }
                if (event.key === "Escape") {
                  setPicker(false);
                  return;
                }
              }
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                submit();
              }
            }}
            className="block min-h-14 w-full resize-none rounded-t-xl bg-transparent px-3 pt-2.5 text-[var(--text)] outline-none"
          />
          <div className="flex items-center gap-3 whitespace-nowrap px-3 pb-2 text-xs text-[var(--text-muted)]">
            <button
              type="button"
              className="inline-flex items-center gap-1 hover:text-[var(--text)] disabled:opacity-50"
              disabled={disabled}
              onClick={() => {
                setPicker((v) => !v);
                setActive(0);
              }}
            >
              <kbd className="rounded border border-[var(--border)] px-1 font-sans">/</kbd> câu mẫu
            </button>
            <span className="hidden sm:inline">
              <kbd className="rounded border border-[var(--border)] px-1 font-sans">Ctrl</kbd> +{" "}
              <kbd className="rounded border border-[var(--border)] px-1 font-sans">Enter</kbd> gửi
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !text.trim() || send.isPending}
          className="inline-flex min-h-12 shrink-0 items-center gap-2 self-end rounded-xl bg-[var(--accent-strong)] px-5 text-sm font-bold text-[var(--surface)] hover:opacity-90 disabled:opacity-50"
        >
          {send.isPending ? "Đang gửi…" : "Gửi"}
          <Icon name="send" />
        </button>
      </div>
      <ErrorLine error={send.error} />
    </div>
  );
}

// --- the customer card ----------------------------------------------------------------------

function SidePanel({
  detail,
  page,
  onClose,
  onConfirm,
  onChanged,
}: {
  detail: ConversationDetail;
  page: InboxPage | undefined;
  onClose: () => void;
  onConfirm: () => void;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<"customer" | "zalo">("customer");
  return (
    <>
      <div role="tablist" aria-label="Thông tin khách" className="flex shrink-0 border-b border-[var(--border)]">
        <button
          type="button"
          onClick={onClose}
          aria-label="Đóng thẻ khách"
          className="inline-flex min-h-12 w-12 items-center justify-center text-[var(--text-muted)] @5xl:hidden"
        >
          <Icon name="close" />
        </button>
        {(
          [
            ["customer", "Khách hàng", 0],
            ["zalo", "Zalo telesale", detail.zalo_threads],
          ] as const
        ).map(([key, label, count]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`inline-flex min-h-12 flex-1 items-center justify-center gap-1.5 text-sm ${
              tab === key
                ? "border-b-2 border-[var(--accent-strong)] font-bold text-[var(--accent-strong)]"
                : "text-[var(--text-muted)]"
            }`}
          >
            {label}
            {count ? (
              <span className="rounded-full bg-[var(--accent-soft)] px-1.5 text-[11px] font-bold text-[var(--accent-strong)]">
                {count}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "customer" ? (
          <CustomerTab detail={detail} page={page} onConfirm={onConfirm} onChanged={onChanged} />
        ) : (
          <ZaloTab conversationId={detail.conversation.id} />
        )}
      </div>
    </>
  );
}

function CustomerTab({
  detail,
  page: inboxPage,
  onConfirm,
  onChanged,
}: {
  detail: ConversationDetail;
  page: InboxPage | undefined;
  onConfirm: () => void;
  onChanged: () => void;
}) {
  const { conversation: conv, page, orders } = detail;
  const users = inboxPage?.pancake_users ?? [];
  const assign = useMutation({
    mutationFn: (userId: string | null) => cskhApi.assign(conv.id, userId),
    onSuccess: onChanged,
  });
  const firstIn = detail.messages.find((m) => m.direction === "in")?.at ?? null;
  const codes = conv.codes ?? [];
  const orderCodes = new Set(orders.map((o) => o.customer_code.toUpperCase()).filter(Boolean));
  const code = codes[0];
  const phone = conv.phones[0];
  const confirmedPhones = new Set(orders.map((o) => o.phone.replace(/\D/g, "")).filter(Boolean));
  const notes = conv.notes ?? [];
  const sectionTitle = "mb-1.5 text-xs font-bold text-[var(--text)]";
  return (
    <div className="space-y-4 p-4 text-sm" data-testid="cskh-customer">
      <div className="flex items-center gap-3">
        <Avatar name={conv.customer_name} size={48} />
        <div className="min-w-0">
          <p className="truncate text-base font-bold text-[var(--accent-strong)]">{conv.customer_name}</p>
          <p className="text-xs text-[var(--text-muted)]">
            {orders.length ? `${orders.length} đơn đã xác nhận` : "Khách mới"}
            {firstIn ? ` · nhắn lần đầu ${formatShortDateTime(firstIn)}` : ""}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-[var(--surface-muted)] p-3">
          <p className="text-xs text-[var(--text-muted)]">Mã KH</p>
          {code ? (
            <>
              <p className="truncate font-bold tabular-nums text-[var(--accent-strong)]" data-testid="cskh-code">
                {code}
              </p>
              <p className="text-[11px] text-[var(--text-muted)]">
                {orderCodes.has(code) ? "từ đơn đã xác nhận" : "từ ghi chú Pancake"}
                {codes.length > 1 ? ` · +${codes.length - 1} mã` : ""}
              </p>
            </>
          ) : (
            <p className="text-[var(--text-muted)]">chưa có</p>
          )}
        </div>
        <div className="rounded-xl bg-[var(--surface-muted)] p-3">
          <p className="text-xs text-[var(--text-muted)]">Số điện thoại</p>
          {phone ? (
            <>
              <CopyChip text={phone} display={phone} />
              <p
                className={`mt-0.5 text-[11px] font-semibold ${
                  confirmedPhones.has(phone.replace(/\D/g, "")) ? "text-[var(--good)]" : "text-[var(--text-muted)]"
                }`}
              >
                {confirmedPhones.has(phone.replace(/\D/g, "")) ? "Đã xác nhận" : "Chưa xác nhận"}
                {conv.phones.length > 1 ? ` · +${conv.phones.length - 1} số` : ""}
              </p>
            </>
          ) : (
            <p className="text-[var(--text-muted)]">chưa có</p>
          )}
        </div>
      </div>
      {users.length ? (
        <label className="block">
          <span className={`block ${sectionTitle}`}>Người phụ trách</span>
          <select
            aria-label="Người phụ trách"
            value={conv.assignee_ids[0] ?? ""}
            disabled={assign.isPending || !page.connected}
            onChange={(event) => assign.mutate(event.target.value || null)}
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          >
            <option value="">Chưa giao</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
          <ErrorLine error={assign.error} />
        </label>
      ) : null}
      <div>
        <h3 className={sectionTitle}>Ghi chú Pancake</h3>
        {notes.length === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">Chưa có ghi chú.</p>
        ) : (
          <ul className="space-y-2" data-testid="cskh-notes">
            {[...notes].reverse().map((note) => (
              <li
                key={note.id || note.text}
                className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-2.5"
              >
                <p className="whitespace-pre-wrap break-words text-[var(--text)]">{note.text}</p>
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  {[note.by, note.at ? formatShortDateTime(note.at) : ""].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <h3 className="text-xs font-bold text-[var(--text)]">Đơn hàng</h3>
          {!orders.length ? <span className="text-xs text-[var(--text-muted)]">chưa có đơn</span> : null}
        </div>
        <ul className="mb-2 space-y-2">
          {orders.map((order) => (
            <li
              key={order.id}
              className="rounded-lg border border-[var(--border)] p-2 text-xs"
              data-testid="cskh-order"
            >
              <p className="font-semibold">
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
        <button
          type="button"
          onClick={onConfirm}
          className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-[var(--accent-strong)] text-sm font-bold text-[var(--surface)] hover:opacity-90"
        >
          <Icon name="check" />
          Xác nhận đơn
        </button>
      </div>
      <div className="border-t border-[var(--border)] pt-3">
        <h3 className={sectionTitle}>Page</h3>
        <p className="font-semibold text-[var(--accent-strong)]">{page.name}</p>
        <p className="text-xs text-[var(--text-muted)]">
          Marketing: {page.marketer_name ?? "chưa gán"} · Cửa hàng POS: {page.pos_shop_name ?? "chưa có"}
        </p>
        {page.members.length ? (
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {page.members.map((m) => `${m.role_label}: ${m.name}`).join(" · ")}
          </p>
        ) : null}
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
    <div className="space-y-3 p-4 text-sm">
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
