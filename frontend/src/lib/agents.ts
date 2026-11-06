/**
 * Đội agent (0091): sending a question and reading the answer as it is written.
 *
 * `POST /api/agents/conversations/<id>/messages` answers with Server-Sent
 * Events (`start`, `tool`, `delta`, `done`, and `: keepalive` comments). The
 * browser's EventSource cannot POST, so the body stream is read by hand.
 * The answer is saved on the server whatever happens here: losing the
 * connection only stops the live view (the page re-reads the conversation).
 */

import {
  ApiError,
  type AgentAttachment,
  type AgentMessage,
  type AgentRoomMessage,
  type Uuid,
} from "@/lib/api";

export type AgentStreamEvent =
  | { type: "start"; user_message: AgentMessage; answer_id: string }
  | { type: "delta"; text: string }
  | { type: "tool"; tool: string; label: string; emoji: string }
  | { type: "done"; message: AgentMessage };

/** Splits an SSE text buffer into whole frames; returns the events and the rest. */
export function readSseFrames<E extends { type: string } = AgentStreamEvent>(
  buffer: string,
): { events: E[]; rest: string } {
  const events: E[] = [];
  const parts = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = parts.pop() ?? "";
  for (const frame of parts) {
    let kind: string | null = null;
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) kind = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!kind || data.length === 0) continue;
    try {
      const payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
      events.push({ ...payload, type: kind } as unknown as E);
    } catch {
      // A broken frame is skipped; the saved answer is re-read at the end anyway.
    }
  }
  return { events, rest };
}

/**
 * Ask; `onEvent` gets each event as it comes. Rejects with an `ApiError` when
 * the server refuses before answering (403/404/409/422/429/503).
 */
export async function sendAgentMessage(
  conversationId: Uuid,
  text: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
  attachmentIds: Uuid[] = [],
): Promise<void> {
  const response = await fetch(`/api/agents/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(attachmentIds.length > 0 ? { text, attachment_ids: attachmentIds } : { text }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw await refusal(response, "Không gửi được tin nhắn. Thử lại sau.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = readSseFrames(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
  const tail = readSseFrames(buffer + decoder.decode() + "\n\n");
  tail.events.forEach(onEvent);
}

export async function refusal(response: Response, fallback: string): Promise<ApiError> {
  type Envelope = { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
  const body: Envelope | null = await response.json().then(
    (parsed: unknown) => parsed as Envelope,
    () => null,
  );
  return new ApiError(
    response.status,
    body?.error?.code ?? "http_error",
    body?.error?.message ?? (response.status === 413 ? "Tệp lớn quá." : fallback),
    body?.error?.details ?? {},
  );
}

/**
 * Upload one file for the next question (0092): the raw bytes are the body,
 * the server reads it at once - a refusal says why ("PDF này là bản scan…").
 */
export async function uploadAgentAttachment(
  conversationId: Uuid,
  file: Blob,
  name: string,
  signal?: AbortSignal,
): Promise<AgentAttachment> {
  const response = await fetch(
    `/api/agents/conversations/${conversationId}/attachments?name=${encodeURIComponent(name)}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
      signal,
    },
  );
  if (!response.ok) throw await refusal(response, "Không tải được tệp lên. Thử lại sau.");
  return (await response.json()) as AgentAttachment;
}

/** A photo is made at most this wide / tall before it is sent (the model reads ~1600 px). */
export const IMAGE_MAX_SIDE = 2048;

/**
 * Shrinks a big photo in the browser (JPEG on white) so it uploads fast and
 * stays under the server's limit. Anything that cannot be drawn here - a GIF,
 * an old browser, a test - goes as it is; the server still checks it.
 */
export async function shrinkImage(file: File): Promise<{ blob: Blob; name: string }> {
  const same = { blob: file as Blob, name: file.name };
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || typeof createImageBitmap !== "function") return same;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, IMAGE_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size <= 1_500_000) {
      bitmap.close();
      return same;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext("2d");
    if (!context) return same;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
    if (!blob || blob.size >= file.size) return same;
    return { blob, name: file.name.replace(/\.[^.]+$/, "") + ".jpg" };
  } catch {
    return same;
  }
}

