/**
 * Step 1E frontend tests. Fifteen, numbered.
 *
 * They are all about one question: **does the browser layer stay a browser
 * layer?** The Python tests prove the rules; these prove the panel does not
 * quietly reimplement one, does not hide a refusal, and does not turn an absent
 * AI review into a reassuring blank space.
 *
 * `fetch` is stubbed rather than a server being run. What is being tested is what
 * the page *sends* and what it *renders* - and asserting on the request body is
 * the only way to prove, for instance, that an approval carries the version that
 * was on screen rather than "the latest".
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import { ApiError, api } from "@/lib/api";
import { STAGE_ORDER, formatDay } from "@/lib/labels";
import { ErrorBox } from "@/components/states";
import ContentDetailPage from "@/app/pr/content/[id]/page";
import DashboardPage from "@/app/pr/page";
import ReportsPage from "@/app/pr/reports/page";
import PermissionsPage from "@/app/pr/permissions/page";
import {
  CONTENT,
  SESSION,
  VERSION,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
} from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr",
  // Section 14 renders the permissions page, whose grant form is its third tab.
  useSearchParams: () => new URLSearchParams("tab=grants"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
}));

const SRC = path.resolve(__dirname, "../src");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

const reviewContext = (overrides: Record<string, unknown> = {}) => ({
  content: CONTENT,
  current_version: VERSION,
  targets: [],
  tasks: [],
  ai_review: null,
  ai_reviews_for_version: [],
  approvals: [],
  ...overrides,
});

/**
 * The `/available-actions` payload, which the detail page now drives itself
 * from. Every test that renders the page must stub it: an action the server did
 * not list is an action the panel does not draw, so an unstubbed route is a page
 * with no buttons rather than a page with all of them.
 */
const availableActions = (
  actions: Array<Record<string, unknown>> = [],
  stage: string = CONTENT.workflow_stage,
) => ({ content_id: CONTENT.id, workflow_stage: stage, available_actions: actions });

const approvalAction = (decision: string, emphasis: string) => ({
  action: "APPROVAL",
  target_stage: null,
  decision,
  emphasis,
});

/**
 * The `/ai-review` payload. Step 1F made the panel read its own endpoint rather
 * than the review context, so every detail render needs this stubbed too.
 */
const aiReviewState = (over: Record<string, unknown> = {}) => ({
  run: null,
  review: null,
  active: false,
  can_retry: false,
  policy_packs: [],
  policy_citations: [],
  ...over,
});

/** The routes every detail-page render needs, most specific first. */
const detailRoutes = (
  actions: Array<Record<string, unknown>>,
  context: Record<string, unknown> = reviewContext(),
  ai: Record<string, unknown> = aiReviewState(),
) => [
  { match: "/available-actions", body: availableActions(actions, (context.content as { workflow_stage: string }).workflow_stage) },
  { match: "/ai-review", body: ai },
  { match: "/review-context", body: context },
  { match: "/versions", method: "GET", body: [VERSION] },
  { match: "/api/pr/people", body: [] },
  {
    match: "/api/pr/contents/",
    body: { content: context.content, current_version: VERSION, targets: [] },
  },
];

// --- 1-5: the boundary ------------------------------------------------------

describe("1. no workflow matrix in the browser", () => {
  it("draws the actions the server listed, and owns no stage table", () => {
    const source = read("app/pr/content/[id]/page.tsx");
    // Step 1E.2 turned "offer everything and let the server refuse" into "ask
    // the server what is possible". Both keep the matrix in Python; this one
    // also stops showing eleven buttons that fail.
    expect(source).toContain("availableActions");
    for (const forbidden of [
      "CONTENT_TRANSITIONS",
      "allowedTransitions",
      "canTransition",
      // Not even the *reading* order: a page that iterated the stage list would
      // be one edit away from iterating it as if it were the transition table.
      "STAGE_ORDER",
    ]) {
      expect(source).not.toContain(forbidden);
    }
    // The board groups stages for layout and must not do it for workflow.
    const board = read("app/pr/content/page.tsx");
    for (const forbidden of ["CONTENT_TRANSITIONS", "nextStage", "canTransition"]) {
      expect(board).not.toContain(forbidden);
    }
    // The board's grouping is a filter and a reading order, and says so. Step
    // 1F.2.3c moved it out of `lib/labels.ts` into a table of its own; Step
    // 1F.2.3c1 made its keys the server's `PrContentGroup` values and sent them
    // with the query. What it still must not carry is a rule.
    expect(read("lib/board.ts")).toContain("Ordering and words, not rules");
  });
});

