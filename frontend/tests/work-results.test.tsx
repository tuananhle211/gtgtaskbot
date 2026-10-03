/**
 * Period containers on the Work page: WORK is actual output, KPI is a target.
 *
 * The browser draws what the server said and computes nothing. Every figure on
 * a stream card - "27 / 20 khách hàng", "135%", "+7 vượt chỉ tiêu", "10.260
 * điểm" - is a response field, and the assertions here check that the number
 * on screen is the number in the response and never a product of two of them.
 *
 * **The default scope** is the other half: with no `scope` in the URL the
 * request carries none, so the server picks *Toàn bộ* for anybody who may see
 * it, and the selector shows the same answer.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

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
const EMPLOYEE = ["PR_WORK_EXECUTE"];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];

const HAO = "22222222-2222-2222-2222-222222222222";
const PERIOD = "66666666-6666-6666-6666-666666666666";
const TYPE = "11111111-1111-1111-1111-111111111111";
const ITEM = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const RESULT = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeee1";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const period = () => ({
  id: PERIOD,
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
  id: TYPE,
  code: "FIND_CUSTOMERS",
  name: "Tìm khách hàng",
  category: "COMMUNITY",
  category_label: "Cộng đồng",
  description: null,
  default_unit: "CUSTOMER",
  default_unit_label: "khách hàng",
  default_quota_basis: "QUANTITY",
  default_quota_basis_label: "Theo số lượng",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Bùi Mỹ Hảo", role: "EMPLOYEE" },
];

const container = (over: Record<string, unknown> = {}) => ({
  period_id: PERIOD,
  period_code: "2026-09",
  period_status: "OPEN",
  subject_user_id: HAO,
  subject_name: "Bùi Mỹ Hảo",
  unit: "CUSTOMER",
  unit_label: "khách hàng",
  actual_quantity: "27.00",
  declared_quantity: "32.00",
  pending_quantity: "5.00",
  excluded_quantity: "0.00",
  result_count: 6,
  pending_count: 1,
  target_quantity: "20.00",
  has_target: true,
  completion_percent: "135.0",
  over_target_quantity: "7.00",
  remaining_quantity: "0.00",
  is_target_met: true,
  progress_percent: "100",
  actual_label: "27 / 20 khách hàng",
  standard_minutes: "10260.00",
  standard_minutes_per_unit: "380.0000",
  scoring_status: "SCORED",
  scoring_status_label: "Đã quy đổi",
  ...over,
});

const result = (over: Record<string, unknown> = {}) => ({
  id: RESULT,
  work_item_id: ITEM,
  user_id: HAO,
  quantity: "5.00",
  label: "Khách tuần 2",
  link: "https://example.com/khach",
  note: null,
  source_type: "MANUAL",
  source_label: "Tự báo cáo",
  source_key: null,
  status: "PENDING",
  status_label: "Chưa ghi nhận",
  reported_by_user_id: HAO,
  reported_by_name: "Bùi Mỹ Hảo",
  reported_at: "2026-09-08T02:00:00Z",
  counted_at: null,
  counted_by_user_id: null,
  counted_by_name: null,
  excluded_at: null,
  excluded_by_user_id: null,
  excluded_by_name: null,
  excluded_reason: null,
  exclusion_kind: null,
  exclusion_kind_label: null,
  held_by_validator: false,
  can_withdraw: false,
  can_validate: true,
  can_reject: true,
  can_reconsider: false,
  ...over,
});

const stream = (over: Record<string, unknown> = {}) => ({
  id: ITEM,
  code: "WRK-2026-000009",
  title: "Tìm khách hàng — 2026-09",
  description: null,
  work_type_id: TYPE,
  work_type_code: "FIND_CUSTOMERS",
  work_type_name: "Tìm khách hàng",
  work_type_category: "COMMUNITY",
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  is_period_container: true,
  reporting_period_id: PERIOD,
  subject_user_id: HAO,
  period_container: container(),
  content_code: null,
  content_id: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  status: "IN_PROGRESS",
  status_label: "Đang ghi nhận kết quả",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: "27.00",
  unit: "CUSTOMER",
  unit_label: "khách hàng",
  due_at: null,
  execution_at: "2026-08-31T17:00:00Z",
  is_overdue: false,
  created_by_user_id: HAO,
  assigned_by_user_id: null,
  assigned_at: null,
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [
    {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      work_item_id: ITEM,
      user_id: HAO,
      user_name: "Bùi Mỹ Hảo",
      contribution_role: "PRIMARY",
      contribution_role_label: "Phụ trách chính",
      credit_weight: "1.0000",
      assigned_at: "2026-09-01T02:00:00Z",
      count_status: "COUNTED",
      count_status_label: "Đã ghi nhận",
      counted_at: "2026-09-08T02:00:00Z",
      excluded_reason: null,
    },
  ],
  ...over,
});

const detail = (over: Record<string, unknown> = {}) => ({
  item: stream(),
  work_type: WORK_TYPE,
  evidence: [],
  content_code: null,
  can_manage: true,
  can_validate: true,
  can_execute: true,
  results: [result()],
  is_subject: false,
  can_report_result: true,
  can_validate_results: true,
  ...over,
});

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-10-01T00:00:00Z",
  created: 1,
  accepted: 1,
  completed: 0,
  approved: 0,
  counted_work_items: 1,
  counted_contributions: 1,
  open: 1,
  in_progress: 1,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
});

const routes = (
  items: Array<Record<string, unknown>>,
  { capabilities = OWNER, detailBody = detail() } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/periods", body: [period()] },
  { match: "/api/pr/work/readiness", body: {} },
  { match: "/api/pr/work/results", body: detailBody, method: "POST" },
  { match: `/api/pr/work/${ITEM}/results/validate`, body: detailBody, method: "POST" },
  { match: `/api/pr/work/${ITEM}/results`, body: detailBody, method: "POST" },
  { match: `/api/pr/work/${ITEM}/history`, body: [] },
  { match: `/api/pr/work/${ITEM}`, body: detailBody },
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

const sent = (calls: ReturnType<typeof stubFetch>) =>
  (calls as unknown as { calls: Array<{ url: string; method: string; body: any }> }).calls;

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1: THE CARD -------------------------------------------------------------

describe("1. a stream card reads actual against target, uncapped", () => {
  it("prints 27 / 20, 135% and +7 as the server said them", async () => {
    stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);

    const card = (await screen.findByText("Tìm khách hàng — 2026-09")).closest("button")!;
    expect(within(card).getByText(/Thực tế 27 \/ 20 khách hàng/)).toBeInTheDocument();
    expect(within(card).getByText(/135%/)).toBeInTheDocument();
    expect(within(card).getByText(/\+7 vượt chỉ tiêu/)).toBeInTheDocument();
    expect(within(card).getByText(/10\.260 điểm/)).toBeInTheDocument();
    expect(within(card).getByText("Đang ghi nhận kết quả")).toBeInTheDocument();
    // The bar is the server's capped figure; the text is not.
    const bar = within(card).getByRole("progressbar", { name: "Tiến độ so với KPI" });
    expect(bar).toHaveAttribute("aria-valuenow", "100");
    expect(within(card).getByText(/5 khách hàng chờ xác nhận/)).toBeInTheDocument();
  });

  it("reads KPI — and still prints points when there is no target", async () => {
    stubFetch(
      routes([
        stream({
          period_container: container({
            actual_quantity: "3.00",
            declared_quantity: "3.00",
            pending_quantity: "0.00",
            pending_count: 0,
            target_quantity: null,
            has_target: false,
            completion_percent: null,
            over_target_quantity: "0",
            is_target_met: false,
            progress_percent: "0",
            actual_label: "3 khách hàng",
            standard_minutes: "1140.00",
          }),
        }),
      ]),
    );
    renderWithQuery(<WorkPage />);

    const card = (await screen.findByText("Tìm khách hàng — 2026-09")).closest("button")!;
    expect(within(card).getByText(/Thực tế 3 khách hàng/)).toBeInTheDocument();
    expect(within(card).getByText(/KPI —/)).toBeInTheDocument();
    expect(within(card).getByText(/1\.140 điểm/)).toBeInTheDocument();
    expect(within(card).queryByText(/%/)).toBeNull();
    expect(within(card).queryByRole("progressbar")).toBeNull();
  });
});

// --- 2: THE DETAIL -------------------------------------------------------------

describe("2. the detail lists results and offers the result controls", () => {
  it("shows the results, the validate button, and no one-off lifecycle buttons", async () => {
    stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));

    expect(await screen.findByText("Kết quả trong kỳ 2026-09")).toBeInTheDocument();
    expect(screen.getByText(/\+5 khách hàng/)).toBeInTheDocument();
    expect(screen.getByText("Khách tuần 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xác nhận 1 kết quả chờ" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Thêm kết quả" })).toBeInTheDocument();
    // A stream is not "finished" or "approved" like a job.
    expect(screen.queryByRole("button", { name: "Hoàn thành" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Xác nhận hoàn thành" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Bắt đầu" })).toBeNull();
  });

  it("validates through the results route, never the approve route", async () => {
    const calls = stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await userEvent.click(await screen.findByRole("button", { name: "Xác nhận 1 kết quả chờ" }));

    await waitFor(() => {
      const posts = sent(calls).filter((call) => call.method === "POST");
      expect(posts.some((call) => call.url.includes(`/api/pr/work/${ITEM}/results/validate`))).toBe(
        true,
      );
      expect(posts.some((call) => call.url.endsWith("/approve"))).toBe(false);
    });
  });

  it("hides the validate button from the subject", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({ is_subject: true, can_validate_results: false }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));

    await screen.findByText("Kết quả trong kỳ 2026-09");
    expect(screen.queryByRole("button", { name: /Xác nhận .* kết quả chờ/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Thêm kết quả" })).toBeInTheDocument();
  });
});

// --- 3: THE REPORT FORM ---------------------------------------------------------

describe("3. the generic report form", () => {
  it("posts quantity, label and link to the results route with no target", async () => {
    const calls = stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Tìm khách hàng — 2026-09");
    await userEvent.click(screen.getByRole("button", { name: "Báo cáo kết quả" }));

    await userEvent.selectOptions(screen.getByLabelText("Loại công việc"), TYPE);
    const quantity = screen.getByLabelText("Số lượng");
    await userEvent.clear(quantity);
    await userEvent.type(quantity, "3");
    await userEvent.type(screen.getByLabelText("Nhãn"), "Khách A");
    await userEvent.type(screen.getByLabelText("Link"), "https://example.com/a");
    await userEvent.click(screen.getByRole("button", { name: "Lưu kết quả" }));

    await waitFor(() => {
      const post = sent(calls).find(
        (call) => call.method === "POST" && call.url.endsWith("/api/pr/work/results"),
      );
      expect(post?.body).toMatchObject({
        work_type_id: TYPE,
        period_id: PERIOD,
        quantity: "3",
        label: "Khách A",
        link: "https://example.com/a",
      });
      expect(post?.body).not.toHaveProperty("target_quantity");
    });
  });

  it("lets an employee report without naming anybody", async () => {
    stubFetch(routes([stream()], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Tìm khách hàng — 2026-09");
    await userEvent.click(screen.getByRole("button", { name: "Báo cáo kết quả" }));

    expect(screen.getByLabelText("Loại công việc")).toBeInTheDocument();
    expect(screen.queryByLabelText("Người thực hiện")).toBeNull();
    expect(screen.getByRole("button", { name: "Lưu kết quả" })).toBeDisabled();
  });
});

// --- 4: THE DEFAULT SCOPE -----------------------------------------------------

describe("4. no scope in the URL means Toàn bộ, decided by the server", () => {
  it("sends no scope and shows Toàn bộ for somebody who may see the department", async () => {
    const calls = stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Tìm khách hàng — 2026-09");

    const list = sent(calls).find(
      (call) => call.method === "GET" && /\/api\/pr\/work\?/.test(call.url),
    );
    expect(list?.url).not.toContain("scope=");
    expect((screen.getByRole("combobox", { name: "Phạm vi" }) as HTMLSelectElement).value).toBe(
      "ALL",
    );
  });

  it("keeps an explicit scope from the URL", async () => {
    NAV.arriveAt("/pr/work?scope=ASSIGNED_BY_ME");
    const calls = stubFetch(routes([stream()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Tìm khách hàng — 2026-09");

    const list = sent(calls).find(
      (call) => call.method === "GET" && /\/api\/pr\/work\?/.test(call.url),
    );
    expect(list?.url).toContain("scope=ASSIGNED_BY_ME");
    expect((screen.getByRole("combobox", { name: "Phạm vi" }) as HTMLSelectElement).value).toBe(
      "ASSIGNED_BY_ME",
    );
  });
});


// --- FINAL CONTENT → WORK SEMANTICS: A MODERN CONTENT RESULT ------------------
//
// Numbered against the resync task: 66 the sync is offered on a content
// result, 67 to ADMIN/OWNER only, 68 as a control separate from the removal,
// 69 the removal never calls the projector, 70 the sync calls the single-source
// projection route, 71 a successful sync re-reads the detail, 72 a business
// outcome reads as Vietnamese, never as an error.

const CONTENT_ID = "77777777-7777-7777-7777-777777777777";
const PROJECT = `/api/pr/work/content/${CONTENT_ID}/project`;

const contentResult = (over: Record<string, unknown> = {}) =>
  result({
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    source_type: "CONTENT",
    source_label: "Từ quy trình nội dung",
    source_key: `content:${CONTENT_ID}:CONTENT_CREATION`,
    content_id: CONTENT_ID,
    label: "CNT-2026-000776 · Một kiểu trưởng thành rất buồn",
    status: "COUNTED",
    status_label: "Đã ghi nhận",
    counted_at: "2026-09-10T03:25:00Z",
    counted_by_user_id: HAO,
    counted_by_name: "Bùi Mỹ Hảo",
    ...over,
  });

const projection = (outcome: string) => ({
  content_id: CONTENT_ID,
  content_code: "CNT-2026-000776",
  outcome,
  results: [
    {
      contribution_kind: "CONTENT_CREATION",
      outcome,
      source_key: `content:${CONTENT_ID}:CONTENT_CREATION`,
      work_item_id: ITEM,
      detail: null,
    },
  ],
});

const syncButton = () => screen.queryByRole("button", { name: /^Đồng bộ lại từ Nội dung/ });
const removeButton = () => screen.queryByRole("button", { name: /^Xóa kết quả/ });

describe("66-68. a content result offers the sync beside the removal, to the right people", () => {
  it("draws two separate controls for an owner", async () => {
    stubFetch(routes([stream()], { detailBody: detail({ results: [contentResult()] }) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await screen.findByText("Kết quả trong kỳ 2026-09");

    expect(syncButton()).toBeInTheDocument();
    expect(removeButton()).toBeInTheDocument();
    expect(syncButton()).not.toBe(removeButton());
  });

  it("offers neither to a team lead, and no sync on a manual result to anybody", async () => {
    stubFetch(
      routes([stream()], {
        capabilities: LEAD,
        detailBody: detail({ results: [contentResult()] }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await screen.findByText("Kết quả trong kỳ 2026-09");
    expect(syncButton()).toBeNull();
    expect(removeButton()).toBeNull();
  });

  it("does not offer the sync on a manual result even to an owner", async () => {
    stubFetch(routes([stream()], { detailBody: detail({ results: [result()] }) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await screen.findByText("Kết quả trong kỳ 2026-09");
    expect(syncButton()).toBeNull();
    expect(removeButton()).toBeInTheDocument();
  });
});

describe("69-71. the removal never projects, and the sync projects exactly once", () => {
  it("removal: one admin-remove POST, no projection route touched", async () => {
    const calls = stubFetch([
      {
        match: "/admin-remove",
        method: "POST",
        body: detail({ results: [contentResult({ status: "EXCLUDED", status_label: "Đã loại bỏ" })] }),
      },
      ...routes([stream()], { detailBody: detail({ results: [contentResult()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await userEvent.click(await screen.findByRole("button", { name: /^Xóa kết quả/ }));
    await confirm();
    await waitFor(() =>
      expect(sent(calls).some((call) => call.url.includes("/admin-remove"))).toBe(true),
    );
    expect(sent(calls).some((call) => call.url.includes("/project"))).toBe(false);
    expect(sent(calls).some((call) => call.url.includes("content-sync"))).toBe(false);
    expect(sent(calls).some((call) => call.url.includes("content-rebuild"))).toBe(false);
  });

  it("sync: one POST to the single-source projection route, then the detail is re-read", async () => {
    const calls = stubFetch([
      { match: PROJECT, method: "POST", body: projection("PROJECTED") },
      ...routes([stream()], { detailBody: detail({ results: [contentResult()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await screen.findByText("Kết quả trong kỳ 2026-09");
    const detailReadsBefore = sent(calls).filter(
      (call) => call.method === "GET" && call.url.endsWith(`/api/pr/work/${ITEM}`),
    ).length;

    await userEvent.click(screen.getByRole("button", { name: /^Đồng bộ lại từ Nội dung/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Đã đồng bộ công việc từ Nội dung.",
    );
    const projects = sent(calls).filter((call) => call.method === "POST" && call.url.includes(PROJECT));
    expect(projects).toHaveLength(1);
    // Nothing destructive rode along: no removal, no batch maintenance.
    expect(sent(calls).some((call) => call.url.includes("/admin-remove"))).toBe(false);
    expect(sent(calls).some((call) => call.url.includes("maintenance/content"))).toBe(false);
    await waitFor(() =>
      expect(
        sent(calls).filter(
          (call) => call.method === "GET" && call.url.endsWith(`/api/pr/work/${ITEM}`),
        ).length,
      ).toBeGreaterThan(detailReadsBefore),
    );
  });
});

describe("72. a business outcome reads as a sentence, not as a failure", () => {
  it.each([
    ["BLOCKED_BY_PERIOD", "Không thể đồng bộ vì kỳ đã đóng hoặc khóa."],
    ["NOT_QUALIFIED", "Nội dung hiện không đủ điều kiện ghi nhận công việc."],
    ["UNCHANGED", "Dữ liệu công việc đã đúng, không cần thay đổi."],
  ])("%s", async (outcome, sentence) => {
    stubFetch([
      { match: PROJECT, method: "POST", body: projection(outcome) },
      ...routes([stream()], { detailBody: detail({ results: [contentResult()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
    await userEvent.click(await screen.findByRole("button", { name: /^Đồng bộ lại từ Nội dung/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(sentence);
    expect(screen.queryByText(/Có lỗi xảy ra/)).toBeNull();
  });
});

// --- 0041: VALIDATOR DECISIONS -----------------------------------------------------

const LEAD_ID = "33333333-3333-3333-3333-333333333333";
const EXCLUDE = `/api/pr/work/results/${RESULT}/exclude`;
const RECONSIDER = `/api/pr/work/results/${RESULT}/reconsider`;

/** A pending manual result as the server draws it for a validator who is not the subject. */
const pendingRow = (over: Record<string, unknown> = {}) =>
  result({ status: "PENDING", status_label: "Chờ xác nhận", ...over });

