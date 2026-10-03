/**
 * KPI self-service: the employee proposes, the manager decides.
 *
 * Migration 0038. The employee's *Kế hoạch KPI* section now has four states -
 * no plan, editable draft, submitted, approved with an optional revision - and
 * every one of them is the server's reading of `plans/mine/summary` plus the
 * draft's own detail. The manager's list gains a review queue whose figures
 * are the server's `counts`, and a *Trả lại* control beside *Duyệt*.
 *
 * Nothing here decides readiness, review state or who may approve: those come
 * back as `can_submit`, `readiness_blocker_labels`, `draft_review_state` and
 * `can_return`, and the tests below assert the page draws what it is told.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);
const { default: WorkPage } = await import("@/app/pr/work/page");

const CONFIGURE = ["PR_WORK_EXECUTE", "PR_WORK_CONFIGURE", "PR_WORK_VIEW_ALL"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];
const ME = "22222222-2222-2222-2222-222222222222";
const PERIOD_ID = "66666666-6666-6666-6666-666666666666";
const TYPE_ID = "11111111-1111-1111-1111-111111111111";
const CURRENT = "33333333-3333-3333-3333-333333333333";
const DRAFT = "44444444-4444-4444-4444-444444444444";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const period = () => ({
  id: PERIOD_ID,
  code: "2026-09",
  period_type: "MONTH",
  date_start: "2026-09-01",
  date_end: "2026-09-30",
  status: "OPEN",
  status_label: "Đang mở",
  closed_at: null,
  locked_at: null,
});

const WORK_TYPE = {
  id: TYPE_ID,
  code: "GROUP_POST",
  name: "Bài Group/Chat khách/Order CTV",
  category: "CONTENT",
  category_label: "Nội dung",
  description: null,
  default_unit: "ITEM",
  default_unit_label: "đầu việc",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const quota = (planId: string, target = "30.00") => ({
  id: "99999999-9999-9999-9999-999999999999",
  plan_id: planId,
  work_type_id: TYPE_ID,
  work_type_name: WORK_TYPE.name,
  basis: "ITEM_COUNT",
  basis_label: "Theo số đầu việc",
  target_value: target,
  eligibility_cap: target,
  unit: null,
  unit_label: null,
  note: null,
});

// KPI workload visibility: the full server shape - complete, with a resolved
// month of 25 workdays at 300 minutes. The breakdown is empty here because
// these fixtures stand in for list rows and summaries; the editor tests in
// `kpi-workload.test.tsx` carry quotas.
const WORKLOAD = {
  projected_minutes: "7150.00",
  target_minutes: "7500.00",
  percent: "95.3",
  unscored_work_type_ids: [],
  priced_quota_count: 2,
  unpriced_quota_count: 0,
  excluded_quota_count: 0,
  is_complete: true,
  unpriced_work_types: [],
  target_unresolved_reason: null,
  target_unresolved_label: null,
  calendar_workdays: "25",
  approved_leave_days: "0",
  eligible_workdays: "25",
  daily_target_minutes: 300,
  target_is_overridden: false,
  rules_effective_on: "2026-09-30",
  quotas: [],
};

const plan = (over: Record<string, unknown> = {}) => ({
  id: DRAFT,
  user_id: ME,
  user_name: "Nguyễn Văn A",
  period_id: PERIOD_ID,
  period_code: "2026-09",
  period_status: "OPEN",
  version_no: 1,
  status: "DRAFT",
  status_label: "Bản nháp",
  supersedes_plan_id: null,
  note: null,
  created_by_user_id: ME,
  approved_by_user_id: null,
  approved_at: null,
  superseded_at: null,
  discarded_at: null,
  created_at: "2026-09-01T02:00:00Z",
  updated_at: "2026-09-05T09:30:00Z",
  quota_count: 1,
  review_state: "EDITING",
  review_state_label: "Bản nháp",
  submitted_at: null,
  submitted_by_user_id: null,
  returned_at: null,
  returned_by_user_id: null,
  return_note: null,
  ...over,
});

const detail = (over: Record<string, unknown> = {}, planOver: Record<string, unknown> = {}) => ({
  plan: plan(planOver),
  period: period(),
  quotas: [quota(DRAFT)],
  created_by_name: "Nguyễn Văn A",
  approved_by_name: null,
  can_edit: true,
  can_approve: false,
  can_revise: false,
  can_discard: true,
  is_subject: true,
  can_submit: true,
  can_return: false,
  readiness_blockers: [],
  readiness_blocker_labels: [],
  submitted_by_name: null,
  returned_by_name: null,
  workload: WORKLOAD,
  ...over,
});

const summary = (over: Record<string, unknown> = {}) => ({
  user_id: ME,
  user_name: "Nguyễn Văn A",
  period_id: PERIOD_ID,
  has_plan: true,
  current_plan_id: DRAFT,
  current_version_no: 1,
  current_status: "DRAFT",
  current_status_label: "Bản nháp",
  quota_count: 1,
  approved_at: null,
  updated_at: "2026-09-05T09:30:00Z",
  latest_draft_id: DRAFT,
  draft_version_no: 1,
  draft_quota_count: 1,
  draft_updated_at: "2026-09-05T09:30:00Z",
  history_count: 1,
  terminal_count: 0,
  draft_review_state: "EDITING",
  draft_review_state_label: "Bản nháp",
  draft_is_submitted: false,
  draft_submitted_at: null,
  draft_submitted_by_user_id: null,
  draft_returned_at: null,
  draft_return_note: null,
  draft_workload: WORKLOAD,
  current_workload: null,
  ...over,
});

const NO_PLAN = summary({
  has_plan: false,
  current_plan_id: null,
  current_version_no: null,
  current_status: null,
  current_status_label: null,
  quota_count: 0,
  updated_at: null,
  latest_draft_id: null,
  draft_version_no: null,
  draft_quota_count: 0,
  draft_updated_at: null,
  history_count: 0,
  draft_review_state: null,
  draft_review_state_label: null,
  draft_workload: null,
});

const SUBMITTED = summary({
  draft_review_state: "SUBMITTED",
  draft_review_state_label: "Chờ duyệt",
  draft_is_submitted: true,
  draft_submitted_at: "2026-09-08T07:20:00Z",
  draft_submitted_by_user_id: ME,
});

const RETURNED = summary({
  draft_review_state: "RETURNED",
  draft_review_state_label: "Cần chỉnh sửa",
  draft_returned_at: "2026-09-08T09:00:00Z",
  draft_return_note: "Mục tiêu video cần điều chỉnh.",
});

const APPROVED_ONLY = summary({
  current_plan_id: CURRENT,
  current_version_no: 4,
  current_status: "APPROVED",
  current_status_label: "Đang áp dụng",
  quota_count: 5,
  approved_at: "2026-09-04T11:21:00Z",
  latest_draft_id: null,
  draft_version_no: null,
  draft_quota_count: 0,
  draft_updated_at: null,
  history_count: 4,
  terminal_count: 3,
  draft_review_state: null,
  draft_review_state_label: null,
  draft_workload: null,
  current_workload: WORKLOAD,
});

const COUNTS = { pending_review: 1, approved: 2, drafting: 1, without_plan: 3 };

const routes = (
  {
    capabilities = EMPLOYEE,
    mine = summary(),
    draft = detail(),
    summaries = [summary()],
    counts = COUNTS,
  } = {},
  extra: Route[] = [],
): Route[] => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [{ user_id: ME, full_name: "Nguyễn Văn A", role: "EMPLOYEE" }] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/periods", body: [period()] },
  {
    match: "/api/pr/work/eligibility/summary",
    body: {
      user_id: ME,
      period: period(),
      plan_id: null,
      plan_version_no: null,
      plan_approved_at: null,
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
    },
  },
  { match: "/api/pr/work/eligibility", body: { period: period(), user_id: ME, contributions: [] } },
  { match: "/api/pr/work/plans/mine/summary", body: mine },
  { match: "/api/pr/work/plans/summary", body: { period_id: PERIOD_ID, items: summaries, counts } },
  { match: "/api/pr/work/plans/history", body: { user_id: ME, period_id: PERIOD_ID, items: [] } },
  { match: `/api/pr/work/plans/${DRAFT}`, body: draft },
  { match: `/api/pr/work/plans/${CURRENT}`, body: detail({ can_edit: false, can_submit: false, can_discard: true, can_revise: true }, { id: CURRENT, version_no: 4, status: "APPROVED", status_label: "Đang áp dụng", review_state: null, review_state_label: null, approved_at: "2026-09-04T11:21:00Z" }) },
  { match: "/api/pr/work/plans/mine", status: 404, body: { error: { code: "pr_not_found", message: "none", details: {} } } },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: {} },
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

async function openKpi() {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
}

const mySection = () => within(screen.getByRole("region", { name: "KPI của tôi" }));

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- EMPLOYEE ---------------------------------------------------------------

describe("75. no plan yet", () => {
  it("shows Tạo KPI của tôi and posts only the period", async () => {
    const stub = stubFetch(
      routes({ mine: NO_PLAN }, [
        { match: "/api/pr/work/plans/mine", method: "POST", status: 201, body: detail() },
      ]),
    );
    await openKpi();
    expect(await screen.findByText(/Chưa có kế hoạch KPI cho kỳ 2026-09/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo KPI của tôi" }));
    await waitFor(() => {
      const posted = (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/api/pr/work/plans/mine"),
      );
      expect(posted?.body).toEqual({ period_id: PERIOD_ID });
    });
  });
});

describe("76. approved plan, no draft", () => {
  it("shows the plan in force and offers Đề xuất điều chỉnh, which revises it", async () => {
    const stub = stubFetch(
      routes({ mine: APPROVED_ONLY }, [
        { match: `/api/pr/work/plans/${CURRENT}/revise`, method: "POST", body: detail() },
      ]),
    );
    await openKpi();
    const section = mySection();
    expect(await section.findByText("Kế hoạch đang áp dụng · bản v4")).toBeInTheDocument();
    expect(section.queryByRole("button", { name: /Gửi duyệt/ })).toBeNull();
    await userEvent.click(section.getByRole("button", { name: "Đề xuất điều chỉnh" }));
    await waitFor(() =>
      expect(
        (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls.some(
          (call) => call.method === "POST" && call.url.endsWith(`/plans/${CURRENT}/revise`),
        ),
      ).toBe(true),
    );
  });
});

describe("77. editable draft", () => {
  it("shows the draft with edit, save, discard and submit, and the projected workload", async () => {
    stubFetch(routes());
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    expect(card.getByText("Bản nháp")).toBeInTheDocument();
    expect(card.getByText(WORK_TYPE.name)).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Thêm hạn mức" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Bỏ bản nháp" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Gửi duyệt" })).toBeEnabled();
    expect(card.getByText("Tải dự kiến")).toBeInTheDocument();
    expect(card.getByText(/7\.150 \/ 7\.500 phút · 95,3%/)).toBeInTheDocument();
    // No approval control for the subject, whatever they see.
    expect(card.queryByRole("button", { name: /^Duyệt/ })).toBeNull();
  });

  it("submits through a confirmation and says what the server refused when not ready", async () => {
    const stub = stubFetch(
      routes({}, [
        {
          match: `/api/pr/work/plans/${DRAFT}/submit`,
          method: "POST",
          body: detail({ can_edit: false, can_submit: false, can_discard: false }, {
            review_state: "SUBMITTED",
            review_state_label: "Chờ duyệt",
            submitted_at: "2026-09-08T07:20:00Z",
            submitted_by_user_id: ME,
          }),
        },
      ]),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    await userEvent.click(card.getByRole("button", { name: "Gửi duyệt" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Gửi bản v1 cho trưởng phòng duyệt?");
    await confirm();
    await waitFor(() =>
      expect(
        (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls.some(
          (call) => call.method === "POST" && call.url.endsWith(`/plans/${DRAFT}/submit`),
        ),
      ).toBe(true),
    );
  });

  it("disables Gửi duyệt and lists the server's reasons for an unready draft", async () => {
    stubFetch(
      routes({
        draft: detail(
          { quotas: [], can_submit: false, readiness_blockers: ["plan_has_no_quotas"], readiness_blocker_labels: ["Cần ít nhất một chỉ tiêu."] },
          { quota_count: 0 },
        ),
      }),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    expect(await card.findByRole("button", { name: "Gửi duyệt" })).toBeDisabled();
    expect(card.getByText("Cần ít nhất một chỉ tiêu.")).toBeInTheDocument();
  });
});

describe("78. submitted draft", () => {
  it("is read-only and says Chờ trưởng phòng duyệt with the time", async () => {
    stubFetch(
      routes({
        mine: SUBMITTED,
        draft: detail({ can_edit: false, can_submit: false, can_discard: false }, {
          review_state: "SUBMITTED",
          review_state_label: "Chờ duyệt",
          submitted_at: "2026-09-08T07:20:00Z",
          submitted_by_user_id: ME,
        }),
      }),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    expect(card.getByText("Chờ trưởng phòng duyệt")).toBeInTheDocument();
    expect(card.getByText(/Gửi lúc/)).toBeInTheDocument();
    expect(card.queryByRole("button", { name: "Thêm hạn mức" })).toBeNull();
    expect(card.queryByRole("button", { name: "Gửi duyệt" })).toBeNull();
    expect(card.queryByRole("button", { name: "Bỏ bản nháp" })).toBeNull();
    expect(card.getByText(WORK_TYPE.name)).toBeInTheDocument();
  });
});

describe("79. approved plan with a submitted revision", () => {
  it("keeps the plan in force and the proposal as two cards", async () => {
    stubFetch(
      routes({
        mine: summary({
          ...APPROVED_ONLY,
          latest_draft_id: DRAFT,
          draft_version_no: 5,
          draft_quota_count: 1,
          draft_review_state: "SUBMITTED",
          draft_review_state_label: "Chờ duyệt",
          draft_is_submitted: true,
          draft_submitted_at: "2026-09-08T07:20:00Z",
          draft_workload: WORKLOAD,
        }),
        draft: detail({ can_edit: false, can_submit: false, can_discard: false }, {
          version_no: 5,
          supersedes_plan_id: CURRENT,
          review_state: "SUBMITTED",
          review_state_label: "Chờ duyệt",
          submitted_at: "2026-09-08T07:20:00Z",
        }),
      }),
    );
    await openKpi();
    const section = mySection();
    expect(await section.findByText("Kế hoạch đang áp dụng · bản v4")).toBeInTheDocument();
    const revision = within(screen.getByRole("region", { name: "Bản điều chỉnh · v5" }));
    expect(revision.getByText("Chờ trưởng phòng duyệt")).toBeInTheDocument();
    expect(revision.getByText(/vẫn giữ nguyên hiệu lực/)).toBeInTheDocument();
    // No second "Đề xuất điều chỉnh" while a revision is in flight.
    expect(section.queryByRole("button", { name: "Đề xuất điều chỉnh" })).toBeNull();
  });
});

describe("80. returned draft", () => {
  it("says Cần chỉnh sửa with the reason, and is editable and resubmittable", async () => {
    stubFetch(
      routes({
        mine: RETURNED,
        draft: detail({}, {
          review_state: "RETURNED",
          review_state_label: "Cần chỉnh sửa",
          returned_at: "2026-09-08T09:00:00Z",
          returned_by_user_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          return_note: "Mục tiêu video cần điều chỉnh.",
        }),
      }),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    expect(card.getByText("Cần chỉnh sửa")).toBeInTheDocument();
    expect(card.getAllByText(/Mục tiêu video cần điều chỉnh/).length).toBeGreaterThan(0);
    expect(card.getByRole("button", { name: "Thêm hạn mức" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Gửi lại duyệt" })).toBeEnabled();
  });
});

describe("81. mobile", () => {
  it("stacks the draft's actions and never lays quotas out as a wide table", async () => {
    stubFetch(routes());
    await openKpi();
    const card = await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" });
    expect(card.querySelector("table")).toBeNull();
    const actions = within(card).getByRole("button", { name: "Gửi duyệt" }).parentElement!;
    expect(actions.className).toContain("flex-col");
    expect(actions.className).toContain("sm:flex-row");
    for (const button of within(card).getAllByRole("button")) {
      expect(button.className).toMatch(/min-h-(9|11)/);
    }
  });
});

// --- MANAGER ----------------------------------------------------------------

const managerRows = () => [
  summary({ user_id: ME, user_name: "Nguyễn Văn A", draft_review_state: "SUBMITTED", draft_review_state_label: "Chờ duyệt", draft_is_submitted: true, draft_submitted_at: "2026-09-08T07:20:00Z" }),
  summary({ user_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", user_name: "Trần B", latest_draft_id: "bbbbbbbb-0000-0000-0000-000000000001", current_plan_id: "bbbbbbbb-0000-0000-0000-000000000001" }),
  NO_PLAN && summary({ ...NO_PLAN, user_id: "cccccccc-cccc-cccc-cccc-cccccccccccc", user_name: "Lê C" }),
];

describe("82-83. the manager's queue", () => {
  it("shows Chờ duyệt with the server's count and filters to submitted drafts", async () => {
    stubFetch(
      routes({
        capabilities: CONFIGURE,
        summaries: managerRows(),
        counts: { pending_review: 1, approved: 0, drafting: 1, without_plan: 1 },
        draft: detail({ is_subject: false, can_submit: false, can_return: true, can_approve: true }, { review_state: "SUBMITTED", review_state_label: "Chờ duyệt", submitted_at: "2026-09-08T07:20:00Z" }),
      }),
    );
    await openKpi();
    const tabs = await screen.findByRole("tablist", { name: "Trạng thái kế hoạch" });
    expect(within(tabs).getByRole("tab", { name: "Chờ duyệt 1" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Bản nháp 1" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Chưa có KPI 1" })).toBeInTheDocument();
    await userEvent.click(within(tabs).getByRole("tab", { name: "Chờ duyệt 1" }));
    const list = screen.getByRole("region", { name: "Quản lý kế hoạch KPI" });
    expect(within(list).getByRole("button", { name: /Nguyễn Văn A/ })).toHaveTextContent("Chờ duyệt");
    expect(within(list).getByRole("button", { name: /Nguyễn Văn A/ })).toHaveTextContent("Xem & duyệt");
    expect(within(list).queryByText("Trần B")).toBeNull();
  });
});

describe("84-86. manager review actions", () => {
  const managerDraft = () =>
    detail({ is_subject: false, can_submit: false, can_return: true, can_approve: true, can_discard: true }, {
      review_state: "SUBMITTED",
      review_state_label: "Chờ duyệt",
      submitted_at: "2026-09-08T07:20:00Z",
      submitted_by_user_id: ME,
    });

  async function openSubmitted(extra: Route[] = []) {
    const stub = stubFetch(
      routes({ capabilities: CONFIGURE, summaries: managerRows(), draft: managerDraft() }, extra),
    );
    await openKpi();
    const list = screen.getByRole("region", { name: "Quản lý kế hoạch KPI" });
    await userEvent.click(await within(list).findByRole("button", { name: /Nguyễn Văn A/ }));
    return stub;
  }

  it("84. lets the manager edit the submitted proposal in place", async () => {
    await openSubmitted();
    const list = within(screen.getByRole("region", { name: "Quản lý kế hoạch KPI" }));
    expect(await list.findByText(/gửi duyệt/)).toBeInTheDocument();
    expect(list.getByRole("button", { name: "Thêm hạn mức" })).toBeInTheDocument();
    expect(list.getByRole("button", { name: "Sửa" })).toBeInTheDocument();
  });

  it("85. returns with a reason, through a confirmation", async () => {
    const stub = await openSubmitted([
      { match: `/api/pr/work/plans/${DRAFT}/return`, method: "POST", body: detail({}, { review_state: "RETURNED", review_state_label: "Cần chỉnh sửa", return_note: "Thêm video." }) },
    ]);
    const list = within(screen.getByRole("region", { name: "Quản lý kế hoạch KPI" }));
    await userEvent.type(await list.findByLabelText("Lý do trả lại"), "Thêm video.");
    await userEvent.click(list.getByRole("button", { name: "Trả lại để chỉnh sửa" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Trả lại bản v1 để chỉnh sửa?");
    await confirm();
    await waitFor(() => {
      const posted = (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls.find(
        (call) => call.method === "POST" && call.url.endsWith(`/plans/${DRAFT}/return`),
      );
      expect(posted?.body).toEqual({ note: "Thêm video." });
    });
  });

  it("86. approves through the canonical route, labelled Duyệt và áp dụng", async () => {
    const stub = await openSubmitted([
      { match: `/api/pr/work/plans/${DRAFT}/approve`, method: "POST", body: detail({ can_edit: false, can_return: false, can_approve: false }, { status: "APPROVED", status_label: "Đang áp dụng", review_state: null, review_state_label: null, approved_at: "2026-09-09T02:00:00Z" }) },
    ]);
    const list = within(screen.getByRole("region", { name: "Quản lý kế hoạch KPI" }));
    await userEvent.click(await list.findByRole("button", { name: "Duyệt và áp dụng" }));
    await confirm();
    await waitFor(() =>
      expect(
        (stub as unknown as { calls: Array<{ url: string; method: string }> }).calls.some(
          (call) => call.method === "POST" && call.url.endsWith(`/plans/${DRAFT}/approve`),
        ),
      ).toBe(true),
    );
  });
});

describe("89. a draft quota may be moved to another active kind of work", () => {
  /**
   * Work-taxonomy cleanup. The editor offers the **active** types the server
   * lists, keeps the current type as the default, and sends `work_type_id`
   * only when a different one was chosen. Basis, unit and workload are the
   * server's to re-derive; nothing here recomputes them.
   */
  const OTHER = {
    ...WORK_TYPE,
    id: "55555555-5555-5555-5555-555555555555",
    code: "SHORT_SCRIPT_V2",
    name: "Kịch bản ngắn/Bài đăng",
  };

  it("offers the other active types and sends the chosen one", async () => {
    const stub = stubFetch(
      routes({}, [
        { match: "/api/pr/work/types", method: "GET", body: [WORK_TYPE, OTHER] },
        { match: `/api/pr/work/plans/${DRAFT}/quotas/`, method: "PATCH", body: detail() },
      ]),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    await userEvent.click(card.getByRole("button", { name: "Sửa" }));
    const picker = await card.findByRole("combobox", { name: "Loại công việc của chỉ tiêu" });
    expect(within(picker).getByRole("option", { name: "Giữ loại hiện tại" })).toBeInTheDocument();
    expect(
      within(picker).getByRole("option", { name: "Kịch bản ngắn/Bài đăng" }),
    ).toBeInTheDocument();
    // The current type is not offered as a "move": it is the default option.
    expect(within(picker).queryByRole("option", { name: WORK_TYPE.name })).toBeNull();

    await userEvent.selectOptions(picker, OTHER.id);
    await userEvent.click(card.getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const sent = (
        stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
      ).calls.find((call) => call.method === "PATCH" && call.url.includes("/quotas/"));
      expect(sent?.body).toMatchObject({ work_type_id: OTHER.id });
    });
  });

  it("renders the duplicate-type refusal from its code", async () => {
    stubFetch(
      routes({}, [
        { match: "/api/pr/work/types", method: "GET", body: [WORK_TYPE, OTHER] },
        {
          match: `/api/pr/work/plans/${DRAFT}/quotas/`,
          method: "PATCH",
          status: 422,
          body: {
            error: {
              code: "validation_error",
              message: "Kế hoạch đã có chỉ tiêu cho loại công việc này.",
              details: { reason: "quota_work_type_already_exists", field: "work_type_id" },
            },
          },
        },
      ]),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    await userEvent.click(card.getByRole("button", { name: "Sửa" }));
    await userEvent.selectOptions(
      await card.findByRole("combobox", { name: "Loại công việc của chỉ tiêu" }),
      OTHER.id,
    );
    await userEvent.click(card.getByRole("button", { name: "Lưu" }));
    expect(await screen.findByText(/đã có chỉ tiêu cho loại công việc này/)).toBeInTheDocument();
  });
});

