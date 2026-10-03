/**
 * The Work detail draws its lifecycle buttons from the server's action
 * contract, and from nothing else.
 *
 * The bug: a REJECTED row's detail offered *Hủy*, the server refused it with
 * "Cannot move work from 'REJECTED' to 'CANCELLED'", and the person read that
 * sentence. The screen had inferred the button from `status` - a second copy
 * of the state machine, already stale. Now the detail carries `can_accept` …
 * `can_cancel`, resolved on the server, and this file holds the page to them:
 *
 * 1-2. REJECTED and CANCELLED details show no *Hủy* - the server says
 *      `can_cancel: false`, and the status label is what it was;
 * 3.   a cancellable status still shows *Hủy* when the server says so;
 * 4-5. the page obeys the flag, not the status: an ACCEPTED row whose server
 *      answer says `can_cancel: false` gets no *Hủy*, and a REJECTED row whose
 *      (contradictory) answer says `can_cancel: true` gets one - which is the
 *      proof that no local status list survives. The same for every other
 *      button: all seven off on a live ACCEPTED row draws nothing;
 * 6-7. a direct cancel the server refuses with `illegal_transition` is shown
 *      as a Vietnamese business sentence, never the raw English;
 * 8.   a CANCELLED row's safe delete is unaffected;
 * 9.   the REJECTED status label remains "Không được chấp nhận".
 *
 * Nothing here contacts a network.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, renderWithQuery, SESSION, stubFetch } from "./helpers";
import { errorMessage } from "@/lib/labels";

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
const PERIOD = "66666666-6666-6666-6666-666666666666";
const TYPE = "11111111-1111-1111-1111-111111111111";
const LINH = "22222222-2222-2222-2222-222222222222";
const ITEM = "abababab-abab-abab-abab-abababababab";

const WORK_TYPE = {
  id: TYPE,
  code: "SCRIPT",
  name: "Kịch bản video",
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

const item = (over: Record<string, unknown> = {}) => ({
  id: ITEM,
  code: "WRK-2026-000021",
  title: "Kịch bản video gỡ rối",
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
  created_by_user_id: LINH,
  assigned_by_user_id: null,
  assigned_at: "2026-09-10T03:25:00Z",
  accepted_at: null,
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
  contributors: [
    {
      id: "abababab-0000-0000-0000-000000000001",
      work_item_id: ITEM,
      user_id: LINH,
      user_name: "Huyền Linh",
      contribution_role: "PRIMARY",
      contribution_role_label: "Phụ trách chính",
      credit_weight: "1.0000",
      assigned_at: "2026-09-10T03:25:00Z",
      count_status: "PENDING",
      count_status_label: "Chờ xác nhận",
      counted_at: null,
      excluded_reason: null,
    },
  ],
  ...over,
});

const rejected = () =>
  item({
    status: "REJECTED",
    status_label: "Không được chấp nhận",
    cancelled_at: "2026-09-11T02:00:00Z",
    cancel_reason: "Không phù hợp",
  });
const cancelled = () =>
  item({ status: "CANCELLED", status_label: "Đã hủy", cancelled_at: "2026-09-11T02:00:00Z" });

const NO_ACTIONS = {
  can_accept: false,
  can_reject: false,
  can_start: false,
  can_complete: false,
  can_approve: false,
  can_reopen: false,
  can_cancel: false,
};

/** The server's answer for one row: coarse flags, the action contract, the delete rules. */
const detail = (row: Record<string, unknown>, actions: Record<string, boolean> = {}) => ({
  item: row,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: null,
  can_manage: true,
  can_validate: true,
  can_execute: true,
  results: [],
  is_subject: false,
  can_report_result: false,
  can_validate_results: false,
  can_delete_legacy: false,
  can_admin_delete: false,
  admin_delete: null,
  ...NO_ACTIONS,
  ...actions,
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

const routes = (row: Record<string, unknown>, body: unknown, extra: unknown[] = []) => [
  ...(extra as Array<{ match: string; method?: string; status?: number; body?: unknown }>),
  {
    match: "/api/pr/dashboard",
    body: {
      stage_counts: [],
      awaiting_my_review: [],
      my_capabilities: OWNER,
      recent_content: [],
      overdue_tasks: [],
    },
  },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  { match: `/api/pr/work/${ITEM}`, method: "GET", body },
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
  { match: "/api/pr/work", body: { items: [row], total: 1, limit: 50, offset: 0 } },
];

const LIFECYCLE = [
  "Chấp nhận",
  "Từ chối",
  "Bắt đầu",
  "Hoàn thành",
  "Xác nhận hoàn thành",
  "Trả lại",
  "Hủy",
];

async function open(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: /WRK-2026-000021/ }));
  return screen.findByRole("region", { name: /Chi tiết Kịch bản video gỡ rối/ });
}

