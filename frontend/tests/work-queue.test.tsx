/**
 * Step 1F.2.3c - the content page as a work queue, and where internal review sits.
 *
 * Numbered 81-95, continuing the Step 1F.2.2 numbering in
 * `tests/content-views.test.tsx`. 93-95 are Step 1F.2.3c1, which made the
 * lifecycle group a **query parameter**: grouping the page after it arrived put
 * a five-item "Chuẩn bị" one card on page 1 and four on page 3, under a tab that
 * said 5. Those three sections are about what is *requested* and what the pager
 * says, which is where that bug lived and where it can come back.
 *
 * Two kinds of test, deliberately separated.
 *
 * **The grouping is a pure table**, so most of the mapping assertions (85-87)
 * run against `lib/board.ts` directly with no DOM at all. That is the honest
 * shape: "INTERNAL_REVIEW is not in Chờ duyệt" is a fact about one data
 * structure, and asserting it through a rendered page would make it fail for
 * fifteen unrelated reasons.
 *
 * **The page is a layout**, so the region and column tests render it. What they
 * assert is structure - which landmark contains which control, in what order -
 * and never a class string: a test that pins Tailwind classes passes on a page
 * that renders nothing.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import { STAGE_ORDER, stageLabel } from "@/lib/labels";
import {
  LANE_PAGE_SIZE,
  OPERATIONAL_GROUPS,
  boardCounts,
  columnLabel,
  contentOperationalGroup,
  contentOperationalState,
  groupCount,
  groupOfStage,
} from "@/lib/board";
import type { ContentSummary } from "@/lib/api";
import { CONTENT, renderWithQuery, SESSION, settleLanes, stubFetch, urlStore } from "./helpers";

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

/**
 * The address bar, and a router that moves it.
 *
 * `replace` records the URL *and* navigates to it, because since Step 1F.2.3c1
 * the lifecycle group is a query parameter: clicking a tab writes the URL, the
 * page re-reads it, and a new request goes out for that group. A mock that only
 * recorded would leave every tab click a no-op and the tests below asserting a
 * board that never changed.
 */
const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const replaced: string[] = [];

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

const { default: ContentBoardPage } = await import("@/app/pr/content/page");

const PRODUCER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

/** One card, at a stage, with the production state the server would derive. */
function item(
  id: string,
  title: string,
  workflow_stage: string,
  extra: Partial<ContentSummary> = {},
): ContentSummary {
  return {
    ...(CONTENT as unknown as ContentSummary),
    id,
    code: `CNT-2026-${id.slice(0, 6)}`,
    title,
    workflow_stage,
    producer_user_id: null,
    production_state: null,
    ...extra,
  };
}

const IDEA_ITEM = item("111111", "Ý tưởng mới", "IDEA");
const LEAD_ITEM = item("222222", "Chờ trưởng nhóm", "TEAM_LEAD_REVIEW");
const HEAD_ITEM = item("333333", "Chờ trưởng phòng", "HEAD_REVIEW");
const UNCLAIMED = item("444444", "Chưa ai nhận", "APPROVED", {
  production_state: "WAITING_FOR_PRODUCER",
});
const CLAIMED = item("555555", "Đã có người nhận", "APPROVED", {
  producer_user_id: PRODUCER,
  production_state: "READY_FOR_PRODUCTION",
});
const PRODUCING = item("666666", "Đang dựng", "PRODUCTION", {
  producer_user_id: PRODUCER,
  production_state: "IN_PRODUCTION",
});
const INTERNAL = item("777777", "Bản dựng chờ duyệt nội bộ", "INTERNAL_REVIEW", {
  producer_user_id: PRODUCER,
  production_state: "IN_INTERNAL_REVIEW",
});
const PUBLISHED_ITEM = item("888888", "Đã lên sóng", "PUBLISHED");
const CANCELLED_ITEM = item("999999", "Bỏ giữa chừng", "CANCELLED");

const PRODUCTION_ITEMS = [UNCLAIMED, CLAIMED, PRODUCING, INTERNAL];

const BRAND = { id: CONTENT.brand_id, code: "APEXMED", name: "Apexmed" };
const PLATFORMS = [
  { id: "dddddddd-dddd-dddd-dddd-dddddddddddd", code: "TIKTOK", name: "TikTok", status: "ACTIVE", policy_grounded: true },
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
const PEOPLE = [{ user_id: SESSION.user_id, full_name: "Lê Trưởng Nhóm", role: "TEAM_LEAD" }];

/**
 * The board envelope, counted the way the server counts it.
 *
 * Both count tables are derived from `counted` rather than from `items`, so a
 * test can hand the page one page of a larger set - which is what pagination
 * looks like, and what a page counting its own cards gets wrong.
 */
function board(
  items: ContentSummary[],
  options: { scope?: string; counted?: ContentSummary[]; total?: number } = {},
) {
  const { scope = "ALL", counted = items, total = counted.length } = options;
  return {
    items,
    total,
    scope,
    stage_counts: STAGE_ORDER.map((stage) => ({
      stage,
      count: counted.filter((row) => row.workflow_stage === stage).length,
    })),
    production_state_counts: [
      "WAITING_FOR_PRODUCER",
      "READY_FOR_PRODUCTION",
      "IN_PRODUCTION",
      "IN_INTERNAL_REVIEW",
    ].map((production_state) => ({
      production_state,
      count: counted.filter((row) => row.production_state === production_state).length,
    })),
    limit: 60,
    offset: 0,
  };
}

const routes = (body: unknown) => [
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/api/pr/platforms", body: PLATFORMS },
  { match: "/api/pr/channels", body: CHANNELS },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/contents/board", body },
];

/** Render the board and wait for it, returning the fetch stub for the requests. */
async function openBoard(items: ContentSummary[], options?: Parameters<typeof board>[1]) {
  const stub = stubFetch(routes(board(items, options)));
  renderWithQuery(<ContentBoardPage />);
  // The board region appears only once the response is in, which is what every
  // assertion below is about - waiting for the strip would let a test read the
  // page mid-flight and pass for the wrong reason.
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument(),
  );
  // Step 1F.2.3c2: the cards arrive per lane, after the figures do.
  await settleLanes();
  return stub;
}

/** The group tab, by its Vietnamese label. */
const groupTab = (label: string) => screen.getByRole("tab", { name: new RegExp(`^${label}`) });

beforeEach(() => {
  SEARCH.value = new URLSearchParams({ scope: "ALL" });
  replaced.length = 0;
});

// ===========================================================================
// 81-82: THE DASHBOARD CAME OFF THE WORK QUEUE
// ===========================================================================

