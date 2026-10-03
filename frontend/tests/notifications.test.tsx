/**
 * Step 1F.2.3d, browser half: the notification centre and content priority.
 *
 * Sections 96-98 are the bell; 99-101 are priority. They share a file because
 * they ship as one step, and because both are really testing the same rule in
 * two places: **the server decides, the panel renders**. The badge is a number
 * from a response rather than local arithmetic, and the priority control appears
 * because `available_actions` says so rather than because a role looked right.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONTENT,
  confirm,
  renderWithQuery,
  SESSION,
  settleLanes,
  stubFetch,
  urlStore,
  VERSION,
} from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const replaced: string[] = [];
const pushed: string[] = [];

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => {
      replaced.push(url);
      URL_BAR.navigate(url);
    },
    push: (url: string) => {
      pushed.push(url);
    },
  }),
}));

const { Shell } = await import("@/components/shell");
const { default: ContentBoardPage } = await import("@/app/pr/content/page");
const { default: ContentDetailPage } = await import("@/app/pr/content/[id]/page");

const NOTIFICATION = {
  id: "55555555-5555-5555-5555-555555555555",
  event_type: "pr_content_approved",
  title: "Nội dung đã được duyệt",
  body: "“Chăm sóc sau nâng mũi” đã được Trưởng phòng duyệt.",
  target_kind: "pr_content",
  target_id: CONTENT.id,
  read_at: null as string | null,
  created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
};

const READ_NOTIFICATION = {
  ...NOTIFICATION,
  id: "66666666-6666-6666-6666-666666666666",
  title: "Nội dung cần sửa",
  body: "“Chăm sóc sau nâng mũi” cần chỉnh sửa sản phẩm.",
  read_at: "2026-08-13T01:00:00+00:00",
};

const inbox = (items: unknown[], unread: number) => ({
  items,
  unread_count: unread,
  has_more: false,
});

/** The shell's own routes, plus whatever a test wants the bell to answer with. */
function shellRoutes(
  extra: Array<{ match: string; status?: number; body?: unknown; method?: string }> = [],
) {
  return [
    { match: "/api/auth/session", body: SESSION },
    { match: "/api/notifications/unread-count", body: { unread_count: 0 } },
    { match: "/api/notifications", body: inbox([], 0) },
    ...extra,
  ];
}

async function openShell(
  extra: Array<{ match: string; status?: number; body?: unknown; method?: string }> = [],
) {
  // `extra` first so a test's stub wins the substring match over the defaults.
  const stub = stubFetch([...extra, ...shellRoutes()]);
  renderWithQuery(<Shell>{null}</Shell>);
  await screen.findByRole("button", { name: "Thông báo" });
  return stub;
}

const bell = () => screen.getByRole("button", { name: "Thông báo" });
const panel = () => screen.getByRole("dialog", { name: "Thông báo" });

beforeEach(() => {
  SEARCH.value = new URLSearchParams({ scope: "ALL" });
  replaced.length = 0;
  pushed.length = 0;
});

// =============================================================================
// 96. The bell
// =============================================================================

describe("96. the bell lives in the authenticated shell", () => {
  it("renders for a signed-in person", async () => {
    await openShell();
    expect(bell()).toBeInTheDocument();
  });

  it("shows the unread count when there is one", async () => {
    await openShell([{ match: "/api/notifications/unread-count", body: { unread_count: 3 } }]);
    await waitFor(() => expect(bell()).toHaveTextContent("3"));
    expect(screen.getByLabelText("3 thông báo chưa đọc")).toBeInTheDocument();
  });

  it("shows no number at all when nothing is unread", async () => {
    await openShell();
    await waitFor(() => expect(bell()).toBeInTheDocument());
    // Not "0" and not a dot: an empty bell should look empty.
    expect(bell()).not.toHaveTextContent(/\d/);
    expect(screen.queryByLabelText(/thông báo chưa đọc/)).not.toBeInTheDocument();
  });

  it("caps the badge rather than widening the header", async () => {
    await openShell([{ match: "/api/notifications/unread-count", body: { unread_count: 47 } }]);
    await waitFor(() => expect(bell()).toHaveTextContent("9+"));
    // The exact number is still available to a screen reader.
    expect(screen.getByLabelText("47 thông báo chưa đọc")).toBeInTheDocument();
  });

  it("does not render for somebody with no session", async () => {
    stubFetch([{ match: "/api/auth/session", status: 401, body: { error: { message: "no" } } }]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Thông báo" })).not.toBeInTheDocument(),
    );
  });
});