const button = (region: HTMLElement, name: string) =>
  within(region).queryByRole("button", { name });

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

describe("1-2, 9. terminal rows offer no Hủy, and say what they are", () => {
  it("REJECTED: the label, the person, the history region - and no Hủy", async () => {
    stubFetch(routes(rejected(), detail(rejected())));
    renderWithQuery(<WorkPage />);
    const region = await open();
    expect(screen.getAllByText("Không được chấp nhận").length).toBeGreaterThan(0);
    const overview = within(region).getByRole("region", { name: "Tổng quan công việc" });
    expect(within(overview).getByText("Huyền Linh")).toBeInTheDocument();
    for (const name of LIFECYCLE) expect(button(region, name), name).toBeNull();
    // Reject is not cancel is not delete: no delete either, the server said none.
    expect(button(region, "Xóa công việc")).toBeNull();
    expect(within(region).queryByTestId("admin-delete-blocked")).toBeNull();
  });

  it("CANCELLED: Đã hủy and no Hủy", async () => {
    stubFetch(routes(cancelled(), detail(cancelled())));
    NAV.arriveAt("/pr/work?status=CANCELLED");
    renderWithQuery(<WorkPage />);
    const region = await open();
    expect(screen.getAllByText("Đã hủy").length).toBeGreaterThan(0);
    for (const name of LIFECYCLE) expect(button(region, name), name).toBeNull();
  });
});

describe("3-5. the page obeys the server's flag, not the status", () => {
  it("draws Hủy on a cancellable row when the server says can_cancel", async () => {
    stubFetch(
      routes(item(), detail(item(), { can_start: true, can_complete: true, can_cancel: true })),
    );
    renderWithQuery(<WorkPage />);
    const region = await open();
    expect(button(region, "Hủy")).toBeInTheDocument();
    expect(button(region, "Bắt đầu")).toBeInTheDocument();
    expect(button(region, "Hoàn thành")).toBeInTheDocument();
    for (const name of ["Chấp nhận", "Từ chối", "Xác nhận hoàn thành", "Trả lại"]) {
      expect(button(region, name), name).toBeNull();
    }
  });

  it("draws nothing on a live ACCEPTED row when every flag is false", async () => {
    // The status alone used to be enough to draw Bắt đầu, Hoàn thành and Hủy.
    stubFetch(routes(item(), detail(item())));
    renderWithQuery(<WorkPage />);
    const region = await open();
    for (const name of LIFECYCLE) expect(button(region, name), name).toBeNull();
  });

  it("draws exactly the flagged buttons, whatever the status says", async () => {
    // A contradictory answer on purpose: the page has no opinion of its own.
    stubFetch(
      routes(
        rejected(),
        detail(rejected(), { can_cancel: true, can_approve: true, can_accept: true }),
      ),
    );
    renderWithQuery(<WorkPage />);
    const region = await open();
    expect(button(region, "Hủy")).toBeInTheDocument();
    expect(button(region, "Xác nhận hoàn thành")).toBeInTheDocument();
    expect(button(region, "Chấp nhận")).toBeInTheDocument();
    for (const name of ["Từ chối", "Bắt đầu", "Hoàn thành", "Trả lại"]) {
      expect(button(region, name), name).toBeNull();
    }
  });

  it("keeps no status-based list of its own in the page source", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const source = fs.readFileSync(path.join(process.cwd(), "src/app/pr/work/page.tsx"), "utf8");
    // Every lifecycle button reads a `can_*` flag; none of them reads a status.
    for (const flag of Object.keys(NO_ACTIONS)) expect(source).toContain(`data.${flag}`);
    expect(source).not.toMatch(/item\.status !== "CANCELLED"/);
    expect(source).not.toMatch(/item\.status !== "APPROVED" &&/);
    expect(source).not.toMatch(/can_execute &&\s*!item\.is_source_derived/);
  });
});

