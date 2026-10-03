/**
 * M1 - Công việc, and what the screen must never let somebody believe.
 *
 * Numbered 1-14 against the milestone's frontend requirement list.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Which words appear are `*_label` fields from the server; whether a control is
 * drawn is a `can_*` flag the server computed from the same checks the writes
 * make; whether a row is overdue is `is_overdue`, computed server-side. A test
 * that passed because the component matched on `"APPROVED"` and rendered its own
 * Vietnamese would be testing a second implementation of rules that live on the
 * server.
 *
 * The three that carry the milestone
 * -----------------------------------
 *
 * **Test 7** - completed work says, in words, that it is not counted yet.
 * **Test 8** - a contributor gets no validate button *and* the backend stays
 * authoritative: the same person calling the route is refused.
 * **Test 10** - selecting a period does not remove overdue work from the screen.
 *
 * And **test 14**: no score, anywhere. Not in the summary strip, not on a card,
 * not on a contribution. `COUNTED` is as far as M1 goes.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  SESSION,
  cancelDialog,
  channelsNavigation,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
  serverWorkActions,
} from "./helpers";

/* `/pr/work` keeps its tab and its selection in the URL, so it needs a router
   that really navigates - the same helper the channel suites use. */
const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const MANAGER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const WORK_TYPE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "HALF_DAY_SHOOT",
  name: "Quay nửa buổi",
  category: "PRODUCTION",
  category_label: "Sản xuất",
  description: null,
  default_unit: "SESSION",
  default_unit_label: "buổi",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const HAO = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";

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
  title: "Quay TVC Apexmed",
  description: null,
  work_type_id: WORK_TYPE.id,
  work_type_code: WORK_TYPE.code,
  work_type_name: WORK_TYPE.name,
  work_type_category: WORK_TYPE.category,
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  status: "ACCEPTED",
  status_label: "Được giao",
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
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution()],
  ...over,
});

const detail = (item: Record<string, unknown>, over: Record<string, unknown> = {}) => {
  const flags = { can_manage: false, can_validate: false, can_execute: true, ...over };
  return {
    item,
    work_type: WORK_TYPE,
    evidence: [],
    ...flags,
    // The server's action contract, as the server would resolve it for these
    // flags and this status. See `serverWorkActions`.
    ...serverWorkActions(item, flags),
  };
};

const summary = (over: Record<string, unknown> = {}) => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-16T00:00:00Z",
  created: 3,
  accepted: 3,
  completed: 1,
  approved: 1,
  counted_work_items: 1,
  counted_contributions: 1,
  open: 2,
  in_progress: 1,
  awaiting_validation: 1,
  proposed: 0,
  overdue: 0,
  ...over,
});

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" },
];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  items: Array<Record<string, unknown>>,
  {
    capabilities = EMPLOYEE,
    itemDetail,
    stats = summary(),
  }: {
    capabilities?: string[];
    itemDetail?: Record<string, unknown>;
    stats?: Record<string, unknown>;
  } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: stats },
  // Before the bare list route, whose match is a prefix of both.
  { match: "/history", body: [] },
  {
    match: `/api/pr/work/${items[0]?.id ?? "none"}`,
    body: itemDetail ?? detail(items[0] ?? workItem()),
  },
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

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-3: THE LIST ---------------------------------------------------------

describe("1. My Work renders", () => {
  it("shows the work with the server's own words for every state", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText("Quay TVC Apexmed")).toBeInTheDocument();
    // The status word is the server's `status_label`, not a table in the browser.
    expect(screen.getAllByText("Được giao").length).toBeGreaterThan(0);
    expect(screen.getByText("Quay nửa buổi")).toBeInTheDocument();
    expect(screen.getByText("WRK-2026-000001")).toBeInTheDocument();
  });
});

