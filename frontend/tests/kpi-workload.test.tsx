/**
 * KPI workload visibility: the screen prints the server's arithmetic.
 *
 * What is under test, in the order the request listed it:
 *
 * * the manager's list shows every employee's applied workload on the row,
 *   a draft's as a separate proposed figure, and no "0%" for anybody without
 *   a plan (21-23);
 * * an incomplete plan shows the priced minutes, the unpriced count and no
 *   percentage; a plan with no resolved month shows minutes and the server's
 *   sentence, never "0%" or "NaN%" (7-11, 14);
 * * the editor prints, per quota, target · rule → contribution from the
 *   server's breakdown, marks a missing rule, and after a target edit shows
 *   the value the server returned (26-32);
 * * the approval screen carries the proposed figure and the breakdown (36,
 *   40); the config table prints the rule with its basis (41-43);
 * * no React file multiplies a target by a rate, divides by a month, or
 *   hard-codes 6 600 / 7 500 (15, load-bearing rule).
 */

import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { channelsNavigation, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);
const { default: WorkPage } = await import("@/app/pr/work/page");

const CONFIGURE = ["PR_WORK_EXECUTE", "PR_WORK_CONFIGURE", "PR_WORK_VIEW_ALL"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];
const ME = "22222222-2222-2222-2222-222222222222";
const HANG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const PERIOD_ID = "66666666-6666-6666-6666-666666666666";
const POSTS = "11111111-1111-1111-1111-111111111111";
const TINY = "12121212-1212-4121-8121-121212121212";
const SEEDING = "13131313-1313-4131-8131-131313131313";
const NEW_TYPE = "14141414-1414-4141-8141-141414141414";
const CURRENT = "33333333-3333-3333-3333-333333333333";
const DRAFT = "44444444-4444-4444-4444-444444444444";
const Q_POSTS = "99999999-9999-9999-9999-999999999999";
const Q_TINY = "98989898-9898-4989-8989-989898989898";
const Q_SEEDING = "97979797-9797-4979-8979-979797979797";
const Q_NEW = "96969696-9696-4969-8969-969696969696";

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

const workType = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  code: name.toUpperCase().replace(/\W+/g, "_"),
  name,
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
  ...over,
});

const TYPES = [
  workType(POSTS, "Bài Group/Chat khách/Order CTV"),
  workType(TINY, "Kịch bản siêu ngắn"),
  workType(SEEDING, "120 Comment seeding", {
    default_unit: "COMMENT",
    default_unit_label: "bình luận",
    default_quota_basis: "QUANTITY",
    default_quota_basis_label: "Theo số lượng",
  }),
  workType(NEW_TYPE, "Loại mới"),
];

const quota = (
  id: string,
  planId: string,
  type: (typeof TYPES)[number],
  target: string,
  over: Record<string, unknown> = {},
) => ({
  id,
  plan_id: planId,
  work_type_id: type.id,
  work_type_name: type.name,
  basis: type.default_quota_basis,
  basis_label: type.default_quota_basis_label,
  basis_hint: "",
  target_value: target,
  eligibility_cap: target,
  unit: type.default_quota_basis === "QUANTITY" ? type.default_unit : null,
  unit_label: type.default_quota_basis === "QUANTITY" ? type.default_unit_label : null,
  note: null,
  ...over,
});

/** The server's per-quota provenance: the rule and the product, worded there. */
const quotaWorkload = (
  quotaId: string,
  type: (typeof TYPES)[number],
  target: string,
  over: Record<string, unknown> = {},
) => ({
  quota_id: quotaId,
  work_type_id: type.id,
  work_type_code: type.code,
  work_type_name: type.name,
  measurement_mode: type.default_quota_basis,
  measurement_mode_label: type.default_quota_basis_label,
  target_value: target,
  target_unit_label: type.default_quota_basis === "QUANTITY" ? type.default_unit_label : "đầu việc",
  standard_minutes_per_unit: "30.0000",
  rule_label: "30 phút / đầu việc",
  rule_version_no: 1,
  rule_effective_from: "2026-01-01",
  contribution_minutes: "840.00",
  is_priced: true,
  status: "PRICED",
  status_label: "Đã quy đổi",
  ...over,
});

