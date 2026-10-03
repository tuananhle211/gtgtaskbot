/**
 * User-initiated deletion of one **legacy** content work item. Work maintenance.
 *
 * The old projector wrote one work item per content milestone; the current
 * one writes results into a monthly container. An administrator who opens an
 * old row may delete it - that row, decided by that person, and nothing put
 * back automatically. These tests hold the screen to that, numbered against
 * the task:
 *
 * 9-12. OWNER and ADMIN see *Xóa công việc* on a legacy row; TEAM_LEAD and
 *       EMPLOYEE do not. The flag is the server's (`can_delete_legacy`); the
 *       capability only decides whether the collapsed card mentions the row
 *       is legacy at all;
 * 46.   the delete is inside the expanded detail, never on a collapsed card,
 *       and never on a row the server did not mark;
 * 47.   the confirmation says what is *not* going to happen: no sync, no
 *       replacement result, no re-mapping, the content stays;
 * 48-53. a confirmed delete sends exactly one DELETE, removes the card, clears
 *       `?item=`, reloads nothing, and calls no sync, rebuild, reconcile or
 *       projection route. The list says "Đã xóa công việc cũ.";
 * K.    a shut month's refusal is worded as a delete, from the server's
 *       structured reason.
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
const LEGACY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const MANUAL = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const CONTENT = "55555555-5555-5555-5555-555555555555";
const MAINT = "/api/pr/work/maintenance";

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

const contribution = (itemId: string) => ({
  id: `${itemId.slice(0, 8)}-0000-0000-0000-000000000001`,
  work_item_id: itemId,
  user_id: ANH,
  user_name: "Trần Minh Anh",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-10T03:25:00Z",
  count_status: "COUNTED",
  count_status_label: "Đã ghi nhận",
  counted_at: "2026-09-10T03:25:00Z",
  excluded_reason: null,
});

const item = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  id,
  code: id === LEGACY ? "WRK-2026-000059" : "WRK-2026-000060",
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
  assigned_by_user_id: null,
  assigned_at: "2026-09-10T03:25:00Z",
  accepted_at: "2026-09-10T03:25:00Z",
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
  created_at: "2026-09-10T03:25:00Z",
  contributors: [contribution(id)],
  ...over,
});

const legacy = () =>
  item(LEGACY, "Nội dung: Một kiểu trưởng thành rất buồn", {
    source_type: "CONTENT",
    source_label: "Từ quy trình nội dung",
    is_source_derived: true,
    is_legacy_content_work: true,
    content_id: CONTENT,
    content_code: "CNT-2026-000776",
  });

const manual = () => item(MANUAL, "Quay TVC Apexmed");

const detail = (row: Record<string, unknown>, canDelete: boolean) => ({
  item: row,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: (row.content_code as string | null) ?? null,
  can_manage: false,
  can_validate: false,
  can_execute: true,
  results: [],
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
  can_delete_legacy: canDelete,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 2,
  accepted: 2,
  completed: 0,
  approved: 2,
  counted_work_items: 2,
  counted_contributions: 2,
  open: 0,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
};

const deletion = () => ({
  work_item_id: LEGACY,
  code: "WRK-2026-000059",
  title: "Nội dung: Một kiểu trưởng thành rất buồn",
  work_type_id: TYPE,
  content_id: CONTENT,
  content_code: "CNT-2026-000776",
  responsible_user_id: ANH,
  period_code: "2026-09",
  removed: {
    contributions: 1,
    counted_contributions: 1,
    quota_allocations: 0,
    score_allocations: 0,
    evidence: 0,
    history: 3,
  },
  performance_refreshed: 0,
  results_created: 0,
  projection_requested: false,
});

/**
 * The stub as a server: the list answers with `items` until the legacy row is
 * DELETEd, then without it. Order matters: `stubFetch` takes the first
 * substring hit, so the specific routes precede `/api/pr/work`.
 */
const routes = ({
  capabilities,
  canDelete,
  deleteStatus = 200,
  deleteBody = deletion(),
}: {
  capabilities: string[];
  canDelete: boolean;
  deleteStatus?: number;
  deleteBody?: unknown;
}) => [
  { match: `${MAINT}/items/${LEGACY}`, method: "DELETE", status: deleteStatus, body: deleteBody },
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  { match: `/api/pr/work/${LEGACY}`, method: "GET", body: detail(legacy(), canDelete) },
  { match: `/api/pr/work/${MANUAL}`, method: "GET", body: detail(manual(), false) },
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
  {
    match: "/api/pr/work",
    body: { items: [legacy(), manual()], total: 2, limit: 50, offset: 0 },
  },
];

