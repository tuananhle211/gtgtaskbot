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

const SENT_FILES = [
  {
    id: "f1",
    name: "banner.png",
    kind: "IMAGE",
    content_type: "image/png",
    size_bytes: 2048,
    pages: null,
    truncated: false,
    note: null,
    url: "/api/agents/attachments/f1",
  },
  {
    id: "f2",
    name: "hop-dong.pdf",
    kind: "DOCUMENT",
    content_type: "application/pdf",
    size_bytes: 120_000,
    pages: 3,
    truncated: false,
    note: "3 trang",
    url: "/api/agents/attachments/f2",
  },
];

const SKILLS = {
  available: true,
  agents: {
    ceo: { approved: [], own: [], builtin: [] },
    phapche: {
      approved: [
        { name: "soat-quang-cao", description: "Soát câu quảng cáo theo luật.", group: null },
        { name: "hop-dong-mau", description: "Mẫu hợp đồng dịch vụ.", group: null },
      ],
      own: [{ name: "ghi-chu-phap-ly", description: "Agent tự học.", group: null }],
      builtin: [{ name: "arxiv", description: "Tìm bài báo khoa học.", group: "research" }],
    },
  },
};

function backend(options: { configured?: boolean; saved?: unknown[] } = {}) {
  const calls: Call[] = [];
  let saved: unknown[] = options.saved ?? [];
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const raw = init?.body;
    calls.push({ url, method, body: typeof raw === "string" ? JSON.parse(raw) : raw });
    if (url.endsWith("/api/agents") && method === "GET") {
      return json({ ...AGENTS, configured: options.configured ?? true });
    }
    if (url.endsWith("/api/agents/skills")) return json(SKILLS);
    if (url.includes("/api/agents/phapche/conversations")) return json({ conversations: [CONVERSATION] });
    if (url.includes("/api/agents/conversations/c1/attachments") && method === "POST") {
      const name = new URL(url, "http://x").searchParams.get("name");
      if (name === "clip.mp4") {
        return json(
          { error: { code: "validation_error", message: "Agent chưa xem được video / nghe được âm thanh.", details: { reason: "agent_file_invalid" } } },
          422,
        );
      }
      return json({ ...SENT_FILES[1], id: "f9", name }, 201);
    }
    if (url.endsWith("/api/agents/conversations/c1") && method === "DELETE") return new Response(null, { status: 204 });
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
    expect(within(agents).getByRole("region", { name: "Ban Giám đốc" })).toBeInTheDocument();
    await user.click(within(agents).getByRole("button", { name: /^Pháp chế/ }));
    expect(NAV.current()).toBe("a=phapche");
    await user.click(await screen.findByRole("button", { name: /^Soát câu quảng cáo/ }));
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

describe("Đội agent: files and deleting", () => {
  it("attaches a document, refuses a video, sends the file with the question", async () => {
    const calls = backend();
    // The picker narrows the dialog only; the server is the one who refuses.
    const user = userEvent.setup({ applyAccept: false });
    NAV.arriveAt("/agents?a=phapche&c=c1");
    renderWithQuery(<AgentsWorkspace />);
    const input = (await screen.findByTestId("agent-file-input")) as HTMLInputElement;
    await user.upload(input, [
      new File(["%PDF-1.4"], "hop-dong.pdf", { type: "application/pdf" }),
      new File(["x"], "clip.mp4", { type: "video/mp4" }),
    ]);
    const chips = await screen.findByRole("list", { name: "Tệp đính kèm" });
    await waitFor(() => expect(within(chips).getByText(/Agent chưa xem được video/)).toBeInTheDocument());
    expect(within(chips).getByText(/3 trang/)).toBeInTheDocument();
    // The refused one is taken out; the other goes with the question.
    await user.click(within(chips).getByRole("button", { name: "Bỏ tệp clip.mp4" }));
    await user.type(screen.getByLabelText("Tin nhắn cho Pháp chế"), "Soát hợp đồng này{Enter}");
    await waitFor(() =>
      expect(calls.find((call) => call.method === "POST" && call.url.includes("/messages"))?.body).toEqual({
        text: "Soát hợp đồng này",
        attachment_ids: ["f9"],
      }),
    );
    const upload = calls.find((call) => call.url.includes("/attachments?name=hop-dong.pdf"));
    expect(upload?.method).toBe("POST");
    await waitFor(() => expect(screen.queryByRole("list", { name: "Tệp đính kèm" })).toBeNull());
  });

  it("draws what was sent: the picture, and the document to download", async () => {
    backend({ saved: [{ ...QUESTION, attachments: SENT_FILES }, ANSWER] });
    NAV.arriveAt("/agents?a=phapche&c=c1");
    renderWithQuery(<AgentsWorkspace />);
    const log = await screen.findByRole("log", { name: "Tin nhắn" });
    const picture = await within(log).findByRole("img", { name: "banner.png" });
    expect(picture).toHaveAttribute("src", "/api/agents/attachments/f1");
    const download = within(log).getByRole("link", { name: /hop-dong\.pdf/ });
    expect(download).toHaveAttribute("href", "/api/agents/attachments/f2");
    expect(download).toHaveAttribute("download", "hop-dong.pdf");
    expect(within(download).getByText(/3 trang · 117 KB/)).toBeInTheDocument();
  });

  it("deletes a conversation for good, after asking", async () => {
    const calls = backend();
    const user = userEvent.setup();
    NAV.arriveAt("/agents?a=phapche&c=c1");
    renderWithQuery(<AgentsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "Xoá cuộc trò chuyện Soát câu quảng cáo" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Không khôi phục được");
    await user.click(within(dialog).getByRole("button", { name: "Xoá cuộc trò chuyện" }));
    await waitFor(() =>
      expect(calls.some((call) => call.method === "DELETE" && call.url.endsWith("/api/agents/conversations/c1"))).toBe(
        true,
      ),
    );
    await waitFor(() => expect(NAV.current()).toBe("a=phapche"));
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


describe("Đội agent: the row of agents and their skills (2026-11-06)", () => {
  it("lays the agents in a row with a person avatar and a skill count; a pick shows the skills", async () => {
    backend();
    const user = userEvent.setup();
    renderWithQuery(<AgentsWorkspace />);
    const agents = await screen.findByRole("navigation", { name: "Danh sách agent" });
    expect(agents.querySelectorAll("[data-agent-avatar]")).toHaveLength(2);
    // Each agent its own robot (public/agent-avatars/<slug>.svg).
    expect([...agents.querySelectorAll("[data-agent-avatar] img")].map((img) => img.getAttribute("src"))).toEqual([
      "/agent-avatars/ceo.svg",
      "/agent-avatars/phapche.svg",
    ]);
    // Approved + its own = 3; the bundled ones are not counted.
    const count = await within(agents).findByRole("button", { name: "3 skill của Pháp chế - xem danh sách" });
    expect(count).toHaveTextContent("3");
    expect(within(agents).getByRole("button", { name: "0 skill của CEO - xem danh sách" })).toBeInTheDocument();
    // Picking the agent shows its skills beside the conversations.
    await user.click(within(agents).getByRole("button", { name: /^Pháp chế/ }));
    const list = await screen.findByTestId("agent-skills");
    expect(within(list).getByText("soat-quang-cao")).toBeInTheDocument();
    expect(within(list).getByText("ghi-chu-phap-ly")).toBeInTheDocument();
    expect(within(list).getByText("Có sẵn của Hermes")).toBeInTheDocument();
    // The count opens the same list in a dialog.
    await user.click(count);
    const dialog = await screen.findByRole("dialog", { name: "Skill của Pháp chế" });
    expect(within(dialog).getByText("hop-dong-mau")).toBeInTheDocument();
    expect(within(dialog).getByText("2 skill Apexmed · 1 tự học · 1 có sẵn của Hermes")).toBeInTheDocument();
  });
});

describe("Đội agent: who sees which agent, in one grid (2026-11-06)", () => {
  it("ticks departments for several agents and saves only the ones that changed", async () => {
    const { AgentAccessMatrix } = await import("@/components/agent-access-matrix");
    const puts: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        puts.push({ url: String(input), body: JSON.parse(String(init?.body ?? "{}")) });
        return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );
    const agent = (slug: string, name: string, extra: object = {}) => ({
      slug, name, title: name, badge: "", group: "MARKETING", active: true, everyone: false,
      department_ids: [] as string[], user_ids: [] as string[], ...extra,
    });
    const agents = [
      agent("mktthietke", "Thiết kế", { department_ids: ["d-tk"] }),
      agent("mktads", "Marketing Ads", { department_ids: ["d-ads"] }),
      agent("phapche", "Pháp chế", { group: "SUPPORT", everyone: true }),
    ];
    const departments = [
      { id: "d-ads", code: "ADS", name: "Phòng Ads", function: "NONE", active: true, sort_order: 1 },
      { id: "d-tk", code: "TK", name: "Thiết Kế", function: "THIET_KE", active: true, sort_order: 2 },
    ];
    const user = userEvent.setup();
    renderWithQuery(<AgentAccessMatrix agents={agents as never} departments={departments as never} onDetails={() => {}} />);
    const grid = screen.getByTestId("agent-matrix");
    // "Mọi người" ticks every department and locks them.
    expect(within(grid).getByRole("checkbox", { name: "Pháp chế: Phòng Ads" })).toBeDisabled();
    // Each department sees its own agent (+ Pháp chế for everyone).
    expect(within(grid).getAllByText("2 agent")).toHaveLength(2);
    await user.click(within(grid).getByRole("checkbox", { name: "Thiết kế: Phòng Ads" }));
    expect(screen.getByText("1 agent đã đổi, chưa lưu.")).toBeInTheDocument();
    // Untick it again: nothing to save.
    await user.click(within(grid).getByRole("checkbox", { name: "Thiết kế: Phòng Ads" }));
    expect(screen.getByRole("button", { name: "Lưu thay đổi" })).toBeDisabled();
    // Take Thiết kế away from its own department and save.
    await user.click(within(grid).getByRole("checkbox", { name: "Thiết kế: Thiết Kế" }));
    await user.click(screen.getByRole("button", { name: "Lưu thay đổi" }));
    const confirm = await screen.findByRole("dialog");
    await user.click(within(confirm).getByRole("button", { name: "Lưu thay đổi" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ url: "/api/agents/admin/mktthietke", body: { everyone: false, department_ids: [] } });
  });
});
