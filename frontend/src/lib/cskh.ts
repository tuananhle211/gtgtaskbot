/**
 * "Chăm sóc khách hàng" (0102): the company inbox of the Pancake pages, the
 * POS Cake confirmations and the telesales' Zalo nicks. Mirrors
 * `src/meobot/api/routers/cskh.py` (plain JSON, no envelope on success).
 */
import { http } from "@/lib/api";

export type Scope = "NONE" | "OWN" | "ALL";

export type CskhAccess = { inbox: Scope; zalo: Scope; manage: boolean; sees: boolean };

export type PageMember = { user_id: string; name: string; role: "OWNER" | "STAFF"; role_label: string };
export type PancakeTag = { id: string; text: string; color: string };
export type PancakeUser = { id: string; name: string };

export type InboxPage = {
  id: string;
  name: string;
  connected: boolean;
  unread: number;
  label_ids: string[];
  members: PageMember[];
  marketer_user_id: string | null;
  marketer_name: string | null;
  pos_shop_id: string | null;
  pancake_tags: PancakeTag[];
  pancake_users: PancakeUser[];
};

export type Label = { id: string; name: string; color: string; pages?: number };
export type QuickReply = {
  id: string;
  shortcut: string;
  text: string;
  page_id: string | null;
  page_name?: string | null;
};

export type InboxState = {
  pages: InboxPage[];
  labels: Label[];
  quick_replies: QuickReply[];
  campaigns: { id: string; name: string }[];
  order_statuses: string[];
  platforms: string[];
  work_now: boolean;
  auto_reply_enabled: boolean;
  unread_total: number;
  /** Active accounts + their departments' codes (the confirmation's pickers). */
  people?: InboxPerson[];
};

export type InboxPerson = { user_id: string; name: string; departments: string[] };

export type ConversationRow = {
  id: string;
  page_id: string;
  page_name: string;
  psid: string;
  customer_name: string;
  last_text: string;
  last_at: string | null;
  last_out: boolean;
  unread: number;
  callback: boolean;
  phones: string[];
  has_qr: boolean;
  ad_id: string | null;
  ad_name: string | null;
  pancake_tags: string[];
  assignee_ids: string[];
  zalo_linked: boolean;
  match?: { why: string; snippet: string };
};

export type ConversationList = { conversations: ConversationRow[]; next_before: string | null };

export type Attachment = {
  kind: "image" | "sticker" | "video" | "audio" | "file" | "card" | "ad" | "address" | "link";
  url?: string;
  title?: string;
  subtitle?: string;
  image?: string;
  ad_id?: string;
  buttons?: { title: string; url: string }[];
};

export type Message = {
  id: string;
  mid: string;
  direction: "in" | "out";
  kind: "customer" | "staff" | "page" | "auto";
  text: string;
  at: string | null;
  status: string;
  attachments: Attachment[];
  qr: string[] | null;
  sender_name: string;
};

export type QrItem = { text: string; kind: string; mid: string; at: string | null; url: string };

export type AdInfo = {
  ad_id: string;
  post_id: string;
  at: string | null;
  name: string;
  image_url: string;
  campaign_id: string;
  campaign_name: string;
};

export type Order = {
  id: string;
  action: "confirm" | "create";
  confirm_type: "phone" | "qr";
  pos_order_no: string | null;
  phone: string;
  customer_name: string;
  address: string;
  content_name: string;
  order_status: string;
  platform: string;
  marketer_name: string;
  creator_name: string;
  telesale_name: string;
  customer_code: string;
  note: string;
  dup_info: string;
  created_at: string | null;
};

export type ConversationDetail = {
  conversation: ConversationRow & { qrs: QrItem[]; ads: AdInfo[]; last_in_at: string | null };
  page: {
    id: string;
    name: string;
    connected: boolean;
    members: PageMember[];
    marketer_user_id: string | null;
    marketer_name: string | null;
    pos_shop_id: string | null;
    pos_shop_name: string | null;
  };
  messages: Message[];
  orders: Order[];
  zalo_threads: number;
};

export type SeenItem = {
  conversation_id: string;
  page_id: string;
  page_name: string;
  customer_name: string;
  phone: string;
  qr?: string;
  first_at: string | null;
  times: number;
  ad_id: string;
};

export type CheckOrder = {
  source: string;
  page_id: string;
  page_name: string;
  phone: string;
  customer_name: string;
  content_name: string;
  at: string | null;
  order_no: string;
  marketer: string;
  status_name: string | null;
  same_conversation: boolean;
};

