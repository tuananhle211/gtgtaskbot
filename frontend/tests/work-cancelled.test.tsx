/**
 * Cancelled work: hidden from the normal dashboard, deletable by an administrator when safe.
 *
 * The server leaves `CANCELLED` rows out of the default query, so the screen's
 * job is to make the explicit view reachable, to keep a cancelled card whole
 * when it is shown, to offer the delete only inside the expanded detail and
 * only when the server said so, and to say why when the server said no. These
 * tests hold the screen to that, numbered against the task:
 *
 * 32-33. the default list sends no status and draws what the server returned;
 *        "Đã hủy" is an option on the existing status filter and sends
 *        `status=CANCELLED`, and the empty state under it says so;
 * 34.    a cancelled card keeps the responsible person on their own row, the
 *        status, the source, the type and the quantity;
 * 35.    the delete is inside the expanded detail, never on a collapsed card,
 *        and never for a row the server did not mark - TEAM_LEAD and EMPLOYEE
 *        get neither the button nor the reasoning;
 * 36.    the confirmation names the row, the person and the status, and says
 *        the four things the delete will and will not do;
 * 37-40. a confirmed delete sends exactly one DELETE to the cancelled route,
 *        removes the card, clears `?item=`, reloads nothing, says "Đã xóa công
 *        việc.", and calls no sync, rebuild, reconcile or projection;
 * 39.    a blocked row shows the server's explanation and the counts, and a
 *        refusal at delete time is shown in the server's words;
 * deep link. `?item=<cancelled-id>` on the default view still opens the row,
 *        marked as outside the filter, with the way to the cancelled view.
 *
 * Nothing here contacts a network.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  cancelDialog,
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

const OWNER = [
  "PR_WORK_EXECUTE",
  "PR_WORK_MANAGE",
  "PR_WORK_VALIDATE",
  "PR_WORK_VIEW_ALL",
  "PR_WORK_CONFIGURE",
];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const PERIOD = "66666666-6666-6666-6666-666666666666";
const TYPE = "11111111-1111-1111-1111-111111111111";
const ANH = "22222222-2222-2222-2222-222222222222";
const GONE = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const ACTIVE = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const MAINT = "/api/pr/work/maintenance";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  my_capabilities: capabilities,
  recent_content: [],
  overdue_tasks: [],
});

const WORK_TYPE = {
  id: TYPE,
  code: "CUSTOMER_CHAT",
  name: "Bài Group/Chat khách/Order CTV",
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

const contribution = (itemId: string, countStatus: string) => ({
  id: `${itemId.slice(0, 8)}-0000-0000-0000-000000000001`,
  work_item_id: itemId,
  user_id: ANH,
  user_name: "Nguyen Nguyen",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-10T03:25:00Z",
  count_status: countStatus,
  count_status_label: countStatus === "EXCLUDED" ? "Không tính" : "Chờ xác nhận",
  counted_at: null,
  excluded_reason: countStatus === "EXCLUDED" ? "Công việc đã hủy" : null,
});

const item = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  id,
  code: id === GONE ? "WRK-2026-000003" : "WRK-2026-000004",
  title,
  description: null,
  work_type_id: TYPE,
  work_type_code: WORK_TYPE.code,
  work_type_name: WORK_TYPE.name,
  work_type_category: "CONTENT",
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  is_period_container: false,
  is_legacy_content_work: false,
  period_container: null,
  reporting_period_id: null,
  subject_user_id: null,
  status: "ACCEPTED",
  status_label: "Được giao",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: "1.00",
  unit: "ITEM",
  unit_label: "sản phẩm",
  due_at: null,
  execution_at: "2026-09-10T03:25:00Z",
  is_overdue: false,
  created_by_user_id: SESSION.user_id,
  assigned_by_user_id: SESSION.user_id,
  assigned_at: "2026-09-10T03:25:00Z",
  accepted_at: "2026-09-10T03:25:00Z",
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  content_id: null,
  content_code: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  created_at: "2026-09-10T03:25:00Z",
  contributors: [contribution(id, "PENDING")],
  ...over,
});

const gone = () =>
  item(GONE, "Chat khách", {
    status: "CANCELLED",
    status_label: "Đã hủy",
    cancelled_at: "2026-09-11T02:00:00Z",
    cancel_reason: "Khách hủy lịch",
    contributors: [contribution(GONE, "EXCLUDED")],
  });

const active = () => item(ACTIVE, "Quay TVC Apexmed");

const CLEAN = {
  rule: "terminal",
  deletable: true,
  reason: null,
  cause: null,
  blocking: { results: 0, counted_contributions: 0, quota_allocations: 0, score_allocations: 0 },
  period_code: "2026-09",
  message: null,
  previous_status: "CANCELLED",
};

const BLOCKED = {
  rule: "terminal",
  deletable: false,
  reason: "terminal_work_item_delete_blocked",
  cause: "blocking_references",
  blocking: { results: 1, counted_contributions: 1, quota_allocations: 1, score_allocations: 0 },
  period_code: "2026-09",
  message:
    "Không thể xóa công việc này vì vẫn còn dữ liệu kết quả hoặc ghi nhận hiệu suất liên quan.",
  previous_status: "CANCELLED",
};

const detail = (
  row: Record<string, unknown>,
  eligibility: typeof CLEAN | typeof BLOCKED | null,
) => ({
  item: row,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: null,
  can_manage: true,
  can_validate: false,
  can_execute: true,
  results: [],
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
  can_delete_legacy: false,
  can_admin_delete: eligibility?.deletable ?? false,
  admin_delete: eligibility,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 1,
  accepted: 1,
  completed: 0,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 1,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
};

const deletion = () => ({
  work_item_id: GONE,
  code: "WRK-2026-000003",
  title: "Chat khách",
  work_type_id: TYPE,
  source_type: "MANUAL",
  source_key: null,
  content_id: null,
  content_code: null,
  recurring_occurrence_id: null,
  responsible_user_id: ANH,
  period_code: "2026-09",
  previous_status: "CANCELLED",
  removed: { contributions: 1, evidence: 0, history: 3 },
  results_created: 0,
  projection_requested: false,
});

/**
 * The stub as a server. **The list answers like the real one**: without
 * `status=CANCELLED` it returns only the active row, with it only the
 * cancelled row - the browser is never handed the cancelled row to hide.
 * Order matters: `stubFetch` takes the first substring hit, so the specific
 * routes precede `/api/pr/work`.
 */
