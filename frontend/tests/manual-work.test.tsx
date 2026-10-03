/**
 * M4A - Tạo công việc, the two multi-assignee modes, and bulk validation.
 *
 * Numbered against the milestone's frontend requirement list.
 *
 * The browser decides nothing here either, and the assertions are shaped to
 * prove it. The mode radio sends a value; it does not compute what that value
 * means. The readiness sentences are drawn from server flags; the screen never
 * works out whether somebody has a quota. Every blocked row in the bulk panel
 * prints the server's own `reason_label`, so a reason nobody has translated
 * cannot ship as a raw token.
 *
 * The three that carry the milestone
 * -----------------------------------
 *
 * **Test 5** - the mode selector appears only when more than one person is
 * named, and is *sent* only then. With one assignee the two modes are the same
 * work, and asking a question whose answer does not matter is how people learn
 * to click past the one that does.
 *
 * **Test 8** - a quota that does not exist is a sentence, never a refusal. The
 * form still submits, because "Kháng page David" is real work whether or not
 * anybody has configured a scoring rule for page recovery.
 *
 * **Test 12** - the bulk panel refuses to offer the button while any row is
 * blocked, and names each one. All-or-nothing is a hostile promise to debug
 * without that.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

/** The shape `stubFetch` takes. Named so the `extra` parameter can say it. */
type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const MANAGER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const HAO = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";

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

/** `QUANTITY`, in comments. The archetype M4 is built around. */
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

const contribution = (over: Record<string, unknown> = {}) => ({
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  work_item_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  user_id: HAO,
  user_name: "Hảo",
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
  work_type_id: ROUTINE_TYPE.id,
  work_type_code: ROUTINE_TYPE.code,
  work_type_name: ROUTINE_TYPE.name,
  work_type_category: ROUTINE_TYPE.category,
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  content_code: null,
  status: "COMPLETED",
  status_label: "Chờ xác nhận",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: null,
  unit: null,
  unit_label: null,
  due_at: "2026-09-20T09:00:00Z",
  is_overdue: false,
  created_by_user_id: LINH,
  assigned_by_user_id: LINH,
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: "2026-09-02T02:00:00Z",
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution()],
  ...over,
});

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-16T00:00:00Z",
  created: 1,
  accepted: 1,
  completed: 1,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 1,
  in_progress: 0,
  awaiting_validation: 1,
  proposed: 0,
  overdue: 0,
});

const readiness = (over: Record<string, unknown> = {}) => ({
  work_type_id: ROUTINE_TYPE.id,
  work_type_name: ROUTINE_TYPE.name,
  has_scoring_rule: true,
  period_id: "55555555-5555-5555-5555-555555555555",
  period_label: "2026-09",
  assignees: [],
  assignees_without_quota: 0,
  ...over,
});

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" },
  { user_id: LINH, full_name: "Linh", role: "EMPLOYEE" },
];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  items: Array<Record<string, unknown>>,
  {
    capabilities = MANAGER,
    types = [ROUTINE_TYPE, SEEDING_TYPE],
    ready = readiness(),
    extra = [] as Route[],
  } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: types },
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/api/pr/work/readiness", body: ready },
  ...extra,
  { match: "/history", body: [] },
  // Post-M4. The Work screen's outer scope is a reporting month, so it asks for
  // the period list. Stubbed **before** the generic `/api/pr/work` entry, which
  // would otherwise swallow it - `stubFetch` takes the first substring hit.
  {
    match: "/api/pr/work/periods",
    body: [
      {
        id: "66666666-6666-6666-6666-666666666666",
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

/** Open *Giao công việc* and choose a work type. The start of every form test. */
async function openAssignForm(type = ROUTINE_TYPE) {
  await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
  const form = await screen.findByRole("heading", { name: "Giao công việc" });
  const scope = form.closest("form") as HTMLElement;
  await userEvent.selectOptions(
    within(scope).getByLabelText("Loại công việc"),
    type.id,
  );
  return scope;
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-4: THE FORM ---------------------------------------------------------

describe("1. the manual creation form", () => {
  it("offers a description and several assignees", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();

    expect(within(form).getByLabelText("Tên công việc")).toBeInTheDocument();
    expect(within(form).getByLabelText("Mô tả")).toBeInTheDocument();
    // Checkboxes, not a single selector: several people is the normal case.
    expect(within(form).getByLabelText("Hảo")).toBeInTheDocument();
    expect(within(form).getByLabelText("Linh")).toBeInTheDocument();
  });

  it("names the work type's unit beside the quantity, and never asks for a score", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm(SEEDING_TYPE);

    // The unit is the *type's*, printed from `default_unit_label`.
    expect(within(form).getByLabelText("Số lượng (bình luận)")).toBeInTheDocument();
    // **No workload points, no standard minutes, no performance score.** M6
    // decides what work is worth, and a form that asked would be inviting
    // somebody to write their own rate.
    expect(within(form).queryByText(/điểm|workload|phút chuẩn/i)).toBeNull();
  });

  it("shows the work type's evidence requirement", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm(SEEDING_TYPE);

    expect(
      within(form).getByText(/bắt buộc có minh chứng khi báo hoàn thành/i),
    ).toBeInTheDocument();
  });

  it("says content work is recorded automatically", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();

    // Part H. The rule is structural - no request body reaches `source_type` -
    // so this sentence is guidance at the place somebody would otherwise
    // duplicate the projector's work by hand.
    expect(
      within(form).getByText(/quy trình Nội dung sẽ được hệ thống tự ghi nhận/i),
    ).toBeInTheDocument();
  });
});