describe("2. no capability rules in the browser", () => {
  it("never decides authority for itself", () => {
    for (const file of [
      "app/pr/permissions/page.tsx",
      "app/pr/content/[id]/page.tsx",
      "app/pr/page.tsx",
      "lib/api.ts",
    ]) {
      const source = read(file);
      for (const forbidden of ["hasPermission", "canApprove", "ROLE_RANK", "isAllowed("]) {
        expect(source, file).not.toContain(forbidden);
      }
    }
  });
});

describe("3. no code generation in the browser", () => {
  it("has no code field in any create form", () => {
    for (const file of ["app/pr/content/page.tsx", "app/pr/tasks/page.tsx"]) {
      const source = read(file);
      expect(source, file).not.toMatch(/name="code"/);
      expect(source, file).not.toMatch(/\bCNT-\d/);
    }
    // And the client type for creating content has no `code` key at all.
    expect(read("lib/api.ts")).toContain("createContent");
    expect(read("lib/api.ts")).not.toMatch(/createContent[\s\S]{0,300}code:/);
  });
});

describe("4. no date arithmetic on assignment intervals", () => {
  it("formats dates but never compares them", () => {
    const source = read("app/pr/channels/page.tsx");
    // Overlap is the domain's decision. A browser that computed it would
    // eventually disagree about whether the last day counts - and it does.
    for (const forbidden of ["overlaps", "Date.now()", "getTime()"]) {
      expect(source).not.toContain(forbidden);
    }
    // The inclusive-end rule is stated to the person instead.
    expect(source).toContain("cuối cùng còn hiệu lực");
  });

  it("renders a date without shifting it", () => {
    // Parsed as local midnight, so a date-only value never lands on the previous
    // day the way `new Date("2026-01-10")` (UTC midnight) would.
    expect(formatDay("2026-01-10")).toBe("10/01/2026");
    expect(formatDay(null)).toBe("—");
  });
});

describe("5. the API client sends no identity of its own", () => {
  it("never puts a reviewer or a user id in an approval body", () => {
    const source = read("lib/api.ts");
    // `reviewer_user_id` appears on *responses* - the ApprovalEvent, and since
    // M6 the monthly performance review - because showing **who decided** is the
    // point of both panels. What must never exist is a **request body** carrying
    // one, and that is now asserted directly rather than by counting
    // occurrences: the count was a proxy that a second legitimate response field
    // breaks, and the invariant it stood for is the one worth keeping.
    //
    // Note the distinction M6 makes necessary: its bodies do carry a `user_id`,
    // naming **the person being reviewed**. That is a subject, not the client
    // asserting who *it* is - which stays impossible, because the actor comes
    // from the session cookie and from nowhere else.
    const decide = source.slice(source.indexOf("decide: ("), source.indexOf("listAiReviews"));
    expect(decide).toContain("version_reviewed");
    expect(decide).not.toContain("reviewer");
    expect(decide).not.toContain("user_id");
    for (const match of source.matchAll(/\(body: \{[\s\S]*?\n  \}\)/g)) {
      expect(match[0]).not.toContain("reviewer_user_id");
      expect(match[0]).not.toContain("actor");
    }
    // Cookies, not headers. A Telegram id in a header would be a bearer token
    // anybody could copy.
    expect(source).toContain('credentials: "same-origin"');
    expect(source).not.toMatch(/X-Telegram|Authorization:/);
  });
});

// --- 6-10: rendering the truth ---------------------------------------------

