"use client";

/**
 * The bell in the PR header, and the panel behind it. Step 1F.2.3d.
 *
 * Until this existed, every workflow notification the system produced went to
 * Telegram and nowhere else - so somebody who had not opened the bot learned
 * that their script was approved by scrolling the board until they noticed. The
 * events were there; there was no way to see them.
 *
 * What this is not
 * ----------------
 *
 * Not a social notification feed. There is no infinite scroll, no grouping, no
 * "mark unread", no per-category preferences and no realtime transport. A
 * dropdown of the twenty most recent, a badge, and two write actions - which is
 * the whole of what somebody needs to find out that a piece of work moved.
 *
 * Read state is the server's
 * -------------------------
 *
 * Every count and every row comes from a response, never from local arithmetic.
 * Clicking a notification does not decrement a number in React and hope; it
 * posts, and the badge is re-read. That matters because the badge is the only
 * thing telling somebody whether they have missed something, and a badge that
 * drifts from the truth is worse than no badge - see `markRead` below, which
 * navigates on failure but does **not** touch the count.
 *
 * Refresh, and why there is no socket
 * -----------------------------------
 *
 * `refetchInterval` on the count, at a minute. The repository already had
 * exactly one poller - the AI review panel, at four seconds while a run is
 * active - and its reasoning applies here in the other direction: a websocket
 * for a bell would be a transport, a connection lifecycle and a reconnect
 * strategy for something nobody needs within the minute. The panel's own list
 * is fetched when it opens, so opening the bell is always current.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type AppNotification } from "@/lib/api";
import { formatAgo } from "@/lib/labels";
import { ErrorBox, Loading } from "@/components/states";

/** How often the badge re-reads itself. See the module comment. */
const COUNT_REFRESH_MS = 60_000;

/** How many rows the dropdown asks for. The server caps this at 50 regardless. */
const PANEL_LIMIT = 15;

/** Where a notification goes when clicked, or `null` if it links nowhere. */
export function notificationHref(item: AppNotification): string | null {
  if (item.target_kind === "pr_content" && item.target_id) {
    return `/pr/content/${item.target_id}`;
  }
  return null;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const router = useRouter();

  // The badge. Cheap - a COUNT behind an index - so it may poll; the list is
  // not fetched until somebody opens the panel.
  const count = useQuery({
    queryKey: ["notification-count"],
    queryFn: api.unreadCount,
    refetchInterval: COUNT_REFRESH_MS,
  });

  const list = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.notifications({ limit: PANEL_LIMIT }),
    enabled: open,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["notification-count"] });
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };

  const markRead = useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSettled: refresh,
  });

  const markAll = useMutation({
    mutationFn: api.markAllNotificationsRead,
    onSuccess: refresh,
  });

  const unread = count.data?.unread_count ?? 0;

  /**
   * Mark read, then navigate.
   *
   * The navigation happens whether or not the write succeeded: a failed
   * mark-read must not trap somebody on the panel, and the piece of content is
   * what they asked for. What a failure must not do is leave the badge lying,
   * which is why nothing here adjusts the count locally - `onSettled` re-reads
   * it from the server in both directions.
   */
  const open_ = (item: AppNotification) => {
    const href = notificationHref(item);
    setOpen(false);
    if (!item.read_at) markRead.mutate(item.id);
    if (href) router.push(href);
  };

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Thông báo"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((isOpen) => !isOpen)}
        className="relative flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-lg hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 ? (
          // The number is the label, not the colour: a red dot alone says
          // "something", and the count is the part worth reading.
          <span
            className="absolute -right-1 -top-1 min-w-5 rounded-full bg-red-600 px-1 text-center text-[11px] font-semibold leading-5 text-white"
            aria-label={`${unread} thông báo chưa đọc`}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="Thông báo"
          // Right-aligned: `body` has `overflow-x: hidden`, so a panel that
          // overflowed to the right would be clipped rather than scrollable.
          className="absolute right-0 z-20 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-[var(--border)] bg-[var(--surface)] p-2 shadow-lg"
        >
          <div className="flex items-center justify-between gap-2 px-1 pb-2">
            <p className="text-sm font-semibold">Thông báo</p>
            <button
              type="button"
              onClick={() => markAll.mutate()}
              disabled={markAll.isPending || unread === 0}
              className="rounded-lg px-2 py-1 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] disabled:opacity-50"
            >
              Đánh dấu tất cả đã đọc
            </button>
          </div>

          {list.isPending ? <Loading label="Đang tải thông báo…" /> : null}
          {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
          {markAll.isError ? <ErrorBox error={markAll.error} /> : null}

          {list.data && list.data.items.length === 0 ? (
            <p className="px-1 py-6 text-center text-sm text-[var(--text-muted)]">
              Không có thông báo.
            </p>
          ) : null}

          {list.data && list.data.items.length > 0 ? (
            <ul className="max-h-96 space-y-1 overflow-y-auto">
              {list.data.items.map((item) => (
                <li key={item.id}>
                  <NotificationRow item={item} onOpen={() => open_(item)} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function NotificationRow({ item, onOpen }: { item: AppNotification; onOpen: () => void }) {
  const href = notificationHref(item);
  const unread = !item.read_at;

  const inner = (
    <>
      <span className="flex items-start gap-2">
        {/*
          The dot is redundant with the weight and the background, on purpose:
          "unread" must not be carried by colour alone. It is `aria-hidden`
          because the accessible name below already says so in words.
        */}
        <span
          aria-hidden="true"
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${unread ? "bg-[var(--accent)]" : "bg-transparent"}`}
        />
        <span className="min-w-0">
          <span className={`block text-sm leading-snug ${unread ? "font-semibold" : ""}`}>
            {item.title}
            {unread ? <span className="sr-only"> (chưa đọc)</span> : null}
          </span>
          {/* `break-words`: a content title is user-supplied and unbounded, and
              one long word must wrap rather than widen the panel. */}
          <span className="mt-0.5 block break-words text-xs text-[var(--text-muted)]">
            {item.body}
          </span>
          <span className="mt-0.5 block text-[11px] text-[var(--text-muted)]">
            {formatAgo(item.created_at)}
          </span>
        </span>
      </span>
    </>
  );

  const className = `block w-full rounded-lg p-2 text-left transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
    unread ? "bg-[var(--surface-muted)]" : ""
  }`;

  // A real `<a>` when there is somewhere to go, so middle-click and "open in
  // new tab" work; a `<button>` when there is not, rather than a link to
  // nowhere. `onClick` marks read in both cases.
  if (href) {
    return (
      <Link
        href={href}
        className={className}
        onClick={(event) => {
          event.preventDefault();
          onOpen();
        }}
      >
        {inner}
      </Link>
    );
  }
  return (
    <button type="button" className={className} onClick={onOpen}>
      {inner}
    </button>
  );
}