export type PhoneCheck = {
  phones: string[];
  is_new: boolean;
  need_ack: boolean;
  own_confirmed: CheckOrder[];
  seen: SeenItem[];
  orders: CheckOrder[];
  self: { phone: string; first_at: string | null; times: number }[];
  repeat_in_conversation: boolean;
  pos_error: string;
  qrs: QrItem[];
  page_marketer: { user_id: string; name: string | null } | null;
  old_marketer: {
    name: string;
    user_id: string | null;
    page_name: string;
    at: string | null;
    locked: boolean;
    lock_days: number;
  } | null;
};

export type ConfirmBody = {
  confirm_type: "phone" | "qr";
  phone: string;
  customer_name: string;
  address: string;
  content_name: string;
  order_status: string;
  platform: string;
  marketer_user_id?: string | null;
  creator_user_id?: string | null;
  telesale_user_id?: string | null;
  telesale_name: string;
  customer_code: string;
  note: string;
  dup_ack: boolean;
};

export type ZaloThread = {
  id: string;
  account_id: string;
  account_name: string;
  account_status: string;
  sale_user_id: string | null;
  sale_name: string | null;
  peer_uid: string;
  peer_name: string;
  peer_avatar: string;
  is_group: boolean;
  last_text: string;
  last_out: boolean;
  last_at: string | null;
  unread: number;
  conversation_id: string | null;
  customer_name: string | null;
  page_id: string | null;
  page_name: string | null;
  conversation_phones: string[];
  customer_code: string;
  link_how: string;
  link_kind: "code" | "phone" | null;
  masked: boolean;
  match?: string;
  snippet?: string;
  messages?: ZaloMessage[];
};

export type ZaloMessage = {
  id: string;
  direction: "in" | "out";
  sender_name: string;
  text: string;
  kind: string;
  at: string | null;
};

export type ZaloForConversation = {
  threads: ZaloThread[];
  codes: string[];
  suggest: ZaloThread[];
  may_link: boolean;
};

export type ZaloPage = {
  conversation: {
    id: string;
    page_id: string;
    page_name: string;
    customer_name: string;
    phones: string[];
    connected: boolean;
  } | null;
  messages: Message[];
  masked: boolean;
};

export type ZaloAccount = {
  id: string;
  name: string;
  phone: string;
  avatar: string;
  user_id: string | null;
  sale_name: string | null;
  status: string;
  status_label: string;
  status_note: string;
  status_at: string | null;
  last_at: string | null;
  threads: number;
  linked: number;
  unlinked: number;
  unread: number;
};

export type ZaloAccounts = {
  accounts: ZaloAccount[];
  bridge: { configured: boolean; alive: boolean };
  may_manage: boolean;
  may_scan: boolean;
  sales: { user_id: string; name: string }[];
};

export type QrState = {
  session?: string;
  state: "starting" | "qr" | "scanned" | "ok" | "expired" | "declined" | "error" | null;
  image?: string | null;
  name?: string | null;
  phone?: string | null;
  avatar?: string | null;
  accountId?: string | null;
  error?: string | null;
};

export type InboxSettings = {
  auto_reply_enabled: boolean;
  work_start: string;
  work_end: string;
  work_days: number[];
  auto_reply_text: string;
  order_statuses: string[];
  mkt_lock_days: number;
  skip_keywords: string[];
  pos_dup_skip_keywords: string[];
};

export type ConnectionPage = {
  id: string;
  name: string;
  username: string;
  active: boolean;
  connected: boolean;
  token_hint: string;
  skipped: boolean;
  marketer_user_id: string | null;
  marketer_name: string | null;
  members: PageMember[];
  label_ids: string[];
  pos_shop_id: string | null;
  pos_shop_name: string | null;
  conversations: number;
  unread: number;
  last_at: string | null;
  polled_at: string | null;
  poll_error: string | null;
  meta_at: string | null;
};

export type ConnectJob = {
  running: boolean;
  stale?: boolean;
  label: string;
  total: number;
  done: number;
  connected: number;
  generated: number;
  conversations: number;
  current: string;
  errors: string[];
  started_at: string | null;
  finished_at: string | null;
  pos_on: boolean;
  pos_linked: number;
  pos_missing: string[];
};

export type Connections = {
  pancake_accounts: {
    id: string;
    label: string;
    token_hint: string;
    expires_on: string | null;
    page_count: number;
    active_count: number;
    last_error: string | null;
    checked_at: string | null;
  }[];
  pos_keys: {
    id: string;
    label: string;
    key_hint: string;
    shop_count: number;
    last_error: string | null;
    checked_at: string | null;
  }[];
  pos_shops: { id: string; name: string; pages: number; dup_check: boolean }[];
  pages: ConnectionPage[];
  labels: Label[];
  people: { user_id: string; name: string; member_code: string | null }[];
  new_pages: number;
  new_pages_checked_at: string | null;
  job: ConnectJob | null;
  settings: InboxSettings;
  secret_ready: boolean;
  qr_ready: boolean;
  member_roles: { value: "OWNER" | "STAFF"; label: string }[];
  label_colors: string[];
};