describe("81. the four KPI cards are gone from the content page", () => {
  it("renders none of the four summary tiles", async () => {
    await openBoard([IDEA_ITEM, LEAD_ITEM, PUBLISHED_ITEM]);

    // The tile labels. "Chờ duyệt" is *also* a lifecycle tab, so the tabs are
    // ignored rather than the label being dropped from the list: the claim is
    // that no *tile* carries these words, and a test that simply skipped the
    // awkward one would not have noticed a summary card reappearing with it.
    for (const label of ["Đang xử lý", "Chờ duyệt", "Sẵn sàng đăng", "Đã đăng"]) {
      expect(
        screen.queryByText(label, { ignore: "[role='tab'], script, style" }),
        label,
      ).not.toBeInTheDocument();
    }
    // Structural, not textual: the tiles were the one place on this page with a
    // big standalone figure, and this is what their markup was.
    expect(document.querySelectorAll("p.text-xl.tabular-nums")).toHaveLength(0);
  });

  it("keeps 'Chờ duyệt' as a lifecycle tab, which is a different thing", async () => {
    await openBoard([IDEA_ITEM, LEAD_ITEM]);

    // The label survives - as navigation, inside the group strip, with a count
    // that describes the queue. That is the distinction the KPI test above must
    // not accidentally forbid.
    const strip = screen.getByRole("tablist", { name: "Nhóm quy trình" });
    expect(within(strip).getByRole("tab", { name: /Chờ duyệt/ })).toBeInTheDocument();
  });

  it("puts the filters immediately after the scope strip, with nothing between", async () => {
    await openBoard([IDEA_ITEM]);

    // The tile row used to sit exactly here, pushing the filters and the first
    // row of cards below the fold. Asserting adjacency is what keeps a future
    // summary widget from quietly reappearing in the same place.
    const scope = screen.getByRole("tablist", { name: "Phạm vi nội dung" });
    expect(scope.nextElementSibling).toBe(screen.getByRole("region", { name: "Bộ lọc" }));
  });

  it("removes the wiring as well as the markup", () => {
    const source = read("app/pr/content/page.tsx");
    for (const dead of ["SUMMARY_BUCKETS", "StatTile"]) {
      expect(source, dead).not.toContain(dead);
    }
  });

  it("leaves the reporting figures where they belong", () => {
    // Step 1F.2.3c moved these off the work queue; it did not delete them. The
    // landing page is a report, and "Đã đăng" is what somebody goes there for.
    const overview = read("app/pr/page.tsx");
    expect(overview).toContain("SUMMARY_BUCKETS");
    expect(read("lib/labels.ts")).toContain("SUMMARY_BUCKETS");
  });
});

describe("82. no second reporting surface grew back", () => {
  it("shows no totals, throughput or charts on the work queue", () => {
    const source = read("app/pr/content/page.tsx");
    for (const dead of ["Tổng cộng", "throughput", "Chart", "<canvas", "recharts"]) {
      expect(source, dead).not.toContain(dead);
    }
    // The counts that *are* on the page are the queue's own, from the same
    // request as the cards - never a second call for figures.
    expect(source).not.toContain("api.dashboard");
    expect(source).toContain("api.contentBoard");
  });
});

// ===========================================================================
// 83-84: THE FOUR REGIONS
// ===========================================================================

