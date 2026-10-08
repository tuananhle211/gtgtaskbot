/**
 * Step 1E.2 - the workspace tests. Ten, numbered 43-52.
 *
 * They continue the numbering of the Step 1E and 1E.1 suites and ask the
 * question this step exists for: **is the panel usable without becoming a second
 * authority?** Every assertion below is either "the browser stopped deciding
 * something" or "the browser stopped hiding something a person needs".
 *
 * What they do *not* test is layout. A test that asserts a Tailwind class is a
 * test of a string, and it would pass on a page that renders nothing. Where a
 * layout property is load-bearing - no horizontal scroller at 390px, the safe
 * area under the sticky bar - the test asserts the *mechanism* (no
 * `overflow-x-auto` wrapper around the lanes, `env(safe-area-inset-bottom)` in
 * the component that positions the bar) rather than a screenshot.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  CHANNEL_CATEGORIES,
  STAGE_ORDER,
  channelCategoryLabel,
  channelStatusLabel,
  stageLabel,
} from "@/lib/labels";
import { OPERATIONAL_GROUPS, columnLabel } from "@/lib/board";
import { Shell } from "@/components/shell";
import ContentBoardPage from "@/app/pr/content/page";
import ContentDetailPage from "@/app/pr/content/[id]/page";
import ChannelsPage from "@/app/pr/channels/page";
import LoginFailed from "@/app/auth/failed/page";
import {
  CONTENT,
  SESSION,
  VERSION,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
  urlStore,
  PR_CONTENT_DETAIL_FILES,
  readPrContentDetailSource,
} from "./helpers";

// Step 1F.2.2 put the board's filters in the URL, so the workspace reads
// `useSearchParams` and writes through `useRouter`. Both are stubbed here rather
// than in each test; `tests/content-views.test.tsx` is where the URL state itself
// is under test and drives these two properly.
//
// The stub navigates rather than recording: since Step 1F.2.3c1 the lifecycle
// group is a query parameter, so clicking a tab writes the URL and the page has
// to follow it - a mock that swallowed the write would leave test 44 clicking a
// tab that never opens.
const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);

// Every entry is a function that reads `URL_BAR` when it is *called*: this file
// imports the pages statically, so the factory runs before the constants above
// exist and touching one here is a TDZ error rather than a mock.
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
});

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const read = (relative: string) =>
  readFileSync(path.join(SRC, relative), "utf8");

const DASHBOARD = {
  stage_counts: STAGE_ORDER.map((stage) => ({
    stage,
    count: stage === "IDEA" ? 2 : 0,
  })),
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: ["PR_TEAM_LEAD_REVIEW"],
  recent_content: [],
};

const IDEA_ITEM = {
  ...CONTENT,
  workflow_stage: "IDEA",
  title: "Chăm sóc sau sinh",
};

/** One item in the *other* tab, so switching groups has something to draw. */
const GATED_ITEM = {
  ...CONTENT,
  id: "99999999-9999-9999-9999-999999999999",
  code: "CNT-2026-000009",
  workflow_stage: "TEAM_LEAD_REVIEW",
  title: "Bài chờ duyệt",
};

const BRAND = { id: CONTENT.brand_id, code: "APEXMED", name: "Apexmed" };

const CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-TT",
  name: "TikTok Apexmed",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: CONTENT.brand_id,
  platform_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  tier: null,
  url: null,
  platform_code: "TIKTOK",
  policy_grounded_platform: true,
  // Step 1F.2.4a. The identity and data badge the server now sends with every
  // channel. Labels included: the browser holds no copy of these tables.
  platform: "TIKTOK",
  platform_label: "TikTok",
  platform_name: "TikTok",
  handle: null,
  external_id: null,
  metrics_status: "DISCONNECTED",
  metrics_status_label: "Chưa có dữ liệu",
  latest_captured_at: null,
  followers: null,
  days_since_capture: null,
};

/** An empty metrics panel, which is what a channel with no readings has. */
const NO_METRICS = {
  channel_id: CHANNEL.id,
  status: "DISCONNECTED",
  status_label: "Chưa có dữ liệu",
  latest: null,
  previous: null,
  trend: null,
  history: [],
  total: 0,
  limit: 30,
  offset: 0,
  days_since_capture: null,
  analytics: null,
  can_record_metrics: false,
  has_history: false,
};

/** The board's platform picker reads this. Named for the board's own channel. */
const BOARD_PLATFORM = {
  id: CHANNEL.platform_id,
  code: "TIKTOK",
  name: "TikTok",
  status: "ACTIVE",
  policy_grounded: true,
};

/** One filtered page, in the envelope `/contents/board` returns. */
const boardBody = (items: unknown[]) => ({
  items,
  total: items.length,
  scope: "MY_ACTIONS",
  stage_counts: STAGE_ORDER.map((stage) => ({
    stage,
    count: items.filter(
      (item) => (item as { workflow_stage: string }).workflow_stage === stage,
    ).length,
  })),
  production_state_counts: [
    "WAITING_FOR_PRODUCER",
    "READY_FOR_PRODUCTION",
    "IN_PRODUCTION",
    "IN_INTERNAL_REVIEW",
  ].map((production_state) => ({
    production_state,
    count: items.filter(
      (item) =>
        (item as { production_state?: string }).production_state ===
        production_state,
    ).length,
  })),
  limit: 60,
  offset: 0,
});

const boardRoutes = (
  items: unknown[] = [IDEA_ITEM],
  brands: unknown[] = [BRAND],
) => [
  { match: "/api/pr/dashboard", body: DASHBOARD },
  { match: "/api/pr/brands", body: brands },
  { match: "/api/pr/channels", body: [CHANNEL] },
  { match: "/api/pr/platforms", body: [BOARD_PLATFORM] },
  {
    match: "/api/pr/people",
    body: [
      {
        user_id: SESSION.user_id,
        full_name: SESSION.full_name,
        role: SESSION.role,
      },
    ],
  },
  // Before `/api/pr/contents`, which would otherwise swallow it: `stubFetch`
  // matches on substring, in order.
  { match: "/api/pr/contents/board", body: boardBody(items) },
  { match: "/api/pr/contents", body: items },
];

const detailRoutes = (
  actions: Array<Record<string, unknown>>,
  stage = "IDEA",
) => {
  const content = { ...CONTENT, workflow_stage: stage };
  return [
    {
      match: "/available-actions",
      body: {
        content_id: CONTENT.id,
        workflow_stage: stage,
        available_actions: actions,
      },
    },
    {
      match: "/review-context",
      body: {
        content,
        current_version: VERSION,
        targets: [],
        tasks: [],
        ai_review: null,
        ai_reviews_for_version: [],
        approvals: [],
      },
    },
    { match: "/versions", method: "GET", body: [VERSION] },
    {
      match: "/ai-review",
      body: { run: null, review: null, active: false, can_retry: false },
    },
    { match: "/api/pr/people", body: [] },
    {
      match: "/api/pr/contents/",
      body: {
        content,
        current_version: VERSION,
        targets: [
          {
            id: "66666666-6666-6666-6666-666666666666",
            channel_id: "77777777-7777-7777-7777-777777777777",
            target_publish_at: null,
            status: "PLANNED",
            adaptation_note: null,
            channel_code: "CH-0001",
            channel_name: "Facebook Apexmed",
          },
        ],
        brand: { id: CONTENT.brand_id, code: "APEXMED", name: "Apexmed" },
      },
    },
  ];
};

