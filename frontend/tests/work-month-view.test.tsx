/**
 * Post-M4 - the employee-centric KPI screen and the unified monthly Work view.
 *
 * The browser decides nothing on either screen, and the assertions are shaped
 * to prove it. Which plan version is current comes from the server's summary;
 * which timestamp is an execution date comes from `execution_at`; which month
 * a query covers comes from `period_id`. There is no `.find()`, no date
 * fallback and no source branch in the components under test.
 *
 * The four that carry the patch
 * ------------------------------
 *
 * **Test 2** - three plan versions render one employee row. That is the bug:
 * the screen used to render the plan *list*, so somebody on their third
 * revision appeared three times as three management entities.
 *
 * **Test 5** - an employee with no plan still appears, with a control to start
 * one. A list built from plan rows structurally cannot show them, and "who has
 * no KPI plan" is half of what the screen is for.
 *
 * **Test 8** - content, manual and recurring work render in one list. Source is
 * a badge and a filter, never a second ledger.
 *
 * **Test 12** - a manual row shows no execution date at all. Falling back to
 * `due_at` would label a deadline as a performance, which is the invention the
 * whole change exists to remove.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, channelsNavigation, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const OWNER = [
  "PR_WORK_EXECUTE",
  "PR_WORK_MANAGE",
  "PR_WORK_VALIDATE",
  "PR_WORK_CONFIGURE",
  "PR_WORK_VIEW_ALL",
];

const HAO = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";
const PERIOD = "66666666-6666-6666-6666-666666666666";
const PLAN = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const TYPE = "11111111-1111-1111-1111-111111111111";
const TEMPLATE = "77777777-7777-7777-7777-777777777777";

const dashboard = () => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: OWNER,
  recent_content: [],
});

const period = (over: Record<string, unknown> = {}) => ({
  id: PERIOD,
  code: "2026-09",
  period_type: "MONTH",
  date_start: "2026-09-01",
  date_end: "2026-09-30",
  status: "OPEN",
  status_label: "Đang mở",
  closed_at: null,
  locked_at: null,
  ...over,
});

const AUGUST = {
  ...period(),
  id: "55555555-5555-5555-5555-555555555555",
  code: "2026-08",
  date_start: "2026-08-01",
  date_end: "2026-08-31",
};

const WORK_TYPE = {
  id: TYPE,
  code: "PAGE_RECOVERY",
  name: "Kháng page",
  category: "OPERATIONS",
  category_label: "Vận hành",
  description: null,
  default_unit: "ITEM",
  default_unit_label: "đầu việc",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Bùi Mỹ Hảo", role: "EMPLOYEE" },
  { user_id: LINH, full_name: "Linh", role: "EMPLOYEE" },
];

const planSummary = (over: Record<string, unknown> = {}) => ({
  user_id: HAO,
  user_name: "Bùi Mỹ Hảo",
  period_id: PERIOD,
  has_plan: true,
  current_plan_id: PLAN,
  current_version_no: 3,
  current_status: "APPROVED",
  current_status_label: "Đang áp dụng",
  quota_count: 5,
  approved_at: "2026-09-04T04:00:00Z",
  updated_at: "2026-09-04T04:00:00Z",
  latest_draft_id: null,
  history_count: 3,
  ...over,
});

const historyEntry = (over: Record<string, unknown> = {}) => ({
  id: PLAN,
  version_no: 3,
  status: "APPROVED",
  status_label: "Đang áp dụng",
  quota_count: 5,
  created_at: "2026-09-01T02:00:00Z",
  approved_at: "2026-09-04T04:00:00Z",
  superseded_at: null,
  discarded_at: null,
  note: null,
  is_current: true,
  ...over,
});

/** The shape `work-quota.test.tsx` already proves the KPI view renders. */
const eligibilitySummary = () => ({
  user_id: HAO,
  period: period(),
  plan_id: PLAN,
  plan_version_no: 3,
  plan_approved_at: "2026-09-04T04:00:00Z",
  types: [],
  contributions_by_status: {
    NO_QUOTA: 0,
    UNMEASURABLE: 0,
    ELIGIBLE: 0,
    PARTIALLY_ELIGIBLE: 0,
    OVER_QUOTA: 0,
    PENDING_EVALUATION: 0,
  },
  counted_contributions: 0,
  counted_work_items: 0,
});