/** After the DELETE lands, the list no longer carries the row. */
function afterDelete(fetchMock: ReturnType<typeof stubFetch>): void {
  const original = fetchMock.getMockImplementation()!;
  let deleted = false;
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "DELETE" && url.includes(`${MAINT}/items/${LEGACY}`)) deleted = true;
    if (deleted && method === "GET" && /\/api\/pr\/work(\?|$)/.test(url)) {
      return new Response(
        JSON.stringify({ items: [manual()], total: 1, limit: 50, offset: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return original(input, init);
  });
}

const calls = (fetchMock: ReturnType<typeof stubFetch>) =>
  (fetchMock as unknown as { calls: Array<{ url: string; method: string }> }).calls;

const legacyCard = () => screen.findByRole("button", { name: /Một kiểu trưởng thành rất buồn/ });
const manualCard = () => screen.findByRole("button", { name: /Quay TVC Apexmed/ });
const deleteButton = () => screen.queryByRole("button", { name: "Xóa công việc" });

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("9-10, 46. OWNER and ADMIN see the delete, inside the expanded legacy detail only", () => {
  it("marks the collapsed card quietly and offers the delete only once opened", async () => {
    stubFetch(routes({ capabilities: OWNER, canDelete: true }));
    renderWithQuery(<WorkPage />);
    const node = await legacyCard();
    // The marker is a neutral pill in the same row as the status - the same
    // styling as the source badge beside it, and never the status's colour.
    const marker = within(node).getByText("Dữ liệu cũ từ Nội dung");
    expect(marker.className).toBe(within(node).getByText("Từ quy trình nội dung").className);
    expect(marker.className).not.toBe(within(node).getByText("Đã xác nhận").className);
    expect(marker.className).not.toMatch(/red|amber/);
    // Collapsed: no delete anywhere.
    expect(deleteButton()).toBeNull();

    await userEvent.click(node);
    await screen.findByRole("region", { name: /Chi tiết/ });
    expect(deleteButton()).toBeInTheDocument();
    // The overview says who, from where, what kind, and what the data is.
    const overview = screen.getByRole("region", { name: "Tổng quan công việc" });
    expect(within(overview).getByText("Phụ trách")).toBeInTheDocument();
    expect(within(overview).getByText("Trần Minh Anh")).toBeInTheDocument();
    expect(within(overview).getByText("Từ quy trình nội dung")).toBeInTheDocument();
    expect(within(overview).getByRole("link", { name: "CNT-2026-000776" })).toBeInTheDocument();
    expect(within(overview).getByText("Kịch bản siêu ngắn")).toBeInTheDocument();
    expect(within(overview).getByText("Dữ liệu cũ từ Nội dung")).toBeInTheDocument();
  });

  it("offers no delete on a manual row even to an owner - the server did not mark it", async () => {
    stubFetch(routes({ capabilities: OWNER, canDelete: true }));
    renderWithQuery(<WorkPage />);
    const node = await manualCard();
    expect(within(node).queryByText("Dữ liệu cũ từ Nội dung")).toBeNull();
    await userEvent.click(node);
    await screen.findByRole("region", { name: /Chi tiết Quay TVC/ });
    expect(deleteButton()).toBeNull();
  });
});

describe("11-12. TEAM_LEAD and EMPLOYEE see neither the marker nor the delete", () => {
  for (const [role, capabilities] of [
    ["TEAM_LEAD", LEAD],
    ["EMPLOYEE", EMPLOYEE],
  ] as const) {
    it(`${role}: nothing to press`, async () => {
      stubFetch(routes({ capabilities, canDelete: false }));
      renderWithQuery(<WorkPage />);
      const node = await legacyCard();
      expect(within(node).queryByText("Dữ liệu cũ từ Nội dung")).toBeNull();
      await userEvent.click(node);
      await screen.findByRole("region", { name: /Chi tiết/ });
      expect(deleteButton()).toBeNull();
      // The overview still says it is legacy data - that is a fact about the
      // row, not a control - and still names the person.
      const overview = screen.getByRole("region", { name: "Tổng quan công việc" });
      expect(within(overview).getByText("Dữ liệu cũ từ Nội dung")).toBeInTheDocument();
      expect(within(overview).getByText("Trần Minh Anh")).toBeInTheDocument();
    });
  }
});

describe("47. the confirmation says what will not happen", () => {
  it("names the row, the person and the source, and the five things left alone", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, canDelete: true }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));

    const box = dialog();
    expect(box.getByText("Xóa công việc?")).toBeInTheDocument();
    const text = screen.getByRole("dialog").textContent ?? "";
    expect(text).toContain("cơ chế Nội dung cũ");
    expect(text).toContain("WRK-2026-000059");
    expect(text).toContain("Nội dung: Một kiểu trưởng thành rất buồn");
    expect(text).toContain("Phụ trách: Trần Minh Anh");
    expect(text).toContain("Nguồn: CNT-2026-000776");
    expect(text).toContain("xóa công việc cũ này");
    expect(text).toContain("giữ nguyên Nội dung nguồn");
    expect(text).toContain("không tự tạo kết quả thay thế");
    expect(text).toContain("không tự chạy đồng bộ Nội dung");
    expect(text).toContain("không tự ánh xạ lại");
    expect(text).toContain("không xóa các công việc khác");
    expect(text).toContain("Đồng bộ dữ liệu công việc");
    expect(box.getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Xác nhận xóa" })).toBeInTheDocument();
    // Nothing was sent by opening the dialog, and nothing by cancelling it.
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
    await cancelDialog();
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).toBeNull();
    await legacyCard();
  });
});

