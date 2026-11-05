"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type AgentInfo,
  type AgentList,
  type AgentMessage,
  type Uuid,
} from "@/lib/api";
import { answerError, sendAgentMessage, type AgentStreamEvent } from "@/lib/agents";
import { archiveAgentConversationConfirmation } from "@/lib/confirmations";
import { formatShortDateTime } from "@/lib/datetime";
import { AgentMarkdown } from "@/components/agent-markdown";
import { ConfirmButton } from "@/components/confirm";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

/**
 * "Đội agent" (0091): chat with the company's Hermes agents, like Telegram -
 * the agents on the left, one's conversations with the chosen agent, the
 * conversation itself. On a phone one column at a time. The URL holds the
 * choice (`?a=<agent>&c=<conversation>`), so the back button and a shared link
 * work. Every answer is saved on the server, even if this page is closed.
 */

const DATA_RULE = "Không gửi thông tin cá nhân, ảnh, hồ sơ của khách hàng.";

type Live = {
  question: AgentMessage | null;
  text: string;
  tools: Array<{ tool: string; label: string; emoji: string }>;
};

function Badge({ agent, size = "md" }: { agent: Pick<AgentInfo, "badge" | "name">; size?: "sm" | "md" }) {
  const box = size === "sm" ? "h-8 w-8 text-[10px]" : "h-10 w-10 text-xs";
  return (
    <span
      aria-hidden
      className={`inline-flex ${box} shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--accent-soft)] font-semibold tracking-wide text-[var(--accent-strong)]`}
    >
      {agent.badge || agent.name.slice(0, 2).toUpperCase()}
    </span>
  );
}