describe("6. an absent AI review is words, not a blank card", () => {
  it("says chưa có instead of rendering an empty verdict", async () => {
    // Ordered most-specific first: the stub matches on substring, so a bare
    // "/api/pr/contents/" route would also swallow the versions request and hand
    // the list an object.
    stubFetch(detailRoutes([]));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));
    await waitFor(() => expect(screen.getByText(/Chưa có AI review/)).toBeInTheDocument());
    // The strongest form of this assertion: no result label anywhere. An empty
    // verdict card would read as "reviewed, nothing found", which is the
    // opposite of "nobody has looked at this" - and from Step 1F it would also
    // be the opposite of "the review has not run yet".
    expect(screen.queryByText("Đạt")).not.toBeInTheDocument();
    expect(screen.queryByText("Đạt, có lưu ý")).not.toBeInTheDocument();
  });
});

describe("7. an AI verdict always carries its provenance", () => {
  it("names the model and prompt version, and says it is advisory", async () => {
    const verdict = {
            id: "55555555-5555-5555-5555-555555555555",
            reviewed_version: 3,
            review_type: "FULL_REVIEW",
            result: "PASS_WITH_WARNINGS",
            score: "7.5",
            summary: "Nội dung ổn, cần kiểm tra lại phần cam kết.",
            issues: ["Câu cam kết quá mạnh"],
            suggestions: [],
            policy_flags: [],
            model_name: "gpt-x",
            model_version: "2026-05",
            prompt_version: "pr-review-v3",
      reviewed_at: "2026-08-02T03:00:00+00:00",
      created_at: "2026-08-02T03:00:00+00:00",
    };
    stubFetch(
      detailRoutes([], reviewContext(), aiReviewState({ review: verdict })),
    );
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));
    await waitFor(() => expect(screen.getByText("Đạt, có lưu ý")).toBeInTheDocument());
    expect(screen.getByText(/gpt-x/)).toBeInTheDocument();
    expect(screen.getByText(/pr-review-v3/)).toBeInTheDocument();
    expect(screen.getByText(/chỉ mang tính tư vấn/)).toBeInTheDocument();
  });
});

describe("8. an empty review queue explains itself", () => {
  it("distinguishes no grant from nothing waiting", async () => {
    stubFetch([
      {
        match: "/api/pr/dashboard",
        body: {
          stage_counts: STAGE_ORDER.map((stage) => ({ stage, count: 0 })),
          awaiting_my_review: [],
          overdue_tasks: [],
          my_capabilities: [],
          recent_content: [],
        },
      },
    ]);
    renderWithQuery(<DashboardPage />);
    // Not "nothing to review" - the honest reason.
    await waitFor(() =>
      expect(screen.getByText(/chưa được cấp quyền duyệt ở bước nào/)).toBeInTheDocument(),
    );
  });
});

describe("9. a 403 is rendered as the server's sentence", () => {
  it("shows the API message and does not invent its own", () => {
    renderWithQuery(
      <ErrorBox
        error={
          new ApiError(403, "pr_permission_denied_error", "Bạn chưa được cấp quyền duyệt ở bước này.", {})
        }
      />,
    );
    expect(screen.getByText("Bạn chưa được cấp quyền duyệt ở bước này.")).toBeInTheDocument();
    // And it points at where a grant comes from, rather than saying "try again".
    expect(screen.getByText("Thành viên & Phân quyền")).toBeInTheDocument();
  });
});

describe("10. a 401 sends the person back to the bot", () => {
  it("names /web rather than showing a broken page", () => {
    renderWithQuery(<ErrorBox error={new ApiError(401, "http_error", "whatever", {})} />);
    expect(screen.getByText(/Bạn cần đăng nhập lại/)).toBeInTheDocument();
    expect(screen.getByText("/web")).toBeInTheDocument();
  });
});

// --- 11-15: what the panel sends -------------------------------------------