// =============================================================================
// 97. The panel
// =============================================================================

describe("97. opening the bell shows what happened", () => {
  it("lists the notifications, newest first as the server sent them", async () => {
    await openShell([
      {
        match: "/api/notifications?",
        body: inbox([NOTIFICATION, READ_NOTIFICATION], 1),
      },
      { match: "/api/notifications/unread-count", body: { unread_count: 1 } },
    ]);

    await userEvent.click(bell());
    const rows = await within(panel()).findAllByText(/đã được duyệt|cần sửa/);
    expect(rows.length).toBeGreaterThan(0);
    expect(within(panel()).getByText(NOTIFICATION.body)).toBeInTheDocument();
    // Both fixtures share a timestamp, so this is "at least one row is dated"
    // rather than an assertion about which.
    expect(within(panel()).getAllByText("5 phút trước").length).toBeGreaterThan(0);
  });

  it("distinguishes unread without relying on colour", async () => {
    await openShell([
      { match: "/api/notifications?", body: inbox([NOTIFICATION, READ_NOTIFICATION], 1) },
      { match: "/api/notifications/unread-count", body: { unread_count: 1 } },
    ]);
    await userEvent.click(bell());

    // The word is in the accessible name, so "unread" survives a greyscale
    // screen and a screen reader alike.
    await waitFor(() =>
      expect(within(panel()).getByText("(chưa đọc)", { selector: ".sr-only" })).toBeInTheDocument(),
    );
    expect(within(panel()).queryAllByText("(chưa đọc)")).toHaveLength(1);
  });

  it("says so plainly when there is nothing", async () => {
    await openShell();
    await userEvent.click(bell());
    expect(await within(panel()).findByText("Không có thông báo.")).toBeInTheDocument();
  });

  it("never renders a raw identifier as something to read", async () => {
    await openShell([
      { match: "/api/notifications?", body: inbox([NOTIFICATION], 1) },
      { match: "/api/notifications/unread-count", body: { unread_count: 1 } },
    ]);
    await userEvent.click(bell());
    await within(panel()).findByText(NOTIFICATION.body);

    expect(panel().textContent).not.toContain(NOTIFICATION.id);
    expect(panel().textContent).not.toContain(CONTENT.id);
  });

  it("wraps a long title instead of stretching the panel", async () => {
    const long = {
      ...NOTIFICATION,
      body: "Rất".repeat(120),
    };
    await openShell([
      { match: "/api/notifications?", body: inbox([long], 1) },
      { match: "/api/notifications/unread-count", body: { unread_count: 1 } },
    ]);
    await userEvent.click(bell());

    const body = await within(panel()).findByText(long.body);
    expect(body.className).toContain("break-words");
  });
});

// =============================================================================
// 98. Reading, and where the count comes from
// =============================================================================

