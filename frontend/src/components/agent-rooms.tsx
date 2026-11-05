"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type AgentInfo,
  type AgentRoom,
  type AgentRoomAgent,
  type AgentRoomList,
  type AgentRoomMessage,
  type AgentRoomMessageList,
  type Uuid,
} from "@/lib/api";
import { answerError, listenAgentRoom, type AgentRoomEvent } from "@/lib/agents";
import {
  deleteAgentRoomConfirmation,
  leaveAgentRoomConfirmation,
  removeAgentRoomMemberConfirmation,
} from "@/lib/confirmations";
import { formatShortDateTime } from "@/lib/datetime";
import { AgentTabs, Badge } from "@/components/agent-common";
import { AgentMarkdown } from "@/components/agent-markdown";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

/**
 * "Phòng họp" (0093): several agents talk in turns. The owner writes the topic;
 * a round = the chair opens, every other agent speaks once, the chair closes.
 * Invited people read along and write (the agents who speak next read it).
 * The page listens to the room live; the round runs on the server whatever
 * the browser does.
 */

const DATA_RULE = "Không gửi thông tin cá nhân, ảnh, hồ sơ của khách hàng.";

export function AgentRoomsWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const roomId = params.get("r");
  const list = useQuery({ queryKey: ["agent-rooms"], queryFn: api.agentRooms });
  const [creating, setCreating] = useState(false);
  const go = (id: string | null) => router.replace(id ? `/agents/rooms?r=${id}` : "/agents/rooms", { scroll: false });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="brand-eyebrow">Trợ lý AI</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Đội agent</h1>
        </div>
        <AgentTabs current="rooms" />
      </div>
      {list.isPending ? <Loading label="Đang tải phòng họp…" /> : null}
      {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      {list.data ? (
        <>
          {!list.data.configured ? (
            <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
              Chưa kết nối tới máy chủ agent nên các agent chưa họp được. Đọc lại các phòng cũ vẫn được.
            </div>
          ) : null}
          <div className="grid h-[calc(100dvh-13rem)] min-h-[30rem] grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <div className={`${roomId ? "hidden" : "flex"} min-h-0 min-w-0 flex-col lg:flex`}>
              <RoomList data={list.data} selected={roomId} onPick={go} onCreate={() => setCreating(true)} />
            </div>
            <div className={`${roomId ? "flex" : "hidden"} min-h-0 min-w-0 flex-col lg:flex`}>
              {roomId ? (
                <Room
                  key={roomId}
                  roomId={roomId}
                  configured={list.data.configured}
                  limits={list.data}
                  onBack={() => go(null)}
                  onGone={() => go(null)}
                />
              ) : (
                <div className="panel hidden h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[var(--text-muted)] lg:flex">
                  <p className="font-medium text-[var(--text)]">Cho các agent họp với nhau</p>
                  <p className="max-w-md">
                    Tạo phòng, chọn agent và chủ trì, nhắn chủ đề. Chủ trì mở đầu, từng agent nói theo lượt, chủ trì
                    kết luận. Mời thêm người cùng đọc và góp ý.
                  </p>
                </div>
              )}
            </div>
          </div>
          <RoomAgentsDialog
            open={creating}
            limits={list.data}
            onClose={() => setCreating(false)}
            onSaved={(room) => {
              setCreating(false);
              go(room.id);
            }}
          />
        </>
      ) : null}
    </div>
  );
}

