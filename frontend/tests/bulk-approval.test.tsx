/**
 * Step 1F.2.8, browser half: selecting several items and approving them at once.
 *
 * Numbered 158-166, continuing from `open-contributions-and-comments`.
 *
 * The whole feature can be stated as five claims, and every test below is one of
 * them:
 *
 * 1. **only what the server says is approvable is selectable.** The checkbox
 *    follows `approvable_by_me`, per card, and nothing in this app derives it;
 * 2. **a selection is one step.** Ticking a card at another gate replaces the
 *    selection rather than adding to it, and the bar's label says which step;
 * 3. **a selection belongs to its filter context.** Any filter change clears it,
 *    so the count in the bar can never describe a board that has moved on;
 * 4. **nothing is sent before the dialog is confirmed**, exactly one request is
 *    sent when it is, and a second press while it is in flight sends nothing;
 * 5. **the result is truthful.** Success names the count the server reported;
 *    a refusal says that *nothing* was approved.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { STAGE_ORDER, stageLabel } from "@/lib/labels";
import { laneApprovalGate, OPERATIONAL_GROUPS } from "@/lib/board";
import type { ContentSummary } from "@/lib/api";
import {
  CONTENT,
  cancelDialog,
  confirm,
  dialog,
  renderWithQuery,
  SESSION,
  settleLanes,
  stubFetch,
  urlStore,
} from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: vi.fn(),
  }),
}));

const { default: ContentBoardPage } = await import("@/app/pr/content/page");

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
    approvable_by_me: false,
    ...extra,
  };
}

/** Three at the team-lead gate this session may decide, and one it may not. */
const MINE_A = item("aaaaaa", "Chăm sóc sau nâng mũi", "TEAM_LEAD_REVIEW", {
  approvable_by_me: true,
});
const MINE_B = item("bbbbbb", "Bí quyết giữ dáng", "TEAM_LEAD_REVIEW", {
  approvable_by_me: true,
});
const MINE_C = item("cccccc", "Hỏi đáp về filler", "TEAM_LEAD_REVIEW", {
  approvable_by_me: true,
});
const THEIRS = item("dddddd", "Kênh ngoài phạm vi", "TEAM_LEAD_REVIEW");
const AT_HEAD = item("eeeeee", "Chờ trưởng phòng", "HEAD_REVIEW", {
  approvable_by_me: true,
});

const BRAND = { id: CONTENT.brand_id, code: "APEXMED", name: "Apexmed" };
const PLATFORMS = [
  { id: "dddddddd-dddd-dddd-dddd-dddddddddddd", code: "TIKTOK", name: "TikTok" },
];
const CHANNELS = [
  {
    id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    code: "CH-TT",
    name: "Apexmed TikTok",
    brand_id: CONTENT.brand_id,
    platform_id: PLATFORMS[0].id,
    status: "ACTIVE",
  },
];
const PEOPLE = [{ user_id: SESSION.user_id, full_name: "Lê Trưởng Nhóm", role: "TEAM_LEAD" }];

function board(items: ContentSummary[], total = items.length) {
  return {
    items,
    total,
    scope: "ALL",
    stage_counts: STAGE_ORDER.map((stage) => ({
      stage,
      count: items.filter((row) => row.workflow_stage === stage).length,
    })),
    production_state_counts: [],
    limit: 20,
    offset: 0,
  };
}

/**
 * The board routes, plus the two Step 1F.2.8 endpoints.
 *
 * `/reviews/approvable` goes **before** `/api/pr/contents/board` because
 * `stubFetch` matches URL substrings in declaration order and both are under
 * `/api/pr`. `bulkApprove` likewise.
 */