const planDetail = () => ({
  plan: {
    id: PLAN,
    user_id: HAO,
    user_name: "Bùi Mỹ Hảo",
    period_id: PERIOD,
    period_code: "2026-09",
    period_status: "OPEN",
    version_no: 3,
    status: "APPROVED",
    status_label: "Đang áp dụng",
    supersedes_plan_id: null,
    note: null,
    created_by_user_id: HAO,
    approved_by_user_id: HAO,
    approved_at: "2026-09-04T04:00:00Z",
    superseded_at: null,
    discarded_at: null,
    created_at: "2026-09-01T02:00:00Z",
    quota_count: 5,
  },
  period: period(),
  quotas: [],
  created_by_name: "Hà Trưởng Phòng",
  approved_by_name: "Hà Trưởng Phòng",
  can_edit: false,
  can_approve: false,
  can_revise: true,
  can_discard: false,
});

const contribution = (over: Record<string, unknown> = {}) => ({
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  work_item_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  user_id: HAO,
  user_name: "Bùi Mỹ Hảo",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-01T02:00:00Z",
  count_status: "PENDING",
  count_status_label: "Chưa ghi nhận",
  counted_at: null,
  excluded_reason: null,
  ...over,
});

const workItem = (over: Record<string, unknown> = {}) => ({
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "WRK-2026-000001",
  title: "Kháng page David",
  description: null,
  work_type_id: TYPE,
  work_type_code: "PAGE_RECOVERY",
  work_type_name: "Kháng page",
  work_type_category: "OPERATIONS",
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  content_code: null,
  content_id: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  status: "IN_PROGRESS",
  status_label: "Đang làm",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: null,
  unit: null,
  unit_label: null,
  due_at: "2026-09-05T09:00:00Z",
  execution_at: null,
  is_overdue: false,
  created_by_user_id: LINH,
  assigned_by_user_id: LINH,
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution()],
  ...over,
});

/** A content-derived row: completed at the milestone, with that day on it. */
const contentItem = () =>
  workItem({
    id: "cccccccc-cccc-cccc-cccc-cccccccccc02",
    code: "WRK-2026-000002",
    title: "Kịch bản video Dr Tiến",
    source_type: "CONTENT",
    source_label: "Từ nội dung",
    is_source_derived: true,
    content_code: "CNT-2026-000042",
    content_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    status: "COMPLETED",
    status_label: "Chờ xác nhận",
    due_at: null,
    execution_at: "2026-09-04T02:00:00Z",
  });

/** A generated routine row: the occurrence's instant, and its own deadline. */
const recurringItem = () =>
  workItem({
    id: "cccccccc-cccc-cccc-cccc-cccccccccc03",
    code: "WRK-2026-000003",
    title: "100 comment seeding",
    source_type: "RECURRING",
    source_label: "Định kỳ",
    is_source_derived: true,
    recurring_occurrence_id: "88888888-8888-8888-8888-888888888881",
    recurring_template_id: TEMPLATE,
    recurring_template_name: "100 comment mỗi ngày",
    status: "COMPLETED",
    status_label: "Chờ xác nhận",
    quantity: "100",
    unit: "COMMENT",
    unit_label: "bình luận",
    due_at: "2026-09-04T10:30:00Z",
    execution_at: "2026-09-04T02:00:00Z",
  });

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-10-01T00:00:00Z",
  created: 3,
  accepted: 3,
  completed: 2,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 1,
  in_progress: 1,
  awaiting_validation: 2,
  proposed: 0,
  overdue: 0,
});

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  items: Array<Record<string, unknown>>,
  {
    summaries = [planSummary()],
    history = [historyEntry()],
    periods = [period(), AUGUST],
    extra = [] as Route[],
  } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard() },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/periods", body: periods },
  { match: "/api/pr/work/plans/summary", body: { period_id: PERIOD, items: summaries } },
  {
    match: "/api/pr/work/plans/history",
    body: { user_id: HAO, period_id: PERIOD, items: history },
  },
  // `MyKpiPlan` asks for the caller's own plan; a 404 is the ordinary "chưa có
  // hạn mức KPI" answer and keeps this file about the administration list.
  { match: "/api/pr/work/plans/mine", body: planDetail() },
  { match: `/api/pr/work/plans/${PLAN}`, body: planDetail() },
  { match: "/api/pr/work/eligibility/summary", body: eligibilitySummary() },
  { match: "/api/pr/work/eligibility", body: { period: period(), user_id: HAO, contributions: [] } },
  { match: "/api/pr/work/readiness", body: {} },
  ...extra,
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