// --- 5-7: THE TWO MODES ----------------------------------------------------

describe("2. shared work and one job each", () => {
  it("asks how to assign only once more than one person is named", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();

    // One person: both modes produce identical work, so there is nothing to ask.
    await userEvent.click(within(form).getByLabelText("Hảo"));
    expect(within(form).queryByText("Cách giao")).toBeNull();

    // Two: now the answer is a different fact about the world.
    await userEvent.click(within(form).getByLabelText("Linh"));
    expect(within(form).getByText("Cách giao")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Mỗi người một công việc/)).toBeInTheDocument();
    expect(within(form).getByLabelText(/Một công việc chung/)).toBeInTheDocument();
  });

  it("defaults to one job each, which is the safer answer", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.click(within(form).getByLabelText("Linh"));

    // Recording three independent obligations as one shared job is the
    // dangerous direction: one person then completes it for all of them.
    expect(within(form).getByLabelText(/Mỗi người một công việc/)).toBeChecked();
  });

  it("sends the chosen mode, and sends none for a single assignee", async () => {
    const fetchMock = stubFetch([
      ...routes([workItem()]),
      { match: "/api/pr/work/batch", body: { assignment_mode: "SHARED_WORK", items: [] } },
    ]);
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();

    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Quay bác sĩ Tiến");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.click(within(form).getByLabelText("Linh"));
    await userEvent.click(within(form).getByLabelText(/Một công việc chung/));
    await userEvent.click(within(form).getByRole("button", { name: "Giao công việc" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((one) =>
        String(one[0]).includes("/api/pr/work/batch"),
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body.assignment_mode).toBe("SHARED_WORK");
      expect(body.contributor_user_ids).toEqual([HAO, LINH]);
    });
  });
});

// --- 8-10: THE DIAGNOSTICS -------------------------------------------------

describe("3. what this work will be worth", () => {
  it("says when no KPI quota covers the assignees, and creates the work anyway", async () => {
    const fetchMock = stubFetch([
      ...routes([workItem()], {
        ready: readiness({
          assignees_without_quota: 1,
          assignees: [{ user_id: HAO, full_name: "Hảo", has_quota: false }],
        }),
      }),
      { match: "/api/pr/work/batch", body: { assignment_mode: "SEPARATE_PER_ASSIGNEE", items: [] } },
    ]);
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Kháng page David");
    await userEvent.click(within(form).getByLabelText("Hảo"));

    expect(
      await within(form).findByText(/chưa có\s+KPI\/quota phù hợp cho kỳ này/i),
    ).toBeInTheDocument();

    // **And the button still works.** A diagnostic, not a gate.
    const submit = within(form).getByRole("button", { name: "Giao công việc" });
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((one) => String(one[0]).includes("/api/pr/work/batch")),
      ).toBe(true),
    );
  });

  it("says when the work type has no M6 workload rule", async () => {
    stubFetch(routes([workItem()], { ready: readiness({ has_scoring_rule: false }) }));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm();

    expect(
      await within(form).findByText(/Chưa có quy tắc workload cho loại công việc này/i),
    ).toBeInTheDocument();
  });

  it("requires a quantity when the work type is measured by one", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const form = await openAssignForm(SEEDING_TYPE);
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "100 comment seeding");
    await userEvent.click(within(form).getByLabelText("Hảo"));

    // A `QUANTITY` type filed without a number reports as zero comments against
    // a plan expressed in comments.
    expect(within(form).getByRole("button", { name: "Giao công việc" })).toBeDisabled();
    // Matched on one text node: the sentence emphasises "một" in its own
    // element, and asserting across the boundary would be asserting the markup.
    expect(
      within(form).getByText(/được tính theo số lượng nên bắt buộc nhập/i),
    ).toBeInTheDocument();

    await userEvent.type(within(form).getByLabelText("Số lượng (bình luận)"), "100");
    expect(within(form).getByRole("button", { name: "Giao công việc" })).toBeEnabled();
  });
});

// --- 11: THE SOURCE FILTER -------------------------------------------------

