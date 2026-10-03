/**
 * **Kế hoạch KPI — the draft revision lifecycle.**
 *
 * The bug this file guards: with an approved v3 and a draft v4, the screen
 * showed one control — *"Tạo bản điều chỉnh"* over v3 — which the server
 * refuses, because M2 allows one draft per employee-month. The draft it
 * collided with was filtered into the *Lịch sử thay đổi* accordion on
 * `is_current`, where nothing acts on it. So the only visible action led to an
 * error, and the draft could not be continued, approved or discarded from
 * anywhere on the screen. Clearing an empty v4 needed a database.
 *
 * The four that carry the patch
 * ------------------------------
 *
 * **Test 1.1** — three sections. *Kế hoạch hiện tại*, *Bản điều chỉnh đang
 * soạn*, *Lịch sử thay đổi*. A draft is not history; it is the thing somebody
 * is in the middle of.
 *
 * **Test 1.3** — *"Tạo bản điều chỉnh"* is gone while a draft exists. Hiding it
 * is a courtesy — the uniqueness index is the rule — but a control whose only
 * outcome is a refusal is what turned a normal state into a trap.
 *
 * **Test 2.2** — an **empty** draft still offers *Tiếp tục chỉnh sửa* and *Bỏ
 * bản nháp*, with *Duyệt* disabled and a reason. Hiding the panel because the
 * draft is incomplete is what made it inescapable.
 *
 * **Test 4.1** — the lost race reads as Vietnamese information, not as the
 * server's English sentence. Keyed on `details.reason`, never on the message.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const CONFIGURE = ["PR_WORK_EXECUTE", "PR_WORK_CONFIGURE", "PR_WORK_VIEW_ALL"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const HAO = "22222222-2222-2222-2222-222222222222";
const PERIOD_ID = "66666666-6666-6666-6666-666666666666";
const TYPE_ID = "11111111-1111-1111-1111-111111111111";
/** v3, approved and in force. */
const CURRENT = "33333333-3333-3333-3333-333333333333";
/** v4, the revision being written. */
const DRAFT = "44444444-4444-4444-4444-444444444444";
/** v2, superseded. Real history. */
const OLD = "22222222-0000-0000-0000-000000000002";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const period = () => ({
  id: PERIOD_ID,
  code: "2026-09",
  period_type: "MONTH",
  date_start: "2026-09-01",
  date_end: "2026-09-30",
  status: "OPEN",
  status_label: "Đang mở",
  closed_at: null,
  locked_at: null,
});

