import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { channelsNavigation, renderWithQuery } from "./helpers";

const NAV = channelsNavigation("/agents/rooms");
vi.mock("next/navigation", () => NAV.module);

const { AgentRoomsWorkspace } = await import("@/components/agent-rooms");

const AGENT = (slug: string, name: string, chair = false) => ({
  agent_id: `id-${slug}`,
  slug,
  name,
  title: `${name} - vai trò`,
  badge: name.slice(0, 3).toUpperCase(),
  active: true,
  chair,
});

const ROOM = {
  id: "r1",
  title: "Ngân sách tháng 12",
  is_owner: true,
  rounds: 1,
  running: false,
  agents: [AGENT("ceo", "CEO", true), AGENT("cmo", "CMO")],
  people: [
    { user_id: "u1", name: "Chủ phòng", owner: true },
    { user_id: "u2", name: "Lan Anh", owner: false },
  ],
  created_at: "2026-11-05T02:00:00Z",
  last_message_at: "2026-11-05T02:05:00Z",
};

const MESSAGE = (over: Record<string, unknown>) => ({
  id: "m0",
  speaker: "PERSON",
  user_id: "u1",
  agent_id: null,
  agent_slug: null,
  speaker_label: "Chủ phòng",
  round_no: null,
  step: null,
  step_label: null,
  content: "",
  status: "DONE",
  error_code: null,
  tools_used: [],
  duration_ms: null,
  created_at: "2026-11-05T02:01:00Z",
  finished_at: "2026-11-05T02:01:00Z",
  ...over,
});

const SAVED = [
  MESSAGE({ id: "m1", content: "Có nên tăng ngân sách TikTok?" }),
  MESSAGE({
    id: "m2",
    speaker: "AGENT",
    user_id: null,
    agent_id: "id-ceo",
    agent_slug: "ceo",
    speaker_label: "CEO",
    round_no: 1,
    step: "OPEN",
    step_label: "Mở đầu",
    content: "CMO cho ý kiến về kênh.",
  }),
  MESSAGE({ id: "m3", user_id: "u2", speaker_label: "Lan Anh - Nhân viên Ads", content: "Em thêm: Google đang rẻ." }),
];

const LIVE = MESSAGE({
  id: "m4",
  speaker: "AGENT",
  user_id: null,
  agent_id: "id-cmo",
  agent_slug: "cmo",
  speaker_label: "CMO",
  round_no: 1,
  step: "SPEAK",
  step_label: "Phát biểu",
  status: "STREAMING",
  finished_at: null,
});

