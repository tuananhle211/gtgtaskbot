/**
 * M3.1 - the two content milestones, on screen.
 *
 * Two questions this suite exists to answer, and they belong to two different
 * people:
 *
 * **The owner's.** *"Why is nothing appearing on anybody's board?"* - almost
 * always a missing Content Type → WorkType mapping, which is why the mapping
 * panel moved out of the KPI view and into *Cấu hình* beside the work types it
 * selects from (tests 1-5).
 *
 * **The employee's.** *"Did the thing I handed in get recorded?"* - answered on
 * the content itself rather than by going to look in another module (tests 6-8).
 *
 * As everywhere in this suite the browser decides nothing: the state words are
 * the server's `status_label` and `count_status`, and a test that passed
 * because the component recomputed them in TypeScript would be testing a second
 * implementation of M1's ladder.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CONTENT, VERSION, channelsNavigation, renderWithQuery, stubFetch } from "./helpers";
import { CONTENT_TYPE_ORDER, contentTypeLabel } from "@/lib/labels";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => ({
  ...NAV.module,
  useParams: () => ({ id: CONTENT.id }),
}));

const { default: WorkPage } = await import("@/app/pr/work/page");
const { default: ContentDetailPage } = await import("@/app/pr/content/[id]/page");

const EMPLOYEE = ["PR_WORK_EXECUTE"];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const OWNER = [...LEAD, "PR_WORK_CONFIGURE", "PR_WORK_VIEW_ALL"];

const TYPE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const EDIT_TYPE_ID = "dddddddd-dddd-dddd-dddd-eeeeeeeeeeee";
const HAO = "22222222-2222-2222-2222-222222222222";
const DUC = "55555555-5555-5555-5555-555555555555";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const workType = (over: Record<string, unknown> = {}) => ({
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
  ...over,
});

const rule = (over: Record<string, unknown> = {}) => ({
  id: "99999999-9999-9999-9999-999999999999",
  contribution_kind: "CONTENT_CREATION",
  content_type: "SHORT_VIDEO_SCRIPT",
  work_type_id: TYPE_ID,
  work_type_code: "SHORT_SCRIPT",
  work_type_name: "Kịch bản video ngắn",
  is_active: true,
  ...over,
});

const contribution = (over: Record<string, unknown> = {}) => ({
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  work_item_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  user_id: HAO,
  user_name: "Hảo",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-01T02:00:00Z",
  count_status: "COUNTED",
  count_status_label: "Đã ghi nhận",
  counted_at: "2026-09-02T02:00:00Z",
  excluded_reason: null,
  ...over,
});

const workItem = (over: Record<string, unknown> = {}) => ({
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  code: "WRK-2026-000009",
  title: "Nội dung: Chăm sóc sau nâng mũi",
  description: null,
  work_type_id: TYPE_ID,
  work_type_code: "SHORT_SCRIPT",
  work_type_name: "Kịch bản video ngắn",
  work_type_category: "CONTENT",
  source_type: "CONTENT",
  source_label: "Từ quy trình nội dung",
  is_source_derived: true,
  status: "APPROVED",
  status_label: "Đã xác nhận",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: null,
  unit: null,
  unit_label: null,
  due_at: null,
  is_overdue: false,
  created_by_user_id: HAO,
  assigned_by_user_id: null,
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: "2026-09-01T05:00:00Z",
  approved_at: "2026-09-02T02:00:00Z",
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  content_id: CONTENT.id,
  content_code: CONTENT.code,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution()],
  ...over,
});

/** The editor's job: handed in, workload recorded, nobody has accepted it yet. */
const productionItem = () =>
  workItem({
    id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    code: "WRK-2026-000010",
    work_type_id: EDIT_TYPE_ID,
    work_type_code: "VIDEO_EDIT",
    work_type_name: "Dựng video",
    status: "COMPLETED",
    status_label: "Đã gửi bản dựng",
    approved_at: null,
    contributors: [
      contribution({
        id: "aaaaaaaa-0000-0000-0000-000000000002",
        work_item_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
        user_id: DUC,
        user_name: "Đức",
        count_status: "PENDING",
        count_status_label: "Chưa ghi nhận",
        counted_at: null,
      }),
    ],
  });

const summary = () => ({
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-16T00:00:00Z",
  created: 0,
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
});

const workRoutes = (
  {
    capabilities = OWNER,
    rules = [] as Array<Record<string, unknown>>,
    types = [workType()],
    items = [] as Array<Record<string, unknown>>,
  } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/content/rules", body: rules },
  { match: "/api/pr/work/types", body: types },
  { match: "/api/pr/work/periods", body: [] },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: summary() },
  { match: "/history", body: [] },
  { match: `/api/pr/work/${items[0]?.id ?? "none"}`, body: { item: items[0], work_type: workType(), evidence: [], content_code: CONTENT.code, can_manage: false, can_validate: false, can_execute: true } },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

