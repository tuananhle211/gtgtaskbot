/**
 * "Chăm sóc khách hàng" (0094): Pancake pages answered by a Hermes agent.
 * Mirrors `src/meobot/api/routers/cskh.py`.
 */
import { http } from "@/lib/api";

export type CskhAccess = { reply: boolean; manage: boolean; configured: boolean };

export type CskhTab = "waiting" | "sent" | "other";

export type CskhViolation = {
  rule?: string;
  rule_name?: string | null;
  quote?: string;
  reason?: string;
  suggestion?: string | null;
};

export type CskhReply = {
  id: string;
  page_row_id: string;
  page_name: string;
  conversation_row_id: string;
  status: "WAITING" | "ANSWERING" | "SUGGESTED" | "SENT" | "DISMISSED" | "SKIPPED" | "FAILED";
  status_label: string;
  reason: string | null;
  reason_label: string | null;
  suggested_text: string | null;
  sent_text: string | null;
  handoff: boolean;
  handoff_reason: string | null;
  violations: CskhViolation[];
  review_passed: boolean | null;
  review_note: string | null;
  customer_at: string | null;
  answered_at: string | null;
  sent_at: string | null;
  decided_by: string | null;
  auto_sent: boolean;
  paused: string | null;
  paused_label: string | null;
  paused_until: string | null;
};

export type CskhReplyList = { replies: CskhReply[]; counts: Record<string, number> };

export type CskhThreadMessage = {
  id: string;
  from_page: boolean;
  text: string;
  at: string | null;
  attachments: number;
};

export type CskhPage = {
  id: string;
  page_id: string;
  name: string;
  active: boolean;
  mode: "SUGGEST" | "AUTO";
  mode_label: string;
  agent_slug: string;
  access_token_hint: string;
  has_token: boolean;
  webhook_url: string;
  handoff_tag_ids: string[];
  stop_when_assigned: boolean;
  last_event_at: string | null;
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  last_check_message: string | null;
};

export type CskhPageList = {
  pages: CskhPage[];
  agents: { slug: string; name: string }[];
  modes: { value: string; label: string }[];
  secret_ready: boolean;
  configured: boolean;
};

export type CskhPageBody = Partial<{
  page_id: string;
  name: string;
  mode: string;
  agent_slug: string;
  access_token: string;
  handoff_tag_ids: string[];
  stop_when_assigned: boolean;
  active: boolean;
  rotate_webhook_key: boolean;
}>;

export const cskhApi = {
  access: () => http.get<CskhAccess>("/api/cskh/access"),
  replies: (tab: CskhTab, pageRowId?: string) =>
    http.get<CskhReplyList>(
      `/api/cskh/replies?tab=${tab}${pageRowId ? `&page_row_id=${encodeURIComponent(pageRowId)}` : ""}`,
    ),
  thread: (replyId: string) =>
    http.get<{ messages: CskhThreadMessage[] }>(`/api/cskh/replies/${replyId}/thread`),
  send: (replyId: string, text: string) =>
    http.post<CskhReply>(`/api/cskh/replies/${replyId}/send`, { text }),
  dismiss: (replyId: string) => http.post<CskhReply>(`/api/cskh/replies/${replyId}/dismiss`),
  resume: (conversationRowId: string) =>
    http.post<void>(`/api/cskh/conversations/${conversationRowId}/resume`),
  pages: () => http.get<CskhPageList>("/api/cskh/pages"),
  createPage: (body: CskhPageBody) => http.post<CskhPage>("/api/cskh/pages", body),
  updatePage: (id: string, body: CskhPageBody) => http.patch<CskhPage>(`/api/cskh/pages/${id}`, body),
  deletePage: (id: string) => http.del<void>(`/api/cskh/pages/${id}`),
  checkPage: (id: string) => http.post<CskhPage>(`/api/cskh/pages/${id}/check`),
};

/** Facebook takes at most 2000 characters in one message. */
export const MAX_REPLY_CHARS = 2000;

/** Tag ids typed as "12, 15 18" → ["12", "15", "18"]. */
export function parseTagIds(value: string): string[] {
  return Array.from(new Set(value.split(/[\s,;]+/).map((part) => part.trim()).filter(Boolean)));
}