describe("2. the period presets are all offered", () => {
  it("offers the day slices inside the selected month, month-first", async () => {
    // **Post-M4.** "Tháng này" is gone and "Tất cả tháng" leads, because the
    // month stopped being one preset among several and became the screen's
    // outer scope: the period selector picks it, and these narrow it. A
    // "Tháng này" button beside a September period selector would be two
    // controls for one thing, disagreeing whenever the selected month was not
    // the current one.
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");

    for (const label of ["Tất cả tháng", "Hôm nay", "Hôm qua", "Tuần này", "Sắp tới", "Nợ việc"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Tháng này" })).toBeNull();
  });

  it("asks the server for the chosen preset rather than filtering here", async () => {
    const fetchMock = stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");

    await userEvent.click(screen.getByRole("button", { name: "Tuần này" }));
    const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
    // Which statuses count as late, and where a Vietnamese week starts, are the
    // server's business - a browser deciding either would eventually disagree
    // with the summary about the same rows.
    expect(calls.some((call) => call.url.includes("preset=WEEK"))).toBe(true);
  });
});

describe("3. the summary shows five figures, not one count", () => {
  it("renders assigned, in progress, awaiting validation, recorded and overdue", async () => {
    stubFetch(routes([workItem()], { stats: summary({ accepted: 7, overdue: 4 }) }));
    renderWithQuery(<WorkPage />);

    const strip = await screen.findByRole("region", { name: "Tổng hợp công việc" });
    for (const label of ["Được giao", "Đang làm", "Chờ xác nhận", "Đã ghi nhận", "Nợ việc"]) {
      expect(within(strip).getByText(label)).toBeInTheDocument();
    }
    expect(within(strip).getByText("7")).toBeInTheDocument();
    expect(within(strip).getByText("4")).toBeInTheDocument();
  });

  it("marks which figures ignore the selected period", async () => {
    stubFetch(routes([workItem()]));
    renderWithQuery(<WorkPage />);
    const strip = await screen.findByRole("region", { name: "Tổng hợp công việc" });
    // Three of the five describe now. Somebody who could not tell would read
    // "Nợ việc: 4" as "four this month" - the misreading that makes carried-over
    // work invisible.
    expect(within(strip).getAllByText(/hiện tại/)).toHaveLength(3);
  });
});

// --- 4-6: PROPOSING AND ASSIGNING ------------------------------------------

describe("4. an employee can propose work, and is told it does not count yet", () => {
  it("posts to the proposal route and says a manager must accept", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/work/proposals",
        method: "POST",
        status: 201,
        body: detail(workItem({ status: "PROPOSED", status_label: "Chờ duyệt đề xuất" })),
      },
      ...routes([workItem()]),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Đề xuất công việc" }));

    expect(
      screen.getByText(/Đề xuất chưa tính là công việc được giao/),
    ).toBeInTheDocument();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Loại công việc" }),
      WORK_TYPE.id,
    );
    await userEvent.type(screen.getByRole("textbox", { name: "Tên công việc" }), "Seeding");
    await userEvent.click(screen.getByRole("button", { name: "Gửi đề xuất" }));

    const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> })
      .calls;
    expect(
      calls.some((call) => call.method === "POST" && call.url.includes("/work/proposals")),
    ).toBe(true);
  });
});

describe("5. only a manager is offered assignment", () => {
  it("hides the control from an employee and shows it to a manager", async () => {
    stubFetch(routes([workItem()], { capabilities: EMPLOYEE }));
    const first = renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");
    expect(
      screen.queryByRole("button", { name: "Giao công việc" }),
    ).not.toBeInTheDocument();
    first.unmount();

    stubFetch(routes([workItem()], { capabilities: MANAGER }));
    renderWithQuery(<WorkPage />);
    expect(
      await screen.findByRole("button", { name: "Giao công việc" }),
    ).toBeInTheDocument();
  });
});

