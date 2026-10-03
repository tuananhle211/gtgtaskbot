/**
 * Work maintenance: the sync/rebuild panel, work type deletion, administrative
 * result removal. **PR_WORK_CONFIGURE only.**
 *
 * What these tests hold the screen to:
 *
 * * nothing runs when the panel opens - a preview is asked for, never assumed;
 * * every number shown is the server's preview, verbatim;
 * * the rebuild confirmation says in numbers what goes, what comes back and
 *   what stays, and the run is sent only after it is confirmed;
 * * a person without the capability sees none of the controls - the tab, the
 *   panel, the delete buttons, the removal on a result. The server refuses
 *   them anyway; not drawing them is a courtesy;
 * * a refused delete shows the server's impact counts, not a generic failure.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  channelsNavigation,
  confirm,
  dialog,
  renderWithQuery,
  SESSION,
  stubFetch,
} from "./helpers";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const OWNER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE", "PR_WORK_CONFIGURE"];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const PERIOD = "66666666-6666-6666-6666-666666666666";
const OLD = "11111111-1111-1111-1111-111111111111";
const NEW = "22222222-2222-2222-2222-222222222222";
const ITEM = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const RESULT = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const HAO = "33333333-3333-3333-3333-333333333333";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const workType = (id: string, code: string, name: string) => ({
  id,
  code,
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
});
const OLD_TYPE = workType(OLD, "OLD_SCRIPT", "Kịch bản video ngắn");
const NEW_TYPE = workType(NEW, "NEW_SCRIPT", "Kịch bản video ngắn/Bài đăng");

const period = {
  id: PERIOD,
  code: "2026-09",
  period_type: "MONTH",
  date_start: "2026-09-01",
  date_end: "2026-09-30",
  status: "OPEN",
  closed_at: null,
  locked_at: null,
};

const preview = (over: Record<string, unknown> = {}) => ({
  period_id: PERIOD,
  period_code: "2026-09",
  period_status: "OPEN",
  user_id: null,
  content_type: null,
  candidate_count: 128,
  truncated: false,
  eligible_content_count: 128,
  correct_result_count: 103,
  missing_result_count: 12,
  wrong_work_type_count: 8,
  stale_result_count: 5,
  new_work_type_count: 2,
  unmapped_count: 0,
  unresolved_count: 0,
  blocked_count: 0,
  results_to_remove: 27,
  results_to_create: 20,
  affected_user_ids: [HAO],
  affected_work_type_ids: [OLD, NEW],
  recreate_by_work_type: { [NEW]: 24 },
  provision_by_content_type: { ULTRA_SHORT_SCRIPT: 11 },
  manual_result_count: 8,
  manual_item_count: 4,
  recurring_result_count: 3,
  finalized_performance_count: 0,
  samples: [
    {
      content_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      content_code: "CNT-2026-000042",
      contribution_kind: "CONTENT_CREATION",
      finding: "WRONG_WORK_TYPE",
      contributor_user_id: HAO,
      current_work_type_id: OLD,
      expected_work_type_id: NEW,
      detail: null,
    },
  ],
  ...over,
});

const run = (operation: "sync" | "rebuild", over: Record<string, unknown> = {}) => ({
  operation,
  preview: preview(),
  content_items: 12,
  results_removed: operation === "rebuild" ? 27 : 0,
  counts: { PROJECTED: 12, UNCHANGED: 0, REVERSED: 0 },
  performance_refreshed: 1,
  ...over,
});

const container = {
  period_id: PERIOD,
  period_code: "2026-09",
  period_status: "OPEN",
  subject_user_id: HAO,
  subject_name: "Bùi Mỹ Hảo",
  unit: "ITEM",
  unit_label: "sản phẩm",
  actual_quantity: "1.00",
  declared_quantity: "1.00",
  pending_quantity: "0.00",
  excluded_quantity: "0.00",
  result_count: 1,
  pending_count: 0,
  target_quantity: null,
  has_target: false,
  completion_percent: null,
  over_target_quantity: "0.00",
  remaining_quantity: "0.00",
  is_target_met: false,
  progress_percent: "0",
  actual_label: "1 sản phẩm",
  standard_minutes: null,
  standard_minutes_per_unit: null,
  scoring_status: "NO_SCORING_RULE",
  scoring_status_label: "Chưa có quy tắc workload",
};

const result = (over: Record<string, unknown> = {}) => ({
  id: RESULT,
  work_item_id: ITEM,
  user_id: HAO,
  quantity: "1.00",
  label: "CNT-2026-000042 · Bí quyết ngủ ngon",
  link: null,
  note: null,
  source_type: "CONTENT",
  source_label: "Nội dung",
  source_key: "content:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa:CONTENT_CREATION",
  status: "COUNTED",
  status_label: "Đã ghi nhận",
  reported_by_user_id: HAO,
  reported_by_name: "Bùi Mỹ Hảo",
  reported_at: "2026-09-08T02:00:00Z",
  counted_at: "2026-09-08T02:00:00Z",
  counted_by_user_id: SESSION.user_id,
  counted_by_name: "Hà Trưởng Phòng",
  excluded_at: null,
  excluded_reason: null,
  can_withdraw: false,
  ...over,
});

const stream = {
  id: ITEM,
  code: "WRK-2026-000009",
  title: "Kịch bản video ngắn — 2026-09",
  description: null,
  work_type_id: OLD,
  work_type_code: OLD_TYPE.code,
  work_type_name: OLD_TYPE.name,
  work_type_category: "CONTENT",
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  is_period_container: true,
  reporting_period_id: PERIOD,
  subject_user_id: HAO,
  period_container: container,
  content_code: null,
  content_id: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  status: "IN_PROGRESS",
  status_label: "Đang ghi nhận kết quả",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: "1.00",
  unit: "ITEM",
  unit_label: "sản phẩm",
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
};

const detail = (results = [result()]) => ({
  item: stream,
  work_type: OLD_TYPE,
  evidence: [],
  content_code: null,
  can_manage: true,
  can_validate: true,
  can_execute: false,
  results,
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
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
};

type Route = { match: string; method?: string; status?: number; body?: unknown };

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (capabilities: string[], extra: Route[] = []): Route[] => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [{ user_id: HAO, full_name: "Bùi Mỹ Hảo", role: "EMPLOYEE" }] },
  { match: "/api/pr/work/maintenance/content-rebuild/preview", method: "POST", body: preview() },
  { match: "/api/pr/work/maintenance/content-sync/run", method: "POST", body: run("sync") },
  { match: "/api/pr/work/maintenance/content-rebuild/run", method: "POST", body: run("rebuild") },
  {
    match: `/api/pr/work/maintenance/results/${RESULT}/admin-remove`,
    method: "POST",
    body: detail([result({ status: "EXCLUDED", status_label: "Đã loại bỏ" })]),
  },
  { match: "/api/pr/work/types/bootstrap", method: "POST", body: { created: [], work_types: [] } },
  { match: "/api/pr/work/types/", method: "GET", body: { ...OLD_TYPE, is_in_use: true } },
  { match: "/api/pr/work/types", method: "GET", body: [OLD_TYPE, NEW_TYPE] },
  { match: "/api/pr/work/content/rules", body: [] },
  { match: "/api/pr/performance/scoring-rules", body: [] },
  { match: "/api/pr/performance/policies", body: [] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  { match: `/api/pr/work/${ITEM}`, method: "GET", body: detail() },
  { match: "/api/pr/work/periods", body: [period] },
  { match: "/api/pr/work", body: { items: [stream], total: 1, limit: 50, offset: 0 } },
];

const calls = (stub: ReturnType<typeof stubFetch>) =>
  (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;

const openConfig = async () => {
  await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
};

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- The panel ---------------------------------------------------------------

describe("1. the panel runs nothing when it opens, and shows the server's preview", () => {
  it("asks for the preview on Kiểm tra dữ liệu and renders every count verbatim", async () => {
    const stub = stubFetch(routes(OWNER));
    renderWithQuery(<WorkPage />);
    await openConfig();
    expect(await screen.findByText("Đồng bộ dữ liệu công việc")).toBeInTheDocument();
    // Nothing maintenance-related has been requested yet.
    expect(calls(stub).some((call) => call.url.includes("/maintenance/"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Đồng bộ thiếu" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Kiểm tra dữ liệu" }));
    const figures = within(await screen.findByLabelText("Kết quả kiểm tra"));
    for (const [label, value] of [
      ["Nội dung đủ điều kiện", "128"],
      ["Đã đúng", "103"],
      ["Thiếu WorkResult", "12"],
      ["Sai loại công việc", "8"],
      ["Kết quả cũ không còn phù hợp", "5"],
      ["WorkType cần tạo mới", "2"],
    ]) {
      const term = figures.getByText(label);
      expect(term.nextElementSibling?.textContent).toBe(value);
    }
    const sent = calls(stub).find((call) => call.url.includes("/content-rebuild/preview"));
    expect(sent?.body).toEqual({ period_id: PERIOD, user_id: null, content_type: null });
    expect(screen.getByRole("button", { name: "Đồng bộ thiếu" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xây dựng lại từ Nội dung" })).toBeInTheDocument();
  });

  it("sends the chosen person and content type as the scope", async () => {
    const stub = stubFetch(routes(OWNER));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await screen.findByText("Đồng bộ dữ liệu công việc");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Nhân sự đồng bộ" }), HAO);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Loại nội dung đồng bộ" }),
      "SHORT_VIDEO_SCRIPT",
    );
    await userEvent.click(screen.getByRole("button", { name: "Kiểm tra dữ liệu" }));
    await screen.findByLabelText("Kết quả kiểm tra");
    const sent = calls(stub).find((call) => call.url.includes("/content-rebuild/preview"));
    expect(sent?.body).toEqual({
      period_id: PERIOD,
      user_id: HAO,
      content_type: "SHORT_VIDEO_SCRIPT",
    });
  });
});

describe("2. Đồng bộ thiếu posts the run and reports what it did", () => {
  it("calls content-sync/run with the same scope", async () => {
    const stub = stubFetch(routes(OWNER));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByRole("button", { name: "Kiểm tra dữ liệu" }));
    await userEvent.click(await screen.findByRole("button", { name: "Đồng bộ thiếu" }));
    await waitFor(() =>
      expect(calls(stub).some((call) => call.url.includes("/content-sync/run"))).toBe(true),
    );
    expect(await screen.findByText(/Hoàn tất đồng bộ: 12 nội dung đã xử lý/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("3. rebuild asks first, in numbers, and runs only on confirm", () => {
  it("names what goes, what comes back and what stays, then posts the run with the note", async () => {
    const stub = stubFetch(routes(OWNER));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByRole("button", { name: "Kiểm tra dữ liệu" }));
    await userEvent.type(
      await screen.findByRole("textbox", { name: "Ghi chú xây dựng lại" }),
      "Làm sạch mapping thử nghiệm tháng 9",
    );
    await userEvent.click(screen.getByRole("button", { name: "Xây dựng lại từ Nội dung" }));

    const box = dialog();
    expect(box.getByText("Xây dựng lại công việc từ Nội dung")).toBeInTheDocument();
    expect(box.getByText(/Gỡ 27 kết quả công việc từ Nội dung/)).toBeInTheDocument();
    expect(box.getByText(/Tạo lại 24 Kịch bản video ngắn\/Bài đăng/)).toBeInTheDocument();
    expect(box.getByText(/Tạo lại 11 Kịch bản siêu ngắn \(loại mới\)/)).toBeInTheDocument();
    expect(
      box.getByText(/Giữ nguyên 8 kết quả tự báo cáo, 4 công việc thủ công, 3 kết\s*quả định kỳ/),
    ).toBeInTheDocument();
    expect(calls(stub).some((call) => call.url.includes("/content-rebuild/run"))).toBe(false);

    await confirm();
    await waitFor(() =>
      expect(calls(stub).some((call) => call.url.includes("/content-rebuild/run"))).toBe(true),
    );
    const sent = calls(stub).find((call) => call.url.includes("/content-rebuild/run"));
    expect(sent?.body).toEqual({
      period_id: PERIOD,
      user_id: null,
      content_type: null,
      note: "Làm sạch mapping thử nghiệm tháng 9",
    });
    expect(await screen.findByText(/27 kết quả đã gỡ/)).toBeInTheDocument();
  });

  it("offers neither act on a shut month, and says why", async () => {
    stubFetch(
      routes(OWNER, [
        {
          match: "/api/pr/work/maintenance/content-rebuild/preview",
          method: "POST",
          body: preview({ period_status: "CLOSED" }),
        },
        { match: "/api/pr/work/periods", body: [{ ...period, status: "CLOSED" }] },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByRole("button", { name: "Kiểm tra dữ liệu" }));
    expect(await screen.findByText(/đã đóng hoặc khóa\. Chỉ xem được/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Đồng bộ thiếu" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Xây dựng lại từ Nội dung" })).toBeNull();
  });

  it("surfaces the server's structured refusal in Vietnamese", async () => {
    stubFetch(
      routes(OWNER, [
        {
          match: "/api/pr/work/maintenance/content-rebuild/run",
          method: "POST",
          status: 409,
          body: {
            error: {
              code: "pr_conflict",
              message: "Không thể chỉnh sửa dữ liệu công việc của kỳ đã đóng hoặc khóa.",
              details: { reason: "work_period_not_open_for_cleanup", period: "2026-09" },
            },
          },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByRole("button", { name: "Kiểm tra dữ liệu" }));
    await userEvent.click(await screen.findByRole("button", { name: "Xây dựng lại từ Nội dung" }));
    await confirm();
    expect((await screen.findAllByText(/kỳ đã đóng hoặc khóa/)).length).toBeGreaterThanOrEqual(1);
  });
});

// --- Who sees the controls -------------------------------------------------------

describe("4. TEAM_LEAD and EMPLOYEE see no maintenance control anywhere", () => {
  it.each([
    ["a Trưởng nhóm", LEAD],
    ["an employee", EMPLOYEE],
  ])("draws nothing for %s", async (_, capabilities) => {
    stubFetch(routes(capabilities));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kịch bản video ngắn — 2026-09");
    expect(screen.queryByRole("button", { name: "Cấu hình" })).toBeNull();
    expect(screen.queryByText("Đồng bộ dữ liệu công việc")).toBeNull();
    expect(screen.queryByRole("button", { name: "Đồng bộ thiếu" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Xây dựng lại từ Nội dung" })).toBeNull();
    // Open the stream: its counted result offers no administrative removal.
    await userEvent.click(screen.getByText("Kịch bản video ngắn — 2026-09"));
    await screen.findByRole("region", { name: /Chi tiết/ });
    expect(screen.queryByRole("button", { name: /Xóa kết quả/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Xóa Kịch bản/ })).toBeNull();
  });
});

// --- Deleting a work type -----------------------------------------------------------

describe("5. a referenced work type is refused with the server's impact counts", () => {
  it("shows what still uses it and offers the empty-container sweep", async () => {
    const stub = stubFetch(
      routes(OWNER, [
        {
          match: `/api/pr/work/maintenance/work-types/${OLD}/references`,
          method: "GET",
          body: {
            work_type_id: OLD,
            content_rules_active: 1,
            content_rules_inactive: 0,
            work_items: 1,
            period_containers: 1,
            empty_containers_removable: 1,
            results: 0,
            contributions: 1,
            recurring_templates: 0,
            quotas: 1,
            quota_allocations: 0,
            scoring_rules: 0,
            score_allocations: 0,
            blocking: { content_rules_active: 1, work_items: 1, contributions: 1, quotas: 1 },
            deletable: false,
          },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    const row = (await screen.findAllByText("Kịch bản video ngắn"))
      .map((node) => node.closest("tr"))
      .find((node) => node !== null)!;
    await userEvent.click(within(row).getByRole("button", { name: "Xóa Kịch bản video ngắn" }));

    expect(await screen.findByText("Không thể xóa loại công việc này.")).toBeInTheDocument();
    expect(screen.getByText("1 ánh xạ nội dung")).toBeInTheDocument();
    expect(screen.getByText("1 chỉ tiêu KPI")).toBeInTheDocument();
    expect(screen.getByText("Thay đổi ánh xạ hoặc dọn dữ liệu trước.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dọn luồng trống" })).toBeInTheDocument();
    // No DELETE was sent for a type the server says is in use.
    expect(calls(stub).some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes an unreferenced type after its own confirmation", async () => {
    const stub = stubFetch(
      routes(OWNER, [
        {
          match: `/api/pr/work/maintenance/work-types/${NEW}/references`,
          method: "GET",
          body: {
            work_type_id: NEW,
            content_rules_active: 0,
            content_rules_inactive: 0,
            work_items: 0,
            period_containers: 0,
            empty_containers_removable: 0,
            results: 0,
            contributions: 0,
            recurring_templates: 0,
            quotas: 0,
            quota_allocations: 0,
            scoring_rules: 0,
            score_allocations: 0,
            blocking: {},
            deletable: true,
          },
        },
        {
          match: `/api/pr/work/maintenance/work-types/${NEW}`,
          method: "DELETE",
          body: { work_type_id: NEW, blocking: {}, deletable: true },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    const row = (await screen.findAllByText("Kịch bản video ngắn/Bài đăng"))
      .map((node) => node.closest("tr"))
      .find((node) => node !== null)!;
    await userEvent.click(
      within(row).getByRole("button", { name: "Xóa Kịch bản video ngắn/Bài đăng" }),
    );
    // The references came back clean, so the button now asks before deleting.
    await userEvent.click(
      await within(row).findByRole("button", { name: "Xóa Kịch bản video ngắn/Bài đăng" }),
    );
    expect(dialog().getByText("Xóa loại công việc này?")).toBeInTheDocument();
    await confirm();
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) => call.method === "DELETE" && call.url.endsWith(`/work-types/${NEW}`),
        ),
      ).toBe(true),
    );
  });
});

// --- Removing one result ---------------------------------------------------------

describe("6. an administrator removes one result, and is told it may come back", () => {
  it("asks, says the source may recreate it, then posts admin-remove", async () => {
    const stub = stubFetch(routes(OWNER));
    // Once the removal has been posted, the re-read must agree with it - a
    // mutation writes the returned detail *and* invalidates the query.
    const original = stub.getMockImplementation()!;
    let removed = false;
    stub.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url.includes("/admin-remove")) removed = true;
      if (removed && method === "GET" && url.includes(`/api/pr/work/${ITEM}`)) {
        return new Response(
          JSON.stringify(detail([result({ status: "EXCLUDED", status_label: "Đã loại bỏ" })])),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return original(input, init);
    });
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Kịch bản video ngắn — 2026-09"));
    const region = await screen.findByRole("region", { name: /Chi tiết/ });
    await userEvent.click(within(region).getByRole("button", { name: /Xóa kết quả 1 sản phẩm/ }));
    const box = dialog();
    expect(box.getByText("Xóa kết quả công việc?")).toBeInTheDocument();
    expect(box.getByText(/lần đồng bộ sau có thể ghi nhận lại/)).toBeInTheDocument();
    expect(calls(stub).some((call) => call.url.includes("/admin-remove"))).toBe(false);

    await confirm();
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) => call.method === "POST" && call.url.endsWith(`/results/${RESULT}/admin-remove`),
        ),
      ).toBe(true),
    );
    // The detail the server returned replaces the old one: the row is now excluded,
    // and the administrative control is gone from it.
    expect(await within(region).findByText("Đã loại bỏ")).toBeInTheDocument();
    expect(within(region).queryByRole("button", { name: /Xóa kết quả/ })).toBeNull();
  });
});