describe("98. read state is the server's answer", () => {
  it("marks a notification read and opens the content it points at", async () => {
    const stub = await openShell([
      { match: "/api/notifications?", body: inbox([NOTIFICATION], 1) },
      { match: "/api/notifications/unread-count", body: { unread_count: 1 } },
      {
        match: `/api/notifications/${NOTIFICATION.id}/read`,
        method: "POST",
        body: { ...NOTIFICATION, read_at: "2026-08-13T02:00:00+00:00" },
      },
    ]);

    await userEvent.click(bell());
    await userEvent.click(await within(panel()).findByText(NOTIFICATION.title));

    const calls = (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls;
    expect(
      calls.some(
        (call) => call.method === "POST" && call.url.includes(`${NOTIFICATION.id}/read`),
      ),
    ).toBe(true);
    expect(pushed).toContain(`/pr/content/${CONTENT.id}`);
  });

  it("does not re-post for a notification already read", async () => {
    const stub = await openShell([
      { match: "/api/notifications?", body: inbox([READ_NOTIFICATION], 0) },
    ]);

    await userEvent.click(bell());
    await userEvent.click(await within(panel()).findByText(READ_NOTIFICATION.title));

    const calls = (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls;
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    expect(pushed).toContain(`/pr/content/${CONTENT.id}`);
  });

  it("marks all read and takes the new count from the response", async () => {
    let unread = 4;
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      {
        match: "/api/notifications/read-all",
        method: "POST",
        body: { marked: 4, unread_count: 0 },
      },
      { match: "/api/notifications?", body: inbox([NOTIFICATION], 4) },
      { match: "/api/notifications/unread-count", body: { unread_count: 4 } },
      { match: "/api/notifications", body: inbox([NOTIFICATION], 4) },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await screen.findByRole("button", { name: "Thông báo" });
    await waitFor(() => expect(bell()).toHaveTextContent("4"));

    await userEvent.click(bell());
    await userEvent.click(
      await within(panel()).findByRole("button", { name: "Đánh dấu tất cả đã đọc" }),
    );

    const calls = (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls;
    expect(calls.some((call) => call.url.includes("/read-all") && call.method === "POST")).toBe(
      true,
    );
    // And the badge is re-read afterwards rather than zeroed locally: the count
    // endpoint is asked again once the write settles, so whatever the server
    // now says is what the header shows.
    const countCalls = () =>
      calls.filter((call) => call.url.includes("/unread-count")).length;
    await waitFor(() => expect(countCalls()).toBeGreaterThan(1));
  });

  it("offers nothing to clear when the bell is already empty", async () => {
    await openShell();
    await userEvent.click(bell());
    expect(
      await within(panel()).findByRole("button", { name: "Đánh dấu tất cả đã đọc" }),
    ).toBeDisabled();
  });

  it("keeps the badge from being local arithmetic", () => {
    // The guardrail behind the three tests above: nothing in the component may
    // compute a count. It renders `unread_count` from a response and re-reads
    // it after every write, so a failed mark-read cannot leave the badge lying.
    const source = readFileSync(
      path.resolve(__dirname, "../src/components/notifications.tsx"),
      "utf8",
    );
    expect(source).not.toMatch(/unread\s*[-+]{2}/);
    expect(source).not.toMatch(/setUnread|useState<number>/);
    expect(source).toContain("count.data?.unread_count");
  });
});

// =============================================================================
// 99. Priority on the board
// =============================================================================

const boardBody = (items: unknown[], total = items.length) => ({
  items,
  total,
  scope: "ALL",
  stage_counts: [{ stage: "IDEA", count: total }],
  production_state_counts: [],
  limit: 60,
  offset: 0,
});

const boardRoutes = (body: unknown) => [
  { match: "/api/pr/brands", body: [{ id: CONTENT.brand_id, name: "Apexmed" }] },
  { match: "/api/pr/platforms", body: [] },
  { match: "/api/pr/channels", body: [] },
  { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }] },
  { match: "/api/pr/contents/board", body },
];

async function openBoard(items: unknown[]) {
  const stub = stubFetch(boardRoutes(boardBody(items)));
  renderWithQuery(<ContentBoardPage />);
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument(),
  );
  // Step 1F.2.3c2: the cards arrive per lane, after the figures do.
  await settleLanes();
  return stub;
}

/** Every `/contents/board` request the page made, as query strings, in order. */
function boardRequests(stub: ReturnType<typeof stubFetch>): URLSearchParams[] {
  return (stub as unknown as { calls: Array<{ url: string }> }).calls
    .filter((call) => call.url.includes("/api/pr/contents/board"))
    .map((call) => new URLSearchParams(call.url.split("?")[1] ?? ""));
}

