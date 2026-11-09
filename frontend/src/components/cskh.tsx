"use client";

/**
 * "Chăm sóc khách hàng" (0094): customers who write to a Pancake page are
 * answered by a Hermes agent (chatpage by default).
 *
 * - "Chờ duyệt": the agent's suggestions; a person reads the thread (read live
 *   from Pancake, never stored), edits if needed, sends or dismisses.
 * - "Đã gửi" / "Khác": what went out (by a person or automatically) and what
 *   was not answered, with why.
 * - "Kết nối Pancake" (CSKH MANAGE): the pages, their token, webhook address,
 *   agent and mode.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ConfirmButton, type ConfirmSpec } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import {
  MAX_REPLY_CHARS,
  cskhApi,
  parseTagIds,
  type CskhPage,
  type CskhPageBody,
  type CskhPageList,
  type CskhReply,
  type CskhTab,
} from "@/lib/cskh";
import { formatDateTime } from "@/lib/datetime";

type ScreenTab = CskhTab | "pages";

const TAB_LABELS: Record<ScreenTab, string> = {
  waiting: "Chờ duyệt",
  sent: "Đã gửi",
  other: "Khác",
  pages: "Kết nối Pancake",
};

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

export function CskhWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const access = useQuery({ queryKey: ["cskh", "access"], queryFn: cskhApi.access });
  const asked = (params.get("tab") as ScreenTab | null) ?? "waiting";
  const tabs: ScreenTab[] = ["waiting", "sent", "other", ...(access.data?.manage ? ["pages" as const] : [])];
  const tab: ScreenTab = tabs.includes(asked) ? asked : "waiting";
  const waiting = useQuery({
    queryKey: ["cskh", "replies", "waiting"],
    queryFn: () => cskhApi.replies("waiting"),
    enabled: Boolean(access.data?.reply),
    refetchInterval: 20_000,
  });

  if (access.isLoading) return <Loading label="Đang tải…" />;
  if (access.error) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!access.data?.reply) {
    return <Empty message="Bạn chưa có quyền ở mục Chăm sóc khách hàng." />;
  }
  const count = waiting.data?.counts?.SUGGESTED ?? 0;

  return (
    <div className="space-y-4 px-4 py-6 sm:px-6 xl:px-8">
      <PageHeader
        title="Chăm sóc khách hàng"
        subtitle="Khách nhắn vào fanpage (qua Pancake), agent soạn câu trả lời; người duyệt rồi gửi, hoặc agent tự gửi câu hỏi đơn giản."
      />
      {!access.data.configured ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Máy chủ chưa kết nối được đội agent (relay Hermes), nên agent chưa soạn được câu trả lời.
        </div>
      ) : null}
      <nav className="flex flex-wrap gap-2" aria-label="Mục chăm sóc khách hàng">
        {tabs.map((key) => (
          <button
            key={key}
            type="button"
            aria-current={key === tab ? "page" : undefined}
            onClick={() => router.replace(`/cskh?tab=${key}`)}
            className={`min-h-11 rounded-full border px-4 text-sm ${
              key === tab
                ? "border-transparent bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {TAB_LABELS[key]}
            {key === "waiting" && count ? ` (${count})` : ""}
          </button>
        ))}
      </nav>
      {tab === "pages" ? <PagesPanel /> : <RepliesPanel tab={tab} />}
    </div>
  );
}

// --- the replies ------------------------------------------------------------------------

function RepliesPanel({ tab }: { tab: CskhTab }) {
  const list = useQuery({
    queryKey: ["cskh", "replies", tab],
    queryFn: () => cskhApi.replies(tab),
    refetchInterval: tab === "waiting" ? 20_000 : false,
  });
  if (list.isLoading) return <Loading label="Đang tải tin nhắn…" />;
  if (list.error) return <ErrorBox error={list.error} onRetry={() => list.refetch()} />;
  const rows = list.data?.replies ?? [];
  if (!rows.length) {
    return (
      <Empty
        message={
          tab === "waiting"
            ? "Không có câu trả lời nào chờ duyệt."
            : tab === "sent"
              ? "Chưa gửi câu trả lời nào."
              : "Không có gì ở đây."
        }
      />
    );
  }
  return (
    <ul className="space-y-3" data-testid={`cskh-${tab}`}>
      {rows.map((reply) => (
        <li key={reply.id}>
          {tab === "waiting" ? <SuggestionCard reply={reply} /> : <HistoryCard reply={reply} />}
        </li>
      ))}
    </ul>
  );
}

function useRefresh() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ["cskh"] });
    void client.invalidateQueries({ queryKey: ["units", "me"] });
  };
}

function ReplyMeta({ reply }: { reply: CskhReply }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
      <span className="font-semibold text-[var(--text)]">{reply.page_name}</span>
      <span>Khách nhắn {formatDateTime(reply.customer_at)}</span>
      {reply.handoff ? (
        <Pill tone="warn">Agent xin chuyển người{reply.handoff_reason ? `: ${reply.handoff_reason}` : ""}</Pill>
      ) : null}
      {reply.violations.length ? <Pill tone="bad">Phạm luật ({reply.violations.length})</Pill> : null}
      {reply.review_passed === null && reply.status === "SUGGESTED" ? (
        <Pill tone="warn">Chưa kiểm tra luật</Pill>
      ) : null}
      {reply.paused_label ? <Pill>Bot tạm dừng: {reply.paused_label}</Pill> : null}
    </div>
  );
}

function Thread({ replyId }: { replyId: string }) {
  const thread = useQuery({
    queryKey: ["cskh", "thread", replyId],
    queryFn: () => cskhApi.thread(replyId),
  });
  if (thread.isLoading) return <Loading label="Đang đọc hội thoại từ Pancake…" />;
  if (thread.error) return <ErrorBox error={thread.error} onRetry={() => thread.refetch()} />;
  const messages = thread.data?.messages ?? [];
  if (!messages.length) return <Empty message="Pancake không trả về tin nào." />;
  return (
    <ol className="space-y-2 rounded-lg bg-[var(--surface-muted)] p-3" aria-label="Hội thoại">
      {messages.map((message) => (
        <li key={message.id} className={`flex ${message.from_page ? "justify-end" : "justify-start"}`}>
          <div
            className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-sm ${
              message.from_page
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "border border-[var(--border)] bg-[var(--surface)]"
            }`}
          >
            <p className="mb-0.5 text-[11px] text-[var(--text-muted)]">
              {message.from_page ? "Page" : "Khách"} · {formatDateTime(message.at)}
            </p>
            {message.text || "—"}
            {message.attachments ? (
              <span className="ml-1 text-xs text-[var(--text-muted)]">(+{message.attachments} ảnh/tệp)</span>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function sendConfirmation(reply: CskhReply, text: string): ConfirmSpec {
  return {
    title: `Gửi câu trả lời cho khách trên ${reply.page_name}?`,
    description: "Tin nhắn đi ngay vào hộp thư của khách trên fanpage, không thu hồi được.",
    confirmLabel: "Gửi cho khách",
    details: <p className="whitespace-pre-wrap rounded-lg bg-[var(--surface-muted)] p-2 text-sm">{text}</p>,
  };
}

const DISMISS_CONFIRMATION: ConfirmSpec = {
  title: "Bỏ qua câu trả lời này?",
  description: "Khách không nhận được tin này. Bạn trả lời khách trực tiếp trên Pancake nếu cần.",
  confirmLabel: "Bỏ qua",
};

const RESUME_CONFIRMATION: ConfirmSpec = {
  title: "Cho bot trả lời lại hội thoại này?",
  description: "Từ tin tiếp theo của khách, agent lại soạn câu trả lời cho hội thoại này.",
  confirmLabel: "Cho bot trả lời lại",
};

function SuggestionCard({ reply }: { reply: CskhReply }) {
  const refresh = useRefresh();
  const [text, setText] = useState(reply.suggested_text ?? "");
  const [showThread, setShowThread] = useState(false);
  const send = useMutation({ mutationFn: () => cskhApi.send(reply.id, text.trim()), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: () => cskhApi.dismiss(reply.id), onSuccess: refresh });
  const resume = useMutation({
    mutationFn: () => cskhApi.resume(reply.conversation_row_id),
    onSuccess: refresh,
  });
  const tooLong = text.length > MAX_REPLY_CHARS;
  return (
    <article className="panel space-y-3 p-4" data-testid="cskh-suggestion">
      <ReplyMeta reply={reply} />
      {reply.violations.length ? (
        <ul className="space-y-1 rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] p-3 text-sm">
          {reply.violations.map((item, index) => (
            <li key={index}>
              <span className="font-semibold">{item.rule_name || item.rule}</span>
              {item.quote ? <> - “{item.quote}”</> : null}
              {item.reason ? <span className="text-[var(--text-muted)]"> · {item.reason}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {reply.review_note ? <p className="text-xs text-[var(--text-muted)]">{reply.review_note}</p> : null}
      {reply.reason_label ? (
        <p className="text-xs text-[var(--bad)]">Lần gửi trước lỗi: {reply.reason_label}</p>
      ) : null}
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-[var(--text-muted)]">Câu trả lời gửi khách (sửa được)</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={4}
          className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-[var(--text)]"
        />
        <span className={`text-xs ${tooLong ? "text-[var(--bad)]" : "text-[var(--text-muted)]"}`}>
          {text.length}/{MAX_REPLY_CHARS}
        </span>
      </label>
      {showThread ? <Thread replyId={reply.id} /> : null}
      <div className="flex flex-wrap items-center gap-2">
        <SecondaryButton type="button" onClick={() => setShowThread((open) => !open)}>
          {showThread ? "Ẩn hội thoại" : "Xem hội thoại"}
        </SecondaryButton>
        {reply.paused ? (
          <ConfirmButton
            spec={RESUME_CONFIRMATION}
            onConfirm={() => resume.mutate()}
            pending={resume.isPending}
            error={resume.error}
            tone="secondary"
          />
        ) : null}
        <ConfirmButton
          spec={DISMISS_CONFIRMATION}
          onConfirm={() => dismiss.mutate()}
          pending={dismiss.isPending}
          error={dismiss.error}
          tone="secondary"
        />
        <ConfirmButton
          spec={sendConfirmation(reply, text.trim())}
          onConfirm={() => send.mutate()}
          pending={send.isPending}
          error={send.error}
          disabled={!text.trim() || tooLong}
          tone="primary"
          className="ml-auto"
        />
      </div>
    </article>
  );
}

function HistoryCard({ reply }: { reply: CskhReply }) {
  const [showThread, setShowThread] = useState(false);
  const sent = reply.status === "SENT";
  return (
    <article className="panel space-y-2 p-4">
      <ReplyMeta reply={reply} />
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Pill tone={sent ? "good" : reply.status === "FAILED" ? "bad" : "neutral"}>{reply.status_label}</Pill>
        {sent ? (
          <span className="text-[var(--text-muted)]">
            {reply.auto_sent ? "Agent tự gửi" : `Gửi bởi ${reply.decided_by ?? "—"}`} · {formatDateTime(reply.sent_at)}
          </span>
        ) : reply.reason_label ? (
          <span className="text-[var(--text-muted)]">{reply.reason_label}</span>
        ) : reply.decided_by ? (
          <span className="text-[var(--text-muted)]">bởi {reply.decided_by}</span>
        ) : null}
      </div>
      {reply.sent_text || reply.suggested_text ? (
        <p className="whitespace-pre-wrap text-sm">{reply.sent_text || reply.suggested_text}</p>
      ) : null}
      <button
        type="button"
        onClick={() => setShowThread((open) => !open)}
        className="min-h-11 text-sm text-[var(--accent)] underline-offset-2 hover:underline"
      >
        {showThread ? "Ẩn hội thoại" : "Xem hội thoại"}
      </button>
      {showThread ? <Thread replyId={reply.id} /> : null}
    </article>
  );
}

// --- the pages --------------------------------------------------------------------------

function PagesPanel() {
  const pages = useQuery({ queryKey: ["cskh", "pages"], queryFn: cskhApi.pages });
  const [editing, setEditing] = useState<CskhPage | "new" | null>(null);
  if (pages.isLoading) return <Loading label="Đang tải các page…" />;
  if (pages.error) return <ErrorBox error={pages.error} onRetry={() => pages.refetch()} />;
  const data = pages.data as CskhPageList;
  return (
    <div className="space-y-4">
      <SetupGuide />
      {!data.secret_ready ? (
        <div className="rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] p-3 text-sm">
          Máy chủ chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được token page.
        </div>
      ) : null}
      <div className="flex justify-end">
        <PrimaryButton type="button" onClick={() => setEditing("new")}>
          + Thêm page
        </PrimaryButton>
      </div>
      {data.pages.length ? (
        <ul className="space-y-3">
          {data.pages.map((page) => (
            <li key={page.id}>
              <PageCard page={page} agents={data.agents} onEdit={() => setEditing(page)} />
            </li>
          ))}
        </ul>
      ) : (
        <Empty message="Chưa kết nối page nào." />
      )}
      {editing ? (
        <PageForm
          page={editing === "new" ? null : editing}
          list={data}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
  );
}

function SetupGuide() {
  return (
    <details className="panel p-4 text-sm" open>
      <summary className="cursor-pointer font-semibold">Cách nối một page Pancake</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-[var(--text-muted)]">
        <li>Trên Pancake (tài khoản admin page): Cài đặt › Công cụ › copy Page Access Token.</li>
        <li>Bấm “+ Thêm page”, nhập Page ID, tên, dán token, lưu (để tắt), rồi “Kiểm tra token”.</li>
        <li>
          Copy “Địa chỉ webhook” của page, dán vào Pancake › Cài đặt › Công cụ › Webhook, chọn sự kiện
          messaging và conversation, bấm xác minh. Mỗi page bật webhook tốn 1 slot kết nối của gói Pancake.
        </li>
        <li>Tắt tự trả lời khác của page (Botcake, kịch bản chào) để khách không nhận hai câu.</li>
        <li>Bật page ở chế độ “Gợi ý” trước; ổn rồi mới chuyển “Tự động gửi”.</li>
      </ol>
    </details>
  );
}

function PageCard({
  page,
  agents,
  onEdit,
}: {
  page: CskhPage;
  agents: CskhPageList["agents"];
  onEdit: () => void;
}) {
  const refresh = useRefresh();
  const [copied, setCopied] = useState(false);
  const check = useMutation({ mutationFn: () => cskhApi.checkPage(page.id), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: () => cskhApi.updatePage(page.id, { active: !page.active }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => cskhApi.deletePage(page.id), onSuccess: refresh });
  const agent = agents.find((item) => item.slug === page.agent_slug)?.name ?? page.agent_slug;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(page.webhook_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <article className="panel space-y-3 p-4" data-testid="cskh-page">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{page.name}</h3>
        <Pill tone={page.active ? "good" : "neutral"}>{page.active ? "Đang bật" : "Đang tắt"}</Pill>
        <Pill>{page.mode_label}</Pill>
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-[var(--text-muted)]">Page ID</dt>
        <dd className="break-all">{page.page_id}</dd>
        <dt className="text-[var(--text-muted)]">Agent trả lời</dt>
        <dd>{agent}</dd>
        <dt className="text-[var(--text-muted)]">Token</dt>
        <dd>
          {page.has_token ? page.access_token_hint : "Chưa nhập"}
          {page.last_check_ok !== null ? (
            <span className={`ml-2 text-xs ${page.last_check_ok ? "text-[var(--good)]" : "text-[var(--bad)]"}`}>
              {page.last_check_message}
            </span>
          ) : null}
        </dd>
        <dt className="text-[var(--text-muted)]">Địa chỉ webhook</dt>
        <dd className="flex min-w-0 flex-wrap items-center gap-2">
          <code className="min-w-0 break-all rounded bg-[var(--surface-muted)] px-2 py-1 text-xs">
            {page.webhook_url}
          </code>
          <button type="button" onClick={copy} className="min-h-11 text-sm text-[var(--accent)] hover:underline">
            {copied ? "Đã sao chép" : "Sao chép"}
          </button>
        </dd>
        <dt className="text-[var(--text-muted)]">Sự kiện gần nhất</dt>
        <dd>{page.last_event_at ? formatDateTime(page.last_event_at) : "Chưa nhận sự kiện nào"}</dd>
        <dt className="text-[var(--text-muted)]">Nhường cho người</dt>
        <dd>
          {page.handoff_tag_ids.length ? `Nhãn ${page.handoff_tag_ids.join(", ")}` : "Không dùng nhãn"}
          {page.stop_when_assigned ? " · khi hội thoại đã giao nhân viên" : ""}
        </dd>
      </dl>
      <NoticeBox error={check.error} />
      <div className="flex flex-wrap gap-2">
        <SecondaryButton type="button" onClick={onEdit}>
          Sửa
        </SecondaryButton>
        <SecondaryButton type="button" onClick={() => check.mutate()} disabled={check.isPending || !page.has_token}>
          {check.isPending ? "Đang kiểm tra…" : "Kiểm tra token"}
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: page.active ? `Tắt trả lời trên ${page.name}?` : `Bật trả lời trên ${page.name}?`,
            description: page.active
              ? "Tin mới của khách sẽ không được agent soạn trả lời nữa."
              : page.mode === "AUTO"
                ? "Agent sẽ TỰ GỬI câu trả lời đạt kiểm tra luật cho khách nhắn vào page này."
                : "Agent sẽ soạn câu trả lời gợi ý cho mọi tin mới của khách; người duyệt rồi mới gửi.",
            confirmLabel: page.active ? "Tắt" : "Bật",
            variant: page.active ? "destructive" : "primary",
          }}
          onConfirm={() => toggle.mutate()}
          pending={toggle.isPending}
          error={toggle.error}
          disabled={!page.has_token && !page.active}
          tone="secondary"
        />
        <ConfirmButton
          spec={{
            title: `Xoá kết nối ${page.name}?`,
            description:
              "Xoá page, token và toàn bộ lịch sử trả lời của page này trong APM. Nhớ gỡ địa chỉ webhook trên Pancake.",
            confirmLabel: "Xoá page",
            variant: "destructive",
          }}
          onConfirm={() => remove.mutate()}
          pending={remove.isPending}
          error={remove.error}
          tone="danger"
          className="ml-auto"
        />
      </div>
    </article>
  );
}

function PageForm({
  page,
  list,
  onClose,
}: {
  page: CskhPage | null;
  list: CskhPageList;
  onClose: () => void;
}) {
  const refresh = useRefresh();
  const [pageId, setPageId] = useState(page?.page_id ?? "");
  const [name, setName] = useState(page?.name ?? "");
  const [token, setToken] = useState("");
  const [agent, setAgent] = useState(page?.agent_slug ?? "chatpage");
  const [mode, setMode] = useState<string>(page?.mode ?? "SUGGEST");
  const [tags, setTags] = useState(page?.handoff_tag_ids.join(", ") ?? "");
  const [assigned, setAssigned] = useState(page?.stop_when_assigned ?? false);
  const [rotate, setRotate] = useState(false);
  const save = useMutation({
    mutationFn: () => {
      const body: CskhPageBody = {
        name,
        agent_slug: agent,
        mode,
        handoff_tag_ids: parseTagIds(tags),
        stop_when_assigned: assigned,
        ...(token.trim() ? { access_token: token.trim() } : {}),
      };
      if (page) return cskhApi.updatePage(page.id, { ...body, ...(rotate ? { rotate_webhook_key: true } : {}) });
      return cskhApi.createPage({ ...body, page_id: pageId.trim() });
    },
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal open onClose={onClose} busy={save.isPending} title={page ? `Sửa ${page.name}` : "Thêm page Pancake"} size="lg">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <NoticeBox error={save.error} />
          {!page ? (
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Page ID (trên Pancake)</span>
              <input value={pageId} onChange={(e) => setPageId(e.target.value)} required className={FIELD} />
            </label>
          ) : null}
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Tên page</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required className={FIELD} />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Page Access Token</span>
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={page?.has_token ? `Đang lưu ${page.access_token_hint} - để trống để giữ` : "Dán token"}
              className={FIELD}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Agent trả lời</span>
              <Select value={agent} onChange={(e) => setAgent(e.target.value)} className="w-full">
                {list.agents.map((item) => (
                  <option key={item.slug} value={item.slug}>
                    {item.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Cách trả lời</span>
              <Select value={mode} onChange={(e) => setMode(e.target.value)} className="w-full">
                {list.modes.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          {mode === "AUTO" ? (
            <p className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-2 text-xs">
              Tự động chỉ gửi khi AI kiểm tra luật đạt và agent không xin chuyển người; còn lại vẫn chờ duyệt.
            </p>
          ) : null}
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Nhãn chuyển người (ID nhãn Pancake, cách nhau dấu phẩy)</span>
            <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Ví dụ: 12, 15" className={FIELD} />
          </label>
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" checked={assigned} onChange={(e) => setAssigned(e.target.checked)} />
            Hội thoại đã giao cho nhân viên thì bot không trả lời
          </label>
          {page ? (
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input type="checkbox" checked={rotate} onChange={(e) => setRotate(e.target.checked)} />
              Đổi địa chỉ webhook (địa chỉ cũ ngừng nhận ngay, phải dán lại trên Pancake)
            </label>
          ) : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
            Huỷ
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={save.isPending || !name.trim() || (!page && !pageId.trim())}>
            {save.isPending ? "Đang lưu…" : page ? "Lưu" : "Thêm page"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}