const workload = (over: Record<string, unknown> = {}) => ({
  projected_minutes: "6204.00",
  target_minutes: "6600.00",
  percent: "94.0",
  unscored_work_type_ids: [],
  priced_quota_count: 3,
  unpriced_quota_count: 0,
  excluded_quota_count: 0,
  is_complete: true,
  unpriced_work_types: [],
  target_unresolved_reason: null,
  target_unresolved_label: null,
  calendar_workdays: "22",
  approved_leave_days: "0",
  eligible_workdays: "22",
  daily_target_minutes: 300,
  target_is_overridden: false,
  rules_effective_on: "2026-09-30",
  quotas: [],
  ...over,
});

const BREAKDOWN = [
  quotaWorkload(Q_TINY, TYPES[1], "10.00", {
    standard_minutes_per_unit: "15.0000",
    rule_label: "15 phút / đầu việc",
    contribution_minutes: "150.00",
  }),
  quotaWorkload(Q_POSTS, TYPES[0], "28.00"),
  quotaWorkload(Q_SEEDING, TYPES[2], "600.00", {
    standard_minutes_per_unit: "1.0000",
    rule_label: "1 phút / bình luận",
    contribution_minutes: "600.00",
  }),
];

const INCOMPLETE = workload({
  projected_minutes: "5940.00",
  percent: null,
  unscored_work_type_ids: [NEW_TYPE],
  priced_quota_count: 4,
  unpriced_quota_count: 1,
  is_complete: false,
  unpriced_work_types: [{ work_type_id: NEW_TYPE, work_type_name: "Loại mới" }],
  quotas: [
    ...BREAKDOWN,
    quotaWorkload(Q_NEW, TYPES[3], "3.00", {
      standard_minutes_per_unit: null,
      rule_label: null,
      rule_version_no: null,
      rule_effective_from: null,
      contribution_minutes: null,
      is_priced: false,
      status: "NO_SCORING_RULE",
      status_label: "Chưa cấu hình quy tắc workload",
    }),
  ],
});

const NO_TARGET = workload({
  projected_minutes: "5940.00",
  target_minutes: null,
  percent: null,
  target_unresolved_reason: "no_active_work_schedule",
  target_unresolved_label: "Chưa có lịch làm việc đang áp dụng cho kỳ này.",
  calendar_workdays: null,
  approved_leave_days: null,
  eligible_workdays: null,
});

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
  quota_count: 3,
  review_state: "EDITING",
  review_state_label: "Bản nháp",
  submitted_at: null,
  submitted_by_user_id: null,
  returned_at: null,
  returned_by_user_id: null,
  return_note: null,
  ...over,
});

const QUOTAS = (planId: string) => [
  quota(Q_TINY, planId, TYPES[1], "10.00"),
  quota(Q_POSTS, planId, TYPES[0], "28.00"),
  quota(Q_SEEDING, planId, TYPES[2], "600.00"),
];

const detail = (over: Record<string, unknown> = {}, planOver: Record<string, unknown> = {}) => ({
  plan: plan(planOver),
  period: period(),
  quotas: QUOTAS(DRAFT),
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
  workload: workload({ quotas: BREAKDOWN }),
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
  quota_count: 3,
  approved_at: null,
  updated_at: "2026-09-05T09:30:00Z",
  latest_draft_id: DRAFT,
  draft_version_no: 1,
  draft_quota_count: 3,
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
  draft_workload: workload(),
  current_workload: null,
  ...over,
});