describe("11. an approval carries the version that was on screen", () => {
  it("sends version_reviewed from the rendered draft, not the newest", async () => {
    const fetchMock = stubFetch([
      {
        match: "/reviews",
        method: "POST",
        status: 201,
        body: { content: CONTENT, current_version: VERSION, targets: [] },
      },
      ...detailRoutes([
        approvalAction("APPROVED", "PRIMARY"),
        approvalAction("REVISION_REQUIRED", "SECONDARY"),
        approvalAction("REJECTED", "DANGER"),
      ]),
    ]);
    renderWithQuery(<ContentDetailPage />);
    // The primary appears twice on purpose: once in the flow of the page and
    // once in the sticky bar a thumb can reach. Both send the same body.
    const approve = await screen.findAllByRole("button", { name: "Duyệt" });
    await userEvent.click(approve[0]);
    // Step 1F.2.8. Approving is a business write, so it confirms first - and the
    // confirmation says what the approval *does*, which is the thing a reviewer
    // pressing "Duyệt" for the ninth time that morning has stopped reading the
    // button for.
    expect(dialog().getByText("Duyệt nội dung này?")).toBeInTheDocument();
    await confirm();

    const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;
    const approval = calls.find((call) => call.method === "POST" && call.url.includes("/reviews"));
    expect(approval).toBeDefined();
    expect(approval?.body).toEqual({ decision: "APPROVED", version_reviewed: 3 });
    // The body carries no reviewer. Whoever the session says you are is who
    // approved, and that is not negotiable from here.
    expect(JSON.stringify(approval?.body)).not.toContain("reviewer");
  });
});

describe("12. a revision sends expected_version", () => {
  it("makes a concurrent edit a 409 rather than an overwrite", async () => {
    const drafting = reviewContext({ content: { ...CONTENT, workflow_stage: "SCRIPTING" } });
    const fetchMock = stubFetch([
      {
        match: "/versions",
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "pr_stale_version_error",
            message: "Nội dung đã được sửa bởi người khác. Bạn tải lại rồi thử lại nhé.",
            details: {},
          },
        },
      },
      ...detailRoutes(
        [{ action: "EDIT_CONTENT", target_stage: null, decision: null, emphasis: "SECONDARY" }],
        drafting,
      ),
    ]);
    renderWithQuery(<ContentDetailPage />);
    // The action panel's own button is the shortcut: it switches to the Nội dung
    // tab and opens the editor, which is the path somebody actually takes.
    const edit = await screen.findAllByRole("button", { name: "Chỉnh sửa nội dung" });
    await userEvent.click(edit[0]);
    await userEvent.click(await screen.findByRole("button", { name: /Lưu thành v4/ }));

    const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;
    const revision = calls.find((call) => call.method === "POST");
    expect((revision?.body as { expected_version: number }).expected_version).toBe(3);
    // And the conflict is shown with the reload advice, not swallowed.
    await waitFor(() => expect(screen.getByText(/đã được sửa bởi người khác/)).toBeInTheDocument());
    expect(screen.getByText(/tải lại trang rồi thử lại/)).toBeInTheDocument();
  });
});

describe("13. a refused transition is reported, not hidden", () => {
  it("renders the server's reason after a 409", async () => {
    const drafting = reviewContext({ content: { ...CONTENT, workflow_stage: "SCRIPTING" } });
    stubFetch([
      {
        match: "/transition",
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "pr_workflow_transition_error",
            message: "Không thể chuyển từ Viết kịch bản sang Chờ AI review.",
            details: {},
          },
        },
      },
      ...detailRoutes(
        [
          {
            action: "TRANSITION",
            target_stage: "AI_REVIEW",
            decision: null,
            emphasis: "PRIMARY",
          },
        ],
        drafting,
      ),
    ]);
    renderWithQuery(<ContentDetailPage />);
    // The item moved after this page loaded, so a move the server offered a
    // moment ago is refused. The refusal is what teaches the person - it is
    // never swallowed into a disabled button with no explanation.
    const send = await screen.findAllByRole("button", { name: "Gửi đi AI review" });
    await userEvent.click(send[0]);
    await confirm();
    // Step 1F.2.8: the dialog stays open on a failure and carries the sentence,
    // so the refusal lands where the person is looking rather than behind a
    // dialog that has already closed. The panel shows it too, which is why this
    // counts rather than asserting a single node.
    await waitFor(() =>
      expect(screen.getAllByText(/Không thể chuyển từ Viết kịch bản/).length).toBeGreaterThan(0),
    );
    expect(dialog().getByText(/Không thể chuyển từ Viết kịch bản/)).toBeInTheDocument();
  });
});