describe("83. the page is four separated regions", () => {
  it("renders scope, filters, lifecycle groups and the board", async () => {
    await openBoard([IDEA_ITEM]);

    expect(screen.getByRole("tablist", { name: "Phạm vi nội dung" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Bộ lọc" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Nhóm quy trình" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument();
  });

  it("keeps the filters and the cards in different containers", async () => {
    await openBoard([IDEA_ITEM]);

    const filters = screen.getByRole("region", { name: "Bộ lọc" });
    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    const card = screen.getByText("Ý tưởng mới").closest("a")!;

    // The complaint this patch is answering: filters floating directly above the
    // cards, so the first row of a busy board read as part of the filter bar.
    expect(filters.contains(card)).toBe(false);
    expect(boardRegion.contains(card)).toBe(true);
    expect(within(filters).getByRole("searchbox")).toBeInTheDocument();
    expect(within(boardRegion).queryByRole("combobox")).not.toBeInTheDocument();
    expect(filters.contains(boardRegion)).toBe(false);
  });

  it("puts the lifecycle strip between the filters and the board", async () => {
    await openBoard([IDEA_ITEM]);

    const nodes = [
      screen.getByRole("tablist", { name: "Phạm vi nội dung" }),
      screen.getByRole("region", { name: "Bộ lọc" }),
      screen.getByRole("tablist", { name: "Nhóm quy trình" }),
      screen.getByRole("region", { name: "Bảng nội dung" }),
    ];
    for (const [index, node] of nodes.slice(0, -1).entries()) {
      expect(
        node.compareDocumentPosition(nodes[index + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
        String(index),
      ).toBeTruthy();
    }
  });
});

describe("84. every filter is still there, and still asked of the server", () => {
  it("holds the whole filter bar inside the panel", async () => {
    // No scope in the URL either, so nothing at all is filtered - which is the
    // state the reset button is subdued in.
    SEARCH.value = new URLSearchParams();
    await openBoard([IDEA_ITEM]);
    const filters = screen.getByRole("region", { name: "Bộ lọc" });

    expect(within(filters).getByRole("searchbox")).toBeInTheDocument();
    for (const label of [
      "Lọc theo ngày",
      "Lọc theo nền tảng",
      "Lọc theo kênh",
      "Lọc theo người phụ trách",
      "Lọc theo bước",
    ]) {
      expect(within(filters).getByRole("combobox", { name: label }), label).toBeInTheDocument();
    }
    // Subdued rather than absent when nothing is filtered, so the row does not
    // reflow the moment somebody picks a platform.
    expect(within(filters).getByRole("button", { name: "Xóa bộ lọc" })).toBeDisabled();
  });

  it("offers the two date boxes and the dimension picker inside the panel", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", date: "CUSTOM", from: "2026-08-01" });
    await openBoard([IDEA_ITEM]);

    const filters = screen.getByRole("region", { name: "Bộ lọc" });
    expect(within(filters).getByRole("combobox", { name: "Mốc ngày" })).toBeInTheDocument();
    expect(within(filters).getByLabelText("Từ ngày")).toHaveValue("2026-08-01");
    expect(within(filters).getByLabelText("Đến ngày")).toBeInTheDocument();
  });

  it("still writes a chosen filter into the URL", async () => {
    await openBoard([IDEA_ITEM]);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo nền tảng" }),
      PLATFORMS[0].id,
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    const written = new URLSearchParams(replaced.at(-1)!.split("?")[1]);
    expect(written.get("platform")).toBe(PLATFORMS[0].id);
    // The scope in the URL is untouched by a filter change, which is what makes
    // "Xóa bộ lọc" and a reload land on the same view.
    expect(written.get("scope")).toBe("ALL");
  });

  it("clears back to the page as it opens, leaving the scope to the server", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    await openBoard([IDEA_ITEM]);

    const reset = screen.getByRole("button", { name: "Xóa bộ lọc" });
    expect(reset).toBeEnabled();
    await userEvent.click(reset);
    expect(replaced.at(-1)).toBe("/pr/content");
  });

  it("narrows nothing in the browser", () => {
    const source = read("app/pr/content/page.tsx");
    // Step 1F.2.3c2. Which rows exist in a column is `?lane=`, in SQL, before
    // the page is cut. The one client-side pass left is inside a lane and drops
    // nothing - the server was asked for this lane and returned it - so it is a
    // guard against a drifted column table rather than a filter.
    expect(source).toContain("columnHolds");
    expect(source).toContain("Defensive only");
    expect(source).toContain("lane, limit: LANE_PAGE_SIZE, offset: pageParam");
  });
});

// ===========================================================================
// 85-87: THE GROUPING ITSELF
// ===========================================================================

describe("85. every stage is in exactly one group", () => {
  it("partitions the whole stage list", () => {
    const grouped = OPERATIONAL_GROUPS.flatMap((group) => group.stages);
    // Step 1F.2.3f.6c. Every *operational* stage: ARCHIVED is the archive
    // view's, off the board by design, and the server's group table agrees.
    expect([...grouped].sort()).toEqual(
      STAGE_ORDER.filter((stage) => stage !== "ARCHIVED").sort(),
    );
    // Exactly one: a stage in two groups would be counted twice by two tabs.
    expect(new Set(grouped).size).toBe(grouped.length);
    expect(groupOfStage("ARCHIVED")).toBeUndefined();
  });

  it("maps the preparation stages to Chuẩn bị", () => {
    for (const stage of ["IDEA", "BRIEFING", "SCRIPTING", "AI_REVIEW"]) {
      expect(groupOfStage(stage), stage).toBe("PREPARATION");
    }
  });

  it("maps the two script gates to Chờ duyệt, and nothing else", () => {
    expect(groupOfStage("TEAM_LEAD_REVIEW")).toBe("EDITORIAL_REVIEW");
    expect(groupOfStage("HEAD_REVIEW")).toBe("EDITORIAL_REVIEW");

    const review = OPERATIONAL_GROUPS.find((group) => group.key === "EDITORIAL_REVIEW")!;
    expect(review.stages).toEqual(["TEAM_LEAD_REVIEW", "HEAD_REVIEW"]);
    expect(review.columns.map(columnLabel)).toEqual([
      "Chờ duyệt Trưởng nhóm",
      "Chờ duyệt Trưởng phòng",
    ]);
  });

  it("maps the completion and cancellation stages", () => {
    for (const stage of ["READY_TO_PUBLISH", "PUBLISHED"]) {
      expect(groupOfStage(stage), stage).toBe("COMPLETED");
    }
    // Step 1F.2.3f.5. `MEASURED` is retired and belongs to no group. It still
    // has a *label*, because a transition-history row can name it and a raw
    // token on screen is worse than the word it used to mean - but nothing puts
    // a card in a column for it.
    expect(groupOfStage("MEASURED")).toBeUndefined();
    expect(groupOfStage("CANCELLED")).toBe("CANCELLED");
    // Abandoned work is its own terminal group and comes last, so it is
    // reachable without sitting in anybody's way.
    expect(OPERATIONAL_GROUPS.at(-1)!.key).toBe("CANCELLED");
    for (const group of OPERATIONAL_GROUPS.slice(0, -1)) {
      expect(group.stages, group.key).not.toContain("CANCELLED");
    }
  });
});

describe("86. internal review is production, not editorial review", () => {
  it("does not put INTERNAL_REVIEW in Chờ duyệt", () => {
    const review = OPERATIONAL_GROUPS.find((group) => group.key === "EDITORIAL_REVIEW")!;
    expect(review.stages).not.toContain("INTERNAL_REVIEW");
    expect(review.columns.map(columnLabel)).not.toContain("Chờ duyệt nội bộ");
  });

  it("puts it in Sản xuất, last, after the work it reviews", () => {
    expect(groupOfStage("INTERNAL_REVIEW")).toBe("PRODUCTION");
    expect(contentOperationalGroup(INTERNAL)!.label).toBe("Sản xuất");
    expect(contentOperationalState(INTERNAL)).toBe("Chờ duyệt nội bộ");
  });

  it("renders no internal-review column on the editorial tab", async () => {
    await openBoard([LEAD_ITEM, HEAD_ITEM, INTERNAL]);
    await userEvent.click(groupTab("Chờ duyệt"));

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    expect(within(boardRegion).getByRole("region", { name: "Chờ duyệt Trưởng nhóm" })).toBeInTheDocument();
    expect(within(boardRegion).getByRole("region", { name: "Chờ duyệt Trưởng phòng" })).toBeInTheDocument();
    expect(
      within(boardRegion).queryByRole("region", { name: "Chờ duyệt nội bộ" }),
    ).not.toBeInTheDocument();
    // And the cut itself is not among the scripts waiting for a decision.
    expect(within(boardRegion).queryByText(INTERNAL.title)).not.toBeInTheDocument();
  });
});

describe("87. APPROVED is two jobs, and the board says which", () => {
  it("maps an unclaimed approved piece to Chờ nhận sản xuất", () => {
    expect(contentOperationalGroup(UNCLAIMED)!.label).toBe("Sản xuất");
    expect(contentOperationalState(UNCLAIMED)).toBe("Chờ nhận sản xuất");
  });

  it("maps a claimed one to Sẵn sàng sản xuất", () => {
    expect(contentOperationalGroup(CLAIMED)!.label).toBe("Sản xuất");
    expect(contentOperationalState(CLAIMED)).toBe("Sẵn sàng sản xuất");
  });

  it("maps PRODUCTION to Đang sản xuất", () => {
    expect(contentOperationalState(PRODUCING)).toBe("Đang sản xuất");
  });

  it("reads the server's derived state rather than recomputing it", () => {
    const source = read("lib/board.ts");
    // The rule that turns (stage, producer) into a production state is the
    // server's. A browser doing it again is a second authority, one refactor
    // away from disagreeing.
    expect(source).toContain("item.production_state === column.key");
    expect(source).not.toContain("producer_user_id");
  });
});

// ===========================================================================
// 88-89: THE PRODUCTION COLUMNS
// ===========================================================================

describe("88. the production columns follow the real lifecycle", () => {
  it("renders the four in order, with internal review last", async () => {
    await openBoard(PRODUCTION_ITEMS);
    await userEvent.click(groupTab("Sản xuất"));

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    const headings = within(boardRegion)
      .getAllByRole("region")
      .map((section) => section.getAttribute("aria-label"));
    expect(headings).toEqual([
      "Chờ nhận sản xuất",
      "Sẵn sàng sản xuất",
      "Đang sản xuất",
      "Chờ duyệt nội bộ",
    ]);
  });

  it("puts each card in its own column", async () => {
    await openBoard(PRODUCTION_ITEMS);
    await userEvent.click(groupTab("Sản xuất"));
    await settleLanes();

    for (const [column, card] of [
      ["Chờ nhận sản xuất", UNCLAIMED],
      ["Sẵn sàng sản xuất", CLAIMED],
      ["Đang sản xuất", PRODUCING],
      ["Chờ duyệt nội bộ", INTERNAL],
    ] as const) {
      const lane = screen.getByRole("region", { name: column });
      expect(within(lane).getByText(card.title), column).toBeInTheDocument();
    }
  });

  it("does not draw an APPROVED lane that mixes the two", async () => {
    await openBoard(PRODUCTION_ITEMS);
    await userEvent.click(groupTab("Sản xuất"));
    // "Đã duyệt" is what happened; it is not a column, because it does not tell
    // anybody what to do next.
    expect(screen.queryByRole("region", { name: stageLabel("APPROVED") })).not.toBeInTheDocument();
  });
});

describe("89. an empty group says so in its own words", () => {
  it("shows the group's sentence instead of four empty columns", async () => {
    // Cards exist, but none of them is in the open group - which is the case a
    // row of empty lanes handles worst.
    await openBoard([IDEA_ITEM]);
    await userEvent.click(groupTab("Sản xuất"));

    expect(screen.getByText("Không có nội dung nào trong quy trình sản xuất.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Chờ nhận sản xuất" })).not.toBeInTheDocument();
  });

  it("gives every group a sentence of its own", () => {
    const messages = OPERATIONAL_GROUPS.map((group) => group.empty);
    expect(new Set(messages).size).toBe(OPERATIONAL_GROUPS.length);
    for (const message of messages) expect(message).not.toBe("");
  });
});

// ===========================================================================
// 90: THE COUNTS
// ===========================================================================

describe("90. the group counts describe the filtered queue", () => {
  it("counts internal review under Sản xuất and not under Chờ duyệt", async () => {
    await openBoard([LEAD_ITEM, HEAD_ITEM, ...PRODUCTION_ITEMS]);

    // Two script gates waiting; four items in the production half, of which one
    // is the cut with the internal reviewer.
    expect(groupTab("Chờ duyệt")).toHaveTextContent("Chờ duyệt2");
    expect(groupTab("Sản xuất")).toHaveTextContent("Sản xuất4");
  });

  it("counts both halves of APPROVED under Sản xuất", () => {
    const counts = boardCounts(board([...PRODUCTION_ITEMS, IDEA_ITEM]) as never);
    const production = OPERATIONAL_GROUPS.find((group) => group.key === "PRODUCTION")!;
    const review = OPERATIONAL_GROUPS.find((group) => group.key === "EDITORIAL_REVIEW")!;
    expect(groupCount(production, counts)).toBe(4);
    expect(groupCount(review, counts)).toBe(0);
  });

  it("labels the columns from the server's counts, not from the page of cards", async () => {
    // Twelve items in the filtered set, one of them on this page. A column that
    // counted its own children would say 1.
    const counted = [
      ...Array.from({ length: 5 }, (_, index) =>
        item(`a${index}0000`, `Chưa nhận ${index}`, "APPROVED", {
          production_state: "WAITING_FOR_PRODUCER",
        }),
      ),
      ...Array.from({ length: 7 }, (_, index) =>
        item(`b${index}0000`, `Đang dựng ${index}`, "PRODUCTION", {
          producer_user_id: PRODUCER,
          production_state: "IN_PRODUCTION",
        }),
      ),
    ];
    await openBoard([counted[0]], { counted, total: counted.length });
    await userEvent.click(groupTab("Sản xuất"));

    expect(screen.getByRole("region", { name: "Chờ nhận sản xuất" })).toHaveTextContent("5");
    expect(screen.getByRole("region", { name: "Đang sản xuất" })).toHaveTextContent("7");
    // And the tab agrees, over the same numbers.
    expect(groupTab("Sản xuất")).toHaveTextContent("Sản xuất12");
  });

  it("counts the cancelled group too", async () => {
    await openBoard([IDEA_ITEM, CANCELLED_ITEM]);
    expect(groupTab("Đã hủy")).toHaveTextContent("Đã hủy1");
    expect(groupTab("Chuẩn bị")).toHaveTextContent("Chuẩn bị1");
  });
});

// ===========================================================================
// 91-92: THE CARDS, AND WHAT THIS PATCH DID NOT TOUCH
// ===========================================================================

describe("91. a card is compact and says what it is", () => {
  it("shows the title, the code, the brand, the responsible person and the state", async () => {
    const owned = { ...UNCLAIMED, owner_user_id: SESSION.user_id };
    await openBoard([owned]);
    await userEvent.click(groupTab("Sản xuất"));
    await settleLanes();

    const card = screen.getByText(owned.title).closest("a")!;
    expect(card).toHaveTextContent(owned.code);
    expect(card).toHaveTextContent("Apexmed");
    expect(card).toHaveTextContent("Lê Trưởng Nhóm");
    // The operational badge, in words. Not "APPROVED", which is the enum, and
    // not "Đã duyệt", which is what happened rather than what to do.
    expect(card).toHaveTextContent("Chờ nhận sản xuất");
    expect(card.textContent).not.toContain("APPROVED");
  });

  it("prints no UUID anywhere on the board", async () => {
    await openBoard(PRODUCTION_ITEMS);
    await userEvent.click(groupTab("Sản xuất"));
    await settleLanes();

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    expect(boardRegion.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/,
    );
    // The id is in the link, which is where an id belongs.
    expect(screen.getByText(PRODUCING.title).closest("a")).toHaveAttribute(
      "href",
      `/pr/content/${PRODUCING.id}`,
    );
  });
});

describe("92. the grouping narrows the query and decides nothing else", () => {
  it("decides nothing about authorization or workflow", () => {
    const source = read("lib/board.ts");
    for (const forbidden of [
      "PR_INTERNAL_REVIEW",
      "PR_TEAM_LEAD_REVIEW",
      "capabilit",
      "canTransition",
      "nextStage",
      "available",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
    // A group is a filter and a reading order, and the table says so. What it
    // must never become is a second opinion about who may do what.
    expect(source).toContain("Ordering and words, not rules");
  });

  it("still shows a reviewer their queue however the board groups it", async () => {
    // The MY_ACTIONS queue is the server's answer and this patch does not touch
    // it: an internal reviewer's item arrives in the response as before, and the
    // only change is which tab draws it.
    SEARCH.value = new URLSearchParams();
    stubFetch(routes(board([LEAD_ITEM, INTERNAL], { scope: "MY_ACTIONS" })));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Cần tôi xử lý" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );

    await userEvent.click(groupTab("Sản xuất"));
    await settleLanes();
    expect(
      within(screen.getByRole("region", { name: "Chờ duyệt nội bộ" })).getByText(INTERNAL.title),
    ).toBeInTheDocument();
  });

  it("keeps the stage table out of the workflow rules", () => {
    // The one thing `board.ts` may know about a stage is which tab it is drawn
    // under. Which stage may follow which is the Python matrix, and the detail
    // page asks the server for it.
    const source = read("lib/board.ts");
    expect(source).toContain("GROUP_STAGES");
    expect(source).not.toContain("STAGE_TRANSITIONS");
  });
});

// ===========================================================================
// 93-95: THE GROUP IS A FILTER, NOT A PASS OVER THE PAGE (STEP 1F.2.3c1)
// ===========================================================================

/**
 * The filtered dataset from the report, as the server would count it: 146 items
 * under the current scope and filters, five of them in *Chuẩn bị*.
 *
 * The point of the shape is the ratio. Five preparation items among 146 do not
 * fit in one page of sixty *unless the group reaches the query* - grouped in the
 * browser they arrive one on page 1 and four on page 3, under a tab that says 5.
 */
const REPORTED = [
  ...Array.from({ length: 5 }, (_, index) => item(`prep-${index}`, `Chuẩn bị ${index}`, "SCRIPTING")),
  ...Array.from({ length: 86 }, (_, index) =>
    item(`rev-${index}`, `Chờ duyệt ${index}`, "TEAM_LEAD_REVIEW"),
  ),
  ...Array.from({ length: 46 }, (_, index) =>
    item(`prod-${index}`, `Sản xuất ${index}`, "PRODUCTION", {
      producer_user_id: PRODUCER,
      production_state: "IN_PRODUCTION",
    }),
  ),
  ...Array.from({ length: 9 }, (_, index) => item(`can-${index}`, `Đã hủy ${index}`, "CANCELLED")),
];

/** The five preparation cards of that set - what the server returns for the tab. */
const PREPARATION_PAGE = REPORTED.filter((row) => row.workflow_stage === "SCRIPTING");

/** Every `/contents/board` request the page made, as query strings, in order. */
function boardRequests(stub: ReturnType<typeof stubFetch>): URLSearchParams[] {
  return (stub as unknown as { calls: Array<{ url: string }> }).calls
    .filter((call) => call.url.includes("/api/pr/contents/board"))
    .map((call) => new URLSearchParams(call.url.split("?")[1] ?? ""));
}

describe("93. the open group reaches the server", () => {
  it("sends it on the first request, before anybody has clicked anything", async () => {
    const stub = await openBoard([IDEA_ITEM]);

    const requests = boardRequests(stub);
    // Not "omitted until chosen": the board opens on Chuẩn bị, so every request
    // it makes is about Chuẩn bị. Sent with the paging, which is the whole
    // claim - the server narrows and *then* cuts.
    for (const request of requests) expect(request.get("group")).toBe("PREPARATION");

    // Step 1F.2.3c2 made that n + 1 requests: the figures, with no rows at all,
    // and one page per lane of the open group.
    const figures = requests.find((request) => !request.has("lane"))!;
    expect(figures.get("limit")).toBe("0");
    const lanes = requests.filter((request) => request.has("lane"));
    expect(lanes.map((request) => request.get("lane")).sort()).toEqual(
      ["AI_REVIEW", "BRIEFING", "IDEA", "SCRIPTING"],
    );
    for (const request of lanes) {
      expect(request.get("limit")).toBe("20");
      expect(request.get("offset")).toBe("0");
    }
  });

  it("asks for the group in the URL, so a reload and a shared link restore it", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION" });
    const stub = await openBoard(PRODUCTION_ITEMS);

    expect(boardRequests(stub).at(-1)!.get("group")).toBe("PRODUCTION");
    // And the page is *at* that tab, not merely fetching for it.
    expect(groupTab("Sản xuất")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Chờ duyệt nội bộ" })).toBeInTheDocument();
  });

  it("asks again, for the new group, when a tab is clicked", async () => {
    const stub = await openBoard([IDEA_ITEM]);

    for (const [label, key] of [
      ["Chờ duyệt", "EDITORIAL_REVIEW"],
      ["Sản xuất", "PRODUCTION"],
      ["Hoàn tất", "COMPLETED"],
      ["Đã hủy", "CANCELLED"],
      ["Chuẩn bị", "PREPARATION"],
    ] as const) {
      await userEvent.click(groupTab(label));
      await waitFor(() => expect(boardRequests(stub).at(-1)!.get("group")).toBe(key));
    }
  });

  it("writes the group into the URL rather than keeping it in the component", async () => {
    await openBoard([IDEA_ITEM]);
    await userEvent.click(groupTab("Sản xuất"));

    const written = new URLSearchParams(replaced.at(-1)!.split("?")[1]);
    expect(written.get("group")).toBe("PRODUCTION");
    // The rest of the query string is untouched: a tab is not a reset.
    expect(written.get("scope")).toBe("ALL");
    // And the page holds no copy of it - a `useState` group is what made the
    // tabs a browser-side regrouping in the first place.
    expect(read("app/pr/content/page.tsx")).not.toContain("setGroup");
  });

  it("follows Back and Forward, because the URL is the state", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION" });
    const stub = await openBoard(PRODUCTION_ITEMS);
    expect(groupTab("Sản xuất")).toHaveAttribute("aria-selected", "true");

    // Back to the preparation view, as the browser would do it: the URL moves
    // and nothing else. The page has to follow it, and ask for that group.
    act(() => URL_BAR.navigate("/pr/content?scope=ALL&group=PREPARATION"));
    await waitFor(() =>
      expect(groupTab("Chuẩn bị")).toHaveAttribute("aria-selected", "true"),
    );
    await waitFor(() => expect(boardRequests(stub).at(-1)!.get("group")).toBe("PREPARATION"));

    // Forward again.
    act(() => URL_BAR.navigate("/pr/content?scope=ALL&group=PRODUCTION"));
    await waitFor(() => expect(groupTab("Sản xuất")).toHaveAttribute("aria-selected", "true"));
  });

  it("resets the page, because page 2 of 146 is past the end of 5", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", page: "2" });
    await openBoard([IDEA_ITEM], { counted: REPORTED, total: REPORTED.length });
    await userEvent.click(groupTab("Chuẩn bị"));

    const written = new URLSearchParams(replaced.at(-1)!.split("?")[1]);
    expect(written.get("group")).toBe("PREPARATION");
    expect(written.has("page")).toBe(false);
  });
});

describe("94. a page of a group is a page of that group", () => {
  it("shows all five preparation items on the first page, and offers no second", async () => {
    // The reported case: 146 items match the filters, five are in this group,
    // the page holds sixty. All five, one page, no pager.
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PREPARATION" });
    await openBoard(PREPARATION_PAGE, { counted: REPORTED, total: PREPARATION_PAGE.length });

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    for (const card of PREPARATION_PAGE) {
      expect(within(boardRegion).getByText(card.title), card.title).toBeInTheDocument();
    }
    // No cards from another group took a slot, and there is nowhere else to go.
    expect(within(boardRegion).queryByText("Chờ duyệt 0")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sau/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Trước/ })).not.toBeInTheDocument();
  });

  it("counts each lane over its own queue and the tabs over everything else", async () => {
    // The two count scopes, on one screen: the lane header is about Chờ duyệt
    // Trưởng nhóm's own 86, and the strip still says how much work the other
    // four tabs hold. There is no third number describing the group's page,
    // because since Step 1F.2.3c2 the group has no page.
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "EDITORIAL_REVIEW" });
    await openBoard(
      REPORTED.filter((row) => row.workflow_stage === "TEAM_LEAD_REVIEW").slice(0, 20),
      { counted: REPORTED, total: 86 },
    );

    expect(screen.getByRole("region", { name: "Chờ duyệt Trưởng nhóm" })).toHaveTextContent("86");
    expect(screen.queryByText("1–60 / 86")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sau/ })).not.toBeInTheDocument();
    for (const [label, count] of [
      ["Chuẩn bị", 5],
      ["Chờ duyệt", 86],
      ["Sản xuất", 46],
      ["Hoàn tất", 0],
      ["Đã hủy", 9],
    ] as const) {
      expect(groupTab(label), label).toHaveTextContent(`${label}${count}`);
    }
  });

  it("says the group is empty rather than blaming the filters", async () => {
    // Nothing in Hoàn tất, plenty everywhere else. "Xóa bộ lọc" would be wrong
    // advice: the filter is fine, the tab is empty.
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "COMPLETED" });
    await openBoard([], { counted: REPORTED, total: 0 });

    expect(screen.getByText("Không có nội dung nào ở nhóm hoàn tất.")).toBeInTheDocument();
    expect(
      screen.queryByText(/Không có nội dung nào khớp bộ lọc hiện tại/),
    ).not.toBeInTheDocument();
  });

  it("keeps the filter message when nothing matches at all", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", platform: PLATFORMS[0].id });
    await openBoard([], { counted: [], total: 0 });

    expect(screen.getByText(/Không có nội dung nào khớp bộ lọc hiện tại/)).toBeInTheDocument();
  });
});

