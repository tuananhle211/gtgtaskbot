/**
 * M2 - Kế hoạch KPI, and what the screen must never let somebody believe.
 *
 * Numbered 1-16 against the milestone's frontend requirement list.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Which words appear are `*_label` fields from the server; whether a control is
 * drawn is a `can_*` flag the server computed from the same checks the writes
 * make; every amount is a decimal string the server computed. A test that
 * passed because the component matched on `"OVER_QUOTA"` and rendered its own
 * Vietnamese - or, worse, did its own quota arithmetic - would be testing a
 * second implementation of the rule M2 exists to have only one of.
 *
 * The four that carry the milestone
 * ----------------------------------
 *
 * **Test 2** - counted work with no approved quota says so in words: *"Đã ghi
 * nhận công việc, chưa có hạn mức KPI."* Not zero, not a failure, not unlimited.
 * **Test 12** - an approved plan has no edit form at all; the only control is
 * *"Tạo bản điều chỉnh"*.
 * **Test 15** - a Trưởng nhóm gains no quota controls from `PR_WORK_MANAGE`.
 * **Test 7** - no point total, anywhere. `ELIGIBLE` is "đủ điều kiện tính KPI"
 * and never "đã được tính điểm". M6 owns scoring.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

/* `/pr/work` keeps its view and its filters in the URL, so it needs a router
   that really navigates - the same helper the ledger suite uses. */
const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const EMPLOYEE = ["PR_WORK_EXECUTE"];
const TEAM_LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const ADMIN = [
  "PR_WORK_EXECUTE",
  "PR_WORK_MANAGE",
  "PR_WORK_VALIDATE",
  "PR_WORK_CONFIGURE",
  "PR_WORK_VIEW_ALL",
];

const PERIOD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PLAN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const QUOTA_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const TYPE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const HAO = "22222222-2222-2222-2222-222222222222";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const period = (over: Record<string, unknown> = {}) => ({
  id: PERIOD_ID,
  code: "2026-09",
  period_type: "MONTH",
  date_start: "2026-09-01",
  date_end: "2026-09-30",
  status: "OPEN",
  closed_at: null,
  locked_at: null,
  ...over,
});