const viewTabs = () => within(screen.getByRole("navigation", { name: "Chế độ xem" }));

async function viewTab(name: string): Promise<HTMLElement> {
  await screen.findByRole("navigation", { name: "Chế độ xem" });
  return viewTabs().findByRole("button", { name });
}

async function openKpi() {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await viewTab("Kế hoạch KPI"));
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-7: THE KPI SCREEN IS EMPLOYEE-CENTRIC -------------------------------

describe("1. the KPI list is one row per employee", () => {
  it("shows the current version rather than every version", async () => {
    stubFetch(routes([]));
    await openKpi();

    const row = await screen.findByRole("button", { name: /Bùi Mỹ Hảo/ });
    expect(within(row).getByText(/Kế hoạch hiện tại: v3/)).toBeInTheDocument();
    expect(within(row).getByText(/5 hạn mức/)).toBeInTheDocument();
    expect(within(row).getByText("Đang áp dụng")).toBeInTheDocument();
  });

  it("renders one row for an employee on their third revision", async () => {
    // **The bug.** The screen used to list plan *versions*, so this employee
    // was three cards, of which two were history.
    stubFetch(routes([]));
    await openKpi();

    await screen.findByRole("button", { name: /Bùi Mỹ Hảo/ });
    expect(screen.getAllByRole("button", { name: /Bùi Mỹ Hảo/ })).toHaveLength(1);
  });

  it("names the number of versions rather than repeating the person", async () => {
    stubFetch(routes([]));
    await openKpi();

    const row = await screen.findByRole("button", { name: /Bùi Mỹ Hảo/ });
    expect(within(row).getByText(/3 phiên bản/)).toBeInTheDocument();
  });

  it("does not print a version count for a first plan", async () => {
    // "1 phiên bản" on every first plan is noise; a count is worth showing only
    // when there is a story behind this month's numbers.
    stubFetch(routes([], { summaries: [planSummary({ history_count: 1 })] }));
    await openKpi();

    const row = await screen.findByRole("button", { name: /Bùi Mỹ Hảo/ });
    expect(within(row).queryByText(/phiên bản/)).toBeNull();
  });
});

describe("2. an employee with no plan", () => {
  it("still appears, and is offered a way to start one", async () => {
    // **The other half of the question.** A list built from plan rows shows
    // nothing at all for the people the screen is actually about.
    stubFetch(
      routes([], {
        summaries: [
          planSummary(),
          planSummary({
            user_id: LINH,
            user_name: "Linh",
            has_plan: false,
            current_plan_id: null,
            current_version_no: null,
            current_status: null,
            current_status_label: null,
            quota_count: 0,
            approved_at: null,
            history_count: 0,
          }),
        ],
      }),
    );
    await openKpi();

    expect(await screen.findByText("Chưa có kế hoạch · 0 hạn mức")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tạo kế hoạch cho Linh" }),
    ).toBeInTheDocument();
  });

  it("posts the person and the period when the row's control is used", async () => {
    const calls = stubFetch(
      routes([], {
        summaries: [
          planSummary({
            user_id: LINH,
            user_name: "Linh",
            has_plan: false,
            current_plan_id: null,
            history_count: 0,
          }),
        ],
        extra: [{ match: "/api/pr/work/plans", body: {}, method: "POST" }],
      }),
    );
    await openKpi();
    await userEvent.click(
      await screen.findByRole("button", { name: "Tạo kế hoạch cho Linh" }),
    );

    const posted = calls.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/pr/work/plans") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(posted).toBeDefined();
    const body = JSON.parse(String((posted?.[1] as RequestInit).body));
    expect(body.user_id).toBe(LINH);
    expect(body.period_id).toBe(PERIOD);
  });
});

