/**
 * Step 1F.2.2 - the work queue, its URL, and readable dropdowns.
 *
 * Numbered 69-80, continuing the Step 1F.2 numbering. Two groups.
 *
 * **69-75, the workspace.** These assert the thing the step is actually for: the
 * page is a work queue rather than a list of everything, every filter is asked of
 * the server, and the filters live in the URL rather than in component memory.
 * The load-bearing assertion in most of them is *what was requested*, read off
 * the fetch stub - because a filter that renders a chip and sends nothing looks
 * identical on screen to one that works.
 *
 * **76-80, dropdown readability.** Not a pixel test: a screenshot comparison
 * would break on a font change and tell nobody why. What is asserted instead is
 * the mechanism - one CSS rule that paints native `<option>` text dark on a light
 * menu, and every PR admin picker going through the one component rather than
 * re-rolling its own. That is what actually keeps the bug fixed, since the reason
 * it existed at all was fourteen hand-written selects and a fix applied to some
 * of them.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  CONTENT_SCOPES,
  DATE_PRESETS,
  STAGE_ORDER,
  datePresetRange,
  periodOptionsFor,
  toDayString,
} from "@/lib/labels";
import { CONTENT, renderWithQuery, SESSION, settleLanes, stubFetch, urlStore } from "./helpers";

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

/**
 * The URL the page is rendered at, and the router that writes to it.
 *
 * `replace` records *and* navigates. Recording alone was the honest shape while
 * the query string only carried filters a test could seed before rendering - the
 * two halves were asserted separately: that a control writes the right URL
 * (`replaced`), and that a page rendered at a URL asks the server for the right
 * thing (`SEARCH`). Step 1F.2.3c1 put the lifecycle group in the URL, so a tab
 * click *is* both halves at once, and a mock that swallowed the write would
 * leave the clicked tab closed. `urlStore` is the subscription that makes the
 * page follow the address bar the way Next's own router does.
 */
const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const replaced: string[] = [];

// `replace` records **and** navigates: since Step 1F.2.3c1 the lifecycle group
// is a query parameter, so a mock that only recorded would leave a clicked tab
// closed and the page fetching the group it started on. See `urlStore`.
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => {
      replaced.push(url);
      URL_BAR.navigate(url);
    },
    push: vi.fn(),
  }),
}));

// Imported after the mock, since the page reads these hooks at module scope.
const { default: ContentBoardPage } = await import("@/app/pr/content/page");
const { default: ChannelsPage } = await import("@/app/pr/channels/page");

const IDEA_ITEM = { ...CONTENT, workflow_stage: "IDEA", title: "Chăm sóc sau sinh" };
const GATED_ITEM = {
  ...CONTENT,
  id: "99999999-9999-9999-9999-999999999999",
  code: "CNT-2026-000009",
  workflow_stage: "TEAM_LEAD_REVIEW",
  title: "Bài chờ duyệt",
};

const BRAND = { id: CONTENT.brand_id, code: "APEXMED", name: "Apexmed" };

const PLATFORMS = [
  {
    id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    code: "TIKTOK",
    name: "TikTok",
    status: "ACTIVE",
    policy_grounded: true,
  },
  {
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    code: "FACEBOOK",
    name: "Facebook",
    status: "ACTIVE",
    policy_grounded: true,
  },
];

const CHANNELS = [
  {
    id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    code: "CH-TT",
    name: "Apexmed TikTok",
    category: "SCALE",
    status: "ACTIVE",
    brand_id: CONTENT.brand_id,
    platform_id: PLATFORMS[0].id,
    tier: null,
    url: null,
    platform_code: "TIKTOK",
    policy_grounded_platform: true,
  },
];

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: "Lê Trưởng Nhóm", role: "TEAM_LEAD" },
  { user_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", full_name: "Nguyễn A", role: "EMPLOYEE" },
];

/** The four derived production states, in the order the work moves. */
const PRODUCTION_STATES = [
  "WAITING_FOR_PRODUCER",
  "READY_FOR_PRODUCTION",
  "IN_PRODUCTION",
  "IN_INTERNAL_REVIEW",
];

/** One board response, with counts that actually describe `items`. */
const board = (items: unknown[], scope = "ALL", total = items.length) => ({
  items,
  total,
  scope,
  stage_counts: STAGE_ORDER.map((stage) => ({
    stage,
    count: items.filter((item) => (item as { workflow_stage: string }).workflow_stage === stage)
      .length,
  })),
  // Step 1F.2.3c. The same set counted by derived production state, which is
  // what labels the four production columns - a stage count cannot, because two
  // of them are the same stage.
  production_state_counts: PRODUCTION_STATES.map((production_state) => ({
    production_state,
    count: items.filter(
      (item) => (item as { production_state?: string }).production_state === production_state,
    ).length,
  })),
  // Step 1F.2.3f.6d. No month unless a test spreads one in: the board's
  // default is every active row. `period_applied` follows the server's rule -
  // a month, and not the action queue.
  period: null,
  period_applied: false,
  // Step 1F.2.3f.6b. The server's current business month, whatever was selected.
  current_period: CURRENT_PERIOD,
  limit: 60,
  offset: 0,
});

/** A month a test can spread into a fixture as the selected one. */
const PERIOD = "2026-09";

/** A board fixture read against `month`, the way the server would report it. */
const boardFor = (month: string, items: unknown[], scope = "ALL") => ({
  ...board(items, scope),
  period: month,
  period_applied: scope !== "MY_ACTIONS",
});

/** The current business month the server reports - the selector's anchor. */
const CURRENT_PERIOD = "2026-09";

/** No published output from the previous month to archive - the quiet default. */
const NO_CANDIDATES = {
  period: "2026-08",
  total: 0,
  content_ids: [],
  limit: 200,
  truncated: false,
  may_archive: true,
};

const routes = (body: unknown, candidates: unknown = NO_CANDIDATES) => [
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/api/pr/platforms", body: PLATFORMS },
  { match: "/api/pr/channels", body: CHANNELS },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/contents/archive-candidates", body: candidates },
  { match: "/api/pr/contents/board", body },
];

/** Every URL the page requested from `/contents/board`, in order. */
function boardRequests(stub: ReturnType<typeof stubFetch>): URLSearchParams[] {
  const calls = (stub as unknown as { calls: Array<{ url: string }> }).calls;
  return calls
    .filter((call) => call.url.includes("/api/pr/contents/board"))
    .map((call) => new URLSearchParams(call.url.split("?")[1] ?? ""));
}

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  replaced.length = 0;
});

// ===========================================================================
// 69-71: THE WORK QUEUE
// ===========================================================================

