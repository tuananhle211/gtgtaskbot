import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { channelsNavigation, renderWithQuery } from "./helpers";
import { readSseFrames } from "@/lib/agents";
import { AgentMarkdown } from "@/components/agent-markdown";
import type { UnitsMe } from "@/lib/api";

const NAV = channelsNavigation("/agents");
vi.mock("next/navigation", () => NAV.module);

const { AgentsWorkspace } = await import("@/components/agent-chat");
const { navFor, navLinks } = await import("@/components/shell");
const { adminTabsFor } = await import("@/components/admin-tabs");

const AGENTS = {
  agents: [
    { slug: "ceo", name: "CEO", title: "Điều hành", description: "Họp tuần.", badge: "CEO", group: "BOARD", last_message_at: null },
    { slug: "phapche", name: "Pháp chế", title: "Soát quảng cáo, hợp đồng", description: "Soát nội dung.", badge: "PC", group: "SUPPORT", last_message_at: null },
  ],
  groups: [
    { key: "BOARD", label: "Ban Giám đốc" },
    { key: "MARKETING", label: "Marketing" },
    { key: "SALES", label: "Kinh doanh - CSKH" },
    { key: "SUPPORT", label: "Hỗ trợ" },
  ],
  configured: true,
  manage: false,
  max_message_chars: 8000,
};

const CONVERSATION = {
  id: "c1",
  agent: "phapche",
  title: "Soát câu quảng cáo",
  created_at: "2026-11-05T02:00:00Z",
  last_message_at: "2026-11-05T02:00:00Z",
  running: false,
};

const QUESTION = {
  id: "m1",
  role: "USER",
  content: "Câu này được không: cam kết hết mụn",
  status: "DONE",
  error_code: null,
  tools_used: [],
  duration_ms: null,
  created_at: "2026-11-05T02:01:00Z",
  finished_at: "2026-11-05T02:01:00Z",
};
const ANSWER = {
  ...QUESTION,
  id: "m2",
  role: "ASSISTANT",
  content: "**Không được.** Vi phạm mục 2: “cam kết”.",
  tools_used: ["search_files"],
  duration_ms: 4200,
};

