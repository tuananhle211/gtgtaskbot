/**
 * What every task table does, and what the Design ban's tables add.
 *
 * Every task table (`/tasks` in each variant, `/tasks/birthday`):
 * - a click anywhere on a row opens the task; a link, a button or the action
 *   cell keeps its own behaviour;
 * - the page size is a choice (10 / 20 / 50 / 100) kept on the URL (`?size=`);
 * - a double click on a column header sorts the rows on screen by that column:
 *   ascending, descending, off (`?sort=`), with a note that only this page is
 *   sorted.
 *
 * The Design ban's tables (the Design view of `/tasks`, the birthday board):
 * - "Thiết kế" also says when the design link was handed in;
 * - "Nhận việc" / "Nộp bài" / "Duyệt" sit on the row, by the kinds in the
 *   row's `actions`, and open the task page's own dialog.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TasksPage from "@/app/tasks/page";
import BirthdayOrdersPage from "@/app/tasks/birthday/page";
import {
  nextSort,
  pageSizeOf,
  parseSort,
  sortRows,
  sortTime,
} from "@/components/task-table";
import { findTaskAction } from "@/components/task-actions";
import type { TaskRow, UnifiedTaskDetail } from "@/lib/api";
import { confirm, renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const PUSHED: string[] = [];
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => {
      PUSHED.push(url);
    },
    refresh: vi.fn(),
    back: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  PUSHED.length = 0;
  pathname = "/tasks";
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const listCalls = (stub: unknown) =>
  calls(stub).filter((call) => call.url.includes("/api/board/tasks"));
const asked = (call: Call) => new URLSearchParams(call.url.split("?")[1]);

const ADS_SETTINGS = {
  urgent_days: 7,
  media_nas_url: null,
  design_nas_url: null,
  telegram_enabled: false,
  review_bien_tap: false,
  review_thiet_ke: false,
  review_dung: false,
  review_video_by_script_lead: false,
  effort_enabled: true,
  default_daily_tokens: 8,
  perf_weights: { output: 0.5, on_time: 0.3, quality: 0.2 },
  output_target: null,
};

type Design = { view: boolean; manage_codes: boolean; birthday: boolean };
const DESIGNER: Design = { view: true, manage_codes: false, birthday: true };

const ME_AS = (role: string, design?: Design, code = "ADS") => ({
  units: [
    {
      code,
      label: code === "ADS" ? "Luồng Order (ORD)" : "Luồng PR",
      role,
      role_label: role,
      is_lead: false,
      member_code: "MY",
      personal_nas_url: null,
      settings: ADS_SETTINGS,
    },
  ],
  default_unit: code,
  can_view_all: false,
  can_admin: [],
  effort_enabled: true,
  ...(design ? { design } : {}),
});

const TKBTS = {
  code_id: "c0000000-0000-4000-8000-000000000001",
  code: "TKBTS",
  name: "SOCIAL - Thiết kế Banner, Thumb Video, Avatar",
  quantity: 3,
};

const ROW: TaskRow = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  id: "o-1",
  code: "TUAN-BT-261012-01",
  title: "Banner khai trương",
  kind: "BT",
  kind_label: "Biên kịch › Design",
  owner_user_id: "u-owner",
  owner_name: "Tuấn",
  created_at: "2026-10-12T02:00:00Z",
  phase: "PRODUCTION",
  phase_label: "Sản xuất",
  status: "THIET_KE",
  status_label: "Design · Đang làm",
  cells: [
    {
      key: "THIET_KE",
      label: "Design",
      person_name: "Diễm My",
      status: "DANG_LAM",
      status_label: "Đang làm",
      is_current: true,
      since: null,
      revisions: 0,
    },
  ],
  product_link: null,
  returned_at: null,
  is_priority: false,
  urgent: false,
  detail_path: "/tasks/TUAN-BT-261012-01",
  version: 4,
  stage_since: null,
  revisions: 0,
  current_person_name: "Diễm My",
  latest_link: null,
  extras: [],
  deadline_at: "2026-10-20T10:00:00Z",
  deadline_status: "ON_TRACK",
  category: "STANDARD",
  design: {
    script_link: "https://docs.example.com/kich-ban",
    script_by: "Hà Biên Kịch",
    reference_link: null,
    note: null,
    codes: [TKBTS],
    proposed_codes: null,
    designer: "Diễm My",
    design_link: "https://drive.example.com/thiet-ke",
    design_submitted_at: "2026-10-12T03:30:00Z",
  },
  actions: [],
};

/** A second and a third row, for sorting: other titles, deadlines, designers. */
const row = (over: Partial<TaskRow> & { n: number }): TaskRow => {
  const { n, ...rest } = over;
  return {
    ...ROW,
    id: `o-${n}`,
    code: `TUAN-BT-261012-0${n}`,
    detail_path: `/tasks/TUAN-BT-261012-0${n}`,
    ...rest,
  };
};