describe("87-88. codes, not sentences; no employee approval", () => {
  it("renders the server's reason for a stale save in Vietnamese, from the code", async () => {
    stubFetch(
      routes({}, [
        {
          match: `/api/pr/work/plans/${DRAFT}/quotas/`,
          method: "PATCH",
          status: 409,
          body: {
            error: {
              code: "pr_work_plan_state",
              message: "This draft has been submitted for review and is locked for its author",
              details: { reason: "draft_submitted_locked", plan_id: DRAFT, version_no: 1 },
            },
          },
        },
      ]),
    );
    await openKpi();
    const card = within(await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" }));
    await userEvent.click(card.getByRole("button", { name: "Sửa" }));
    await userEvent.click(card.getByRole("button", { name: "Lưu" }));
    expect(await screen.findByText(/đã gửi duyệt/)).toBeInTheDocument();
    expect(screen.queryByText(/locked for its author/)).toBeNull();
  });

  it("offers an employee no approval control anywhere", async () => {
    stubFetch(routes({ mine: SUBMITTED, draft: detail({ can_edit: false, can_submit: false, can_discard: false }, { review_state: "SUBMITTED", review_state_label: "Chờ duyệt", submitted_at: "2026-09-08T07:20:00Z" }) }));
    await openKpi();
    await screen.findByRole("region", { name: "Kế hoạch KPI · bản v1" });
    expect(screen.queryByRole("button", { name: /^Duyệt/ })).toBeNull();
    expect(screen.queryByRole("region", { name: "Quản lý kế hoạch KPI" })).toBeNull();
  });
});