describe("6-7. a refused direct cancel is a Vietnamese sentence, not the raw English", () => {
  it("shows the business message from the structured illegal_transition reason", async () => {
    // A stale panel: the server said yes a minute ago and the row was rejected since.
    stubFetch(
      routes(item(), detail(item(), { can_cancel: true }), [
        {
          match: `/api/pr/work/${ITEM}/cancel`,
          method: "POST",
          status: 422,
          body: {
            error: {
              code: "validation_error",
              message: "Cannot move work from 'REJECTED' to 'CANCELLED'",
              details: {
                field: "status",
                reason: "illegal_transition",
                current: "REJECTED",
                target: "CANCELLED",
                allowed: [],
              },
            },
          },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    const region = await open();
    await userEvent.click(button(region, "Hủy")!);
    await confirm();
    expect(
      await screen.findByText("Công việc đã bị từ chối nên không thể hủy."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Cannot move work/)).toBeNull();
  });

  it("words every illegal_transition edge, and leaves other reasons to the server", () => {
    const at = (current: string, target: string) =>
      errorMessage("validation_error", { reason: "illegal_transition", current, target });
    expect(at("REJECTED", "CANCELLED")).toBe("Công việc đã bị từ chối nên không thể hủy.");
    expect(at("CANCELLED", "CANCELLED")).toBe("Công việc này đã được hủy trước đó.");
    expect(at("APPROVED", "CANCELLED")).toBe("Công việc đã được xác nhận nên không hủy được.");
    expect(at("PROPOSED", "COMPLETED")).toBe(
      "Không thể hoàn thành công việc ở trạng thái hiện tại.",
    );
    expect(at("REJECTED", "ACCEPTED")).toBe("Không thể chấp nhận công việc ở trạng thái hiện tại.");
    expect(at("REJECTED", "APPROVED")).toBe(
      "Không thể xác nhận hoàn thành công việc ở trạng thái hiện tại.",
    );
    expect(errorMessage("validation_error", { reason: "illegal_transition" })).toBe(
      "Không thể thực hiện thao tác này ở trạng thái hiện tại của công việc.",
    );
    // An unexpected failure is not masked: no reason, no sentence of ours.
    expect(errorMessage("internal_error", {})).toBeNull();
  });
});

describe("8. the cancelled row's safe delete is a different rule and still works", () => {
  it("offers Xóa công việc from can_admin_delete while offering no Hủy", async () => {
    stubFetch(
      routes(
        cancelled(),
        detail(cancelled(), {}) && {
          ...detail(cancelled()),
          can_admin_delete: true,
          admin_delete: {
            rule: "terminal",
            deletable: true,
            reason: null,
            cause: null,
            blocking: {
              results: 0,
              counted_contributions: 0,
              quota_allocations: 0,
              score_allocations: 0,
            },
            period_code: "2026-09",
            message: null,
            previous_status: "CANCELLED",
          },
        },
      ),
    );
    NAV.arriveAt("/pr/work?status=CANCELLED");
    renderWithQuery(<WorkPage />);
    const region = await open();
    expect(button(region, "Hủy")).toBeNull();
    expect(button(region, "Xóa công việc")).toBeInTheDocument();
  });
});