const PAGE = (items: TaskRow[], total = items.length) => ({
  unit: "ADS",
  items,
  total,
  limit: 10,
  offset: 0,
  phases: [],
});
const COUNTS = { mine: 2, awaiting_me: 1, priority: 0, urgent: 0, overdue: 0 };

type Routes = Parameters<typeof stubFetch>[0];

const tasksPage = (me: unknown, rows: TaskRow[], extra: Routes = [], total?: number) => {
  const unit = (me as { default_unit: string }).default_unit;
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
    { match: "/api/units/me", body: me },
    {
      match: `/api/units/${unit}/members`,
      body: { unit, unit_label: unit, members: [], assignable_roles: [] },
    },
    { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
    { match: "/api/units/ADS/design-codes", body: { codes: [] } },
    { match: "/api/board/counts", body: COUNTS },
    { match: "/api/board/tasks", body: PAGE(rows, total) },
  ]);
  renderWithQuery(<TasksPage />);
  return stub;
};

const BIRTHDAY_ROW: TaskRow = {
  ...ROW,
  id: "o-b1",
  code: "MY-T-261012-01",
  title: "Trần Diễm My",
  kind: "T",
  kind_label: "Design",
  detail_path: "/tasks/MY-T-261012-01",
  category: "BIRTHDAY",
  design: {
    script_link: null,
    script_by: null,
    reference_link: "https://drive.example.com/anh-my",
    note: "Tông hồng",
    codes: [],
    proposed_codes: null,
    designer: "Quang Design",
    design_link: "https://drive.example.com/thiep-my",
    design_submitted_at: "2026-10-11T09:00:00Z",
  },
  birthday: {
    full_name: "Trần Diễm My",
    branch: "VP - Hà Nội",
    gender: "FEMALE",
    birthday_on: "1998-10-20",
  },
};

const birthdayRow = (
  n: number,
  birthday: NonNullable<TaskRow["birthday"]>,
  over: Partial<TaskRow> = {},
): TaskRow => ({
  ...BIRTHDAY_ROW,
  id: `o-b${n}`,
  code: `MY-T-261012-0${n}`,
  detail_path: `/tasks/MY-T-261012-0${n}`,
  title: birthday.full_name,
  birthday,
  ...over,
});

const birthdayPage = (rows: TaskRow[], extra: Routes = []) => {
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
    { match: "/api/units/me", body: ME_AS("THIET_KE", DESIGNER) },
    { match: "/api/units/ADS/design-codes", body: { codes: [] } },
    { match: "/api/orders/birthday/options", body: { branches: [] } },
    { match: "/api/board/counts", body: COUNTS },
    { match: "/api/board/tasks", body: PAGE(rows) },
  ]);
  pathname = "/tasks/birthday";
  renderWithQuery(<BirthdayOrdersPage />);
  return stub;
};

const TASK_ID = "7c1d6f0e-bbbb-4c3b-9a3e-0000000000d1";

/** The task behind `ROW`, with whatever actions the test wants on it. */
const DETAIL = (
  actions: UnifiedTaskDetail["actions"],
  code = ROW.code,
  version = 5,
): UnifiedTaskDetail => ({
  task: {
    id: TASK_ID,
    unit: "ADS",
    unit_label: "Luồng Order (ORD)",
    code,
    title: "Banner khai trương",
    kind: "BT",
    kind_label: "Biên kịch › Design",
    phase: "PRODUCTION",
    phase_label: "Sản xuất",
    stage: "THIET_KE",
    stage_label: "Design · Đang làm",
    state: "DANG_LAM",
    owner: { user_id: "u-owner", name: "Tuấn Marketing" },
    current_person: { user_id: SESSION.user_id, name: "Diễm My" },
    is_priority: false,
    urgent: false,
    created_at: "2026-10-12T02:00:00+00:00",
    updated_at: "2026-10-12T03:00:00+00:00",
    stage_since: "2026-10-12T03:00:00+00:00",
    finished_at: null,
    product_link: null,
    latest_link: null,
    revisions: 0,
    version,
    source: { type: "ORDER", id: "o-1" },
  },
  steps: [],
  people: [],
  fields: [],
  submissions: [],
  timeline: [],
  actions,
});

const HINT = "Sửa mã video thì bài nộp cần Trưởng phòng Design duyệt.";
type Action = UnifiedTaskDetail["actions"][number];

const HAND_IN: Action = {
  key: "ads:SUBMIT_WORK:n-tk",
  label: "Nộp bài · Design",
  emphasis: "PRIMARY",
  requires_note: false,
  inputs: ["link", "design_codes"],
  required_inputs: ["link"],
  assignee_options: [],
  design_codes: [TKBTS],
  hint: HINT,
};
const APPROVE: Action = {
  key: "ads:APPROVE_NODE:n-tk",
  label: "Duyệt · Design",
  emphasis: "PRIMARY",
  requires_note: false,
  inputs: ["note"],
  assignee_options: [],
};
const RETURN: Action = {
  key: "ads:RETURN_NODE:n-tk",
  label: "Trả sửa · Design",
  emphasis: "DANGER",
  requires_note: true,
  inputs: ["note"],
  assignee_options: [],
};
const ACCEPT: Action = {
  key: "ads:ACCEPT:n-tk",
  label: "Nhận việc · Design",
  emphasis: "PRIMARY",
  requires_note: false,
  inputs: [],
  assignee_options: [],
};