describe("95. the group composes with the filters instead of replacing them", () => {
  it("sends both the group and the stage, and lets the server intersect them", async () => {
    SEARCH.value = new URLSearchParams({
      scope: "ALL",
      group: "PRODUCTION",
      stage: "INTERNAL_REVIEW",
    });
    const stub = await openBoard([INTERNAL], { counted: [INTERNAL] });

    const request = boardRequests(stub).at(-1)!;
    expect(request.get("group")).toBe("PRODUCTION");
    expect(request.get("stage")).toBe("INTERNAL_REVIEW");
  });

  it("carries every other filter alongside it", async () => {
    SEARCH.value = new URLSearchParams({
      scope: "MY_ACTIONS",
      group: "PRODUCTION",
      platform: PLATFORMS[0].id,
      channel: CHANNELS[0].id,
      responsible: SESSION.user_id,
      q: "nâng mũi",
      date: "TODAY",
    });
    const stub = await openBoard(PRODUCTION_ITEMS, { scope: "MY_ACTIONS" });

    const request = boardRequests(stub).at(-1)!;
    expect(request.get("group")).toBe("PRODUCTION");
    expect(request.get("scope")).toBe("MY_ACTIONS");
    expect(request.get("platform_id")).toBe(PLATFORMS[0].id);
    expect(request.get("channel_id")).toBe(CHANNELS[0].id);
    expect(request.get("responsible_user_id")).toBe(SESSION.user_id);
    expect(request.get("search")).toBe("nâng mũi");
    expect(request.get("date_from")).toBeTruthy();
  });

  it("still splits the production group by the server's derived state", async () => {
    // The group brings back three stages; `production_state` sorts them into
    // four columns, and `INTERNAL_REVIEW` is the last of them rather than a
    // third script gate. Step 1F.2.3c's split, unchanged by the group filter.
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION" });
    await openBoard(PRODUCTION_ITEMS);

    for (const [column, card] of [
      ["Chờ nhận sản xuất", UNCLAIMED],
      ["Sẵn sàng sản xuất", CLAIMED],
      ["Đang sản xuất", PRODUCING],
      ["Chờ duyệt nội bộ", INTERNAL],
    ] as const) {
      const lane = screen.getByRole("region", { name: column });
      expect(within(lane).getByText(card.title), column).toBeInTheDocument();
    }
  });
});