const routes = ({
  capabilities,
  eligibility,
  deleteStatus = 200,
  deleteBody = deletion(),
}: {
  capabilities: string[];
  eligibility: typeof CLEAN | typeof BLOCKED | null;
  deleteStatus?: number;
  deleteBody?: unknown;
}) => [
  {
    match: `${MAINT}/terminal-items/${GONE}`,
    method: "DELETE",
    status: deleteStatus,
    body: deleteBody,
  },
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  { match: `/api/pr/work/${GONE}`, method: "GET", body: detail(gone(), eligibility) },
  { match: `/api/pr/work/${ACTIVE}`, method: "GET", body: detail(active(), null) },
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
  { match: "status=CANCELLED", body: { items: [gone()], total: 1, limit: 50, offset: 0 } },
  { match: "/api/pr/work", body: { items: [active()], total: 1, limit: 50, offset: 0 } },
];

/** After the DELETE lands, the cancelled view no longer carries the row. */
function afterDelete(fetchMock: ReturnType<typeof stubFetch>): void {
  const original = fetchMock.getMockImplementation()!;
  let deleted = false;
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "DELETE" && url.includes(`${MAINT}/terminal-items/${GONE}`)) deleted = true;
    // The stub records a call inside its original implementation; a reply
    // produced here has to be recorded by hand or the assertions on what was
    // sent after the delete would read an incomplete log.
    const handled =
      deleted &&
      method === "GET" &&
      (url.includes("status=CANCELLED") || new RegExp(`/api/pr/work/${GONE}(\\?|$)`).test(url));
    if (handled) calls(fetchMock).push({ url, method });
    if (deleted && method === "GET" && url.includes("status=CANCELLED")) {
      return new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (deleted && method === "GET" && new RegExp(`/api/pr/work/${GONE}(\\?|$)`).test(url)) {
      return new Response(
        JSON.stringify({
          error: {
            code: "pr_not_found",
            message: "Không tìm thấy công việc.",
            details: { reason: "work_item_not_found" },
          },
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    return original(input, init);
  });
}

const calls = (fetchMock: ReturnType<typeof stubFetch>) =>
  (fetchMock as unknown as { calls: Array<{ url: string; method: string }> }).calls;

// By code, not by title: the work type's name - "Bài Group/Chat khách/Order
// CTV" - is on every card, so a title regex would match the wrong row.
const cancelledCard = () => screen.findByRole("button", { name: /WRK-2026-000003/ });
const activeCard = () => screen.findByRole("button", { name: /WRK-2026-000004/ });
const cancelledCardOrNull = () => screen.queryByRole("button", { name: /WRK-2026-000003/ });
const activeCardOrNull = () => screen.queryByRole("button", { name: /WRK-2026-000004/ });
const deleteButton = () => screen.queryByRole("button", { name: "Xóa công việc" });
const statusFilter = () => screen.getByLabelText("Trạng thái");

async function openCancelledView(): Promise<void> {
  await activeCard();
  await userEvent.selectOptions(statusFilter(), "CANCELLED");
  await cancelledCard();
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("32-33. cancelled work is out of the default list and one filter away", () => {
  it("sends no status by default, lists what the server returned, and never a cancelled row", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await activeCard();
    expect(cancelledCardOrNull()).toBeNull();
    const lists = calls(fetchMock).filter(
      (one) => one.method === "GET" && /\/api\/pr\/work(\?|$)/.test(one.url),
    );
    expect(lists.length).toBeGreaterThan(0);
    for (const one of lists) expect(one.url).not.toContain("status=");
  });

  it("offers Đã hủy on the status filter, sends status=CANCELLED, and lists only cancelled rows", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await activeCard();
    const options = within(statusFilter())
      .getAllByRole("option")
      .map((one) => one.textContent);
    expect(options).toContain("Đã hủy");
    expect(options.at(-1)).toBe("Đã hủy");

    await userEvent.selectOptions(statusFilter(), "CANCELLED");
    await cancelledCard();
    expect(activeCardOrNull()).toBeNull();
    expect(NAV.current()).toContain("status=CANCELLED");
    expect(
      calls(fetchMock).some(
        (one) =>
          one.method === "GET" &&
          one.url.includes("status=CANCELLED") &&
          one.url.includes(`period_id=${PERIOD}`),
      ),
    ).toBe(true);
    // The tiles are not re-asked with the status: cancelled work is not a
    // figure, and the strip describes the month either way.
    for (const one of calls(fetchMock).filter((c) => c.url.includes("/summary"))) {
      expect(one.url).not.toContain("status=");
    }
  });

  it("says what an empty cancelled view is empty of", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("status=CANCELLED")) {
        return new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return original(input, init);
    });
    renderWithQuery(<WorkPage />);
    await activeCard();
    await userEvent.selectOptions(statusFilter(), "CANCELLED");
    expect(await screen.findByText("Không có công việc đã hủy nào ở mục này.")).toBeInTheDocument();
  });
});

