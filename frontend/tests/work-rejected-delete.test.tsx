/**
 * Safe delete for REJECTED work items. Work maintenance.
 *
 * A rejected proposal is as finished as a cancelled job - the lifecycle has
 * no edge out of either - and an administrator who opens one may delete it
 * outright when the server says it is safe. Never by cancelling it first;
 * there is no such transition. The detail carries one administrative delete
 * (`admin_delete`, with its `rule`) and the page draws from that alone.
 * Numbered against the task:
 *
 * 1.     REJECTED shows no Hủy;
 * 2-3.   REJECTED eligible + ADMIN / OWNER shows Xóa công việc;
 * 4-5.   TEAM_LEAD / EMPLOYEE get neither the button nor the reasoning;
 * 6.     a blocked REJECTED row shows the blocking explanation, not a button;
 * 7.     CANCELLED safe delete still works (see also work-cancelled.test.tsx);
 * 8.     a period container never shows the hard delete, whatever it says;
 * 9.     a modern result keeps its own Xóa kết quả công việc, separate;
 * 10.    a row both rules cover draws one Xóa công việc, not two;
 * 11.    the confirmation names the status - "Không được chấp nhận" - and
 *        never calls a rejected proposal "đã hủy";
 * 12.    a confirmed delete sends one DELETE to the terminal route, removes
 *        the card and its cache entry, clears ?item, says "Đã xóa công việc."
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
const ADMIN = OWNER;
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const PERIOD = "66666666-6666-6666-6666-666666666666";
const TYPE = "11111111-1111-1111-1111-111111111111";
const LINH = "22222222-2222-2222-2222-222222222222";
const REJECTED = "abcdefab-cdef-abcd-efab-cdefabcdefab";
const CANCELLED = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const LEGACY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CONTAINER = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const CONTENT = "55555555-5555-5555-5555-555555555555";
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
  code: "SHORT_SCRIPT",
  name: "Kịch bản video ngắn/Bài đăng",
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

const contribution = (itemId: string, countStatus = "EXCLUDED") => ({
  id: `${itemId.slice(0, 8)}-0000-0000-0000-000000000001`,
  work_item_id: itemId,
  user_id: LINH,
  user_name: "Huyền Linh",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-10T03:25:00Z",
  count_status: countStatus,
  count_status_label: countStatus === "EXCLUDED" ? "Không tính" : "Chờ xác nhận",
  counted_at: null,
  excluded_reason: countStatus === "EXCLUDED" ? "Đề xuất không được chấp nhận" : null,
});

const item = (id: string, code: string, title: string, over: Record<string, unknown> = {}) => ({
  id,
  code,
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
  status: "REJECTED",
  status_label: "Không được chấp nhận",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: "20.00",
  unit: "ITEM",
  unit_label: "sản phẩm",
  due_at: null,
  execution_at: "2026-09-10T03:25:00Z",
  is_overdue: false,
  created_by_user_id: LINH,
  assigned_by_user_id: null,
  assigned_at: null,
  accepted_at: null,
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: "2026-09-11T02:00:00Z",
  cancel_reason: "Không phù hợp",
  channel_id: null,
  content_id: null,
  content_code: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  created_at: "2026-09-10T03:25:00Z",
  contributors: [contribution(id)],
  ...over,
});

const rejected = () => item(REJECTED, "WRK-2026-000053", "Kịch bản video tâm sự");
const cancelled = () =>
  item(CANCELLED, "WRK-2026-000003", "Chat khách", { status: "CANCELLED", status_label: "Đã hủy" });
const legacyRejected = () =>
  item(LEGACY, "WRK-2026-000059", "Nội dung: Một kiểu trưởng thành rất buồn", {
    source_type: "CONTENT",
    source_label: "Từ quy trình nội dung",
    is_source_derived: true,
    is_legacy_content_work: true,
    content_id: CONTENT,
    content_code: "CNT-2026-000776",
  });
const container = () =>
  item(CONTAINER, "WRK-2026-000070", "Kịch bản video ngắn/Bài đăng · 2026-09", {
    is_period_container: true,
    reporting_period_id: PERIOD,
    subject_user_id: LINH,
    period_container: {
      period_id: PERIOD,
      period_code: "2026-09",
      subject_user_id: LINH,
      subject_name: "Huyền Linh",
      actual: "3.00",
      target: "10.00",
      unit: "ITEM",
      unit_label: "sản phẩm",
      result_count: 1,
      counted_count: 1,
      pending_count: 0,
      standard_minutes: null,
    },
  });

const eligibility = (previous: string, over: Record<string, unknown> = {}) => ({
  rule: "terminal",
  deletable: true,
  reason: null,
  cause: null,
  blocking: { results: 0, counted_contributions: 0, quota_allocations: 0, score_allocations: 0 },
  period_code: "2026-09",
  message: null,
  previous_status: previous,
  ...over,
});

const BLOCKED = eligibility("REJECTED", {
  deletable: false,
  reason: "terminal_work_item_delete_blocked",
  cause: "blocking_references",
  blocking: { results: 1, counted_contributions: 1, quota_allocations: 0, score_allocations: 0 },
  message:
    "Không thể xóa công việc này vì vẫn còn dữ liệu kết quả hoặc ghi nhận hiệu suất liên quan.",
});

const NO_ACTIONS = {
  can_accept: false,
  can_reject: false,
  can_start: false,
  can_complete: false,
  can_approve: false,
  can_reopen: false,
  can_cancel: false,
};

const detail = (row: Record<string, unknown>, adminDelete: Record<string, unknown> | null) => ({
  item: row,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: (row.content_code as string | null) ?? null,
  can_manage: true,
  can_validate: true,
  can_execute: true,
  results: [],
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
  ...NO_ACTIONS,
  can_delete_legacy: adminDelete?.rule === "legacy",
  can_admin_delete: Boolean(adminDelete?.deletable),
  admin_delete: adminDelete,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 1,
  accepted: 0,
  completed: 0,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 0,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
};

const deletion = () => ({
  work_item_id: REJECTED,
  code: "WRK-2026-000053",
  title: "Kịch bản video tâm sự",
  work_type_id: TYPE,
  source_type: "MANUAL",
  source_key: null,
  content_id: null,
  content_code: null,
  recurring_occurrence_id: null,
  responsible_user_id: LINH,
  period_code: "2026-09",
  previous_status: "REJECTED",
  removed: { contributions: 1, evidence: 0, history: 2 },
  results_created: 0,
  projection_requested: false,
});

const routes = ({
  capabilities,
  rows,
  details,
  deleteStatus = 200,
  deleteBody = deletion(),
}: {
  capabilities: string[];
  rows: Array<Record<string, unknown>>;
  details: Record<string, unknown>;
  deleteStatus?: number;
  deleteBody?: unknown;
}) => [
  {
    match: `${MAINT}/terminal-items/${REJECTED}`,
    method: "DELETE",
    status: deleteStatus,
    body: deleteBody,
  },
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  ...Object.entries(details).map(([id, body]) => ({
    match: `/api/pr/work/${id}`,
    method: "GET",
    body,
  })),
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
  { match: "/api/pr/work", body: { items: rows, total: rows.length, limit: 50, offset: 0 } },
];

const calls = (fetchMock: ReturnType<typeof stubFetch>) =>
  (fetchMock as unknown as { calls: Array<{ url: string; method: string }> }).calls;

const cardOf = (code: string) => screen.findByRole("button", { name: new RegExp(code) });
const deleteButtons = () => screen.queryAllByRole("button", { name: "Xóa công việc" });

async function openRejected(): Promise<HTMLElement> {
  await userEvent.click(await cardOf("WRK-2026-000053"));
  return screen.findByRole("region", { name: /Chi tiết Kịch bản video tâm sự/ });
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("1-3. a clean REJECTED row: no Hủy, and the delete for ADMIN and OWNER", () => {
  for (const [role, capabilities] of [
    ["ADMIN", ADMIN],
    ["OWNER", OWNER],
  ] as const) {
    it(`${role}: the label, the person, no Hủy, and Xóa công việc inside the detail`, async () => {
      stubFetch(
        routes({
          capabilities,
          rows: [rejected()],
          details: { [REJECTED]: detail(rejected(), eligibility("REJECTED")) },
        }),
      );
      renderWithQuery(<WorkPage />);
      const card = await cardOf("WRK-2026-000053");
      expect(within(card).getByTestId("work-owner")).toHaveTextContent("Huyền Linh");
      expect(within(card).getByText("Không được chấp nhận")).toBeInTheDocument();
      expect(within(card).getByText("20 sản phẩm")).toBeInTheDocument();
      // Collapsed: no delete anywhere.
      expect(deleteButtons()).toHaveLength(0);

      const region = await openRejected();
      expect(within(region).queryByRole("button", { name: "Hủy" })).toBeNull();
      expect(within(region).getByTestId("admin-delete")).toHaveTextContent(
        "Không được chấp nhận",
      );
      expect(within(region).getByRole("button", { name: "Xóa công việc" })).toBeInTheDocument();
      expect(deleteButtons()).toHaveLength(1);
    });
  }
});

describe("4-5. TEAM_LEAD and EMPLOYEE get neither the button nor the reasoning", () => {
  for (const [role, capabilities] of [
    ["TEAM_LEAD", LEAD],
    ["EMPLOYEE", EMPLOYEE],
  ] as const) {
    it(`${role}: nothing to press`, async () => {
      // The server sends `admin_delete: null` to anybody below ADMIN.
      stubFetch(
        routes({
          capabilities,
          rows: [rejected()],
          details: { [REJECTED]: detail(rejected(), null) },
        }),
      );
      renderWithQuery(<WorkPage />);
      const region = await openRejected();
      expect(within(region).queryByRole("button", { name: "Hủy" })).toBeNull();
      expect(deleteButtons()).toHaveLength(0);
      expect(within(region).queryByTestId("admin-delete")).toBeNull();
      expect(within(region).queryByTestId("admin-delete-blocked")).toBeNull();
      const overview = within(region).getByRole("region", { name: "Tổng quan công việc" });
      expect(within(overview).getByText("Huyền Linh")).toBeInTheDocument();
    });
  }
});

describe("6. a blocked REJECTED row explains itself instead of offering a button", () => {
  it("shows the sentence and the counts", async () => {
    stubFetch(
      routes({
        capabilities: ADMIN,
        rows: [rejected()],
        details: { [REJECTED]: detail(rejected(), BLOCKED) },
      }),
    );
    renderWithQuery(<WorkPage />);
    const region = await openRejected();
    expect(deleteButtons()).toHaveLength(0);
    const box = within(region).getByTestId("admin-delete-blocked");
    expect(box).toHaveTextContent("Không thể xóa công việc này.");
    expect(box).toHaveTextContent(String(BLOCKED.message));
    const listed = within(box)
      .getAllByRole("listitem")
      .map((one) => one.textContent);
    expect(listed).toEqual(["1 kết quả công việc", "1 ghi nhận hiệu suất"]);
  });
});

describe("7-8. cancelled still deletes; a container never does", () => {
  it("offers the delete on a clean CANCELLED row under the same block", async () => {
    stubFetch(
      routes({
        capabilities: OWNER,
        rows: [cancelled()],
        details: { [CANCELLED]: detail(cancelled(), eligibility("CANCELLED")) },
      }),
    );
    NAV.arriveAt("/pr/work?status=CANCELLED");
    renderWithQuery(<WorkPage />);
    await userEvent.click(await cardOf("WRK-2026-000003"));
    const region = await screen.findByRole("region", { name: /Chi tiết Chat khách/ });
    expect(within(region).queryByRole("button", { name: "Hủy" })).toBeNull();
    expect(within(region).getByTestId("admin-delete")).toHaveTextContent("Đã hủy");
    expect(within(region).getByRole("button", { name: "Xóa công việc" })).toBeInTheDocument();
  });

  it("draws no hard delete on a period container, even when old data marked it terminal", async () => {
    // The server refuses a container with `cause: period_container`, and says so.
    stubFetch(
      routes({
        capabilities: OWNER,
        rows: [container()],
        details: {
          [CONTAINER]: detail(
            container(),
            eligibility("REJECTED", {
              deletable: false,
              reason: "terminal_work_item_delete_blocked",
              cause: "period_container",
              message: "Luồng công việc theo kỳ không xóa bằng cách này.",
            }),
          ),
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await cardOf("WRK-2026-000070"));
    await screen.findByRole("region", { name: /Chi tiết/ });
    expect(deleteButtons()).toHaveLength(0);
    expect(screen.getByTestId("admin-delete-blocked")).toHaveTextContent(
      "Luồng công việc theo kỳ không xóa bằng cách này.",
    );
  });
});

describe("9-10. one delete per panel, and results keep their own control", () => {
  it("draws the legacy block, and only one Xóa công việc, for a rejected legacy row", async () => {
    stubFetch(
      routes({
        capabilities: OWNER,
        rows: [legacyRejected()],
        details: {
          [LEGACY]: detail(legacyRejected(), {
            rule: "legacy",
            deletable: true,
            reason: null,
            cause: null,
            blocking: {},
            period_code: null,
            message: null,
            previous_status: "REJECTED",
          }),
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await cardOf("WRK-2026-000059"));
    await screen.findByRole("region", { name: /Chi tiết/ });
    expect(deleteButtons()).toHaveLength(1);
    expect(screen.queryByTestId("admin-delete")).toBeNull();
    expect(screen.getByRole("button", { name: "Đồng bộ lại từ Nội dung" })).toBeInTheDocument();
  });

  it("never shows Xóa kết quả công việc on a rejected one-off job - that control is a result's", async () => {
    stubFetch(
      routes({
        capabilities: OWNER,
        rows: [rejected()],
        details: { [REJECTED]: detail(rejected(), eligibility("REJECTED")) },
      }),
    );
    renderWithQuery(<WorkPage />);
    await openRejected();
    expect(screen.queryByRole("button", { name: /Xóa kết quả/ })).toBeNull();
  });
});

describe("11-12. the confirmation names the status, and a confirmed delete removes the row", () => {
  it("says Không được chấp nhận, never đã hủy, and promises the four things", async () => {
    const fetchMock = stubFetch(
      routes({
        capabilities: ADMIN,
        rows: [rejected()],
        details: { [REJECTED]: detail(rejected(), eligibility("REJECTED")) },
      }),
    );
    renderWithQuery(<WorkPage />);
    const region = await openRejected();
    await userEvent.click(within(region).getByRole("button", { name: "Xóa công việc" }));
    const box = dialog();
    expect(box.getByText("Xóa công việc?")).toBeInTheDocument();
    const text = screen.getByRole("dialog").textContent ?? "";
    expect(text).toContain("WRK-2026-000053");
    expect(text).toContain("Kịch bản video tâm sự");
    expect(text).toContain("Phụ trách: Huyền Linh");
    expect(text).toContain("Trạng thái: Không được chấp nhận");
    expect(text).toContain("“Không được chấp nhận”");
    expect(text).not.toContain("đã bị hủy");
    expect(text).not.toContain("Đã hủy");
    expect(text).toContain("xóa công việc này khỏi hệ thống");
    expect(text).toContain("xóa dữ liệu con chỉ thuộc riêng công việc này nếu an toàn");
    expect(text).toContain("không ảnh hưởng các công việc khác");
    expect(text).toContain("không thể khôi phục lại từ giao diện");
    expect(box.getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Xác nhận xóa" })).toBeInTheDocument();
    await cancelDialog();
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
  });

  it("sends one DELETE to the terminal route, drops the card, clears ?item, says so", async () => {
    const fetchMock = stubFetch(
      routes({
        capabilities: ADMIN,
        rows: [rejected()],
        details: { [REJECTED]: detail(rejected(), eligibility("REJECTED")) },
      }),
    );
    const original = fetchMock.getMockImplementation()!;
    let deleted = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "DELETE" && url.includes(`${MAINT}/terminal-items/${REJECTED}`)) {
        deleted = true;
      }
      if (deleted && method === "GET" && /\/api\/pr\/work(\?|$)/.test(url)) {
        calls(fetchMock).push({ url, method });
        return new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return original(input, init);
    });
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    renderWithQuery(<WorkPage />);
    const region = await openRejected();
    expect(NAV.current()).toContain(`item=${REJECTED}`);
    await userEvent.click(within(region).getByRole("button", { name: "Xóa công việc" }));
    await confirm();

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /WRK-2026-000053/ })).toBeNull(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Đã xóa công việc.");
    expect(NAV.current()).not.toContain("item=");
    expect(document.querySelectorAll('[id^="work-detail-"]')).toHaveLength(0);
    expect(reload).not.toHaveBeenCalled();
    const sent = calls(fetchMock);
    const deletes = sent.filter((one) => one.method === "DELETE");
    expect(deletes).toHaveLength(1);
    expect(deletes[0].url).toContain(`${MAINT}/terminal-items/${REJECTED}`);
    // Never a cancel on the way out, and nothing else written.
    expect(sent.some((one) => one.url.includes("/cancel"))).toBe(false);
    expect(sent.filter((one) => one.method !== "GET" && one.method !== "DELETE")).toHaveLength(0);
    const after = sent.slice(sent.findIndex((one) => one.method === "DELETE") + 1);
    expect(
      after.some((one) => new RegExp(`/api/pr/work/${REJECTED}(\\?|$)`).test(one.url)),
    ).toBe(false);
  });
});