// ===========================================================================
// 96-100: EVERY LANE PAGES ON ITS OWN (STEP 1F.2.3c2)
// ===========================================================================

/**
 * The production board from the report, exactly as the server counted it.
 *
 * 155 waiting for a producer, none ready, three being cut, twelve with the
 * internal reviewer. Every number on that screen was right and the board was
 * still wrong: one pager over the group meant the 155 owned the first pages, so
 * a lane header reading *Đang sản xuất 3* sat above an empty column and the
 * three cards were on page three of a pager belonging to another lane.
 *
 * The ratio is the fixture. Four columns of five would fit on any page and
 * would prove nothing.
 */
const WAITING_MANY = Array.from({ length: 155 }, (_, index) =>
  item(`w${index}`, `Chờ nhận ${index}`, "APPROVED", {
    production_state: "WAITING_FOR_PRODUCER",
  }),
);
const PRODUCING_THREE = Array.from({ length: 3 }, (_, index) =>
  item(`p${index}`, `Đang dựng ${index}`, "PRODUCTION", {
    producer_user_id: PRODUCER,
    production_state: "IN_PRODUCTION",
  }),
);
const INTERNAL_TWELVE = Array.from({ length: 12 }, (_, index) =>
  item(`i${index}`, `Chờ nội bộ ${index}`, "INTERNAL_REVIEW", {
    producer_user_id: PRODUCER,
    production_state: "IN_INTERNAL_REVIEW",
  }),
);
const REPORTED_PRODUCTION = [...WAITING_MANY, ...PRODUCING_THREE, ...INTERNAL_TWELVE];