const transition = (target: string, emphasis: string) => ({
  action: "TRANSITION",
  target_stage: target,
  decision: null,
  emphasis,
});

// --- 43-45: the shell and the board ----------------------------------------

describe("43. the global header shows a person, not a capability model", () => {
  it("never prints a raw PR_* code", async () => {
    stubFetch([
      {
        match: "/api/auth/session",
        body: {
          ...SESSION,
          full_name: "Phương Nhung",
          role: "OWNER",
          // Every capability an OWNER can hold. None of them may reach the
          // header: "PR_CONTENT_CANCEL · PR_CONTENT_EDIT · PR_CONTENT_TRANSITION"
          // is the authorization model's vocabulary shown to somebody who wanted
          // to know whose account they were in.
          capabilities: [
            "PR_CONTENT_CANCEL",
            "PR_CONTENT_CREATE",
            "PR_CONTENT_EDIT",
            "PR_CONTENT_TRANSITION",
            "PR_TEAM_LEAD_REVIEW",
          ],
        },
      },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() =>
      expect(screen.getByText(/Phương Nhung/)).toBeInTheDocument(),
    );

    const header = document.querySelector("header");
    expect(header?.textContent).toContain("TasksBot · Creative Ops");
    // The brand mark is the inline TasksBot logo, decorative next to the name.
    const mark = header?.querySelector("svg[data-logo-mark]");
    expect(mark).not.toBeNull();
    expect(mark).toHaveAttribute("aria-hidden", "true");
    // The role label is the server's `ROLE_LABELS` word for it - the same one
    // Telegram prints. OWNER is workspace ownership, "Chủ sở hữu" - never the
    // department head's title.
    expect(header?.textContent).toContain("Chủ sở hữu");
    expect(header?.textContent).not.toContain("Trưởng phòng");
    for (const code of [
      "PR_CONTENT_CANCEL",
      "PR_CONTENT_EDIT",
      "PR_CONTENT_TRANSITION",
      "PR_",
    ]) {
      expect(header?.textContent, code).not.toContain(code);
    }
    // And nowhere else on the shell either.
    expect(document.body.textContent).not.toContain("PR_CONTENT");
  });

  it("keeps the raw codes out of the component that renders the header", () => {
    const shell = read("components/shell.tsx");
    // The Permissions page is where grants are administered and where their
    // codes belong; the frame around every screen is not.
    expect(shell).not.toContain("capabilityLabel");
    expect(shell).not.toContain("session.data.capabilities");
  });
});

describe("the legal pages are reachable from the panel without crowding it", () => {
  it("puts both links in the footer and neither in the primary nav", async () => {
    // A PR member: the PR screens appear once /api/units/me says so (an
    // account with no stream gets only the shared screens).
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      {
        match: "/api/units/me",
        body: {
          units: [
            {
              code: "PR",
              label: "Luồng PR",
              role: "MEMBER",
              role_label: "Thành viên",
              is_lead: false,
              member_code: null,
              personal_nas_url: null,
              settings: {},
            },
          ],
          default_unit: "PR",
          can_view_all: false,
          can_admin: [],
        },
      },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() =>
      expect(screen.getByText(/Le Trưởng Nhóm/)).toBeInTheDocument(),
    );

    const legal = screen.getByRole("navigation", { name: "Thông tin pháp lý" });
    expect(
      within(legal).getByRole("link", { name: "Điều khoản sử dụng" }),
    ).toHaveAttribute("href", "/terms");
    expect(
      within(legal).getByRole("link", { name: "Chính sách quyền riêng tư" }),
    ).toHaveAttribute("href", "/privacy");

    // The point of the footer: the primary strip carries **working
    // destinations only**. An entry on a strip that already scrolls on a phone
    // costs every screen room, and a link nobody opens twice a year has not
    // earned it.
    //
    // Asserted as the *set* rather than a count. The count was a proxy for "no
    // legal link crept in", and a proxy that has to be edited every time the
    // product grows a module stops testing anything - M1 added "Công việc",
    // which is exactly the kind of entry that belongs here.
    const primary = screen.getByRole("navigation", {
      name: "Điều hướng chính",
    });
    await waitFor(() =>
      expect(within(primary).getByRole("link", { name: /Công việc/ })).toBeInTheDocument(),
    );
    const destinations = within(primary)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(destinations).toEqual([
      "/dashboard",
      "/tasks",
      "/orders/new",
      "/pr/work",
      "/pr/tasks",
      "/pr/channels",
      "/pr/permissions",
      "/pr/reports",
    ]);
    for (const href of ["/terms", "/privacy"]) {
      expect(destinations).not.toContain(href);
    }
  });

  it("keeps them reachable for somebody who is not signed in", async () => {
    // /terms and /privacy are public, and the visit most likely to need them is
    // the one where a login link has just expired. The footer is outside the
    // `session.data` branch that gates <main> for exactly that reason.
    stubFetch([
      {
        match: "/api/auth/session",
        status: 401,
        body: { detail: "hết phiên" },
      },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() =>
      expect(screen.getByText(/Bạn cần đăng nhập lại/)).toBeInTheDocument(),
    );
    // <main> is gated on a session; the footer must not be.
    expect(document.querySelector("main")).toBeNull();

    const legal = screen.getByRole("navigation", { name: "Thông tin pháp lý" });
    expect(
      within(legal).getByRole("link", { name: "Điều khoản sử dụng" }),
    ).toBeInTheDocument();
    expect(
      within(legal).getByRole("link", { name: "Chính sách quyền riêng tư" }),
    ).toBeInTheDocument();
  });
});

describe("44. the board groups the workflow instead of unrolling it", () => {
  it("shows one group's columns at a time, not all thirteen stages", async () => {
    // Both tabs have work in them: a group with nothing in it draws its own
    // sentence rather than a row of empty columns, which is Step 1F.2.3c's
    // empty state and not what this test is about.
    stubFetch(boardRoutes([IDEA_ITEM, GATED_ITEM]));
    renderWithQuery(<ContentBoardPage />);
    await waitFor(() =>
      expect(screen.getByText("Chăm sóc sau sinh")).toBeInTheDocument(),
    );

    // The groups are tabs. Step 1F.2.3c made them five, `Đã hủy` included.
    for (const group of OPERATIONAL_GROUPS) {
      expect(
        screen.getByRole("tab", { name: new RegExp(group.label) }),
      ).toBeInTheDocument();
    }
    // Only the open group's columns are on the page. "Chờ duyệt Trưởng nhóm"
    // belongs to another tab, so it must not be rendered - that is the whole
    // point of not having one 3400px-wide strip.
    for (const column of OPERATIONAL_GROUPS[0].columns) {
      expect(
        screen.getByRole("region", { name: columnLabel(column) }),
      ).toBeInTheDocument();
    }
    for (const column of OPERATIONAL_GROUPS[1].columns) {
      expect(
        screen.queryByRole("region", { name: columnLabel(column) }),
      ).not.toBeInTheDocument();
    }

    // And switching tabs swaps them.
    await userEvent.click(
      screen.getByRole("tab", {
        name: new RegExp(OPERATIONAL_GROUPS[1].label),
      }),
    );
    expect(
      screen.getByRole("region", {
        name: columnLabel(OPERATIONAL_GROUPS[1].columns[0]),
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: stageLabel("IDEA") }),
    ).not.toBeInTheDocument();
  });

  it("keeps CANCELLED out of the forward path", () => {
    // Its own terminal group, last in the strip - not a stage inside one of the
    // four phases, where dead cards would sit in everybody's way.
    for (const group of OPERATIONAL_GROUPS.slice(0, -1)) {
      expect(group.stages, group.key).not.toContain("CANCELLED");
    }
    expect(OPERATIONAL_GROUPS.at(-1)!.stages).toEqual(["CANCELLED"]);
  });

  it("has no page-level horizontal scroller anywhere in the layout", () => {
    // The old board was a single `overflow-x-auto` flex row of thirteen 256px
    // columns. At 390px that made the *page* draggable sideways, which every
    // other screen in the app inherited.
    const board = read("app/pr/content/page.tsx");
    expect(board).not.toContain("overflow-x-auto");
    expect(board).toContain("grid");
    // The body says so too, so a future mistake clips rather than scrolls.
    expect(readFileSync(path.join(SRC, "app/globals.css"), "utf8")).toContain(
      "overflow-x: hidden",
    );
  });
});

describe("45. a card leads with the title and mutes the code", () => {
  it("puts the title first and the CNT code second", async () => {
    stubFetch(boardRoutes());
    renderWithQuery(<ContentBoardPage />);
    const title = await screen.findByText("Chăm sóc sau sinh");
    const card = title.closest("a");
    expect(card).not.toBeNull();

    const code = within(card as HTMLElement).getByText(CONTENT.code);
    // Title before code in reading order...
    expect(
      title.compareDocumentPosition(code) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // ...and the code is muted metadata rather than the headline.
    expect(code.className).toContain("text-[var(--text-muted)]");
    expect(title.className).toContain("font-medium");
    // The whole card is the link, so a tap anywhere on it opens the item.
    expect(card?.getAttribute("href")).toBe(`/pr/content/${CONTENT.id}`);
  });
});

// --- 46-49: the detail page ------------------------------------------------

describe("46. the detail page does not offer every workflow stage", () => {
  it("renders one next step, not thirteen buttons", async () => {
    stubFetch(detailRoutes([transition("BRIEFING", "PRIMARY")]));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByText("Việc cần làm tiếp")).toBeInTheDocument(),
    );

    expect(
      screen.getAllByRole("button", { name: "Chuyển sang Brief" }).length,
    ).toBeGreaterThan(0);
    // Every other stage label the old panel drew. None of them is a button now.
    for (const stage of STAGE_ORDER.filter((code) => code !== "BRIEFING")) {
      expect(
        screen.queryByRole("button", { name: stageLabel(stage) }),
        stage,
      ).not.toBeInTheDocument();
    }
  });

  it("renders exactly what the server listed, and nothing it did not", async () => {
    // The same page, same stage, a different answer from the server: the panel
    // follows the response rather than the stage.
    stubFetch(detailRoutes([]));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByText(/Chưa thể chuyển bước lúc này/),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Chuyển sang Brief" }),
    ).not.toBeInTheDocument();
  });
});