function RoomList({
  data,
  selected,
  onPick,
  onCreate,
}: {
  data: AgentRoomList;
  selected: string | null;
  onPick: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <nav aria-label="Phòng họp" className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="border-b border-[var(--border)] p-2">
        <SecondaryButton type="button" className="w-full" onClick={onCreate}>
          + Phòng họp mới
        </SecondaryButton>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {data.rooms.length === 0 ? (
          <p className="p-3 text-sm text-[var(--text-muted)]">Chưa có phòng họp nào.</p>
        ) : null}
        <ul className="space-y-0.5">
          {data.rooms.map((room) => {
            const active = room.id === selected;
            return (
              <li key={room.id}>
                <button
                  type="button"
                  onClick={() => onPick(room.id)}
                  aria-current={active ? "true" : undefined}
                  className={`flex min-h-11 w-full flex-col gap-1 rounded-lg px-2.5 py-2 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${
                    active ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "hover:bg-[var(--surface-muted)]"
                  }`}
                >
                  <span className={`block truncate text-sm ${active ? "font-semibold" : "font-medium"}`}>{room.title}</span>
                  <span className="block truncate text-xs text-[var(--text)]">
                    {room.agents.map((agent) => (agent.chair ? `${agent.name} (chủ trì)` : agent.name)).join(" · ")}
                  </span>
                  <span className="block text-xs text-[var(--text-muted)]">
                    {room.running
                      ? "Đang họp…"
                      : `${room.is_owner ? "Của bạn" : room.owner_name} · ${room.people} người · ${formatShortDateTime(room.last_message_at ?? room.created_at)}`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}

type Live = { messageId: string | null; text: string; tool: string | null };

function Room({
  roomId,
  configured,
  limits,
  onBack,
  onGone,
}: {
  roomId: Uuid;
  configured: boolean;
  limits: AgentRoomList;
  onBack: () => void;
  onGone: () => void;
}) {
  const queryClient = useQueryClient();
  const key = useMemo(() => ["agent-rooms", "messages", roomId], [roomId]);
  const data = useQuery({ queryKey: key, queryFn: () => api.agentRoomMessages(roomId) });
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const me = session.data?.user_id ?? null;
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState<Live>({ messageId: null, text: "", tool: null });
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [inviting, setInviting] = useState(false);
  const bottom = useRef<HTMLDivElement | null>(null);

  const room = data.data?.room ?? null;
  useEffect(() => {
    if (room) setRunning(room.running);
  }, [room]);

  const upsert = (message: AgentRoomMessage) =>
    queryClient.setQueryData<AgentRoomMessageList>(key, (old) => {
      if (!old) return old;
      const at = old.messages.findIndex((item) => item.id === message.id);
      const messages = [...old.messages];
      if (at >= 0) messages[at] = message;
      else messages.push(message);
      return { ...old, messages };
    });

  // The room live, once its messages are here (an event must have a list to land in);
  // reconnects (and re-reads) when the stream drops.
  const loaded = data.isSuccess;
  useEffect(() => {
    if (!loaded) return;
    const controller = new AbortController();
    let stopped = false;
    const onEvent = (event: AgentRoomEvent) => {
      switch (event.type) {
        case "state":
          setRunning(event.running);
          setLive({
            messageId: event.message_id,
            text: event.text,
            tool: event.tools.length ? event.tools[event.tools.length - 1].label : null,
          });
          break;
        case "message":
          upsert(event.message);
          if (event.message.status === "STREAMING") setLive({ messageId: event.message.id, text: "", tool: null });
          break;
        case "delta":
          setLive((old) =>
            old.messageId === event.message_id
              ? { ...old, text: old.text + event.text, tool: null }
              : { messageId: event.message_id, text: event.text, tool: null },
          );
          break;
        case "tool":
          setLive((old) => ({ ...old, messageId: event.message_id, tool: `${event.emoji ? `${event.emoji} ` : ""}${event.label}` }));
          break;
        case "done":
          upsert(event.message);
          setLive({ messageId: null, text: "", tool: null });
          break;
        case "round":
          setRunning(event.running);
          if (!event.running) {
            queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
            queryClient.invalidateQueries({ queryKey: key });
          }
          break;
        case "people":
          queryClient.invalidateQueries({ queryKey: key });
          break;
        case "deleted":
          queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
          onGone();
          break;
      }
    };
    void (async () => {
      while (!stopped) {
        try {
          await listenAgentRoom(roomId, onEvent, controller.signal);
        } catch (error) {
          if (controller.signal.aborted) return;
          if ((error as { status?: number }).status === 404) {
            queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
            onGone();
            return;
          }
        }
        if (stopped) return;
        await new Promise((resolve) => setTimeout(resolve, 3000));
        queryClient.invalidateQueries({ queryKey: key });
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, loaded]);

  const messages = data.data?.messages ?? [];
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, live.text]);

  const send = useMutation({
    mutationFn: (text: string) => api.postAgentRoomMessage(roomId, text),
    onSuccess: (result) => {
      setDraft("");
      upsert(result.message);
      if (result.round_started) setRunning(true);
      queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
    },
  });
  const next = useMutation({
    mutationFn: () => api.startAgentRoomRound(roomId),
    onSuccess: () => setRunning(true),
  });
  const stop = useMutation({ mutationFn: () => api.stopAgentRoomRound(roomId) });
  const remove = useMutation({
    mutationFn: () => api.deleteAgentRoom(roomId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
      onGone();
    },
  });
  const leave = useMutation({
    mutationFn: () => api.removeAgentRoomMember(roomId, me as Uuid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
      onGone();
    },
  });

  if (data.isPending) return <div className="panel flex-1"><Loading label="Đang mở phòng họp…" /></div>;
  if (data.isError || !room) {
    return (
      <div className="panel flex-1 p-3">
        <ErrorBox error={data.error} onRetry={() => data.refetch()} />
      </div>
    );
  }

  const owner = room.is_owner;
  const tooLong = draft.length > limits.max_message_chars;
  const canSend = draft.trim().length > 0 && !tooLong && !send.isPending;
  const failure = send.error ?? next.error ?? stop.error;
  const hint = owner
    ? running
      ? "Đang họp - tin mới sẽ được các agent nói sau đọc."
      : "Tin của bạn mở một vòng họp: chủ trì mở đầu, từng agent nói, chủ trì kết luận."
    : "Tin của bạn được các agent đọc ở lượt nói sau.";

  return (
    <section aria-label={room.title} className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="space-y-2 border-b border-[var(--border)] p-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] lg:hidden"
            aria-label="Quay lại danh sách phòng"
          >
            ‹
          </button>
          <div className="min-w-0 flex-1 basis-[calc(100%-3.5rem)] sm:basis-0">
            <p className="truncate text-sm font-semibold">{room.title}</p>
            <p className="truncate text-xs text-[var(--text-muted)]">
              {room.rounds > 0 ? `${room.rounds} vòng họp` : "Chưa họp vòng nào"} · {room.people.length} người
            </p>
          </div>
          {owner ? (
            <>
              <SecondaryButton type="button" className="!min-h-11 !px-2.5 text-xs" onClick={() => setInviting(true)}>
                Mời người
              </SecondaryButton>
              <SecondaryButton
                type="button"
                className="!min-h-11 !px-2.5 text-xs"
                disabled={running}
                onClick={() => setEditing(true)}
              >
                Sửa phòng
              </SecondaryButton>
              <ConfirmButton
                spec={deleteAgentRoomConfirmation(room.title)}
                onConfirm={() => remove.mutate()}
                pending={remove.isPending}
                error={remove.error}
                tone="secondary"
                className="min-h-11 rounded-lg px-2.5 text-xs hover:!text-[var(--bad)]"
              >
                Xoá
              </ConfirmButton>
            </>
          ) : (
            <ConfirmButton
              spec={leaveAgentRoomConfirmation(room.title)}
              onConfirm={() => leave.mutate()}
              pending={leave.isPending}
              error={leave.error}
              disabled={!me}
              tone="secondary"
              className="min-h-11 rounded-lg px-2.5 text-xs"
            >
              Rời phòng
            </ConfirmButton>
          )}
        </div>
        <ul aria-label="Thứ tự nói" className="flex flex-wrap items-center gap-1.5">
          {room.agents.map((agent, index) => (
            <li
              key={agent.slug}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${
                agent.chair
                  ? "border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text)]"
              } ${agent.active ? "" : "opacity-50"}`}
              title={agent.title}
            >
              <span className="tabular-nums text-[var(--text-muted)]">{index + 1}.</span>
              <span className="font-medium">{agent.name}</span>
              {agent.chair ? <span className="font-semibold">· chủ trì</span> : null}
              {!agent.active ? <span>· đang tắt</span> : null}
            </li>
          ))}
          <li aria-hidden className="mx-1 h-4 w-px bg-[var(--border)]" />
          {room.people.map((person) => (
            <li
              key={person.user_id}
              className="inline-flex items-center rounded-full bg-[var(--surface-muted)] px-2 py-0.5 text-xs text-[var(--text-muted)]"
            >
              {person.name}
              {person.owner ? " (tạo phòng)" : ""}
            </li>
          ))}
        </ul>
      </header>

      <div role="log" aria-live="polite" aria-label="Diễn biến cuộc họp" className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-[var(--surface-muted)] p-3">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-md pt-8 text-center text-sm text-[var(--text-muted)]">
            <p className="font-medium text-[var(--text)]">
              {owner ? "Nhắn chủ đề để bắt đầu họp." : "Chưa có ai nói gì."}
            </p>
            <p className="mt-1">
              {room.agents.find((agent) => agent.chair)?.name ?? "Chủ trì"} mở đầu, từng agent nói theo thứ tự trên,
              rồi chủ trì kết luận.
            </p>
          </div>
        ) : null}
        {messages.map((message, index) => {
          // "Vòng N" above the first turn of each round (people's messages in between do not repeat it).
          const lastRound = messages
            .slice(0, index)
            .reduce<number | null>((seen, item) => (item.speaker === "AGENT" && item.round_no !== null ? item.round_no : seen), null);
          const newRound = message.speaker === "AGENT" && message.round_no !== null && message.round_no !== lastRound;
          return (
            <div key={message.id} className="space-y-3">
              {newRound ? <RoundMark round={message.round_no ?? 0} /> : null}
              <RoomBubble
                message={message}
                agent={room.agents.find((agent) => agent.slug === message.agent_slug)}
                mine={message.speaker === "PERSON" && message.user_id === me}
                live={live.messageId === message.id ? live : null}
              />
            </div>
          );
        })}
        {running && !live.messageId ? (
          <p className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-strong)]" aria-hidden />
            Đang chuyển lượt…
          </p>
        ) : null}
        <div ref={bottom} />
      </div>

      <form
        className="border-t border-[var(--border)] bg-[var(--surface)] p-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSend) send.mutate(draft.trim());
        }}
      >
        {failure ? (
          <div className="mb-2">
            <NoticeBox
              error={failure}
              onDismiss={() => {
                send.reset();
                next.reset();
                stop.reset();
              }}
            />
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          <label className="sr-only" htmlFor="room-draft">
            Tin nhắn trong phòng họp
          </label>
          <textarea
            id="room-draft"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                if (canSend) send.mutate(draft.trim());
              }
            }}
            rows={Math.min(6, Math.max(1, draft.split("\n").length))}
            placeholder={messages.length === 0 && owner ? "Nhắn chủ đề cuộc họp…" : "Nhắn vào phòng…"}
            className="min-h-11 min-w-0 flex-1 resize-none rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 text-[var(--text)] max-sm:text-[16px] sm:text-sm"
          />
          {owner && running ? (
            <SecondaryButton type="button" disabled={stop.isPending} onClick={() => stop.mutate()}>
              Dừng
            </SecondaryButton>
          ) : null}
          {owner && !running && messages.length > 0 && !draft.trim() ? (
            <SecondaryButton type="button" disabled={!configured || next.isPending} onClick={() => next.mutate()}>
              Họp tiếp
            </SecondaryButton>
          ) : null}
          <PrimaryButton type="submit" disabled={!canSend}>
            Gửi
          </PrimaryButton>
        </div>
        <p className="mt-1.5 flex flex-wrap justify-between gap-2 text-xs text-[var(--text-muted)]">
          <span>{hint}</span>
          <span>{DATA_RULE}</span>
          {tooLong ? (
            <span className="text-[var(--bad)]">
              Dài quá {limits.max_message_chars.toLocaleString("vi-VN")} ký tự ({draft.length.toLocaleString("vi-VN")})
            </span>
          ) : null}
        </p>
      </form>

      {owner ? (
        <>
          <RoomAgentsDialog
            open={editing}
            limits={limits}
            room={room}
            onClose={() => setEditing(false)}
            onSaved={() => {
              setEditing(false);
              queryClient.invalidateQueries({ queryKey: key });
              queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
            }}
          />
          <InviteDialog
            open={inviting}
            room={room}
            onClose={() => setInviting(false)}
            onChanged={() => {
              queryClient.invalidateQueries({ queryKey: key });
              queryClient.invalidateQueries({ queryKey: ["agent-rooms"], exact: true });
            }}
          />
        </>
      ) : null}
    </section>
  );
}

function RoundMark({ round }: { round: number }) {
  return (
    <div className="flex items-center gap-2 pt-1" role="separator" aria-label={`Vòng ${round}`}>
      <span className="h-px flex-1 bg-[var(--border)]" aria-hidden />
      <span className="brand-eyebrow !text-[11px]">Vòng {round}</span>
      <span className="h-px flex-1 bg-[var(--border)]" aria-hidden />
    </div>
  );
}

function RoomBubble({
  message,
  agent,
  mine,
  live,
}: {
  message: AgentRoomMessage;
  agent: AgentRoomAgent | undefined;
  mine: boolean;
  live: Live | null;
}) {
  if (message.speaker === "PERSON") {
    return (
      <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
        <div
          className={`max-w-[85%] min-w-0 rounded-2xl px-3.5 py-2 text-sm ${
            mine
              ? "rounded-br-md bg-[var(--accent-soft)] text-[var(--accent-strong)]"
              : "rounded-bl-md border border-[var(--border)] bg-[var(--surface)] text-[var(--text)]"
          }`}
        >
          {!mine ? <p className="mb-0.5 text-xs font-semibold">{message.speaker_label}</p> : null}
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          <p className={`mt-1 text-[11px] opacity-70 ${mine ? "text-right" : ""}`}>{formatShortDateTime(message.created_at)}</p>
        </div>
      </div>
    );
  }
  const streaming = message.status === "STREAMING";
  const text = streaming && live ? live.text || message.content : message.content;
  const shown = agent ?? { badge: "", name: message.speaker_label };
  return (
    <div className="flex items-start gap-2">
      <Badge agent={shown} size="sm" />
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-tl-md border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-[var(--text)]">
        <p className="mb-1 flex flex-wrap items-center gap-x-2 text-xs">
          <span className="font-semibold">{message.speaker_label}</span>
          {message.step_label ? (
            <span
              className={`rounded-full px-1.5 py-px ${
                message.step === "SPEAK"
                  ? "bg-[var(--surface-muted)] text-[var(--text-muted)]"
                  : "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
              }`}
            >
              {message.step_label}
            </span>
          ) : null}
        </p>
        {text ? <AgentMarkdown text={text} /> : null}
        {streaming ? (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-strong)]" aria-hidden />
            {live?.tool ?? (text ? "Đang nói…" : "Đang suy nghĩ…")}
          </p>
        ) : null}
        {message.status === "FAILED" ? (
          <p className="mt-1 rounded-md bg-[var(--bad-soft)] px-2 py-1 text-xs text-[var(--bad)]">{answerError(message.error_code)}</p>
        ) : null}
        {message.status === "CANCELLED" ? <p className="mt-1 text-xs text-[var(--text-muted)]">Đã dừng.</p> : null}
        {!streaming ? (
          <p className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-[var(--text-muted)]">
            <span>{formatShortDateTime(message.finished_at ?? message.created_at)}</span>
            {message.tools_used.length > 0 ? <span>· Đã dùng {message.tools_used.length} công cụ</span> : null}
            {message.duration_ms ? <span>· {(message.duration_ms / 1000).toFixed(1).replace(".", ",")} giây</span> : null}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Create a room, or change its name, agents (speaking order) and chair. */
function RoomAgentsDialog({
  open,
  limits,
  room,
  onClose,
  onSaved,
}: {
  open: boolean;
  limits: AgentRoomList;
  room?: AgentRoom;
  onClose: () => void;
  onSaved: (room: AgentRoom) => void;
}) {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, enabled: open });
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [chair, setChair] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setTitle(room?.title ?? "");
    setPicked(room?.agents.map((agent) => agent.slug) ?? []);
    setChair(room?.agents.find((agent) => agent.chair)?.slug ?? null);
  }, [open, room]);

  const save = useMutation({
    mutationFn: () => {
      const body = { title: title.trim(), agents: picked, chair: chair && picked.includes(chair) ? chair : picked[0] };
      return room ? api.updateAgentRoom(room.id, body) : api.createAgentRoom(body);
    },
    onSuccess: (saved) => {
      save.reset();
      onSaved(saved);
    },
  });

  const all: AgentInfo[] = agents.data?.agents ?? [];
  const bySlug = new Map(all.map((agent) => [agent.slug, agent]));
  const nameOf = (slug: string) => bySlug.get(slug)?.name ?? room?.agents.find((a) => a.slug === slug)?.name ?? slug;
  const toggle = (slug: string) =>
    setPicked((old) => (old.includes(slug) ? old.filter((item) => item !== slug) : [...old, slug]));
  const move = (index: number, by: -1 | 1) =>
    setPicked((old) => {
      const next = [...old];
      const target = index + by;
      if (target < 0 || target >= next.length) return old;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  const countOk = picked.length >= limits.min_agents && picked.length <= limits.max_agents;
  const chairSlug = chair && picked.includes(chair) ? chair : picked[0] ?? null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={save.isPending}
      size="xl"
      title={room ? "Sửa phòng họp" : "Phòng họp mới"}
      description={`Chọn ${limits.min_agents}-${limits.max_agents} agent, xếp thứ tự nói và chọn chủ trì. Chủ trì mở đầu và kết luận mỗi vòng.`}
    >
      <ModalBody className="space-y-4">
        <label className="block space-y-1">
          <span className="text-sm font-medium">Tên phòng</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={120}
            placeholder="Để trống: lấy theo chủ đề đầu tiên"
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] max-sm:text-[16px] sm:text-sm"
          />
        </label>
        <div className="grid gap-4 md:grid-cols-2">
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium">Agent (bạn được chat cùng)</legend>
            {agents.isPending ? <Loading label="Đang tải agent…" /> : null}
            {agents.isError ? <ErrorBox error={agents.error} onRetry={() => agents.refetch()} /> : null}
            <ul className="max-h-72 space-y-0.5 overflow-y-auto rounded-lg border border-[var(--border)] p-1">
              {all.map((agent) => (
                <li key={agent.slug}>
                  <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-2 hover:bg-[var(--surface-muted)]">
                    <input
                      type="checkbox"
                      checked={picked.includes(agent.slug)}
                      onChange={() => toggle(agent.slug)}
                      disabled={!picked.includes(agent.slug) && picked.length >= limits.max_agents}
                    />
                    <Badge agent={agent} size="sm" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{agent.name}</span>
                      <span className="block truncate text-xs text-[var(--text-muted)]">{agent.title}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="space-y-1">
            <p className="text-sm font-medium">Thứ tự nói</p>
            {picked.length === 0 ? (
              <p className="rounded-lg border border-dashed border-[var(--border)] p-3 text-sm text-[var(--text-muted)]">
                Tích agent bên trái để thêm vào phòng.
              </p>
            ) : (
              <ol className="space-y-1">
                {picked.map((slug, index) => (
                  <li key={slug} className="flex min-h-11 items-center gap-2 rounded-lg border border-[var(--border)] px-2">
                    <span className="w-5 text-center text-xs tabular-nums text-[var(--text-muted)]">{index + 1}</span>
                    <span className="min-w-0 flex-1 truncate text-sm">{nameOf(slug)}</span>
                    <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
                      <input type="radio" name="room-chair" checked={chairSlug === slug} onChange={() => setChair(slug)} />
                      Chủ trì
                    </label>
                    <button
                      type="button"
                      aria-label={`Đưa ${nameOf(slug)} lên`}
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                      className="min-h-9 min-w-9 rounded-md text-[var(--text-muted)] hover:bg-[var(--surface-muted)] disabled:opacity-30"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label={`Đưa ${nameOf(slug)} xuống`}
                      disabled={index === picked.length - 1}
                      onClick={() => move(index, 1)}
                      className="min-h-9 min-w-9 rounded-md text-[var(--text-muted)] hover:bg-[var(--surface-muted)] disabled:opacity-30"
                    >
                      ↓
                    </button>
                  </li>
                ))}
              </ol>
            )}
            <p className="text-xs text-[var(--text-muted)]">
              Mỗi vòng: chủ trì mở đầu → các agent còn lại nói theo thứ tự → chủ trì kết luận.
            </p>
          </div>
        </div>
        {save.isError ? <NoticeBox error={save.error} onDismiss={() => save.reset()} /> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
          Huỷ
        </SecondaryButton>
        <PrimaryButton type="button" disabled={!countOk || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Đang lưu…" : room ? "Lưu" : "Tạo phòng"}
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}

/** The owner invites people (only those who may chat with every agent) or takes them out. */
function InviteDialog({
  open,
  room,
  onClose,
  onChanged,
}: {
  open: boolean;
  room: AgentRoom;
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const candidatesKey = ["agent-rooms", "candidates", room.id];
  const candidates = useQuery({
    queryKey: candidatesKey,
    queryFn: () => api.agentRoomCandidates(room.id),
    enabled: open,
  });
  const [query, setQuery] = useState("");
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: candidatesKey });
    onChanged();
  };
  const add = useMutation({
    mutationFn: (userId: Uuid) => api.addAgentRoomMember(room.id, userId),
    onSuccess: refresh,
  });
  const drop = useMutation({
    mutationFn: (userId: Uuid) => api.removeAgentRoomMember(room.id, userId),
    onSuccess: refresh,
  });
  const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").replace(/đ/gi, "d").toLowerCase();
  const shown = (candidates.data?.people ?? []).filter((person) => fold(person.name).includes(fold(query.trim())));
  const members = room.people.filter((person) => !person.owner);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Người trong phòng"
      description="Chỉ mời được người chat được với mọi agent trong phòng - agent đọc những vùng kho mà người kia chưa được xem."
    >
      <ModalBody className="space-y-4">
        <section className="space-y-1">
          <h3 className="text-sm font-medium">Đang trong phòng</h3>
          {members.length === 0 ? <p className="text-sm text-[var(--text-muted)]">Chưa mời ai.</p> : null}
          <ul className="space-y-1">
            {members.map((person) => (
              <li key={person.user_id} className="flex min-h-11 items-center gap-2 rounded-lg border border-[var(--border)] px-3">
                <span className="min-w-0 flex-1 truncate text-sm">{person.name}</span>
                <ConfirmButton
                  spec={removeAgentRoomMemberConfirmation(person.name)}
                  onConfirm={() => drop.mutate(person.user_id)}
                  pending={drop.isPending && drop.variables === person.user_id}
                  error={drop.variables === person.user_id ? drop.error : undefined}
                  tone="secondary"
                  ariaLabel={`Mời ${person.name} ra khỏi phòng`}
                  className="min-h-9 rounded-lg px-2.5 text-xs hover:!text-[var(--bad)]"
                >
                  Mời ra
                </ConfirmButton>
              </li>
            ))}
          </ul>
        </section>
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Mời thêm</h3>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm người…"
            aria-label="Tìm người để mời"
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] max-sm:text-[16px] sm:text-sm"
          />
          {add.isError ? <NoticeBox error={add.error} onDismiss={() => add.reset()} /> : null}
          {candidates.isPending ? <Loading label="Đang tải danh sách…" /> : null}
          {candidates.isError ? <ErrorBox error={candidates.error} onRetry={() => candidates.refetch()} /> : null}
          <ul className="max-h-72 space-y-1 overflow-y-auto">
            {shown.map((person) => (
              <li key={person.user_id} className="flex min-h-11 items-center gap-2 rounded-lg px-3 hover:bg-[var(--surface-muted)]">
                <span className="min-w-0 flex-1">
                  <span className={`block truncate text-sm ${person.allowed ? "" : "text-[var(--text-muted)]"}`}>{person.name}</span>
                  {!person.allowed ? (
                    <span className="block truncate text-xs text-[var(--text-muted)]">
                      Không chat được với: {person.missing.join(", ") || "tài khoản đã khoá"}
                    </span>
                  ) : null}
                </span>
                <SecondaryButton
                  type="button"
                  className="!min-h-9 !px-2.5 text-xs"
                  disabled={!person.allowed || add.isPending}
                  onClick={() => add.mutate(person.user_id)}
                >
                  {add.isPending && add.variables === person.user_id ? "Đang mời…" : "Mời"}
                </SecondaryButton>
              </li>
            ))}
            {candidates.data && shown.length === 0 ? (
              <li className="p-2 text-sm text-[var(--text-muted)]">Không có ai khớp.</li>
            ) : null}
          </ul>
        </section>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Xong
        </SecondaryButton>
      </ModalFooter>
    </Modal>
  );
}