describe("48-53. a confirmed delete removes the card and calls nothing else", () => {
  it("sends one DELETE, drops the row, clears ?item, and never syncs, rebuilds or projects", async () => {
    const fetchMock = stubFetch(routes({ capabilities: OWNER, canDelete: true }));
    afterDelete(fetchMock);
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    expect(NAV.current()).toContain(`item=${LEGACY}`);
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));
    await confirm();

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Một kiểu trưởng thành rất buồn/ })).toBeNull(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Đã xóa công việc cũ.");
    expect(NAV.current()).not.toContain("item=");
    expect(document.querySelectorAll('[id^="work-detail-"]')).toHaveLength(0);
    // The other row is exactly where it was.
    await manualCard();
    expect(reload).not.toHaveBeenCalled();

    const sent = calls(fetchMock);
    const deletes = sent.filter((one) => one.method === "DELETE");
    expect(deletes).toHaveLength(1);
    expect(deletes[0].url).toContain(`${MAINT}/items/${LEGACY}`);
    for (const forbidden of [
      "content-sync",
      "content-rebuild",
      "/reconcile",
      "/project",
      "/content/",
      "admin-remove",
      "cleanup-empty-containers",
    ]) {
      expect(sent.some((one) => one.url.includes(forbidden)), forbidden).toBe(false);
    }
    expect(sent.filter((one) => one.method !== "GET" && one.method !== "DELETE")).toHaveLength(0);
  });

  it("K. a shut month's refusal is worded as a delete and the row stays", async () => {
    stubFetch(
      routes({
        capabilities: OWNER,
        canDelete: true,
        deleteStatus: 409,
        deleteBody: {
          error: {
            code: "pr_conflict",
            message: "Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa.",
            details: {
              reason: "work_period_not_open_for_cleanup",
              operation: "legacy_delete",
              period: "2026-08",
              status: "CLOSED",
            },
          },
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    await userEvent.click(await screen.findByRole("button", { name: "Xóa công việc" }));
    await confirm();
    // The server's structured refusal, worded as a delete, on the panel.
    expect(
      await screen.findByText("Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    await legacyCard();
    expect(NAV.current()).toContain(`item=${LEGACY}`);
  });
});


describe("resync. a legacy row offers the sync as a second control, and the delete never syncs", () => {
  const PROJECT = `/api/pr/work/content/${CONTENT}/project`;
  const projection = (outcome: string) => ({
    content_id: CONTENT,
    content_code: "CNT-2026-000776",
    outcome,
    results: [],
  });

  it("draws Xóa công việc and Đồng bộ lại từ Nội dung as two buttons for an owner", async () => {
    stubFetch(routes({ capabilities: OWNER, canDelete: true }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    await screen.findByRole("region", { name: /Chi tiết/ });
    const sync = screen.getByRole("button", { name: "Đồng bộ lại từ Nội dung" });
    const remove = screen.getByRole("button", { name: "Xóa công việc" });
    expect(sync).not.toBe(remove);
  });

  it("offers no sync to a team lead", async () => {
    stubFetch(routes({ capabilities: LEAD, canDelete: false }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    await screen.findByRole("region", { name: /Chi tiết/ });
    expect(screen.queryByRole("button", { name: "Đồng bộ lại từ Nội dung" })).toBeNull();
  });

  it("clicking the sync posts to the single-source projection route and says what happened", async () => {
    const fetchMock = stubFetch([
      { match: PROJECT, method: "POST", body: projection("PROJECTED") },
      ...routes({ capabilities: OWNER, canDelete: true }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await legacyCard());
    await userEvent.click(await screen.findByRole("button", { name: "Đồng bộ lại từ Nội dung" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Đã đồng bộ công việc từ Nội dung.");
    const posts = calls(fetchMock).filter((one) => one.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain(PROJECT);
    expect(calls(fetchMock).filter((one) => one.method === "DELETE")).toHaveLength(0);
    // The row is still there: a sync deletes nothing.
    await legacyCard();
  });
});