const actionCall = (stub: unknown) =>
  calls(stub).find((call) => call.method === "POST" && call.url.endsWith("/actions"));

const bodyRows = () => screen.getAllByRole("row").slice(1);
const codesInOrder = () =>
  bodyRows().map((line) => within(line).getAllByRole("link")[0].textContent);
const header = (name: RegExp | string) => screen.getByRole("columnheader", { name });

// --- 1. The hand-in date ---------------------------------------------------------

describe('the hand-in date in "Thiết kế"', () => {
  it("is drawn under the design link on the Design table", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [ROW]);
    const cell = await screen.findByTestId("design-designer");
    // The app's short day (`formatShortDay`, Việt Nam time): "12/10" or "12-10" by the ICU at hand.
    const chip = within(cell).getByTestId("design-submitted");
    expect(chip).toHaveTextContent(/^Nộp 12[/-]10$/);
    expect(chip).toHaveAttribute("title", expect.stringContaining("Nộp link thiết kế"));
  });

  it("is not drawn while nothing was handed in", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [
      { ...ROW, design: { ...ROW.design, design_link: null, design_submitted_at: null } },
    ]);
    const cell = await screen.findByTestId("design-designer");
    expect(within(cell).queryByTestId("design-submitted")).not.toBeInTheDocument();
  });

  it("is drawn on the birthday board too, and not by an API without the field", async () => {
    const old = { ...BIRTHDAY_ROW.design };
    delete old.design_submitted_at;
    birthdayPage([
      BIRTHDAY_ROW,
      birthdayRow(2, { ...BIRTHDAY_ROW.birthday!, full_name: "Nguyễn Văn An" }, { design: old }),
    ]);
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    const [first, second] = screen.getAllByTestId("design-designer");
    expect(within(first).getByTestId("design-submitted")).toHaveTextContent(/^Nộp 11[/-]10$/);
    expect(within(second).queryByTestId("design-submitted")).not.toBeInTheDocument();
  });
});

// --- 2. The inline actions ---------------------------------------------------------