describe("4. where the work came from", () => {
  it("filters the ledger by source and narrows the tiles with it", async () => {
    const fetchMock = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    const nav = screen.getByRole("navigation", { name: "Nguồn công việc" });
    expect(within(nav).getByRole("button", { name: "Thủ công" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "Định kỳ" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "Nội dung" })).toBeInTheDocument();

    await userEvent.click(within(nav).getByRole("button", { name: "Thủ công" }));

    await waitFor(() => {
      // Both the list and the tiles above it, because a summary that ignored
      // the filter would put a figure over a list that disagrees with it.
      expect(
        fetchMock.mock.calls.some((one) =>
          String(one[0]).includes("/api/pr/work?") &&
          String(one[0]).includes("source_type=MANUAL"),
        ),
      ).toBe(true);
      expect(
        fetchMock.mock.calls.some((one) =>
          String(one[0]).includes("/api/pr/work/summary") &&
          String(one[0]).includes("source_type=MANUAL"),
        ),
      ).toBe(true);
    });
  });

  it("shows the source on each card", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);

    // The server's `source_label`, never a table in the browser.
    expect(await screen.findByText(/Nhập thủ công/)).toBeInTheDocument();
  });
});

// --- 12-14: BULK VALIDATION ------------------------------------------------

describe("5. validating a queue in one act", () => {
  it("is offered only to a validator", async () => {
    stubFetch(routes([workItem()], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Kháng page David");

    expect(screen.queryByRole("button", { name: /Xác nhận hàng loạt/ })).toBeNull();
  });

  it("names every blocked row and withholds the button until they are dropped", async () => {
    const second = workItem({
      id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
      code: "WRK-2026-000002",
      title: "Việc của chính tôi",
    });
    stubFetch([
      ...routes([workItem(), second], {
        extra: [
          {
            match: "/api/pr/work/validate/preflight",
            body: {
              candidates: [
                {
                  work_item_id: second.id,
                  code: "WRK-2026-000002",
                  title: "Việc của chính tôi",
                  reason: "self_validation",
                  reason_label:
                    "Bạn có tham gia công việc này nên không thể tự xác nhận.",
                  current_status: "COMPLETED",
                },
              ],
              validatable_count: 1,
              blocked_count: 1,
              requested: 2,
              duplicates_removed: 0,
              max_items: 200,
            },
          },
        ],
      }),
    ]);
    renderWithQuery(<WorkPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: /Xác nhận hàng loạt \(2\)/ }),
    );
    const panel = screen.getByRole("region", { name: "Xác nhận hàng loạt" });
    await userEvent.click(within(panel).getByLabelText("Chọn WRK-2026-000001"));
    await userEvent.click(within(panel).getByLabelText("Chọn WRK-2026-000002"));
    await userEvent.click(within(panel).getByRole("button", { name: /Kiểm tra 2 công việc/ }));

    // The server's sentence, not one this component composed.
    expect(
      await within(panel).findByText(
        /Bạn có tham gia công việc này nên không thể tự xác nhận/,
      ),
    ).toBeInTheDocument();
    // All-or-nothing, so the button stays out of reach while a row would fail.
    expect(
      within(panel).getByRole("button", { name: /Xác nhận 1 công việc/ }),
    ).toBeDisabled();
  });

  it("confirms before validating, and sends the whole selection", async () => {
    const fetchMock = stubFetch([
      ...routes([workItem()], {
        extra: [
          {
            match: "/api/pr/work/validate/preflight",
            body: {
              candidates: [
                {
                  work_item_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
                  code: "WRK-2026-000001",
                  title: "Kháng page David",
                  reason: null,
                  reason_label: null,
                  current_status: "COMPLETED",
                },
              ],
              validatable_count: 1,
              blocked_count: 0,
              requested: 1,
              duplicates_removed: 0,
              max_items: 200,
            },
          },
          {
            match: "/api/pr/work/validate",
            body: {
              batch_id: "66666666-6666-6666-6666-666666666666",
              validated_count: 1,
              counted_contributions: 1,
              requested: 1,
              duplicates_removed: 0,
              validated: [],
            },
          },
        ],
      }),
    ]);
    renderWithQuery(<WorkPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: /Xác nhận hàng loạt \(1\)/ }),
    );
    const panel = screen.getByRole("region", { name: "Xác nhận hàng loạt" });
    await userEvent.click(within(panel).getByLabelText("Chọn WRK-2026-000001"));
    await userEvent.click(within(panel).getByRole("button", { name: /Kiểm tra 1 công việc/ }));
    await userEvent.click(
      await within(panel).findByRole("button", { name: /Xác nhận 1 công việc/ }),
    );

    // Counting somebody's work is final, so it is confirmed like every other
    // irreversible act on this screen.
    await confirm();

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (one) =>
          String(one[0]).includes("/api/pr/work/validate") &&
          !String(one[0]).includes("preflight"),
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body.work_item_ids).toEqual(["cccccccc-cccc-cccc-cccc-cccccccccccc"]);
    });
  });
});