describe("47. dangerous actions are separated from the forward one", () => {
  it("hides cancel behind Thao tác khác and asks for confirmation", async () => {
    const fetchMock = stubFetch([
      {
        match: "/transition",
        method: "POST",
        body: { content: CONTENT, current_version: VERSION, targets: [] },
      },
      ...detailRoutes([
        transition("BRIEFING", "PRIMARY"),
        transition("CANCELLED", "DANGER"),
      ]),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByText("Việc cần làm tiếp")).toBeInTheDocument(),
    );

    // Not in the forward row: "Hủy nội dung" next to "Chuyển sang Brief" is a
    // mis-click that ends somebody's work.
    expect(
      screen.queryByRole("button", { name: "Hủy nội dung" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Thao tác khác/ }),
    );

    const cancel = screen.getByRole("button", { name: "Hủy nội dung" });
    await userEvent.click(cancel);
    // One press opens the shared dialog; nothing has been sent. Step 1F.2.8
    // replaced this panel's own inline "Xác nhận: X / Thôi" pair with it, and
    // the dialog is destructive-styled and says what cancelling does.
    const calls = (fetchMock as unknown as { calls: Array<{ method: string }> })
      .calls;
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
    expect(dialog().getByText("Hủy nội dung này?")).toBeInTheDocument();

    await confirm();
    await waitFor(() =>
      expect(calls.filter((call) => call.method === "POST").length).toBe(1),
    );
  });
});

describe("48. review actions come from the server's answer", () => {
  it("offers no decision when the server offers none", async () => {
    // Somebody without the grant for this gate. `PrApprovalService` would refuse
    // them; the panel says so before they press anything, and - this is the part
    // that matters - it does not work that out for itself.
    stubFetch(detailRoutes([], "TEAM_LEAD_REVIEW"));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));
    await waitFor(() =>
      expect(screen.getByText(/không chờ bạn duyệt/)).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Duyệt" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Từ chối" }),
    ).not.toBeInTheDocument();
  });

  it("offers approve and revision up front, and reject only behind the fold", async () => {
    stubFetch(
      detailRoutes(
        [
          {
            action: "APPROVAL",
            target_stage: null,
            decision: "APPROVED",
            emphasis: "PRIMARY",
          },
          {
            action: "APPROVAL",
            target_stage: null,
            decision: "REVISION_REQUIRED",
            emphasis: "SECONDARY",
          },
          {
            action: "APPROVAL",
            target_stage: null,
            decision: "REJECTED",
            emphasis: "DANGER",
          },
        ],
        "TEAM_LEAD_REVIEW",
      ),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Duyệt" }).length,
      ).toBeGreaterThan(0),
    );
    expect(
      screen.getByRole("button", { name: "Yêu cầu sửa" }),
    ).toBeInTheDocument();
    // Rejecting cancels the content at every gate, so it lives with cancel.
    expect(
      screen.queryByRole("button", { name: "Từ chối" }),
    ).not.toBeInTheDocument();
    // The reviewer's comment box is labelled as a comment, not as a note.
    expect(screen.getByText(/Nhận xét/)).toBeInTheDocument();
  });
});

describe("49. mobile actions clear the home indicator", () => {
  it("positions the sticky bar with the safe-area inset", () => {
    const source = read("components/pr.tsx");
    // Without this the primary action sits under the iPhone home indicator,
    // where a tap either does nothing or dismisses the browser.
    expect(source).toContain("env(safe-area-inset-bottom)");
    expect(source).toContain("sticky bottom-0");
    // The inset is only a real number when the viewport opts into it.
    expect(read("app/layout.tsx")).toContain('viewportFit: "cover"');
  });

  it("keeps touch targets and form text at usable sizes", () => {
    const components = read("components/pr.tsx");
    // ~44px. Buttons defined here are the ones every screen uses.
    expect(components).toContain("min-h-11");
    const css = readFileSync(path.join(SRC, "app/globals.css"), "utf8");
    // iOS zooms the page when a focused input is under 16px and never zooms
    // back out - which strands somebody at 130% with a sideways scrollbar.
    expect(css).toContain("font-size: 16px");
  });
});

// --- 50-52: the words ------------------------------------------------------

describe("50. the auth-failed page explains both ordinary causes", () => {
  it("names expiry and single use, and points at /web", () => {
    renderWithQuery(<LoginFailed />);
    expect(screen.getByText(/hết hạn/)).toBeInTheDocument();
    expect(screen.getByText(/chỉ dùng được một lần/)).toBeInTheDocument();
    expect(screen.getByText("/web")).toBeInTheDocument();
    // And it explains the Telegram in-app browser, which is why somebody's
    // session vanishes when they switch to Chrome.
    expect(screen.getByText(/Chrome/)).toBeInTheDocument();
  });

  it("still says nothing about which failure occurred", () => {
    const source = read("app/auth/failed/page.tsx");
    // Naming the two ordinary causes together is guidance. Reporting *which* one
    // applied would answer a probe's question.
    for (const leak of ["token", "hash", "session_id", "revoked_at"]) {
      expect(source.toLowerCase()).not.toContain(leak);
    }
  });
});