/** Thanh Hằng: v4 in force at 94%, a v5 proposal at 108%. The request's example. */
const HANG_ROW = summary({
  user_id: HANG,
  user_name: "Thanh Hằng",
  current_plan_id: CURRENT,
  current_version_no: 4,
  current_status: "APPROVED",
  current_status_label: "Đang áp dụng",
  quota_count: 5,
  approved_at: "2026-09-09T03:40:00Z",
  latest_draft_id: DRAFT,
  draft_version_no: 5,
  draft_quota_count: 7,
  history_count: 5,
  terminal_count: 3,
  draft_review_state: "SUBMITTED",
  draft_review_state_label: "Chờ duyệt",
  draft_is_submitted: true,
  draft_submitted_at: "2026-09-08T07:20:00Z",
  draft_workload: workload({
    projected_minutes: "7128.00",
    percent: "108.0",
    priced_quota_count: 7,
  }),
  current_workload: workload(),
});

const NO_PLAN_ROW = summary({
  user_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  user_name: "Lê C",
  has_plan: false,
  current_plan_id: null,
  current_version_no: null,
  current_status: null,
  current_status_label: null,
  quota_count: 0,
  latest_draft_id: null,
  draft_version_no: null,
  draft_quota_count: 0,
  draft_review_state: null,
  draft_review_state_label: null,
  draft_workload: null,
  current_workload: null,
});

const INCOMPLETE_ROW = summary({
  user_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  user_name: "Trần D",
  current_plan_id: "dddddddd-0000-4000-8000-000000000001",
  latest_draft_id: "dddddddd-0000-4000-8000-000000000001",
  current_status: "APPROVED",
  current_status_label: "Đang áp dụng",
  draft_review_state: null,
  draft_workload: null,
  current_workload: INCOMPLETE,
});

const routes = (
  {
    capabilities = EMPLOYEE,
    mine = summary(),
    draft = detail(),
    current = detail(
      {
        can_edit: false,
        can_submit: false,
        can_discard: false,
        can_revise: true,
        is_subject: false,
      },
      {
        id: CURRENT,
        user_id: HANG,
        user_name: "Thanh Hằng",
        version_no: 4,
        status: "APPROVED",
        status_label: "Đang áp dụng",
        review_state: null,
        review_state_label: null,
        approved_at: "2026-09-09T03:40:00Z",
        quota_count: 3,
      },
    ),
    summaries = [summary()],
  } = {},
  extra: Route[] = [],
): Route[] => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [{ user_id: ME, full_name: "Nguyễn Văn A", role: "EMPLOYEE" }] },
  { match: "/api/pr/work/types", body: TYPES },
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
  {
    match: "/api/pr/work/plans/summary",
    body: {
      period_id: PERIOD_ID,
      items: summaries,
      counts: { pending_review: 1, approved: 1, drafting: 0, without_plan: 1 },
    },
  },
  { match: "/api/pr/work/plans/history", body: { user_id: ME, period_id: PERIOD_ID, items: [] } },
  { match: `/api/pr/work/plans/${DRAFT}`, body: draft },
  { match: `/api/pr/work/plans/${CURRENT}`, body: current },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: {} },
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

type Calls = { calls: Array<{ url: string; method: string; body: unknown }> };

async function openKpi() {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
}

