"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type AgentAttachment,
  type AgentInfo,
  type AgentList,
  type AgentMessage,
  type AgentSkillSet,
  type Uuid,
} from "@/lib/api";
import {
  ATTACH_ACCEPT,
  answerError,
  fileSize,
  hideAnswerMedia,
  sendAgentMessage,
  shrinkImage,
  uploadAgentAttachment,
  type AgentStreamEvent,
} from "@/lib/agents";
import { deleteAgentConversationConfirmation } from "@/lib/confirmations";
import { formatShortDateTime } from "@/lib/datetime";
import { AgentMarkdown } from "@/components/agent-markdown";
import { AgentTabs, Badge } from "@/components/agent-common";
import {
  AgentSkillList,
  AgentSkillsDialog,
  skillCount,
  skillSummary,
  useAgentSkills,
} from "@/components/agent-skills";
import { ConfirmButton } from "@/components/confirm";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

/**
 * "Đội agent" (0091): chat with the company's Hermes agents - the agents in a
 * row across the top (avatar, skill count; 2026-11-06), below one's
 * conversations with the chosen agent and the conversation itself (with no
 * conversation open: the agent's skills). On a phone the row stays on top and
 * one column below. The URL holds the
 * choice (`?a=<agent>&c=<conversation>`), so the back button and a shared link
 * work. Every answer is saved on the server, even if this page is closed.
 */

const DATA_RULE = "Không gửi thông tin cá nhân, ảnh, hồ sơ của khách hàng.";
const ATTACH_HINT = "Ảnh, PDF, Word, Excel, PowerPoint, tệp chữ, skill (SKILL.md hoặc .zip)";

/** A file in the composer: uploading, ready (sent by id with the question) or refused. */
type Pending = {
  key: string;
  name: string;
  size: number;
  /** An object URL drawing a picture before it is sent. */
  preview: string | null;
  status: "uploading" | "ready" | "failed";
  attachment?: AgentAttachment;
  error?: string;
};

type Live = {
  question: AgentMessage | null;
  text: string;
  tools: Array<{ tool: string; label: string; emoji: string }>;
};