describe("6. a manager accepts somebody else's proposal", () => {
  it("asks first, then posts to the accept route", async () => {
    const proposal = workItem({
      status: "PROPOSED",
      status_label: "Chờ duyệt đề xuất",
    });
    const fetchMock = stubFetch([
      {
        match: "/accept",
        method: "POST",
        body: detail(workItem({ status: "ACCEPTED" }), { can_manage: true }),
      },
      ...routes([proposal], {
        capabilities: MANAGER,
        itemDetail: detail(proposal, { can_manage: true }),
      }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    // The proposal says plainly that it is not yet anybody's work.
    expect(
      await screen.findByText(/Chưa tính là công việc được giao/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Chấp nhận" }));
    expect(dialog().getByText("Chấp nhận đề xuất công việc này?")).toBeInTheDocument();
    await confirm();

    const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> })
      .calls;
    expect(calls.some((call) => call.method === "POST" && call.url.includes("/accept"))).toBe(
      true,
    );
  });
});

// --- 7-9: THE BOUNDARY -----------------------------------------------------

describe("7. completing work says it is not counted yet", () => {
  it("shows the awaiting-validation sentence and the server's label", async () => {
    const done = workItem({ status: "COMPLETED", status_label: "Chờ xác nhận" });
    stubFetch(routes([done], { itemDetail: detail(done) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    // The status word itself carries it - "Chờ xác nhận", never "Đã xong".
    expect(await screen.findAllByText("Chờ xác nhận")).not.toHaveLength(0);
    expect(
      screen.getByText(/chưa được ghi nhận/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/một người không tham gia công việc này xác nhận/),
    ).toBeInTheDocument();
  });

  it("asks before completing, and says what completing does not do", async () => {
    const mine = workItem({ status: "IN_PROGRESS", status_label: "Đang làm" });
    stubFetch([
      { match: "/complete", method: "POST", body: detail(workItem({ status: "COMPLETED" })) },
      ...routes([mine], { itemDetail: detail(mine) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Hoàn thành" }));
    expect(
      dialog().getByText(/chỉ được ghi nhận sau khi người khác xác nhận/),
    ).toBeInTheDocument();
  });
});

describe("8. a contributor is offered no validation, and the backend is authoritative", () => {
  it("draws no validate button when the server says can_validate is false", async () => {
    const done = workItem({ status: "COMPLETED", status_label: "Chờ xác nhận" });
    stubFetch(
      routes([done], {
        capabilities: MANAGER,
        // The server's answer for somebody who contributed to this work: they
        // hold PR_WORK_VALIDATE and still may not use it here.
        itemDetail: detail(done, { can_manage: true, can_validate: false }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByText(/chưa được ghi nhận/);

    expect(
      screen.queryByRole("button", { name: "Xác nhận hoàn thành" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces the server's refusal when the route is called anyway", async () => {
    const done = workItem({ status: "COMPLETED", status_label: "Chờ xác nhận" });
    const fetchMock = stubFetch([
      {
        match: "/approve",
        method: "POST",
        status: 403,
        body: {
          error: {
            code: "pr.permission_denied",
            message:
              "Bạn có tham gia công việc này nên không thể tự xác nhận. " +
              "Cần một người khác xác nhận để công việc được ghi nhận.",
            details: { reason: "self_validation" },
          },
        },
      },
      ...routes([done], {
        capabilities: MANAGER,
        // A stale screen: the server said yes a minute ago and says no now.
        // Hiding the button is a courtesy; this is the rule.
        itemDetail: detail(done, { can_manage: true, can_validate: true }),
      }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Xác nhận hoàn thành" }));
    await confirm();
    // The dialog stays open on a failure and shows the server's own sentence.
    // Asserted on the body text rather than by node, because the message is
    // rendered inside the dialog beside the 403's standing "quyền" note.
    // The server's own sentence, on the panel. Nothing was counted.
    expect(await screen.findByText(/không thể tự xác nhận/)).toBeInTheDocument();
    expect(screen.getByText("Chưa ghi nhận")).toBeInTheDocument();
    expect(
      (fetchMock as unknown as { calls: Array<{ url: string; method: string }> }).calls.some(
        (call) => call.method === "POST" && call.url.includes("/approve"),
      ),
    ).toBe(true);
  });
});

describe("9. an independent validator approves, and is told what it does", () => {
  it("names how many people will be credited before the click", async () => {
    const done = workItem({
      status: "COMPLETED",
      status_label: "Chờ xác nhận",
      contributors: [
        contribution(),
        contribution({ id: "aaaaaaaa-0000-0000-0000-000000000002", user_id: LINH, user_name: "Linh" }),
      ],
    });
    stubFetch([
      { match: "/approve", method: "POST", body: detail(workItem({ status: "APPROVED" })) },
      ...routes([done], {
        capabilities: MANAGER,
        itemDetail: detail(done, { can_manage: true, can_validate: true, can_execute: false }),
      }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Xác nhận hoàn thành" }));
    // Two contributors, and the dialog says so: this click credits two people.
    expect(dialog().getByText(/cả 2 người thực hiện/)).toBeInTheDocument();
    await confirm();
  });
});

// --- 10: CARRY-OVER --------------------------------------------------------

describe("10. overdue is independent of the period", () => {
  it("keeps the overdue figure while a month is selected", async () => {
    // Nothing achieved this month, and four items still outstanding from
    // before it. Both are true at once, and the screen has to show both.
    stubFetch(
      routes([workItem()], {
        stats: summary({ approved: 0, counted_contributions: 0, overdue: 4 }),
      }),
    );
    renderWithQuery(<WorkPage />);
    const strip = await screen.findByRole("region", { name: "Tổng hợp công việc" });

    const overdue = within(strip).getByText("Nợ việc").closest("div") as HTMLElement;
    expect(within(overdue).getByText("4")).toBeInTheDocument();
    const recorded = within(strip).getByText("Đã ghi nhận").closest("div") as HTMLElement;
    expect(within(recorded).getByText("0")).toBeInTheDocument();
  });

  it("sends no date bounds with the overdue preset", async () => {
    const late = workItem({ is_overdue: true, due_at: "2026-06-20T09:00:00Z" });
    const fetchMock = stubFetch(routes([late]));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");

    await userEvent.click(screen.getByRole("button", { name: "Nợ việc" }));
    const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
    const overdue = calls.filter((call) => call.url.includes("preset=OVERDUE"));
    expect(overdue.length).toBeGreaterThan(0);
    // A status preset carries no dates. June's debt cannot be filtered out of
    // a request that never mentions a month.
    for (const call of overdue) {
      expect(call.url).not.toContain("date_from");
    }
  });

  it("marks a late row from the server's flag, not from the browser's clock", async () => {
    const late = workItem({ is_overdue: true, due_at: "2026-06-20T09:00:00Z" });
    stubFetch(routes([late], { itemDetail: detail(late) }));
    renderWithQuery(<WorkPage />);
    expect(await screen.findByText("Quá hạn")).toBeInTheDocument();
  });
});

// --- 11-13: QUANTITY, PEOPLE, EVIDENCE -------------------------------------

describe("11. a quantity renders with the unit the work type chose", () => {
  it("shows one row for a hundred comments, not a hundred rows", async () => {
    const seeding = workItem({
      title: "Seeding nhóm kín",
      quantity: "100.00",
      unit: "COMMENT",
      unit_label: "bình luận",
    });
    stubFetch(routes([seeding], { itemDetail: detail(seeding) }));
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText("Seeding nhóm kín")).toBeInTheDocument();
    expect(screen.getAllByText("100 bình luận").length).toBeGreaterThan(0);
    // One card. The shape is the work type's decision, not the filer's.
    expect(screen.getAllByText("Seeding nhóm kín")).toHaveLength(1);
  });
});

describe("12. every contributor is listed with their own credit status", () => {
  it("shows three people and three statuses, never a divided total", async () => {
    const shoot = workItem({
      contributors: [
        contribution({ count_status: "COUNTED", count_status_label: "Đã ghi nhận", counted_at: "2026-09-10T02:00:00Z" }),
        contribution({
          id: "aaaaaaaa-0000-0000-0000-000000000002",
          user_id: LINH,
          user_name: "Linh",
          contribution_role: "CONTRIBUTOR",
          contribution_role_label: "Tham gia",
          count_status: "COUNTED",
          count_status_label: "Đã ghi nhận",
          counted_at: "2026-09-10T02:00:00Z",
        }),
      ],
      status: "APPROVED",
      status_label: "Đã xác nhận",
    });
    stubFetch(routes([shoot], { itemDetail: detail(shoot) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    const people = (await screen.findByText("Người thực hiện")).parentElement as HTMLElement;
    expect(within(people).getByText("Hảo")).toBeInTheDocument();
    expect(within(people).getByText("Linh")).toBeInTheDocument();
    // Each gets a whole credit. Nobody's share was divided into a half.
    // Scoped to the contributor list: the summary strip has a tile with the
    // same word, and it is a different figure entirely.
    expect(within(people).getAllByText("Đã ghi nhận")).toHaveLength(2);
    expect(screen.queryByText(/0\.5/)).not.toBeInTheDocument();
  });
});

describe("13. evidence is one text, and adding it is its own form", () => {
  it("submits through the form rather than stacking a second dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/evidence", method: "POST", status: 201, body: detail(workItem()) },
      ...routes([workItem()], { itemDetail: detail(workItem()) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    // Inline UX patch: with nothing attached yet, the one textarea is simply
    // there - no "Thêm minh chứng" to press first, and no label/link pair.
    await userEvent.type(
      await screen.findByRole("textbox", { name: "Minh chứng" }),
      "Bản dựng https://drive.google.com/file/d/x/view",
    );
    await userEvent.click(screen.getByRole("button", { name: "Lưu minh chứng" }));

    // A parameter form's submit button *is* the confirmation step. Two dialogs
    // for one decision is what `ACTION_INVENTORY` forbids.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const calls = (
      fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
    ).calls;
    const sent = calls.find((call) => call.method === "POST" && call.url.includes("/evidence"));
    expect(sent?.body).toEqual({ text: "Bản dựng https://drive.google.com/file/d/x/view" });
  });
});

// --- 15: STARTING WORK ASKS FIRST ------------------------------------------

describe("15. starting work asks first", () => {
  const started = () => workItem({ status: "ACCEPTED", status_label: "Được giao" });

  it("sends nothing on the first click, and opens the dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/start", method: "POST", body: detail(workItem({ status: "IN_PROGRESS" })) },
      ...routes([started()], { itemDetail: detail(started()) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Bắt đầu" }));
    // `ACCEPTED -> IN_PROGRESS` is a workflow transition a manager reading the
    // board can see. M1 shipped it without a dialog; this is the fix.
    expect(dialog().getByText("Bắt đầu công việc này?")).toBeInTheDocument();
    expect(dialog().getByText(/sẽ chuyển sang trạng thái Đang làm/)).toBeInTheDocument();
    expect(posts(fetchMock, "/start")).toHaveLength(0);
  });

  it("sends nothing when the dialog is cancelled", async () => {
    const fetchMock = stubFetch([
      { match: "/start", method: "POST", body: detail(workItem({ status: "IN_PROGRESS" })) },
      ...routes([started()], { itemDetail: detail(started()) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Bắt đầu" }));
    await cancelDialog();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(fetchMock, "/start")).toHaveLength(0);
  });

  it("sends exactly one mutation on confirm", async () => {
    const fetchMock = stubFetch([
      { match: "/start", method: "POST", body: detail(workItem({ status: "IN_PROGRESS" })) },
      ...routes([started()], { itemDetail: detail(started()) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Bắt đầu" }));
    await confirm();
    await waitFor(() => expect(posts(fetchMock, "/start")).toHaveLength(1));
  });

  it("keeps double-submit protection: a second confirm sends nothing more", async () => {
    const fetchMock = stubFetch([
      { match: "/start", method: "POST", body: detail(workItem({ status: "IN_PROGRESS" })) },
      ...routes([started()], { itemDetail: detail(started()) }),
    ]);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    await userEvent.click(await screen.findByRole("button", { name: "Bắt đầu" }));
    const accept = screen
      .getByRole("dialog")
      .querySelector<HTMLElement>("[data-confirm-accept]") as HTMLElement;
    // Two clicks on the same accept button, as a double-tap produces.
    await userEvent.click(accept);
    await userEvent.click(accept);
    await waitFor(() => expect(posts(fetchMock, "/start")).toHaveLength(1));
  });

  it("does not offer Bắt đầu once the work is already under way", async () => {
    const going = workItem({ status: "IN_PROGRESS", status_label: "Đang làm" });
    stubFetch(routes([going], { itemDetail: detail(going) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByText("Người thực hiện");
    expect(screen.queryByRole("button", { name: "Bắt đầu" })).not.toBeInTheDocument();
  });
});

// --- 16: SCOPE OPTIONS FOLLOW THE CAPABILITIES -----------------------------

describe("16. the scope picker offers only what the server would accept", () => {
  it("offers an employee nothing to choose", async () => {
    stubFetch(routes([workItem()], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");
    // One scope is not a choice, so there is no control at all.
    expect(screen.queryByRole("combobox", { name: "Phạm vi" })).not.toBeInTheDocument();
  });

  it("offers a team lead their own book and their queue, but not the department", async () => {
    stubFetch(routes([workItem()], { capabilities: MANAGER }));
    renderWithQuery(<WorkPage />);

    const picker = await screen.findByRole("combobox", { name: "Phạm vi" });
    const options = within(picker)
      .getAllByRole("option")
      .map((one) => one.getAttribute("value"));
    // **The patch.** `PR_WORK_MANAGE` no longer implies the whole department.
    expect(options).toEqual(["MINE", "ASSIGNED_BY_ME", "NEEDS_MY_DECISION"]);
    expect(options).not.toContain("ALL");
  });

  it("offers Head and Admin the department view", async () => {
    stubFetch(routes([workItem()], { capabilities: [...MANAGER, "PR_WORK_VIEW_ALL"] }));
    renderWithQuery(<WorkPage />);

    const picker = await screen.findByRole("combobox", { name: "Phạm vi" });
    expect(
      within(picker)
        .getAllByRole("option")
        .map((one) => one.getAttribute("value")),
    ).toEqual(["MINE", "ASSIGNED_BY_ME", "NEEDS_MY_DECISION", "ALL"]);
  });

  it("offers the decision queue to a validator who cannot assign", async () => {
    // Accepting a proposal and validating finished work are two capabilities,
    // and somebody may hold one without the other.
    stubFetch(
      routes([workItem()], { capabilities: ["PR_WORK_EXECUTE", "PR_WORK_VALIDATE"] }),
    );
    renderWithQuery(<WorkPage />);

    const picker = await screen.findByRole("combobox", { name: "Phạm vi" });
    expect(
      within(picker)
        .getAllByRole("option")
        .map((one) => one.getAttribute("value")),
    ).toEqual(["MINE", "NEEDS_MY_DECISION"]);
  });
});

// --- 14: NO SCORE ----------------------------------------------------------

describe("14. no score appears anywhere", () => {
  /*
   * **Re-aimed by M2, and narrowed rather than weakened.**
   *
   * The original list forbade "KPI", "hạn mức" and "vượt hạn" along with the
   * point words, because M1 had no quota engine and any of them appearing would
   * have been somebody starting M2 by accident. M2 *is* the quota engine, and
   * "Kế hoạch KPI" is now a tab on this very screen - so the quota vocabulary
   * comes off the list and the point words stay on it, joined by the ones Part
   * Y of the M2 brief names.
   *
   * The invariant that has to survive is unchanged: **no points.** `COUNTED` is
   * M1's word, `ELIGIBLE` is M2's, and neither is a score. Nothing that was
   * forbidden for a *scoring* reason has been allowed.
   */
  it("shows counted work and never a point or a rate", async () => {
    const approved = workItem({
      status: "APPROVED",
      status_label: "Đã xác nhận",
      contributors: [
        contribution({
          count_status: "COUNTED",
          count_status_label: "Đã ghi nhận",
          counted_at: "2026-09-10T02:00:00Z",
        }),
      ],
    });
    stubFetch(routes([approved], { itemDetail: detail(approved) }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByText("Người thực hiện");

    const rendered = document.body.textContent ?? "";
    // "Ghi nhận" - recorded - is the vocabulary. "Điểm" is a promise M1 has no
    // right to make, and M6's problem.
    expect(rendered).toContain("Đã ghi nhận");
    for (const forbidden of ["điểm", "Điểm", "tính điểm", "thưởng", "hệ số"]) {
      expect(rendered, forbidden).not.toContain(forbidden);
    }
  });

  it("keeps the API client free of scoring fields", () => {
    // Structural: adding a score field to the wire types would be a red test
    // rather than a quiet feature.
    const source = read("lib/api.ts");
    const start = source.indexOf("export interface WorkContribution");
    const end = source.indexOf("export interface WorkEvidence");
    const block = source.slice(start, end);
    // "quota" is off this list for the reason above - M2 owns the word, and
    // `WorkContribution` still does not carry one. What must never appear on the
    // KPI-facing row is a *score*.
    for (const forbidden of ["score", "multiplier", "awarded", "points", "bonus"]) {
      expect(block, forbidden).not.toContain(forbidden);
    }
    // And the M1 row still carries no quota field either: eligibility lives in
    // its own type, because a decision belongs to a period and a contribution
    // does not.
    expect(block).not.toContain("quota_status");
  });
});

/** The POST calls the stub recorded against one path. */
function posts(
  fetchMock: ReturnType<typeof stubFetch>,
  path: string,
): Array<{ url: string; method: string }> {
  const calls = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> })
    .calls;
  return calls.filter((call) => call.method === "POST" && call.url.includes(path));
}

/** Read one frontend source file, for the structural assertions above. */
function read(relative: string): string {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  return fs.readFileSync(path.join(process.cwd(), "src", relative), "utf8");
}