/**
 * One lane's page, stubbed at the offset it will actually be asked for.
 *
 * The match string carries the lane *and* the offset, so a test can tell "the
 * lane asked again for its first page" from "the lane asked for its second" -
 * which is the whole of what "Xem thêm appends" and "a filter change resets"
 * come down to.
 */
function lanePage(lane: string, offset: number, items: ContentSummary[], total: number) {
  return {
    match: `lane=${lane}&limit=${LANE_PAGE_SIZE}&offset=${offset}`,
    body: {
      items,
      total,
      scope: "ALL",
      // Empty, as the server sends them for a lane request: one column was
      // asked about, so the board's two count tables are not part of the answer.
      stage_counts: [],
      production_state_counts: [],
      limit: LANE_PAGE_SIZE,
      offset,
    },
  };
}

/** The figures request - `limit=0`, so it carries counts and no cards at all. */
function figuresPage(counted: ContentSummary[], total: number, scope = "ALL") {
  return { match: "limit=0", body: { ...board([], { counted, total, scope }), limit: 0 } };
}

const BASE_ROUTES = [
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/api/pr/platforms", body: PLATFORMS },
  { match: "/api/pr/channels", body: CHANNELS },
  { match: "/api/pr/people", body: PEOPLE },
];

/** The reported board, with the busy lane's second page stubbed as well. */
const PRODUCTION_ROUTES = [
  ...BASE_ROUTES,
  lanePage("WAITING_FOR_PRODUCER", 0, WAITING_MANY.slice(0, 20), 155),
  lanePage("WAITING_FOR_PRODUCER", 20, WAITING_MANY.slice(20, 40), 155),
  lanePage("WAITING_FOR_PRODUCER", 40, WAITING_MANY.slice(40, 60), 155),
  lanePage("READY_FOR_PRODUCTION", 0, [], 0),
  lanePage("IN_PRODUCTION", 0, PRODUCING_THREE, 3),
  lanePage("IN_INTERNAL_REVIEW", 0, INTERNAL_TWELVE, 12),
  figuresPage(REPORTED_PRODUCTION, 170),
];

/** Render the reported production board and wait for every lane to answer. */
async function openProduction(routes = PRODUCTION_ROUTES, params?: Record<string, string>) {
  SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION", ...params });
  const stub = stubFetch(routes);
  renderWithQuery(<ContentBoardPage />);
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument(),
  );
  await settleLanes();
  return stub;
}

/** A lane's section, by its Vietnamese heading. */
const laneOf = (label: string) => screen.getByRole("region", { name: label });

/**
 * The offset each lane was **last** asked for.
 *
 * The reset assertions are about this rather than about the tail of the request
 * log: what has to be true after a filter changes is that every lane's current
 * page is its first, and counting requests off the end would depend on which
 * lane's fetch happened to be recorded last.
 */
function lastOffsets(
  stub: ReturnType<typeof stubFetch>,
  lanes: readonly string[],
): Record<string, string | undefined> {
  const seen: Record<string, string | undefined> = {};
  for (const request of laneRequests(stub)) {
    if (lanes.includes(request.lane)) seen[request.lane] = request.offset;
  }
  return seen;
}

const PRODUCTION_LANES = [
  "WAITING_FOR_PRODUCER",
  "READY_FOR_PRODUCTION",
  "IN_PRODUCTION",
  "IN_INTERNAL_REVIEW",
] as const;

const PREPARATION_LANES = ["IDEA", "BRIEFING", "SCRIPTING", "AI_REVIEW"] as const;

/** Which lanes were requested, and at what offset, in order. */
function laneRequests(stub: ReturnType<typeof stubFetch>) {
  return boardRequests(stub)
    .filter((request) => request.has("lane"))
    .map((request) => ({
      lane: request.get("lane")!,
      offset: request.get("offset") ?? "0",
      limit: request.get("limit") ?? "",
    }));
}

describe("96. the board is one request per lane, plus the figures", () => {
  it("renders every lane of the selected group", async () => {
    await openProduction();

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    expect(
      within(boardRegion)
        .getAllByRole("region")
        .map((section) => section.getAttribute("aria-label")),
    ).toEqual([
      "Chờ nhận sản xuất",
      "Sẵn sàng sản xuất",
      "Đang sản xuất",
      "Chờ duyệt nội bộ",
    ]);
  });

  it("gives each lane its own paginated dataset", async () => {
    const stub = await openProduction();

    // One request per column, each asking for that column and its first page.
    expect(laneRequests(stub)).toEqual([
      { lane: "WAITING_FOR_PRODUCER", offset: "0", limit: "20" },
      { lane: "READY_FOR_PRODUCTION", offset: "0", limit: "20" },
      { lane: "IN_PRODUCTION", offset: "0", limit: "20" },
      { lane: "IN_INTERNAL_REVIEW", offset: "0", limit: "20" },
    ]);
    // And every one of them carries the group and the scope: a lane narrows
    // *with* the filters, never instead of them.
    for (const request of boardRequests(stub)) {
      expect(request.get("group")).toBe("PRODUCTION");
      expect(request.get("scope")).toBe("ALL");
    }
  });

  it("asks for the figures without any cards", async () => {
    const stub = await openProduction();

    const figures = boardRequests(stub).filter((request) => !request.has("lane"));
    expect(figures).toHaveLength(1);
    expect(figures[0].get("limit")).toBe("0");
    // The regression this is really about: the page must not receive a page of
    // the group and keep the fraction of it that belongs to each column.
    expect(boardRequests(stub).some((request) => request.get("limit") === "60")).toBe(false);
  });

  it("issues the lane requests together rather than in a chain", async () => {
    // A waterfall - lane 1, then lane 2, then lane 3 - would make a four-column
    // board four round trips deep. They are all in flight before any of them
    // has answered, which is what mounting them in one render buys.
    const seen: string[] = [];
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const routes = PRODUCTION_ROUTES.map((route) =>
      route.match.startsWith("lane=")
        ? {
            ...route,
            body: route.body,
          }
        : route,
    );
    SEARCH.value = new URLSearchParams({ scope: "ALL", group: "PRODUCTION" });
    const stub = stubFetch(routes);
    const original = globalThis.fetch as unknown as (...args: unknown[]) => Promise<Response>;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("lane=")) {
        seen.push(new URLSearchParams(url.split("?")[1]).get("lane")!);
        await gate;
      }
      return original(input, init);
    });
    renderWithQuery(<ContentBoardPage />);
    // All four asked before the first was allowed to answer.
    await waitFor(() => expect(seen).toHaveLength(4));
    release!();
    await settleLanes();
    expect(new Set(seen).size).toBe(4);
    expect(stub).toBeTruthy();
  });
});