const WORK_TYPE = {
  id: TYPE_ID,
  code: "SHORT_SCRIPT",
  name: "Kịch bản video ngắn",
  category: "CONTENT",
  category_label: "Nội dung",
  description: null,
  default_unit: "ITEM",
  default_unit_label: "sản phẩm",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

/** One work type's five figures, exactly as the server composes them. */
const progress = (over: Record<string, unknown> = {}) => ({
  work_type_id: TYPE_ID,
  work_type_code: "SHORT_SCRIPT",
  work_type_name: "Kịch bản video ngắn",
  basis: "ITEM_COUNT",
  basis_label: "Theo số đầu việc",
  unit: null,
  unit_label: null,
  target_value: "20.00",
  eligibility_cap: "20.00",
  work_quota_id: QUOTA_ID,
  counted_contributions: 23,
  measured_contributions: 23,
  counted_amount: "23.00",
  eligible_amount: "20.00",
  over_quota_amount: "3.00",
  no_quota_amount: "0.00",
  no_quota_contributions: 0,
  unmeasurable_contributions: 0,
  pending_contributions: 0,
  target_progress: "20.00",
  extra_eligible_above_target: "0.00",
  ...over,
});

const summary = (over: Record<string, unknown> = {}) => ({
  user_id: HAO,
  period: period(),
  plan_id: PLAN_ID,
  plan_version_no: 1,
  plan_approved_at: "2026-09-01T02:00:00Z",
  types: [progress()],
  contributions_by_status: {
    NO_QUOTA: 0,
    UNMEASURABLE: 0,
    ELIGIBLE: 20,
    PARTIALLY_ELIGIBLE: 0,
    OVER_QUOTA: 3,
    PENDING_EVALUATION: 0,
  },
  counted_contributions: 23,
  counted_work_items: 23,
  ...over,
});

const quota = (over: Record<string, unknown> = {}) => ({
  id: QUOTA_ID,
  plan_id: PLAN_ID,
  work_type_id: TYPE_ID,
  work_type_code: "SHORT_SCRIPT",
  work_type_name: "Kịch bản video ngắn",
  basis: "ITEM_COUNT",
  basis_label: "Theo số đầu việc",
  basis_hint: "Mỗi đầu việc được ghi nhận tính là 1.",
  target_value: "20.00",
  eligibility_cap: "20.00",
  unit: null,
  unit_label: null,
  note: null,
  ...over,
});

const plan = (over: Record<string, unknown> = {}) => ({
  id: PLAN_ID,
  user_id: HAO,
  user_name: "Hảo",
  period_id: PERIOD_ID,
  period_code: "2026-09",
  period_status: "OPEN",
  version_no: 1,
  status: "DRAFT",
  status_label: "Bản nháp",
  supersedes_plan_id: null,
  note: null,
  created_by_user_id: HAO,
  approved_by_user_id: null,
  approved_at: null,
  superseded_at: null,
  discarded_at: null,
  created_at: "2026-09-01T02:00:00Z",
  quota_count: 1,
  ...over,
});

const planDetail = (over: Record<string, unknown> = {}) => ({
  plan: plan(),
  period: period(),
  quotas: [quota()],
  created_by_name: "Hà Trưởng Phòng",
  approved_by_name: null,
  can_edit: true,
  can_approve: true,
  can_revise: false,
  can_discard: true,
  ...over,
});

const eligibility = (over: Record<string, unknown> = {}) => ({
  contribution_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  work_item_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
  work_item_code: "WRK-2026-000001",
  work_item_title: "Kịch bản số 21",
  work_type_id: TYPE_ID,
  work_type_code: "SHORT_SCRIPT",
  work_type_name: "Kịch bản video ngắn",
  counted_at: "2026-09-10T02:00:00Z",
  quota_status: "OVER_QUOTA",
  quota_status_label: "Vượt hạn mức",
  quota_status_hint: "Công việc vẫn được ghi nhận đầy đủ, nhưng hạn mức KPI của kỳ đã dùng hết.",
  basis: "ITEM_COUNT",
  unit: null,
  unit_label: null,
  basis_amount: "1.00",
  eligible_amount: "0.00",
  over_quota_amount: "1.00",
  reason_code: null,
  reason_label: null,
  is_materialised: true,
  work_plan_id: PLAN_ID,
  work_quota_id: QUOTA_ID,
  evaluated_at: "2026-09-10T02:00:00Z",
  ...over,
});

const PEOPLE = [{ user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" }];

/**
 * **Post-M4.** One row per *employee*, which is what the KPI screen now asks
 * for. `GET /plans` still exists and still lists versions; it is no longer what
 * the main list is built from, because a list of versions cannot say who has no
 * plan and shows somebody on their third revision three times.
 */
const planSummary = (over: Record<string, unknown> = {}) => ({
  user_id: HAO,
  user_name: "Hảo",
  period_id: PERIOD_ID,
  has_plan: true,
  current_plan_id: PLAN_ID,
  current_version_no: 1,
  current_status: "DRAFT",
  current_status_label: "Bản nháp",
  quota_count: 1,
  approved_at: null,
  updated_at: "2026-09-01T02:00:00Z",
  latest_draft_id: PLAN_ID,
  history_count: 1,
  ...over,
});

const historyEntry = (over: Record<string, unknown> = {}) => ({
  id: PLAN_ID,
  version_no: 1,
  status: "DRAFT",
  status_label: "Bản nháp",
  quota_count: 1,
  created_at: "2026-09-01T02:00:00Z",
  approved_at: null,
  superseded_at: null,
  discarded_at: null,
  note: null,
  is_current: true,
  ...over,
});

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  {
    capabilities = EMPLOYEE,
    stats = summary(),
    periods = [period()],
    plans = [plan()],
    summaries = [planSummary()],
    history = [historyEntry()],
    detail = planDetail(),
    contributions = [eligibility()],
  }: {
    capabilities?: string[];
    stats?: Record<string, unknown>;
    periods?: Array<Record<string, unknown>>;
    plans?: Array<Record<string, unknown>>;
    summaries?: Array<Record<string, unknown>>;
    history?: Array<Record<string, unknown>>;
    detail?: Record<string, unknown>;
    contributions?: Array<Record<string, unknown>>;
  } = {},
  extra: Array<{ match: string; method?: string; status?: number; body?: unknown }> = [],
) => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/periods", body: periods },
  { match: "/api/pr/work/eligibility/summary", body: stats },
  {
    match: "/api/pr/work/eligibility",
    body: { period: period(), user_id: HAO, contributions },
  },
  // Both **before** the generic `/api/pr/work/plans` entry, which their paths
  // contain - `stubFetch` takes the first substring hit.
  { match: "/api/pr/work/plans/summary", body: { period_id: PERIOD_ID, items: summaries } },
  {
    match: "/api/pr/work/plans/history",
    body: { user_id: HAO, period_id: PERIOD_ID, items: history },
  },
  { match: `/api/pr/work/plans/${PLAN_ID}`, body: detail },
  {
    match: "/api/pr/work/plans",
    body: { plans, total: plans.length, limit: 50, offset: 0 },
  },
  { match: "/api/pr/work/summary", body: {} },
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

/** Arrive on the KPI view. It is a view of `/pr/work`, not a page of its own. */
const openKpi = async () => {
  await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
};

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-2: THE EMPLOYEE VIEW ------------------------------------------------

describe("1. the employee KPI plan renders", () => {
  it("shows the target, the counted work, the eligible amount and the excess", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openKpi();

    await screen.findByRole("heading", { name: "Kế hoạch KPI" });
    expect(screen.getByText("Kịch bản video ngắn")).toBeInTheDocument();
    // Four different facts, said as four figures. A single "KPI" number would
    // have to pick one and hide the rest.
    for (const label of ["Mục tiêu", "Đã ghi nhận", "Đủ điều kiện", "Vượt hạn mức"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // The basis is the server's label, not a table in the browser.
    expect(screen.getByText("Theo số đầu việc")).toBeInTheDocument();
  });

  it("asks the server for the chosen period rather than filtering here", async () => {
    const fetchMock = stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByRole("heading", { name: "Kế hoạch KPI" });

    const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
    expect(
      calls.some((call) => call.url.includes(`/eligibility/summary?period_id=${PERIOD_ID}`)),
    ).toBe(true);
  });
});

describe("2. work with no quota says so in words", () => {
  /**
   * **The milestone.** Missing quota is a decision nobody has taken, not zero
   * and not unlimited - so the sentence sends the reader to the person who can
   * take it rather than telling them their work was rejected.
   */
  it("shows the NO_QUOTA sentence and no target", async () => {
    stubFetch(
      routes({
        stats: summary({
          plan_id: null,
          plan_version_no: null,
          types: [
            progress({
              target_value: null,
              eligibility_cap: null,
              work_quota_id: null,
              counted_contributions: 4,
              measured_contributions: 4,
              counted_amount: "4.00",
              eligible_amount: "0.00",
              over_quota_amount: "0.00",
              no_quota_amount: "4.00",
              no_quota_contributions: 4,
              target_progress: "0.00",
            }),
          ],
          contributions_by_status: {
            NO_QUOTA: 4,
            UNMEASURABLE: 0,
            ELIGIBLE: 0,
            PARTIALLY_ELIGIBLE: 0,
            OVER_QUOTA: 0,
            PENDING_EVALUATION: 0,
          },
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();

    expect(
      await screen.findByText("Đã ghi nhận công việc, chưa có hạn mức KPI."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Chưa có kế hoạch KPI được duyệt cho kỳ này."),
    ).toBeInTheDocument();
    // No target row at all - showing "Mục tiêu 0" would be inventing a decision.
    expect(screen.queryByText("Mục tiêu")).not.toBeInTheDocument();
  });
});

// --- 3-6: THE FOUR SHAPES OF PROGRESS --------------------------------------

describe("3. ITEM_COUNT progress", () => {
  it("renders counts without a unit, because the amounts are jobs", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByRole("heading", { name: "Kế hoạch KPI" });

    // Read the four figures as label/value pairs rather than by text alone:
    // "20" is both the target and the eligible amount, and asserting on the
    // string would pass on a card that printed one number twice.
    expect(figure("Mục tiêu")).toBe("20");
    expect(figure("Đã ghi nhận")).toBe("23");
    expect(figure("Đủ điều kiện")).toBe("20");
    expect(figure("Vượt hạn mức")).toBe("3");
    // No unit word: a count of jobs is not a quantity of anything.
    expect(document.body.textContent).not.toContain("sản phẩm");
  });
});

describe("4. QUANTITY progress", () => {
  /**
   * The reason `QUANTITY` exists: "100 bình luận" is one work item, and a quota
   * on `ITEM_COUNT` would read it as 1.
   */
  it("renders the amount in the work type's own unit", async () => {
    stubFetch(
      routes({
        stats: summary({
          types: [
            progress({
              work_type_code: "SEEDING_COMMENT",
              work_type_name: "Seeding bình luận",
              basis: "QUANTITY",
              basis_label: "Theo số lượng",
              unit: "COMMENT",
              unit_label: "bình luận",
              target_value: "3000.00",
              eligibility_cap: "3000.00",
              counted_contributions: 32,
              counted_amount: "3150.00",
              eligible_amount: "3000.00",
              over_quota_amount: "150.00",
              target_progress: "3000.00",
            }),
          ],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();

    await screen.findByText("Seeding bình luận");
    // 3 150 comments from 32 work items, and the two figures stay apart: the
    // amount is a quantity in the work type's unit, not a count of rows.
    expect(figure("Mục tiêu")).toBe("3.000 bình luận");
    expect(figure("Đã ghi nhận")).toBe("3.150 bình luận");
    expect(figure("Đủ điều kiện")).toBe("3.000 bình luận");
    expect(figure("Vượt hạn mức")).toBe("150 bình luận");
  });
});

describe("5. PARTIALLY_ELIGIBLE rendering", () => {
  it("shows how much of one contribution fitted inside the cap", async () => {
    stubFetch(
      routes({
        contributions: [
          eligibility({
            quota_status: "PARTIALLY_ELIGIBLE",
            quota_status_label: "Đủ điều kiện một phần",
            unit: "COMMENT",
            unit_label: "bình luận",
            basis: "QUANTITY",
            basis_amount: "60.00",
            eligible_amount: "40.00",
            over_quota_amount: "20.00",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));

    // The badge carries the split, so "một phần" is a number rather than a hint.
    expect(
      await screen.findByText(/Đủ điều kiện một phần 40 bình luận\/60 bình luận/),
    ).toBeInTheDocument();
  });
});

describe("6. OVER_QUOTA rendering", () => {
  it("uses the server's label and never calls the work invalid", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));

    const row = (await screen.findByText("Kịch bản số 21")).closest("li")!;
    // The badge is the server's `quota_status_label`, not a table in the browser.
    expect(within(row).getByText("Vượt hạn mức")).toBeInTheDocument();
    expect(within(row).getByText("WRK-2026-000001")).toBeInTheDocument();
    // The work was done correctly and was counted in full; what ran out was the
    // cap. Nothing on the row says otherwise.
    const rendered = document.body.textContent ?? "";
    expect(rendered).not.toContain("Không hợp lệ");
    expect(rendered).not.toContain("Bị loại");
  });
});

// --- 7: NO POINTS ----------------------------------------------------------

describe("7. no point total appears anywhere", () => {
  /**
   * `ELIGIBLE` means *inside an approved quota*, which is a different sentence
   * from *worth something*. M6 owns points, and a screen promising them would be
   * describing a feature that does not exist.
   */
  it("shows eligibility and never a score, a rate or a bonus", async () => {
    stubFetch(routes({ capabilities: ADMIN }));
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByRole("heading", { name: "Kế hoạch KPI" });

    const rendered = document.body.textContent ?? "";
    expect(rendered).toContain("Đủ điều kiện");
    for (const forbidden of ["điểm", "Điểm", "hệ số", "thưởng", "xếp hạng"]) {
      expect(rendered, forbidden).not.toContain(forbidden);
    }
  });

  it("keeps the API client free of scoring fields", () => {
    // Structural: adding a score field to the wire types would be a red test
    // rather than a quiet feature.
    const source = read("lib/api.ts");
    const start = source.indexOf("export interface QuotaTypeProgress");
    const end = source.indexOf("export interface ReconcileOutcome");
    const block = source.slice(start, end);
    for (const forbidden of ["score", "points", "multiplier", "awarded", "bonus"]) {
      expect(block, forbidden).not.toContain(forbidden);
    }
  });
});

// --- 8-11: THE ADMINISTRATOR HALF ------------------------------------------

describe("8. an administrator creates a draft plan", () => {
  it("posts the person and the period, and says a draft decides nothing", async () => {
    const fetchMock = stubFetch(
      routes({ capabilities: ADMIN }, [
        { match: "/api/pr/work/plans", method: "POST", body: planDetail() },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();

    await userEvent.click(await screen.findByRole("button", { name: "Tạo kế hoạch" }));
    expect(
      screen.getByText(/Kế hoạch mới là bản nháp/),
    ).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Nhân sự"), HAO);
    await userEvent.click(screen.getByRole("button", { name: "Tạo bản nháp" }));

    const calls = (fetchMock as unknown as {
      calls: Array<{ url: string; method: string; body: unknown }>;
    }).calls;
    const created = calls.find(
      (call) => call.method === "POST" && call.url.endsWith("/api/pr/work/plans"),
    );
    expect(created?.body).toEqual({ user_id: HAO, period_id: PERIOD_ID });
  });
});

describe("9. adding a quota", () => {
  it("sends the target and the cap, and never the basis or the unit", async () => {
    const fetchMock = stubFetch(
      routes({ capabilities: ADMIN }, [
        {
          match: `/api/pr/work/plans/${PLAN_ID}/quotas`,
          method: "POST",
          body: planDetail(),
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));

    await userEvent.click(await screen.findByRole("button", { name: "Thêm hạn mức" }));
    await userEvent.selectOptions(screen.getByLabelText("Loại công việc"), TYPE_ID);
    await userEvent.type(screen.getByLabelText(/^Mục tiêu/), "20");
    await userEvent.type(screen.getByLabelText("Trần hạn mức"), "25");
    await userEvent.click(
      screen.getAllByRole("button", { name: "Thêm hạn mức" }).at(-1)!,
    );

    const calls = (fetchMock as unknown as {
      calls: Array<{ url: string; method: string; body: Record<string, unknown> }>;
    }).calls;
    const sent = calls.find((call) => call.method === "POST" && call.url.includes("/quotas"));
    expect(sent?.body).toEqual({
      work_type_id: TYPE_ID,
      target_value: "20",
      eligibility_cap: "25",
    });
    // The basis and the unit come from the work type, which is the semantic
    // authority - offering them would let somebody write a quota in units the
    // work is never recorded in.
    expect(sent?.body).not.toHaveProperty("basis");
    expect(sent?.body).not.toHaveProperty("unit");
  });
});

describe("10. validation errors are the server's", () => {
  it("renders the refusal rather than pre-empting it in the browser", async () => {
    stubFetch(
      routes({ capabilities: ADMIN }, [
        {
          match: `/api/pr/work/plans/${PLAN_ID}/quotas`,
          method: "POST",
          status: 422,
          body: {
            error: {
              code: "pr_validation_error",
              message: "eligibility_cap must be at least target_value",
              details: { field: "eligibility_cap", reason: "cap_below_target" },
            },
          },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));

    await userEvent.click(await screen.findByRole("button", { name: "Thêm hạn mức" }));
    await userEvent.selectOptions(screen.getByLabelText("Loại công việc"), TYPE_ID);
    await userEvent.type(screen.getByLabelText(/^Mục tiêu/), "20");
    await userEvent.type(screen.getByLabelText("Trần hạn mức"), "10");
    await userEvent.click(
      screen.getAllByRole("button", { name: "Thêm hạn mức" }).at(-1)!,
    );

    expect(
      await screen.findByText(/eligibility_cap must be at least target_value/),
    ).toBeInTheDocument();
  });
});

describe("11. approving a plan asks first", () => {
  it("names the person, the period and what cannot be undone", async () => {
    const fetchMock = stubFetch(
      routes({ capabilities: ADMIN }, [
        {
          match: `/api/pr/work/plans/${PLAN_ID}/approve`,
          method: "POST",
          body: planDetail({
            plan: plan({ status: "APPROVED", status_label: "Đang áp dụng" }),
            can_edit: false,
            can_approve: false,
            can_revise: true,
            can_discard: false,
          }),
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));

    await userEvent.click(await screen.findByRole("button", { name: "Duyệt kế hoạch" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Duyệt kế hoạch KPI này?")).toBeInTheDocument();
    expect(dialog.textContent).toContain("Hảo");
    expect(dialog.textContent).toContain("2026-09");
    // The thing somebody is most likely to assume wrongly.
    expect(dialog.textContent).toContain("không sửa trực tiếp được");

    await confirm();
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> })
        .calls;
      expect(
        calls.some((call) => call.method === "POST" && call.url.includes("/approve")),
      ).toBe(true);
    });
  });
});

// --- 12-13: IMMUTABILITY AND REVISION --------------------------------------

describe("12. an approved plan is read-only", () => {
  /**
   * **`can_edit` is false for every approved plan, whoever is asking.** Not a
   * permission that happened to fail - the immutability rule as a flag, so the
   * panel offers a revision rather than a disabled form.
   */
  it("offers no edit controls and says to create a revision instead", async () => {
    stubFetch(
      routes({
        capabilities: ADMIN,
        plans: [plan({ status: "APPROVED", status_label: "Đang áp dụng" })],
        detail: planDetail({
          plan: plan({
            status: "APPROVED",
            status_label: "Đang áp dụng",
            approved_at: "2026-09-01T02:00:00Z",
          }),
          approved_by_name: "Hà Trưởng Phòng",
          can_edit: false,
          can_approve: false,
          can_revise: true,
          can_discard: false,
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));

    expect(
      await screen.findByText(/Kế hoạch đã duyệt không sửa trực tiếp được/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo bản điều chỉnh" })).toBeInTheDocument();
    for (const gone of ["Thêm hạn mức", "Sửa", "Bỏ", "Duyệt kế hoạch"]) {
      expect(screen.queryByRole("button", { name: gone })).not.toBeInTheDocument();
    }
  });
});

describe("13. the revision flow", () => {
  it("asks first, then follows the new draft", async () => {
    const revision = planDetail({
      plan: plan({
        id: "99999999-9999-9999-9999-999999999999",
        version_no: 2,
        status: "DRAFT",
        status_label: "Bản nháp",
        supersedes_plan_id: PLAN_ID,
      }),
      can_edit: true,
      can_approve: true,
      can_revise: false,
      can_discard: true,
    });
    const fetchMock = stubFetch(
      routes(
        {
          capabilities: ADMIN,
          plans: [plan({ status: "APPROVED", status_label: "Đang áp dụng" })],
          detail: planDetail({
            plan: plan({ status: "APPROVED", status_label: "Đang áp dụng" }),
            can_edit: false,
            can_approve: false,
            can_revise: true,
            can_discard: false,
          }),
        },
        [
          {
            match: `/api/pr/work/plans/${PLAN_ID}/revise`,
            method: "POST",
            body: revision,
          },
          { match: "/api/pr/work/plans/99999999", body: revision },
        ],
      ),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));

    await userEvent.click(await screen.findByRole("button", { name: "Tạo bản điều chỉnh" }));
    const dialog = await screen.findByRole("dialog");
    // The description says the important half: nothing changes for the employee
    // until the revision is approved.
    expect(dialog.textContent).toContain("vẫn giữ nguyên hiệu lực");
    await confirm();

    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> })
        .calls;
      expect(
        calls.some((call) => call.method === "POST" && call.url.includes("/revise")),
      ).toBe(true);
    });
  });
});

// --- 14-16: WHO SEES WHAT --------------------------------------------------

describe("14. an employee sees no edit controls", () => {
  it("renders their own plan and none of the administration", async () => {
    stubFetch(routes({ capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByRole("heading", { name: "Kế hoạch KPI" });

    expect(screen.queryByText("Quản lý kế hoạch KPI")).not.toBeInTheDocument();
    for (const gone of ["Tạo kế hoạch", "Tính lại điều kiện KPI", "Mở kỳ"]) {
      expect(screen.queryByRole("button", { name: gone })).not.toBeInTheDocument();
    }
  });
});

describe("15. a team lead gains no quota controls", () => {
  /**
   * **The M1 scope model kept rather than regressed.** `PR_WORK_MANAGE` means
   * deciding whose job a piece of work is; deciding an arbitrary colleague's KPI
   * targets is a different act, and TasksBot models no team that would make a
   * narrower middle ground honest.
   */
  it("shows the plan view and none of the configuration", async () => {
    stubFetch(routes({ capabilities: TEAM_LEAD }));
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByRole("heading", { name: "Kế hoạch KPI" });

    expect(screen.queryByText("Quản lý kế hoạch KPI")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tạo kế hoạch" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Tính lại điều kiện KPI" }),
    ).not.toBeInTheDocument();
  });
});

describe("16. a closed period disables the recompute", () => {
  it("marks the period and offers no recompute the server would refuse", async () => {
    stubFetch(
      routes({
        capabilities: ADMIN,
        periods: [period({ status: "CLOSED", closed_at: "2026-10-01T02:00:00Z" })],
        stats: summary({ period: period({ status: "CLOSED" }) }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();

    expect(await screen.findByText(/Đã chốt · không tính lại/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Tính lại điều kiện KPI" }),
    ).not.toBeInTheDocument();
    // The read path still works on a closed period: reporting a reported month
    // must not be an error.
    expect(screen.getByRole("heading", { name: "Kế hoạch KPI" })).toBeInTheDocument();
  });
});


// --- 17-20: "NO ALLOCATION" IS NOT "NO QUOTA" -------------------------------
//
// The semantic patch, from the browser's side. Three states that used to be one
// sentence, and each sends the reader to a different person: ask for a quota,
// type a number onto a work item, or wait for an administrator to reconcile.

describe("17. UNMEASURABLE renders its own label and reason", () => {
  it("says the cap cannot be applied yet, and why, without an amount", async () => {
    stubFetch(
      routes({
        contributions: [
          eligibility({
            quota_status: "UNMEASURABLE",
            quota_status_label: "Chưa thể tính hạn mức",
            quota_status_hint:
              "Công việc đã được ghi nhận. Hạn mức KPI đã có, nhưng còn thiếu dữ liệu để đối chiếu.",
            reason_code: "MISSING_QUANTITY",
            reason_label: "Thiếu số lượng công việc.",
            basis_amount: null,
            eligible_amount: "0.00",
            over_quota_amount: "0.00",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));

    const row = (await screen.findByText("Kịch bản số 21")).closest("li")!;
    // Both come from the server: the browser invents no eligibility sentence.
    expect(within(row).getByText("Chưa thể tính hạn mức")).toBeInTheDocument();
    expect(within(row).getByText("Thiếu số lượng công việc.")).toBeInTheDocument();
    // And it is not the other sentence.
    expect(row.textContent).not.toContain("Chưa có hạn mức KPI");
    // No fabricated amount. A null is not a zero.
    expect(row.textContent).not.toContain("0.00");
  });

  it("never shows an internal exception detail", async () => {
    stubFetch(
      routes({
        contributions: [
          eligibility({
            quota_status: "UNMEASURABLE",
            quota_status_label: "Chưa thể tính hạn mức",
            reason_code: "UNIT_MISMATCH",
            reason_label: "Đơn vị công việc không khớp hạn mức KPI.",
            basis_amount: null,
            eligible_amount: "0.00",
            over_quota_amount: "0.00",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));
    await screen.findByText("Đơn vị công việc không khớp hạn mức KPI.");

    const rendered = document.body.textContent ?? "";
    for (const leak of ["Traceback", "Error", "Exception", "null", "undefined"]) {
      expect(rendered, leak).not.toContain(leak);
    }
  });
});

describe("18. NO_QUOTA and UNMEASURABLE are two different sentences", () => {
  it("renders both, and never the same words for both", async () => {
    stubFetch(
      routes({
        contributions: [
          eligibility({
            contribution_id: "eeeeeeee-0000-0000-0000-000000000001",
            work_item_title: "Seeding chưa nhập số",
            quota_status: "UNMEASURABLE",
            quota_status_label: "Chưa thể tính hạn mức",
            reason_code: "MISSING_QUANTITY",
            reason_label: "Thiếu số lượng công việc.",
            basis_amount: null,
            eligible_amount: "0.00",
            over_quota_amount: "0.00",
          }),
          eligibility({
            contribution_id: "eeeeeeee-0000-0000-0000-000000000002",
            work_item_title: "Ghi chú nghiên cứu",
            quota_status: "NO_QUOTA",
            quota_status_label: "Chưa có hạn mức KPI",
            reason_code: null,
            reason_label: null,
            basis_amount: "1.00",
            eligible_amount: "0.00",
            over_quota_amount: "0.00",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));

    const unmeasurable = (await screen.findByText("Seeding chưa nhập số")).closest("li")!;
    const noQuota = (await screen.findByText("Ghi chú nghiên cứu")).closest("li")!;
    expect(within(unmeasurable).getByText("Chưa thể tính hạn mức")).toBeInTheDocument();
    expect(within(noQuota).getByText("Chưa có hạn mức KPI")).toBeInTheDocument();
    // The one with a fixable field carries a reason; the one waiting on a
    // decision does not.
    expect(within(unmeasurable).getByText("Thiếu số lượng công việc.")).toBeInTheDocument();
    expect(noQuota.textContent).not.toContain("Thiếu số lượng");
  });
});

describe("19. the summary counts unmeasurable work separately", () => {
  /**
   * **Counted, never summed as zero.** A missing quantity is not a quantity of
   * nothing, and folding it into the totals would put a smaller number on the
   * screen than the work the employee did.
   */
  it("shows the count and says the totals leave it out", async () => {
    stubFetch(
      routes({
        stats: summary({
          types: [
            progress({
              work_type_code: "SEEDING_COMMENT",
              work_type_name: "Seeding bình luận",
              basis: "QUANTITY",
              basis_label: "Theo số lượng",
              unit: "COMMENT",
              unit_label: "bình luận",
              target_value: "100.00",
              eligibility_cap: "100.00",
              counted_contributions: 3,
              measured_contributions: 2,
              counted_amount: "120.00",
              eligible_amount: "100.00",
              over_quota_amount: "20.00",
              unmeasurable_contributions: 1,
              target_progress: "100.00",
            }),
          ],
          contributions_by_status: {
            NO_QUOTA: 0,
            UNMEASURABLE: 1,
            ELIGIBLE: 1,
            PARTIALLY_ELIGIBLE: 1,
            OVER_QUOTA: 0,
            PENDING_EVALUATION: 0,
          },
          counted_contributions: 3,
          counted_work_items: 3,
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByText("Seeding bình luận");

    // 120 comments, not 120 + 0.
    expect(figure("Đã ghi nhận")).toBe("120 bình luận");
    expect(figure("Đủ điều kiện")).toBe("100 bình luận");
    // And the row the totals leave out is named rather than hidden.
    expect(
      screen.getByText(/Chưa thể tính hạn mức cho/),
    ).toBeInTheDocument();
    // The footer keeps the two states apart.
    const rendered = document.body.textContent ?? "";
    expect(rendered).toContain("chưa thể tính hạn mức");
    expect(rendered).toContain("chưa có hạn mức");
  });
});

describe("20. an unevaluated contribution is not shown as NO_QUOTA", () => {
  /**
   * `PENDING_EVALUATION`. A target exists and nothing has looked at this yet -
   * which is an administrator's reconcile, not a missing quota.
   */
  it("says the period has not been recomputed, not that no quota exists", async () => {
    stubFetch(
      routes({
        stats: summary({
          types: [
            progress({
              counted_contributions: 5,
              measured_contributions: 0,
              counted_amount: "0.00",
              eligible_amount: "0.00",
              over_quota_amount: "0.00",
              pending_contributions: 5,
              target_progress: "0.00",
            }),
          ],
          contributions_by_status: {
            NO_QUOTA: 0,
            UNMEASURABLE: 0,
            ELIGIBLE: 0,
            PARTIALLY_ELIGIBLE: 0,
            OVER_QUOTA: 0,
            PENDING_EVALUATION: 5,
          },
          counted_contributions: 5,
          counted_work_items: 5,
        }),
        contributions: [
          eligibility({
            quota_status: "PENDING_EVALUATION",
            quota_status_label: "Chưa tính điều kiện KPI",
            quota_status_hint:
              "Công việc đã được ghi nhận. Hạn mức KPI đã có, nhưng kỳ này chưa được tính lại.",
            basis_amount: null,
            eligible_amount: null,
            over_quota_amount: null,
            reason_code: null,
            reason_label: null,
            is_materialised: false,
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await screen.findByText("Kịch bản video ngắn");

    expect(screen.getByText(/chưa được tính lại theo hạn mức/)).toBeInTheDocument();
    // Not the sentence for "nobody set you a target" - the plan is in force.
    expect(
      screen.queryByText("Đã ghi nhận công việc, chưa có hạn mức KPI."),
    ).not.toBeInTheDocument();

    await userEvent.click(await screen.findByText("Chi tiết từng đầu việc"));
    const row = (await screen.findByText("Kịch bản số 21")).closest("li")!;
    expect(within(row).getByText("Chưa tính điều kiện KPI")).toBeInTheDocument();
    // No fabricated amounts on an undecided row.
    expect(row.textContent).not.toContain("null");
  });
});

// --- M2.5 -----------------------------------------------------------------

describe("M2.5. an empty taxonomy is explained on the quota form too", () => {
  it("replaces the selector with guidance rather than offering nothing", async () => {
    stubFetch(
      routes({ capabilities: ADMIN }).map((route) =>
        // The one difference from the ordinary fixture: a department whose
        // `pr_work_types` is still empty, which is the production state M2.5
        // was opened for.
        route.match === "/api/pr/work/types" ? { ...route, body: [] } : route,
      ),
    );
    renderWithQuery(<WorkPage />);
    await openKpi();
    await userEvent.click(await screen.findByText("Hảo"));
    await userEvent.click(await screen.findByRole("button", { name: "Thêm hạn mức" }));

    expect(
      await screen.findByText("Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Loại công việc")).not.toBeInTheDocument();
  });
});

/** Read one frontend source file, for the structural assertion above. */
function read(relative: string): string {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  return fs.readFileSync(path.join(process.cwd(), "src", relative), "utf8");
}

/**
 * One figure from the progress card, read as a label/value pair.
 *
 * By pair rather than by text, because several of the four figures legitimately
 * carry the same number - a plan whose target is met exactly has "20" twice -
 * and a text query would pass on a card that printed one of them twice.
 */
function figure(label: string): string {
  const term = screen.getAllByText(label).find((node) => node.tagName === "DT");
  return term?.nextElementSibling?.textContent?.trim() ?? "";
}