describe("3. history is secondary", () => {
  it("is a collapsed section under the current plan, not a peer of it", async () => {
    stubFetch(
      routes([], {
        history: [
          historyEntry(),
          historyEntry({
            id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02",
            version_no: 2,
            status: "SUPERSEDED",
            status_label: "Đã thay thế",
            superseded_at: "2026-09-04T04:00:00Z",
            is_current: false,
          }),
          historyEntry({
            id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01",
            version_no: 1,
            status: "SUPERSEDED",
            status_label: "Đã thay thế",
            superseded_at: "2026-09-02T04:00:00Z",
            is_current: false,
          }),
        ],
        extra: [{ match: `/api/pr/work/plans/${PLAN}`, body: { plan: null } }],
      }),
    );
    await openKpi();
    await userEvent.click(await screen.findByRole("button", { name: /Bùi Mỹ Hảo/ }));

    // Two past versions, and **the current one is not repeated in the list** -
    // the server marks it `is_current` and it is rendered above, as the editor.
    const details = await screen.findByText(/Lịch sử thay đổi \(2\)/);
    expect(details).toBeInTheDocument();
  });
});

// --- 4-14: THE UNIFIED MONTHLY WORK VIEW -----------------------------------

describe("4. the reporting month is the outer scope", () => {
  it("offers a period selector and defaults to the newest opened month", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);

    // Awaited on the list first, so the period query has certainly resolved -
    // a bare `findByLabelText` finds the empty select on the first render.
    await screen.findByText("Kháng page David");
    const picker = screen.getByLabelText("Kỳ báo cáo") as HTMLSelectElement;
    expect(picker.value).toBe(PERIOD);
    expect(within(picker).getByText("2026-08")).toBeInTheDocument();
  });

  it("sends the period with the list query", async () => {
    const calls = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    expect(
      calls.mock.calls.some(([url]) => String(url).includes(`period_id=${PERIOD}`)),
    ).toBe(true);
  });

  it("defaults to the whole month rather than to today", async () => {
    // **The default that changed.** The screen's outer scope is a reporting
    // month; opening it on "Hôm nay" made it answer a different question from
    // the one its own period selector poses.
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    const all = screen.getByRole("button", { name: "Tất cả tháng" });
    expect(all).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Hôm nay" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});

describe("5. one list, three sources", () => {
  it("renders content, manual and recurring work together", async () => {
    stubFetch(routes([contentItem(), recurringItem(), workItem()]));
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText("Kịch bản video Dr Tiến")).toBeInTheDocument();
    expect(screen.getByText("100 comment seeding")).toBeInTheDocument();
    expect(screen.getByText("Kháng page David")).toBeInTheDocument();
  });

  it("badges each row with the server's Vietnamese source label", async () => {
    stubFetch(routes([contentItem(), recurringItem(), workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    expect(screen.getByText("Từ nội dung")).toBeInTheDocument();
    expect(screen.getByText("Định kỳ", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Nhập thủ công")).toBeInTheDocument();
    // Never a raw code.
    expect(screen.queryByText("RECURRING")).toBeNull();
    expect(screen.queryByText("CONTENT")).toBeNull();
  });

  it("defaults the source filter to everything", async () => {
    stubFetch(routes([contentItem(), recurringItem(), workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    const nav = within(screen.getByRole("navigation", { name: "Nguồn công việc" }));
    expect(nav.getByRole("button", { name: "Tất cả" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

describe("6. the execution date", () => {
  it("is shown for content work, and is the milestone rather than the deadline", async () => {
    stubFetch(routes([contentItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    expect(screen.getByText(/^Thực hiện /)).toBeInTheDocument();
    // Content work carries no deadline at all, so the card says so rather than
    // borrowing the execution date for it.
    expect(screen.getByText(/Không có hạn/)).toBeInTheDocument();
  });

  it("is shown for recurring work beside a separate deadline", async () => {
    stubFetch(routes([recurringItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("100 comment seeding");

    expect(screen.getByText(/^Thực hiện /)).toBeInTheDocument();
    expect(screen.getByText(/^Hạn /)).toBeInTheDocument();
  });

  it("is absent for manual work, and no deadline is printed in its place", async () => {
    // **The invention this removes.** `due_at` under a heading that says the
    // work was performed is a deadline mislabelled as a performance.
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    expect(screen.queryByText(/Thực hiện/)).toBeNull();
    expect(screen.getByText(/^Hạn /)).toBeInTheDocument();
  });

  it("keeps undated work visible under its own heading", async () => {
    stubFetch(routes([contentItem(), workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    expect(screen.getByText("Chưa có ngày thực hiện")).toBeInTheDocument();
    expect(screen.getByText("Kháng page David")).toBeInTheDocument();
  });

  it("shows no undated heading when every row has a date", async () => {
    stubFetch(routes([contentItem(), recurringItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    expect(screen.queryByText("Chưa có ngày thực hiện")).toBeNull();
  });
});

describe("7. the filters are separate questions", () => {
  it("offers person and status filters beside the source and day strips", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    expect(screen.getByLabelText("Người thực hiện")).toBeInTheDocument();
    expect(screen.getByLabelText("Trạng thái")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Nguồn công việc" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Khoảng thời gian" })).toBeInTheDocument();
  });

  it("sends the person as a filter over the same monthly query", async () => {
    const calls = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    await userEvent.selectOptions(screen.getByLabelText("Người thực hiện"), HAO);
    expect(
      calls.mock.calls.some(
        ([url]) =>
          String(url).includes(`user_id=${HAO}`) &&
          String(url).includes(`period_id=${PERIOD}`),
      ),
    ).toBe(true);
  });

  it("sends the status without touching the source or the period", async () => {
    const calls = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    await userEvent.selectOptions(screen.getByLabelText("Trạng thái"), "COMPLETED");
    expect(
      calls.mock.calls.some(
        ([url]) =>
          String(url).includes("status=COMPLETED") &&
          String(url).includes(`period_id=${PERIOD}`) &&
          !String(url).includes("source_type="),
      ),
    ).toBe(true);
  });
});

describe("8. the summary strip says what it counts", () => {
  it("names the reporting period and warns that the day filter does not move it", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    expect(await screen.findByText(/Tính cho cả kỳ 2026-09/)).toBeInTheDocument();
    expect(screen.getByText(/không đổi.*theo bộ lọc ngày/s)).toBeInTheDocument();
  });

  it("asks the server for the period rather than a preset", async () => {
    const calls = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    const summaryCall = calls.mock.calls.find(([url]) =>
      String(url).includes("/api/pr/work/summary"),
    );
    expect(summaryCall).toBeDefined();
    expect(String(summaryCall?.[0])).toContain(`period_id=${PERIOD}`);
    expect(String(summaryCall?.[0])).not.toContain("preset=");
  });
});

describe("9. a recurring row names its template", () => {
  it("shows the routine's name rather than a bare source word", async () => {
    stubFetch(routes([recurringItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("100 comment seeding");

    expect(screen.getByText(/Nguồn: 100 comment mỗi ngày/)).toBeInTheDocument();
  });

  it("names the content code for content work", async () => {
    stubFetch(routes([contentItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video Dr Tiến");

    expect(screen.getByText(/Nguồn: CNT-2026-000042/)).toBeInTheDocument();
  });
});

describe("10. operational state is not KPI accounting", () => {
  it("shows content work as awaiting validation, never as counted", async () => {
    // The content workflow said the deliverable exists; whether it **counts**
    // is a different decision, taken by somebody who did not do the work.
    stubFetch(routes([contentItem()]));
    renderWithQuery(<WorkPage />);
    const card = (await screen.findByText("Kịch bản video Dr Tiến")).closest(
      "button",
    ) as HTMLElement;

    // Scoped to the card: the status filter above the list legitimately offers
    // the same word, and this assertion is about what the **row** says.
    expect(within(card).getByText("Chờ xác nhận")).toBeInTheDocument();
    // Also scoped: "Đã ghi nhận" is a summary tile above the list, counting the
    // month's validated work. The claim here is that **this row** does not
    // carry it.
    expect(within(card).queryByText("Đã ghi nhận")).toBeNull();
    // And the row carries no score, rate or eligibility verdict. M2 decides
    // whether this contribution counts towards a quota, later, from the
    // contribution rather than from what a card said.
    expect(within(card).queryByText(/điểm|phút chuẩn|ELIGIBLE|hợp lệ KPI/i)).toBeNull();
  });
});