/** The same row after *Từ chối / Không ghi nhận*: what the server says, verbatim. */
const rejectedRow = (over: Record<string, unknown> = {}) =>
  result({
    status: "EXCLUDED",
    status_label: "Đã từ chối",
    excluded_at: "2026-09-12T08:30:00Z",
    excluded_by_user_id: LEAD_ID,
    excluded_by_name: "Lê Trưởng Nhóm",
    excluded_reason: "Không đủ minh chứng",
    exclusion_kind: "VALIDATOR_REJECTED",
    exclusion_kind_label: "Đã từ chối",
    held_by_validator: true,
    can_validate: false,
    can_reject: false,
    can_reconsider: true,
    ...over,
  });

const open = async () => {
  await userEvent.click(await screen.findByText("Tìm khách hàng — 2026-09"));
  await screen.findByText("Kết quả trong kỳ 2026-09");
};

describe("47-48. a pending result offers Xác nhận and Từ chối to a validator, and to nobody else", () => {
  it("draws both per-row controls beside the pill", async () => {
    stubFetch(routes([stream()], { detailBody: detail({ results: [pendingRow()] }) }));
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.getByRole("button", { name: "Xác nhận kết quả +5 khách hàng" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Từ chối kết quả +5 khách hàng" })).toHaveTextContent(
      "Từ chối / Không ghi nhận",
    );
    expect(screen.queryByRole("button", { name: /Xem xét lại/ })).toBeNull();
  });

  it("confirms one row through the validate route with that row's id", async () => {
    const calls = stubFetch(routes([stream()], { detailBody: detail({ results: [pendingRow()] }) }));
    renderWithQuery(<WorkPage />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận kết quả +5 khách hàng" }));
    await waitFor(() => {
      const post = sent(calls).find(
        (call) => call.method === "POST" && call.url.includes(`/api/pr/work/${ITEM}/results/validate`),
      );
      expect(post).toBeDefined();
      expect(post!.body).toMatchObject({ result_ids: [RESULT] });
    });
  });

  it("draws neither for the subject - the server said so", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({
          is_subject: true,
          can_validate_results: false,
          results: [pendingRow({ can_validate: false, can_reject: false })],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.queryByRole("button", { name: /Xác nhận kết quả/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Từ chối kết quả/ })).toBeNull();
  });
});

describe("49. the rejection dialog requires a reason and posts it", () => {
  it("names the result, disables Từ chối until a reason is typed, then posts {reason}", async () => {
    const calls = stubFetch([
      { match: EXCLUDE, method: "POST", body: detail({ results: [rejectedRow()] }) },
      ...routes([stream()], { detailBody: detail({ results: [pendingRow()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Từ chối kết quả +5 khách hàng" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Từ chối kết quả?")).toBeInTheDocument();
    expect(within(dialog).getByText(/Kết quả: \+5 khách hàng/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    const accept = within(dialog).getByRole("button", { name: "Từ chối" });
    expect(accept).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText("Lý do từ chối"), "   ");
    expect(accept).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Lý do từ chối"), "Không đủ minh chứng");
    expect(accept).toBeEnabled();
    await confirm();

    await waitFor(() => {
      const post = sent(calls).find((call) => call.method === "POST" && call.url.includes(EXCLUDE));
      expect(post).toBeDefined();
      expect(post!.body).toEqual({ reason: "Không đủ minh chứng" });
    });
    // Nothing else rode along: no projection, no maintenance.
    expect(sent(calls).some((call) => call.url.includes("/project"))).toBe(false);
    expect(sent(calls).some((call) => call.url.includes("maintenance/"))).toBe(false);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Hủy posts nothing", async () => {
    const calls = stubFetch(routes([stream()], { detailBody: detail({ results: [pendingRow()] }) }));
    renderWithQuery(<WorkPage />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Từ chối kết quả +5 khách hàng" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Hủy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls).some((call) => call.method === "POST")).toBe(false);
  });

  it("a counted result is taken back through the same dialog, worded as Loại bỏ", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({
          results: [
            result({
              status: "COUNTED",
              status_label: "Đã ghi nhận",
              counted_at: "2026-09-08T02:00:00Z",
              can_validate: false,
            }),
          ],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.queryByRole("button", { name: /Xác nhận kết quả/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Loại bỏ kết quả +5 khách hàng" }));
    expect(within(await screen.findByRole("dialog")).getByText("Loại bỏ kết quả?")).toBeInTheDocument();
  });
});

describe("50-52. a rejected result reads as a decision and offers Xem xét lại", () => {
  it("shows Đã từ chối, the reason, who and when, and the reconsider control", async () => {
    stubFetch(routes([stream()], { detailBody: detail({ results: [rejectedRow()] }) }));
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.getByText("Đã từ chối")).toBeInTheDocument();
    const trail = screen.getByTestId(`result-exclusion-${RESULT}`);
    expect(trail).toHaveTextContent("Lý do: Không đủ minh chứng");
    expect(trail).toHaveTextContent("Người xử lý: Lê Trưởng Nhóm");
    expect(trail).toHaveTextContent("Thời gian:");
    expect(screen.getByRole("button", { name: "Xem xét lại kết quả +5 khách hàng" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Xác nhận kết quả/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Từ chối kết quả/ })).toBeNull();
    // Rejected is not "removable": the administrator's control is not drawn either.
    expect(removeButton()).toBeNull();
  });

  it("reconsider asks first, then posts to the reconsider route and draws the pending row", async () => {
    const calls = stubFetch([
      { match: RECONSIDER, method: "POST", body: detail({ results: [pendingRow()] }) },
      ...routes([stream()], { detailBody: detail({ results: [rejectedRow()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Xem xét lại kết quả +5 khách hàng" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Xem xét lại kết quả?")).toBeInTheDocument();
    expect(within(dialog).getByText(/vẫn được giữ trong lịch sử/)).toBeInTheDocument();
    await confirm();
    await waitFor(() =>
      expect(sent(calls).some((call) => call.method === "POST" && call.url.includes(RECONSIDER))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls).some((call) => call.url.includes("/project"))).toBe(false);
  });

  it("does not offer Xem xét lại to somebody the server did not allow", async () => {
    stubFetch(routes([stream()], { detailBody: detail({ results: [rejectedRow({ can_reconsider: false })] }) }));
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.getByText("Đã từ chối")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Xem xét lại/ })).toBeNull();
  });
});

describe("53-55. a sync never poses as the way back from a rejection", () => {
  it("a rejected content result offers no sync and no removal, and says why", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({
          results: [
            contentResult({
              status: "EXCLUDED",
              status_label: "Đã từ chối",
              counted_at: null,
              exclusion_kind: "VALIDATOR_REJECTED",
              exclusion_kind_label: "Đã từ chối",
              excluded_reason: "Số liệu không khớp",
              excluded_by_name: "Lê Trưởng Nhóm",
              excluded_at: "2026-09-12T08:30:00Z",
              held_by_validator: true,
              can_validate: false,
              can_reject: false,
              can_reconsider: true,
            }),
          ],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await open();
    expect(syncButton()).toBeNull();
    expect(removeButton()).toBeNull();
    expect(screen.getByText(/Đồng bộ từ Nội dung sẽ không ghi nhận lại kết quả này/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Xem xét lại/ })).toBeInTheDocument();
  });

  it("an administratively removed content result is restored through the sync", async () => {
    const calls = stubFetch([
      { match: PROJECT, method: "POST", body: projection("PROJECTED") },
      ...routes([stream()], {
        detailBody: detail({
          results: [
            contentResult({
              status: "EXCLUDED",
              status_label: "Đã xóa khỏi ghi nhận",
              counted_at: null,
              exclusion_kind: "ADMIN_REMOVED",
              exclusion_kind_label: "Đã xóa khỏi ghi nhận",
              excluded_reason: "Quản trị viên gỡ kết quả",
              held_by_validator: false,
              can_validate: false,
              can_reject: false,
              can_reconsider: false,
            }),
          ],
        }),
      }),
    ]);
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.getByText("Đã xóa khỏi ghi nhận")).toBeInTheDocument();
    expect(removeButton()).toBeNull();
    expect(screen.queryByRole("button", { name: /Xem xét lại/ })).toBeNull();
    await userEvent.click(syncButton()!);
    expect(await screen.findByRole("status")).toHaveTextContent("Đã đồng bộ công việc từ Nội dung.");
    expect(sent(calls).filter((call) => call.method === "POST" && call.url.includes(PROJECT))).toHaveLength(1);
  });

  it("HELD_BY_VALIDATOR reads as the projector's sentence, not as a failure", async () => {
    stubFetch([
      { match: PROJECT, method: "POST", body: projection("HELD_BY_VALIDATOR") },
      ...routes([stream()], { detailBody: detail({ results: [contentResult()] }) }),
    ]);
    renderWithQuery(<WorkPage />);
    await open();
    await userEvent.click(syncButton()!);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Kết quả này đã bị người xác nhận từ chối nên đồng bộ không ghi nhận lại.",
    );
    expect(screen.queryByText(/Có lỗi xảy ra/)).toBeNull();
  });
});

describe("56. a pending content row the source withdrew is not an ordinary pending row", () => {
  it("shows Không còn đủ điều kiện and neither Xác nhận nor Từ chối", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({
          results: [
            contentResult({
              status: "PENDING",
              status_label: "Chờ xác nhận",
              counted_at: null,
              counted_by_user_id: null,
              counted_by_name: null,
              source_eligible: false,
              can_validate: false,
              can_reject: false,
            }),
          ],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.getByText("Không còn đủ điều kiện")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Xác nhận kết quả/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Từ chối kết quả/ })).toBeNull();
  });

  it("an eligible pending content row keeps both controls", async () => {
    stubFetch(
      routes([stream()], {
        detailBody: detail({
          results: [
            contentResult({
              status: "PENDING",
              status_label: "Chờ xác nhận",
              counted_at: null,
              source_eligible: true,
            }),
          ],
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await open();
    expect(screen.queryByText("Không còn đủ điều kiện")).toBeNull();
    expect(screen.getByRole("button", { name: /Xác nhận kết quả/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Từ chối kết quả/ })).toBeInTheDocument();
  });
});
