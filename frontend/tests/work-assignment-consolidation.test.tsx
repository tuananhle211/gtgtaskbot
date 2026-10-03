/**
 * **Post-M4 UX consolidation - one assignment entry point.**
 *
 * The product change this file guards is a sentence: a manager assigning work
 * decides *"is this one job or a standing responsibility"*, not *"do I go to
 * Công việc or to Định kỳ"*. So *Định kỳ* stops being a top-level module and
 * becomes a **mode** inside *Giao công việc*, plus a management panel on the
 * same screen.
 *
 * Nothing behind it moved: the recurring templates, the occurrence ledger, the
 * generator, the M3 content projector and the M1 lifecycle are exactly as their
 * milestones left them. That is why several assertions here are about what did
 * **not** change - the source filter, the badges, the endpoints.
 *
 * The five that carry the patch
 * ------------------------------
 *
 * **Test 1.1** - *Định kỳ* is gone from the view tabs and the other four are
 * still there. That absence is the whole product decision.
 *
 * **Test 2.4** - the shared fields survive a switch between *Một lần* and
 * *Định kỳ*. If they did not, the "one form" would be two forms wearing one
 * heading, and a manager would retype a work type to change their mind.
 *
 * **Test 3.2 / 4.1** - the mode picks the **endpoint**. One-time posts to
 * `/work/assign` and creates no template; recurring posts to `/work/recurring`
 * and creates no work item. One merged endpoint owning two persistence
 * lifecycles is exactly what this patch does not build.
 *
 * **Test 5.1** - a routine is never offered as a way to record content work.
 * The content projector is the only writer of `CONTENT` work, and no body this
 * form can build reaches `source_type` at all.
 *
 * **Test 6.2** - the *Bắt đầu* / *Thực hiện* split. A manual job's operational
 * start is `accepted_at` - the instant the manager pressed the button - and a
 * recurring occurrence's is `scheduled_for`. Naming both "Thực hiện" would
 * claim manual work was performed the moment it was handed over.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, channelsNavigation, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const MANAGER = [
  "PR_WORK_EXECUTE",
  "PR_WORK_MANAGE",
  "PR_WORK_VALIDATE",
  "PR_WORK_CONFIGURE",
  "PR_PERFORMANCE_REVIEW",
];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const HAO = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";
const TEMPLATE = "77777777-7777-7777-7777-777777777777";
const PERIOD = "66666666-6666-6666-6666-666666666666";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

/** `ITEM_COUNT`: one job is one unit, and a quantity is optional. */
const ROUTINE_TYPE = {
  id: "11111111-1111-1111-1111-111111111111",
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

/** `QUANTITY`, in comments, and evidence-bearing. "100 comment mỗi ngày". */
const SEEDING_TYPE = {
  ...ROUTINE_TYPE,
  id: "44444444-4444-4444-4444-444444444444",
  code: "SEEDING_COMMENT",
  name: "Seeding bình luận",
  category: "COMMUNITY",
  category_label: "Cộng đồng",
  default_unit: "COMMENT",
  default_unit_label: "bình luận",
  default_quota_basis: "QUANTITY",
  default_quota_basis_label: "Theo số lượng",
  requires_evidence: true,
  display_order: 1,
};

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" },
  { user_id: LINH, full_name: "Linh", role: "EMPLOYEE" },
];

const contribution = (over: Record<string, unknown> = {}) => ({
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  work_item_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  user_id: HAO,
  user_name: "Hảo",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-04T02:00:00Z",
  count_status: "PENDING",
  count_status_label: "Chưa ghi nhận",
  counted_at: null,
  excluded_reason: null,
  ...over,
});

/** A manually assigned job: **no execution date**, and an `accepted_at`. */
const manualWork = (over: Record<string, unknown> = {}) => ({
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "WRK-2026-000001",
  title: "Kháng page David",
  description: null,
  work_type_id: ROUTINE_TYPE.id,
  work_type_code: ROUTINE_TYPE.code,
  work_type_name: ROUTINE_TYPE.name,
  work_type_category: ROUTINE_TYPE.category,
  source_type: "MANUAL",
  source_label: "Thủ công",
  is_source_derived: false,
  content_id: null,
  content_code: null,
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
  due_at: "2026-09-04T09:00:00Z",
  execution_at: null,
  is_overdue: false,
  created_by_user_id: LINH,
  assigned_by_user_id: LINH,
  assigned_at: "2026-09-04T03:25:00Z",
  accepted_at: "2026-09-04T03:25:00Z",
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  created_at: "2026-09-04T03:25:00Z",
  contributors: [contribution()],
  ...over,
});