/** What the file picker offers (the server decides; this only narrows the dialog). */
export const ATTACH_ACCEPT = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  ".pdf",
  ".docx",
  ".xlsx",
  ".xlsm",
  ".pptx",
  ".txt",
  ".md",
  ".csv",
  ".tsv",
  ".json",
  ".zip",
].join(",");

/** "1,2 MB", "340 KB". */
export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Why an answer failed, in words. */
/**
 * A picture the agent puts in its answer arrives as `MEDIA:/path.png` (or a
 * `![image](data:...)`); the server takes it out of the saved answer and keeps
 * it as the answer's file. While the answer still streams, hide the raw tag.
 */
const MEDIA_TAG = /[`'"]?MEDIA:\s*\/[^\s`'"<>()[\]]+\.(?:png|jpe?g|webp|gif)(?![A-Za-z0-9])[`'"]?/gi;
const DATA_IMAGE = /!\[[^\]\n]{0,200}\]\(data:image\/[^)]*\)?/g;
/** A ```html page still being written (no closing fence yet): the code stays out of the bubble. */
const HTML_PAGE_OPEN = /```[ \t]*html[^\n]*\n(?![\s\S]*```)[\s\S]*$/i;
/** An unfinished tag at the very end of the streamed text (`MEDIA:/ho…`). */
const MEDIA_TAIL = /[`'"]?MEDIA:[^\s]*$|!\[[^\]\n]*\]\(data:[^)]*$/i;

export function hideAnswerMedia(text: string): { text: string; pictures: number } {
  let pictures = 0;
  const count = () => {
    pictures += 1;
    return "";
  };
  const out = text
    .replace(MEDIA_TAG, count)
    .replace(DATA_IMAGE, count)
    .replace(MEDIA_TAIL, count)
    .replace(HTML_PAGE_OPEN, () => {
      pictures += 1;
      return "*(Đang viết trang HTML…)*";
    });
  return { text: out.replace(/\n{3,}/g, "\n\n"), pictures };
}

export const ANSWER_ERRORS: Record<string, string> = {
  agent_unreachable: "Không kết nối được máy chủ agent.",
  agent_timeout: "Máy chủ agent không phản hồi.",
  timeout: "Câu trả lời quá 5 phút nên bị dừng.",
  agent_error: "Agent gặp lỗi khi trả lời.",
  empty_answer: "Agent không trả lời gì.",
  interrupted: "Bị gián đoạn (máy chủ vừa khởi động lại).",
  agent_relay_refused: "Máy chủ agent từ chối kết nối (sai token).",
  agent_not_on_relay: "Agent này chưa có trên máy chủ agent.",
  agents_not_configured: "Chưa kết nối tới máy chủ agent.",
  agent_request_too_large: "Tệp đính kèm quá lớn với máy chủ agent. Gửi bớt tệp.",
};

export function answerError(code: string | null): string {
  return (code && ANSWER_ERRORS[code]) || "Câu trả lời bị gián đoạn.";
}

// --- Phòng họp agent (0093) ----------------------------------------------------------

export type AgentRoomEvent =
  | {
      type: "state";
      running: boolean;
      round: number | null;
      message_id: string | null;
      text: string;
      tools: Array<{ tool: string; label: string; emoji: string; message_id: string }>;
    }
  | { type: "message"; message: AgentRoomMessage }
  | { type: "delta"; message_id: string; text: string }
  | { type: "tool"; message_id: string; tool: string; label: string; emoji: string }
  | { type: "done"; message: AgentRoomMessage }
  | { type: "round"; running: boolean; round: number | null }
  | { type: "people" }
  | { type: "deleted" };

/**
 * The room live (`GET /api/agents/rooms/<id>/events`): `state` first, then
 * every message, piece of a turn, tool, round start / end. Resolves when the
 * server closes the stream; rejects with an `ApiError` when it refuses (404).
 */
export async function listenAgentRoom(
  roomId: Uuid,
  onEvent: (event: AgentRoomEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/agents/rooms/${roomId}/events`, {
    credentials: "same-origin",
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) {
    throw await refusal(response, "Mất kết nối với phòng họp.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = readSseFrames<AgentRoomEvent>(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}