const managerList = () => within(screen.getByRole("region", { name: "Quản lý kế hoạch KPI" }));
const mySection = () => within(screen.getByRole("region", { name: "KPI của tôi" }));

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("21-25. the manager's list", () => {
  it("prints the applied workload on every row, from the batched list response", async () => {
    const stub = stubFetch(
      routes({ capabilities: CONFIGURE, summaries: [HANG_ROW, NO_PLAN_ROW, INCOMPLETE_ROW] }),
    ) as unknown as Calls;
    await openKpi();
    const row = await managerList().findByRole("button", { name: /Thanh Hằng/ });
    expect(row).toHaveTextContent("Kế hoạch hiện tại: v4 · 5 hạn mức");
    expect(row).toHaveTextContent(/Tải KPI\s*6\.204 \/ 6\.600 phút · 94%/);
    // The proposal is its own figure under its own label - never added.
    expect(row).toHaveTextContent(/Tải đề xuất\s*7\.128 \/ 6\.600 phút · 108%/);
    expect(row).not.toHaveTextContent("13.332");
    // No detail was fetched to draw the rows: the list response carried it.
    expect(stub.calls.some((call) => call.url.includes(`/plans/${CURRENT}`))).toBe(false);
  });

  it("shows nobody without a plan at 0%", async () => {
    stubFetch(routes({ capabilities: CONFIGURE, summaries: [HANG_ROW, NO_PLAN_ROW] }));
    await openKpi();
    await managerList().findByRole("button", { name: /Thanh Hằng/ });
    const none = managerList().getByText("Lê C").closest("div") as HTMLElement;
    expect(none).toHaveTextContent("Chưa có kế hoạch");
    expect(none).not.toHaveTextContent("%");
    expect(none).not.toHaveTextContent("Tải KPI");
  });

  it("does not present an incomplete plan as a trustworthy percentage", async () => {
    stubFetch(routes({ capabilities: CONFIGURE, summaries: [INCOMPLETE_ROW] }));
    await openKpi();
    const row = await managerList().findByRole("button", { name: /Trần D/ });
    expect(row).toHaveTextContent("5.940 phút");
    expect(row).toHaveTextContent("1 chỉ tiêu chưa quy đổi");
    expect(row).not.toHaveTextContent("90%");
    expect(row).not.toHaveTextContent("%");
  });
});

describe("7-11, 14. incomplete pricing and a missing month", () => {
  it("names the unpriced type, keeps the priced minutes and withholds the percentage", async () => {
    stubFetch(
      routes({
        mine: summary({ draft_workload: INCOMPLETE }),
        draft: detail({
          workload: INCOMPLETE,
          quotas: [...QUOTAS(DRAFT), quota(Q_NEW, DRAFT, TYPES[3], "3.00")],
        }),
      }),
    );
    await openKpi();
    const card = mySection();
    const box = await card.findByTestId("workload-summary");
    expect(box).toHaveTextContent("Tải đã tính");
    expect(box).toHaveTextContent("5.940 phút");
    expect(box).toHaveTextContent("1 chỉ tiêu chưa quy đổi · Chưa thể tính chính xác % tải");
    expect(box).toHaveTextContent("Chưa có quy tắc workload: Loại mới");
    expect(box).not.toHaveTextContent("90%");
    // The unpriced quota is marked on its own line, and shows no number.
    const gap = card
      .getAllByTestId("quota-workload")
      .find((node) => node.getAttribute("data-status") === "NO_SCORING_RULE") as HTMLElement;
    expect(gap).toHaveTextContent("⚠ Chưa cấu hình quy tắc workload");
    expect(gap).not.toHaveTextContent("phút");
  });

  it("shows minutes and the server's reason when the month has no target", async () => {
    stubFetch(
      routes({
        mine: summary({ draft_workload: NO_TARGET }),
        draft: detail({ workload: { ...NO_TARGET, quotas: BREAKDOWN } }),
      }),
    );
    await openKpi();
    const box = await mySection().findByTestId("workload-summary");
    expect(box).toHaveTextContent("Tải dự kiến");
    expect(box).toHaveTextContent("5.940 phút");
    expect(box).toHaveTextContent("Chưa có lịch làm việc đang áp dụng cho kỳ này.");
    expect(box).not.toHaveTextContent("%");
    expect(box).not.toHaveTextContent("NaN");
  });
});