describe("the step's actions on a row", () => {
  it("offers a button per kind the row lists, and only the four inline kinds", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [
      row({ n: 1, actions: ["SUBMIT_WORK"] }),
      row({ n: 2, actions: ["APPROVE_NODE", "RETURN_NODE"] }),
      row({ n: 3, actions: ["ACCEPT"] }),
      row({ n: 4, actions: ["ASSIGN", "SET_NODE_PLAN"] }),
      row({ n: 5, actions: [] }),
      row({ n: 6, actions: undefined }),
    ]);
    await screen.findByRole("link", { name: "TUAN-BT-261012-01" });
    const buttons = bodyRows().map((line) =>
      within(line)
        .queryAllByRole("button")
        .map((button) => button.textContent),
    );
    expect(buttons).toEqual([["Nộp bài"], ["Duyệt"], ["Nhận việc"], ["Giao việc"], [], []]);
    // Nothing else sits in the action cell: no "Mở", the row itself opens the task.
    expect(screen.queryByRole("link", { name: "Mở" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nộp bài TUAN-BT-261012-01" })).toBeInTheDocument();
  });

  it("offers none on the ordinary table, whatever the row lists", async () => {
    tasksPage(ME_AS("ORDERER"), [row({ n: 1, actions: ["SUBMIT_WORK", "APPROVE_NODE", "ACCEPT"] })]);
    await screen.findByRole("link", { name: "TUAN-BT-261012-01" });
    for (const name of [/Nộp bài/, /Duyệt/, /Nhận việc/]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("opens the task page's dialog and posts that action's key", async () => {
    const stub = tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: ["SUBMIT_WORK"] })], [
      { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: DETAIL([]) },
      { match: `/api/tasks/${ROW.code}`, body: DETAIL([RETURN, HAND_IN]) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Nộp bài TUAN-BT-261012-01" }));
    // The same dialog: the action's own title, its inputs, the picker and its hint.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Nộp bài · Design · TUAN-BT-261012-01?");
    expect(within(dialog).getByText(HINT)).toBeInTheDocument();
    expect(within(dialog).getByTestId("design-code-item")).toHaveTextContent("TKBTS");
    const accept = dialog.querySelector<HTMLButtonElement>("[data-confirm-accept]")!;
    // The same required rule: no link, no hand-in.
    expect(accept).toBeDisabled();
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Link sản phẩm (bắt buộc)" }),
      "https://drive.example.com/thiet-ke-v2",
    );
    expect(accept).toBeEnabled();
    const listsBefore = listCalls(stub).length;
    const countsBefore = calls(stub).filter((call) => call.url.includes("/api/board/counts")).length;
    await confirm();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCall(stub)!.url).toBe(`/api/tasks/${TASK_ID}/actions`);
    expect(actionCall(stub)!.body).toEqual({
      key: "ads:SUBMIT_WORK:n-tk",
      version: 5,
      link: "https://drive.example.com/thiet-ke-v2",
      design_codes: [{ code_id: TKBTS.code_id, quantity: 3 }],
    });
    // The board and its counts are read again.
    await waitFor(() => expect(listCalls(stub).length).toBeGreaterThan(listsBefore));
    await waitFor(() =>
      expect(
        calls(stub).filter((call) => call.url.includes("/api/board/counts")).length,
      ).toBeGreaterThan(countsBefore),
    );
    // Acting on a row is not opening it.
    expect(PUSHED).toEqual([]);
  });

  it('approves from the birthday board ("Duyệt")', async () => {
    const stub = birthdayPage([{ ...BIRTHDAY_ROW, actions: ["APPROVE_NODE", "RETURN_NODE"] }], [
      { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: DETAIL([], BIRTHDAY_ROW.code) },
      {
        match: `/api/tasks/${BIRTHDAY_ROW.code}`,
        body: DETAIL([APPROVE, RETURN], BIRTHDAY_ROW.code, 9),
      },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Duyệt MY-T-261012-01" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Duyệt · Design · MY-T-261012-01?");
    await confirm();
    await waitFor(() => expect(actionCall(stub)).toBeDefined());
    expect(actionCall(stub)!.body).toEqual({ key: "ads:APPROVE_NODE:n-tk", version: 9 });
    expect(screen.queryByRole("button", { name: /Trả sửa/ })).not.toBeInTheDocument();
  });

  it('takes the step from the row ("Nhận việc")', async () => {
    const stub = birthdayPage([{ ...BIRTHDAY_ROW, actions: ["ACCEPT"] }], [
      { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: DETAIL([], BIRTHDAY_ROW.code) },
      { match: `/api/tasks/${BIRTHDAY_ROW.code}`, body: DETAIL([ACCEPT], BIRTHDAY_ROW.code) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Nhận việc MY-T-261012-01" }));
    await screen.findByRole("dialog");
    await confirm();
    await waitFor(() => expect(actionCall(stub)).toBeDefined());
    expect(actionCall(stub)!.body).toEqual({ key: "ads:ACCEPT:n-tk", version: 5 });
  });

  it('hands the step out from the row ("Giao việc")', async () => {
    const assign: Action = {
      key: "ads:ASSIGN:n-tk",
      label: "Giao việc · Design",
      emphasis: "PRIMARY",
      requires_note: false,
      inputs: ["assignee"],
      required_inputs: [],
      assignee_options: [{ user_id: "u-designer-2", name: "Mạnh Tăng" }],
    };
    const stub = birthdayPage([{ ...BIRTHDAY_ROW, actions: ["ASSIGN", "SET_NODE_PLAN"] }], [
      { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: DETAIL([], BIRTHDAY_ROW.code) },
      { match: `/api/tasks/${BIRTHDAY_ROW.code}`, body: DETAIL([assign], BIRTHDAY_ROW.code) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Giao việc MY-T-261012-01" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByRole("combobox"), "u-designer-2");
    await confirm();
    await waitFor(() => expect(actionCall(stub)).toBeDefined());
    expect(actionCall(stub)!.body).toEqual({
      key: "ads:ASSIGN:n-tk",
      version: 5,
      assignee_user_id: "u-designer-2",
    });
  });

  it("says so and reads the list again when the action is gone", async () => {
    const stub = tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: ["SUBMIT_WORK"] })], [
      // Somebody else acted in between: the task no longer offers the hand-in.
      { match: `/api/tasks/${ROW.code}`, body: DETAIL([RETURN]) },
    ]);
    const button = await screen.findByRole("button", { name: "Nộp bài TUAN-BT-261012-01" });
    const before = listCalls(stub).length;
    await userEvent.click(button);
    expect(await screen.findByText(/Thao tác này không còn nữa/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listCalls(stub).length).toBeGreaterThan(before));
    expect(actionCall(stub)).toBeUndefined();
  });

  it("keeps the dialog open on a stale version, and reads the task again", async () => {
    const stub = tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: ["APPROVE_NODE"] })], [
      {
        match: `/api/tasks/${TASK_ID}/actions`,
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "order_stale_version",
            message: "Order vừa được người khác cập nhật.",
            details: { reason: "order_stale_version" },
          },
        },
      },
      { match: `/api/tasks/${ROW.code}`, body: DETAIL([APPROVE]) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Duyệt TUAN-BT-261012-01" }));
    const dialog = await screen.findByRole("dialog");
    const reads = () => calls(stub).filter((call) => call.url === `/api/tasks/${ROW.code}`).length;
    expect(reads()).toBe(1);
    await confirm();
    expect(
      await within(dialog).findByText("Order vừa được người khác cập nhật."),
    ).toBeInTheDocument();
    await waitFor(() => expect(reads()).toBe(2));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("finds an action by its kind, with or without a node", () => {
    const detail = DETAIL([RETURN, { ...APPROVE, key: "ads:APPROVE_NODE" }, HAND_IN]);
    expect(findTaskAction(detail, "SUBMIT_WORK")?.key).toBe("ads:SUBMIT_WORK:n-tk");
    expect(findTaskAction(detail, "APPROVE_NODE")?.key).toBe("ads:APPROVE_NODE");
    // A kind is matched whole: "ACCEPT" is not a prefix of something else.
    expect(findTaskAction(detail, "APPROVE")).toBeUndefined();
    expect(findTaskAction(detail, "ACCEPT")).toBeUndefined();
  });
});

// --- 3. Clicking a row ---------------------------------------------------------------

describe("a click on a row", () => {
  it("opens the task from anywhere that is not a control", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: ["SUBMIT_WORK"] })]);
    const line = (await screen.findByRole("link", { name: "TUAN-BT-261012-01" })).closest("tr")!;
    expect(line).toHaveClass("cursor-pointer");
    // No role, no tab stop: the row's own links stay the accessible way in.
    expect(line).not.toHaveAttribute("tabindex");
    expect(line).not.toHaveAttribute("role");
    await userEvent.click(within(line).getByText("Biên kịch › Design"));
    expect(PUSHED).toEqual(["/tasks/TUAN-BT-261012-01"]);
    await userEvent.click(within(line).getByTestId("design-codes"));
    expect(PUSHED).toHaveLength(2);
  });

  it("leaves links, buttons and the action cell alone", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: [] })]);
    const line = (await screen.findByRole("link", { name: "TUAN-BT-261012-01" })).closest("tr")!;
    // A Drive link opens the file, not the task.
    fireEvent.click(within(line).getByRole("link", { name: /Link thiết kế/ }));
    fireEvent.click(within(line).getByRole("link", { name: /Kịch bản/ }));
    // The row's own links navigate by themselves.
    fireEvent.click(within(line).getByRole("link", { name: "TUAN-BT-261012-01" }));
    // The action cell, around its controls.
    const actions = line.querySelector("[data-no-row-nav]")!;
    fireEvent.click(actions);
    // A modifier is for the links (a new tab), not for the row.
    fireEvent.click(within(line).getByTestId("design-codes"), { ctrlKey: true });
    expect(PUSHED).toEqual([]);
  });

  it("does not open the task when an inline action's dialog is clicked", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [row({ n: 1, actions: ["APPROVE_NODE"] })], [
      { match: `/api/tasks/${ROW.code}`, body: DETAIL([APPROVE]) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Duyệt TUAN-BT-261012-01" }));
    const dialog = await screen.findByRole("dialog");
    // The dialog is portalled out of the row; React still bubbles its clicks there.
    await userEvent.click(within(dialog).getByText(/Thao tác được ghi vào lịch sử/));
    expect(PUSHED).toEqual([]);
  });

  it("works on the ordinary table and on a PR table", async () => {
    tasksPage(ME_AS("MEMBER", undefined, "PR"), [
      { ...ROW, unit: "PR", code: "PR-0001", detail_path: "/tasks/PR-0001", design: null },
    ]);
    const line = (await screen.findByRole("link", { name: "PR-0001" })).closest("tr")!;
    await userEvent.click(within(line).getByText("Biên kịch › Design"));
    expect(PUSHED).toEqual(["/tasks/PR-0001"]);
  });

  it("works on the birthday board, outside its action cell", async () => {
    birthdayPage([BIRTHDAY_ROW]);
    const line = (await screen.findByRole("link", { name: "MY-T-261012-01" })).closest("tr")!;
    fireEvent.click(within(line).getByRole("link", { name: /Ảnh minh hoạ/ }));
    fireEvent.click(line.querySelector("[data-no-row-nav]")!);
    expect(PUSHED).toEqual([]);
    await userEvent.click(within(line).getByText("VP - Hà Nội"));
    expect(PUSHED).toEqual(["/tasks/MY-T-261012-01"]);
  });
});