describe("34. a cancelled card is whole", () => {
  it("keeps the responsible person on their own row, the status, the source, the type and the quantity", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    const node = await cancelledCard();
    expect(within(node).getByText("WRK-2026-000003")).toBeInTheDocument();
    expect(within(node).getByText("Chat khách")).toBeInTheDocument();
    const owner = within(node).getByTestId("work-owner");
    expect(owner).toHaveTextContent("Nguyen Nguyen");
    expect(within(owner).getByText("Nguyen Nguyen").className).toContain("font-semibold");
    expect(within(node).getByText("Đã hủy")).toBeInTheDocument();
    expect(within(node).getByText("Nhập thủ công")).toBeInTheDocument();
    expect(within(node).getByText("Bài Group/Chat khách/Order CTV")).toBeInTheDocument();
    expect(within(node).getByText("1 sản phẩm")).toBeInTheDocument();
    // Collapsed: no delete anywhere.
    expect(deleteButton()).toBeNull();
  });
});

describe("35. the delete lives inside the expanded detail, for the people the server named", () => {
  it("OWNER: no button on the card, the button once opened, and the status in the overview", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    expect(deleteButton()).toBeNull();
    await userEvent.click(await cancelledCard());
    await screen.findByRole("region", { name: /Chi tiết Chat khách/ });
    expect(await screen.findByTestId("admin-delete")).toBeInTheDocument();
    expect(deleteButton()).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "Tổng quan công việc" });
    expect(within(overview).getByText("Phụ trách")).toBeInTheDocument();
    expect(within(overview).getByText("Nguyen Nguyen")).toBeInTheDocument();
    // No cancel on a cancelled row, and the ordinary lifecycle is closed.
    expect(screen.queryByRole("button", { name: "Hủy" })).toBeNull();
  });

  it("never offers the delete on a row the server did not mark, even to an owner", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await activeCard());
    await screen.findByRole("region", { name: /Chi tiết Quay TVC/ });
    expect(deleteButton()).toBeNull();
    expect(screen.queryByTestId("admin-delete")).toBeNull();
  });

  for (const [role, capabilities] of [
    ["TEAM_LEAD", LEAD],
    ["EMPLOYEE", EMPLOYEE],
  ] as const) {
    it(`${role}: neither the button nor the reasoning`, async () => {
      // The server sends `admin_delete: null` to anybody below ADMIN.
      stubFetch(routes({ capabilities, eligibility: null }));
      renderWithQuery(<WorkPage />);
      await openCancelledView();
      await userEvent.click(await cancelledCard());
      await screen.findByRole("region", { name: /Chi tiết Chat khách/ });
      expect(deleteButton()).toBeNull();
      expect(screen.queryByTestId("admin-delete")).toBeNull();
      expect(screen.queryByTestId("admin-delete-blocked")).toBeNull();
      // The overview still names the person - a fact about the row.
      const overview = screen.getByRole("region", { name: "Tổng quan công việc" });
      expect(within(overview).getByText("Nguyen Nguyen")).toBeInTheDocument();
    });
  }
});