export function AgentsWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const agentSlug = params.get("a");
  const conversationId = params.get("c");
  const list = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const skills = useAgentSkills();
  const [skillsOf, setSkillsOf] = useState<string | null>(null);

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
  // undefined = still loading, null = the agents' VPS could not say.
  const setOf = (slug: string) =>
    skills.isError
      ? null
      : skills.data === undefined
        ? undefined
        : skills.data.available
          ? (skills.data.agents[slug] ?? null)
          : null;
  const dialogAgent = data.agents.find((item) => item.slug === skillsOf) ?? null;

  // Phone: the agents stay on top (scrolled sideways); below, the chat when one is open, else the conversations.
  const pane = conversationId && agent ? "chat" : "conversations";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="brand-eyebrow">Trợ lý AI</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Đội agent</h1>
        </div>
        <div className="flex items-center gap-2">
          {data.manage ? (
            <Link
              href="/admin/agents"
              className="inline-flex min-h-11 items-center rounded-lg px-3 text-sm text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
            >
              Ai thấy agent nào
            </Link>
          ) : null}
          <AgentTabs current="chat" />
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
        <>
          <AgentStrip
            data={data}
            selected={agentSlug}
            skillSets={(slug) => setOf(slug)}
            onPick={(slug) => go({ a: slug, c: null })}
            onSkills={setSkillsOf}
          />
          <div className="grid h-[calc(100dvh-14.5rem)] min-h-[30rem] grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[17rem_minmax(0,1fr)]">
            <div className={`${pane === "conversations" ? "flex" : "hidden"} min-h-0 min-w-0 flex-col lg:flex`}>
              {agent ? (
                <Conversations
                  agent={agent}
                  selected={conversationId}
                  onBack={() => go({ a: null, c: null })}
                  onPick={(id) => go({ c: id })}
                  onDeleted={(id) => {
                    if (id === conversationId) go({ c: null });
                  }}
                />
              ) : (
                <div className="panel flex h-full items-center justify-center p-4 text-center text-sm text-[var(--text-muted)]">
                  Chọn một agent ở trên để xem các cuộc trò chuyện.
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
                  maxFiles={data.max_attachments ?? 6}
                  onBack={() => go({ c: null })}
                  onGone={() => go({ c: null })}
                />
              ) : (
                <div className="panel hidden h-full min-h-0 flex-col overflow-y-auto p-6 lg:flex">
                  {agent ? (
                    <div className="mx-auto w-full max-w-2xl space-y-4">
                      <div className="flex items-center gap-3">
                        <Badge agent={agent} size="lg" />
                        <div className="min-w-0">
                          <p className="text-lg font-semibold">{agent.name}</p>
                          <p className="text-sm text-[var(--text-muted)]">{agent.description}</p>
                        </div>
                      </div>
                      <p className="text-sm text-[var(--text-muted)]">
                        Mở một cuộc trò chuyện bên trái hoặc tạo cuộc mới. {agent.name} đang có các skill sau:
                      </p>
                      <AgentSkillList set={setOf(agent.slug)} agent={agent} />
                    </div>
                  ) : (
                    <p className="m-auto text-sm text-[var(--text-muted)]">
                      Chọn một agent ở trên: bên phải hiện các skill của agent đó.
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
      <AgentSkillsDialog
        agent={dialogAgent}
        set={dialogAgent ? setOf(dialogAgent.slug) : undefined}
        onClose={() => setSkillsOf(null)}
      />
    </div>
  );
}

/** The agents in one compact row across the page, by group; scrolls sideways (most room left to the chat). */
function AgentStrip({
  data,
  selected,
  skillSets,
  onPick,
  onSkills,
}: {
  data: AgentList;
  selected: string | null;
  skillSets: (slug: string) => AgentSkillSet | null | undefined;
  onPick: (slug: string) => void;
  onSkills: (slug: string) => void;
}) {
  const [query, setQuery] = useState("");
  const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").replace(/đ/gi, "d").toLowerCase();
  const shown = data.agents.filter((agent) =>
    fold(`${agent.name} ${agent.title}`).includes(fold(query.trim())),
  );
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Keep the chosen agent in view (a shared link, the back button).
    row.current?.querySelector('[aria-current="true"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selected]);
  return (
    <nav aria-label="Danh sách agent" className="panel flex items-center gap-2 overflow-hidden p-1.5">
      <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Tìm agent…"
        aria-label="Tìm agent"
        className="min-h-11 w-28 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 text-sm text-[var(--text)] sm:w-36"
      />
      <div ref={row} className="flex min-w-0 flex-1 snap-x items-center gap-1.5 overflow-x-auto [scrollbar-width:thin]">
        {data.groups.map((group) => {
          const agents = shown.filter((agent) => agent.group === group.key);
          if (agents.length === 0) return null;
          return (
            <section key={group.key} aria-label={group.label} title={group.label} className="group/agents flex shrink-0 items-center gap-1.5">
              <span aria-hidden title={group.label} className="h-7 w-px shrink-0 bg-[var(--border)] group-first/agents:hidden" />
              <ul className="flex gap-1.5">
                {agents.map((agent) => {
                  const active = agent.slug === selected;
                  const set = skillSets(agent.slug);
                  const count = skillCount(set ?? undefined);
                  return (
                    <li key={agent.slug} className="flex shrink-0 snap-start">
                      <div
                        className={`flex items-center rounded-full border pr-1 transition-colors ${
                          active
                            ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                            : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => onPick(agent.slug)}
                          aria-current={active ? "true" : undefined}
                          title={`${agent.name} - ${agent.title}`}
                          className="flex min-h-11 items-center gap-2 rounded-full py-1 pl-1 pr-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]"
                        >
                          <Badge agent={agent} size="sm" />
                          <span className={`max-w-[9rem] truncate whitespace-nowrap text-sm ${active ? "font-semibold" : "font-medium"}`}>
                            {agent.name}
                          </span>
                        </button>
                        {count !== null && set ? (
                          <button
                            type="button"
                            onClick={() => onSkills(agent.slug)}
                            title={skillSummary(set)}
                            aria-label={`${count} skill của ${agent.name} - xem danh sách`}
                            className="inline-flex min-h-7 min-w-7 items-center justify-center gap-0.5 rounded-full px-1.5 text-[11px] font-semibold text-[var(--accent-strong)] hover:bg-[var(--accent-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                          >
                            <PuzzleIcon />
                            {count}
                          </button>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
        {shown.length === 0 ? (
          <p className="px-2 text-sm text-[var(--text-muted)]">Không có agent nào khớp.</p>
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
  onDeleted,
}: {
  agent: AgentInfo;
  selected: string | null;
  onBack: () => void;
  onPick: (id: Uuid) => void;
  onDeleted: (id: Uuid) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: Uuid) => api.deleteAgentConversation(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ["agents", "conversations", agent.slug] });
      queryClient.removeQueries({ queryKey: ["agents", "messages", id] });
      onDeleted(id);
    },
  });
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
              <li
                key={item.id}
                className={`group flex items-stretch rounded-lg transition-colors ${
                  active ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "hover:bg-[var(--surface-muted)]"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onPick(item.id)}
                  aria-current={active ? "true" : undefined}
                  className="flex min-h-11 min-w-0 flex-1 flex-col justify-center rounded-lg px-2.5 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                >
                  <span className={`block truncate text-sm ${active ? "font-semibold" : ""}`}>{item.title}</span>
                  <span className="block text-xs text-[var(--text-muted)]">
                    {item.running ? "Đang trả lời…" : formatShortDateTime(item.last_message_at ?? item.created_at)}
                  </span>
                </button>
                <ConfirmButton
                  spec={deleteAgentConversationConfirmation(item.title)}
                  onConfirm={() => remove.mutate(item.id)}
                  pending={remove.isPending && remove.variables === item.id}
                  error={remove.variables === item.id ? remove.error : undefined}
                  tone="secondary"
                  ariaLabel={`Xoá cuộc trò chuyện ${item.title}`}
                  className="!min-h-11 !min-w-10 shrink-0 !border-0 !bg-transparent !px-2 text-[var(--text-muted)] opacity-70 hover:!text-[var(--bad)] group-hover:opacity-100"
                >
                  <TrashIcon />
                </ConfirmButton>
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
  maxFiles,
  onBack,
  onGone,
}: {
  agent: AgentInfo;
  conversationId: Uuid;
  configured: boolean;
  maxChars: number;
  maxFiles: number;
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
  const [pending, setPending] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const previews = useRef<Set<string>>(new Set());
  useEffect(() => {
    const urls = previews.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);

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
  const remove = useMutation({
    mutationFn: () => api.deleteAgentConversation(conversationId),
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
  const ready = pending.filter((item) => item.status === "ready");
  const uploading = pending.some((item) => item.status === "uploading");
  const canSend = (Boolean(text) || ready.length > 0) && !tooLong && !uploading && configured;

  const drop = (key: string) => {
    setPending((current) => {
      const gone = current.find((item) => item.key === key);
      if (gone?.preview) {
        URL.revokeObjectURL(gone.preview);
        previews.current.delete(gone.preview);
      }
      if (gone?.attachment) void api.removeAgentAttachment(gone.attachment.id).catch(() => undefined);
      return current.filter((item) => item.key !== key);
    });
  };

  const addFiles = (files: File[]) => {
    if (!configured || files.length === 0) return;
    const room = Math.max(0, maxFiles - pending.filter((item) => item.status !== "failed").length);
    if (files.length > room) {
      setFailure(new Error(`Mỗi tin nhắn gửi tối đa ${maxFiles} tệp.`));
    }
    files.slice(0, room).forEach((file, index) => {
      const key = `${Date.now()}-${index}-${file.name}`;
      const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      if (preview) previews.current.add(preview);
      setPending((current) => [...current, { key, name: file.name, size: file.size, preview, status: "uploading" }]);
      void (async () => {
        try {
          const { blob, name } = await shrinkImage(file);
          const attachment = await uploadAgentAttachment(conversationId, blob, name);
          setPending((current) =>
            current.map((item) => (item.key === key ? { ...item, status: "ready", attachment, size: blob.size } : item)),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : "Không tải được tệp lên.";
          setPending((current) =>
            current.map((item) => (item.key === key ? { ...item, status: "failed", error: message } : item)),
          );
        }
      })();
    });
  };

  const send = async () => {
    if (!canSend || running) return;
    setFailure(null);
    setLive({ question: null, text: "", tools: [] });
    const controller = new AbortController();
    abort.current = controller;
    const asked = draft;
    const sentFiles = pending;
    const attachmentIds = ready.map((item) => item.attachment!.id);
    setDraft("");
    setPending([]);
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
        attachmentIds,
      );
      sentFiles.forEach((item) => {
        if (item.preview) {
          URL.revokeObjectURL(item.preview);
          previews.current.delete(item.preview);
        }
      });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setFailure(error);
        // Refused before it was recorded: the words and the files come back to the composer.
        if (error instanceof ApiError) {
          setDraft(asked);
          setPending(sentFiles);
        }
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
    <section
      aria-label={conversation?.title ?? "Cuộc trò chuyện"}
      className="panel relative flex min-h-0 flex-1 flex-col overflow-hidden"
      onDragOver={(event) => {
        if (!configured || !Array.from(event.dataTransfer.types).includes("Files")) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        setDragging(false);
      }}
      onDrop={(event) => {
        if (!configured) return;
        event.preventDefault();
        setDragging(false);
        addFiles(Array.from(event.dataTransfer.files));
      }}
    >
      {dragging ? (
        <div className="pointer-events-none absolute inset-2 z-10 flex flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-[var(--accent)] bg-[var(--accent-soft)] p-4 text-center text-sm text-[var(--accent-strong)]">
          <p className="font-semibold">Thả tệp vào đây để gửi {agent.name}</p>
          <p className="text-xs">{ATTACH_HINT}</p>
        </div>
      ) : null}
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
              spec={deleteAgentConversationConfirmation(conversation?.title ?? "")}
              onConfirm={() => remove.mutate()}
              pending={remove.isPending}
              error={remove.error}
              tone="secondary"
              className="min-h-11 rounded-lg px-2.5 text-xs hover:!text-[var(--bad)]"
            >
              Xoá
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
        {pending.length > 0 ? (
          <ul aria-label="Tệp đính kèm" className="mb-2 flex flex-wrap gap-2">
            {pending.map((item) => (
              <PendingChip key={item.key} item={item} onRemove={() => drop(item.key)} />
            ))}
          </ul>
        ) : null}
        <input
          ref={picker}
          type="file"
          multiple
          accept={ATTACH_ACCEPT}
          className="hidden"
          aria-hidden
          tabIndex={-1}
          data-testid="agent-file-input"
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={() => picker.current?.click()}
            disabled={!configured}
            aria-label="Đính kèm tệp"
            title={`Đính kèm: ${ATTACH_HINT}`}
            className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] disabled:opacity-50"
          >
            <PaperclipIcon />
          </button>
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
            onPaste={(event) => {
              const files = Array.from(event.clipboardData?.files ?? []);
              if (files.length > 0) {
                event.preventDefault();
                addFiles(files);
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
            <PrimaryButton type="submit" disabled={!canSend}>
              {uploading ? "Đang tải…" : "Gửi"}
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
    const files = message.attachments ?? [];
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] min-w-0 rounded-2xl rounded-br-md bg-[var(--accent-soft)] px-3.5 py-2 text-sm text-[var(--accent-strong)] shadow-none">
          {files.length > 0 ? <SentFiles files={files} /> : null}
          {message.content ? <p className="whitespace-pre-wrap break-words">{message.content}</p> : null}
          <p className="mt-1 text-right text-[11px] opacity-70">{formatShortDateTime(message.created_at)}</p>
        </div>
      </div>
    );
  }
  const failed = message.status === "FAILED";
  const cancelled = message.status === "CANCELLED";
  const streaming = message.status === "STREAMING";
  const shown = hideAnswerMedia(message.content ?? "");
  const pictures = message.attachments ?? [];
  return (
    <div className="flex items-end gap-2">
      <Badge agent={agent} size="sm" />
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-bl-md border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-[var(--text)]">
        {shown.text.trim() ? <AgentMarkdown text={shown.text} /> : null}
        {pictures.length > 0 ? (
          <div className={shown.text.trim() ? "mt-2" : undefined}>
            <SentFiles files={pictures} />
          </div>
        ) : null}
        {streaming && shown.pictures > 0 ? <p className="text-xs text-[var(--text-muted)]">🖼 Đang lấy ảnh…</p> : null}
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
  const shown = hideAnswerMedia(live.text);
  return (
    <div className="flex items-end gap-2">
      <Badge agent={agent} size="sm" />
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-bl-md border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-[var(--text)]">
        {shown.text.trim() ? <AgentMarkdown text={shown.text} /> : null}
        <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-muted)]" aria-live="polite">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-strong)]" aria-hidden />
          {shown.pictures > 0
            ? "🖼 Đang gửi ảnh…"
            : lastTool
              ? `${lastTool.emoji ? `${lastTool.emoji} ` : ""}${lastTool.label}`
              : live.text
                ? "Đang viết…"
                : "Đang suy nghĩ…"}
        </p>
      </div>
    </div>
  );
}

function SentFiles({ files }: { files: AgentAttachment[] }) {
  const pictures = files.filter((file) => file.kind === "IMAGE");
  const documents = files.filter((file) => file.kind !== "IMAGE");
  return (
    <div className="mb-1.5 space-y-1.5">
      {pictures.length > 0 ? (
        <div className={`grid gap-1.5 ${pictures.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
          {pictures.map((file) => (
            <a
              key={file.id}
              href={file.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--surface)]"
              title={`Mở ảnh ${file.name}`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- a private API image, not optimisable */}
              <img src={file.url} alt={file.name} loading="lazy" className="max-h-56 w-full object-contain" />
            </a>
          ))}
        </div>
      ) : null}
      {documents.map((file) =>
        file.content_type === "text/html" ? (
          <PageCard key={file.id} file={file} />
        ) : (
        <a
          key={file.id}
          href={file.url}
          download={file.name}
          className="flex min-h-11 items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[var(--text)] hover:bg-[var(--surface-muted)]"
          title={`Tải ${file.name}`}
        >
          <FileBadge kind={file.kind} name={file.name} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{file.name}</span>
            <span className="block truncate text-xs text-[var(--text-muted)]">
              {[file.note, fileSize(file.size_bytes)].filter(Boolean).join(" · ")}
            </span>
          </span>
        </a>
        ),
      )}
    </div>
  );
}

/** A web page the agent wrote: preview in its own (sandboxed) tab, or download. */
function PageCard({ file }: { file: AgentAttachment }) {
  return (
    <div className="flex min-h-11 flex-wrap items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[var(--text)]">
      <FileBadge kind={file.kind} name={file.name} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{file.name}</span>
        <span className="block truncate text-xs text-[var(--text-muted)]">Trang HTML · {fileSize(file.size_bytes)}</span>
      </span>
      <span className="flex gap-1.5">
        <a
          href={`${file.url}/view`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-9 items-center rounded-md bg-[var(--accent)] px-3 text-xs font-medium text-[var(--accent-text)] hover:opacity-90"
        >
          Xem trước
        </a>
        <a
          href={file.url}
          download={file.name}
          className="inline-flex min-h-9 items-center rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 text-xs hover:bg-[var(--surface-muted)]"
          title={`Tải ${file.name}`}
        >
          Tải về
        </a>
      </span>
    </div>
  );
}

function PendingChip({ item, onRemove }: { item: Pending; onRemove: () => void }) {
  const failed = item.status === "failed";
  const detail =
    item.status === "uploading"
      ? "Đang tải lên…"
      : failed
        ? item.error
        : [item.attachment?.note, fileSize(item.size)].filter(Boolean).join(" · ");
  return (
    <li
      className={`flex min-h-11 max-w-full items-center gap-2 rounded-lg border px-2 py-1 sm:max-w-[18rem] ${
        failed ? "border-[var(--bad)]/40 bg-[var(--bad-soft)]" : "border-[var(--border)] bg-[var(--surface-muted)]"
      }`}
    >
      {item.preview ? (
        // eslint-disable-next-line @next/next/no-img-element -- a local preview (blob:)
        <img src={item.preview} alt="" className="h-9 w-9 shrink-0 rounded-md object-cover" />
      ) : (
        <FileBadge kind={item.attachment?.kind ?? "DOCUMENT"} name={item.name} />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-[var(--text)]">{item.name}</span>
        <span className={`block truncate text-[11px] ${failed ? "text-[var(--bad)]" : "text-[var(--text-muted)]"}`}>
          {detail}
        </span>
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Bỏ tệp ${item.name}`}
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] hover:bg-[var(--surface)] hover:text-[var(--text)]"
      >
        ×
      </button>
    </li>
  );
}

/** A small square naming the file's type: PDF, DOC, XLS, PPT, SKILL, TXT. */
function FileBadge({ kind, name }: { kind: AgentAttachment["kind"]; name: string }) {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  const label =
    kind === "SKILL"
      ? "SKILL"
      : extension === "pdf"
        ? "PDF"
        : extension.startsWith("doc")
          ? "DOC"
          : extension.startsWith("xls")
            ? "XLS"
            : extension.startsWith("ppt")
              ? "PPT"
              : extension.startsWith("htm")
                ? "HTML"
                : ["svg", "ai", "eps", "psd", "zip"].includes(extension)
                  ? extension.toUpperCase()
                  : ["ttf", "otf", "woff", "woff2"].includes(extension)
                    ? "FONT"
                    : "TXT";
  const tone =
    kind === "SKILL"
      ? "bg-[var(--magenta-soft)] text-[var(--magenta)]"
      : "bg-[var(--accent-soft)] text-[var(--accent-strong)]";
  return (
    <span
      aria-hidden
      className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-[9px] font-bold tracking-wide ${tone}`}
    >
      {label}
    </span>
  );
}

function PaperclipIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="m21 11.5-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9" />
    </svg>
  );
}

function PuzzleIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 3h6v4a2 2 0 1 0 4 0h2v6h-2a2 2 0 1 0 0 4h2v4H3V7h4a2 2 0 1 1 4 0" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 12.5h9l1-12.5M10 11v5M14 11v5" />
    </svg>
  );
}