describe("51. stage wording is Vietnamese and lives in one table", () => {
  it("maps every canonical stage without scattering the map", () => {
    expect(stageLabel("IDEA")).toBe("Ý tưởng");
    expect(stageLabel("READY_TO_PUBLISH")).toBe("Sẵn sàng đăng");
    expect(stageLabel("CANCELLED")).toBe("Đã hủy");
    // An unknown code renders as itself - a deployment mismatch should be
    // visible, not hidden behind a dash.
    expect(stageLabel("NOT_A_STAGE")).toBe("NOT_A_STAGE");

    // No screen defines its own stage labels.
    for (const file of [
      "app/pr/content/page.tsx",
      ...PR_CONTENT_DETAIL_FILES,
      "app/pr/page.tsx",
      "components/pr.tsx",
    ]) {
      expect(read(file), file).not.toContain("Ý tưởng");
      expect(read(file), file).not.toContain("Sẵn sàng đăng");
    }
  });
});

describe("52. the panel still decides nothing", () => {
  it("has no transition, gate or capability table in any component", () => {
    for (const file of [
      "app/pr/content/page.tsx",
      ...PR_CONTENT_DETAIL_FILES,
      "components/pr.tsx",
      "components/shell.tsx",
      "lib/api.ts",
    ]) {
      const source = read(file);
      for (const forbidden of [
        "CONTENT_TRANSITIONS",
        "APPROVAL_CAPABILITIES",
        "STAGE_APPROVAL_GATES",
        "canApprove",
        "hasPermission",
        "EDITABLE_STAGES",
      ]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
    // Whether editing is possible is read off the server's action list, not off
    // the stage - `EDITABLE_STAGES` is the Python table that decides it.
    expect(readPrContentDetailSource()).toContain("EDIT_CONTENT");
  });
});

// --- 53-56: Step 1E.2.1, brands by name and actions that work ---------------

describe("53. creating content asks for a brand, not for a UUID", () => {
  it("offers brand names in a picker", async () => {
    stubFetch(boardRoutes());
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Tạo nội dung/ }),
    );

    const picker = await screen.findByRole("combobox", { name: /Thương hiệu/ });
    expect(
      within(picker).getByRole("option", { name: "Apexmed" }),
    ).toBeInTheDocument();

    // The thing this patch exists to remove.
    expect(screen.queryByPlaceholderText(/UUID/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Brand ID/i)).not.toBeInTheDocument();
    // The visible label is the name; the id is not shown anywhere on the form.
    const form = picker.closest("form");
    expect(form?.textContent).not.toContain(CONTENT.brand_id);
  });

  it("submits the chosen brand's id to the unchanged create API", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/contents",
        method: "POST",
        status: 201,
        body: {
          content: IDEA_ITEM,
          current_version: VERSION,
          targets: [],
          brand: BRAND,
        },
      },
      ...boardRoutes(),
    ]);
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Tạo nội dung/ }),
    );

    await userEvent.type(
      screen.getByRole("textbox", { name: /Tiêu đề/ }),
      "Bài mới",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Thương hiệu/ }),
      BRAND.id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Người phụ trách/ }),
      SESSION.user_id,
    );
    // Step 1F.2.3e: new content must have a format.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Loại nội dung/ }),
      "SHORT_VIDEO_SCRIPT",
    );
    // Step 1F.2: a channel is required, and a grounded one needs its mode.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Chọn kênh" }),
      CHANNEL.id,
    );
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Hình thức đăng cho/ }),
      "ORGANIC",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo nội dung" }));

    const calls = (
      fetchMock as unknown as {
        calls: Array<{ method: string; body: unknown }>;
      }
    ).calls;
    const created = calls.find((call) => call.method === "POST");
    expect(created).toBeDefined();
    // A person picked a name; the mutation still carries the id it always did.
    expect((created?.body as { brand_id: string }).brand_id).toBe(BRAND.id);
  });

  it("refuses to show a form that cannot succeed when no brand exists", async () => {
    stubFetch(boardRoutes([IDEA_ITEM], []));
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Tạo nội dung/ }),
    );

    // Every content item belongs to a brand, so with none on file there is
    // nothing to create against - and the fix is an admin task, not a UUID to
    // guess at.
    await waitFor(() =>
      expect(
        screen.getByText(/Chưa có thương hiệu nào đang hoạt động/),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("textbox", { name: /Tiêu đề/ }),
    ).not.toBeInTheDocument();
  });
});

describe("54. brands and channels are shown by name", () => {
  it("names the brand on a board card", async () => {
    stubFetch(boardRoutes());
    renderWithQuery(<ContentBoardPage />);
    const title = await screen.findByText("Chăm sóc sau sinh");
    const card = title.closest("a") as HTMLElement;
    expect(within(card).getByText(/Apexmed/)).toBeInTheDocument();
    // The id is nowhere on the card.
    expect(card.textContent).not.toContain(CONTENT.brand_id);
  });

  it("names the brand and channel in the detail header", async () => {
    stubFetch(detailRoutes([transition("BRIEFING", "PRIMARY")]));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByText("Apexmed · Facebook Apexmed"),
      ).toBeInTheDocument(),
    );
    expect(document.body.textContent).not.toContain(CONTENT.brand_id);
  });
});

describe("55. the panel renders only what the server allows", () => {
  it("shows a real state when the server withholds the forward move", async () => {
    // Step 1E.2.1: at PUBLISHED with no metric snapshot, the server withholds
    // MEASURED. The panel says so rather than drawing a button that 409s.
    stubFetch(detailRoutes([transition("CANCELLED", "DANGER")], "PUBLISHED"));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByText(/Chưa thể chuyển bước lúc này/),
      ).toBeInTheDocument(),
    );
    // Not offered, and not predicted back into existence by the browser.
    expect(
      screen.queryByRole("button", { name: stageLabel("MEASURED") }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ghi nhận đã đo hiệu quả/ }),
    ).not.toBeInTheDocument();
  });

  it("has no prerequisite check anywhere in the browser", () => {
    for (const file of [
      ...PR_CONTENT_DETAIL_FILES,
      "app/pr/content/page.tsx",
      "components/pr.tsx",
      "lib/api.ts",
    ]) {
      const source = read(file);
      for (const forbidden of [
        "metric_snapshot",
        "hasMetrics",
        "ai_review !==",
        "latest_gating",
        "canMeasure",
        "isApprovable",
      ]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });
});

describe("56. the brand list is read, never decided", () => {
  it("has no status filtering in the browser", () => {
    // Which brands may be chosen is the server's decision - it returns active
    // ones. A client-side `status === "ACTIVE"` would be a second answer.
    for (const file of ["app/pr/content/page.tsx", "lib/api.ts"]) {
      const source = read(file);
      expect(source, file).not.toContain("INACTIVE");
      expect(source, file).not.toMatch(/status\s*===\s*"ACTIVE"/);
    }
  });
});

// --- 57-64: Step 1F, the AI review panel ------------------------------------

const aiState = (over: Record<string, unknown> = {}) => ({
  run: null,
  review: null,
  active: false,
  can_retry: false,
  policy_packs: [],
  policy_citations: [],
  ...over,
});

