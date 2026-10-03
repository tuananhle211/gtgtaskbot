/**
 * M3 - work recorded automatically from content, and what the screen must say.
 *
 * Numbered 1-9 against the milestone's frontend requirement list.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Whether a row is source-derived is `is_source_derived` from the server;
 * whether it is waiting on somebody is the work `status`; the piece it came
 * from is `content_code`, resolved server-side. A test that passed because the
 * component matched on `"CONTENT"` and rendered its own Vietnamese would be
 * testing a second implementation of rules that live on the server.
 *
 * The three that carry the milestone
 * -----------------------------------
 *
 * **Test 2** - the employee is told, in words, that they do not have to file
 * this again. That sentence is the whole product reason M3 exists.
 * **Test 3** - a self-approved piece says *"Chờ xác nhận độc lập"* and not a
 * generic error: the content was accepted, and what is missing is a person.
 * **Test 5** - no edit control is drawn on source-derived work, whoever is
 * looking. The server refuses the same edits either way.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, renderWithQuery, serverWorkActions, stubFetch } from "./helpers";

/* `/pr/work` keeps its view and its selection in the URL, so it needs a router
   that really navigates - the same helper the ledger suite uses. */
const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const EMPLOYEE = ["PR_WORK_EXECUTE"];
const ADMIN = [
  "PR_WORK_EXECUTE",
  "PR_WORK_MANAGE",
  "PR_WORK_VALIDATE",
  "PR_WORK_CONFIGURE",
  "PR_WORK_VIEW_ALL",
];

const CONTENT_ID = "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa";
const TYPE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const HAO = "22222222-2222-2222-2222-222222222222";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const WORK_TYPE = {
  id: TYPE_ID,
  code: "SHORT_SCRIPT",
  name: "Kịch bản video ngắn",
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

const contribution = (over: Record<string, unknown> = {}) => ({
  id: "cccccccc-0000-0000-0000-000000000001",
  work_item_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  user_id: HAO,
  user_name: "Hảo",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-01T02:00:00Z",
  count_status: "COUNTED",
  count_status_label: "Đã ghi nhận",
  counted_at: "2026-09-01T02:00:00Z",
  excluded_reason: null,
  ...over,
});

/** One work item the projector wrote. */
const sourceItem = (over: Record<string, unknown> = {}) => ({
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  code: "WRK-2026-000009",
  title: "Nội dung: Bí quyết ngủ ngon",
  description: null,
  work_type_id: TYPE_ID,
  work_type_code: WORK_TYPE.code,
  work_type_name: WORK_TYPE.name,
  work_type_category: WORK_TYPE.category,
  source_type: "CONTENT",
  source_label: "Từ quy trình nội dung",
  status: "APPROVED",
  status_label: "Đã xác nhận",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: "1.00",
  unit: "ITEM",
  unit_label: "sản phẩm",
  due_at: null,
  is_overdue: false,
  created_by_user_id: HAO,
  assigned_by_user_id: null,
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: "2026-09-01T02:00:00Z",
  approved_at: "2026-09-01T02:00:00Z",
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  content_id: CONTENT_ID,
  content_code: "CNT-2026-000042",
  is_source_derived: true,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution()],
  ...over,
});

/** One work item a person filed. The control group for every test below. */
const manualItem = (over: Record<string, unknown> = {}) =>
  sourceItem({
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    code: "WRK-2026-000010",
    title: "Quay TVC Apexmed",
    source_type: "MANUAL",
    source_label: "Nhập thủ công",
    status: "ACCEPTED",
    status_label: "Được giao",
    approved_at: null,
    completed_at: null,
    content_id: null,
    content_code: null,
    is_source_derived: false,
    ...over,
  });

const detail = (item: Record<string, unknown>, over: Record<string, unknown> = {}) => {
  const flags = { can_manage: true, can_validate: true, can_execute: true, ...over };
  return {
    item,
    work_type: WORK_TYPE,
    evidence: [],
    content_code: (item.content_code as string | null) ?? null,
    ...flags,
    // The server's action contract for these flags and this status.
    ...serverWorkActions(item, flags),
  };
};