function routes(
  items: ContentSummary[],
  options: {
    eligible?: { total: number; content_ids: string[]; truncated?: boolean };
    approveStatus?: number;
    approveBody?: unknown;
    total?: number;
  } = {},
) {
  const eligible = options.eligible ?? {
    total: 3,
    content_ids: [MINE_A.id, MINE_B.id, MINE_C.id],
  };
  return [
    {
      match: "/api/pr/reviews/bulk-approve",
      method: "POST",
      status: options.approveStatus ?? 201,
      body:
        options.approveBody ??
        {
          batch_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
          gate: "TEAM_LEAD_REVIEW",
          approved_count: 2,
          approved: [],
          requested_count: 2,
          duplicates_removed: 0,
        },
    },
    {
      match: "/api/pr/reviews/approvable",
      body: {
        gate: "TEAM_LEAD_REVIEW",
        limit: 200,
        truncated: false,
        ...eligible,
      },
    },
    { match: "/api/pr/brands", body: [BRAND] },
    { match: "/api/pr/platforms", body: PLATFORMS },
    { match: "/api/pr/channels", body: CHANNELS },
    { match: "/api/pr/people", body: PEOPLE },
    { match: "/api/pr/contents/board", body: board(items, options.total) },
  ];
}

async function openBoard(
  items: ContentSummary[],
  options?: Parameters<typeof routes>[1],
) {
  const stub = stubFetch(routes(items, options));
  renderWithQuery(<ContentBoardPage />);
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument(),
  );
  await settleLanes();
  return stub;
}

const calls = (stub: ReturnType<typeof stubFetch>) =>
  (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;

const bulkCalls = (stub: ReturnType<typeof stubFetch>) =>
  calls(stub).filter((call) => call.url.includes("/reviews/bulk-approve"));

/** The bulk bar, once something is selected. */
const bar = () => screen.getByRole("region", { name: "Thao tác hàng loạt" });

/** Tick one card's checkbox by its title. */
async function tick(title: string) {
  await userEvent.click(screen.getByRole("checkbox", { name: `Chọn ${title}` }));
}

beforeEach(() => {
  // The team-lead gate lives in the "Chờ duyệt" group, so the board has to be
  // standing there for its lane to be drawn at all.
  SEARCH.value = new URLSearchParams({ scope: "ALL", group: "EDITORIAL_REVIEW" });
});

// ===========================================================================
// 158. Only the server decides which cards may be ticked
// ===========================================================================

describe("158. a checkbox follows the server's per-item answer", () => {
  it("offers one on each approvable card and on none of the others", async () => {
    await openBoard([MINE_A, THEIRS]);

    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_A.title}` })).toBeInTheDocument();
    // `THEIRS` is at the same stage, in the same lane, one row away - and the
    // only difference is the server's flag. Deriving selectability from the
    // stage, or from "I hold a review capability", would tick both.
    expect(
      screen.queryByRole("checkbox", { name: `Chọn ${THEIRS.title}` }),
    ).not.toBeInTheDocument();
  });

  it("offers none at all when nothing on the board is approvable", async () => {
    await openBoard([THEIRS], { eligible: { total: 0, content_ids: [] } });

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
    ).not.toBeInTheDocument();
  });

  it("never offers one outside a review lane", () => {
    // A pure table assertion: three of the fifteen columns are review queues,
    // and one of the three is a production-state column rather than a stage one.
    const gated = OPERATIONAL_GROUPS.flatMap((group) =>
      group.columns.filter((column) => laneApprovalGate(column) !== null).map((c) => c.key),
    );
    expect(gated).toEqual(["TEAM_LEAD_REVIEW", "HEAD_REVIEW", "IN_INTERNAL_REVIEW"]);
    expect(laneApprovalGate({ key: "INTERNAL_REVIEW", kind: "PRODUCTION_STATE" })).toBeNull();
    expect(laneApprovalGate({ key: "PUBLISHED", kind: "STAGE" })).toBeNull();
  });
});

// ===========================================================================
// 159. The bar: count, step, and the two controls
// ===========================================================================

describe("159. the bulk action bar says how many and at which step", () => {
  it("appears on the first tick and counts what is selected", async () => {
    await openBoard([MINE_A, MINE_B, MINE_C]);
    expect(
      screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
    ).not.toBeInTheDocument();

    await tick(MINE_A.title);
    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();

    await tick(MINE_B.title);
    expect(within(bar()).getByText("Đã chọn 2 nội dung")).toBeInTheDocument();
    // The step, beside the count. "Đã chọn 12 nội dung" with no step is a
    // sentence somebody can act on without knowing what they are approving.
    expect(within(bar()).getByText(`· ${stageLabel("TEAM_LEAD_REVIEW")}`)).toBeInTheDocument();
    expect(
      within(bar()).getByRole("button", { name: "Duyệt 2 nội dung" }),
    ).toBeInTheDocument();
    expect(within(bar()).getByRole("button", { name: "Bỏ chọn" })).toBeInTheDocument();
  });

  it("unticks, and disappears when the last one goes", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await tick(MINE_A.title);

    expect(
      screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
    ).not.toBeInTheDocument();
  });

  it("clears everything on Bỏ chọn", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await tick(MINE_B.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Bỏ chọn" }));

    expect(
      screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_A.title}` })).not.toBeChecked();
  });
});