describe("26-32. the employee's editor", () => {
  it("prints target · rule → contribution per quota, from the server's breakdown", async () => {
    stubFetch(routes());
    await openKpi();
    const card = mySection();
    await card.findByTestId("workload-summary");
    const lines = card.getAllByTestId("quota-workload").map((node) => node.textContent);
    expect(lines).toEqual([
      "10 đầu việc · 15 phút/đầu việc → 150 phút",
      "28 đầu việc · 30 phút/đầu việc → 840 phút",
      "600 bình luận · 1 phút/bình luận → 600 phút",
    ]);
    expect(card.getByTestId("workload-summary")).toHaveTextContent(
      /Tải dự kiến\s*6\.204 \/ 6\.600 phút · 94%/,
    );
    expect(card.getByTestId("workload-summary")).toHaveTextContent(
      /Mốc 100% của tháng: 6\.600 phút = 22 ngày công × 300 phút/,
    );
  });

  it("shows the server's recomputed contribution after a target edit, on the same version", async () => {
    const after = detail({
      quotas: [
        quota(Q_TINY, DRAFT, TYPES[1], "10.00"),
        quota(Q_POSTS, DRAFT, TYPES[0], "35.00"),
        quota(Q_SEEDING, DRAFT, TYPES[2], "600.00"),
      ],
      workload: workload({
        projected_minutes: "6414.00",
        percent: "97.2",
        quotas: [
          BREAKDOWN[0],
          quotaWorkload(Q_POSTS, TYPES[0], "35.00", { contribution_minutes: "1050.00" }),
          BREAKDOWN[2],
        ],
      }),
    });
    // The save answers with the recomputed detail, and from then on so does
    // the plan's own GET - the editor invalidates and refetches after a save,
    // and a stub that kept answering with the old plan would be testing the
    // stub. The route list is read on every call, so the swap takes effect
    // for the refetch that follows the PATCH.
    const list = routes({});
    const planRoute = list.find((route) => route.match === `/api/pr/work/plans/${DRAFT}`)!;
    list.unshift({
      match: `/api/pr/work/plans/${DRAFT}/quotas/${Q_POSTS}`,
      method: "PATCH",
      get body() {
        planRoute.body = after;
        return after;
      },
    });
    const stub = stubFetch(list) as unknown as Calls;
    await openKpi();
    const card = mySection();
    await card.findByTestId("workload-summary");
    const edit = card.getAllByRole("button", { name: "Sửa" })[1];
    await userEvent.click(edit);
    const target = card.getByLabelText("Mục tiêu mới");
    await userEvent.clear(target);
    await userEvent.type(target, "35");
    const cap = card.getByLabelText("Trần hạn mức mới");
    await userEvent.clear(cap);
    await userEvent.type(cap, "35");
    await userEvent.click(card.getByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(card.getAllByTestId("quota-workload")[1]).toHaveTextContent(
        "35 đầu việc · 30 phút/đầu việc → 1.050 phút",
      ),
    );
    expect(card.getByTestId("workload-summary")).toHaveTextContent(/6\.414 \/ 6\.600 phút · 97,2%/);
    // The client sent targets; it multiplied nothing.
    const sent = stub.calls.find((call) => call.method === "PATCH");
    expect(sent?.body).toEqual({ target_value: "35", eligibility_cap: "35" });
    expect(JSON.stringify(sent?.body)).not.toContain("1050");
  });

  it("keeps a submitted draft's workload visible, read-only, above Gửi duyệt's place", async () => {
    stubFetch(
      routes({
        mine: summary({
          draft_review_state: "SUBMITTED",
          draft_review_state_label: "Chờ duyệt",
          draft_is_submitted: true,
          draft_submitted_at: "2026-09-08T07:20:00Z",
        }),
        draft: detail(
          { can_edit: false, can_submit: false, can_discard: false },
          {
            review_state: "SUBMITTED",
            review_state_label: "Chờ duyệt",
            submitted_at: "2026-09-08T07:20:00Z",
          },
        ),
      }),
    );
    await openKpi();
    const card = mySection();
    const box = await card.findByTestId("workload-summary");
    expect(box).toHaveTextContent(/Tải đề xuất\s*6\.204 \/ 6\.600 phút · 94%/);
    expect(card.queryByRole("button", { name: "Sửa" })).toBeNull();
    expect(card.getAllByTestId("quota-workload")).toHaveLength(3);
  });

  it("offers the employee no way to edit a workload rule", async () => {
    stubFetch(routes());
    await openKpi();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).toBeNull();
    expect(screen.queryByText("Quy tắc workload")).toBeNull();
  });
});

