/**
 * M4B - việc định kỳ, after the post-M4 UX consolidation moved it.
 *
 * **There is no *Định kỳ* tab any more.** A routine is created in *Công việc →
 * Giao công việc* by answering *Hình thức: Định kỳ*, and the routines that
 * exist are read and driven from *Quản lý việc định kỳ* on the same screen. The
 * two helpers at the top of this file are where that shows: `openForm` goes
 * through the assignment form, and `openManagement` opens the panel.
 *
 * Everything below the navigation is M4B unchanged - the same endpoints, the
 * same `can_*` flags, the same server-owned preview.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * The schedule sentence and the dates come from `POST /preview`; the state
 * labels come from `status_label`; every lifecycle button is drawn from a
 * `can_*` the server computed. Nothing on this screen works out when a routine
 * will fire.
 *
 * The four that carry the milestone
 * ----------------------------------
 *
 * **Test 2** - the form offers no cron, no RRULE, no source key, no cursor and
 * no occurrence internals. That absence *is* the product decision, so it is
 * asserted rather than left to a code review.
 *
 * **Test 4** - the preview is the server's. The screen renders what `/preview`
 * returned, and a browser that computed dates would be a second implementation
 * of the calendar - the one that would be wrong on a daylight-saving boundary.
 *
 * **Test 6** - activating asks first, and the sentence says both halves of what
 * activation means: work will appear under the caller's name, and it still has
 * to be confirmed by somebody else. "Bật lịch" is exactly the act somebody
 * assumes is only a schedule.
 *
 * **Test 9** - the occurrence history prints the server's `state_label` and
 * `message`, so a firing declined because its month was closed reads as a
 * sentence rather than as `SKIPPED_CLOSED_PERIOD`.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";

type Route = { match: string; status?: number; body?: unknown; method?: string };

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const MANAGER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const HAO = "22222222-2222-2222-2222-222222222222";
const LINH = "33333333-3333-3333-3333-333333333333";
const TEMPLATE = "77777777-7777-7777-7777-777777777777";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

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

/** `QUANTITY`, in comments. The archetype: "100 comment mỗi ngày". */
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
  display_order: 1,
};

const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  { user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" },
  { user_id: LINH, full_name: "Linh", role: "EMPLOYEE" },
];

const template = (over: Record<string, unknown> = {}) => ({
  id: TEMPLATE,
  name: "Seeding 100 bình luận",
  description: null,
  work_type_id: SEEDING_TYPE.id,
  work_type_name: SEEDING_TYPE.name,
  work_type_unit_label: "bình luận",
  assignment_mode: "SEPARATE_PER_ASSIGNEE",
  quantity: "100",
  priority: "NORMAL",
  priority_label: "Bình thường",
  frequency: "DAILY",
  frequency_label: "Hằng ngày",
  weekdays: [],
  day_of_month: null,
  run_time: "09:00:00",
  due_after_hours: 8,
  start_date: "2026-09-01",
  end_date: null,
  status: "ACTIVE",
  status_label: "Đang chạy",
  contributor_user_ids: [HAO, LINH],
  contributor_names: { [HAO]: "Hảo", [LINH]: "Linh" },
  schedule_label: "09:00 mỗi ngày",
  next_occurrences: ["2026-09-05T02:00:00Z", "2026-09-06T02:00:00Z"],
  generated_work_items: 6,
  unsettled_occurrences: 0,
  occurrence_count: 3,
  activated_at: "2026-09-01T02:00:00Z",
  activated_by_user_id: LINH,
  can_activate: false,
  can_pause: true,
  can_resume: false,
  can_end: true,
  can_edit: true,
  can_delete: false,
  ...over,
});

const occurrence = (over: Record<string, unknown> = {}) => ({
  id: "88888888-8888-8888-8888-888888888881",
  scheduled_for: "2026-09-03T02:00:00Z",
  state: "GENERATED",
  state_label: "Đã tạo việc",
  work_item_count: 2,
  attempts: 1,
  message: null,
  generated_at: "2026-09-03T02:00:05Z",
  template_revision_no: 1,
  ...over,
});