// ===========================================================================
// 160. Select all: the page, and the whole step
// ===========================================================================

describe("160. two select-alls, and the difference is stated", () => {
  it("ticks every eligible card on the page and no others", async () => {
    await openBoard([MINE_A, MINE_B, THEIRS]);

    await userEvent.click(screen.getByRole("checkbox", { name: /Chọn tất cả trên trang/ }));

    expect(within(bar()).getByText("Đã chọn 2 nội dung")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_A.title}` })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_B.title}` })).toBeChecked();
  });

  it("names the eligible total in the whole-step control, not the lane's", async () => {
    // Two cards loaded of a queue the server says holds 79 this person may
    // decide. The control has to say 79 - the lane's own count includes work
    // outside their scope, and "Chọn tất cả" with no number is unauditable.
    await openBoard([MINE_A, MINE_B, THEIRS], {
      total: 120,
      eligible: { total: 79, content_ids: [MINE_A.id, MINE_B.id, MINE_C.id] },
    });

    expect(
      screen.getByRole("button", {
        name: `Chọn tất cả 79 nội dung ở bước ${stageLabel("TEAM_LEAD_REVIEW")}`,
      }),
    ).toBeInTheDocument();
    // And the two controls are distinguishable, which requirement 4 is about.
    expect(screen.getByRole("checkbox", { name: "Chọn tất cả trên trang (2)" })).toBeInTheDocument();
  });

  it("freezes the server's id list rather than the cards on screen", async () => {
    const stub = await openBoard([MINE_A, MINE_B], {
      eligible: { total: 3, content_ids: [MINE_A.id, MINE_B.id, MINE_C.id] },
    });

    await userEvent.click(
      screen.getByRole("button", { name: /Chọn tất cả 3 nội dung ở bước/ }),
    );

    // Three, including one that is not on this page - which is the whole point
    // of resolving a select-all on the server.
    await waitFor(() =>
      expect(within(bar()).getByText("Đã chọn 3 nội dung")).toBeInTheDocument(),
    );
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 3 nội dung" }));
    await confirm();

    await waitFor(() => expect(bulkCalls(stub)).toHaveLength(1));
    expect(bulkCalls(stub)[0].body).toEqual({
      gate: "TEAM_LEAD_REVIEW",
      content_ids: [MINE_A.id, MINE_B.id, MINE_C.id],
    });
  });

  it("says it is approving the first N of a longer queue when truncated", async () => {
    await openBoard([MINE_A, MINE_B], {
      eligible: { total: 340, content_ids: [MINE_A.id, MINE_B.id, MINE_C.id], truncated: true },
    });
    await userEvent.click(
      screen.getByRole("button", { name: /Chọn tất cả 340 nội dung ở bước/ }),
    );
    await waitFor(() =>
      expect(within(bar()).getByText("Đã chọn 3 nội dung")).toBeInTheDocument(),
    );

    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 3 nội dung" }));
    // The button promises three, and the body says why it is three and not 340.
    expect(dialog().getByText("Duyệt 3 nội dung?")).toBeInTheDocument();
    expect(
      dialog().getByText(/Lần này duyệt 3 nội dung đầu tiên trong 340 nội dung/),
    ).toBeInTheDocument();
  });
});

// ===========================================================================
// 161. One selection, one step
// ===========================================================================