const WORK_TYPE = {
  id: TYPE_ID,
  code: "SHORT_SCRIPT",
  name: "Kịch bản video ngắn",
  category: "CONTENT",
  category_label: "Nội dung",
  description: null,
  default_unit: "ITEM",
  default_unit_label: "đầu việc",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const quota = () => ({
  id: "99999999-9999-9999-9999-999999999999",
  plan_id: DRAFT,
  work_type_id: TYPE_ID,
  work_type_name: WORK_TYPE.name,
  basis: "ITEM_COUNT",
  basis_label: "Theo số đầu việc",
  target_value: "5.00",
  eligibility_cap: "5.00",
  unit: null,
  unit_label: null,
  note: null,
});

const plan = (over: Record<string, unknown> = {}) => ({
  id: CURRENT,
  user_id: HAO,
  user_name: "Hảo",
  period_id: PERIOD_ID,
  period_code: "2026-09",
  period_status: "OPEN",
  version_no: 3,
  status: "APPROVED",
  status_label: "Đang áp dụng",
  supersedes_plan_id: OLD,
  note: null,
  created_by_user_id: HAO,
  approved_by_user_id: HAO,
  approved_at: "2026-09-04T11:21:00Z",
  superseded_at: null,
  discarded_at: null,
  created_at: "2026-09-01T02:00:00Z",
  updated_at: "2026-09-04T11:21:00Z",
  quota_count: 5,
  ...over,
});

/** v3's detail. `can_revise` false is the server saying a draft already exists. */
const currentDetail = (over: Record<string, unknown> = {}) => ({
  plan: plan(),
  period: period(),
  quotas: [quota()],
  created_by_name: "Hà Trưởng Phòng",
  approved_by_name: "Hà Trưởng Phòng",
  can_edit: false,
  can_approve: false,
  can_revise: false,
  can_discard: false,
  ...over,
});

/** v4's detail. Empty by default: the state the report was filed from. */
const draftDetail = (over: Record<string, unknown> = {}) => ({
  plan: plan({
    id: DRAFT,
    version_no: 4,
    status: "DRAFT",
    status_label: "Bản nháp",
    supersedes_plan_id: CURRENT,
    approved_at: null,
    approved_by_user_id: null,
    updated_at: "2026-09-05T09:30:00Z",
    quota_count: 0,
  }),
  period: period(),
  quotas: [],
  created_by_name: "Hà Trưởng Phòng",
  approved_by_name: null,
  can_edit: true,
  // M2 refuses a plan with no quotas, so an empty draft is not approvable.
  can_approve: false,
  can_revise: false,
  can_discard: true,
  ...over,
});

const planSummary = (over: Record<string, unknown> = {}) => ({
  user_id: HAO,
  user_name: "Hảo",
  period_id: PERIOD_ID,
  has_plan: true,
  current_plan_id: CURRENT,
  current_version_no: 3,
  current_status: "APPROVED",
  current_status_label: "Đang áp dụng",
  quota_count: 5,
  approved_at: "2026-09-04T11:21:00Z",
  updated_at: "2026-09-04T11:21:00Z",
  latest_draft_id: DRAFT,
  draft_version_no: 4,
  draft_quota_count: 0,
  draft_updated_at: "2026-09-05T09:30:00Z",
  history_count: 4,
  terminal_count: 2,
  ...over,
});

const historyEntry = (over: Record<string, unknown> = {}) => ({
  id: OLD,
  version_no: 2,
  status: "SUPERSEDED",
  status_label: "Đã thay thế",
  quota_count: 4,
  created_at: "2026-09-01T02:00:00Z",
  approved_at: "2026-09-02T02:00:00Z",
  superseded_at: "2026-09-04T11:21:00Z",
  discarded_at: null,
  note: null,
  is_current: false,
  is_active_draft: false,
  ...over,
});

/** The three-row history the screen partitions: current, active draft, past. */
const HISTORY = [
  historyEntry({ id: DRAFT, version_no: 4, status: "DRAFT", status_label: "Bản nháp",
    quota_count: 0, approved_at: null, superseded_at: null, is_active_draft: true }),
  historyEntry({ id: CURRENT, version_no: 3, status: "APPROVED",
    status_label: "Đang áp dụng", quota_count: 5, superseded_at: null, is_current: true }),
  historyEntry(),
  historyEntry({ id: "11111111-0000-0000-0000-000000000001", version_no: 1, quota_count: 3 }),
];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  {
    capabilities = CONFIGURE,
    summaries = [planSummary()],
    history = HISTORY,
    current = currentDetail(),
    draft = draftDetail(),
  } = {},
  extra: Route[] = [],
): Route[] => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [{ user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" }] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/periods", body: [period()] },
  {
    match: "/api/pr/work/eligibility/summary",
    body: {
      user_id: HAO,
      period: period(),
      plan_id: CURRENT,
      plan_version_no: 3,
      plan_approved_at: "2026-09-04T11:21:00Z",
      types: [],
      contributions_by_status: {
        NO_QUOTA: 0,
        UNMEASURABLE: 0,
        ELIGIBLE: 0,
        PARTIALLY_ELIGIBLE: 0,
        OVER_QUOTA: 0,
        PENDING_EVALUATION: 0,
      },
      counted_contributions: 0,
      counted_work_items: 0,
    },
  },
  { match: "/api/pr/work/eligibility", body: { period: period(), user_id: HAO, contributions: [] } },
  { match: "/api/pr/work/plans/summary", body: { period_id: PERIOD_ID, items: summaries } },
  { match: "/api/pr/work/plans/history", body: { user_id: HAO, period_id: PERIOD_ID, items: history } },
  { match: `/api/pr/work/plans/${DRAFT}`, body: draft },
  { match: `/api/pr/work/plans/${CURRENT}`, body: current },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: {} },
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

/** Arrive on the KPI view and open the employee. */
async function openEmployee() {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
  await userEvent.click(await screen.findByRole("button", { name: /Hảo/ }));
}

const draftPanel = () =>
  within(screen.getByRole("region", { name: "Bản điều chỉnh đang soạn" }));

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1: THE THREE SECTIONS -------------------------------------------------

