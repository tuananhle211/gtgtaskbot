/**
 * Owner-visibility patch. **The responsible person is the card's second line.**
 *
 * The name used to be the middle item of the muted metadata string -
 * "Không có hạn · Trần Minh Anh · Nguồn: CNT-…" - which is the one thing a
 * manager scanning a long list is looking for, printed where it is hardest to
 * find. These tests hold the card to the new shape, numbered against the
 * task's own list:
 *
 * 1-2. the name is on its own row, at body weight, and is not inside the
 *      deadline/source line;
 * 3.   no responsible person renders "Chưa có người phụ trách" - never a
 *      dash, `undefined` or `null`;
 * 4.   a primary plus others renders the primary and "+2 người tham gia",
 *      not five names across the card; the detail still lists everybody;
 * 5-7. manual, content and recurring cards all name their owner;
 * 8.   a period container names its subject.
 *
 * Who the person *is* comes from the ledger's own semantics (`PRIMARY`
 * contribution; a container's subject). No team concept is invented.
 * Nothing here contacts a network.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, renderWithQuery, SESSION, stubFetch } from "./helpers";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const MANAGER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE", "PR_WORK_VIEW_ALL"];

const PERIOD = "66666666-6666-6666-6666-666666666666";
const TYPE = "11111111-1111-1111-1111-111111111111";
const ANH = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";
const HUY = "44444444-4444-4444-4444-444444444444";
const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const D = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const CONTENT = "55555555-5555-5555-5555-555555555555";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const WORK_TYPE = {
  id: TYPE,
  code: "TINY_SCRIPT",
  name: "Kịch bản siêu ngắn",
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

const contribution = (
  itemId: string,
  userId: string,
  name: string,
  over: Record<string, unknown> = {},
) => ({
  id: `${itemId.slice(0, 8)}-0000-0000-0000-${userId.slice(0, 12)}`,
  work_item_id: itemId,
  user_id: userId,
  user_name: name,
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-01T02:00:00Z",
  count_status: "COUNTED",
  count_status_label: "Đã ghi nhận",
  counted_at: "2026-09-10T03:25:00Z",
  excluded_reason: null,
  ...over,
});

const item = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  id,
  code: `WRK-2026-${id.slice(0, 6)}`,
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
  status: "APPROVED",
  status_label: "Đã xác nhận",
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
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: "2026-09-10T03:25:00Z",
  approved_at: "2026-09-10T03:25:00Z",
  approved_by_user_id: SESSION.user_id,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  content_id: null,
  content_code: null,
  recurring_occurrence_id: null,
  recurring_template_id: null,
  recurring_template_name: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution(id, ANH, "Trần Minh Anh")],
  ...over,
});

const legacyContent = () =>
  item(A, "Nội dung: Một kiểu trưởng thành rất buồn", {
    source_type: "CONTENT",
    source_label: "Từ quy trình nội dung",
    is_source_derived: true,
    is_legacy_content_work: true,
    content_id: CONTENT,
    content_code: "CNT-2026-000776",
  });

const manual = () =>
  item(B, "Quay TVC Apexmed", {
    execution_at: null,
    due_at: "2026-09-20T09:00:00Z",
    status: "ACCEPTED",
    status_label: "Được giao",
    contributors: [contribution(B, LINH, "Nguyễn Thùy Linh", { count_status: "PENDING" })],
  });

const recurring = () =>
  item(C, "100 comment seeding", {
    source_type: "RECURRING",
    source_label: "Định kỳ",
    is_source_derived: true,
    recurring_occurrence_id: "77777777-7777-7777-7777-777777777777",
    recurring_template_id: "88888888-8888-8888-8888-888888888888",
    recurring_template_name: "100 comment mỗi ngày",
    contributors: [contribution(C, HUY, "Phạm Quốc Huy")],
  });

const container = () =>
  item(D, "Tìm khách hàng · 2026-09", {
    is_period_container: true,
    reporting_period_id: PERIOD,
    subject_user_id: LINH,
    status: "ACCEPTED",
    status_label: "Đang ghi nhận kết quả",
    quantity: "27.00",
    unit: "CUSTOMER",
    unit_label: "khách hàng",
    execution_at: "2026-09-01T00:00:00Z",
    period_container: {
      period_id: PERIOD,
      period_code: "2026-09",
      period_status: "OPEN",
      subject_user_id: LINH,
      subject_name: "Nguyễn Thùy Linh",
      unit: "CUSTOMER",
      unit_label: "khách hàng",
      actual_quantity: "27.00",
      declared_quantity: "27.00",
      pending_quantity: "0.00",
      excluded_quantity: "0.00",
      result_count: 6,
      pending_count: 0,
      target_quantity: "20.00",
      has_target: true,
      completion_percent: "135.0",
      over_target_quantity: "7.00",
      remaining_quantity: "0.00",
      is_target_met: true,
      progress_percent: "100",
      actual_label: "27 / 20 khách hàng",
      standard_minutes: null,
      standard_minutes_per_unit: null,
      scoring_status: "UNSCORED",
      scoring_status_label: "Chưa quy đổi",
    },
    contributors: [contribution(D, LINH, "Nguyễn Thùy Linh")],
  });

const detail = (row: Record<string, unknown>) => ({
  item: row,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: (row.content_code as string | null) ?? null,
  can_manage: true,
  can_validate: false,
  can_execute: true,
  results: [],
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
  can_delete_legacy: false,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 1,
  accepted: 1,
  completed: 0,
  approved: 1,
  counted_work_items: 1,
  counted_contributions: 1,
  open: 0,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
};

const routes = (items: Array<Record<string, unknown>>) => [
  { match: "/api/pr/dashboard", body: dashboard(MANAGER) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  ...items.map((one) => ({
    match: `/api/pr/work/${one.id}`,
    method: "GET",
    body: detail(one),
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
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

/** The card button whose accessible name contains the title. */
const card = async (title: string) =>
  screen.findByRole("button", { name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });

/** The owner row inside one card. */
const ownerRow = (node: HTMLElement) => within(node).getByTestId("work-owner");

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("1-2. the responsible person has a row of their own", () => {
  it("prints the name under the title, at body weight, and not in the metadata line", async () => {
    stubFetch(routes([legacyContent()]));
    renderWithQuery(<WorkPage />);
    const node = await card("Một kiểu trưởng thành rất buồn");

    const row = ownerRow(node);
    expect(row).toHaveTextContent("Trần Minh Anh");
    const name = within(row).getByText("Trần Minh Anh");
    expect(name.className).toContain("font-semibold");
    expect(name.className).not.toContain("text-[var(--text-muted)]");
    // The row is the card's second line: right after the title.
    const title = within(node).getByText("Nội dung: Một kiểu trưởng thành rất buồn");
    expect(title.nextElementSibling).toBe(row);
    // And the muted metadata line no longer carries the name.
    const metadata = within(node).getByText(/Nguồn: CNT-2026-000776/);
    expect(metadata.textContent).not.toContain("Trần Minh Anh");
    expect(metadata.textContent).toContain("Không có hạn");
    // The rest of the card is intact: status, source, type, quantity, when.
    expect(node.textContent).toContain("Đã xác nhận");
    expect(node.textContent).toContain("Từ quy trình nội dung");
    expect(node.textContent).toContain("Kịch bản siêu ngắn");
    expect(node.textContent).toContain("1 sản phẩm");
    expect(node.textContent).toMatch(/Thực hiện /);
    expect(node.textContent).toContain("Xem chi tiết");
  });
});

describe("3. no responsible person", () => {
  it("says so in words and never prints a dash, undefined or null", async () => {
    const proposal = item(B, "Đề xuất chưa ai nhận", {
      status: "PROPOSED",
      status_label: "Đề xuất",
      contributors: [],
      execution_at: null,
      accepted_at: null,
      quantity: null,
      unit: null,
      unit_label: null,
    });
    stubFetch(routes([proposal]));
    renderWithQuery(<WorkPage />);
    const node = await card("Đề xuất chưa ai nhận");
    const row = ownerRow(node);
    expect(row).toHaveTextContent("Chưa có người phụ trách");
    expect(within(row).getByText("Chưa có người phụ trách").className).toContain(
      "text-[var(--text-muted)]",
    );
    expect(node.textContent).not.toContain("—");
    expect(node.textContent).not.toContain("undefined");
    expect(node.textContent).not.toContain("null");
  });

  it("treats a contributor with no resolved name as nobody, not as a dash", async () => {
    const nameless = item(B, "Không tên", {
      contributors: [contribution(B, ANH, "", { user_name: null })],
    });
    stubFetch(routes([nameless]));
    renderWithQuery(<WorkPage />);
    const node = await card("Không tên");
    expect(ownerRow(node)).toHaveTextContent("Chưa có người phụ trách");
    expect(node.textContent).not.toContain("—");
  });
});