const openConfig = async () => {
  await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
};

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-5: THE OWNER'S SCREEN ----------------------------------------------

describe("1. the mapping panel lives in Cấu hình, beside the work types", () => {
  it("is on the configuration tab rather than the KPI view", async () => {
    stubFetch(workRoutes({ rules: [rule()] }));
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(await screen.findByText(/Ánh xạ nội dung/)).toBeInTheDocument();
    // And not on the KPI view it used to sit under.
    await userEvent.click(screen.getByRole("button", { name: "Kế hoạch KPI" }));
    expect(screen.queryByText(/Ánh xạ nội dung/)).not.toBeInTheDocument();
  });
});

describe("2. only a configurer reaches the mapping", () => {
  it("hides the tab from an employee and from a Trưởng nhóm", async () => {
    stubFetch(workRoutes({ capabilities: EMPLOYEE }));
    const view = renderWithQuery(<WorkPage />);
    expect(await screen.findByRole("button", { name: "Công việc" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
    view.unmount();

    vi.unstubAllGlobals();
    NAV.reset();
    // PR_WORK_MANAGE assigns work. It does not configure the taxonomy or the
    // mapping, and this is the assertion that keeps those apart.
    stubFetch(workRoutes({ capabilities: LEAD }));
    renderWithQuery(<WorkPage />);
    expect(await screen.findByRole("button", { name: "Công việc" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
  });
});

describe("3. only the two V1 milestones can be configured", () => {
  it("offers writing and production, and never publication", async () => {
    stubFetch(workRoutes({ rules: [rule()] }));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));
    const picker = await screen.findByLabelText("Loại đóng góp");
    expect(within(picker).getByRole("option", { name: "Viết nội dung" })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Sản xuất" })).toBeInTheDocument();
    // V1 records automatic workload at two content milestones. Offering a third
    // would promise a projection that no longer happens.
    expect(within(picker).queryByRole("option", { name: /Đăng bài/ })).not.toBeInTheDocument();
  });
});

describe("4. a mapping configured before M3.1 still reads as words", () => {
  it("renders a historical publication rule, and an inactive work type", async () => {
    stubFetch(
      workRoutes({
        rules: [
          rule({
            id: "99999999-9999-9999-9999-000000000002",
            contribution_kind: "PUBLICATION",
            work_type_name: "Đăng bài",
            is_active: false,
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();

    // The kind is retired, not forgotten: the row still says what it is rather
    // than showing a raw enum code.
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));
    expect(await screen.findByText(/Đăng bài \(không còn tự động\)/)).toBeInTheDocument();
    expect(screen.getByText("Đã tắt")).toBeInTheDocument();
  });
});

describe("5. an empty mapping says so", () => {
  it("explains that nothing is being recorded automatically yet", async () => {
    stubFetch(workRoutes({ rules: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));
    expect(await screen.findByText(/Chưa có ánh xạ nào/)).toBeInTheDocument();
  });
});

// --- 5b: ONE TAXONOMY, ONE SET OF WORDS -----------------------------------

describe("5b. the mapping screen names content types the way /pr/content does", () => {
  /**
   * The patch this block exists for: the mapping selector used to render raw
   * codes - `SHORT_VIDEO_SCRIPT` - while the content board rendered "Kịch bản
   * video ngắn" for the same taxonomy. Two dictionaries for one vocabulary,
   * and an owner reading both screens had to know they were the same thing.
   *
   * Asserted against the **shared helper** rather than against a copied list of
   * expected Vietnamese, which is the point: a test carrying its own map would
   * be a third dictionary, and would keep passing while the two screens drifted
   * apart. `content-types.test.tsx` test 103 pins the same helper's output on
   * the content side.
   */
  it("offers the six formats in Vietnamese, from the shared label source", async () => {
    stubFetch(workRoutes({ rules: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    const select = await screen.findByLabelText("Loại nội dung");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Mặc định (mọi loại nội dung)",
      ...CONTENT_TYPE_ORDER.map((code) => contentTypeLabel(code)),
    ]);
  });

  it("never shows a raw enum code as an option's words", async () => {
    stubFetch(workRoutes({ rules: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    const select = await screen.findByLabelText("Loại nội dung");
    for (const option of within(select).getAllByRole("option")) {
      expect(option.textContent).not.toMatch(/^[A-Z][A-Z0-9_]+$/);
    }
  });

  it("still submits the code, not the label", async () => {
    const fetchMock = stubFetch(workRoutes({ rules: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    // Chosen by the words a person reads; sent as the value the database keys on.
    const select = await screen.findByLabelText("Loại nội dung");
    await userEvent.selectOptions(
      select,
      within(select).getByRole("option", { name: "Kịch bản video ngắn" }),
    );
    await userEvent.selectOptions(
      await screen.findByLabelText("Loại công việc tương ứng"),
      TYPE_ID,
    );
    await userEvent.click(screen.getByRole("button", { name: "Lưu ánh xạ" }));

    await waitFor(() => {
      const sent = (
        fetchMock as unknown as { calls: Array<{ method: string; body: Record<string, unknown> }> }
      ).calls
        .filter((call) => call.method === "PUT" || call.method === "POST")
        .at(-1);
      expect(sent?.body).toMatchObject({ content_type: "SHORT_VIDEO_SCRIPT" });
    });
  });

  it("names an existing rule's content type in Vietnamese", async () => {
    stubFetch(workRoutes({ rules: [rule({ content_type: "PRESS_ARTICLE" })] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    // Both the rule row and the selector option legitimately say it - that is
    // the consistency being asserted. What must not appear anywhere is the code.
    expect(await screen.findAllByText("Báo chí")).not.toHaveLength(0);
    expect(screen.queryByText("PRESS_ARTICLE")).not.toBeInTheDocument();
  });

  /**
   * **The distinction this patch must not collapse.**
   *
   * A rule with no content type is the *default scope for its kind*. A content
   * item with no content type is *"Chưa phân loại"*. The shared helper answers
   * `null` with the second, which is right for a content item and wrong for a
   * rule - so the rule's null case is answered before the helper is reached.
   * Getting this backwards would tell an owner they had configured a mapping
   * for unclassified content when they had configured the fallback for
   * everything.
   */
  it("calls a default rule a scope, never Chưa phân loại", async () => {
    stubFetch(workRoutes({ rules: [rule({ content_type: null })] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    expect(await screen.findAllByText("Mặc định (mọi loại nội dung)")).not.toHaveLength(0);
    expect(screen.queryByText("Chưa phân loại")).not.toBeInTheDocument();
  });

  it("does not offer Chưa phân loại as something a rule can target", async () => {
    stubFetch(workRoutes({ rules: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    await userEvent.click(await screen.findByText(/Ánh xạ nội dung/));

    // There is no rule shape that targets only unclassified content: such a
    // piece falls to the kind's default, so offering it would promise a
    // precision the resolver cannot deliver.
    const select = await screen.findByLabelText("Loại nội dung");
    expect(within(select).queryByText("Chưa phân loại")).not.toBeInTheDocument();
  });
});

// --- 6-8: THE EMPLOYEE'S SCREEN -------------------------------------------

describe("6. work detail names its source and links back to the content", () => {
  it("says Nguồn: Nội dung and offers the way back", async () => {
    const item = workItem();
    stubFetch(workRoutes({ items: [item] }));
    NAV.arriveAt(`/pr/work?item=${item.id}`);
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText(/Nguồn: Nội dung/)).toBeInTheDocument();
    const back = screen.getByRole("link", { name: `Mở nội dung ${CONTENT.code}` });
    expect(back).toHaveAttribute("href", `/pr/content/${CONTENT.id}`);
  });
});

describe("7. a handed-in cut says it is waiting for somebody", () => {
  it("shows the workload state and the pending-validation sentence", async () => {
    const item = productionItem();
    stubFetch(workRoutes({ items: [item] }));
    NAV.arriveAt(`/pr/work?item=${item.id}`);
    renderWithQuery(<WorkPage />);

    expect(await screen.findByText(/Chờ xác nhận độc lập/)).toBeInTheDocument();
    // The state word is the server's `status_label`, not a table in the browser.
    expect(screen.getAllByText("Đã gửi bản dựng").length).toBeGreaterThan(0);
    // And no points anywhere: what the work is worth is M6's question.
    for (const word of ["điểm", "score", "point"]) {
      expect(document.body.textContent?.toLowerCase()).not.toContain(word);
    }
  });
});

// --- 8: THE CONTENT'S OWN SCREEN ------------------------------------------

const contentRoutes = (items: Array<Record<string, unknown>>, capabilities = OWNER) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/available-actions", body: { content_id: CONTENT.id, workflow_stage: "APPROVED", available_actions: [] } },
  {
    match: "/review-context",
    body: {
      content: { ...CONTENT, workflow_stage: "APPROVED" },
      current_version: VERSION,
      targets: [],
      tasks: [],
      ai_review: null,
      ai_reviews_for_version: [],
      approvals: [],
    },
  },
  { match: "/versions", method: "GET", body: [VERSION] },
  { match: "/ai-review", body: { run: null, review: null, active: false, can_retry: false } },
  { match: "/api/pr/people", body: [] },
  { match: "/production", body: { content_id: CONTENT.id, workflow_stage: "APPROVED", producer_user_id: null, submissions: [] } },
  { match: "/api/pr/contents/", body: { content: { ...CONTENT, workflow_stage: "APPROVED" }, current_version: VERSION, targets: [], brand: null } },
  { match: "/destinations", body: [] },
  { match: "/comments", body: [] },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

/** The section lives on *Tổng quan*, which is not the tab the page opens on. */
const openOverview = async () => {
  await userEvent.click(await screen.findByRole("tab", { name: "Tổng quan" }));
};

describe("8. the content shows the work it produced", () => {
  it("lists the writer and the editor, with their states, linking into Work", async () => {
    stubFetch(contentRoutes([workItem(), productionItem()]));
    renderWithQuery(<ContentDetailPage />);
    await openOverview();

    expect(await screen.findByText("Công việc liên quan")).toBeInTheDocument();
    const section = screen.getByText("Công việc liên quan").closest("section")!;

    expect(within(section).getByText("Hảo")).toBeInTheDocument();
    expect(within(section).getByText("Đức")).toBeInTheDocument();
    // Two different facts, never collapsed: the item's own state, and whether
    // anybody's contribution on it is counted.
    expect(within(section).getByText("Đã ghi nhận")).toBeInTheDocument();
    expect(within(section).getByText("Chờ xác nhận độc lập")).toBeInTheDocument();

    const link = within(section).getByRole("link", { name: "Kịch bản video ngắn" });
    expect(link).toHaveAttribute("href", `/pr/work?item=${workItem().id}`);
  });

  it("says nothing at all to a lead or a writer when the content produced no work", async () => {
    stubFetch(contentRoutes([], LEAD));
    renderWithQuery(<ContentDetailPage />);
    await openOverview();

    expect(await screen.findAllByText(CONTENT.title)).not.toHaveLength(0);
    // An empty section would be a permanent blank panel on every idea nobody
    // has written yet - the absence is the honest rendering for somebody who
    // could do nothing about it anyway.
    expect(screen.queryByText("Công việc liên quan")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Đồng bộ lại từ Nội dung" })).toBeNull();
  });
});

// --- FINAL CONTENT → WORK SEMANTICS: THE CONTENT'S OWN SYNC --------------------
//
// The one place a per-content sync outlives the work: a result an
// administrator removed has no card any more, and this piece is still the
// thing to ask. ADMIN/OWNER only; the button is the canonical projector.

describe("resync. the content page offers Đồng bộ lại từ Nội dung to an owner", () => {
  const PROJECT = `/api/pr/work/content/${CONTENT.id}/project`;
  const projection = (outcome: string) => ({
    content_id: CONTENT.id,
    content_code: CONTENT.code,
    outcome,
    results: [],
  });

  it("draws the section, the standing and the button even when nothing is recorded", async () => {
    const fetchMock = stubFetch([
      { match: PROJECT, method: "POST", body: projection("PROJECTED") },
      ...contentRoutes([]),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await openOverview();

    const section = (await screen.findByText("Công việc liên quan")).closest("section")!;
    expect(within(section).getByText("Chưa ghi nhận")).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Đồng bộ lại từ Nội dung" }));
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "Đã đồng bộ công việc từ Nội dung.",
    );
    const posts = (fetchMock as unknown as { calls: Array<{ url: string; method: string }> }).calls
      .filter((one) => one.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain(PROJECT);
  });

  it("says Đã đồng bộ when a counted result exists, and a refusal in Vietnamese", async () => {
    stubFetch([
      { match: PROJECT, method: "POST", body: projection("BLOCKED_BY_PERIOD") },
      ...contentRoutes([workItem()]),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await openOverview();
    const section = (await screen.findByText("Công việc liên quan")).closest("section")!;
    expect(within(section).getByText("Đã đồng bộ")).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Đồng bộ lại từ Nội dung" }));
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "Không thể đồng bộ vì kỳ đã đóng hoặc khóa.",
    );
  });

  it("offers no button to a lead even when work exists", async () => {
    stubFetch(contentRoutes([workItem()], LEAD));
    renderWithQuery(<ContentDetailPage />);
    await openOverview();
    await screen.findByText("Công việc liên quan");
    expect(screen.queryByRole("button", { name: "Đồng bộ lại từ Nội dung" })).toBeNull();
    expect(screen.queryByText(/Trạng thái:/)).toBeNull();
  });
});