describe("97. the reported board: 155 / 0 / 3 / 12", () => {
  it("shows all three in-production cards on the first view", async () => {
    await openProduction();

    // The bug, stated as the assertion that would have failed: three cards, in
    // their own column, with nobody having touched another lane's pagination.
    const producing = laneOf("Đang sản xuất");
    expect(producing).toHaveTextContent("3");
    for (const card of PRODUCING_THREE) {
      expect(within(producing).getByText(card.title), card.title).toBeInTheDocument();
    }
    // And no "Xem thêm" there, because all three are loaded.
    expect(within(producing).queryByRole("button", { name: /Xem thêm/ })).not.toBeInTheDocument();
  });

  it("shows the busy lane's first page and offers more, without taking anybody's slots", async () => {
    await openProduction();

    const waiting = laneOf("Chờ nhận sản xuất");
    // The header is the whole queue; the cards are one page of it.
    expect(waiting).toHaveTextContent("155");
    expect(within(waiting).getAllByRole("link")).toHaveLength(20);
    expect(within(waiting).getByRole("button", { name: /Xem thêm/ })).toBeInTheDocument();
  });

  it("shows the internal review lane whole, because twelve fits in a lane page", async () => {
    await openProduction();

    const internal = laneOf("Chờ duyệt nội bộ");
    expect(internal).toHaveTextContent("12");
    expect(within(internal).getAllByRole("link")).toHaveLength(12);
    expect(within(internal).queryByRole("button", { name: /Xem thêm/ })).not.toBeInTheDocument();
  });

  it("draws an empty lane as empty rather than as a page to go and find", async () => {
    await openProduction();

    const ready = laneOf("Sẵn sàng sản xuất");
    expect(ready).toHaveTextContent("0");
    expect(within(ready).getByText("Chưa có nội dung ở bước này.")).toBeInTheDocument();
    expect(within(ready).queryByRole("button", { name: /Xem thêm/ })).not.toBeInTheDocument();
  });

  it("has no global pager to hide any of it behind", async () => {
    await openProduction();

    expect(screen.queryByRole("button", { name: /Sau/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Trước/ })).not.toBeInTheDocument();
    // "1–60 / 170" is the sentence this step deleted: it described a set that
    // was four queues, and it was the reason three cards were unreachable.
    expect(screen.queryByText(/1–60 \/ 170/)).not.toBeInTheDocument();
    expect(read("app/pr/content/page.tsx")).not.toContain("Sau →");
  });
});

describe("98. Xem thêm belongs to one lane", () => {
  it("appends that lane's next page and leaves the others alone", async () => {
    const stub = await openProduction();
    const before = within(laneOf("Đang sản xuất")).getAllByRole("link").length;

    await userEvent.click(
      within(laneOf("Chờ nhận sản xuất")).getByRole("button", { name: /Xem thêm/ }),
    );
    await waitFor(() =>
      expect(within(laneOf("Chờ nhận sản xuất")).getAllByRole("link")).toHaveLength(40),
    );

    const waiting = laneOf("Chờ nhận sản xuất");
    // Appended, not replaced: the first page's cards are still on screen above
    // the second page's.
    expect(within(waiting).getByText("Chờ nhận 0")).toBeInTheDocument();
    expect(within(waiting).getByText("Chờ nhận 20")).toBeInTheDocument();
    // The header still describes the queue rather than what is loaded.
    expect(waiting).toHaveTextContent("155");

    // Nothing else moved. No other lane refetched, and no other lane's cards
    // changed - which is the property the global pager could not have.
    expect(within(laneOf("Đang sản xuất")).getAllByRole("link")).toHaveLength(before);
    expect(laneRequests(stub).filter((request) => request.lane !== "WAITING_FOR_PRODUCER")).toEqual([
      { lane: "READY_FOR_PRODUCTION", offset: "0", limit: "20" },
      { lane: "IN_PRODUCTION", offset: "0", limit: "20" },
      { lane: "IN_INTERNAL_REVIEW", offset: "0", limit: "20" },
    ]);
  });

  it("asks only for the next page, never for the ones it already has", async () => {
    const stub = await openProduction();

    await userEvent.click(
      within(laneOf("Chờ nhận sản xuất")).getByRole("button", { name: /Xem thêm/ }),
    );
    await waitFor(() =>
      expect(within(laneOf("Chờ nhận sản xuất")).getAllByRole("link")).toHaveLength(40),
    );

    expect(laneRequests(stub).filter((request) => request.lane === "WAITING_FOR_PRODUCER")).toEqual([
      { lane: "WAITING_FOR_PRODUCER", offset: "0", limit: "20" },
      { lane: "WAITING_FOR_PRODUCER", offset: "20", limit: "20" },
    ]);
  });

  it("stops offering itself once the whole lane is loaded", async () => {
    // A lane of 22: one full page, then two, and then nothing more to ask for.
    const twentyTwo = WAITING_MANY.slice(0, 22);
    const routes = [
      ...BASE_ROUTES,
      lanePage("WAITING_FOR_PRODUCER", 0, twentyTwo.slice(0, 20), 22),
      lanePage("WAITING_FOR_PRODUCER", 20, twentyTwo.slice(20), 22),
      lanePage("READY_FOR_PRODUCTION", 0, [], 0),
      lanePage("IN_PRODUCTION", 0, PRODUCING_THREE, 3),
      lanePage("IN_INTERNAL_REVIEW", 0, [], 0),
      figuresPage([...twentyTwo, ...PRODUCING_THREE], 25),
    ];
    await openProduction(routes);

    await userEvent.click(
      within(laneOf("Chờ nhận sản xuất")).getByRole("button", { name: /Xem thêm/ }),
    );
    await waitFor(() =>
      expect(within(laneOf("Chờ nhận sản xuất")).getAllByRole("link")).toHaveLength(22),
    );
    expect(
      within(laneOf("Chờ nhận sản xuất")).queryByRole("button", { name: /Xem thêm/ }),
    ).not.toBeInTheDocument();
  });

  it("names the lane it belongs to, so four of them are four controls", async () => {
    await openProduction();
    // Four identical "Xem thêm" buttons would be indistinguishable to a screen
    // reader; each is labelled with its column.
    expect(
      screen.getByRole("button", { name: "Xem thêm Chờ nhận sản xuất" }),
    ).toBeInTheDocument();
  });
});