// --- 4. The page size ---------------------------------------------------------------

describe("the page size", () => {
  const sizeOf = () => screen.getByRole("combobox", { name: "Số dòng" });

  it("is 10 unless chosen, and offers 10 / 20 / 50 / 100", async () => {
    const stub = tasksPage(ME_AS("ORDERER"), [ROW], [], 240);
    await screen.findByRole("link", { name: ROW.code });
    expect(sizeOf()).toHaveValue("10");
    expect(within(sizeOf()).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "10",
      "20",
      "50",
      "100",
    ]);
    expect(asked(listCalls(stub)[0]).get("limit")).toBe("10");
    expect(screen.getByRole("navigation", { name: "Phân trang" })).toHaveTextContent(
      "1–1 / 240 task · 10 dòng mỗi trang",
    );
  });

  it("changes the request's limit, goes back to page 1 and lives on the URL", async () => {
    SEARCH.value = new URLSearchParams("page=3&priority=true");
    const stub = tasksPage(ME_AS("ORDERER"), [ROW], [], 240);
    await screen.findByRole("link", { name: ROW.code });
    expect(asked(listCalls(stub)[0]).get("offset")).toBe("20");
    await userEvent.selectOptions(sizeOf(), "50");
    expect(SEARCH.value.get("size")).toBe("50");
    expect(SEARCH.value.get("page")).toBeNull();
    // The filters stay.
    expect(SEARCH.value.get("priority")).toBe("true");
    await waitFor(() => {
      const last = asked(listCalls(stub).at(-1)!);
      expect(last.get("limit")).toBe("50");
      expect(last.get("offset")).toBe("0");
      expect(last.get("priority")).toBe("true");
    });
    await waitFor(() => expect(sizeOf()).toHaveValue("50"));
    // Back to the default: the URL goes back to saying nothing.
    await userEvent.selectOptions(sizeOf(), "10");
    expect(SEARCH.value.get("size")).toBeNull();
  });

  it("is read from the URL on arrival, and pages by it", async () => {
    SEARCH.value = new URLSearchParams("size=20&page=3");
    const stub = tasksPage(ME_AS("ORDERER"), [ROW], [], 240);
    await screen.findByRole("link", { name: ROW.code });
    const first = asked(listCalls(stub)[0]);
    expect(first.get("limit")).toBe("20");
    expect(first.get("offset")).toBe("40");
    expect(sizeOf()).toHaveValue("20");
    // 240 rows at 20 a page: twelve pages, and the range starts at 41.
    const pager = screen.getByRole("navigation", { name: "Phân trang" });
    expect(pager).toHaveTextContent("41–41 / 240 task · 20 dòng mỗi trang");
    expect(within(pager).getByRole("button", { name: "12" })).toBeInTheDocument();
    expect(within(pager).queryByRole("button", { name: "13" })).not.toBeInTheDocument();
  });

  it("ignores a size that is not on offer", () => {
    expect(pageSizeOf("20")).toBe(20);
    expect(pageSizeOf("100")).toBe(100);
    expect(pageSizeOf("200")).toBe(10);
    expect(pageSizeOf("abc")).toBe(10);
    expect(pageSizeOf(null)).toBe(10);
  });

  it("is on the birthday board too", async () => {
    const stub = birthdayPage([BIRTHDAY_ROW]);
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    await userEvent.selectOptions(sizeOf(), "100");
    expect(SEARCH.value.get("size")).toBe("100");
    await waitFor(() => {
      const last = asked(listCalls(stub).at(-1)!);
      expect(last.get("limit")).toBe("100");
      // Still the birthday list, in the server's order.
      expect(last.get("category")).toBe("BIRTHDAY");
      expect(last.get("order")).toBe("birthday");
    });
  });

  it("survives clearing the filters, with the sort", async () => {
    SEARCH.value = new URLSearchParams("unit=ADS&size=50&sort=-title&priority=true&q=banner");
    tasksPage(ME_AS("ORDERER"), [ROW]);
    await screen.findByRole("link", { name: ROW.code });
    await userEvent.click(screen.getByRole("button", { name: /Xoá lọc/ }));
    expect(Object.fromEntries(SEARCH.value)).toEqual({ unit: "ADS", size: "50", sort: "-title" });
  });
});