/**
 * Order matters: `stubFetch` takes the **first substring hit**, so every
 * literal path has to come before the bare `/api/pr/work` list route - which is
 * a prefix of all of them. `extra` is prepended for the same reason.
 */
const routes = (
  items: Array<Record<string, unknown>>,
  {
    capabilities = EMPLOYEE,
    itemDetail,
    rules = [],
  }: {
    capabilities?: string[];
    itemDetail?: Record<string, unknown>;
    rules?: Array<Record<string, unknown>>;
  } = {},
  extra: Array<{ match: string; method?: string; status?: number; body?: unknown }> = [],
) => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/content/rules", body: rules },
  { match: "/api/pr/work/periods", body: [] },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/history", body: [] },
  {
    match: `/api/pr/work/${items[0]?.id ?? "none"}`,
    body: itemDetail ?? detail(items[0] ?? sourceItem()),
  },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-16T00:00:00Z",
  created: 1,
  accepted: 1,
  completed: 1,
  approved: 1,
  counted_work_items: 1,
  counted_contributions: 1,
  open: 0,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
});

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-2: THE EMPLOYEE SEES WHERE IT CAME FROM -----------------------------

describe("1. a source-derived card names the content it came from", () => {
  it("shows the content code rather than a generic source label", async () => {
    stubFetch(routes([sourceItem()]));
    renderWithQuery(<WorkPage />);

    const card = (await screen.findByText("Nội dung: Bí quyết ngủ ngon")).closest("button")!;
    // "CNT-2026-000042" is a job somebody remembers doing. "Từ quy trình nội
    // dung" alone is not.
    expect(card.textContent).toContain("Nguồn: CNT-2026-000042");
  });

  it("leaves a manual card saying what it always said", async () => {
    stubFetch(routes([manualItem()]));
    renderWithQuery(<WorkPage />);

    const card = (await screen.findByText("Quay TVC Apexmed")).closest("button")!;
    expect(card.textContent).toContain("Nhập thủ công");
    expect(card.textContent).not.toContain("Nguồn: CNT");
  });
});

describe("2. the employee is told they do not have to file it again", () => {
  /**
   * **The product reason M3 exists**, said in words on the screen where it
   * matters. Somebody who sees work they did not enter needs to know it is
   * theirs and that the system put it there.
   */
  it("says the work was recorded automatically, and links to the piece", async () => {
    stubFetch(routes([sourceItem()]));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Nội dung: Bí quyết ngủ ngon"));

    expect(await screen.findByText(/ghi nhận tự động/)).toBeInTheDocument();
    expect(screen.getByText(/Bạn không cần nhập lại/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "CNT-2026-000042" });
    expect(link).toHaveAttribute("href", `/pr/content/${CONTENT_ID}`);
  });
});

// --- 3-4: SELF-APPROVED WORK -----------------------------------------------