/** A generated occurrence: `execution_at` is the firing, not the acceptance. */
const recurringWork = (over: Record<string, unknown> = {}) =>
  manualWork({
    id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    code: "WRK-2026-000002",
    title: "100 comment",
    work_type_id: SEEDING_TYPE.id,
    work_type_code: SEEDING_TYPE.code,
    work_type_name: SEEDING_TYPE.name,
    source_type: "RECURRING",
    source_label: "Định kỳ",
    status: "ACCEPTED",
    status_label: "Được giao",
    quantity: "100",
    unit: "COMMENT",
    unit_label: "bình luận",
    // Generated at 06:00 and scheduled for 01:00 UTC: the two are different
    // instants on purpose, so a test can tell which one the screen printed.
    accepted_at: "2026-09-04T06:00:00Z",
    execution_at: "2026-09-05T01:00:00Z",
    due_at: "2026-09-05T10:30:00Z",
    recurring_occurrence_id: "88888888-8888-8888-8888-888888888881",
    recurring_template_id: TEMPLATE,
    recurring_template_name: "Seeding 100 bình luận",
    contributors: [contribution({ work_item_id: "dddddddd-dddd-dddd-dddd-dddddddddddd" })],
    ...over,
  });

/** Projected by M3.1 from the content workflow. Nobody typed it. */
const contentWork = (over: Record<string, unknown> = {}) =>
  manualWork({
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    code: "WRK-2026-000003",
    title: "Kịch bản Dr Tiến",
    source_type: "CONTENT",
    source_label: "Nội dung",
    is_source_derived: true,
    content_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
    content_code: "CNT-2026-000042",
    status: "APPROVED",
    status_label: "Đã hoàn thành",
    due_at: null,
    execution_at: "2026-09-04T04:00:00Z",
    contributors: [contribution({ work_item_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee" })],
    ...over,
  });

const template = (over: Record<string, unknown> = {}) => ({
  id: TEMPLATE,
  name: "Seeding 100 bình luận",
  description: null,
  work_type_id: SEEDING_TYPE.id,
  work_type_name: SEEDING_TYPE.name,
  work_type_unit_label: "bình luận",
  assignment_mode: "SEPARATE_PER_ASSIGNEE",
  quantity: "100",
  priority: "NORMAL",
  priority_label: "Bình thường",
  frequency: "WEEKLY",
  frequency_label: "Hằng tuần",
  weekdays: [0, 1, 2, 3, 4],
  day_of_month: null,
  run_time: "08:00:00",
  due_after_hours: 9,
  start_date: "2026-09-05",
  end_date: null,
  status: "ACTIVE",
  status_label: "Đang chạy",
  contributor_user_ids: [HAO],
  contributor_names: { [HAO]: "Hảo" },
  schedule_label: "08:00 thứ Hai đến thứ Sáu",
  next_occurrences: ["2026-09-05T01:00:00Z", "2026-09-08T01:00:00Z"],
  generated_work_items: 6,
  unsettled_occurrences: 0,
  occurrence_count: 3,
  activated_at: "2026-09-01T02:00:00Z",
  activated_by_user_id: LINH,
  can_activate: false,
  can_pause: true,
  can_resume: false,
  can_end: true,
  can_edit: true,
  can_delete: false,
  ...over,
});

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 3,
  accepted: 3,
  completed: 1,
  approved: 1,
  counted_work_items: 1,
  counted_contributions: 1,
  open: 2,
  in_progress: 1,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
});

const readiness = (over: Record<string, unknown> = {}) => ({
  work_type_id: ROUTINE_TYPE.id,
  work_type_name: ROUTINE_TYPE.name,
  has_scoring_rule: true,
  period_id: PERIOD,
  period_label: "2026-09",
  assignees: [],
  assignees_without_quota: 0,
  ...over,
});

const preview = (over: Record<string, unknown> = {}) => ({
  schedule_label: "08:00 thứ Hai đến thứ Sáu",
  next_occurrences: ["2026-09-05T01:00:00Z", "2026-09-08T01:00:00Z"],
  ...over,
});

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  items: Array<Record<string, unknown>> = [],
  {
    capabilities = MANAGER,
    templates = [] as Array<Record<string, unknown>>,
    extra = [] as Route[],
  } = {},
): Route[] => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: [ROUTINE_TYPE, SEEDING_TYPE] },
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/api/pr/work/readiness", body: readiness() },
  { match: "/api/pr/work/recurring/preview", body: preview(), method: "POST" },
  ...extra,
  { match: "/api/pr/work/recurring", body: { items: templates } },
  {
    match: "/api/pr/work/periods",
    body: [
      {
        id: PERIOD,
        code: "2026-09",
        period_type: "MONTH",
        date_start: "2026-09-01",
        date_end: "2026-09-30",
        status: "OPEN",
        status_label: "Đang mở",
        closed_at: null,
        locked_at: null,
      },
    ],
  },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