const aiRun = (status: string, over: Record<string, unknown> = {}) => ({
  id: "88888888-8888-8888-8888-888888888888",
  status,
  trigger: "AUTO",
  content_version_id: VERSION.id,
  attempt_count: 1,
  outcome: null,
  model_name: null,
  prompt_version: "pr-full-review-v1",
  error_code: null,
  created_at: "2026-08-09T03:00:00+00:00",
  started_at: null,
  finished_at: null,
  ...over,
});

const aiReview = (result: string, over: Record<string, unknown> = {}) => ({
  id: "99999999-9999-9999-9999-999999999999",
  reviewed_version: VERSION.version_no,
  review_type: "FULL_REVIEW",
  result,
  score: null,
  summary: "Nội dung nhìn chung ổn.",
  issues: [],
  suggestions: [],
  policy_flags: [],
  model_name: "gpt-x",
  model_version: null,
  prompt_version: "pr-full-review-v1",
  reviewed_at: "2026-08-09T03:05:00+00:00",
  created_at: "2026-08-09T03:05:00+00:00",
  ...over,
});

/** The detail routes with an explicit `/ai-review` payload. */
const aiRoutes = (ai: Record<string, unknown>) => [
  { match: "/ai-review", body: ai },
  ...detailRoutes([], "AI_REVIEW"),
];

const openReviewTab = async () => {
  renderWithQuery(<ContentDetailPage />);
  await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));
};

describe("57. the panel shows what the worker is doing", () => {
  it("says queued while the run waits for a worker", async () => {
    stubFetch(aiRoutes(aiState({ run: aiRun("QUEUED"), active: true })));
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Đang chờ xử lý…")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Đạt/)).not.toBeInTheDocument();
  });

  it("says analysing while it runs", async () => {
    stubFetch(aiRoutes(aiState({ run: aiRun("RUNNING"), active: true })));
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Đang phân tích nội dung…")).toBeInTheDocument(),
    );
  });
});

describe("58. a finished review is rendered as its verdict", () => {
  it("shows PASS", async () => {
    stubFetch(
      aiRoutes(
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS" }),
          review: aiReview("PASS"),
        }),
      ),
    );
    await openReviewTab();
    await waitFor(() => expect(screen.getByText("Đạt")).toBeInTheDocument());
    expect(screen.getByText(/Nội dung nhìn chung ổn/)).toBeInTheDocument();
    // Provenance survives Step 1F: a finding with no record of what produced it
    // cannot be re-read later.
    expect(screen.getByText(/pr-full-review-v1/)).toBeInTheDocument();
  });

  it("shows PASS_WITH_WARNINGS", async () => {
    stubFetch(
      aiRoutes(
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS_WITH_WARNINGS" }),
          review: aiReview("PASS_WITH_WARNINGS", {
            issues: ["Cam kết quá mạnh"],
          }),
        }),
      ),
    );
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Đạt, có lưu ý")).toBeInTheDocument(),
    );
    expect(screen.getByText("Cam kết quá mạnh")).toBeInTheDocument();
  });

  it("shows REVISION_REQUIRED", async () => {
    stubFetch(
      aiRoutes(
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "REVISION_REQUIRED" }),
          review: aiReview("REVISION_REQUIRED"),
        }),
      ),
    );
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Cần sửa")).toBeInTheDocument(),
    );
    // And it still says the machine does not approve anything.
    expect(screen.getByText(/không thay thế người duyệt/)).toBeInTheDocument();
  });
});

describe("59. a failure is safe to read and retryable when the server allows", () => {
  it("offers the retry the server offered, and never a provider error", async () => {
    const fetchMock = stubFetch([
      {
        match: "/ai-review/retry",
        method: "POST",
        status: 202,
        body: aiState({
          run: aiRun("QUEUED", { trigger: "MANUAL_RETRY" }),
          active: true,
        }),
      },
      ...aiRoutes(
        aiState({
          run: aiRun("FAILED", {
            error_code: "llm_error",
            finished_at: "2026-08-09T03:02:00+00:00",
          }),
          can_retry: true,
        }),
      ),
    ]);
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Không thể xử lý")).toBeInTheDocument(),
    );
    // The content is still where it was, and the panel says so.
    expect(screen.getByText(/vẫn đang ở bước AI review/)).toBeInTheDocument();
    // The raw code the API sent is never put on screen.
    expect(document.body.textContent).not.toContain("llm_error");

    await userEvent.click(
      screen.getByRole("button", { name: "Chạy lại AI review" }),
    );
    const calls = (
      fetchMock as unknown as { calls: Array<{ url: string; method: string }> }
    ).calls;
    expect(
      calls.some(
        (call) =>
          call.method === "POST" && call.url.includes("/ai-review/retry"),
      ),
    ).toBe(true);
  });

  it("does not offer a retry the server withheld", async () => {
    stubFetch(
      aiRoutes(
        aiState({
          run: aiRun("FAILED", { error_code: "timed_out" }),
          can_retry: false,
        }),
      ),
    );
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Không thể xử lý")).toBeInTheDocument(),
    );
    // Authorization is not the browser's to work out. No button, and pressing
    // one anyway would be refused by the route.
    expect(
      screen.queryByRole("button", { name: /Chạy lại AI review/ }),
    ).not.toBeInTheDocument();
  });
});

describe("60. polling runs only while the server says something is active", () => {
  it("keeps asking while a run is active", async () => {
    const fetchMock = stubFetch(
      aiRoutes(aiState({ run: aiRun("RUNNING"), active: true })),
    );
    await openReviewTab();
    await waitFor(() =>
      expect(screen.getByText("Đang phân tích nội dung…")).toBeInTheDocument(),
    );

    const count = () =>
      (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.filter(
        (call) => call.url.includes("/ai-review"),
      ).length;
    const before = count();
    // The interval is four seconds; wait past one tick.
    await waitFor(() => expect(count()).toBeGreaterThan(before), {
      timeout: 6000,
    });
  }, 10000);

  it("stops once the state is terminal", async () => {
    const fetchMock = stubFetch(
      aiRoutes(
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS" }),
          review: aiReview("PASS"),
        }),
      ),
    );
    await openReviewTab();
    await waitFor(() => expect(screen.getByText("Đạt")).toBeInTheDocument());

    const count = () =>
      (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.filter(
        (call) => call.url.includes("/ai-review"),
      ).length;
    const settled = count();
    await new Promise((resolve) => setTimeout(resolve, 5000));
    // A finished review is not re-fetched for ever. `active: false` is the
    // server's word and the poller takes it.
    expect(count()).toBe(settled);
  }, 10000);
});