describe("1. current plan, active draft and history are three different things", () => {
  it("shows the draft in its own section, not inside history", async () => {
    stubFetch(routes());
    await openEmployee();

    // The plan in force. Matched as "at least one": the employee's own KPI
    // header above the administration list names the same version, and that is
    // the same fact rather than a duplicate control.
    expect((await screen.findAllByText(/bản v3/)).length).toBeGreaterThan(0);
    // The revision being written, in a section of its own.
    const panel = await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });
    expect(within(panel).getByText("v4")).toBeInTheDocument();
    expect(within(panel).getByText("Bản nháp")).toBeInTheDocument();
    expect(within(panel).getByText("0 hạn mức")).toBeInTheDocument();
  });

  it("counts only the versions that are over as history", async () => {
    // Four versions exist. v3 is in force and v4 is being written; neither is
    // history, so the accordion says 2 - v2 and v1.
    stubFetch(routes());
    await openEmployee();

    expect(await screen.findByText("Lịch sử thay đổi (2)")).toBeInTheDocument();
    const accordion = screen.getByText("Lịch sử thay đổi (2)").closest("details") as HTMLElement;
    expect(within(accordion).queryByText("v4")).toBeNull();
    expect(within(accordion).queryByText("v3")).toBeNull();
    expect(within(accordion).getByText("v2")).toBeInTheDocument();
    expect(within(accordion).getByText("v1")).toBeInTheDocument();
  });

  it("does not offer to create a revision while one is in flight", async () => {
    // **The trap.** The server refuses a second draft, so the control that used
    // to be the only thing on this screen is not drawn at all.
    stubFetch(routes());
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });

    expect(screen.queryByRole("button", { name: "Tạo bản điều chỉnh" })).toBeNull();
    expect(draftPanel().getByRole("button", { name: "Tiếp tục chỉnh sửa" })).toBeInTheDocument();
  });

  it("offers to create one again once no draft exists", async () => {
    stubFetch(
      routes({
        summaries: [planSummary({ latest_draft_id: null, draft_version_no: null, terminal_count: 3 })],
        history: HISTORY.filter((one) => one.id !== DRAFT),
        current: currentDetail({ can_revise: true }),
      }),
    );
    await openEmployee();

    expect(
      await screen.findByRole("button", { name: "Tạo bản điều chỉnh" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Bản điều chỉnh đang soạn" })).toBeNull();
  });

  it("names the draft on the employee row, so nobody has to go looking", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));

    const row = (await screen.findByText("Hảo")).closest("button") as HTMLElement;
    expect(within(row).getByText(/Kế hoạch hiện tại: v3/)).toBeInTheDocument();
    // KPI self-service renamed the caption and added the draft's review state,
    // in the server's words; the draft is still named on the row.
    expect(within(row).getByText("Bản điều chỉnh:")).toBeInTheDocument();
    expect(within(row).getByText("v4")).toBeInTheDocument();
    expect(within(row).getByText("· 0 chỉ tiêu")).toBeInTheDocument();
    expect(within(row).getByText("Bản nháp")).toBeInTheDocument();
  });
});

// --- 2: THE THREE WAYS OUT -------------------------------------------------

describe("2. an empty draft is recoverable without a database", () => {
  it("offers continue and discard, and disables approve with the reason", async () => {
    stubFetch(routes());
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });
    const panel = draftPanel();
    // The lifecycle controls follow the draft's own detail request.
    await waitFor(() => expect(panel.getByRole("button", { name: /Bỏ bản nháp/ })).toBeEnabled());

    expect(panel.getByRole("button", { name: "Tiếp tục chỉnh sửa" })).toBeEnabled();
    expect(panel.getByRole("button", { name: /Bỏ bản nháp/ })).toBeEnabled();
    // Disabled rather than absent: a control that vanishes is how somebody
    // concludes there is no way to approve at all.
    expect(panel.getByRole("button", { name: "Duyệt" })).toBeDisabled();
    expect(panel.getByText("Cần ít nhất một hạn mức trước khi duyệt.")).toBeInTheDocument();
  });

  it("opens the existing draft rather than creating another version", async () => {
    const calls = stubFetch(routes());
    await openEmployee();
    await userEvent.click(draftPanel().getByRole("button", { name: "Tiếp tục chỉnh sửa" }));

    // The editor that appears is v4's, and nothing was posted to create one.
    await waitFor(() =>
      expect(draftPanel().getByRole("button", { name: "Thêm hạn mức" })).toBeInTheDocument(),
    );
    expect(
      calls.mock.calls.some(
        ([url, init]) =>
          /\/revise$/.test(String(url)) && (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
    expect(
      calls.mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/api/pr/work/plans") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("enables approve once the draft has a quota, per the server's flag", async () => {
    // The rule is M2's `can_approve`, and this screen follows it rather than
    // deciding for itself what "ready" means.
    stubFetch(routes({ draft: draftDetail({ quotas: [quota()], can_approve: true }) }));
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });
    await waitFor(() =>
      expect(draftPanel().getByRole("button", { name: "Duyệt" })).toBeEnabled(),
    );
    expect(draftPanel().queryByText("Cần ít nhất một hạn mức trước khi duyệt.")).toBeNull();
  });

  it("asks before discarding, and says the plan in force is untouched", async () => {
    const calls = stubFetch(
      routes({}, [
        {
          match: `/api/pr/work/plans/${DRAFT}/discard`,
          method: "POST",
          body: draftDetail({
            plan: { ...draftDetail().plan, status: "DISCARDED", status_label: "Đã bỏ" },
            can_edit: false,
            can_discard: false,
          }),
        },
      ]),
    );
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });
    await waitFor(() =>
      expect(draftPanel().getByRole("button", { name: /Bỏ bản nháp/ })).toBeEnabled(),
    );
    await userEvent.click(draftPanel().getByRole("button", { name: /Bỏ bản nháp/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/v4/)).toBeInTheDocument();
    await confirm();
    await waitFor(() =>
      expect(calls.mock.calls.some(([url]) => String(url).includes("/discard"))).toBe(true),
    );
  });
});