export type PancakeListing = {
  id: string;
  name: string;
  username: string;
  platform: string;
  status: "activated" | "inactivated";
  account_label: string;
  connected: boolean;
  known: boolean;
  skipped: boolean;
};

export type MemberItem = { user_id: string; role: "OWNER" | "STAFF" };

export type ConversationFilters = {
  page_id?: string;
  label_id?: string;
  pancake_tag?: string;
  campaign_id?: string;
  tab?: "all" | "unread" | "callback" | "phone";
  q?: string;
  before?: string;
};

function query(params: Record<string, string | undefined | null | boolean>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const C = "/api/cskh";

export const cskhApi = {
  access: () => http.get<CskhAccess>(`${C}/access`),
  // --- page inbox
  state: () => http.get<InboxState>(`${C}/state`),
  conversations: (filters: ConversationFilters) =>
    http.get<ConversationList>(`${C}/conversations${query(filters)}`),
  conversation: (id: string) => http.get<ConversationDetail>(`${C}/conversations/${id}`),
  send: (id: string, text: string, clearCallback = false) =>
    http.post<Message>(`${C}/conversations/${id}/messages`, { text, clear_callback: clearCallback }),
  setCallback: (id: string, on: boolean) => http.post<void>(`${C}/conversations/${id}/callback`, { on }),
  markUnread: (id: string) => http.post<void>(`${C}/conversations/${id}/unread`),
  pancakeTag: (id: string, tagId: string, add: boolean) =>
    http.post<{ pancake_tags: string[] }>(`${C}/conversations/${id}/pancake-tags`, { tag_id: tagId, add }),
  assign: (id: string, userId: string | null) =>
    http.post<{ assignee_ids: string[] }>(`${C}/conversations/${id}/assign`, { user_id: userId }),
  scanQr: (id: string) =>
    http.post<{ scanned: number; found: number; qrs: QrItem[] }>(`${C}/conversations/${id}/scan-qr`),
  phoneCheck: (id: string, phone = "") =>
    http.get<PhoneCheck>(`${C}/conversations/${id}/phone-check${query({ phone })}`),
  confirm: (id: string, body: ConfirmBody) =>
    http.post<{ order: Order; action: "confirm" | "create" }>(`${C}/conversations/${id}/confirm`, body),
  conversationZalo: (id: string) => http.get<ZaloForConversation>(`${C}/conversations/${id}/zalo`),
  // --- Zalo
  zaloThreads: (params: { account_id?: string; user_id?: string; tab?: string; q?: string }) =>
    http.get<{ threads: ZaloThread[] }>(`${C}/zalo/threads${query(params)}`),
  zaloThread: (id: string) =>
    http.get<{ thread: ZaloThread; messages: ZaloMessage[] }>(`${C}/zalo/threads/${id}`),
  zaloThreadPage: (id: string) => http.get<ZaloPage>(`${C}/zalo/threads/${id}/page`),
  zaloPageSend: (id: string, text: string) =>
    http.post<Message>(`${C}/zalo/threads/${id}/page-send`, { text }),
  zaloLink: (id: string, body: { term?: string; conversation_id?: string; match?: string }) =>
    http.post<ZaloThread>(`${C}/zalo/threads/${id}/link`, body),
  zaloUnlink: (id: string) => http.post<ZaloThread>(`${C}/zalo/threads/${id}/unlink`),
  zaloSearch: (q: string, conversationId?: string) =>
    http.get<{ results: ZaloThread[] }>(`${C}/zalo/search${query({ q, conversation_id: conversationId })}`),
  zaloAccounts: () => http.get<ZaloAccounts>(`${C}/zalo/accounts`),
  zaloAssign: (accountIds: string[], userId: string | null) =>
    http.post<{ changed: number }>(`${C}/zalo/accounts/assign`, {
      account_ids: accountIds,
      user_id: userId,
    }),
  zaloLogout: (accountId: string) =>
    http.post<void>(`${C}/zalo/accounts/${encodeURIComponent(accountId)}/logout`),
  zaloQrStart: (userId: string | null) => http.post<QrState>(`${C}/zalo/qr`, { user_id: userId }),
  zaloQrStatus: (session: string) => http.get<QrState>(`${C}/zalo/qr/${encodeURIComponent(session)}`),
  // --- connections
  connections: () => http.get<Connections>(`${C}/connections`),
  addPancakeAccount: (body: { label?: string; token: string; expires_on?: string | null }) =>
    http.post<{ id: string; page_count: number; active_count: number }>(`${C}/pancake-accounts`, body),
  updatePancakeAccount: (id: string, body: { label?: string; token?: string; expires_on?: string | null }) =>
    http.patch<void>(`${C}/pancake-accounts/${id}`, body),
  removePancakeAccount: (id: string) => http.del<void>(`${C}/pancake-accounts/${id}`),
  pancakePages: () => http.get<{ pages: PancakeListing[] }>(`${C}/pancake/pages`),
  addPosKey: (body: { label?: string; key: string }) =>
    http.post<{ id: string; shops: number }>(`${C}/pos-keys`, body),
  removePosKey: (id: string) => http.del<void>(`${C}/pos-keys/${id}`),
  reloadPos: () => http.post<{ shops: number }>(`${C}/pos-keys/reload`),
  connectPages: (body: {
    page_ids: string[] | null;
    label_ids: string[];
    members: MemberItem[];
    import_history: boolean;
  }) => http.post<{ started: boolean; total: number }>(`${C}/pages/connect`, body),
  refreshPages: () => http.post<{ started: boolean }>(`${C}/pages/refresh`),
  updatePages: (body: {
    page_ids: string[];
    active?: boolean;
    marketer_user_id?: string | null;
    members?: MemberItem[];
    member_mode?: "set" | "add" | "remove";
    label_ids?: string[];
    label_mode?: "set" | "add" | "remove";
  }) => http.post<{ changed: number }>(`${C}/pages/update`, body),
  disconnectPage: (id: string) => http.post<void>(`${C}/pages/${encodeURIComponent(id)}/disconnect`),
  labels: () => http.get<{ labels: Label[] }>(`${C}/labels`),
  addLabel: (body: { name: string; color: string }) => http.post<Label>(`${C}/labels`, body),
  editLabel: (id: string, body: { name: string; color: string }) =>
    http.patch<Label>(`${C}/labels/${id}`, body),
  deleteLabel: (id: string) => http.del<void>(`${C}/labels/${id}`),
  quickReplies: () => http.get<{ quick_replies: QuickReply[] }>(`${C}/quick-replies`),
  addQuickReply: (body: { shortcut: string; text: string; page_id: string | null }) =>
    http.post<QuickReply>(`${C}/quick-replies`, body),
  editQuickReply: (id: string, body: { shortcut: string; text: string; page_id: string | null }) =>
    http.patch<QuickReply>(`${C}/quick-replies/${id}`, body),
  deleteQuickReply: (id: string) => http.del<void>(`${C}/quick-replies/${id}`),
  importQuickReplies: (text: string, pageId: string | null) =>
    http.post<{ added: number; skipped: string[] }>(`${C}/quick-replies/import`, {
      text,
      page_id: pageId,
    }),
  shareQuickReplies: (ids: string[] | null, overwrite: boolean) =>
    http.post<{ moved: number; conflict: string[] }>(`${C}/quick-replies/share`, { ids, overwrite }),
  settings: () => http.get<InboxSettings>(`${C}/settings`),
  saveSettings: (body: Partial<InboxSettings>) => http.put<InboxSettings>(`${C}/settings`, body),
};

/** Facebook takes at most 2000 characters in one message. */
export const MAX_MESSAGE_CHARS = 2000;

export const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"] as const;

/** Labels' colour names -> CSS (token-based tints, both themes). */
export const LABEL_TONES: Record<string, string> = {
  blue: "var(--accent)",
  teal: "var(--brand-sky)",
  green: "var(--good)",
  amber: "var(--warn)",
  coral: "var(--bad)",
  pink: "var(--magenta)",
  purple: "var(--brand-magenta-pastel)",
  gray: "var(--text-muted)",
};

/** A quick reply's text with `{ten}` replaced by the customer's name. */
export function fillReply(text: string, customerName: string): string {
  return text.replaceAll("{ten}", customerName || "anh/chị");
}

/** The quick replies a page offers: shared ones, a page's own winning on the same shortcut. */
export function repliesFor(all: QuickReply[], pageId: string | null | undefined): QuickReply[] {
  const byShortcut = new Map<string, QuickReply>();
  for (const reply of all) if (!reply.page_id) byShortcut.set(reply.shortcut, reply);
  for (const reply of all) if (pageId && reply.page_id === pageId) byShortcut.set(reply.shortcut, reply);
  return [...byShortcut.values()].sort((a, b) => a.shortcut.localeCompare(b.shortcut));
}

/** Customer-typed text for a conversation-list preview, attachments as their label. */
export const ATTACHMENT_LABELS: Record<string, string> = {
  image: "Ảnh",
  sticker: "Nhãn dán",
  video: "Video",
  audio: "Ghi âm",
  file: "Tệp",
  card: "Thẻ",
  ad: "Khách bấm quảng cáo",
  address: "Địa chỉ",
  link: "Liên kết",
};

/** Only http(s) URLs become links / images. */
export function safeUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  return /^https?:\/\//i.test(url) ? url : null;
}