describe("14. permissions only ever offers the three grant-backed capabilities", () => {
  it("does not present role-derived capabilities as grantable", async () => {
    stubFetch([
      {
        match: "/api/pr/members",
        body: {
          members: [],
          counts: { total: 1, active: 1, suspended: 0, revoked: 0, pending: 0 },
          may_add: true,
          may_change_status: true,
          may_change_role: true,
          assignable_roles: [],
        },
      },
      { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name, role: "TEAM_LEAD" }] },
      { match: "/api/pr/channels", body: [] },
      { match: "/api/pr/capabilities", body: [] },
    ]);
    renderWithQuery(<PermissionsPage />);
    await waitFor(() => expect(screen.getByText("Cấp quyền duyệt")).toBeInTheDocument());

    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toContain("Duyệt Trưởng nhóm");
    expect(options).toContain("Duyệt Trưởng phòng");
    expect(options).toContain("Duyệt nội bộ");
    // The ten role-derived ones must not be offered.
    for (const notGrantable of ["Tạo nội dung", "Quản lý task", "Quản lý kênh", "Xem dữ liệu PR"]) {
      expect(options).not.toContain(notGrantable);
    }
    // Step 1F.2.7 replaced the narrowing rule with the additive one, and the
    // page states the replacement rather than leaving it to be discovered. The
    // scope rules themselves are asserted in `permission-scopes.test.tsx`.
    expect(screen.getByText(/không đổi vai trò/)).toBeInTheDocument();
  });
});

describe("15. reports invents nothing", () => {
  it("shows live counts and says the reporting service does not exist", async () => {
    stubFetch([
      {
        match: "/api/pr/dashboard",
        body: {
          stage_counts: [
            { stage: "IDEA", count: 2 },
            { stage: "PUBLISHED", count: 5 },
          ],
          awaiting_my_review: [],
          overdue_tasks: [],
          my_capabilities: [],
          recent_content: [],
        },
      },
    ]);
    renderWithQuery(<ReportsPage />);
    await waitFor(() => expect(screen.getByText("Tổng")).toBeInTheDocument());
    // The total is the sum of what the server sent, not an estimate.
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText(/chưa có gì ghi vào chúng/)).toBeInTheDocument();
    expect(screen.getByText(/không vẽ biểu đồ từ số liệu không tồn tại/)).toBeInTheDocument();
  });

  it("has no chart library and no invented series", () => {
    const source = read("app/pr/reports/page.tsx");
    for (const forbidden of ["recharts", "chart.js", "Math.random", "mockData", "sampleData"]) {
      expect(source).not.toContain(forbidden);
    }
  });
});

describe("api client error parsing", () => {
  it("turns the error envelope into an ApiError with the server's code", async () => {
    stubFetch([
      {
        match: "/api/pr/dashboard",
        status: 422,
        body: { error: { code: "pr_validation_error", message: "Giá trị không hợp lệ.", details: { field: "stage" } } },
      },
    ]);
    await expect(api.dashboard()).rejects.toMatchObject({
      status: 422,
      code: "pr_validation_error",
      message: "Giá trị không hợp lệ.",
      details: { field: "stage" },
    });
  });

  it("does not put a non-JSON upstream body on screen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>502 Bad Gateway</html>", { status: 502 })),
    );
    // A proxy error page must not become the message a person reads.
    await expect(api.dashboard()).rejects.toMatchObject({
      status: 502,
      message: "Yêu cầu thất bại (502).",
    });
  });
});