describe("36, 40. the manager's review", () => {
  it("shows the proposed figure and the breakdown before Duyệt và áp dụng", async () => {
    stubFetch(
      routes(
        {
          capabilities: CONFIGURE,
          summaries: [HANG_ROW],
          draft: detail(
            {
              is_subject: false,
              can_submit: false,
              can_return: true,
              can_approve: true,
              can_discard: true,
            },
            {
              user_id: HANG,
              user_name: "Thanh Hằng",
              version_no: 5,
              supersedes_plan_id: CURRENT,
              review_state: "SUBMITTED",
              review_state_label: "Chờ duyệt",
              submitted_at: "2026-09-08T07:20:00Z",
            },
          ),
        },
        [
          {
            match: "/api/pr/work/plans/history",
            body: {
              user_id: HANG,
              period_id: PERIOD_ID,
              items: [
                {
                  id: DRAFT,
                  version_no: 5,
                  status: "DRAFT",
                  status_label: "Bản nháp",
                  quota_count: 3,
                  created_at: "2026-09-08T07:00:00Z",
                  approved_at: null,
                  superseded_at: null,
                  discarded_at: null,
                  note: null,
                  is_current: false,
                  is_active_draft: true,
                },
                {
                  id: CURRENT,
                  version_no: 4,
                  status: "APPROVED",
                  status_label: "Đang áp dụng",
                  quota_count: 3,
                  created_at: "2026-09-01T07:00:00Z",
                  approved_at: "2026-09-09T03:40:00Z",
                  superseded_at: null,
                  discarded_at: null,
                  note: null,
                  is_current: true,
                  is_active_draft: false,
                },
              ],
            },
          },
        ],
      ),
    );
    await openKpi();
    await userEvent.click(await managerList().findByRole("button", { name: /Thanh Hằng/ }));
    const list = managerList();
    // The plan in force opens first, with its own applied figure...
    const boxes = await list.findAllByTestId("workload-summary");
    expect(boxes[0]).toHaveTextContent(/Tải KPI\s*6\.204 \/ 6\.600 phút · 94%/);
    // ...and the proposal panel carries the proposed one beside Duyệt.
    const panel = within(screen.getByRole("region", { name: "Bản điều chỉnh đang soạn" }));
    expect(await panel.findByTestId("workload-summary")).toHaveTextContent(
      /Tải đề xuất\s*6\.204 \/ 6\.600 phút · 94%/,
    );
    expect(panel.getByRole("button", { name: "Duyệt và áp dụng" })).toBeInTheDocument();
    await userEvent.click(panel.getByRole("button", { name: "Tiếp tục chỉnh sửa" }));
    expect((await panel.findAllByTestId("quota-workload")).map((node) => node.textContent)).toEqual(
      [
        "10 đầu việc · 15 phút/đầu việc → 150 phút",
        "28 đầu việc · 30 phút/đầu việc → 840 phút",
        "600 bình luận · 1 phút/bình luận → 600 phút",
      ],
    );
  });
});

describe("15, load-bearing. no second formula in React", () => {
  const root = path.resolve(__dirname, "../src/app/pr/work");
  const strip = (text: string) =>
    text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("hard-codes neither 6 600 nor 7 500, and multiplies no target by a rate", () => {
    for (const file of ["kpi.tsx", "workload.tsx"]) {
      const source = strip(fs.readFileSync(path.join(root, file), "utf8"));
      expect(source, file).not.toMatch(/\b6[.,]?600\b/);
      expect(source, file).not.toMatch(/\b7[.,]?500\b/);
      expect(source, file).not.toMatch(/target_value\)?\s*\*/);
      expect(source, file).not.toMatch(/\*\s*Number\([^)]*standard_minutes/);
      expect(source, file).not.toMatch(/projected_minutes\)?\s*\/\s*Number/);
    }
    // No work type is special-cased by name anywhere in the module.
    for (const file of fs.readdirSync(root)) {
      const source = strip(fs.readFileSync(path.join(root, file), "utf8"));
      expect(source, file).not.toMatch(/work_type_name\s*===\s*"/);
      expect(source, file).not.toMatch(/\.name\s*===\s*"/);
    }
  });
});