describe("99. priority on cards and in the filter bar", () => {
  it("badges content that is not ordinary, and leaves ordinary content alone", async () => {
    // Titles deliberately unlike the labels, so finding "Rất gấp" on screen
    // proves the badge rendered rather than the card's own heading.
    await openBoard([
      { ...CONTENT, id: "aaa", title: "Bài một", workflow_stage: "IDEA", priority: "CRITICAL" },
      { ...CONTENT, id: "bbb", title: "Bài hai", workflow_stage: "IDEA", priority: "URGENT" },
      { ...CONTENT, id: "ccc", title: "Bài ba", workflow_stage: "IDEA", priority: "HIGH" },
      { ...CONTENT, id: "ddd", title: "Bài bốn", workflow_stage: "IDEA", priority: "NORMAL" },
    ]);

    // Scoped to the board: the same four words are also the filter's options.
    const board = within(screen.getByRole("region", { name: "Bảng nội dung" }));
    expect(board.getByText("Rất gấp")).toBeInTheDocument();
    expect(board.getByText("Gấp")).toBeInTheDocument();
    expect(board.getByText("Ưu tiên")).toBeInTheDocument();
    // "Bình thường" is deliberately absent from the cards: sixty of them would
    // train people to stop reading the badge.
    expect(board.queryByText("Bình thường")).not.toBeInTheDocument();
  });

  it("never shows the raw code to somebody reading the board", async () => {
    await openBoard([
      { ...CONTENT, id: "aaa", title: "Bài A", workflow_stage: "IDEA", priority: "CRITICAL" },
    ]);
    const board = screen.getByRole("region", { name: "Bảng nội dung" });
    expect(board.textContent).not.toContain("CRITICAL");
  });

  it("puts the filter in the Bộ lọc panel with the rest", async () => {
    SEARCH.value = new URLSearchParams();
    await openBoard([]);
    const filters = screen.getByRole("region", { name: "Bộ lọc" });

    const select = within(filters).getByRole("combobox", { name: "Lọc theo mức ưu tiên" });
    expect(select).toBeInTheDocument();
    // Most urgent first, and in words.
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Mọi mức ưu tiên",
      "Rất gấp",
      "Gấp",
      "Ưu tiên",
      "Bình thường",
    ]);
  });

  it("sends the chosen level to the server and writes it to the URL", async () => {
    const stub = await openBoard([]);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo mức ưu tiên" }),
      "CRITICAL",
    );

    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    expect(new URLSearchParams(replaced.at(-1)!.split("?")[1]).get("priority")).toBe("CRITICAL");
    await waitFor(() =>
      expect(boardRequests(stub).at(-1)!.get("priority")).toBe("CRITICAL"),
    );
  });

  it("restores the filter from the URL, so a reload and a shared link keep it", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", priority: "URGENT" });
    const stub = await openBoard([]);

    expect(boardRequests(stub).at(-1)!.get("priority")).toBe("URGENT");
    expect(screen.getByRole("combobox", { name: "Lọc theo mức ưu tiên" })).toHaveValue("URGENT");
  });

  it("drops the page when the filter changes", async () => {
    // Page 3 of an unfiltered board is past the end of a filtered one - the
    // failure Step 1F.2.3c1 fixed for groups, in a new dimension.
    SEARCH.value = new URLSearchParams({ scope: "ALL", page: "2" });
    await openBoard([]);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo mức ưu tiên" }),
      "CRITICAL",
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    expect(new URLSearchParams(replaced.at(-1)!.split("?")[1]).get("page")).toBeNull();
  });

  it("does not sort the page it was handed", () => {
    // The board renders the server's order. A second sort here would disagree
    // with the pagination the same response carried, which is how a "Rất gấp"
    // item ends up stranded on page 3.
    const source = readFileSync(
      path.resolve(__dirname, "../src/app/pr/content/page.tsx"),
      "utf8",
    );
    expect(source).not.toContain(".sort(");
    expect(source).not.toContain("PRIORITY_RANK");
  });
});

// =============================================================================
// 100. Priority in the create form
// =============================================================================

describe("100. creating content offers a priority, defaulted", () => {
  it("offers the four levels in words, starting at Bình thường", async () => {
    await openBoard([]);
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));

    const select = await screen.findByRole("combobox", { name: /Mức độ ưu tiên/ });
    expect(select).toHaveValue("NORMAL");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Bình thường",
      "Ưu tiên",
      "Gấp",
      "Rất gấp",
    ]);
  });

  it("lets somebody escalate at creation time", async () => {
    await openBoard([]);
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));

    const select = await screen.findByRole("combobox", { name: /Mức độ ưu tiên/ });
    await userEvent.selectOptions(select, "CRITICAL");
    expect(select).toHaveValue("CRITICAL");
  });

  it("carries the level on the create call rather than patching afterwards", () => {
    // A piece created as "Rất gấp" must be urgent from its first row, not
    // ordinary for as long as a second request takes to land.
    const source = readFileSync(path.resolve(__dirname, "../src/lib/api.ts"), "utf8");
    const createBlock = source.slice(source.indexOf("createContent:"));
    expect(createBlock.slice(0, 600)).toContain("priority?: string");
  });
});

// =============================================================================
// 101. Priority on the detail page
// =============================================================================

const detailRoutes = (
  actions: Array<{ action: string }>,
  content: Record<string, unknown> = CONTENT,
) => [
  {
    match: `/api/pr/contents/${CONTENT.id}/available-actions`,
    body: { content_id: CONTENT.id, workflow_stage: content.workflow_stage, available_actions: actions },
  },
  { match: `/api/pr/contents/${CONTENT.id}/review-context`, body: { content, current_version: VERSION, approvals: [], tasks: [] } },
  { match: `/api/pr/contents/${CONTENT.id}/versions`, body: [VERSION] },
  { match: `/api/pr/contents/${CONTENT.id}/history`, body: { transitions: [] } },
  { match: `/api/pr/contents/${CONTENT.id}/production`, body: { production_state: null } },
  { match: `/api/pr/contents/${CONTENT.id}/ai-review`, body: { active: false, runs: [] } },
  {
    match: `/api/pr/contents/${CONTENT.id}`,
    body: { content, current_version: VERSION, targets: [], brand: { id: content.brand_id, name: "Apexmed" } },
  },
  { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }] },
];