describe("4. a primary responsible person plus other contributors", () => {
  it("names the primary and counts the rest; the detail still lists everybody", async () => {
    const shoot = item(B, "Quay TVC Apexmed", {
      contributors: [
        contribution(B, LINH, "Nguyễn Thùy Linh", {
          contribution_role: "CONTRIBUTOR",
          contribution_role_label: "Tham gia",
          assigned_at: "2026-09-01T01:00:00Z",
        }),
        contribution(B, ANH, "Trần Minh Anh"),
        contribution(B, HUY, "Phạm Quốc Huy", {
          contribution_role: "SUPPORT",
          contribution_role_label: "Hỗ trợ",
        }),
      ],
    });
    stubFetch(routes([shoot]));
    renderWithQuery(<WorkPage />);
    const node = await card("Quay TVC Apexmed");
    const row = ownerRow(node);
    // The PRIMARY is the responsible person, whatever the list order.
    expect(within(row).getByText("Trần Minh Anh")).toBeInTheDocument();
    expect(row).toHaveTextContent("+2 người tham gia");
    expect(node.textContent).not.toContain("Nguyễn Thùy Linh");
    expect(node.textContent).not.toContain("Phạm Quốc Huy");

    await userEvent.click(node);
    const people = (await screen.findByText("Người thực hiện")).parentElement as HTMLElement;
    expect(within(people).getByText("Nguyễn Thùy Linh")).toBeInTheDocument();
    expect(within(people).getByText("Phạm Quốc Huy")).toBeInTheDocument();
    expect(within(people).getByText("Trần Minh Anh")).toBeInTheDocument();
    // And the detail's overview leads with the same person.
    const overview = screen.getByRole("region", { name: "Tổng quan công việc" });
    expect(within(overview).getByText("Phụ trách")).toBeInTheDocument();
    expect(within(overview).getByText("Trần Minh Anh")).toBeInTheDocument();
  });
});

describe("5-7. every source names its owner the same way", () => {
  it("manual work: the assignee", async () => {
    stubFetch(routes([manual()]));
    renderWithQuery(<WorkPage />);
    const node = await card("Quay TVC Apexmed");
    expect(within(ownerRow(node)).getByText("Nguyễn Thùy Linh")).toBeInTheDocument();
    expect(node.textContent).toMatch(/Hạn /);
    expect(within(node).getByText(/^Hạn /).textContent).not.toContain("Nguyễn Thùy Linh");
  });

  it("content work: the writer the projector credited", async () => {
    stubFetch(routes([legacyContent()]));
    renderWithQuery(<WorkPage />);
    const node = await card("Một kiểu trưởng thành rất buồn");
    expect(within(ownerRow(node)).getByText("Trần Minh Anh")).toBeInTheDocument();
  });

  it("recurring work: the routine's assignee, with the routine named as the source", async () => {
    stubFetch(routes([recurring()]));
    renderWithQuery(<WorkPage />);
    const node = await card("100 comment seeding");
    expect(within(ownerRow(node)).getByText("Phạm Quốc Huy")).toBeInTheDocument();
    const metadata = within(node).getByText(/Nguồn: 100 comment mỗi ngày/);
    expect(metadata.textContent).not.toContain("Phạm Quốc Huy");
  });
});

describe("8. a period container names its subject", () => {
  it("prints the stream's subject on the owner row and nobody else", async () => {
    stubFetch(routes([container()]));
    renderWithQuery(<WorkPage />);
    const node = await card("Tìm khách hàng");
    const row = ownerRow(node);
    expect(within(row).getByText("Nguyễn Thùy Linh")).toBeInTheDocument();
    expect(row.textContent).not.toContain("người tham gia");
    // The figures the container is about are still there under it.
    expect(node.textContent).toContain("27 / 20 khách hàng");
  });

  it("falls back to the container's subject name when the contribution list is empty", async () => {
    const stream = container();
    stubFetch(routes([{ ...stream, contributors: [] }]));
    renderWithQuery(<WorkPage />);
    const node = await card("Tìm khách hàng");
    expect(within(ownerRow(node)).getByText("Nguyễn Thùy Linh")).toBeInTheDocument();
  });
});