// --- 5. Sorting by a column -----------------------------------------------------------

describe("a double click on a column header", () => {
  const ROWS = [
    row({ n: 1, title: "Đèn trung thu", deadline_at: "2026-10-20T10:00:00Z" }),
    row({ n: 2, title: "Ảnh bìa tháng 10", deadline_at: null }),
    row({ n: 3, title: "Dây chuyền mới", deadline_at: "2026-10-15T10:00:00Z" }),
    row({ n: 4, title: "banner khai trương", deadline_at: "2026-10-25T10:00:00Z" }),
  ];
  const serverOrder = ROWS.map((item) => item.code);
  const titles = () => bodyRows().map((line) => within(line).getAllByRole("link")[1].textContent);

  it("sorts a text column: ascending, descending, then the server's order", async () => {
    SEARCH.value = new URLSearchParams("page=2");
    const stub = tasksPage(ME_AS("ORDERER"), ROWS, [], 40);
    await screen.findByRole("link", { name: ROWS[0].code });
    const title = header(/Tên · Loại/);
    expect(title).toHaveAttribute("title", "Nhấp đúp để sắp xếp");
    expect(title).not.toHaveAttribute("aria-sort");
    expect(codesInOrder()).toEqual(serverOrder);
    expect(screen.queryByText("Đang sắp xếp trong trang này")).not.toBeInTheDocument();
    const requests = listCalls(stub).length;

    // A single click is not a sort.
    await userEvent.click(title);
    expect(codesInOrder()).toEqual(serverOrder);

    await userEvent.dblClick(title);
    // Vietnamese collation: A-with-marks with A, D before Đ, case ignored.
    expect(titles()).toEqual([
      "Ảnh bìa tháng 10",
      "banner khai trương",
      "Dây chuyền mới",
      "Đèn trung thu",
    ]);
    expect(header(/Tên · Loại/)).toHaveAttribute("aria-sort", "ascending");
    expect(header(/Tên · Loại/)).toHaveTextContent("Tên · Loại ▲");
    expect(SEARCH.value.get("sort")).toBe("title");
    // The page on screen is rearranged: no new page, no new request.
    expect(SEARCH.value.get("page")).toBe("2");
    expect(screen.getByRole("status")).toHaveTextContent("Đang sắp xếp trong trang này");

    await userEvent.dblClick(header(/Tên · Loại/));
    expect(titles()).toEqual([
      "Đèn trung thu",
      "Dây chuyền mới",
      "banner khai trương",
      "Ảnh bìa tháng 10",
    ]);
    expect(header(/Tên · Loại/)).toHaveAttribute("aria-sort", "descending");
    expect(header(/Tên · Loại/)).toHaveTextContent("Tên · Loại ▼");
    expect(SEARCH.value.get("sort")).toBe("-title");

    await userEvent.dblClick(header(/Tên · Loại/));
    expect(codesInOrder()).toEqual(serverOrder);
    expect(header(/Tên · Loại/)).not.toHaveAttribute("aria-sort");
    expect(header(/Tên · Loại/)).toHaveTextContent(/^Tên · Loại$/);
    expect(SEARCH.value.get("sort")).toBeNull();
    expect(screen.queryByText("Đang sắp xếp trong trang này")).not.toBeInTheDocument();
    expect(listCalls(stub).length).toBe(requests);
  });

  it("sorts a date column by value, with the empty ones last either way", async () => {
    tasksPage(ME_AS("ORDERER"), ROWS);
    await screen.findByRole("link", { name: ROWS[0].code });
    await userEvent.dblClick(header("Deadline"));
    expect(codesInOrder()).toEqual([ROWS[2].code, ROWS[0].code, ROWS[3].code, ROWS[1].code]);
    expect(header(/Deadline/)).toHaveAttribute("aria-sort", "ascending");
    await userEvent.dblClick(header(/Deadline/));
    expect(codesInOrder()).toEqual([ROWS[3].code, ROWS[0].code, ROWS[2].code, ROWS[1].code]);
    await userEvent.dblClick(header(/Deadline/));
    expect(codesInOrder()).toEqual(serverOrder);
  });

  it("moves to another column on its first double click", async () => {
    tasksPage(ME_AS("ORDERER"), ROWS);
    await screen.findByRole("link", { name: ROWS[0].code });
    await userEvent.dblClick(header(/Tên · Loại/));
    await userEvent.dblClick(header("Deadline"));
    expect(SEARCH.value.get("sort")).toBe("deadline");
    expect(header(/Tên · Loại/)).not.toHaveAttribute("aria-sort");
    expect(header(/Deadline/)).toHaveAttribute("aria-sort", "ascending");
  });

  it("keeps the sort when the page, a filter or the page size changes", async () => {
    SEARCH.value = new URLSearchParams("sort=-title");
    tasksPage(ME_AS("ORDERER"), ROWS, [], 40);
    await screen.findByRole("link", { name: ROWS[0].code });
    expect(titles()[0]).toBe("Đèn trung thu");
    await userEvent.click(screen.getByRole("button", { name: "2" }));
    expect(SEARCH.value.get("sort")).toBe("-title");
    await userEvent.click(screen.getByRole("button", { name: /Ưu tiên/ }));
    expect(SEARCH.value.get("sort")).toBe("-title");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Số dòng" }), "20");
    expect(SEARCH.value.get("sort")).toBe("-title");
    await waitFor(() => expect(titles()[0]).toBe("Đèn trung thu"));
  });

  it("sorts a step column by that step's status, and leaves an unknown column alone", async () => {
    const cell = (status_label: string) => [{ ...ROW.cells[0], status_label }];
    SEARCH.value = new URLSearchParams("sort=step:DUNG");
    tasksPage(ME_AS("ORDERER"), [
      row({ n: 1, cells: cell("Đang làm") }),
      row({ n: 2, cells: cell("Chờ duyệt") }),
      row({ n: 3, cells: cell("Hoàn thành") }),
    ]);
    await screen.findByRole("link", { name: "TUAN-BT-261012-01" });
    // This table has no "Dựng" step: the server's order, and no note.
    expect(codesInOrder()).toEqual(["TUAN-BT-261012-01", "TUAN-BT-261012-02", "TUAN-BT-261012-03"]);
    expect(screen.queryByText("Đang sắp xếp trong trang này")).not.toBeInTheDocument();
    await userEvent.dblClick(header("Design"));
    expect(SEARCH.value.get("sort")).toBe("step:THIET_KE");
    expect(codesInOrder()).toEqual(["TUAN-BT-261012-02", "TUAN-BT-261012-01", "TUAN-BT-261012-03"]);
  });

  it("sorts the Design columns: the designer, then the hand-in date", async () => {
    const design = (designer: string | null, at: string | null, code = "TKBTS") => ({
      ...ROW.design,
      designer,
      design_submitted_at: at,
      codes: [{ ...TKBTS, code }],
    });
    tasksPage(ME_AS("THIET_KE", DESIGNER), [
      row({ n: 1, design: design("Quang", "2026-10-12T03:00:00Z", "TKBTS") }),
      row({ n: 2, design: design(null, null, "CA") }),
      row({ n: 3, design: design("Diễm My", "2026-10-11T03:00:00Z", "VID") }),
      row({ n: 4, design: design("Quang", "2026-10-10T03:00:00Z", "AVT") }),
    ]);
    await screen.findByRole("link", { name: "TUAN-BT-261012-01" });
    await userEvent.dblClick(header("Thiết kế"));
    expect(codesInOrder()).toEqual([
      "TUAN-BT-261012-03",
      "TUAN-BT-261012-04",
      "TUAN-BT-261012-01",
      "TUAN-BT-261012-02",
    ]);
    await userEvent.dblClick(header("Tên task"));
    expect(codesInOrder()).toEqual([
      "TUAN-BT-261012-04",
      "TUAN-BT-261012-02",
      "TUAN-BT-261012-01",
      "TUAN-BT-261012-03",
    ]);
  });

  it("sorts the birthday board: a birthday by its day in the year, a name alphabetically", async () => {
    const person = (full_name: string, birthday_on: string, branch = "VP - Hà Nội") => ({
      full_name,
      branch,
      gender: "FEMALE",
      birthday_on,
    });
    birthdayPage([
      birthdayRow(1, person("Trần Diễm My", "1998-10-20")),
      birthdayRow(2, person("Đỗ Hà", "2001-02-03", "CS - Hà Nội 2")),
      birthdayRow(3, person("Nguyễn Văn An", "1990-12-01")),
    ]);
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    const order = () => bodyRows().map((line) => within(line).getAllByRole("link")[1].textContent);
    expect(order()).toEqual(["Trần Diễm My", "Đỗ Hà", "Nguyễn Văn An"]);
    await userEvent.dblClick(header("Ngày sinh"));
    // By month and day, not by the year of birth.
    expect(order()).toEqual(["Đỗ Hà", "Trần Diễm My", "Nguyễn Văn An"]);
    expect(screen.getByRole("status")).toHaveTextContent("Đang sắp xếp trong trang này");
    await userEvent.dblClick(header("Họ và tên"));
    expect(order()).toEqual(["Đỗ Hà", "Nguyễn Văn An", "Trần Diễm My"]);
    await userEvent.dblClick(header(/Họ và tên/));
    expect(order()).toEqual(["Trần Diễm My", "Nguyễn Văn An", "Đỗ Hà"]);
    await userEvent.dblClick(header(/Họ và tên/));
    expect(order()).toEqual(["Trần Diễm My", "Đỗ Hà", "Nguyễn Văn An"]);
    await userEvent.dblClick(header("Chi nhánh"));
    expect(order()[0]).toBe("Đỗ Hà");
  });

  it("reads and steps the ?sort= value", () => {
    expect(parseSort(null)).toBeNull();
    expect(parseSort("")).toBeNull();
    expect(parseSort("title")).toEqual({ key: "title", desc: false });
    expect(parseSort("-step:DUNG")).toEqual({ key: "step:DUNG", desc: true });
    expect(nextSort(null, "title")).toBe("title");
    expect(nextSort({ key: "title", desc: false }, "title")).toBe("-title");
    expect(nextSort({ key: "title", desc: true }, "title")).toBe("");
    expect(nextSort({ key: "title", desc: true }, "deadline")).toBe("deadline");
  });

  it("sorts numbers by value, keeps ties in place and never mutates the list", () => {
    const list = [
      { id: "a", n: 10, at: "2026-10-12T00:00:00Z" },
      { id: "b", n: 9, at: null },
      { id: "c", n: 10, at: "not a date" },
      { id: "d", n: 100, at: "2026-10-01T00:00:00Z" },
    ];
    const columns = {
      n: (item: (typeof list)[number]) => item.n,
      at: (item: (typeof list)[number]) => sortTime(item.at),
    };
    const ids = (sorted: typeof list) => sorted.map((item) => item.id).join("");
    expect(ids(sortRows(list, { key: "n", desc: false }, columns))).toBe("bacd");
    expect(ids(sortRows(list, { key: "n", desc: true }, columns))).toBe("dacb");
    expect(ids(sortRows(list, { key: "at", desc: false }, columns))).toBe("dabc");
    expect(ids(sortRows(list, { key: "at", desc: true }, columns))).toBe("adbc");
    expect(ids(sortRows(list, null, columns))).toBe("abcd");
    expect(ids(list)).toBe("abcd");
  });
});