describe("99. changing the view resets every lane", () => {
  /** Load a second page into the busy lane, so a reset has something to undo. */
  async function loadMore() {
    await userEvent.click(
      within(laneOf("Chờ nhận sản xuất")).getByRole("button", { name: /Xem thêm/ }),
    );
    await waitFor(() =>
      expect(within(laneOf("Chờ nhận sản xuất")).getAllByRole("link")).toHaveLength(40),
    );
  }

  it("discards lane depth when the group changes and comes back", async () => {
    const routes = [
      ...BASE_ROUTES,
      lanePage("WAITING_FOR_PRODUCER", 0, WAITING_MANY.slice(0, 20), 155),
      lanePage("WAITING_FOR_PRODUCER", 20, WAITING_MANY.slice(20, 40), 155),
      lanePage("READY_FOR_PRODUCTION", 0, [], 0),
      lanePage("IN_PRODUCTION", 0, PRODUCING_THREE, 3),
      lanePage("IN_INTERNAL_REVIEW", 0, INTERNAL_TWELVE, 12),
      lanePage("IDEA", 0, [IDEA_ITEM], 1),
      lanePage("BRIEFING", 0, [], 0),
      lanePage("SCRIPTING", 0, [], 0),
      lanePage("AI_REVIEW", 0, [], 0),
      // The figures have to hold work in *both* groups, or the tab this test
      // switches to would draw its "nothing here" sentence and no lanes at all.
      figuresPage([...REPORTED_PRODUCTION, IDEA_ITEM], 171),
    ];
    const stub = await openProduction(routes);
    await loadMore();

    await userEvent.click(groupTab("Chuẩn bị"));
    await settleLanes();
    // The new group's lanes, each from its first page. No offset came with us.
    await waitFor(() =>
      expect(Object.keys(lastOffsets(stub, PREPARATION_LANES))).toHaveLength(4),
    );
    expect(lastOffsets(stub, PREPARATION_LANES)).toEqual({
      IDEA: "0",
      BRIEFING: "0",
      SCRIPTING: "0",
      AI_REVIEW: "0",
    });

    await userEvent.click(groupTab("Sản xuất"));
    await settleLanes();
    await waitFor(() =>
      expect(within(laneOf("Chờ nhận sản xuất")).getAllByRole("link")).toHaveLength(20),
    );
    // Back to twenty: the lane opens at its first page rather than restoring
    // somebody's scroll depth from before they left.
    expect(
      within(laneOf("Chờ nhận sản xuất")).getByRole("button", { name: /Xem thêm/ }),
    ).toBeInTheDocument();
  });

  for (const [name, control, value] of [
    ["priority", "Lọc theo mức ưu tiên", "CRITICAL"],
    ["content type", "Lọc theo loại nội dung", "CORPORATE_TVC"],
  ] as const) {
    it(`resets every lane when the ${name} filter changes`, async () => {
      const filtered = WAITING_MANY.slice(0, 2);
      const routes = [
        ...PRODUCTION_ROUTES,
        // The filtered board: a different query, so a different first page.
        {
          match: `lane=WAITING_FOR_PRODUCER&limit=${LANE_PAGE_SIZE}&offset=0`,
          body: {
            items: filtered,
            total: 2,
            scope: "ALL",
            stage_counts: [],
            production_state_counts: [],
            limit: LANE_PAGE_SIZE,
            offset: 0,
          },
        },
      ];
      const stub = await openProduction(routes);
      await loadMore();

      await userEvent.selectOptions(screen.getByRole("combobox", { name: control }), value);
      await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
      await settleLanes();

      // Every lane asked again, and every one of them from offset 0. A lane
      // that kept its offset would show page 2 of a filter it had never seen
      // page 1 of.
      const key = name === "priority" ? "priority" : "content_type";
      await waitFor(() =>
        expect(
          boardRequests(stub).filter((request) => request.get(key) === value),
        ).toHaveLength(5),
      );
      expect(lastOffsets(stub, PRODUCTION_LANES)).toEqual({
        WAITING_FOR_PRODUCER: "0",
        READY_FOR_PRODUCTION: "0",
        IN_PRODUCTION: "0",
        IN_INTERNAL_REVIEW: "0",
      });
      // And the filter reached every lane request, not only the figures.
      for (const request of boardRequests(stub).filter((entry) => entry.has("lane")).slice(-4)) {
        expect(request.get(key)).toBe(value);
      }
    });
  }

  it("resets every lane when the scope changes", async () => {
    const stub = await openProduction();
    await loadMore();

    await userEvent.click(screen.getByRole("tab", { name: "Của tôi" }));
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    await settleLanes();

    await waitFor(() =>
      expect(
        boardRequests(stub).filter((request) => request.get("scope") === "MY_CONTENT"),
      ).toHaveLength(5),
    );
    expect(lastOffsets(stub, PRODUCTION_LANES)).toEqual({
      WAITING_FOR_PRODUCER: "0",
      READY_FOR_PRODUCTION: "0",
      IN_PRODUCTION: "0",
      IN_INTERNAL_REVIEW: "0",
    });
  });

  it("never appends a response that belonged to the previous filter", () => {
    // The filters are part of the query key, so a lane's pages live under the
    // filter set they were fetched for. A response arriving late lands in a
    // cache entry nothing is observing rather than on top of the new board -
    // which is why there is no per-lane offset in state or in the URL for a
    // stale response to be appended to.
    const source = read("app/pr/content/page.tsx");
    expect(source).toContain('queryKey: ["content-lane", lane, filters]');
    expect(source).not.toContain("useState<number");
    // And no lane cursor in the query string either: durable state is the
    // filters, and "Xem thêm" depth is not durable.
    expect(source).not.toContain("waiting_page");
    expect(source).not.toContain("lane_page");
  });
});

describe("100. the lane vocabulary is the server's, and stays off the screen", () => {
  it("keeps internal review under Sản xuất, as its last lane", async () => {
    await openProduction();

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    const headings = within(boardRegion)
      .getAllByRole("region")
      .map((section) => section.getAttribute("aria-label"));
    expect(headings.at(-1)).toBe("Chờ duyệt nội bộ");
    expect(within(laneOf("Chờ duyệt nội bộ")).getByText("Chờ nội bộ 0")).toBeInTheDocument();
  });

  it("keeps the four production lanes in handoff order", async () => {
    await openProduction();
    // Unchanged by this step: nobody has taken it, somebody has, they are
    // cutting it, it is with the internal reviewer.
    expect(
      OPERATIONAL_GROUPS.find((group) => group.key === "PRODUCTION")!.columns.map(
        (column) => column.key,
      ),
    ).toEqual([
      "WAITING_FOR_PRODUCER",
      "READY_FOR_PRODUCTION",
      "IN_PRODUCTION",
      "IN_INTERNAL_REVIEW",
    ]);
  });

  it("shows no raw lane code anywhere on the board", async () => {
    await openProduction();

    const boardRegion = screen.getByRole("region", { name: "Bảng nội dung" });
    for (const code of [
      "WAITING_FOR_PRODUCER",
      "READY_FOR_PRODUCTION",
      "IN_PRODUCTION",
      "IN_INTERNAL_REVIEW",
      "APPROVED",
      "INTERNAL_REVIEW",
    ]) {
      expect(boardRegion.textContent, code).not.toContain(code);
    }
    // The lane code is a query parameter and an aria-label is not it: the words
    // on screen are the Vietnamese ones from `lib/labels.ts`.
    expect(laneOf("Chờ nhận sản xuất")).toBeInTheDocument();
  });

  it("sends the column key as the lane, because they are the same vocabulary", async () => {
    const stub = await openProduction();

    const asked = laneRequests(stub).map((request) => request.lane);
    expect(asked).toEqual(
      OPERATIONAL_GROUPS.find((group) => group.key === "PRODUCTION")!.columns.map(
        (column) => column.key,
      ),
    );
  });
});