describe("161. a selection never spans two approval steps", () => {
  it("replaces the selection when a card at another gate is ticked", async () => {
    await openBoard([MINE_A, MINE_B, AT_HEAD]);

    await tick(MINE_A.title);
    await tick(MINE_B.title);
    expect(within(bar()).getByText("Đã chọn 2 nội dung")).toBeInTheDocument();

    await tick(AT_HEAD.title);

    // One item, at the other gate - and the bar's step label changed with it,
    // which is what makes the replacement visible rather than silent.
    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();
    expect(within(bar()).getByText(`· ${stageLabel("HEAD_REVIEW")}`)).toBeInTheDocument();
    // And the team-lead cards are no longer drawn as ticked.
    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_A.title}` })).not.toBeChecked();
  });
});

// ===========================================================================
// 162. A selection belongs to its filter context
// ===========================================================================

describe("162. changing a filter clears the selection", () => {
  it("clears it when the search changes", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox", { name: "Tìm nội dung" }), "filler");

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("clears it when the channel filter changes", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo kênh" }),
      CHANNELS[0].id,
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("clears it when the lifecycle group changes, which changes the gate", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);

    await userEvent.click(screen.getByRole("tab", { name: /^Sản xuất/ }));

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("clears it when the stage filter changes", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo bước" }),
      "HEAD_REVIEW",
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("survives opening a card, because a link is not a filter change", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);

    // The checkbox sits *beside* the card's link rather than inside it, so
    // reading a card is a navigation and ticking one is not. Clicking the link
    // in jsdom does not navigate, which is exactly the "opened and came back"
    // case: the selection must be untouched.
    await userEvent.click(screen.getByRole("link", { name: new RegExp(MINE_A.title) }));

    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Chọn ${MINE_A.title}` })).toBeChecked();
  });
});

// ===========================================================================
// 163. The confirmation
// ===========================================================================