describe("61. the panel decides nothing about the review", () => {
  it("derives no outcome and knows no terminal-status list", () => {
    const source = readPrContentDetailSource();
    for (const forbidden of [
      "BLOCKER",
      "deriveOutcome",
      "severity ===",
      // The list of terminal statuses belongs to the server; a copy here would
      // keep polling for ever the first time a status was added.
      'status === "SUCCEEDED" || ',
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
    // Polling is driven by the server's own flag.
    expect(source).toContain("active");
    expect(source).toContain("can_retry");
  });
});

// --- 62-66: Step 1F.1, policy grounding -------------------------------------

const target = (over: Record<string, unknown> = {}) => ({
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  channel_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  target_publish_at: null,
  status: "PLANNED",
  adaptation_note: null,
  channel_code: "CH-TT",
  channel_name: "TikTok Apexmed",
  distribution_mode: "UNSPECIFIED",
  policy_grounded_platform: true,
  platform_code: "TIKTOK",
  ...over,
});

/** Detail routes carrying targets and an explicit ai-review payload. */
const policyRoutes = (
  targets: Array<Record<string, unknown>>,
  ai: Record<string, unknown> = aiState(),
  stage = "SCRIPTING",
) => {
  const content = { ...CONTENT, workflow_stage: stage };
  return [
    {
      match: "/available-actions",
      body: {
        content_id: CONTENT.id,
        workflow_stage: stage,
        available_actions: [],
      },
    },
    { match: "/ai-review", body: ai },
    {
      match: "/review-context",
      body: {
        content,
        current_version: VERSION,
        targets: [],
        tasks: [],
        ai_review: null,
        ai_reviews_for_version: [],
        approvals: [],
      },
    },
    { match: "/versions", method: "GET", body: [VERSION] },
    { match: "/api/pr/people", body: [] },
    {
      match: "/api/pr/contents/",
      body: { content, current_version: VERSION, targets, brand: null },
    },
  ];
};

describe("62. organic vs paid is chosen, never guessed", () => {
  it("offers the two real modes and never UNSPECIFIED as a choice", async () => {
    stubFetch(policyRoutes([target()]));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sửa kênh" }),
    );

    const picker = await screen.findByRole("combobox", {
      name: /TikTok Apexmed/,
    });
    const options = within(picker)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("Organic");
    expect(options).toContain("Quảng cáo trả phí");
    // "Chưa xác định" is a starting state, not something to pick.
    expect(
      within(picker).getByRole("option", { name: "Chưa xác định" }),
    ).toBeDisabled();
    // No raw enum reaches a person.
    expect(document.body.textContent).not.toContain("PAID_AD");
    expect(document.body.textContent).not.toContain("UNSPECIFIED");
  });

  it("submits the chosen mode to the server", async () => {
    const fetchMock = stubFetch([
      {
        match: "/targets/",
        method: "PATCH",
        body: {
          content: CONTENT,
          current_version: VERSION,
          targets: [],
          brand: null,
        },
      },
      ...policyRoutes([target()]),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sửa kênh" }),
    );
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /TikTok Apexmed/ }),
      "PAID_AD",
    );
    const calls = (
      fetchMock as unknown as {
        calls: Array<{ url: string; method: string; body: unknown }>;
      }
    ).calls;
    // Step 1F.2.8. Choosing is not sending: this field decides which policy
    // pack the AI review is run against, so it confirms first and the dialog
    // says which mode and what follows from it.
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    expect(dialog().getByText(/Đổi hình thức đăng/)).toBeInTheDocument();
    await confirm();

    await waitFor(() =>
      expect(calls.some((call) => call.method === "PATCH")).toBe(true),
    );
    const sent = calls.find((call) => call.method === "PATCH");
    expect(sent?.body).toEqual({ distribution_mode: "PAID_AD" });
  });

  it("says plainly that AI review is blocked until the mode is set", async () => {
    stubFetch(policyRoutes([target()]));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sửa kênh" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/Cần chọn Organic hay Quảng cáo trả phí/),
      ).toBeInTheDocument(),
    );
    // And the server withheld the action, which the panel does not second-guess.
    expect(
      screen.queryByRole("button", { name: /Gửi đi AI review/ }),
    ).not.toBeInTheDocument();
  });

  it("does not ask for a mode on an unsupported platform", async () => {
    stubFetch(
      policyRoutes([
        target({
          policy_grounded_platform: false,
          platform_code: "YOUTUBE",
          channel_name: "YouTube",
        }),
      ]),
    );
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sửa kênh" }),
    );
    // Which platforms are grounded is the server's answer, carried on the
    // target - the browser matches no platform codes of its own.
    expect(
      screen.queryByRole("combobox", { name: /YouTube/ }),
    ).not.toBeInTheDocument();
  });
});

describe("63. a grounded review names the pack it used", () => {
  it("shows platform, mode and pack label as secondary metadata", async () => {
    stubFetch(
      policyRoutes(
        [target({ distribution_mode: "PAID_AD" })],
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS_WITH_WARNINGS" }),
          review: aiReview("PASS_WITH_WARNINGS"),
          policy_packs: [
            {
              platform_code: "TIKTOK",
              distribution_mode: "PAID_AD",
              pack_label: "TIKTOK-PAID_AD-2026-08-09.1",
              pack_version: 1,
            },
          ],
          policy_citations: [
            {
              rule_id: "TT-AD-001",
              title: "Misleading and false content",
              source_url:
                "https://ads.tiktok.com/help/article/tiktok-advertising-policies",
              section_path: "Advertising Policies > Deceptive practices",
            },
          ],
        }),
        "AI_REVIEW",
      ),
    );
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));

    await waitFor(() =>
      expect(screen.getByText("Đã kiểm tra chính sách")).toBeInTheDocument(),
    );
    expect(screen.getByText(/TIKTOK · Quảng cáo trả phí/)).toBeInTheDocument();
    expect(screen.getByText("TIKTOK-PAID_AD-2026-08-09.1")).toBeInTheDocument();
    expect(
      screen.getByText(/Misleading and false content/),
    ).toBeInTheDocument();
  });

  it("links to the official source the server stored, not one it built", async () => {
    const sourceUrl =
      "https://ads.tiktok.com/help/article/tiktok-advertising-policies";
    stubFetch(
      policyRoutes(
        [target({ distribution_mode: "PAID_AD" })],
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS" }),
          review: aiReview("PASS"),
          policy_packs: [
            {
              platform_code: "TIKTOK",
              distribution_mode: "PAID_AD",
              pack_label: "L",
              pack_version: 1,
            },
          ],
          policy_citations: [
            {
              rule_id: "TT-AD-001",
              title: "Deceptive practices",
              source_url: sourceUrl,
              section_path: null,
            },
          ],
        }),
        "AI_REVIEW",
      ),
    );
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));

    const link = await screen.findByRole("link", {
      name: "Xem nguồn chính thức",
    });
    // Exactly the stored URL. The client assembles no policy address of its own.
    expect(link).toHaveAttribute("href", sourceUrl);
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));

    // And no policy URL is constructed anywhere in the page source.
    const source = readPrContentDetailSource();
    for (const forbidden of [
      "transparency.meta.com",
      "ads.tiktok.com",
      "tiktok.com/community",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });
});

describe("64. a legacy review is not dressed up as policy-grounded", () => {
  it("says so rather than implying a check that never ran", async () => {
    stubFetch(
      policyRoutes(
        [],
        aiState({
          run: aiRun("SUCCEEDED", { outcome: "PASS" }),
          review: aiReview("PASS"),
          policy_packs: [],
          policy_citations: [],
        }),
        "AI_REVIEW",
      ),
    );
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "Lượt review này không đối chiếu chính sách nền tảng.",
        ),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("Đã kiểm tra chính sách"),
    ).not.toBeInTheDocument();
  });
});