const viewTabs = () => within(screen.getByRole("navigation", { name: "Chế độ xem" }));

/** Open *Giao công việc*. One entry point, whichever shape follows. */
async function openAssign(type = ROUTINE_TYPE) {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
  const form = (await screen.findByRole("heading", { name: "Giao công việc" })).closest(
    "form",
  ) as HTMLElement;
  await userEvent.selectOptions(within(form).getByLabelText("Loại công việc"), type.id);
  return form;
}

/** What the form posted, as an object. */
function posted(calls: ReturnType<typeof stubFetch>, path: string) {
  const call = calls.mock.calls.find(
    ([url, init]) =>
      String(url).endsWith(path) && (init as RequestInit | undefined)?.method === "POST",
  );
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : undefined;
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1: NAVIGATION ---------------------------------------------------------

describe("1. navigation", () => {
  it("offers four views, and Định kỳ is not one of them", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    // Waited on the capability-gated tab, so the absence below is "the
    // dashboard answered" rather than "it has not loaded yet".
    await screen.findByRole("button", { name: "Cấu hình" });

    for (const label of ["Công việc", "Kế hoạch KPI", "Hiệu suất", "Cấu hình"]) {
      expect(viewTabs().getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(viewTabs().queryByRole("button", { name: "Định kỳ" })).toBeNull();
  });

  it("keeps Định kỳ as a source filter over the one unified list", async () => {
    // The distinction the patch has to keep visible: *Định kỳ* is where work
    // came from, and no longer a module that owns a screen.
    stubFetch(routes([recurringWork()]));
    renderWithQuery(<WorkPage />);
    const sources = within(
      await screen.findByRole("navigation", { name: "Nguồn công việc" }),
    );
    for (const label of ["Tất cả", "Thủ công", "Định kỳ", "Nội dung"]) {
      expect(sources.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("lands an old ?view=recurring bookmark on the management panel", async () => {
    // Part V. The tab existed until this patch and people saved it. A bookmark that
    // silently shows a different screen is the failure; a blank page is worse.
    stubFetch(routes([], { templates: [template()] }));
    act(() => NAV.arriveAt("/pr/work?view=recurring"));
    renderWithQuery(<WorkPage />);

    expect(
      await screen.findByRole("region", { name: "Quản lý việc định kỳ" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(NAV.current()).toContain("panel=recurring"));
    expect(NAV.current()).not.toContain("view=recurring");
  });

  it("keeps the panel in the URL, so it is shareable", async () => {
    stubFetch(routes([], { templates: [template()] }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Quản lý việc định kỳ" }),
    );
    expect(NAV.current()).toContain("panel=recurring");
  });
});

// --- 2: THE UNIFIED FORM ---------------------------------------------------

describe("2. one assignment form, two shapes", () => {
  it("asks Hình thức first and defaults to Một lần", async () => {
    stubFetch(routes());
    const form = await openAssign();

    expect(within(form).getByText("Hình thức công việc")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Một lần/)).toBeChecked();
    expect(within(form).getByLabelText(/Định kỳ/)).not.toBeChecked();
  });

  it("hides every recurrence field while the shape is Một lần", async () => {
    stubFetch(routes());
    const form = await openAssign();

    expect(within(form).queryByText("Tần suất")).toBeNull();
    expect(within(form).queryByLabelText("Giờ tạo")).toBeNull();
    expect(within(form).queryByLabelText("Ngày bắt đầu")).toBeNull();
    expect(within(form).queryByLabelText("Ngày kết thúc (không bắt buộc)")).toBeNull();
    expect(within(form).queryByLabelText("Hạn hoàn thành (giờ)")).toBeNull();
    expect(within(form).queryByLabelText("T2")).toBeNull();
    expect(within(form).queryByLabelText("Lịch dự kiến")).toBeNull();
    // And a one-off assignment states a deadline instant, which a routine has
    // no equivalent of.
    expect(within(form).getByLabelText("Hạn")).toBeInTheDocument();
  });

  it("reveals the recurrence fields, and takes the deadline instant away", async () => {
    stubFetch(routes());
    const form = await openAssign();
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));

    expect(within(form).getByText("Tần suất")).toBeInTheDocument();
    expect(within(form).getByLabelText("Giờ tạo")).toBeInTheDocument();
    expect(within(form).getByLabelText("Ngày bắt đầu")).toBeInTheDocument();
    expect(within(form).getByLabelText("Ngày kết thúc (không bắt buộc)")).toBeInTheDocument();
    expect(within(form).getByLabelText("Hạn hoàn thành (giờ)")).toBeInTheDocument();
    // A routine with one due date would be a routine that is late forever.
    expect(within(form).queryByLabelText("Hạn")).toBeNull();
  });

  it("keeps the shared fields when the shape changes", async () => {
    // **The test that makes it one form.** If these were discarded, "one entry
    // point" would be two forms wearing one heading.
    stubFetch(routes());
    const form = await openAssign(SEEDING_TYPE);
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Seeding chiến dịch A");
    await userEvent.type(within(form).getByLabelText("Mô tả"), "Theo brief");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.type(within(form).getByLabelText("Số lượng (bình luận)"), "100");

    await userEvent.click(within(form).getByLabelText(/Định kỳ/));

    expect(within(form).getByLabelText("Loại công việc")).toHaveValue(SEEDING_TYPE.id);
    expect(within(form).getByLabelText("Tên công việc")).toHaveValue("Seeding chiến dịch A");
    expect(within(form).getByLabelText("Mô tả")).toHaveValue("Theo brief");
    expect(within(form).getByLabelText("Hảo")).toBeChecked();
    expect(within(form).getByLabelText("Số lượng (bình luận)")).toHaveValue(100);

    await userEvent.click(within(form).getByLabelText(/Một lần/));
    expect(within(form).getByLabelText("Tên công việc")).toHaveValue("Seeding chiến dịch A");
    expect(within(form).getByLabelText("Hảo")).toBeChecked();
  });

  it("shares the unit, the evidence helper and the quantity rule across shapes", async () => {
    stubFetch(routes());
    const form = await openAssign(SEEDING_TYPE);

    // The unit comes from the work type and is part of the field's name - never
    // typed, so one real job cannot be split into whichever shape counts best.
    const both = ["Một lần", "Định kỳ"];
    for (const shape of both) {
      await userEvent.click(within(form).getByLabelText(new RegExp(shape)));
      expect(within(form).getByLabelText("Số lượng (bình luận)")).toBeInTheDocument();
      expect(within(form).queryByLabelText("Đơn vị")).toBeNull();
      expect(
        within(form).getByText(/bắt buộc có minh chứng khi báo hoàn thành/i),
      ).toBeInTheDocument();
      expect(
        within(form).getByText(/được tính theo số lượng nên bắt buộc nhập/i),
      ).toBeInTheDocument();
    }
  });

  it("asks how to assign for a routine even with one person, and for one-off only with several", async () => {
    // Part S. On a routine the mode is stored and outlives the moment, so a
    // second person added next month must not inherit a default nobody chose.
    stubFetch(routes());
    const form = await openAssign();
    await userEvent.click(within(form).getByLabelText("Hảo"));
    expect(within(form).queryByText("Cách giao")).toBeNull();

    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    expect(within(form).getByText("Cách giao")).toBeInTheDocument();

    await userEvent.click(within(form).getByLabelText(/Một lần/));
    await userEvent.click(within(form).getByLabelText("Linh"));
    expect(within(form).getByText("Cách giao")).toBeInTheDocument();
  });
});

// --- 3: ONE-TIME -----------------------------------------------------------

describe("3. Một lần is manual work, unchanged", () => {
  it("asks for no execution date, only a deadline", async () => {
    // Part E. The operational start of manually assigned work is `accepted_at`
    // - the instant the manager confirms - and asking somebody to type a date
    // would invite one that disagrees with what the module recorded.
    stubFetch(routes());
    const form = await openAssign();
    expect(within(form).queryByLabelText(/Ngày thực hiện/)).toBeNull();
    expect(within(form).queryByLabelText(/Thực hiện/)).toBeNull();
    expect(within(form).getByLabelText("Hạn")).toBeInTheDocument();
  });

  it("posts the manual assignment and creates no routine", async () => {
    const calls = stubFetch(
      routes([], {
        extra: [
          {
            match: "/api/pr/work/batch",
            method: "POST",
            body: { assignment_mode: "SEPARATE_PER_ASSIGNEE", items: [manualWork()] },
          },
        ],
      }),
    );
    const form = await openAssign();
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Kháng page David");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.click(within(form).getByRole("button", { name: "Giao công việc" }));

    await waitFor(() => expect(posted(calls, "/api/pr/work/batch")).toBeDefined());
    const body = posted(calls, "/api/pr/work/batch");
    expect(body.contributor_user_ids).toEqual([HAO]);
    // The recurrence half never reaches the wire for a one-off.
    expect(body).not.toHaveProperty("frequency");
    expect(body).not.toHaveProperty("run_time");
    expect(body).not.toHaveProperty("start_date");
    // And no template was created.
    expect(posted(calls, "/api/pr/work/recurring")).toBeUndefined();
  });
});

// --- 4: RECURRING ----------------------------------------------------------

describe("4. Định kỳ is a routine, and creates no work item", () => {
  it("posts the template, and posts nothing to the assignment endpoint", async () => {
    // Part G. Submitting *Định kỳ* configures a routine. It must not file one
    // permanent work item standing in for the schedule - the occurrences the
    // generator produces later are the work.
    const calls = stubFetch(
      routes([], {
        extra: [{ match: "/api/pr/work/recurring", method: "POST", body: template() }],
      }),
    );
    const form = await openAssign(SEEDING_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "100 comment mỗi ngày");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.type(within(form).getByLabelText("Số lượng (bình luận)"), "100");
    await userEvent.click(within(form).getByRole("button", { name: "Tạo việc định kỳ" }));

    await waitFor(() => expect(posted(calls, "/api/pr/work/recurring")).toBeDefined());
    const body = posted(calls, "/api/pr/work/recurring");
    expect(body.name).toBe("100 comment mỗi ngày");
    expect(body.quantity).toBe("100");
    expect(body.frequency).toBe("DAILY");
    expect(body.assignment_mode).toBe("SEPARATE_PER_ASSIGNEE");
    expect(posted(calls, "/api/pr/work/batch")).toBeUndefined();
    // Scheduler internals are absent from the wire, not merely hidden.
    expect(body).not.toHaveProperty("status");
    expect(body).not.toHaveProperty("source_key");
    expect(body).not.toHaveProperty("cron");
  });

  it("sends the selected weekdays for a weekly routine", async () => {
    const calls = stubFetch(
      routes([], {
        extra: [{ match: "/api/pr/work/recurring", method: "POST", body: template() }],
      }),
    );
    const form = await openAssign(SEEDING_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Seeding T2-T6");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.type(within(form).getByLabelText("Số lượng (bình luận)"), "100");
    await userEvent.click(within(form).getByLabelText(/Hằng tuần/));
    for (const day of ["T2", "T3", "T4", "T5", "T6"]) {
      await userEvent.click(within(form).getByLabelText(day));
    }
    await userEvent.click(within(form).getByRole("button", { name: "Tạo việc định kỳ" }));

    await waitFor(() => expect(posted(calls, "/api/pr/work/recurring")).toBeDefined());
    const body = posted(calls, "/api/pr/work/recurring");
    expect(body.frequency).toBe("WEEKLY");
    expect(body.weekdays).toEqual([0, 1, 2, 3, 4]);
    expect(body.day_of_month).toBeNull();
  });

  it("sends a day of month for a monthly routine, and drops the weekdays", async () => {
    // A frequency owns its own parameters: a routine switched from weekly to
    // monthly must not carry weekdays the server would refuse.
    const calls = stubFetch(
      routes([], {
        extra: [{ match: "/api/pr/work/recurring", method: "POST", body: template() }],
      }),
    );
    const form = await openAssign(ROUTINE_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Báo cáo tháng");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.click(within(form).getByLabelText(/Hằng tuần/));
    await userEvent.click(within(form).getByLabelText("T2"));
    await userEvent.click(within(form).getByLabelText(/Hằng tháng/));
    await userEvent.type(within(form).getByLabelText("Ngày trong tháng"), "5");
    await userEvent.click(within(form).getByRole("button", { name: "Tạo việc định kỳ" }));

    await waitFor(() => expect(posted(calls, "/api/pr/work/recurring")).toBeDefined());
    const body = posted(calls, "/api/pr/work/recurring");
    expect(body.frequency).toBe("MONTHLY");
    expect(body.day_of_month).toBe(5);
    expect(body.weekdays).toEqual([]);
  });

  it("keeps SHARED_WORK when the manager picks it", async () => {
    const calls = stubFetch(
      routes([], {
        extra: [{ match: "/api/pr/work/recurring", method: "POST", body: template() }],
      }),
    );
    const form = await openAssign(ROUTINE_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Trực page");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.click(within(form).getByLabelText("Linh"));
    await userEvent.click(within(form).getByLabelText(/Một công việc chung/));
    await userEvent.click(within(form).getByRole("button", { name: "Tạo việc định kỳ" }));

    await waitFor(() => expect(posted(calls, "/api/pr/work/recurring")).toBeDefined());
    expect(posted(calls, "/api/pr/work/recurring").assignment_mode).toBe("SHARED_WORK");
  });

  it("takes the schedule preview from the server and computes no date itself", async () => {
    const calls = stubFetch(routes());
    const form = await openAssign(SEEDING_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.click(within(form).getByLabelText("Hảo"));

    const panel = await within(form).findByLabelText("Lịch dự kiến");
    expect(await within(panel).findByText("08:00 thứ Hai đến thứ Sáu")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        calls.mock.calls.some(([url]) =>
          String(url).includes("/api/pr/work/recurring/preview"),
        ),
      ).toBe(true),
    );
  });

  it("says the new routine is a draft that has not started", async () => {
    stubFetch(routes());
    const form = await openAssign(ROUTINE_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    expect(within(form).getByText(/chưa\s+sinh việc/i)).toBeInTheDocument();
  });
});

// --- 5: CONTENT ------------------------------------------------------------

describe("5. content stays automatic", () => {
  it("says content work is recorded by the system, in both shapes", async () => {
    // Part I and Part J. The rule is structural - no body this form can build
    // reaches `source_type` - so the sentence is guidance, not the guard.
    stubFetch(routes());
    const form = await openAssign();
    for (const shape of ["Một lần", "Định kỳ"]) {
      await userEvent.click(within(form).getByLabelText(new RegExp(shape)));
      expect(
        within(form).getByText(/quy trình Nội dung sẽ được hệ thống tự ghi nhận/i),
      ).toBeInTheDocument();
    }
  });

  it("offers no way to name a source, a content item or a content type", async () => {
    stubFetch(routes());
    const form = await openAssign();
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));

    expect(within(form).queryByLabelText(/Nguồn/)).toBeNull();
    expect(within(form).queryByLabelText(/Nội dung/)).toBeNull();
    expect(within(form).queryByLabelText(/Loại nội dung/)).toBeNull();
  });

  it("never queries the content endpoints while a routine is being configured", async () => {
    // Part AF. A recurring form that read the content calendar would be one
    // step from mirroring it, which is the duplication this patch forbids.
    const calls = stubFetch(routes());
    const form = await openAssign(SEEDING_TYPE);
    await userEvent.click(within(form).getByLabelText(/Định kỳ/));
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await within(form).findByLabelText("Lịch dự kiến");

    expect(
      calls.mock.calls.some(([url]) => /\/api\/pr\/content/.test(String(url))),
    ).toBe(false);
  });
});

// --- 6: THE UNIFIED LIST ---------------------------------------------------

describe("6. one list, three sources, no duplication", () => {
  it("shows all three sources together, each once, each badged", async () => {
    // Part AG. The same September, one list. A content deliverable enters once
    // as CONTENT; an explicitly configured routine enters independently as
    // RECURRING; neither is a copy of the other.
    stubFetch(routes([contentWork(), manualWork(), recurringWork()]));
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText("Kịch bản Dr Tiến")).toBeInTheDocument();
    expect(screen.getByText("Kháng page David")).toBeInTheDocument();
    expect(screen.getByText("100 comment")).toBeInTheDocument();
    expect(screen.getAllByText("Kịch bản Dr Tiến")).toHaveLength(1);
    expect(screen.getAllByText("100 comment")).toHaveLength(1);

    for (const label of ["Nội dung", "Thủ công", "Định kỳ"]) {
      expect(screen.getByText(label, { selector: "span" })).toBeInTheDocument();
    }
  });

  it("names each source's time for what it is", async () => {
    // Part Q. `accepted_at` is when a manual job entered somebody's workload;
    // `execution_at` is when recurring and content work is performed. Calling
    // both "Thực hiện" would claim manual work was done when it was handed out.
    stubFetch(routes([manualWork(), recurringWork()]));
    renderWithQuery(<WorkPage />);

    const manual = (await screen.findByText("Kháng page David")).closest(
      "button",
    ) as HTMLElement;
    expect(within(manual).getByText(/^Bắt đầu /)).toBeInTheDocument();
    expect(within(manual).queryByText(/^Thực hiện /)).toBeNull();
    expect(within(manual).getByText(/^Hạn /)).toBeInTheDocument();

    const routine = screen.getByText("100 comment").closest("button") as HTMLElement;
    expect(within(routine).getByText(/^Thực hiện /)).toBeInTheDocument();
    expect(within(routine).queryByText(/^Bắt đầu /)).toBeNull();
    // The deadline stays its own fact, beside the execution date and never
    // instead of it.
    expect(within(routine).getByText(/^Hạn /)).toBeInTheDocument();
  });
});

// --- 7: MANAGEMENT AND PERMISSIONS ----------------------------------------

describe("7. managing the routines that exist", () => {
  it("lists them with their schedule, quantity, next firing, deadline rule and state", async () => {
    stubFetch(routes([], { templates: [template()] }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Quản lý việc định kỳ" }),
    );

    const card = (await screen.findByText("Seeding 100 bình luận")).closest(
      "article",
    ) as HTMLElement;
    expect(within(card).getByText(/08:00 thứ Hai đến thứ Sáu/)).toBeInTheDocument();
    expect(within(card).getByText(/· 100 bình luận ·/)).toBeInTheDocument();
    expect(within(card).getByText(/Hạn sau 9 giờ/)).toBeInTheDocument();
    expect(within(card).getByText(/Lần tới/)).toBeInTheDocument();
    expect(within(card).getByText("Đang chạy")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Tạm dừng" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Sửa" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Kết thúc/ })).toBeInTheDocument();
  });

  it("says an edit reaches future firings only", async () => {
    // Part M. Work already generated keeps the wording it was handed out with:
    // it is somebody's assignment, not a view of a template.
    stubFetch(routes([], { templates: [template()] }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Quản lý việc định kỳ" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Sửa" }));

    expect(await screen.findByRole("heading", { name: "Sửa việc định kỳ" })).toBeInTheDocument();
    expect(
      screen.getByText(/công việc đã sinh ra giữ nguyên nội dung lúc được giao/i),
    ).toBeInTheDocument();
  });

  it("shows an employee their generated work and none of the controls", async () => {
    // Part X. They do not need to know a template exists, and the server
    // refuses them either way.
    stubFetch(routes([recurringWork()], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText("100 comment")).toBeInTheDocument();
    expect(screen.getByText("Định kỳ", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quản lý việc định kỳ" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Giao công việc" })).toBeNull();
  });

  it("offers no recurring shape on a proposal", async () => {
    // Part W. There is no standing authorization to propose: *Định kỳ* is an
    // assignment-only shape, and the capability gates it besides.
    stubFetch(routes([], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Đề xuất công việc" }));
    const form = (await screen.findByRole("heading", { name: "Đề xuất công việc" })).closest(
      "form",
    ) as HTMLElement;

    expect(within(form).queryByText("Hình thức công việc")).toBeNull();
    expect(within(form).queryByText("Tần suất")).toBeNull();
  });

  it("hides the panel from somebody whose bookmark outlived their capability", async () => {
    stubFetch(routes([], { capabilities: EMPLOYEE }));
    act(() => NAV.arriveAt("/pr/work?panel=recurring"));
    renderWithQuery(<WorkPage />);

    await screen.findByRole("button", { name: "Đề xuất công việc" });
    expect(screen.queryByRole("region", { name: "Quản lý việc định kỳ" })).toBeNull();
  });
});