function sse(events: Array<[string, unknown]>): string {
  return events.map(([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`).join(": keepalive\n\n");
}

type Call = { url: string; method: string; body: unknown };

function backend(options: { configured?: boolean } = {}) {
  const calls: Call[] = [];
  let saved: unknown[] = [];
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/api/agents") && method === "GET") {
      return json({ ...AGENTS, configured: options.configured ?? true });
    }
    if (url.includes("/api/agents/phapche/conversations")) return json({ conversations: [CONVERSATION] });
    if (url.includes("/api/agents/conversations/c1/messages") && method === "GET") {
      return json({ conversation: CONVERSATION, messages: saved, has_more: false });
    }
    if (url.includes("/api/agents/conversations/c1/messages") && method === "POST") {
      saved = [QUESTION, ANSWER];
      return new Response(
        sse([
          ["start", { user_message: QUESTION, answer_id: "m2" }],
          ["tool", { tool: "search_files", label: "Tìm trong kho", emoji: "🔎" }],
          ["delta", { text: "**Không được.** " }],
          ["delta", { text: "Vi phạm mục 2: “cam kết”." }],
          ["done", { message: ANSWER }],
        ]),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    throw new Error(`No stub for ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

beforeEach(() => NAV.reset());
afterEach(() => vi.unstubAllGlobals());

describe("Đội agent: reading the stream", () => {
  it("splits whole frames, skips keepalives, keeps the unfinished rest", () => {
    const text =
      ": keepalive\n\nevent: delta\ndata: {\"text\":\"Xin \"}\n\nevent: tool\ndata: {\"tool\":\"x\",\"label\":\"Tìm\",\"emoji\":\"\"}\n\nevent: delta\ndata: {\"te";
    const { events, rest } = readSseFrames(text);
    expect(events).toEqual([
      { type: "delta", text: "Xin " },
      { type: "tool", tool: "x", label: "Tìm", emoji: "" },
    ]);
    expect(rest).toBe('event: delta\ndata: {"te');
  });
});

describe("Đội agent: the answer's Markdown", () => {
  it("draws tables, lists and bold, and never a javascript: link", () => {
    render(
      <AgentMarkdown
        text={[
          "## Kết luận",
          "- **Mục 2**: cam kết",
          "- Mục 4",
          "",
          "| Câu | Lỗi |",
          "|---|---|",
          "| cam kết | mục 2 |",
          "",
          "[nguồn](https://aduca.vn/tai-lieu) và [xấu](javascript:alert(1))",
        ].join("\n")}
      />,
    );
    expect(screen.getByText("Kết luận")).toBeInTheDocument();
    expect(screen.getByText("Mục 2").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Lỗi" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nguồn" })).toHaveAttribute("href", "https://aduca.vn/tai-lieu");
    expect(screen.queryByRole("link", { name: "xấu" })).toBeNull();
  });
});

describe("Đội agent: the chat", () => {
  it("picks an agent and a conversation, sends with Enter and shows the saved answer", async () => {
    const calls = backend();
    const user = userEvent.setup();
    renderWithQuery(<AgentsWorkspace />);
    const agents = await screen.findByRole("navigation", { name: "Danh sách agent" });
    expect(within(agents).getByText("Ban Giám đốc")).toBeInTheDocument();
    await user.click(within(agents).getByRole("button", { name: /Pháp chế/ }));
    expect(NAV.current()).toBe("a=phapche");
    await user.click(await screen.findByRole("button", { name: /Soát câu quảng cáo/ }));
    expect(NAV.current()).toBe("a=phapche&c=c1");

    expect(await screen.findByText("Không gửi thông tin cá nhân, ảnh, hồ sơ của khách hàng.")).toBeInTheDocument();
    const box = screen.getByLabelText("Tin nhắn cho Pháp chế");
    await user.type(box, "Câu này được không: cam kết hết mụn{Enter}");

    const log = screen.getByRole("log", { name: "Tin nhắn" });
    await waitFor(() => expect(within(log).getByText("Không được.")).toBeInTheDocument());
    expect(within(log).getByText("Câu này được không: cam kết hết mụn")).toBeInTheDocument();
    expect(within(log).getByText(/Đã dùng 1 công cụ/)).toBeInTheDocument();
    const sent = calls.find((call) => call.method === "POST" && call.url.includes("/messages"));
    expect(sent?.body).toEqual({ text: "Câu này được không: cam kết hết mụn" });
    expect(box).toHaveValue("");
  });

  it("says when the agents' server is not connected, and does not let you type", async () => {
    backend({ configured: false });
    NAV.arriveAt("/agents?a=phapche&c=c1");
    renderWithQuery(<AgentsWorkspace />);
    expect(await screen.findByText(/Chưa kết nối tới máy chủ agent nên chưa gửi tin được/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Tin nhắn cho Pháp chế")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Gửi" })).toBeDisabled();
  });
});

describe("Đội agent: the menu", () => {
  const me = (agents: UnitsMe["agents"]) =>
    ({
      units: [],
      default_unit: null,
      can_view_all: false,
      can_admin: [],
      can_tag: [],
      can_edit_members: [],
      departments: [],
      effort_enabled: true,
      agents,
    }) as unknown as UnitsMe;

  it("shows Đội agent only to somebody with an agent to talk to", () => {
    const hrefs = (value: UnitsMe) => navLinks(navFor(value, "EMPLOYEE")).map((item) => item.href);
    expect(hrefs(me({ chat: true, manage: false }))).toContain("/agents");
    expect(hrefs(me({ chat: false, manage: false }))).not.toContain("/agents");
    expect(hrefs(me(null))).not.toContain("/agents");
  });

  it("gives the OWNER the Đội agent admin tab", () => {
    expect(adminTabsFor(me({ chat: true, manage: true }), "OWNER").map((tab) => tab.href)).toContain(
      "/admin/agents",
    );
    expect(adminTabsFor(me({ chat: true, manage: false }), "ADMIN").map((tab) => tab.href)).not.toContain(
      "/admin/agents",
    );
  });
});