describe("3. a self-approved piece says what it is waiting for", () => {
  /**
   * Not a generic error, and not silence. The content workflow accepted the
   * piece; what is missing is somebody other than the writer confirming the
   * *work*, and the sentence has to send the reader to a person rather than
   * suggest something went wrong.
   *
   * **M3.1 widened the wording.** `COMPLETED` now has two causes - a script
   * whose approver was its writer, and an editor's cut that simply has not been
   * reviewed yet - so the sentence no longer asserts the self-approval one,
   * which would be wrong half the time. What it still has to do, and what this
   * test pins, is name the missing *person*.
   */
  it("shows the pending-validation sentence and offers the validate control", async () => {
    const pending = sourceItem({
      status: "COMPLETED",
      status_label: "Chờ xác nhận",
      approved_at: null,
      contributors: [
        contribution({
          count_status: "PENDING",
          count_status_label: "Chưa ghi nhận",
          counted_at: null,
        }),
      ],
    });
    stubFetch(routes([pending], { itemDetail: detail(pending) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Nội dung: Bí quyết ngủ ngon"));

    expect(await screen.findByText(/Chờ xác nhận độc lập/)).toBeInTheDocument();
    expect(
      screen.getByText(/một người khác — không phải\s+người thực hiện/),
    ).toBeInTheDocument();
    // The one manual action source-derived work offers is still there: the
    // source has no opinion about whether somebody else has checked it.
    expect(
      screen.getByRole("button", { name: "Xác nhận hoàn thành" }),
    ).toBeInTheDocument();
  });

  it("never renders it as a failure", async () => {
    const pending = sourceItem({
      status: "COMPLETED",
      status_label: "Chờ xác nhận",
      approved_at: null,
      contributors: [contribution({ count_status: "PENDING", counted_at: null })],
    });
    stubFetch(routes([pending], { itemDetail: detail(pending) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Nội dung: Bí quyết ngủ ngon"));
    await screen.findByText(/Chờ xác nhận độc lập/);

    const rendered = document.body.textContent ?? "";
    for (const forbidden of ["Lỗi", "Thất bại", "Không hợp lệ", "Bị từ chối"]) {
      expect(rendered, forbidden).not.toContain(forbidden);
    }
  });
});

// --- 5-6: SOURCE-DERIVED WORK IS NOT EDITABLE ------------------------------

describe("5. no edit control is drawn on source-derived work", () => {
  /**
   * **Whoever is looking** - this actor is an admin with every capability. The
   * rule is not a permission level; it is *whose fact it is*, and the server
   * refuses the same edits either way.
   */
  it("hides cancel, evidence and the lifecycle buttons", async () => {
    const pending = sourceItem({
      status: "COMPLETED",
      status_label: "Chờ xác nhận",
      approved_at: null,
      contributors: [contribution({ count_status: "PENDING", counted_at: null })],
    });
    stubFetch(
      routes([pending], { capabilities: ADMIN, itemDetail: detail(pending) }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Nội dung: Bí quyết ngủ ngon"));
    await screen.findByText(/ghi nhận tự động/);

    for (const gone of ["Hủy", "Thêm minh chứng", "Bắt đầu", "Hoàn thành"]) {
      expect(screen.queryByRole("button", { name: gone }), gone).not.toBeInTheDocument();
    }
    // Nor the evidence field itself: source-derived work has no evidence to
    // type, and the textarea is the edit control now.
    expect(screen.queryByRole("textbox", { name: "Minh chứng" })).not.toBeInTheDocument();
  });

  it("keeps every one of them on a manual item", async () => {
    const manual = manualItem();
    stubFetch(routes([manual], { capabilities: ADMIN, itemDetail: detail(manual) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByText("Người thực hiện");

    // The control group: M1's board is untouched for work a person filed. With
    // no evidence yet the textarea is offered directly - see the Evidence panel.
    expect(screen.getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Minh chứng" })).toBeInTheDocument();
  });
});

// --- 7-8: THE MAPPING ADMIN -------------------------------------------------

describe("7. the mapping panel configures the work type and nothing else", () => {
  /**
   * Which milestone counts as work, whose it is and when independent
   * validation is required are **not** on this screen. A dropdown for the
   * milestone would be a dropdown that turns the anti-gaming boundary off.
   */
  it("sends the kind, the content type and the work type", async () => {
    const fetchMock = stubFetch(
      routes([sourceItem()], { capabilities: ADMIN }, [
      {
        match: "/api/pr/work/content/rules",
        method: "PUT",
        body: {
          id: "11111111-1111-1111-1111-111111111111",
          contribution_kind: "CONTENT_CREATION",
          content_type: null,
          work_type_id: TYPE_ID,
          work_type_code: WORK_TYPE.code,
          work_type_name: WORK_TYPE.name,
          is_active: true,
          note: null,
          created_at: "2026-09-01T02:00:00Z",
        },
      },
      ]),
    );
    renderWithQuery(<WorkPage />);
    // M3.1 moved the mapping panel out of the KPI view and into *Cấu hình*,
    // beside the work types it selects from.
    await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
    await userEvent.click(await screen.findByText("Ánh xạ nội dung → công việc"));

    await userEvent.selectOptions(
      await screen.findByLabelText("Loại công việc tương ứng"),
      TYPE_ID,
    );
    await userEvent.click(screen.getByRole("button", { name: "Lưu ánh xạ" }));

    const calls = (fetchMock as unknown as {
      calls: Array<{ url: string; method: string; body: Record<string, unknown> }>;
    }).calls;
    const sent = calls.find((call) => call.method === "PUT" && call.url.includes("/rules"));
    expect(sent?.body).toEqual({
      contribution_kind: "CONTENT_CREATION",
      content_type: null,
      work_type_id: TYPE_ID,
    });
    // No milestone, no contributor, no timestamp - the three things a client
    // must never be able to suggest.
    for (const forbidden of ["milestone", "contributor_user_id", "counted_at", "occurred_at"]) {
      expect(sent?.body, forbidden).not.toHaveProperty(forbidden);
    }
  });
});

describe("8. a missing mapping is shown as missing", () => {
  it("says the first approved piece of each type will create one", async () => {
    stubFetch(routes([sourceItem()], { capabilities: ADMIN }));
    renderWithQuery(<WorkPage />);
    // M3.1 moved the mapping panel out of the KPI view and into *Cấu hình*,
    // beside the work types it selects from.
    await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
    await userEvent.click(await screen.findByText("Ánh xạ nội dung → công việc"));

    // Since auto-provisioning, an empty panel is no longer "nothing is being
    // recorded": the first approved piece of each content type writes its own
    // row here. The panel says that rather than raising a false alarm.
    expect(await screen.findByText(/Chưa có ánh xạ nào/)).toBeInTheDocument();
    expect(screen.getByText(/sẽ tự tạo ánh xạ ở đây/)).toBeInTheDocument();
    expect(screen.queryByText(/chưa có công việc nào được ghi nhận/)).not.toBeInTheDocument();
  });

  it("badges a rule the projector wrote, beside one a person wrote", async () => {
    const human = {
      id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      contribution_kind: "CONTENT_CREATION",
      content_type: null,
      work_type_id: TYPE_ID,
      work_type_code: WORK_TYPE.code,
      work_type_name: WORK_TYPE.name,
      is_active: true,
      note: null,
      created_at: "2026-09-01T02:00:00Z",
      auto_provisioned: false,
    };
    const system = {
      ...human,
      id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      content_type: "LONG_YOUTUBE_SCRIPT",
      work_type_code: "CONTENT_AUTO_CONTENT_CREATION_LONG_YOUTUBE_SCRIPT",
      work_type_name: "Kịch bản YouTube dài",
      auto_provisioned: true,
    };
    stubFetch(routes([sourceItem()], { capabilities: ADMIN, rules: [human, system] }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
    await userEvent.click(await screen.findByText("Ánh xạ nội dung → công việc"));

    const auto = (await screen.findAllByText("Kịch bản YouTube dài"))
      .map((node) => node.closest("li"))
      .find((node) => node !== null)!;
    expect(within(auto).getByText("Hệ thống tự tạo")).toBeInTheDocument();
    const manual = screen
      .getAllByText("Mặc định (mọi loại nội dung)")
      .map((node) => node.closest("li"))
      .find((node) => node !== null)!;
    expect(within(manual).queryByText("Hệ thống tự tạo")).not.toBeInTheDocument();
  });
});

// --- 9: NO POINTS, STILL ----------------------------------------------------

describe("9. automatic work recording introduces no score", () => {
  it("shows counted work and never a point or a rate", async () => {
    stubFetch(routes([sourceItem()]));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Nội dung: Bí quyết ngủ ngon"));
    await screen.findByText("Người thực hiện");

    const rendered = document.body.textContent ?? "";
    expect(rendered).toContain("Đã ghi nhận");
    for (const forbidden of ["điểm", "Điểm", "hệ số", "thưởng"]) {
      expect(rendered, forbidden).not.toContain(forbidden);
    }
  });

  it("keeps the API client's source fields free of eligibility", () => {
    // Structural: a `quota_status` on the work item would invite a screen to
    // read M3's output as M2's answer.
    const source = read("lib/api.ts");
    const start = source.indexOf("export interface ContentWorkRule");
    const end = source.indexOf("export interface ReconcileContentWorkOutcome");
    const block = source.slice(start, end);
    for (const forbidden of ["quota_status", "eligible_amount", "score", "points"]) {
      expect(block, forbidden).not.toContain(forbidden);
    }
  });
});

/** Read one frontend source file, for the structural assertion above. */
function read(relative: string): string {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  return fs.readFileSync(path.join(process.cwd(), "src", relative), "utf8");
}