async function openDetail(
  actions: Array<{ action: string }>,
  content: Record<string, unknown> = CONTENT,
  extra: Array<{ match: string; status?: number; body?: unknown; method?: string }> = [],
) {
  const stub = stubFetch([...extra, ...detailRoutes(actions, content)]);
  renderWithQuery(<ContentDetailPage />);
  await screen.findByRole("tab", { name: "Tổng quan" });
  await userEvent.click(screen.getByRole("tab", { name: "Tổng quan" }));
  return stub;
}

describe("101. the detail page always shows the priority", () => {
  /** The `<dd>` beside the "Mức độ ưu tiên" term - the header carries a badge too. */
  const priorityCell = async () => {
    const term = await screen.findByText("Mức độ ưu tiên");
    const cell = term.parentElement?.querySelector("dd");
    expect(cell).toBeTruthy();
    return cell as HTMLElement;
  };

  it("shows it in words even when it is the ordinary one", async () => {
    await openDetail([]);
    expect(await priorityCell()).toHaveTextContent("Bình thường");
  });

  it("is read-only when the server does not offer the action", async () => {
    await openDetail([], { ...CONTENT, priority: "URGENT" });
    const cell = await priorityCell();
    expect(within(cell).queryByRole("combobox")).not.toBeInTheDocument();
    expect(cell).toHaveTextContent("Gấp");
  });

  it("becomes a control when the server offers SET_PRIORITY", async () => {
    await openDetail([{ action: "SET_PRIORITY" }]);
    const select = await screen.findByRole("combobox", { name: "Mức độ ưu tiên" });
    expect(select).toHaveValue("NORMAL");
  });

  it("patches the chosen level", async () => {
    const stub = await openDetail(
      [{ action: "SET_PRIORITY" }],
      CONTENT,
      [
        {
          match: `/api/pr/contents/${CONTENT.id}/priority`,
          method: "PATCH",
          body: { content: { ...CONTENT, priority: "CRITICAL" }, current_version: VERSION, targets: [] },
        },
      ],
    );

    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Mức độ ưu tiên" }),
      "CRITICAL",
    );
    // Step 1F.2.8: a dropdown that wrote on `change` is a mis-scroll away from
    // a business write, so all three classification fields confirm.
    await confirm();

    const calls = (
      stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
    ).calls;
    const patch = calls.find((call) => call.method === "PATCH" && call.url.includes("/priority"));
    expect(patch).toBeTruthy();
    expect(patch!.body).toEqual({ priority: "CRITICAL" });
  });

  it("does not decide for itself who may retriage", () => {
    // The rule lives on the server. The page reads `available_actions`; it does
    // not look at a role, an owner id, or a stage.
    const source = readFileSync(
      path.resolve(__dirname, "../src/app/pr/content/[id]/page.tsx"),
      "utf8",
    );
    expect(source).toContain('has(actions.data?.available_actions, "SET_PRIORITY")');
    expect(source).not.toMatch(/role\s*===\s*"(ADMIN|OWNER|TEAM_LEAD)"/);
  });
});

// =============================================================================
// 102. Nothing above changed the work queue's rules
// =============================================================================

describe("102. the board still asks the server for everything", () => {
  it("keeps the group in the request beside the new filter", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION", priority: "URGENT" });
    const stub = await openBoard([]);

    const last = boardRequests(stub).at(-1)!;
    expect(last.get("group")).toBe("PRODUCTION");
    expect(last.get("priority")).toBe("URGENT");
    expect(last.get("scope")).toBe("ALL");
  });

  it("counts the filter as a filter, so it can be cleared", async () => {
    SEARCH.value = new URLSearchParams({ priority: "CRITICAL" });
    await openBoard([]);
    expect(screen.getByRole("button", { name: "Xóa bộ lọc" })).toBeEnabled();

    await act(async () => {
      URL_BAR.navigate("/pr/content");
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa bộ lọc" })).toBeDisabled(),
    );
  });
});