describe("36. the confirmation is explicit", () => {
  it("names the row, the person and the status, and the four promises", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    await userEvent.click(await cancelledCard());
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));

    const box = dialog();
    expect(box.getByText("Xóa công việc?")).toBeInTheDocument();
    const text = screen.getByRole("dialog").textContent ?? "";
    expect(text).toContain("WRK-2026-000003");
    expect(text).toContain("Chat khách");
    expect(text).toContain("Phụ trách: Nguyen Nguyen");
    expect(text).toContain("Trạng thái: Đã hủy");
    expect(text).toContain("“Đã hủy”");
    expect(text).not.toContain("Không được chấp nhận");
    expect(text).toContain("Việc xóa sẽ:");
    expect(text).toContain("xóa công việc này khỏi hệ thống");
    expect(text).toContain("xóa dữ liệu con chỉ thuộc riêng công việc này nếu an toàn");
    expect(text).toContain("không ảnh hưởng các công việc khác");
    expect(text).toContain("không thể khôi phục lại từ giao diện");
    expect(text).not.toContain("Bạn có chắc không");
    expect(box.getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Xác nhận xóa" })).toBeInTheDocument();
    // Nothing was sent by opening the dialog, and nothing by cancelling it.
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
    await cancelDialog();
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).toBeNull();
    await cancelledCard();
  });
});