function sse(events: Array<[string, unknown]>): string {
  return events.map(([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

type Call = { url: string; method: string; body: unknown };

function backend(options: { owner?: boolean; events?: Array<[string, unknown]> } = {}) {
  const calls: Call[] = [];
  const room = { ...ROOM, is_owner: options.owner ?? true };
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const raw = init?.body;
    calls.push({ url, method, body: typeof raw === "string" ? JSON.parse(raw) : raw });
    if (url.endsWith("/api/auth/session")) return json({ user_id: room.is_owner ? "u1" : "u2", full_name: "X" });
    if (url.endsWith("/api/agents/rooms") && method === "GET") {
      return json({
        rooms: [{ ...room, agents: room.agents, people: room.people.length, owner_name: "Chủ phòng" }],
        configured: true,
        min_agents: 2,
        max_agents: 6,
        max_message_chars: 8000,
      });
    }
    if (url.endsWith("/api/agents/rooms") && method === "POST") return json({ ...room, id: "r9" }, 201);
    if (url.endsWith("/api/agents") && method === "GET") {
      return json({
        agents: [
          { slug: "ceo", name: "CEO", title: "Điều hành", description: "", badge: "CEO", group: "BOARD", last_message_at: null },
          { slug: "cmo", name: "CMO", title: "Marketing", description: "", badge: "CMO", group: "MARKETING", last_message_at: null },
          { slug: "cfo", name: "CFO", title: "Tài chính", description: "", badge: "CFO", group: "BOARD", last_message_at: null },
        ],
        groups: [],
        configured: true,
        manage: false,
        max_message_chars: 8000,
      });
    }
    if (url.includes("/api/agents/rooms/r1/messages") && method === "GET") {
      return json({ room, messages: SAVED, has_more: false });
    }
    if (url.includes("/api/agents/rooms/r1/messages") && method === "POST") {
      const text = (calls[calls.length - 1].body as { text: string }).text;
      return json({ message: MESSAGE({ id: "m9", content: text }), round_started: true });
    }
    if (url.includes("/api/agents/rooms/r1/events")) {
      return new Response(sse(options.events ?? [["state", { running: false, round: null, message_id: null, text: "", tools: [] }]]), {
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    if (url.includes("/api/agents/rooms/r1/stop")) return json({ stopped: true });
    if (url.includes("/api/agents/rooms/r1/candidates")) {
      return json({
        people: [
          { user_id: "u3", name: "Minh Ads", allowed: true, missing: [] },
          { user_id: "u4", name: "Hà Kế toán", allowed: false, missing: ["CMO"] },
        ],
      });
    }
    if (url.includes("/api/agents/rooms/r1/members") && method === "POST") return json(room);
    return json({ error: { code: "not_found", message: "?" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

beforeEach(() => NAV.reset());
afterEach(() => vi.unstubAllGlobals());

describe("Phòng họp agent (0093)", () => {
  it("shows the speaking order, the rounds and who said what", async () => {
    backend();
    NAV.arriveAt("/agents/rooms?r=r1");
    renderWithQuery(<AgentRoomsWorkspace />);
    const order = await screen.findByRole("list", { name: "Thứ tự nói" });
    expect(within(order).getByText("CEO").closest("li")).toHaveTextContent("1.CEO· chủ trì");
    expect(within(order).getByText("Lan Anh")).toBeInTheDocument();
    const log = screen.getByRole("log", { name: "Diễn biến cuộc họp" });
    await within(log).findByText("CMO cho ý kiến về kênh.");
    expect(within(log).getByRole("separator", { name: "Vòng 1" })).toBeInTheDocument();
    expect(within(log).getByText("Mở đầu")).toBeInTheDocument();
    // Somebody else's message carries their name; one's own does not.
    expect(within(log).getByText("Lan Anh - Nhân viên Ads")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Họp tiếp" })).toBeInTheDocument();
  });

  it("draws the turn being written live", async () => {
    backend({
      events: [
        ["state", { running: true, round: 1, message_id: null, text: "", tools: [] }],
        ["message", { message: LIVE }],
        ["delta", { message_id: "m4", text: "Tăng TikTok " }],
        ["delta", { message_id: "m4", text: "thêm 10%." }],
      ],
    });
    NAV.arriveAt("/agents/rooms?r=r1");
    renderWithQuery(<AgentRoomsWorkspace />);
    const log = await screen.findByRole("log", { name: "Diễn biến cuộc họp" });
    await within(log).findByText("Tăng TikTok thêm 10%.");
    expect(within(log).getByText("Phát biểu")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dừng" })).toBeInTheDocument();
    expect(screen.getByText(/Đang họp - tin mới/)).toBeInTheDocument();
  });

  it("the owner writes and stops; an invited person may only leave", async () => {
    const { calls } = backend({
      events: [["state", { running: true, round: 1, message_id: null, text: "", tools: [] }]],
    });
    NAV.arriveAt("/agents/rooms?r=r1");
    const user = userEvent.setup();
    renderWithQuery(<AgentRoomsWorkspace />);
    await user.type(await screen.findByLabelText("Tin nhắn trong phòng họp"), "Chốt trần 10%{Enter}");
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/r1/messages"))).toBe(true),
    );
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/r1/messages"))?.body).toEqual({ text: "Chốt trần 10%" });
    await user.click(screen.getByRole("button", { name: "Dừng" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/r1/stop"))).toBe(true));
  });

  it("an invited person sees no owner controls", async () => {
    backend({ owner: false });
    NAV.arriveAt("/agents/rooms?r=r1");
    renderWithQuery(<AgentRoomsWorkspace />);
    await screen.findByRole("button", { name: "Rời phòng" });
    expect(screen.queryByRole("button", { name: "Mời người" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Họp tiếp" })).toBeNull();
    expect(screen.getByText("Tin của bạn được các agent đọc ở lượt nói sau.")).toBeInTheDocument();
  });

  it("creates a room with agents in order and a chair", async () => {
    const { calls } = backend();
    const user = userEvent.setup();
    renderWithQuery(<AgentRoomsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "+ Phòng họp mới" }));
    const dialog = await screen.findByRole("dialog", { name: "Phòng họp mới" });
    const create = within(dialog).getByRole("button", { name: "Tạo phòng" });
    await user.click(await within(dialog).findByRole("checkbox", { name: /CMO/ }));
    expect(create).toBeDisabled(); // one agent is not a meeting
    await user.click(within(dialog).getByRole("checkbox", { name: /CFO/ }));
    await user.click(within(dialog).getAllByRole("radio", { name: "Chủ trì" })[1]);
    await user.click(create);
    await waitFor(() => expect(NAV.current()).toBe("r=r9"));
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/api/agents/rooms"))?.body).toEqual({
      title: "",
      agents: ["cmo", "cfo"],
      chair: "cfo",
    });
  });

  it("invites only people who may chat with every agent", async () => {
    const { calls } = backend();
    NAV.arriveAt("/agents/rooms?r=r1");
    const user = userEvent.setup();
    renderWithQuery(<AgentRoomsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "Mời người" }));
    const dialog = await screen.findByRole("dialog", { name: "Người trong phòng" });
    const refused = (await within(dialog).findByText("Hà Kế toán")).closest("li") as HTMLElement;
    expect(refused).toHaveTextContent("Không chat được với: CMO");
    expect(within(refused).getByRole("button", { name: "Mời" })).toBeDisabled();
    const allowed = within(dialog).getByText("Minh Ads").closest("li") as HTMLElement;
    await user.click(within(allowed).getByRole("button", { name: "Mời" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/r1/members"))?.body).toEqual({ user_id: "u3" }),
    );
  });
});