const preview = (over: Record<string, unknown> = {}) => ({
  schedule_label: "09:00 mỗi thứ Hai, thứ Tư",
  next_occurrences: ["2026-09-07T02:00:00Z", "2026-09-09T02:00:00Z"],
  ...over,
});

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  templates: Array<Record<string, unknown>>,
  {
    capabilities = MANAGER,
    types = [ROUTINE_TYPE, SEEDING_TYPE],
    extra = [] as Route[],
  } = {},
) => [
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/work/types", body: types },
  // Post-M4 consolidation. The assignment form asks this diagnostic, and this
  // file now reaches the recurring fields *through* that form. Answered
  // "everything is configured" so the two guidance sentences stay off screen -
  // the tests that are about them live in `manual-work.test.tsx`.
  {
    match: "/api/pr/work/readiness",
    body: {
      work_type_id: SEEDING_TYPE.id,
      work_type_name: SEEDING_TYPE.name,
      has_scoring_rule: true,
      period_id: "66666666-6666-6666-6666-666666666666",
      period_label: "2026-09",
      assignees: [],
      assignees_without_quota: 0,
    },
  },
  { match: "/api/pr/work/recurring/preview", body: preview(), method: "POST" },
  ...extra,
  { match: "/api/pr/work/recurring", body: { items: templates } },
  { match: "/api/pr/work/summary", body: {} },
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
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

/**
 * The view tabs, scoped by their nav's own label.
 *
 * Necessary rather than fussy: the ledger's **source filter** offers a button
 * called "Định kỳ" too - work generated by a template, as opposed to typed by a
 * person - and an unscoped query would match whichever rendered first. The two
 * navs carry `aria-label`s precisely so a person using a screen reader can tell
 * them apart, and the test uses the same handle.
 */
const viewTabs = () => within(screen.getByRole("navigation", { name: "Chế độ xem" }));

/** One view tab, once the dashboard has said which the caller may see. */
async function viewTab(name: string): Promise<HTMLElement> {
  await screen.findByRole("navigation", { name: "Chế độ xem" });
  return viewTabs().findByRole("button", { name });
}

/** Open *Quản lý việc định kỳ*, the panel that lists the routines. */
async function openManagement() {
  renderWithQuery(<WorkPage />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Quản lý việc định kỳ" }),
  );
  return screen.findByRole("region", { name: "Quản lý việc định kỳ" });
}

/**
 * Open the assignment form and switch it to *Định kỳ*. **The start of every
 * form test, and the shape of the consolidation:** there is one entry point for
 * assigning work, and the recurrence fields are revealed inside it.
 */
async function openForm(type = SEEDING_TYPE) {
  renderWithQuery(<WorkPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
  const heading = await screen.findByRole("heading", { name: "Giao công việc" });
  const form = heading.closest("form") as HTMLElement;
  await userEvent.click(within(form).getByLabelText(/Định kỳ/));
  await userEvent.selectOptions(within(form).getByLabelText("Loại công việc"), type.id);
  return form;
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-3: THE TAB AND THE FORM ---------------------------------------------

describe("1. the entry point", () => {
  it("is no longer a top-level tab", async () => {
    // **The consolidation, asserted.** A manager assigning work answers "một
    // lần or định kỳ", not "which module am I in".
    stubFetch(routes([template()]));
    renderWithQuery(<WorkPage />);
    await viewTab("Hiệu suất");
    expect(viewTabs().queryByRole("button", { name: "Định kỳ" })).toBeNull();
  });

  it("is a panel a manager opens from the ledger", async () => {
    stubFetch(routes([template()]));
    expect(await openManagement()).toBeInTheDocument();
  });

  it("is not offered without PR_WORK_MANAGE", async () => {
    // Setting up the department's routines is the same capability as assigning
    // one job by hand. Merging the two forms must not widen either. The server
    // refuses anyway; not drawing the button is a courtesy.
    stubFetch(routes([], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    // Waited on a control everybody gets, so the absence below is "the
    // dashboard answered" rather than "it has not loaded yet".
    await screen.findByRole("button", { name: "Đề xuất công việc" });
    expect(screen.queryByRole("button", { name: "Quản lý việc định kỳ" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Giao công việc" })).toBeNull();
  });

  it("says a routine only generates work, and never validates it", async () => {
    stubFetch(routes([template()]));
    await openManagement();
    expect(
      await screen.findByText(/Bật việc định kỳ không phải là xác nhận công việc/i),
    ).toBeInTheDocument();
  });

  it("creates from the assignment form, not from the management panel", async () => {
    // A second create button here would rebuild the split this change removed.
    stubFetch(routes([template()]));
    const panel = await openManagement();
    expect(
      within(panel).queryByRole("button", { name: /^Tạo/ }),
    ).toBeNull();
    expect(within(panel).getByText(/Tạo việc định kỳ mới ở/)).toBeInTheDocument();
  });
});

describe("2. the form asks a manager's questions and no scheduler's", () => {
  it("offers the whole product form", async () => {
    stubFetch(routes([]));
    const form = await openForm();

    expect(within(form).getByLabelText("Tên công việc")).toBeInTheDocument();
    expect(within(form).getByLabelText("Loại công việc")).toBeInTheDocument();
    expect(within(form).getByLabelText("Hảo")).toBeInTheDocument();
    expect(within(form).getByText("Cách giao")).toBeInTheDocument();
    expect(within(form).getByText("Tần suất")).toBeInTheDocument();
    expect(within(form).getByLabelText("Giờ tạo")).toBeInTheDocument();
    expect(within(form).getByLabelText("Hạn hoàn thành (giờ)")).toBeInTheDocument();
    expect(within(form).getByLabelText("Ngày bắt đầu")).toBeInTheDocument();
    expect(within(form).getByLabelText("Ngày kết thúc (không bắt buộc)")).toBeInTheDocument();
    expect(within(form).getByLabelText("Mô tả")).toBeInTheDocument();
  });

  it("exposes no cron, no RRULE, no source key, no cursor and no score", async () => {
    // **The product decision, asserted.** Every one of these is either a
    // scheduler internal a field would let somebody corrupt, or M6's arithmetic.
    stubFetch(routes([]));
    const form = await openForm();

    expect(within(form).queryByText(/cron|rrule|source[_ ]key|cursor|con trỏ/i)).toBeNull();
    expect(within(form).queryByText(/điểm|workload|phút chuẩn|hiệu suất/i)).toBeNull();
  });

  it("asks how to assign even for one person, unlike the one-off form", async () => {
    // On a template the mode is a **stored** fact that survives until the next
    // occurrence, so it has to be answered now - a second person added next
    // month would otherwise inherit whatever the field defaulted to.
    stubFetch(routes([]));
    const form = await openForm();
    await userEvent.click(within(form).getByLabelText("Hảo"));

    expect(within(form).getByText("Cách giao")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Mỗi người một công việc/)).toBeInTheDocument();
  });

  it("requires a quantity only when the work type is measured by one", async () => {
    // Post-M4 consolidation. The field is **shared** with the one-off form and
    // so is always drawn; what the work type decides is whether it is required
    // and what unit it is counted in. A `QUANTITY` type filed without a number
    // reports as zero comments against a plan expressed in comments.
    stubFetch(routes([]));
    const form = await openForm(SEEDING_TYPE);
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Seeding");
    await userEvent.click(within(form).getByLabelText("Hảo"));

    expect(within(form).getByLabelText("Số lượng (bình luận)")).toBeInTheDocument();
    expect(
      within(form).getByRole("button", { name: "Tạo việc định kỳ" }),
    ).toBeDisabled();
    // And it says a hundred comments is one job of a hundred, per firing.
    expect(within(form).getByText(/cho mỗi lần chạy/)).toBeInTheDocument();

    await userEvent.selectOptions(
      within(form).getByLabelText("Loại công việc"),
      ROUTINE_TYPE.id,
    );
    // Counted by job rather than by quantity: the unit follows the type, and
    // the routine is complete without a number.
    expect(within(form).getByLabelText("Số lượng (đầu việc)")).toBeInTheDocument();
    expect(
      within(form).getByRole("button", { name: "Tạo việc định kỳ" }),
    ).toBeEnabled();
  });
});

describe("3. the frequency decides which parameters are asked", () => {
  it("asks for weekdays only when weekly, and a date only when monthly", async () => {
    stubFetch(routes([]));
    const form = await openForm();

    // Daily: neither.
    expect(within(form).queryByLabelText("T2")).toBeNull();
    expect(within(form).queryByLabelText("Ngày trong tháng")).toBeNull();

    await userEvent.click(within(form).getByLabelText(/Hằng tuần/));
    expect(within(form).getByLabelText("T2")).toBeInTheDocument();
    expect(within(form).queryByLabelText("Ngày trong tháng")).toBeNull();

    await userEvent.click(within(form).getByLabelText(/Hằng tháng/));
    expect(within(form).queryByLabelText("T2")).toBeNull();
    expect(within(form).getByLabelText("Ngày trong tháng")).toBeInTheDocument();
    // And the shorter-month rule is stated where the field is, not in a manual.
    expect(within(form).getByText(/lấy ngày cuối tháng/i)).toBeInTheDocument();
  });
});

// --- 4: THE PREVIEW --------------------------------------------------------

describe("4. the preview is the server's", () => {
  it("renders the sentence the server returned", async () => {
    const calls = stubFetch(routes([]));
    const form = await openForm();
    await userEvent.click(within(form).getByLabelText(/Hằng tuần/));
    await userEvent.click(within(form).getByLabelText("T2"));
    await userEvent.click(within(form).getByLabelText("Hảo"));

    // The sentence is `schedule_label`, verbatim. The browser composed none of
    // it - a second implementation of the calendar is the one that would be
    // wrong on a daylight-saving boundary.
    expect(await within(form).findByText("09:00 mỗi thứ Hai, thứ Tư")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        calls.mock.calls.some(([url]) =>
          String(url).includes("/api/pr/work/recurring/preview"),
        ),
      ).toBe(true),
    );
  });

  it("names the frequency and the time before the server has answered, and no dates", async () => {
    // The placeholder deliberately computes nothing. It says what was chosen and
    // stops, so nothing on this screen is ever a second opinion about *when*.
    stubFetch(routes([]));
    const form = await openForm();
    expect(within(form).getByText("09:00 mỗi ngày")).toBeInTheDocument();
    expect(within(form).queryByText(/Các lần chạy tới/)).toBeNull();
  });
});

// --- 5-7: LIFECYCLE --------------------------------------------------------

describe("5. the lifecycle buttons come from the server", () => {
  it("draws only what the can_* flags allow", async () => {
    stubFetch(routes([template()]));
    await openManagement();
    await screen.findByText("Seeding 100 bình luận");

    // `ACTIVE`: pause and end, never activate or resume.
    expect(screen.getByRole("button", { name: "Tạm dừng" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Bật chạy/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Chạy lại/ })).toBeNull();
  });

  it("offers Chạy lại for a paused routine and Bật chạy for a draft", async () => {
    stubFetch(
      routes([
        template({
          id: "99999999-9999-9999-9999-999999999991",
          name: "Đang tạm dừng",
          status: "PAUSED",
          status_label: "Tạm dừng",
          can_pause: false,
          can_resume: true,
        }),
        template({
          id: "99999999-9999-9999-9999-999999999992",
          name: "Bản nháp",
          status: "DRAFT",
          status_label: "Nháp",
          can_pause: false,
          can_activate: true,
          can_delete: true,
          generated_work_items: 0,
          occurrence_count: 0,
        }),
      ]),
    );
    await openManagement();
    await screen.findByText("Đang tạm dừng");

    expect(screen.getByRole("button", { name: "Chạy lại" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bật chạy Bản nháp/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Xoá Bản nháp/ })).toBeInTheDocument();
  });

  it("never offers to delete a routine that has run", async () => {
    stubFetch(routes([template()]));
    await openManagement();
    await screen.findByText("Seeding 100 bình luận");
    expect(screen.queryByRole("button", { name: /^Xoá/ })).toBeNull();
  });
});

describe("6. activating asks first, and says what it means", () => {
  it("names both halves: work under your name, still confirmed by somebody else", async () => {
    const calls = stubFetch(
      routes(
        [
          template({
            status: "DRAFT",
            status_label: "Nháp",
            can_activate: true,
            can_pause: false,
          }),
        ],
        {
          extra: [
            {
              match: `/api/pr/work/recurring/${TEMPLATE}/activate`,
              body: template(),
              method: "POST",
            },
          ],
        },
      ),
    );
    await openManagement();
    await userEvent.click(await screen.findByRole("button", { name: /Bật chạy/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/bạn là người giao/i)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/cần một người khác xác nhận khi hoàn thành/i),
    ).toBeInTheDocument();

    await confirm();
    await waitFor(() =>
      expect(calls.mock.calls.some(([url]) => String(url).includes("/activate"))).toBe(true),
    );
  });

  it("says an ended routine cannot be restarted, and that its work stays", async () => {
    stubFetch(routes([template()]));
    await openManagement();
    await userEvent.click(await screen.findByRole("button", { name: /Kết thúc/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/không bật lại được/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/vẫn giữ nguyên và vẫn phải làm/i)).toBeInTheDocument();
  });
});

describe("7. pausing does not ask", () => {
  it("sends the call directly", async () => {
    // Reversible in one click, changes no work already generated, and pausing is
    // what a manager does in a hurry. The paused interval is never backfilled,
    // so pressing it by mistake creates no unwanted work.
    const calls = stubFetch(
      routes([template()], {
        extra: [
          {
            match: `/api/pr/work/recurring/${TEMPLATE}/pause`,
            body: template({ status: "PAUSED" }),
            method: "POST",
          },
        ],
      }),
    );
    await openManagement();
    await userEvent.click(await screen.findByRole("button", { name: "Tạm dừng" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() =>
      expect(calls.mock.calls.some(([url]) => String(url).includes("/pause"))).toBe(true),
    );
  });
});

// --- 8-10: WHAT THE LIST AND THE HISTORY SAY -------------------------------

describe("8. the list", () => {
  it("prints the server's schedule sentence and state label", async () => {
    stubFetch(routes([template()]));
    await openManagement();

    // Scoped to the card: the status filter above the list offers "Đang chạy"
    // as a choice, and the badge on the routine is a different statement.
    const card = (await screen.findByText("Seeding 100 bình luận")).closest(
      "article",
    ) as HTMLElement;
    expect(within(card).getByText(/09:00 mỗi ngày/)).toBeInTheDocument();
    expect(within(card).getByText("Đang chạy")).toBeInTheDocument();
    // Never a raw state token.
    expect(screen.queryByText("ACTIVE")).toBeNull();
  });

  it("warns only when a routine actually has unsettled firings", async () => {
    stubFetch(routes([template()]));
    await openManagement();
    await screen.findByText("Seeding 100 bình luận");
    // Zero is not printed: "0 lần chạy chưa xử lý xong" on every healthy routine
    // is how the one that matters stops being noticed.
    expect(screen.queryByText(/chưa xử lý xong/)).toBeNull();
  });

  it("names the backlog when there is one", async () => {
    stubFetch(routes([template({ unsettled_occurrences: 3 })]));
    await openManagement();
    expect(await screen.findByText(/3 lần chạy chưa xử lý xong/)).toBeInTheDocument();
  });
});

describe("9. the occurrence history answers 'why is there no work for Tuesday'", () => {
  it("prints the server's state label and message, never a raw token", async () => {
    stubFetch(
      routes([template()], {
        extra: [
          {
            match: `/api/pr/work/recurring/${TEMPLATE}/occurrences`,
            body: {
              items: [
                occurrence(),
                occurrence({
                  id: "88888888-8888-8888-8888-888888888882",
                  scheduled_for: "2026-08-31T02:00:00Z",
                  state: "SKIPPED_CLOSED_PERIOD",
                  state_label: "Bỏ qua - kỳ báo cáo đã chốt",
                  work_item_count: 0,
                  generated_at: null,
                  message:
                    "Kỳ báo cáo 2026-08 đã chốt nên không tạo việc lùi ngày vào kỳ này.",
                }),
              ],
            },
          },
        ],
      }),
    );
    await openManagement();
    await userEvent.click(await screen.findByRole("button", { name: "Lịch sử chạy" }));

    expect(await screen.findByText("Đã tạo việc")).toBeInTheDocument();
    expect(screen.getByText("Bỏ qua - kỳ báo cáo đã chốt")).toBeInTheDocument();
    expect(
      screen.getByText(/Kỳ báo cáo 2026-08 đã chốt nên không tạo việc lùi ngày/),
    ).toBeInTheDocument();
    expect(screen.queryByText("SKIPPED_CLOSED_PERIOD")).toBeNull();
  });
});

describe("10. creating, from the one assignment form", () => {
  it("posts the routine and says it is a draft that has not started", async () => {
    const calls = stubFetch(
      routes([], {
        extra: [{ match: "/api/pr/work/recurring", body: template(), method: "POST" }],
      }),
    );
    const form = await openForm(SEEDING_TYPE);
    await userEvent.type(within(form).getByLabelText("Tên công việc"), "Seeding hằng ngày");
    await userEvent.click(within(form).getByLabelText("Hảo"));
    await userEvent.type(within(form).getByLabelText("Số lượng (bình luận)"), "100");

    // The sentence that stops somebody assuming they have just started a routine.
    expect(within(form).getByText(/chưa sinh việc/i)).toBeInTheDocument();

    await userEvent.click(within(form).getByRole("button", { name: "Tạo việc định kỳ" }));
    await waitFor(() => {
      const posted = calls.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/pr/work/recurring") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(posted).toBeDefined();
      const body = JSON.parse(String((posted?.[1] as RequestInit).body));
      expect(body.assignment_mode).toBe("SEPARATE_PER_ASSIGNEE");
      expect(body.frequency).toBe("DAILY");
      expect(body.quantity).toBe("100");
      // The scheduler's own fields are absent from the wire, not merely hidden.
      expect(body).not.toHaveProperty("status");
      expect(body).not.toHaveProperty("revision_no");
      expect(body).not.toHaveProperty("last_evaluated_occurrence_at");
    });
  });
});