describe("69. the workspace opens on a scope rather than on everything", () => {
  it("offers the four views and highlights the one the server chose", async () => {
    stubFetch(routes(board([IDEA_ITEM, GATED_ITEM], "MY_ACTIONS")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    for (const scope of CONTENT_SCOPES) {
      expect(screen.getByRole("tab", { name: scope.label })).toBeInTheDocument();
    }
    // "Cần tôi xử lý" is selected because the *server* said `MY_ACTIONS`, not
    // because the browser worked out anything about this session.
    expect(screen.getByRole("tab", { name: "Cần tôi xử lý" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Tất cả" })).toHaveAttribute("aria-selected", "false");
  });

  it("opens on Tất cả when the URL names no scope and the server picks the default", async () => {
    // Step 1F.2.3f.6a. The server's default is ALL for everybody; the page
    // sends no scope and highlights the echo. A URL that carries no scope
    // stays that way - nothing writes `scope=ALL` into it.
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(screen.getByRole("tab", { name: "Tất cả" })).toHaveAttribute("aria-selected", "true");
    expect(boardRequests(stub)[0].has("scope")).toBe(false);
    expect(SEARCH.value.has("scope")).toBe(false);
    expect(replaced).toEqual([]);
  });

  it("sends no scope when the URL carries none, so the server picks", async () => {
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const [request] = boardRequests(stub);
    // Absent, not `ALL`. Sending `ALL` would be the browser choosing the widest
    // view for everybody, which is the behaviour this step removed.
    expect(request.has("scope")).toBe(false);
  });

  it("follows the server's scope even when it is not the one the browser would pick", async () => {
    stubFetch(routes(board([IDEA_ITEM], "MY_CONTENT")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(screen.getByRole("tab", { name: "Của tôi" })).toHaveAttribute("aria-selected", "true");
  });

  it("offers the views widest first, starting with the one people orient by", async () => {
    stubFetch(routes(board([IDEA_ITEM, GATED_ITEM], "MY_ACTIONS")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const strip = screen.getByRole("tablist", { name: "Phạm vi nội dung" });
    expect(within(strip).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Tất cả",
      "Cần tôi xử lý",
      "Của tôi",
      "Team",
    ]);
    // The keys are untouched by the reordering: the server's vocabulary is not a
    // display decision, and a tab strip rearranged into new enum values would be
    // a protocol change wearing a UX change's clothes.
    expect(CONTENT_SCOPES.map((scope) => scope.key)).toEqual([
      "ALL",
      "MY_ACTIONS",
      "MY_CONTENT",
      "TEAM",
    ]);
  });

  it("does not make the first tab the default, whichever tab is first", async () => {
    // The reviewer case, and the one the order change could plausibly have
    // broken: "Tất cả" now leads the strip, and the session still opens on the
    // queue because the *server* said so. Position is not precedence.
    stubFetch(routes(board([IDEA_ITEM, GATED_ITEM], "MY_ACTIONS")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const strip = screen.getByRole("tablist", { name: "Phạm vi nội dung" });
    const [first] = within(strip).getAllByRole("tab");
    expect(first).toHaveTextContent("Tất cả");
    expect(first).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Cần tôi xử lý" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("69b. the board distinguishes the two halves of APPROVED", () => {
  it("labels an unclaimed approved card differently from a claimed one", async () => {
    // Step 1F.2.3b. Both cards are at ``APPROVED`` and sit in the same lane -
    // the stage is the same fact - so the difference has to be on the card, and
    // it comes from the server's ``production_state`` rather than from the
    // browser comparing ``producer_user_id`` to null.
    const waiting = {
      ...IDEA_ITEM,
      id: "aaaaaaaa-1111-1111-1111-111111111111",
      workflow_stage: "APPROVED",
      title: "Chờ người nhận",
      producer_user_id: null,
      production_state: "WAITING_FOR_PRODUCER",
    };
    const ready = {
      ...IDEA_ITEM,
      id: "aaaaaaaa-2222-2222-2222-222222222222",
      workflow_stage: "APPROVED",
      title: "Đã có người nhận",
      producer_user_id: "bbbbbbbb-2222-2222-2222-222222222222",
      production_state: "READY_FOR_PRODUCTION",
    };
    const producing = {
      ...IDEA_ITEM,
      id: "aaaaaaaa-3333-3333-3333-333333333333",
      workflow_stage: "PRODUCTION",
      title: "Đang làm",
      producer_user_id: "bbbbbbbb-2222-2222-2222-222222222222",
      production_state: "IN_PRODUCTION",
    };
    SEARCH.value = new URLSearchParams({ scope: "ALL" });
    stubFetch(routes(board([waiting, ready, producing], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    // The board opens on "Chuẩn bị"; these three live in "Sản xuất", which is
    // where the two halves of APPROVED sit side by side and have to be told
    // apart. Clicking the tab moves the URL and refetches - since Step 1F.2.3c1
    // the group is part of the query, not a regrouping of what already arrived.
    await waitFor(() => expect(screen.getByRole("tab", { name: /Sản xuất/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: /Sản xuất/ }));
    await waitFor(() => expect(screen.getByText("Chờ người nhận")).toBeInTheDocument());

    // Step 1F.2.3c made each of these a column heading too, so every card is
    // located through its title and read from there - the words alone now
    // appear twice on the page, which is the point of the columns.
    for (const [title, state] of [
      ["Chờ người nhận", "Chờ nhận sản xuất"],
      ["Đã có người nhận", "Sẵn sàng sản xuất"],
      ["Đang làm", "Đang sản xuất"],
    ] as const) {
      expect(screen.getByText(title).closest("a")?.textContent, title).toContain(state);
    }
  });
});

describe("70. the counts describe the filtered view", () => {
  it("labels lanes and tiles from the response, not from the cards on screen", async () => {
    // 3 at IDEA in the counts, but only one card in this page of items - which is
    // what paging looks like, and what a lane counting its own children gets
    // wrong.
    const body = board([IDEA_ITEM], "MY_ACTIONS", 3);
    body.stage_counts = STAGE_ORDER.map((stage) => ({
      stage,
      count: stage === "IDEA" ? 3 : 0,
    }));
    stubFetch(routes(body));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const lane = screen.getByRole("region", { name: "Ý tưởng" });
    expect(lane).toHaveTextContent("3");
    // And the lifecycle tab above it agrees, because both read the same numbers.
    // Step 1F.2.3c removed the summary tile that used to make the third claim -
    // it was a report on a screen for processing work.
    expect(screen.getByRole("tab", { name: /^Chuẩn bị/ })).toHaveTextContent("Chuẩn bị3");
  });

  it("never derives a total from the page of items it was given", () => {
    const source = read("app/pr/content/page.tsx");
    // Every figure on the page is the server's, read through `lib/board.ts` so
    // that the tabs, the lane headers and the cards cannot index them three
    // different ways. Step 1F.2.3c2 sharpened this rather than loosening it: the
    // request that carries the figures now asks for `limit: 0`, so there is no
    // page of items for anything to be derived from in the first place.
    expect(source).toContain("boardCounts(board.data)");
    expect(source).toContain("columnCount(column, counts)");
    expect(source).toContain("limit: 0");
    expect(read("lib/board.ts")).toContain("stage_counts");
    // No second request for the figures: `/dashboard` is the landing page's, and
    // reading it here is exactly how the counts came to disagree with the list.
    expect(source).not.toContain("api.dashboard");
  });
});

describe("71. every filter is asked of the server", () => {
  it("sends the platform, channel, responsible user and stage as query parameters", async () => {
    SEARCH.value = new URLSearchParams({
      scope: "MY_ACTIONS",
      platform: PLATFORMS[0].id,
      channel: CHANNELS[0].id,
      responsible: PEOPLE[1].user_id,
      stage: "IDEA",
    });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const [request] = boardRequests(stub);
    expect(request.get("scope")).toBe("MY_ACTIONS");
    expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
    expect(request.get("channel_id")).toBe(CHANNELS[0].id);
    expect(request.get("responsible_user_id")).toBe(PEOPLE[1].user_id);
    expect(request.get("stage")).toBe("IDEA");
  });

  it("resolves a date preset into the from/to days the API takes", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", date: "TODAY" });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const today = toDayString(new Date());
    const [request] = boardRequests(stub);
    expect(request.get("date_from")).toBe(today);
    expect(request.get("date_to")).toBe(today);
    // And the dimension is named rather than left to the server's default, so the
    // labelled control and the query agree about which date is being filtered.
    expect(request.get("date_field")).toBe("CREATED_AT");
  });

  it("filters nothing in the browser", () => {
    const source = read("app/pr/content/page.tsx");
    // Step 1F.2.3c2. There is exactly one client-side pass over the rows, it is
    // inside a lane, and it is a guard rather than a filter: the server was
    // asked for this lane and returned it, so the pass drops nothing. The claim
    // this test makes is that the *selection* is a query parameter - `?lane=`,
    // beside `?group=` - and not a decision the browser takes over rows it was
    // handed. A lane that narrowed its way to the right cards would be the bug
    // this step removed, one component further in.
    expect(source).toContain("api.contentBoard");
    expect(source).toContain("lane, limit: LANE_PAGE_SIZE, offset: pageParam");
    expect(source).toContain("Defensive only");
    // And the guard is the column table's own predicate, never a stage compared
    // with a producer column - that rule is the server's.
    expect(source).toContain("columnHolds(column, item)");
    expect(source).not.toContain("producer_user_id");
  });
});

// ===========================================================================
// 72-73: THE URL IS THE STATE
// ===========================================================================

describe("72. filters live in the URL", () => {
  it("writes a chosen filter into the query string", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo nền tảng" }),
      PLATFORMS[0].id,
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    const written = new URLSearchParams(replaced.at(-1)!.split("?")[1]);
    expect(written.get("platform")).toBe(PLATFORMS[0].id);
  });

  it("keeps the filters already in the URL when one more is chosen", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", date: "TODAY" });
    stubFetch(routes(board([IDEA_ITEM], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo người phụ trách" }),
      PEOPLE[1].user_id,
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    const written = new URLSearchParams(replaced.at(-1)!.split("?")[1]);
    expect(written.get("scope")).toBe("ALL");
    expect(written.get("date")).toBe("TODAY");
    expect(written.get("responsible")).toBe(PEOPLE[1].user_id);
  });

  it("reproduces the same view from the same URL, which is what a reload is", async () => {
    const url = new URLSearchParams({
      scope: "MY_CONTENT",
      platform: PLATFORMS[1].id,
      date: "7D",
    });
    const asked: URLSearchParams[] = [];
    for (const _ of [0, 1]) {
      SEARCH.value = new URLSearchParams(url);
      const stub = stubFetch(routes(board([IDEA_ITEM], "MY_CONTENT")));
      const view = renderWithQuery(<ContentBoardPage />);
      await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
      asked.push(boardRequests(stub)[0]);
      view.unmount();
    }
    // Two independent mounts of the same URL asked the server the same question.
    // That is the property a reload, a bookmark and a shared link all rely on.
    expect(asked[0].toString()).toBe(asked[1].toString());
    expect(asked[0].get("scope")).toBe("MY_CONTENT");
    expect(asked[0].get("platform_id")).toBe(PLATFORMS[1].id);

    const week = datePresetRange("7D")!;
    expect(asked[0].get("date_from")).toBe(week.from);
    expect(asked[0].get("date_to")).toBe(week.to);
  });

  it("holds no local copy of the filters", () => {
    const source = read("app/pr/content/page.tsx");
    // The URL is read directly. A `useState` mirror is how the back button comes
    // to move the address bar and not the screen.
    expect(source).toContain("useSearchParams");
    for (const stale of [
      "useState(scope",
      "useState(platform",
      "useState(channel",
      "const [scope, setScope]",
      "const [platform, setPlatform]",
    ]) {
      expect(source).not.toContain(stale);
    }
  });
});

describe("73. the board has no global pager, and paging keeps the filters", () => {
  // Step 1F.2.3c2 removed the one pager under the board. It was the right shape
  // for a list and the wrong shape for four independent queues: with 155 items
  // waiting for a producer, three in production and one pager over all of them,
  // "1–60 / 170" was an honest sentence about a set nobody was looking at, and
  // "Sau →" was the only way to reach three cards under a header that said 3.
  it("offers no Previous/Next under the board", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    stubFetch(routes(board([IDEA_ITEM, GATED_ITEM], "ALL", 140)));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    await settleLanes();

    expect(screen.queryByRole("button", { name: /Sau/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Trước/ })).not.toBeInTheDocument();
    // Nor the range it captioned, which described the group rather than any
    // column of it.
    expect(screen.queryByText(/\d+–\d+ \/ \d+/)).not.toBeInTheDocument();
  });

  it("pages each lane instead, and keeps every filter on the lane request", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    const stub = stubFetch(routes(board([IDEA_ITEM], "ALL", 140)));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    await settleLanes();

    const lanes = boardRequests(stub).filter((request) => request.has("lane"));
    expect(lanes.length).toBeGreaterThan(0);
    for (const request of lanes) {
      expect(request.get("limit")).toBe("20");
      expect(request.get("offset")).toBe("0");
      // The lane narrows *with* the filters, never instead of them.
      expect(request.get("scope")).toBe("ALL");
      expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
    }
    // And the figures come from a request that asks for no rows at all, so the
    // page is never handed cards it would throw away.
    const figures = boardRequests(stub).find((request) => !request.has("lane"))!;
    expect(figures.get("limit")).toBe("0");
  });

  it("ignores a bookmarked page and drops it from the URL", async () => {
    // Old links exist. `?page=2` describes a pagination this screen no longer
    // has, so it must not reach a request and must not survive the next write.
    SEARCH.value = new URLSearchParams({ scope: "ALL", page: "2" });
    const stub = stubFetch(routes(board([IDEA_ITEM], "ALL", 140)));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    await settleLanes();

    for (const request of boardRequests(stub)) {
      expect(request.has("page")).toBe(false);
      expect(request.get("offset") ?? "0").toBe("0");
    }

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo nền tảng" }),
      PLATFORMS[0].id,
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    expect(new URLSearchParams(replaced.at(-1)!.split("?")[1]).has("page")).toBe(false);
  });
});

// ===========================================================================
// 74-75: THE FILTER BAR ITSELF
// ===========================================================================

describe("74. the pickers show names, never UUIDs", () => {
  it("names platforms, channels and people", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    for (const [label, expected] of [
      ["Lọc theo nền tảng", ["Mọi nền tảng", "TikTok", "Facebook"]],
      ["Lọc theo kênh", ["Mọi kênh", "Apexmed TikTok"]],
      ["Lọc theo người phụ trách", ["Mọi người phụ trách", "Lê Trưởng Nhóm", "Nguyễn A"]],
    ] as const) {
      const picker = screen.getByRole("combobox", { name: label });
      const text = [...picker.querySelectorAll("option")].map((option) => option.textContent);
      expect(text).toEqual(expected);
      // The value carries the id; the person reads a name. Nothing on screen is
      // a UUID, which is what made the pre-1E.2.1 pickers unusable.
      expect(picker.textContent).not.toMatch(
        /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/,
      );
    }
  });

  it("offers the Vietnamese date presets", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const picker = screen.getByRole("combobox", { name: "Lọc theo ngày" });
    expect([...picker.querySelectorAll("option")].map((option) => option.textContent)).toEqual(
      DATE_PRESETS.map((preset) => preset.label),
    );
  });

  it("asks which date dimension only once a date filter is on", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    const view = renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    // "Mọi lúc" filters no date, so there is nothing for a dimension to be about.
    expect(screen.queryByRole("combobox", { name: "Mốc ngày" })).not.toBeInTheDocument();
    view.unmount();

    SEARCH.value = new URLSearchParams({ date: "TODAY" });
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    const dimension = screen.getByRole("combobox", { name: "Mốc ngày" });
    expect([...dimension.querySelectorAll("option")].map((option) => option.textContent)).toEqual(
      ["Ngày tạo", "Ngày dự kiến đăng", "Ngày cập nhật mới nhất"],
    );
    // **The default does not move because a third choice exists.** `CREATED_AT`
    // is the only dimension that is always populated, and what somebody sees
    // before they choose is a separate decision from what they can choose.
    expect(dimension).toHaveValue("CREATED_AT");
  });

  it("filters by the latest update when that dimension is chosen", async () => {
    // The case the other two cannot answer: a piece drafted in August and
    // scheduled for October that somebody edited yesterday.
    SEARCH.value = new URLSearchParams({ date: "TODAY" });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Mốc ngày" }),
      "UPDATED_AT",
    );

    // In the URL, so the view is shareable like every other filter here.
    await waitFor(() => expect(SEARCH.value.get("date_field")).toBe("UPDATED_AT"));
    // And on the wire: the server is what filters, not this page.
    await waitFor(() => {
      const requests = boardRequests(stub);
      expect(requests[requests.length - 1].get("date_field")).toBe("UPDATED_AT");
    });
  });

  it("restores the chosen dimension from the URL, and moves back off it", async () => {
    SEARCH.value = new URLSearchParams({ date: "TODAY", date_field: "UPDATED_AT" });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const dimension = screen.getByRole("combobox", { name: "Mốc ngày" });
    expect(dimension).toHaveValue("UPDATED_AT");
    expect(boardRequests(stub)[0].get("date_field")).toBe("UPDATED_AT");

    // The two older values still work: an existing bookmark is not broken by a
    // third option appearing beside them.
    for (const code of ["PLANNED_PUBLISH_AT", "CREATED_AT"]) {
      await userEvent.selectOptions(dimension, code);
      await waitFor(() => expect(SEARCH.value.get("date_field")).toBe(code));
    }
  });

  it("keeps every other filter while the date dimension changes", async () => {
    SEARCH.value = new URLSearchParams({
      date: "TODAY",
      scope: "ALL",
      platform: PLATFORMS[0].id,
      stage: "IDEA",
    });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Mốc ngày" }),
      "UPDATED_AT",
    );

    await waitFor(() => {
      const request = boardRequests(stub)[boardRequests(stub).length - 1];
      expect(request.get("date_field")).toBe("UPDATED_AT");
      // The intersection travels as one query. A date basis that only narrowed
      // rows already fetched would agree for one screenful and diverge on the next.
      expect(request.get("scope")).toBe("ALL");
      expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
      expect(request.get("stage")).toBe("IDEA");
    });
    expect(SEARCH.value.get("scope")).toBe("ALL");
    expect(SEARCH.value.get("platform")).toBe(PLATFORMS[0].id);
    expect(SEARCH.value.get("stage")).toBe("IDEA");
  });

  it("lets the longest label shrink rather than push the row sideways", async () => {
    // The new label is half again as long as the old two, and the filters sit
    // in a wrapping flex row on a phone. `min-w-0` on the wrapper is what lets
    // the control shrink below its content; `max-w-full` on the select is what
    // stops it overflowing its own cell.
    SEARCH.value = new URLSearchParams({ date: "TODAY" });
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    const dimension = screen.getByRole("combobox", { name: "Mốc ngày" });
    expect(dimension.className).toContain("max-w-full");
    expect(dimension.closest("label")?.className).toContain("min-w-0");
  });

  it("shows the two date boxes only for the custom preset", async () => {
    SEARCH.value = new URLSearchParams({ date: "CUSTOM", from: "2026-08-01", to: "2026-08-10" });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(screen.getByLabelText("Từ ngày")).toHaveValue("2026-08-01");
    expect(screen.getByLabelText("Đến ngày")).toHaveValue("2026-08-10");
    const [request] = boardRequests(stub);
    expect(request.get("date_from")).toBe("2026-08-01");
    expect(request.get("date_to")).toBe("2026-08-10");
  });
});

describe("75. clearing the filters returns to the page as it opens", () => {
  it("offers the clear button only when something is filtered", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    const view = renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    // Present but inert, so choosing a filter does not reflow the row it is in.
    expect(screen.getByRole("button", { name: "Xóa bộ lọc" })).toBeDisabled();
    view.unmount();

    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    stubFetch(routes(board([IDEA_ITEM], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Xóa bộ lọc" }));
    // Back to a bare path, which drops the scope too - so the server picks the
    // default again rather than the person being left on "Tất cả".
    expect(replaced.at(-1)).toBe("/pr/content");
  });

  it("says why the board is empty when a filter is the reason", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    stubFetch(routes(board([], "ALL", 0)));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() =>
      expect(screen.getByText(/Không có nội dung nào khớp bộ lọc/)).toBeInTheDocument(),
    );
  });
});

// ===========================================================================
// 76-80: DROPDOWN READABILITY
// ===========================================================================

describe("76. option text is dark on a light menu, in one rule", () => {
  it("paints select options explicitly rather than inheriting the page colour", () => {
    const css = readFileSync(path.join(SRC, "app", "globals.css"), "utf8");
    const rule = css.slice(css.indexOf("select option"));
    expect(rule).toMatch(/select option,\s*\n?\s*select optgroup\s*\{/);
    // Literal colours, not the theme tokens. The surface being painted is the
    // operating system's menu, which stays light in dark mode - so following
    // `--text` is exactly the bug: near-white letters on a white menu.
    expect(rule.slice(0, 200)).toContain("color: #16191d");
    expect(rule.slice(0, 200)).toContain("background-color: #ffffff");
    expect(rule.slice(0, 200)).not.toContain("var(--text)");
  });

  it("keeps the fix in one place instead of one class per option", () => {
    for (const page of [
      "app/pr/content/page.tsx",
      "app/pr/content/[id]/page.tsx",
      "app/pr/channels/page.tsx",
      "app/pr/tasks/page.tsx",
      "app/pr/permissions/page.tsx",
    ]) {
      const source = read(page);
      // No per-option colour classes: that was the first attempt at this, and it
      // went stale the next time somebody added a picker.
      expect(source).not.toMatch(/<option[^>]*className/);
      // And no page re-rolls a raw `<select>` alongside the shared component.
      expect(source).not.toContain("<select");
    }
  });
});

describe("77. every PR admin picker goes through the shared component", () => {
  it("marks the board's filter pickers", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    for (const label of [
      "Lọc theo ngày",
      "Lọc theo nền tảng",
      "Lọc theo kênh",
      "Lọc theo người phụ trách",
      "Lọc theo bước",
    ]) {
      expect(screen.getByRole("combobox", { name: label })).toHaveAttribute("data-select", "pr");
    }
  });

  it("marks the brand, responsible-user, channel and distribution-mode pickers", async () => {
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));

    const brand = await screen.findByRole("combobox", { name: /Thương hiệu/ });
    expect(brand).toHaveAttribute("data-select", "pr");
    expect([...brand.querySelectorAll("option")].map((o) => o.textContent)).toContain("Apexmed");

    for (const label of [/Người phụ trách/, /Chọn kênh/]) {
      expect(screen.getByRole("combobox", { name: label })).toHaveAttribute("data-select", "pr");
    }

    // Distribution mode appears once a policy-grounded channel is chosen, which
    // is the only time it is a real question.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Chọn kênh/ }),
      CHANNELS[0].id,
    );
    const mode = await screen.findByRole("combobox", {
      name: `Hình thức đăng cho ${CHANNELS[0].name}`,
    });
    expect(mode).toHaveAttribute("data-select", "pr");
    expect([...mode.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Chưa xác định",
      "Organic",
      "Quảng cáo trả phí",
    ]);
  });

  it("marks the platform, category and status pickers on the channels admin", async () => {
    stubFetch([
      // The create controls are offered on a capability the server reports, so
      // the dashboard has to say this session may manage channels.
      {
        match: "/api/pr/dashboard",
        body: {
          stage_counts: [],
          awaiting_my_review: [],
          overdue_tasks: [],
          my_capabilities: ["PR_CHANNEL_MANAGE"],
          recent_content: [],
        },
      },
      { match: "/api/pr/platforms", body: PLATFORMS },
      { match: "/api/pr/brands", body: [BRAND] },
      { match: "/api/pr/people", body: PEOPLE },
      { match: "/api/pr/channels", body: CHANNELS },
    ]);
    renderWithQuery(<ChannelsPage />);
    await waitFor(() => expect(screen.getByText("Apexmed TikTok")).toBeInTheDocument());

    await userEvent.click(await screen.findByRole("button", { name: "+ Tạo kênh" }));
    const pickers = screen.queryAllByRole("combobox");
    // Guarded, or the loop below passes on a page that rendered no picker at all.
    expect(pickers.length).toBeGreaterThanOrEqual(3);
    for (const select of pickers) {
      expect(select).toHaveAttribute("data-select", "pr");
    }
    // And the readable names are the option text, on every one of them.
    const all = pickers.flatMap((select) =>
      [...select.querySelectorAll("option")].map((o) => o.textContent ?? ""),
    );
    expect(all.join(" ")).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/,
    );
  });

  it("gives the shared component a themed field and a marker", () => {
    const source = read("components/pr.tsx");
    const select = source.slice(source.indexOf("export function Select"));
    expect(select).toContain('data-select="pr"');
    // The closed field still follows the page, dark-on-light or light-on-dark.
    expect(select).toContain("text-[var(--text)]");
    expect(select).toContain("bg-[var(--surface)]");
  });
});


// ===========================================================================
// KỲ BÁO CÁO: ONE GLOBAL REPORTING MONTH. Step 1F.2.3f.6.
//
// The month used to be a selector over "Hoàn tất" alone, and reached the cards
// but not the counts. It is now one control over the whole board: every group,
// every lane, every count and every card is read against it, each lane by its
// own business fact - which is the server's table, not this page's. The page
// sends a month (or CURRENT) and renders what comes back, month included.
// ===========================================================================

/** A card the completed board actually holds. The IDEA fixture is not one. */
const PUBLISHED_ITEM = {
  ...CONTENT,
  workflow_stage: "PUBLISHED",
  title: "Chăm sóc sau sinh",
  published_at: "2026-09-06T03:00:00Z",
};

const picker = () => screen.getByRole("combobox", { name: "Kỳ báo cáo" });

describe("79. one reporting month over the whole board", () => {
  it("50-51. is offered once, on every group, and the completed-only selector is gone", async () => {
    for (const group of ["PREPARATION", "EDITORIAL_REVIEW", "PRODUCTION", "COMPLETED", "CANCELLED"]) {
      SEARCH.value = new URLSearchParams({ group });
      const stub = stubFetch(routes(board([IDEA_ITEM])));
      const view = renderWithQuery(<ContentBoardPage />);
      await screen.findByRole("tablist", { name: "Nhóm quy trình" });
      await waitFor(() => expect(boardRequests(stub).length).toBeGreaterThan(0));

      expect(screen.getAllByRole("combobox", { name: "Kỳ báo cáo" })).toHaveLength(1);
      expect(screen.queryByRole("combobox", { name: "Kỳ công việc" })).toBeNull();
      expect(screen.queryByText("Kỳ công việc")).toBeNull();
      // Step 1F.2.3f.6d. No month chosen, so no month sent - by every request.
      expect(boardRequests(stub).every((one) => one.get("period") === null)).toBe(true);
      expect(screen.getAllByRole("combobox", { name: "Kỳ báo cáo" })[0]).toHaveValue("");
      view.unmount();
    }
  });

  it("1-5. opens on Tất cả kỳ, sends no month, and writes none into the URL", async () => {
    // Step 1F.2.3f.6d. The default board is every active row: no `period` in
    // the request, none written to the URL, "Tất cả kỳ" on the selector - and
    // the current month still known from the server for the option list.
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(boardRequests(stub).every((one) => one.get("period") === null)).toBe(true);
    expect(boardRequests(stub).some((one) => one.get("period") === "CURRENT")).toBe(false);
    expect(picker()).toHaveValue("");
    expect(picker()).toBeEnabled();
    expect(within(picker()).getByRole("option", { name: "Tất cả kỳ" })).toBeInTheDocument();
    expect(SEARCH.value.has("period")).toBe(false);
    expect(replaced).toEqual([]);
    expect(screen.getByRole("tab", { name: "Tất cả" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/Mọi nội dung đang hoạt động/)).toBeInTheDocument();
    // The page's own source never seeds a month from the clock, and never
    // asks the server to substitute one.
    const source = readFileSync(
      path.join(process.cwd(), "src/app/pr/content/page.tsx"),
      "utf8",
    );
    expect(source).not.toContain("currentMonth()");
    expect(source).not.toContain('"CURRENT"');
  });

  it("11-13. an explicit month applies, and Tất cả kỳ removes only the month", async () => {
    SEARCH.value = new URLSearchParams({ group: "PRODUCTION", scope: "ALL" });
    const stub = stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toBeEnabled());
    await waitFor(() => expect(within(picker()).getAllByRole("option").length).toBeGreaterThan(1));

    stubFetch(routes(boardFor("2026-09", [IDEA_ITEM])));
    await userEvent.selectOptions(picker(), "2026-09");
    await waitFor(() => expect(SEARCH.value.get("period")).toBe("2026-09"));
    await waitFor(() => expect(boardRequests(stub).at(-1)?.get("period") ?? "2026-09").toBe("2026-09"));
    await waitFor(() => expect(screen.getByText(/mốc nghiệp vụ của bước đó/)).toBeInTheDocument());

    stubFetch(routes(board([IDEA_ITEM])));
    await userEvent.selectOptions(picker(), "");
    await waitFor(() => expect(SEARCH.value.has("period")).toBe(false));
    expect(SEARCH.value.get("group")).toBe("PRODUCTION");
    expect(SEARCH.value.get("scope")).toBe("ALL");
    await waitFor(() => expect(picker()).toHaveValue(""));
  });

  it("puts the chosen month in the URL and in every query", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(within(picker()).getAllByRole("option").length).toBeGreaterThan(2));

    // Options: "Tất cả kỳ", then the current month, then the one before it.
    const earlier = [...picker().querySelectorAll("option")][2] as HTMLOptionElement;
    await userEvent.selectOptions(picker(), earlier.value);

    await waitFor(() => expect(SEARCH.value.get("period")).toBe(earlier.value));
    // 53-54: the figures request *and* the lane requests are re-issued for the
    // new month, so the tab counts, the lane headers and the cards all move.
    await waitFor(() => {
      const after = boardRequests(stub).filter((one) => one.get("period") === earlier.value);
      expect(after.some((one) => one.get("limit") === "0")).toBe(true);
      expect(after.some((one) => one.get("lane") !== null)).toBe(true);
    });
  });

  it("52. survives a change of group", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await screen.findByRole("tablist", { name: "Nhóm quy trình" });

    await userEvent.click(screen.getByRole("tab", { name: /Sản xuất/ }));
    await waitFor(() => expect(SEARCH.value.get("group")).toBe("PRODUCTION"));
    expect(SEARCH.value.get("period")).toBe("2026-08");
    await waitFor(() => {
      const last = boardRequests(stub).at(-1)!;
      expect(last.get("group")).toBe("PRODUCTION");
      expect(last.get("period")).toBe("2026-08");
    });
  });

  it("restores a shared month from the URL", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    const stub = stubFetch(routes({ ...board([PUBLISHED_ITEM]), period: "2026-08" }));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(picker()).toHaveValue("2026-08");
    expect(boardRequests(stub)[0].get("period")).toBe("2026-08");
    // A month outside the last twelve is still selectable rather than silently
    // snapping to something else - a shared link has to reproduce its own view.
    expect(
      [...picker().querySelectorAll("option")].map((one) => (one as HTMLOptionElement).value),
    ).toContain("2026-08");
  });

  it("57. keeps every other filter when the month changes, and the month when a filter changes", async () => {
    SEARCH.value = new URLSearchParams({
      group: "COMPLETED",
      scope: "ALL",
      platform: PLATFORMS[0].id,
      period: "2026-09",
    });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.selectOptions(picker(), "2026-08");
    await waitFor(() => {
      const request = boardRequests(stub).at(-1)!;
      expect(request.get("period")).toBe("2026-08");
      expect(request.get("scope")).toBe("ALL");
      expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
    });

    // An ordinary date window narrows inside the month; it does not replace it.
    const preset = DATE_PRESETS.find((one) => one.key !== "ALL" && one.key !== "CUSTOM")!;
    await userEvent.selectOptions(screen.getByLabelText("Lọc theo ngày"), preset.key);
    await waitFor(() => {
      const request = boardRequests(stub).at(-1)!;
      expect(request.get("date_from")).not.toBeNull();
      expect(request.get("period")).toBe("2026-08");
    });
  });

  it("is cleared by “Xóa bộ lọc” like any other filter, while the group stays", async () => {
    // Step 1F.2.3f.6d. The month is optional now, so "Xóa bộ lọc" returns to
    // the page as it opens: Tất cả kỳ, every active row, the open group kept.
    SEARCH.value = new URLSearchParams({
      group: "COMPLETED",
      scope: "ALL",
      platform: PLATFORMS[0].id,
      period: "2026-08",
    });
    stubFetch(routes(boardFor("2026-08", [PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Xóa bộ lọc" }));
    const after = new URLSearchParams(replaced.at(-1)!.split("?")[1] ?? "");
    expect(after.has("period")).toBe(false);
    expect(after.get("group")).toBe("COMPLETED");
    expect(after.get("platform")).toBeNull();
    expect(after.get("scope")).toBeNull();
  });

  it("offers “Xóa bộ lọc” when only a month is chosen", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    stubFetch(routes(boardFor("2026-08", [PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Xóa bộ lọc" })).toBeEnabled();
  });

  it("55. labels a lane with the figure the same month's query produced", async () => {
    // The header number is the server's `stage_counts` for the month, and the
    // lane's rows come from a request carrying that same month - one predicate,
    // asked twice. The page adds nothing up itself.
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-09" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM, { ...PUBLISHED_ITEM, id: "b" }], "ALL", 2)));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getAllByText("Chăm sóc sau sinh").length).toBeGreaterThan(0));

    const header = screen.getByRole("heading", { name: /Đã đăng/ });
    expect(header).toHaveTextContent("2");
    const laneRequest = boardRequests(stub).find((one) => one.get("lane") === "PUBLISHED")!;
    const figures = boardRequests(stub).find((one) => one.get("limit") === "0")!;
    expect(laneRequest.get("period")).toBe(figures.get("period"));
  });

  it("56. is reachable on a phone: named, native, and never hidden by width", async () => {
    SEARCH.value = new URLSearchParams({ group: "PREPARATION" });
    stubFetch(routes(board([IDEA_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await screen.findByRole("tablist", { name: "Nhóm quy trình" });

    const control = picker();
    expect(control.tagName).toBe("SELECT");
    expect(control).toHaveAccessibleName("Kỳ báo cáo");
    const region = screen.getByRole("region", { name: "Kỳ báo cáo" });
    expect(region.className).not.toMatch(/\bhidden\b/);
    expect(region.className).toContain("flex-wrap");
    // The visible label reads as the dashboard's scope, not as a filter.
    expect(within(region).getByText("Kỳ báo cáo")).toBeInTheDocument();
  });
});

describe("82. the action queue keeps the month but does not read it", () => {
  const GATED_BOARD = (scope: string) => board([GATED_ITEM], scope);

  it("11. keeps the selected month in the URL when switching to Cần tôi xử lý", async () => {
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "ALL", period: "2026-08" });
    const stub = stubFetch(routes(GATED_BOARD("ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toBeEnabled());

    stubFetch(routes(GATED_BOARD("MY_ACTIONS")));
    await userEvent.click(screen.getByRole("tab", { name: "Cần tôi xử lý" }));
    await waitFor(() => expect(SEARCH.value.get("scope")).toBe("MY_ACTIONS"));
    expect(SEARCH.value.get("period")).toBe("2026-08");
    // The month still travels with the request - the server decides not to
    // read it, and says so. The browser never drops it itself.
    const last = boardRequests(stub).at(-1) ?? new URLSearchParams();
    expect(last.get("period") ?? "2026-08").toBe("2026-08");
  });

  it("13. disables the selector and says the month is not in force", async () => {
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "MY_ACTIONS", period: "2026-08" });
    stubFetch(routes({ ...GATED_BOARD("MY_ACTIONS"), period: "2026-08" }));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Bài chờ duyệt")).toBeInTheDocument());

    expect(picker()).toBeDisabled();
    expect(picker()).toHaveValue("2026-08");
    expect(screen.getByRole("note")).toHaveTextContent("Không áp dụng cho “Cần tôi xử lý”");
    // The cards and the counts are the server's month-free queue; the page
    // hides nothing it was handed.
    expect(screen.getByRole("heading", { name: /Chờ duyệt Trưởng nhóm/ })).toHaveTextContent("1");
  });

  it("follows the server's resolved scope, not the tab it asked for", async () => {
    // A server that resolves the scope to ALL keeps the selector live even
    // when the URL asked for the queue - the page reads the echo.
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "MY_ACTIONS", period: "2026-08" });
    stubFetch(routes(boardFor("2026-08", [GATED_ITEM], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Bài chờ duyệt")).toBeInTheDocument());
    await waitFor(() => expect(picker()).toBeEnabled());
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("21 and 25. with no month chosen, the queue is still marked month-free and returns to Tất cả kỳ", async () => {
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "ALL" });
    const stub = stubFetch(routes(board([GATED_ITEM], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toBeEnabled());

    stubFetch(routes(board([GATED_ITEM], "MY_ACTIONS")));
    await userEvent.click(screen.getByRole("tab", { name: "Cần tôi xử lý" }));
    await waitFor(() => expect(picker()).toBeDisabled());
    expect(picker()).toHaveValue("");
    expect(screen.getByRole("note")).toHaveTextContent("Không áp dụng cho “Cần tôi xử lý”");
    expect(SEARCH.value.has("period")).toBe(false);

    stubFetch(routes(board([GATED_ITEM], "ALL")));
    await userEvent.click(screen.getByRole("tab", { name: "Tất cả" }));
    await waitFor(() => expect(picker()).toBeEnabled());
    expect(picker()).toHaveValue("");
    expect(SEARCH.value.has("period")).toBe(false);
    expect(boardRequests(stub).every((one) => one.get("period") === null)).toBe(true);
  });

  it("12. re-enables the selector and re-applies the kept month on the way back", async () => {
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "MY_ACTIONS", period: "2026-08" });
    stubFetch(routes({ ...GATED_BOARD("MY_ACTIONS"), period: "2026-08" }));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toBeDisabled());

    const stub = stubFetch(routes({ ...GATED_BOARD("ALL"), period: "2026-08" }));
    await userEvent.click(screen.getByRole("tab", { name: "Tất cả" }));
    await waitFor(() => expect(picker()).toBeEnabled());
    expect(picker()).toHaveValue("2026-08");
    expect(SEARCH.value.get("period")).toBe("2026-08");
    await waitFor(() => {
      const last = boardRequests(stub).at(-1)!;
      expect(last.get("scope")).toBe("ALL");
      expect(last.get("period")).toBe("2026-08");
    });
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("83. the month list is anchored on the current business month", () => {
  const options = () =>
    [...picker().querySelectorAll("option")].map((one) => (one as HTMLOptionElement).value);

  it("7-8. runs from the server's current month backwards, never from the selected one", () => {
    // The helper, on its own: the anchor is the first argument, and the
    // selected month only ever *extends* the list downwards.
    expect(periodOptionsFor("2026-09", "2026-09").slice(0, 3)).toEqual([
      "2026-09",
      "2026-08",
      "2026-07",
    ]);
    expect(periodOptionsFor("2026-09", "2026-08")[0]).toBe("2026-09");
    expect(periodOptionsFor("2026-09", "2026-08")).toContain("2026-08");
    const deep = periodOptionsFor("2026-09", "2026-03");
    expect(deep[0]).toBe("2026-09");
    expect(deep).toContain("2026-03");
    const far = periodOptionsFor("2026-09", "2024-02");
    expect(far[0]).toBe("2026-09");
    expect(far.at(-1)).toBe("2024-02");
    // A future deep link is still offered, above the run.
    expect(periodOptionsFor("2026-09", "2026-11")[0]).toBe("2026-11");
    // No anchor yet: only the URL's month, and nothing from the clock.
    expect(periodOptionsFor(undefined, "2026-03")).toEqual(["2026-03"]);
    expect(periodOptionsFor(undefined, undefined)).toEqual([]);
    const source = readFileSync(
      path.join(process.cwd(), "src/app/pr/content/page.tsx"),
      "utf8",
    );
    expect(source).not.toMatch(/new Date\(\)/);
    expect(source).not.toContain("recentMonths(");
  });

  it("1 and 16-17. lists Tất cả kỳ first, then the current month downwards", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-09" });
    stubFetch(routes(boardFor("2026-09", [PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toHaveValue("2026-09"));
    await waitFor(() => expect(options().slice(0, 4)).toEqual(["", "2026-09", "2026-08", "2026-07"]));
    // And with nothing chosen the same list stands under "Tất cả kỳ".
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
  });

  it("2 and 4-5. keeps the current month after August is chosen, and goes straight back", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    const stub = stubFetch(routes(boardFor("2026-08", [PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toHaveValue("2026-08"));
    // Until the server answers only the URL's month is known; once it has,
    // the list is anchored on the current month it reported - after the
    // "Tất cả kỳ" entry, which is not a month.
    await waitFor(() => expect(options()[1]).toBe("2026-09"));
    expect(options()[0]).toBe("");
    expect(options()[2]).toBe("2026-08");
    // Straight back to the current month from the same control - no URL edit,
    // no reload, no Back button, no scope change.
    await userEvent.selectOptions(picker(), "2026-09");
    await waitFor(() => expect(SEARCH.value.get("period")).toBe("2026-09"));
    await waitFor(() => expect(boardRequests(stub).at(-1)!.get("period")).toBe("2026-09"));
  });

  it("3. keeps every month up to the current one for a historical deep link", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-03" });
    stubFetch(routes(boardFor("2026-03", [PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toHaveValue("2026-03"));
    await waitFor(() => expect(options()[1]).toBe("2026-09"));

    const listed = options().slice(1);
    expect(listed[0]).toBe("2026-09");
    expect(listed.slice(0, 7)).toEqual([
      "2026-09",
      "2026-08",
      "2026-07",
      "2026-06",
      "2026-05",
      "2026-04",
      "2026-03",
    ]);
    expect(picker()).toHaveValue("2026-03");
  });

  it("6. survives a round trip through Cần tôi xử lý with the current month still offered", async () => {
    SEARCH.value = new URLSearchParams({ group: "EDITORIAL_REVIEW", scope: "ALL", period: "2026-08" });
    stubFetch(routes(boardFor("2026-08", [GATED_ITEM], "ALL")));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(picker()).toBeEnabled());
    await waitFor(() => expect(options()[1]).toBe("2026-09"));

    stubFetch(routes(boardFor("2026-08", [GATED_ITEM], "MY_ACTIONS")));
    await userEvent.click(screen.getByRole("tab", { name: "Cần tôi xử lý" }));
    await waitFor(() => expect(picker()).toBeDisabled());
    // Disabled, but still correct underneath.
    expect(picker()).toHaveValue("2026-08");
    expect(options()[1]).toBe("2026-09");

    stubFetch(routes(boardFor("2026-08", [GATED_ITEM], "ALL")));
    await userEvent.click(screen.getByRole("tab", { name: "Tất cả" }));
    await waitFor(() => expect(picker()).toBeEnabled());
    expect(picker()).toHaveValue("2026-08");
    expect(options()[1]).toBe("2026-09");
    expect(options()).toContain("2026-08");
  });
});

describe("80. a card carries its publication day and no archive badge", () => {
  const published = (over: Record<string, unknown>) => ({
    ...CONTENT,
    workflow_stage: "PUBLISHED",
    title: "CHIBI tháng trước",
    published_at: "2026-08-18T03:00:00Z",
    ...over,
  });

  it("prints the publication date on a published card", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    stubFetch(routes(board([published({})])));
    renderWithQuery(<ContentBoardPage />);

    expect(await screen.findByText(/^Thực tế đăng /)).toBeInTheDocument();
    expect(screen.queryByText("Đã lưu trữ")).toBeNull();
    expect(screen.queryByText(/^Đã đăng · /)).toBeNull();
  });

  it("renders an archived card in the archive view with nothing to explain", async () => {
    SEARCH.value = new URLSearchParams({ view: "archive", period: "2026-09" });
    stubFetch(
      routes({
        ...board([published({ workflow_stage: "ARCHIVED", published_at: null })]),
        view: "ARCHIVE",
      }),
    );
    renderWithQuery(<ContentBoardPage />);

    expect(await screen.findByText("CHIBI tháng trước")).toBeInTheDocument();
    // No "kind of archive" to tell apart any more: the view says it all.
    expect(screen.queryByText("Đã lưu trữ")).toBeNull();
    const source = readFileSync(path.join(process.cwd(), "src/components/pr.tsx"), "utf8");
    expect(source).not.toContain("OLDER_PUBLISHED");
    expect(source).not.toContain("archive_reason");
  });
});

describe("84. the archive is off the operational board and fetched only on demand", () => {
  const ARCHIVED_ITEM = {
    ...CONTENT,
    id: "77777777-7777-7777-7777-777777777777",
    code: "CNT-2026-000077",
    workflow_stage: "ARCHIVED",
    title: "Bài đã lưu trữ",
    published_at: "2026-08-18T03:00:00Z",
  };
  const archiveCalls = (stub: ReturnType<typeof stubFetch>) =>
    (stub as unknown as { calls: Array<{ url: string }> }).calls.filter(
      (call) => call.url.includes("view=ARCHIVE") || call.url.includes("lane=ARCHIVED"),
    );

  it("1-2. draws Hoàn tất as Sẵn sàng đăng and Đã đăng, with no Lưu trữ column", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM])));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: /Sẵn sàng đăng/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Đã đăng/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Lưu trữ/ })).toBeNull();
    // 6. Nothing archive-shaped was requested: no archive lane, no archive view.
    expect(archiveCalls(stub)).toEqual([]);
    expect(boardRequests(stub).every((one) => one.get("view") === null)).toBe(true);
    // The table agrees with the server's: two stages, two columns.
    const source = readFileSync(path.join(process.cwd(), "src/lib/board.ts"), "utf8");
    expect(source).toContain('stages: ["READY_TO_PUBLISH", "PUBLISHED"],');
  });

  it("4. counts Hoàn tất from its two lanes even if a response carried an archive figure", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
    const body = board([PUBLISHED_ITEM]);
    body.stage_counts = body.stage_counts.map((row) =>
      row.stage === "ARCHIVED" ? { ...row, count: 40 } : row,
    );
    stubFetch(routes(body));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /Hoàn tất/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /Hoàn tất/ })).not.toHaveTextContent("41");
  });

  it("offers the archive as a link and opens it as ?view=archive, keeping the month", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-08" });
    stubFetch(routes({ ...board([PUBLISHED_ITEM]), period: "2026-08" }));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());

    stubFetch(routes({ ...board([ARCHIVED_ITEM]), period: "2026-08", view: "ARCHIVE" }));
    await userEvent.click(screen.getByRole("button", { name: "Xem nội dung lưu trữ" }));
    await waitFor(() => expect(SEARCH.value.get("view")).toBe("archive"));
    expect(SEARCH.value.get("period")).toBe("2026-08");
  });

  it("opens on Tất cả kỳ from the default board, and keeps an explicit month", async () => {
    SEARCH.value = new URLSearchParams({ view: "archive" });
    const stub = stubFetch(routes({ ...board([ARCHIVED_ITEM]), view: "ARCHIVE" }));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Bài đã lưu trữ")).toBeInTheDocument());
    expect(picker()).toHaveValue("");
    expect(picker()).toBeEnabled();
    expect(screen.getByText("Mọi nội dung đã lưu trữ.")).toBeInTheDocument();
    expect(boardRequests(stub).every((one) => one.get("period") === null)).toBe(true);
    expect(SEARCH.value.has("period")).toBe(false);
  });

  it("8-10. the archive view asks for ARCHIVED alone, month by archived_at, filters intact", async () => {
    SEARCH.value = new URLSearchParams({
      view: "archive",
      period: "2026-08",
      platform: PLATFORMS[0].id,
    });
    const stub = stubFetch(
      routes({ ...boardFor("2026-08", [ARCHIVED_ITEM]), view: "ARCHIVE" }),
    );
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() => expect(screen.getByText("Bài đã lưu trữ")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "Nội dung lưu trữ" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "Nhóm quy trình" })).toBeNull();
    expect(screen.queryByRole("tablist", { name: "Phạm vi nội dung" })).toBeNull();
    expect(screen.getByText("Tính theo ngày lưu trữ.")).toBeInTheDocument();
    for (const request of boardRequests(stub)) {
      expect(request.get("view")).toBe("ARCHIVE");
      expect(request.get("period")).toBe("2026-08");
      expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
      expect(request.get("group")).toBeNull();
    }
    expect(boardRequests(stub).some((one) => one.get("lane") === "ARCHIVED")).toBe(true);
    expect(boardRequests(stub).every((one) => one.get("lane") !== "PUBLISHED")).toBe(true);
    // No archive control here: last month's output is archived from the board.
    expect(screen.queryByRole("button", { name: /Lưu trữ nội dung kỳ/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Quay lại bảng nội dung" })).toBeInTheDocument();
  });
});

describe("81. archiving last month's published output is a deliberate, confirmed action", () => {
  const CANDIDATES = {
    period: "2026-08",
    total: 3,
    content_ids: ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"],
    limit: 200,
    truncated: false,
    may_archive: true,
  };

  it("offers the previous month's count, from the server, in Hoàn tất only", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-09" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM]), CANDIDATES));
    renderWithQuery(<ContentBoardPage />);

    expect(
      await screen.findByRole("button", { name: "Lưu trữ nội dung kỳ 08/2026" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/vẫn đang ở “Đã đăng”/)).toHaveTextContent("3");
    const asked = (stub as unknown as { calls: Array<{ url: string }> }).calls.find((call) =>
      call.url.includes("/archive-candidates"),
    )!;
    expect(new URLSearchParams(asked.url.split("?")[1]).get("period")).toBe("2026-08");
  });

  it("32-33. with no month chosen, targets the month before the current business month", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED" });
    const stub = stubFetch(routes(board([PUBLISHED_ITEM]), CANDIDATES));
    renderWithQuery(<ContentBoardPage />);
    expect(
      await screen.findByRole("button", { name: "Lưu trữ nội dung kỳ 08/2026" }),
    ).toBeInTheDocument();
    const asked = (stub as unknown as { calls: Array<{ url: string }> }).calls.find((call) =>
      call.url.includes("/archive-candidates"),
    )!;
    expect(new URLSearchParams(asked.url.split("?")[1]).get("period")).toBe("2026-08");
  });

  it("35. with a closed month chosen, targets that month and says so", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-07" });
    const stub = stubFetch(
      routes(boardFor("2026-07", [PUBLISHED_ITEM]), { ...CANDIDATES, period: "2026-07" }),
    );
    renderWithQuery(<ContentBoardPage />);
    expect(
      await screen.findByRole("button", { name: "Lưu trữ nội dung kỳ 07/2026" }),
    ).toBeInTheDocument();
    const asked = (stub as unknown as { calls: Array<{ url: string }> }).calls.find((call) =>
      call.url.includes("/archive-candidates"),
    )!;
    expect(new URLSearchParams(asked.url.split("?")[1]).get("period")).toBe("2026-07");
  });

  it("confirms with the server's count and posts exactly the frozen ids", async () => {
    SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-09" });
    const stub = stubFetch([
      ...routes(board([PUBLISHED_ITEM]), CANDIDATES),
      {
        match: "/api/pr/contents/archive-batch",
        method: "POST",
        status: 201,
        body: {
          batch_id: "44444444-4444-4444-4444-444444444444",
          period: "2026-08",
          archived_count: 3,
          archived: [],
          requested_count: 3,
          duplicates_removed: 0,
        },
      },
    ]);
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Lưu trữ nội dung kỳ 08/2026" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Lưu trữ 3 nội dung đã đăng trong kỳ 08/2026?");
    // Nothing was written yet.
    const calls = (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;
    expect(calls.some((call) => call.method === "POST")).toBe(false);

    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu trữ 3 nội dung" }));
    await waitFor(() => {
      const posted = calls.find((call) => call.url.includes("/archive-batch"));
      expect(posted?.body).toEqual({ period: "2026-08", content_ids: CANDIDATES.content_ids });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Đã lưu trữ 3 nội dung");
  });

  it("is absent when there is nothing to archive or the session may not", async () => {
    for (const candidates of [
      { ...CANDIDATES, total: 0, content_ids: [] },
      { ...CANDIDATES, may_archive: false },
    ]) {
      SEARCH.value = new URLSearchParams({ group: "COMPLETED", period: "2026-09" });
      stubFetch(routes(board([PUBLISHED_ITEM]), candidates));
      const view = renderWithQuery(<ContentBoardPage />);
      await waitFor(() => expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument());
      expect(screen.queryByRole("button", { name: /Lưu trữ nội dung kỳ/ })).toBeNull();
      view.unmount();
    }
    // And never on an operational group: the work there is not finished.
    SEARCH.value = new URLSearchParams({ group: "PRODUCTION", period: "2026-09" });
    const stub = stubFetch(routes(board([IDEA_ITEM]), CANDIDATES));
    renderWithQuery(<ContentBoardPage />);
    await screen.findByRole("tablist", { name: "Nhóm quy trình" });
    expect(screen.queryByRole("button", { name: /Lưu trữ nội dung kỳ/ })).toBeNull();
    expect(
      (stub as unknown as { calls: Array<{ url: string }> }).calls.some((call) =>
        call.url.includes("/archive-candidates"),
      ),
    ).toBe(false);
  });
});