describe("163. the bulk confirmation says what will happen to how many", () => {
  it("names the count, the step, and where the items go", async () => {
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await tick(MINE_B.title);

    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 2 nội dung" }));

    const box = dialog();
    expect(box.getByText("Duyệt 2 nội dung?")).toBeInTheDocument();
    expect(
      box.getByText(
        `2 nội dung ở bước ${stageLabel(
          "TEAM_LEAD_REVIEW",
        )} sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.`,
      ),
    ).toBeInTheDocument();
    expect(box.getByText("Số lượng: 2 nội dung")).toBeInTheDocument();
    // Titles as a sample, never a list of eighty.
    expect(box.getByText(new RegExp(MINE_A.title))).toBeInTheDocument();
  });

  it("shows five titles and counts the rest", async () => {
    const many = ["1", "2", "3", "4", "5", "6", "7"].map((n) =>
      item(`00000${n}`, `Nội dung ${n}`, "TEAM_LEAD_REVIEW", { approvable_by_me: true }),
    );
    await openBoard(many, {
      eligible: { total: many.length, content_ids: many.map((row) => row.id) },
    });

    await userEvent.click(screen.getByRole("checkbox", { name: /Chọn tất cả trên trang/ }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 7 nội dung" }));

    expect(dialog().getByText(/và 2 nội dung khác/)).toBeInTheDocument();
    expect(dialog().queryByText(/Nội dung 7/)).not.toBeInTheDocument();
  });

  it("sends nothing when the dialog is cancelled", async () => {
    const stub = await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 1 nội dung" }));
    await cancelDialog();

    expect(bulkCalls(stub)).toHaveLength(0);
    // And the selection survives being cancelled: cancelling means "not yet",
    // not "start again".
    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();
  });

  it("sends exactly one request when it is confirmed", async () => {
    const stub = await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await tick(MINE_B.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 2 nội dung" }));
    await confirm();

    await waitFor(() => expect(bulkCalls(stub)).toHaveLength(1));
    expect(bulkCalls(stub)[0].method).toBe("POST");
    expect(bulkCalls(stub)[0].body).toEqual({
      gate: "TEAM_LEAD_REVIEW",
      content_ids: [MINE_A.id, MINE_B.id],
    });
  });

  it("cannot be double-submitted from the dialog", async () => {
    const stub = await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 1 nội dung" }));

    const accept = screen
      .getByRole("dialog")
      .querySelector<HTMLElement>("[data-confirm-accept]")!;
    await userEvent.click(accept);
    // The latch is held for as long as the request is, so a second press on the
    // same button - before the dialog has closed - adds nothing.
    await userEvent.click(accept).catch(() => undefined);

    await waitFor(() => expect(bulkCalls(stub)).toHaveLength(1));
  });
});

// ===========================================================================
// 164-166. What the panel says afterwards
// ===========================================================================

describe("164. success says how many were approved", () => {
  it("reports the server's count, not the browser's", async () => {
    // The selection is two and the server says two. The sentence takes the
    // server's number on purpose: an all-or-nothing endpoint makes them equal,
    // and reading the local one would be the panel asserting an outcome it did
    // not observe.
    await openBoard([MINE_A, MINE_B]);
    await tick(MINE_A.title);
    await tick(MINE_B.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 2 nội dung" }));
    await confirm();

    await waitFor(() =>
      expect(screen.getByText("Duyệt thành công 2 nội dung.")).toBeInTheDocument(),
    );
    // And the selection is gone, so nobody presses it twice.
    expect(
      screen.queryByRole("region", { name: "Thao tác hàng loạt" }),
    ).not.toBeInTheDocument();
  });
});

describe("165. a conflict says that nothing was approved", () => {
  it("renders the server's sentence for a stale batch", async () => {
    await openBoard([MINE_A, MINE_B], {
      approveStatus: 409,
      approveBody: {
        error: {
          code: "pr_bulk_approval_stale",
          message:
            "Không thể duyệt vì 2 nội dung đã thay đổi. Không có nội dung nào được duyệt.",
          details: { reason: "moved", approved: 0, affected: [] },
        },
      },
    });
    await tick(MINE_A.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 1 nội dung" }));
    await confirm();

    await waitFor(() =>
      expect(
        screen.getAllByText(/Không có nội dung nào được duyệt/).length,
      ).toBeGreaterThan(0),
    );
    // No success sentence anywhere, and the selection is kept so the person can
    // look at what changed and try again.
    expect(screen.queryByText(/Duyệt thành công/)).not.toBeInTheDocument();
    expect(within(bar()).getByText("Đã chọn 1 nội dung")).toBeInTheDocument();
  });

  it("renders the server's sentence for a permission mismatch", async () => {
    await openBoard([MINE_A, MINE_B], {
      approveStatus: 403,
      approveBody: {
        error: {
          code: "pr_bulk_approval_forbidden",
          message:
            "Không thể duyệt vì quyền của bạn không còn áp dụng cho một hoặc nhiều nội dung. Không có nội dung nào được duyệt.",
          details: { reason: "out_of_grant_scope", approved: 0, affected: [] },
        },
      },
    });
    await tick(MINE_A.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 1 nội dung" }));
    await confirm();

    await waitFor(() =>
      expect(
        screen.getAllByText(/quyền của bạn không còn áp dụng/).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByText(/Duyệt thành công/)).not.toBeInTheDocument();
  });

  it("never shows a raw stack trace or an internal code", async () => {
    await openBoard([MINE_A], {
      approveStatus: 500,
      approveBody: { error: { code: "internal_error", message: "boom", details: {} } },
    });
    await tick(MINE_A.title);
    await userEvent.click(within(bar()).getByRole("button", { name: "Duyệt 1 nội dung" }));
    await confirm();

    await waitFor(() => expect(screen.getAllByText("boom").length).toBeGreaterThan(0));
    // The server's own sentence, and nothing structural around it.
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
    expect(screen.queryByText(/pr_bulk_approval/)).not.toBeInTheDocument();
  });
});

describe("166. the browser never fetches a queue to select it", () => {
  it("asks the server for ids and a count, and sends only ids", async () => {
    const stub = await openBoard([MINE_A, MINE_B], {
      eligible: { total: 79, content_ids: [MINE_A.id, MINE_B.id, MINE_C.id] },
    });

    const selection = calls(stub).filter((call) =>
      call.url.includes("/reviews/approvable"),
    );
    expect(selection.length).toBeGreaterThan(0);
    // The gate is pinned and the board's own filters ride along, so the answer
    // is about what is on screen. No `limit`, because the server's batch cap is
    // the server's to apply.
    const params = new URLSearchParams(selection[0].url.split("?")[1] ?? "");
    expect(params.get("gate")).toBe("TEAM_LEAD_REVIEW");
    expect(params.get("group")).toBe("EDITORIAL_REVIEW");
    expect(params.get("limit")).toBeNull();
  });
});