// --- 3: PERMISSIONS --------------------------------------------------------

describe("3. the controls follow the server's flags", () => {
  it("offers no lifecycle actions to somebody who may not configure", async () => {
    stubFetch(
      routes({
        capabilities: EMPLOYEE,
        draft: draftDetail({ can_edit: false, can_approve: false, can_discard: false }),
        current: currentDetail(),
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));

    // An employee does not reach the administration list at all, so there is no
    // draft panel to carry controls.
    await screen.findByRole("region", { name: "Kế hoạch KPI" });
    expect(screen.queryByRole("region", { name: "Bản điều chỉnh đang soạn" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tạo bản điều chỉnh" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Bỏ bản nháp/ })).toBeNull();
  });

  it("hides Duyệt when the server refuses it for a reason other than emptiness", async () => {
    // A closed period, say. The screen has no sentence for that and does not
    // invent one - it simply does not draw a button the server would refuse.
    stubFetch(routes({ draft: draftDetail({ quotas: [quota()], can_approve: false }) }));
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });
    await waitFor(() =>
      expect(draftPanel().getByRole("button", { name: /Bỏ bản nháp/ })).toBeEnabled(),
    );

    expect(draftPanel().queryByRole("button", { name: "Duyệt" })).toBeNull();
    expect(draftPanel().queryByText("Cần ít nhất một hạn mức trước khi duyệt.")).toBeNull();
    // The other two ways out remain.
    expect(draftPanel().getByRole("button", { name: "Tiếp tục chỉnh sửa" })).toBeInTheDocument();
    expect(draftPanel().getByRole("button", { name: /Bỏ bản nháp/ })).toBeInTheDocument();
  });
});

// --- 3b: ONE CREATION PATH PER STATE ---------------------------------------

describe("3b. the creation control follows the state", () => {
  it("offers Tạo kế hoạch only to an employee with nothing", async () => {
    stubFetch(
      routes({
        summaries: [
          planSummary({
            has_plan: false,
            current_plan_id: null,
            current_version_no: null,
            current_status: null,
            current_status_label: null,
            quota_count: 0,
            approved_at: null,
            latest_draft_id: null,
            draft_version_no: null,
            history_count: 0,
            terminal_count: 0,
          }),
        ],
        history: [],
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));

    expect(
      await screen.findByRole("button", { name: "Tạo kế hoạch cho Hảo" }),
    ).toBeInTheDocument();
  });

  it("never offers Tạo kế hoạch beside a plan already in force", async () => {
    // **The invariant, as a screen.** A plan in force is changed by revising
    // it; `create_plan` is refused for this state, so the control that would
    // call it is not drawn on the row at all.
    stubFetch(
      routes({
        summaries: [planSummary({ latest_draft_id: null, draft_version_no: null })],
        history: HISTORY.filter((one) => one.id !== DRAFT),
        current: currentDetail({ can_revise: true }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
    await screen.findByText(/Kế hoạch hiện tại: v3/);

    expect(screen.queryByRole("button", { name: "Tạo kế hoạch cho Hảo" })).toBeNull();
    // And the one that does work is there, once the row is open.
    await userEvent.click(screen.getByRole("button", { name: /Hảo/ }));
    expect(
      await screen.findByRole("button", { name: "Tạo bản điều chỉnh" }),
    ).toBeInTheDocument();
  });

  it("offers neither creation control while a draft is in flight", async () => {
    stubFetch(routes());
    await openEmployee();
    await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" });

    expect(screen.queryByRole("button", { name: "Tạo kế hoạch cho Hảo" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tạo bản điều chỉnh" })).toBeNull();
    await waitFor(() =>
      expect(draftPanel().getByRole("button", { name: /Bỏ bản nháp/ })).toBeEnabled(),
    );
    expect(draftPanel().getByRole("button", { name: "Tiếp tục chỉnh sửa" })).toBeInTheDocument();
  });

  it("tells a stale client to revise instead, and opens the employee", async () => {
    // The toolbar form picks any employee, so it can collide with a state it
    // did not know about. `approved_plan_requires_revision` is the server's own
    // code; the English sentence is never rendered.
    const calls = stubFetch(
      routes(
        {
          summaries: [planSummary({ latest_draft_id: null, draft_version_no: null })],
          history: HISTORY.filter((one) => one.id !== DRAFT),
          current: currentDetail({ can_revise: true }),
        },
        [
          {
            match: "/api/pr/work/plans",
            method: "POST",
            status: 409,
            body: {
              error: {
                code: "pr_work_plan_state",
                message:
                  "This person already has an approved plan for this period; revise it instead",
                details: {
                  reason: "approved_plan_requires_revision",
                  plan_id: CURRENT,
                  version_no: 3,
                },
              },
            },
          },
        ],
      ),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
    await userEvent.click(await screen.findByRole("button", { name: "Tạo kế hoạch" }));
    await userEvent.selectOptions(screen.getByLabelText("Nhân sự"), HAO);
    await userEvent.click(screen.getByRole("button", { name: "Tạo bản nháp" }));

    expect(
      await screen.findByText(/đã có kế hoạch đang áp dụng.*tạo bản điều chỉnh/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/This person already has an approved plan/),
    ).toBeNull();
    // The employee's row is opened, where the working control lives.
    expect(
      await screen.findByRole("button", { name: "Tạo bản điều chỉnh" }),
    ).toBeInTheDocument();
    // And the state was refetched rather than assumed.
    await waitFor(() =>
      expect(
        calls.mock.calls.filter(([url]) => String(url).includes("/plans/summary")).length,
      ).toBeGreaterThan(1),
    );
  });

  it("tells a stale client to continue an existing draft, and keeps that path", async () => {
    // The other code, unchanged by this patch.
    stubFetch(
      routes({}, [
        {
          match: "/api/pr/work/plans",
          method: "POST",
          status: 409,
          body: {
            error: {
              code: "pr_work_plan_state",
              message: "A draft plan for this person and period already exists",
              details: { reason: "draft_already_exists", plan_id: DRAFT, version_no: 4 },
            },
          },
        },
      ]),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kế hoạch KPI" }));
    await userEvent.click(await screen.findByRole("button", { name: "Tạo kế hoạch" }));
    await userEvent.selectOptions(screen.getByLabelText("Nhân sự"), HAO);
    await userEvent.click(screen.getByRole("button", { name: "Tạo bản nháp" }));

    expect(
      await screen.findByText(/đã có một bản kế hoạch đang soạn/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/A draft plan for this person/)).toBeNull();
    expect(
      await screen.findByRole("region", { name: "Bản điều chỉnh đang soạn" }),
    ).toBeInTheDocument();
  });
});

// --- 4: THE RACE -----------------------------------------------------------

describe("4. losing the race is information, not a dead end", () => {
  it("says so in Vietnamese and never shows the server's English", async () => {
    // Browser A rendered before the draft existed, so it still offers the
    // control; the server refuses, naming the plan it collided with.
    stubFetch(
      routes(
        {
          summaries: [planSummary({ latest_draft_id: null, draft_version_no: null })],
          history: HISTORY.filter((one) => one.id !== DRAFT),
          current: currentDetail({ can_revise: true }),
        },
        [
          {
            match: `/api/pr/work/plans/${CURRENT}/revise`,
            method: "POST",
            status: 409,
            body: {
              error: {
                code: "pr_work_plan_state",
                message: "A draft revision of this plan already exists",
                details: { reason: "draft_already_exists", plan_id: DRAFT, version_no: 4 },
              },
            },
          },
        ],
      ),
    );
    await openEmployee();
    await userEvent.click(await screen.findByRole("button", { name: "Tạo bản điều chỉnh" }));
    await confirm();

    expect(
      await screen.findByText(/Đã có một bản điều chỉnh đang soạn/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/A draft revision of this plan already exists/)).toBeNull();
  });
});