describe("37-40. a confirmed delete removes the card and calls nothing else", () => {
  it("sends one DELETE to the cancelled route, drops the row, clears ?item, says so, reloads nothing", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    afterDelete(fetchMock);
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    await userEvent.click(await cancelledCard());
    expect(NAV.current()).toContain(`item=${GONE}`);
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));
    await confirm();

    await waitFor(() => expect(cancelledCardOrNull()).toBeNull());
    expect(screen.getByRole("status")).toHaveTextContent("Đã xóa công việc.");
    expect(screen.getByRole("status")).not.toHaveTextContent("công việc cũ");
    expect(NAV.current()).not.toContain("item=");
    expect(NAV.current()).toContain("status=CANCELLED");
    expect(document.querySelectorAll('[id^="work-detail-"]')).toHaveLength(0);
    expect(reload).not.toHaveBeenCalled();

    const sent = calls(fetchMock);
    const deletes = sent.filter((one) => one.method === "DELETE");
    expect(deletes).toHaveLength(1);
    expect(deletes[0].url).toContain(`${MAINT}/terminal-items/${GONE}`);
    expect(deletes[0].url).not.toContain(`${MAINT}/items/`);
    for (const forbidden of [
      "content-sync",
      "content-rebuild",
      "/reconcile",
      "/project",
      "/content/",
      "admin-remove",
      "cleanup-empty-containers",
      "/results",
      "/performance",
    ]) {
      expect(
        sent.some((one) => one.url.includes(forbidden)),
        forbidden,
      ).toBe(false);
    }
    expect(sent.filter((one) => one.method !== "GET" && one.method !== "DELETE")).toHaveLength(0);
    // The list and the tiles were re-asked; the deleted row's detail was not.
    const after = sent.slice(sent.findIndex((one) => one.method === "DELETE") + 1);
    expect(after.some((one) => one.url.includes("status=CANCELLED"))).toBe(true);
    expect(after.some((one) => one.url.includes("/summary"))).toBe(true);
    expect(after.some((one) => new RegExp(`/api/pr/work/${GONE}(\\?|$)`).test(one.url))).toBe(
      false,
    );
  });
});