describe("65. the browser decides nothing about policy", () => {
  it("holds no platform list, no pack resolution and no readiness rule", () => {
    for (const file of [
      ...PR_CONTENT_DETAIL_FILES,
      "app/pr/content/page.tsx",
      "lib/api.ts",
      "lib/labels.ts",
    ]) {
      const source = read(file);
      for (const forbidden of [
        "POLICY_GROUNDED",
        "resolve_active_pack",
        "policy_pack_unavailable",
        // Which platforms are grounded is a server answer carried on the target.
        '=== "FACEBOOK"',
        '=== "TIKTOK"',
      ]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
    // It reads the server's flag instead.
    expect(readPrContentDetailSource()).toContain("policy_grounded_platform");
  });
});

// --- 66-68: Step 1F.2 ---------------------------------------------------------

describe("66. the detail page opens on the draft", () => {
  it("selects Nội dung on first render, not Tổng quan", async () => {
    stubFetch(policyRoutes([target({ distribution_mode: "ORGANIC" })]));
    renderWithQuery(<ContentDetailPage />);

    const content = await screen.findByRole("tab", { name: "Nội dung" });
    expect(content).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Tổng quan" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    // The strip order is the life of a piece: what it is, what it says, who
    // approved it, what was made from it, where it went, what happened. Step
    // 1F.2.3f added the middle two - before them the page ended at "Duyệt" and
    // everything after approval had nowhere to be.
    const labels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(labels).toEqual([
      "Tổng quan",
      "Nội dung",
      "Duyệt",
      "Sản phẩm",
      "Xuất bản",
      "Lịch sử",
    ]);
  });

  it("does not flicker through Tổng quan first", () => {
    // Initial state, not an effect that corrects itself after paint - so the
    // very first render is already the draft tab.
    const source = readPrContentDetailSource();
    expect(source).toContain('useState<string>("content")');
    expect(source).not.toContain('useState<string>("overview")');
    expect(source).not.toMatch(/useEffect\([^)]*setTab/);
  });

  it("still reaches the other tabs", async () => {
    stubFetch(policyRoutes([target({ distribution_mode: "ORGANIC" })]));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("tab", { name: "Tổng quan" }),
    );
    expect(screen.getByRole("tab", { name: "Tổng quan" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("67. the draft tab shows where the piece is going", () => {
  it("names each channel and its mode", async () => {
    stubFetch(
      policyRoutes([
        target({ distribution_mode: "PAID_AD" }),
        target({
          id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
          channel_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
          channel_code: "CH-FB",
          channel_name: "Facebook Apexmed",
          platform_code: "FACEBOOK",
          distribution_mode: "ORGANIC",
        }),
      ]),
    );
    renderWithQuery(<ContentDetailPage />);
    const summary = (await screen.findByText("Kênh dự kiến")).closest(
      "section",
    )!;
    expect(within(summary).getByText(/TikTok Apexmed/)).toBeInTheDocument();
    expect(within(summary).getByText(/Facebook Apexmed/)).toBeInTheDocument();
    expect(within(summary).getByText(/Quảng cáo trả phí/)).toBeInTheDocument();
    expect(within(summary).getByText(/Organic/)).toBeInTheDocument();
  });

  it("says plainly when legacy content has no channel at all", async () => {
    // Hiding this would make a piece that cannot be policy-reviewed look
    // identical to one that can.
    stubFetch(policyRoutes([]));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByText(/Chưa có kênh dự kiến/)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Sửa kênh" }),
    ).toBeInTheDocument();
  });
});

describe("68. creating content requires a channel", () => {
  it("blocks submission until a channel and its mode are chosen", async () => {
    stubFetch(boardRoutes());
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Tạo nội dung/ }),
    );

    const submit = await screen.findByRole("button", { name: "Tạo nội dung" });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/Vui lòng chọn ít nhất một kênh dự kiến/),
    ).toBeInTheDocument();

    // A grounded channel still needs its mode before the form will submit.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Chọn kênh" }),
      CHANNEL.id,
    );
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/Chọn Organic hay Quảng cáo trả phí/),
    ).toBeInTheDocument();

    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Hình thức đăng cho/ }),
      "PAID_AD",
    );
    // Step 1F.2.3e: a format is required too, so the button stays disabled
    // until one is chosen - which is the rule this assertion now also covers.
    expect(submit).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Loại nội dung/ }),
      "FACEBOOK_POST",
    );
    expect(submit).toBeEnabled();
  });

  it("sends each target with its own mode", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/contents",
        method: "POST",
        status: 201,
        body: {
          content: IDEA_ITEM,
          current_version: VERSION,
          targets: [],
          brand: BRAND,
        },
      },
      ...boardRoutes(),
    ]);
    renderWithQuery(<ContentBoardPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Tạo nội dung/ }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Tiêu đề/ }),
      "Bài mới",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Thương hiệu/ }),
      BRAND.id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Người phụ trách/ }),
      SESSION.user_id,
    );
    // Step 1F.2.3e: new content must have a format.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Loại nội dung/ }),
      "SHORT_VIDEO_SCRIPT",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Chọn kênh" }),
      CHANNEL.id,
    );
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Hình thức đăng cho/ }),
      "PAID_AD",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo nội dung" }));

    const calls = (
      fetchMock as unknown as {
        calls: Array<{ method: string; body: unknown }>;
      }
    ).calls;
    const created = calls.find((call) => call.method === "POST");
    expect((created?.body as { targets: unknown }).targets).toEqual([
      { channel_id: CHANNEL.id, distribution_mode: "PAID_AD" },
    ]);
  });

  it("asks the server which channels may be chosen", () => {
    // Status filtering is the server's, like brands. A browser-side
    // `status === "ACTIVE"` would be the UI deciding what may be published on.
    // The form lives in the shared component since it moved to "Tạo order".
    const source = read("components/pr-create-content.tsx");
    expect(source).toContain('api.listChannels({ status: "ACTIVE" })');
    expect(source).not.toMatch(/channel\.status\s*===/);
    // And the picker never shows a platform code or a UUID.
    expect(source).not.toContain('=== "TIKTOK"');
    expect(source).toContain("policy_grounded_platform");
  });
});

/**
 * Step 1F.2.1 - master data, 69-74.
 *
 * The bug these exist for is not subtle: Step 1F.2 made a target channel
 * mandatory while the only way to register one was SQL, so a fresh deployment
 * could not create its first piece of content. What is worth asserting is
 * therefore less "a form renders" and more that the form does not become a
 * second authority - the codes are typed rather than derived, the capability
 * check comes from the server, and no UUID reaches the screen.
 */

const PLATFORM = {
  id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  code: "FACEBOOK",
  name: "Facebook",
  status: "ACTIVE",
  policy_grounded: true,
};

const channelRoutes = (
  channels: unknown[] = [],
  platforms: unknown[] = [PLATFORM],
  capabilities: string[] = ["PR_CHANNEL_MANAGE"],
) => [
  {
    match: "/api/pr/dashboard",
    body: { ...DASHBOARD, my_capabilities: capabilities },
  },
  { match: "/api/pr/platforms", body: platforms },
  { match: "/api/pr/brands", body: [BRAND] },
  // Step 1F.2.4a. Ordered before the bare list route because ``stubFetch``
  // matches on the first substring hit, and every metrics URL also contains
  // "/api/pr/channels".
  { match: "/metrics", body: NO_METRICS },
  // Step 1F.2.9. Creating a channel opens it, and the detail route has to answer
  // with a detail object rather than with the list. It always did open it - the
  // selection simply used to be React state and is now `?channel=` - but the
  // fixture answered every `/api/pr/channels*` URL with the array, so the panel
  // read `assignments` off it and threw. Ordered before the bare list route,
  // like `/metrics` above and for the same reason.
  {
    match: `/api/pr/channels/${CHANNEL.id}`,
    body: {
      channel: CHANNEL,
      assignments: [],
      can_edit_channel: true,
      can_record_metrics: true,
      can_manage_assignments: true,
    },
  },
  { match: "/api/pr/channels", body: channels },
  {
    match: "/api/pr/people",
    body: [
      {
        user_id: SESSION.user_id,
        full_name: SESSION.full_name,
        role: SESSION.role,
      },
    ],
  },
];

