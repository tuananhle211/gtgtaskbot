/**
 * Đội agent (0091): sending a question and reading the answer as it is written.
 *
 * `POST /api/agents/conversations/<id>/messages` answers with Server-Sent
 * Events (`start`, `tool`, `delta`, `done`, and `: keepalive` comments). The
 * browser's EventSource cannot POST, so the body stream is read by hand.
 * The answer is saved on the server whatever happens here: losing the
 * connection only stops the live view (the page re-reads the conversation).
 */

import { ApiError, type AgentMessage, type Uuid } from "@/lib/api";

export type AgentStreamEvent =
  | { type: "start"; user_message: AgentMessage; answer_id: string }
  | { type: "delta"; text: string }
  | { type: "tool"; tool: string; label: string; emoji: string }
  | { type: "done"; message: AgentMessage };

/** Splits an SSE text buffer into whole frames; returns the events and the rest. */
export function readSseFrames(buffer: string): { events: AgentStreamEvent[]; rest: string } {
  const events: AgentStreamEvent[] = [];
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
      events.push({ ...payload, type: kind } as AgentStreamEvent);
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
): Promise<void> {
  const response = await fetch(`/api/agents/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok || !response.body) {
    type Envelope = { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
    const body: Envelope | null = await response.json().then(
      (parsed: unknown) => parsed as Envelope,
      () => null,
    );
    throw new ApiError(
      response.status,
      body?.error?.code ?? "http_error",
      body?.error?.message ?? "Không gửi được tin nhắn. Thử lại sau.",
      body?.error?.details ?? {},
    );
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

/** Why an answer failed, in words. */
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
};

export function answerError(code: string | null): string {
  return (code && ANSWER_ERRORS[code]) || "Câu trả lời bị gián đoạn.";
}