describe("39. a blocked row explains itself", () => {
  it("shows the server's sentence and the counts instead of a button", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: BLOCKED }));
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    await userEvent.click(await cancelledCard());
    const box = await screen.findByTestId("admin-delete-blocked");
    expect(deleteButton()).toBeNull();
    expect(box).toHaveTextContent("Không thể xóa công việc này.");
    expect(box).toHaveTextContent(
      "Không thể xóa công việc này vì vẫn còn dữ liệu kết quả hoặc ghi nhận hiệu suất liên quan.",
    );
    expect(box).toHaveTextContent("Đang còn:");
    const listed = within(box)
      .getAllByRole("listitem")
      .map((one) => one.textContent);
    expect(listed).toEqual(["1 kết quả công việc", "1 ghi nhận hiệu suất", "1 phân bổ KPI/M2"]);
  });

  it("shows a refusal at delete time in the server's words, keeps the row, and re-reads the detail", async () => {
    const fetchMock = stubFetch(
      routes({
        capabilities: OWNER,
        eligibility: CLEAN,
        deleteStatus: 409,
        deleteBody: {
          error: {
            code: "pr_conflict",
            message: BLOCKED.message,
            details: {
              reason: "terminal_work_item_delete_blocked",
              cause: "blocking_references",
              operation: "terminal_delete",
              blocking: BLOCKED.blocking,
            },
          },
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    await userEvent.click(await cancelledCard());
    const detailReads = () =>
      calls(fetchMock).filter(
        (one) => one.method === "GET" && new RegExp(`/api/pr/work/${GONE}(\\?|$)`).test(one.url),
      ).length;
    const before = detailReads();
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));
    await confirm();
    expect(await screen.findByText(BLOCKED.message)).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    await cancelledCard();
    expect(NAV.current()).toContain(`item=${GONE}`);
    await waitFor(() => expect(detailReads()).toBeGreaterThan(before));
  });

  it("words a shut month's refusal as a delete", async () => {
    stubFetch(
      routes({
        capabilities: OWNER,
        eligibility: CLEAN,
        deleteStatus: 409,
        deleteBody: {
          error: {
            code: "pr_conflict",
            message: "Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa.",
            details: {
              reason: "work_period_not_open_for_cleanup",
              operation: "terminal_delete",
              period: "2026-08",
              status: "CLOSED",
            },
          },
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await openCancelledView();
    await userEvent.click(await cancelledCard());
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));
    await confirm();
    expect(
      await screen.findByText("Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa."),
    ).toBeInTheDocument();
  });
});

describe("deep link. ?item=<cancelled-id> on the default view still opens the row", () => {
  it("draws the row above the list, marked as outside the filter, with the way to Đã hủy", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    NAV.arriveAt(`/pr/work?item=${GONE}`);
    renderWithQuery(<WorkPage />);
    await activeCard();
    const outside = await screen.findByRole("region", { name: "Công việc đang mở ngoài bộ lọc" });
    expect(outside).toHaveTextContent("WRK-2026-000003");
    expect(outside).toHaveTextContent("không nằm trong bộ lọc hiện tại vì đã hủy");
    // The row is the ordinary card, open on the ordinary detail.
    expect(within(outside).getByTestId("work-owner")).toHaveTextContent("Nguyen Nguyen");
    await within(outside).findByRole("region", { name: /Chi tiết Chat khách/ });
    expect(within(outside).getByRole("button", { name: "Xóa công việc" })).toBeInTheDocument();
    // The filter was not changed behind the person's back.
    expect(NAV.current()).not.toContain("status=");

    await userEvent.click(within(outside).getByRole("button", { name: "Mở mục Đã hủy" }));
    expect(NAV.current()).toContain("status=CANCELLED");
    expect(NAV.current()).toContain(`item=${GONE}`);
    // Now the list owns the row, and the marker is gone.
    await cancelledCard();
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Công việc đang mở ngoài bộ lọc" })).toBeNull(),
    );
  });

  it("closes the row without touching the filter", async () => {
    stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    NAV.arriveAt(`/pr/work?item=${GONE}`);
    renderWithQuery(<WorkPage />);
    const outside = await screen.findByRole("region", { name: "Công việc đang mở ngoài bộ lọc" });
    await userEvent.click(within(outside).getByRole("button", { name: "Đóng" }));
    expect(NAV.current()).not.toContain("item=");
    expect(NAV.current()).not.toContain("status=");
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Công việc đang mở ngoài bộ lọc" })).toBeNull(),
    );
    await activeCard();
  });

  it("says when the linked row no longer exists", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, eligibility: CLEAN }));
    afterDelete(fetchMock);
    // Simulate a link to a row somebody already deleted.
    await fetchMock(`${MAINT}/terminal-items/${GONE}`, { method: "DELETE" });
    NAV.arriveAt(`/pr/work?item=${GONE}`);
    renderWithQuery(<WorkPage />);
    const outside = await screen.findByRole("region", { name: "Công việc đang mở ngoài bộ lọc" });
    expect(await within(outside).findByText(/không còn tồn tại/)).toBeInTheDocument();
    expect(within(outside).getByRole("button", { name: "Đóng" })).toBeInTheDocument();
    await activeCard();
  });
});