export function AgentsWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const agentSlug = params.get("a");
  const conversationId = params.get("c");
  const list = useQuery({ queryKey: ["agents"], queryFn: api.agents });

  const go = (next: { a?: string | null; c?: string | null }) => {
    const query = new URLSearchParams();
    const a = next.a === undefined ? agentSlug : next.a;
    const c = next.c === undefined ? conversationId : next.c;
    if (a) query.set("a", a);
    if (c) query.set("c", c);
    const suffix = query.toString();
    router.replace(suffix ? `/agents?${suffix}` : "/agents", { scroll: false });
  };

  if (list.isPending) return <Loading label="Đang tải đội agent…" />;
  if (list.isError) return <ErrorBox error={list.error} onRetry={() => list.refetch()} />;
  const data = list.data;
  const agent = data.agents.find((item) => item.slug === agentSlug) ?? null;

  // Phone: one column - the chat when a conversation is open, else its agent's list, else the agents.
  const pane = conversationId && agent ? "chat" : agent ? "conversations" : "agents";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="brand-eyebrow">Trợ lý AI</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Đội agent</h1>
        </div>
      </div>
      {!data.configured ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Chưa kết nối tới máy chủ agent nên chưa gửi tin được. Đọc lại các cuộc trò chuyện cũ vẫn được.
        </div>
      ) : null}
      {data.agents.length === 0 ? (
        <div className="panel p-6 text-sm text-[var(--text-muted)]">
          Bạn chưa được chat với agent nào. Hỏi chủ sở hữu nếu bạn cần.
        </div>
      ) : (
        <div className="grid h-[calc(100dvh-13rem)] min-h-[30rem] grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[16rem_15rem_minmax(0,1fr)]">
          <div className={`${pane === "agents" ? "flex" : "hidden"} min-h-0 min-w-0 flex-col lg:flex`}>
            <AgentPicker data={data} selected={agentSlug} onPick={(slug) => go({ a: slug, c: null })} />
          </div>
          <div className={`${pane === "conversations" ? "flex" : "hidden"} min-h-0 min-w-0 flex-col lg:flex`}>
            {agent ? (
              <Conversations
                agent={agent}
                selected={conversationId}
                onBack={() => go({ a: null, c: null })}
                onPick={(id) => go({ c: id })}
              />
            ) : (
              <div className="panel hidden h-full items-center justify-center p-4 text-sm text-[var(--text-muted)] lg:flex">
                Chọn một agent để bắt đầu.
              </div>
            )}
          </div>
          <div className={`${pane === "chat" ? "flex" : "hidden"} min-h-0 min-w-0 flex-col lg:flex`}>
            {agent && conversationId ? (
              <Chat
                key={conversationId}
                agent={agent}
                conversationId={conversationId}
                configured={data.configured}
                maxChars={data.max_message_chars}
                onBack={() => go({ c: null })}
                onGone={() => go({ c: null })}
              />
            ) : (
              <div className="panel hidden h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[var(--text-muted)] lg:flex">
                {agent ? (
                  <>
                    <Badge agent={agent} />
                    <p className="font-medium text-[var(--text)]">{agent.name}</p>
                    <p className="max-w-sm">{agent.description}</p>
                    <p>Mở một cuộc trò chuyện hoặc tạo cuộc mới.</p>
                  </>
                ) : (
                  <p>Chọn một agent, rồi một cuộc trò chuyện.</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AgentPicker({
  data,
  selected,
  onPick,
}: {
  data: AgentList;
  selected: string | null;
  onPick: (slug: string) => void;
}) {
  const [query, setQuery] = useState("");
  const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").replace(/đ/gi, "d").toLowerCase();
  const shown = data.agents.filter((agent) =>
    fold(`${agent.name} ${agent.title}`).includes(fold(query.trim())),
  );
  return (
    <nav aria-label="Danh sách agent" className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="border-b border-[var(--border)] p-2">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm agent…"
          aria-label="Tìm agent"
          className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {data.groups.map((group) => {
          const agents = shown.filter((agent) => agent.group === group.key);
          if (agents.length === 0) return null;
          return (
            <section key={group.key} className="mb-2">
              <h2 className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                {group.label}
              </h2>
              <ul className="space-y-0.5">
                {agents.map((agent) => {
                  const active = agent.slug === selected;
                  return (
                    <li key={agent.slug}>
                      <button
                        type="button"
                        onClick={() => onPick(agent.slug)}
                        aria-current={active ? "true" : undefined}
                        className={`flex min-h-11 w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${
                          active
                            ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                            : "hover:bg-[var(--surface-muted)]"
                        }`}
                      >
                        <Badge agent={agent} size="sm" />
                        <span className="min-w-0 flex-1">
                          <span className={`block truncate text-sm ${active ? "font-semibold" : "font-medium"}`}>
                            {agent.name}
                          </span>
                          <span className="block truncate text-xs text-[var(--text-muted)]">{agent.title}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
        {shown.length === 0 ? (
          <p className="p-3 text-sm text-[var(--text-muted)]">Không có agent nào khớp.</p>
        ) : null}
      </div>
    </nav>
  );
}

function Conversations({
  agent,
  selected,
  onBack,
  onPick,
}: {
  agent: AgentInfo;
  selected: string | null;
  onBack: () => void;
  onPick: (id: Uuid) => void;
}) {
  const queryClient = useQueryClient();
  const conversations = useQuery({
    queryKey: ["agents", "conversations", agent.slug],
    queryFn: () => api.agentConversations(agent.slug),
  });
  const create = useMutation({
    mutationFn: () => api.createAgentConversation(agent.slug),
    onSuccess: (made) => {
      queryClient.invalidateQueries({ queryKey: ["agents", "conversations", agent.slug] });
      onPick(made.id);
    },
  });
  return (
    <section aria-label={`Cuộc trò chuyện với ${agent.name}`} className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-[var(--border)] p-2">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] lg:hidden"
          aria-label="Quay lại danh sách agent"
        >
          ‹
        </button>
        <Badge agent={agent} size="sm" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{agent.name}</p>
          <p className="truncate text-xs text-[var(--text-muted)]">{agent.title}</p>
        </div>
      </div>
      <div className="p-2">
        <SecondaryButton
          type="button"
          className="w-full"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Đang tạo…" : "+ Cuộc trò chuyện mới"}
        </SecondaryButton>
        {create.isError ? (
          <div className="mt-2">
            <NoticeBox error={create.error} onDismiss={() => create.reset()} />
          </div>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {conversations.isPending ? <Loading label="Đang tải…" /> : null}
        {conversations.isError ? (
          <ErrorBox error={conversations.error} onRetry={() => conversations.refetch()} />
        ) : null}
        {conversations.data && conversations.data.conversations.length === 0 ? (
          <p className="p-3 text-sm text-[var(--text-muted)]">Chưa có cuộc trò chuyện nào.</p>
        ) : null}
        <ul className="space-y-0.5">
          {conversations.data?.conversations.map((item) => {
            const active = item.id === selected;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onPick(item.id)}
                  aria-current={active ? "true" : undefined}
                  className={`flex min-h-11 w-full flex-col justify-center rounded-lg px-2.5 py-1.5 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${
                    active ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "hover:bg-[var(--surface-muted)]"
                  }`}
                >
                  <span className={`block truncate text-sm ${active ? "font-semibold" : ""}`}>{item.title}</span>
                  <span className="block text-xs text-[var(--text-muted)]">
                    {item.running ? "Đang trả lời…" : formatShortDateTime(item.last_message_at ?? item.created_at)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

function Chat({
  agent,
  conversationId,
  configured,
  maxChars,
  onBack,
  onGone,
}: {
  agent: AgentInfo;
  conversationId: Uuid;
  configured: boolean;
  maxChars: number;
  onBack: () => void;
  onGone: () => void;
}) {
  const queryClient = useQueryClient();
  const key = ["agents", "messages", conversationId];
  const [live, setLive] = useState<Live | null>(null);
  const [draft, setDraft] = useState("");
  const [failure, setFailure] = useState<unknown>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [older, setOlder] = useState<AgentMessage[]>([]);
  const [olderMore, setOlderMore] = useState<boolean | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  const messages = useQuery({
    queryKey: key,
    queryFn: () => api.agentMessages(conversationId),
    // An answer started elsewhere (another tab, before a reload): look again until it is done.
    refetchInterval: (query) => (query.state.data?.conversation.running && !live ? 3000 : false),
  });
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [messages.data?.messages.length, live?.text, live?.tools.length]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["agents", "conversations", agent.slug] });
  };

  const rename = useMutation({
    mutationFn: (title: string) => api.renameAgentConversation(conversationId, title),
    onSuccess: () => {
      setRenaming(null);
      refresh();
    },
  });
  const archive = useMutation({
    mutationFn: () => api.archiveAgentConversation(conversationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents", "conversations", agent.slug] });
      onGone();
    },
  });
  const loadOlder = useMutation({
    mutationFn: (before: string) => api.agentMessages(conversationId, before),
    onSuccess: (page) => {
      setOlder((current) => [...page.messages, ...current]);
      setOlderMore(page.has_more);
    },
  });

  const sending = live !== null;
  const running = sending || Boolean(messages.data?.conversation.running);
  const text = draft.trim();
  const tooLong = draft.length > maxChars;

  const send = async () => {
    if (!text || tooLong || running || !configured) return;
    setFailure(null);
    setLive({ question: null, text: "", tools: [] });
    const controller = new AbortController();
    abort.current = controller;
    const asked = draft;
    setDraft("");
    try {
      await sendAgentMessage(
        conversationId,
        asked,
        (event: AgentStreamEvent) => {
          setLive((current) => {
            const now = current ?? { question: null, text: "", tools: [] };
            if (event.type === "start") return { ...now, question: event.user_message };
            if (event.type === "delta") return { ...now, text: now.text + event.text };
            if (event.type === "tool") return { ...now, tools: [...now.tools, event] };
            return now;
          });
        },
        controller.signal,
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setFailure(error);
        if (error instanceof ApiError && error.status !== 409) setDraft(asked);
      }
    } finally {
      abort.current = null;
      await queryClient.invalidateQueries({ queryKey: key });
      queryClient.invalidateQueries({ queryKey: ["agents", "conversations", agent.slug] });
      queryClient.invalidateQueries({ queryKey: ["agents"], exact: true });
      setLive(null);
    }
  };

  const stop = async () => {
    try {
      await api.stopAgentAnswer(conversationId);
    } catch (error) {
      setFailure(error);
    }
    refresh();
  };

  const conversation = messages.data?.conversation;
  const shown = useMemo(() => {
    const rows = [...older, ...(messages.data?.messages ?? [])];
    // While streaming, the saved answer row is drawn live instead.
    return live ? rows.filter((row) => row.status !== "STREAMING") : rows;
  }, [older, messages.data, live]);
  const hasMore = olderMore ?? messages.data?.has_more ?? false;

  if (messages.isError) {
    const gone = messages.error instanceof ApiError && messages.error.status === 404;
    return (
      <section className="panel flex-1 p-4">
        {gone ? (
          <p className="text-sm text-[var(--text-muted)]">Không tìm thấy cuộc trò chuyện này.</p>
        ) : (
          <ErrorBox error={messages.error} onRetry={() => messages.refetch()} />
        )}
      </section>
    );
  }

  return (
    <section aria-label={conversation?.title ?? "Cuộc trò chuyện"} className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="flex items-center gap-2 border-b border-[var(--border)] p-2">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--surface-muted)] lg:hidden"
          aria-label="Quay lại các cuộc trò chuyện"
        >
          ‹
        </button>
        <Badge agent={agent} size="sm" />
        {renaming !== null ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (renaming.trim()) rename.mutate(renaming);
            }}
          >
            <input
              value={renaming}
              onChange={(event) => setRenaming(event.target.value)}
              aria-label="Tên cuộc trò chuyện"
              maxLength={120}
              autoFocus
              className="min-h-11 min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
            />
            <SecondaryButton type="submit" disabled={rename.isPending || !renaming.trim()}>
              Lưu
            </SecondaryButton>
            <SecondaryButton type="button" onClick={() => setRenaming(null)}>
              Thôi
            </SecondaryButton>
          </form>
        ) : (
          <>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{conversation?.title ?? "…"}</p>
              <p className="truncate text-xs text-[var(--text-muted)]">{agent.name}</p>
            </div>
            <button
              type="button"
              onClick={() => setRenaming(conversation?.title ?? "")}
              className="min-h-11 rounded-lg px-2.5 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            >
              Đổi tên
            </button>
            <ConfirmButton
              spec={archiveAgentConversationConfirmation(conversation?.title ?? "")}
              onConfirm={() => archive.mutate()}
              pending={archive.isPending}
              error={archive.error}
              tone="secondary"
              className="min-h-11 rounded-lg px-2.5 text-xs"
            >
              Lưu trữ
            </ConfirmButton>
          </>
        )}
      </header>

      <div role="log" aria-live="polite" aria-label="Tin nhắn" className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-[var(--surface-muted)] p-3">
        {messages.isPending ? <Loading label="Đang tải tin nhắn…" /> : null}
        {hasMore && shown.length > 0 ? (
          <div className="text-center">
            <button
              type="button"
              disabled={loadOlder.isPending}
              onClick={() => shown[0]?.created_at && loadOlder.mutate(shown[0].created_at)}
              className="min-h-9 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 text-xs text-[var(--text-muted)] hover:text-[var(--text)]"
            >
              {loadOlder.isPending ? "Đang tải…" : "Xem tin cũ hơn"}
            </button>
          </div>
        ) : null}
        {messages.data && shown.length === 0 && !live ? (
          <div className="mx-auto max-w-md pt-8 text-center text-sm text-[var(--text-muted)]">
            <p className="font-medium text-[var(--text)]">Hỏi {agent.name} điều bạn cần.</p>
            <p className="mt-1">{agent.description}</p>
          </div>
        ) : null}
        {shown.map((message) => (
          <Bubble key={message.id} message={message} agent={agent} />
        ))}
        {live ? (
          <>
            {live.question ? <Bubble message={live.question} agent={agent} /> : null}
            <LiveAnswer live={live} agent={agent} />
          </>
        ) : null}
        <div ref={bottom} />
      </div>

      <form
        className="border-t border-[var(--border)] bg-[var(--surface)] p-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {failure ? (
          <div className="mb-2">
            <NoticeBox error={failure} onDismiss={() => setFailure(null)} />
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          <label className="sr-only" htmlFor="agent-draft">
            Tin nhắn cho {agent.name}
          </label>
          <textarea
            id="agent-draft"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void send();
              }
            }}
            rows={Math.min(6, Math.max(1, draft.split("\n").length))}
            placeholder={configured ? `Nhắn ${agent.name}…` : "Chưa kết nối máy chủ agent"}
            disabled={!configured}
            className="min-h-11 min-w-0 flex-1 resize-none rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 text-[var(--text)] max-sm:text-[16px] sm:text-sm"
          />
          {running ? (
            <SecondaryButton type="button" onClick={() => void stop()}>
              Dừng
            </SecondaryButton>
          ) : (
            <PrimaryButton type="submit" disabled={!text || tooLong || !configured}>
              Gửi
            </PrimaryButton>
          )}
        </div>
        <p className="mt-1.5 flex flex-wrap justify-between gap-2 text-xs text-[var(--text-muted)]">
          <span>{DATA_RULE}</span>
          <span className="hidden sm:inline">Enter gửi · Shift+Enter xuống dòng</span>
          {tooLong ? (
            <span className="text-[var(--bad)]">
              Dài quá {maxChars.toLocaleString("vi-VN")} ký tự ({draft.length.toLocaleString("vi-VN")})
            </span>
          ) : null}
        </p>
      </form>
    </section>
  );
}

function Bubble({ message, agent }: { message: AgentMessage; agent: AgentInfo }) {
  if (message.role === "USER") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[var(--accent-soft)] px-3.5 py-2 text-sm text-[var(--accent-strong)] shadow-none">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          <p className="mt-1 text-right text-[11px] opacity-70">{formatShortDateTime(message.created_at)}</p>
        </div>
      </div>
    );
  }
  const failed = message.status === "FAILED";
  const cancelled = message.status === "CANCELLED";
  const streaming = message.status === "STREAMING";
  return (
    <div className="flex items-end gap-2">
      <Badge agent={agent} size="sm" />
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-bl-md border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-[var(--text)]">
        {message.content ? <AgentMarkdown text={message.content} /> : null}
        {streaming && !message.content ? <p className="text-sm text-[var(--text-muted)]">Đang trả lời…</p> : null}
        {failed ? (
          <p className="mt-1 rounded-md bg-[var(--bad-soft)] px-2 py-1 text-xs text-[var(--bad)]">
            {answerError(message.error_code)}
          </p>
        ) : null}
        {cancelled ? <p className="mt-1 text-xs text-[var(--text-muted)]">Đã dừng.</p> : null}
        <p className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-[var(--text-muted)]">
          <span>{formatShortDateTime(message.finished_at ?? message.created_at)}</span>
          {message.tools_used.length > 0 ? <span>· Đã dùng {message.tools_used.length} công cụ</span> : null}
          {message.duration_ms ? <span>· {(message.duration_ms / 1000).toFixed(1).replace(".", ",")} giây</span> : null}
        </p>
      </div>
    </div>
  );
}

function LiveAnswer({ live, agent }: { live: Live; agent: AgentInfo }) {
  const lastTool = live.tools[live.tools.length - 1];
  return (
    <div className="flex items-end gap-2">
      <Badge agent={agent} size="sm" />
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-bl-md border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-[var(--text)]">
        {live.text ? <AgentMarkdown text={live.text} /> : null}
        <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-muted)]" aria-live="polite">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-strong)]" aria-hidden />
          {lastTool ? `${lastTool.emoji ? `${lastTool.emoji} ` : ""}${lastTool.label}` : live.text ? "Đang viết…" : "Đang suy nghĩ…"}
        </p>
      </div>
    </div>
  );
}