describe("69. the empty channel list offers a way out of it", () => {
  it("shows both create actions to somebody who may manage channels", async () => {
    stubFetch(channelRoutes());
    renderWithQuery(<ChannelsPage />);

    expect(
      await screen.findByRole("button", { name: "+ Tạo nền tảng" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "+ Tạo kênh" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Hãy tạo nền tảng và kênh đầu tiên/),
    ).toBeInTheDocument();
  });

  it("shows neither to somebody who may only read", async () => {
    stubFetch(channelRoutes([], [PLATFORM], ["PR_TEAM_LEAD_REVIEW"]));
    renderWithQuery(<ChannelsPage />);

    // The empty state still appears - a reader is told there is nothing, not
    // offered a button that would 403.
    expect(await screen.findByText("Chưa có kênh nào.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "+ Tạo nền tảng" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "+ Tạo kênh" }),
    ).not.toBeInTheDocument();
  });

  it("will not let a channel be created before a platform exists", async () => {
    stubFetch(channelRoutes([], []));
    renderWithQuery(<ChannelsPage />);

    expect(
      await screen.findByRole("button", { name: "+ Tạo kênh" }),
    ).toBeDisabled();
    expect(screen.getByText(/Hãy tạo nền tảng trước/)).toBeInTheDocument();
  });
});

describe("70. creating a platform asks for the code", () => {
  it("sends what was typed, and never derives it from the name", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/platforms",
        method: "POST",
        status: 201,
        body: PLATFORM,
      },
      ...channelRoutes(),
    ]);
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Tạo nền tảng" }),
    );

    await userEvent.type(
      screen.getByRole("textbox", { name: "Mã nền tảng" }),
      "facebook",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Tên" }),
      "Facebook Việt Nam",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo nền tảng" }));

    const calls = (
      fetchMock as unknown as {
        calls: Array<{ method: string; body: unknown }>;
      }
    ).calls;
    const created = calls.find((call) => call.method === "POST");
    // The name is a label and the code is identity. The form kept them apart.
    expect(created?.body).toEqual({
      code: "FACEBOOK",
      name: "Facebook Việt Nam",
    });
  });

  it("renders a duplicate refusal as the sentence the server sent", async () => {
    stubFetch([
      {
        match: "/api/pr/platforms",
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "pr.conflict",
            message: "Đã có nền tảng với mã FACEBOOK.",
          },
        },
      },
      ...channelRoutes(),
    ]);
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Tạo nền tảng" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Mã nền tảng" }),
      "FACEBOOK",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Tên" }),
      "Facebook",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo nền tảng" }));

    expect(
      await screen.findByText("Đã có nền tảng với mã FACEBOOK."),
    ).toBeInTheDocument();
  });
});

describe("71. creating a channel picks from what the server has", () => {
  it("lists platforms and brands by name, and sends their ids", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/channels",
        method: "POST",
        status: 201,
        body: {
          channel: { ...CHANNEL, name: "Apexmed Facebook" },
          assignments: [],
        },
      },
      ...channelRoutes(),
    ]);
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Tạo kênh" }),
    );

    const platform = screen.getByRole("combobox", { name: "Nền tảng *" });
    expect(
      within(platform).getByRole("option", { name: "Facebook" }),
    ).toBeInTheDocument();
    const brand = screen.getByRole("combobox", { name: "Thương hiệu" });
    expect(
      within(brand).getByRole("option", { name: "Apexmed" }),
    ).toBeInTheDocument();

    await userEvent.selectOptions(platform, PLATFORM.id);
    await userEvent.selectOptions(brand, BRAND.id);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Tên kênh" }),
      "Apexmed Facebook",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo kênh" }));

    const calls = (
      fetchMock as unknown as {
        calls: Array<{ method: string; body: unknown }>;
      }
    ).calls;
    const created = calls.find((call) => call.method === "POST");
    expect(created?.body).toMatchObject({
      name: "Apexmed Facebook",
      platform_id: PLATFORM.id,
      brand_id: BRAND.id,
      category: "SCALE",
    });
    // No code was sent. `CH-nnnn` is the server's to allocate.
    expect(created?.body).not.toHaveProperty("code");
  });

  it("offers only the domain's own categories", async () => {
    stubFetch(channelRoutes());
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Tạo kênh" }),
    );

    const category = screen.getByRole("combobox", { name: "Danh mục" });
    expect(within(category).getAllByRole("option")).toHaveLength(
      CHANNEL_CATEGORIES.length,
    );
  });
});

describe("72. a created channel shows up without a reload", () => {
  it("refetches the list once the server has accepted it", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/channels",
        method: "POST",
        status: 201,
        body: { channel: CHANNEL, assignments: [] },
      },
      {
        match: "/api/pr/dashboard",
        body: { ...DASHBOARD, my_capabilities: ["PR_CHANNEL_MANAGE"] },
      },
      { match: "/api/pr/platforms", body: [PLATFORM] },
      { match: "/api/pr/brands", body: [BRAND] },
      {
        match: "/api/pr/channels/",
        body: { channel: CHANNEL, assignments: [] },
      },
      { match: "/api/pr/channels", body: [CHANNEL] },
      {
        match: "/api/pr/people",
        body: [
          {
            user_id: SESSION.user_id,
            full_name: SESSION.full_name,
            role: SESSION.role,
          },
        ],
      },
    ]);
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Tạo kênh" }),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Nền tảng *" }),
      PLATFORM.id,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Tên kênh" }),
      "TikTok Apexmed",
    );
    await userEvent.click(screen.getByRole("button", { name: "Tạo kênh" }));

    await waitFor(() => {
      const listed = (
        fetchMock as unknown as {
          calls: Array<{ url: string; method: string }>;
        }
      ).calls.filter(
        (call) =>
          call.method === "GET" && call.url.endsWith("/api/pr/channels"),
      );
      expect(listed.length).toBeGreaterThan(1);
    });
  });
});

describe("73. the channel screen shows no identifiers a person cannot use", () => {
  it("renders names and Vietnamese labels, never a UUID", async () => {
    stubFetch(channelRoutes([CHANNEL]));
    renderWithQuery(<ChannelsPage />);

    expect(await screen.findByText("TikTok Apexmed")).toBeInTheDocument();
    expect(
      screen.getByText(channelCategoryLabel(CHANNEL.category)),
    ).toBeInTheDocument();
    expect(
      screen.getByText(channelStatusLabel(CHANNEL.status)),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(CHANNEL.id);
    expect(document.body.textContent).not.toContain(CHANNEL.platform_id);
    // The allocated code is not a UUID and is worth showing - it is what people
    // paste into spreadsheets.
    expect(screen.getByText(CHANNEL.code)).toBeInTheDocument();
  });
});

describe("74. the master-data screen decides nothing on its own", () => {
  it("asks the server both what exists and whether the actor may add to it", () => {
    const source = read("app/pr/channels/page.tsx");
    // The capability comes from the dashboard's `my_capabilities`, which is the
    // same capability service every write consults.
    expect(source).toContain("PR_CHANNEL_MANAGE");
    expect(source).toContain("my_capabilities");
    // No role comparison, and no policy-grounding decided in the browser.
    expect(source).not.toMatch(/role\s*===/);
    expect(source).not.toContain('=== "FACEBOOK"');
    // No client-side status filter: the platform list route decides.
    expect(source).not.toMatch(/platform\.status\s*===/);
  });
});
