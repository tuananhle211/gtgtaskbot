/**
 * ORD tokens and deadlines on the screen.
 *
 * The server decides every number and status (a deadline's `deadline_status`,
 * an assignee's load, a person-day's budget/used/left); the screens draw them
 * and send the inputs back: `tokens` as a number and `deadline_at` as ISO.
 * The one thing the client works out is the warning when a node deadline is
 * after the orderer's wished one - only a warning, the server accepts it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskDetailPage from "@/app/tasks/[ref]/page";
import TasksPage from "@/app/tasks/page";
import EffortPage from "@/app/tasks/effort/page";
import DashboardPage from "@/app/dashboard/page";
import { Shell } from "@/components/shell";
import { UnitPanel } from "@/components/unit-panel";
import type { EffortGrid, TaskRow, UnifiedTaskDetail } from "@/lib/api";
import {
  assigneeLoadLabel,
  deadlineStatusLabel,
  formatDeadline,
  formatSpan,
  overDesired,
  parseTokens,
  toIsoFromLocal,
  toLocalInput,
} from "@/lib/deadline";
import { addDays, dayHeading, mondayOf } from "@/lib/effort";
import { deadlineColor } from "@/lib/status-colors";
import { renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
let pathname = "/tasks/TUAN-D-261010-01";

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: "TUAN-D-261010-01" }),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  pathname = "/tasks/TUAN-D-261010-01";
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const posts = (stub: unknown) => calls(stub).filter((call) => call.method === "POST");

// Wall-clock values as a datetime-local box holds them (the browser's clock);
// the wished deadline is the same instant in ISO, so the test runs in any zone.
const DESIRED_LOCAL = "2099-12-01T10:00";
const DESIRED = new Date(DESIRED_LOCAL).toISOString();
const STEP_DEADLINE = "2099-11-30T10:00:00+07:00";

const TASK_ID = "7c1d6f0e-aaaa-4c3b-9a3e-000000000001";
const LEAD_ID = "7c1d6f0e-aaaa-4c3b-9a3e-000000000002";
const NHU_ID = "7c1d6f0e-aaaa-4c3b-9a3e-000000000003";
const VY_ID = "7c1d6f0e-aaaa-4c3b-9a3e-000000000004";

const DETAIL: UnifiedTaskDetail = {
  task: {
    id: TASK_ID,
    unit: "ADS",
    unit_label: "Luồng Order (ORD)",
    code: "TUAN-D-261010-01",
    title: "Video serum mùa đông",
    kind: "D",
    kind_label: "Short video",
    phase: "PRODUCTION",
    phase_label: "Sản xuất",
    stage: "DUNG",
    stage_label: "Dựng · Chờ Lan phân công",
    state: "CHO_PHAN_CONG",
    owner: { user_id: "u-owner", name: "Tuấn Marketing" },
    current_person: { user_id: LEAD_ID, name: "Lan Leader" },
    is_priority: false,
    urgent: false,
    created_at: "2026-10-10T02:00:00+00:00",
    updated_at: "2026-10-10T03:00:00+00:00",
    stage_since: "2026-10-10T03:00:00+00:00",
    finished_at: null,
    product_link: null,
    latest_link: null,
    revisions: 0,
    version: 3,
    desired_deadline_at: DESIRED,
    deadline_at: STEP_DEADLINE,
    deadline_status: "OVERDUE",
    over_deadline_count: 1,
    source: { type: "ORDER", id: "o-1" },
  },
  steps: [
    {
      key: "DUNG",
      label: "Dựng",
      person_name: "Lan Leader",
      status: "CHO_PHAN_CONG",
      status_label: "Chờ Lan phân công",
      is_current: true,
      since: "2026-10-10T03:00:00+00:00",
      revisions: 0,
      tokens: 3.5,
      deadline_at: STEP_DEADLINE,
      deadline_status: "OVERDUE",
    },
  ],
  people: [],
  fields: [
    {
      key: "desired_deadline_at",
      label: "Deadline mong muốn",
      value: DESIRED,
      type: "date",
      group: "ads",
    },
    {
      key: "over_deadline_count",
      label: "Quá deadline mong muốn",
      value: "1 lần",
      type: "text",
      group: "ads",
    },
  ],
  submissions: [],
  timeline: [],
  actions: [
    {
      key: "ads:ASSIGN:n-dung",
      label: "Giao Dựng",
      emphasis: "PRIMARY",
      requires_note: false,
      inputs: ["assignee", "tokens", "deadline"],
      required_inputs: ["tokens", "deadline"],
      plan_mode: "ESTIMATE",
      assignee_options: [
        {
          user_id: NHU_ID,
          name: "Quỳnh Như",
          tokens_budget_today: 8,
          tokens_left_today: 3,
          tokens_open: 5,
          open_tasks: 2,
        },
        {
          user_id: VY_ID,
          name: "Vy",
          tokens_budget_today: 8,
          tokens_left_today: -2,
          tokens_open: 12,
          open_tasks: 4,
        },
      ],
    },
    {
      key: "ads:RETURN_NODE:n-dung",
      label: "Trả sửa",
      emphasis: "SECONDARY",
      requires_note: true,
      inputs: ["note", "tokens", "deadline"],
      required_inputs: ["tokens"],
      plan_mode: "REVISION",
      assignee_options: [],
    },
    {
      key: "ads:SET_NODE_PLAN:n-dung",
      label: "Sửa token/deadline · Dựng",
      emphasis: "SECONDARY",
      requires_note: false,
      inputs: ["tokens", "deadline"],
      required_inputs: ["tokens"],
      plan_mode: "ESTIMATE",
      defaults: { tokens: 3.5, deadline_at: DESIRED },
      assignee_options: [],
    },
  ],
};

function stubDetail() {
  return stubFetch([
    { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: { ...DETAIL, actions: [] } },
    { match: `/api/tasks/${DETAIL.task.code}`, body: DETAIL },
  ]);
}

describe("the deadline helpers", () => {
  it("names and colours every status", () => {
    expect(
      ["ON_TRACK", "DUE_SOON", "OVERDUE", "MET", "MISSED"].map(deadlineStatusLabel),
    ).toEqual(["Còn hạn", "Sắp tới hạn", "Quá hạn", "Đúng hạn", "Trễ hạn"]);
    expect(deadlineColor("DUE_SOON")).toBe("amber");
    expect(deadlineColor("OVERDUE")).toBe("red");
    expect(deadlineColor("MISSED")).toBe("red");
    expect(deadlineColor("MET")).toBe("green");
    expect(deadlineColor("ON_TRACK")).toBe("slate");
    expect(deadlineColor(null)).toBe("slate");
    expect(deadlineColor("SOMETHING_NEW")).toBe("slate");
  });

  it("writes a deadline in Vietnam time, dd/MM HH:mm", () => {
    expect(formatDeadline("2026-10-12T10:00:00Z")).toBe("12/10 17:00");
    expect(formatDeadline("2026-10-12T20:30:00+00:00")).toBe("13/10 03:30");
    expect(formatDeadline(null)).toBe("—");
  });

  it("turns a datetime-local value into ISO and back", () => {
    const iso = toIsoFromLocal("2026-10-12T17:00");
    expect(iso).toBe(new Date("2026-10-12T17:00").toISOString());
    expect(toLocalInput(iso)).toBe("2026-10-12T17:00");
    expect(toIsoFromLocal("")).toBeNull();
    expect(toLocalInput(null)).toBe("");
  });

  it("warns by how much a deadline passes the wished one, and only then", () => {
    expect(overDesired("2099-12-02T13:00", DESIRED)).toBe(
      "Vượt deadline mong muốn 1 ngày 3 giờ",
    );
    expect(overDesired("2026-10-12T12:00:00Z", "2026-10-12T10:00:00Z")).toBe(
      "Vượt deadline mong muốn 2 giờ",
    );
    expect(overDesired("2026-10-12T10:45:00Z", "2026-10-12T10:00:00Z")).toBe(
      "Vượt deadline mong muốn 45 phút",
    );
    expect(overDesired("2026-10-12T09:00:00Z", "2026-10-12T10:00:00Z")).toBeNull();
    expect(overDesired("2026-10-12T10:00:00Z", "2026-10-12T10:00:00Z")).toBeNull();
    expect(overDesired("", DESIRED)).toBeNull();
    expect(overDesired("2026-10-12T10:00:00Z", null)).toBeNull();
    expect(formatSpan(2 * 86_400_000)).toBe("2 ngày");
  });

  it("labels an assignee with today's balance and the open load", () => {
    expect(assigneeLoadLabel(DETAIL.actions[0].assignee_options[0])).toBe(
      "Quỳnh Như · ⚡ còn 3/8 hôm nay · ⚡ đang ôm 5 (2 task)",
    );
    expect(assigneeLoadLabel(DETAIL.actions[0].assignee_options[1])).toBe(
      "Vy · ⚡ vượt 2/8 hôm nay · ⚡ đang ôm 12 (4 task)",
    );
    expect(assigneeLoadLabel({ user_id: "x", name: "Chỉ tên" } as never)).toBe("Chỉ tên");
  });

  it("reads tokens as 0..999.99 with up to two decimals", () => {
    expect(parseTokens("2.5")).toBe(2.5);
    expect(parseTokens("1,25")).toBe(1.25);
    expect(parseTokens("0")).toBe(0);
    expect(parseTokens("-1")).toBeNull();
    expect(parseTokens("1000")).toBeNull();
    expect(parseTokens("")).toBeNull();
  });

  it("walks Monday..Sunday weeks on calendar days", () => {
    expect(mondayOf("2026-10-10")).toBe("2026-10-05");
    expect(mondayOf("2026-10-11")).toBe("2026-10-05");
    expect(mondayOf("2026-10-05")).toBe("2026-10-05");
    expect(addDays("2026-10-05", 7)).toBe("2026-10-12");
    expect(addDays("2026-11-01", -1)).toBe("2026-10-31");
    expect(dayHeading("2026-10-05")).toBe("T2 05/10");
    expect(dayHeading("2026-10-11")).toBe("CN 11/10");
  });
});

describe("the task page's tokens and deadlines", () => {
  it("shows each step's tokens and deadline coloured by its status", async () => {
    stubDetail();
    renderWithQuery(<TaskDetailPage />);
    const strip = await screen.findByRole("region", { name: "Tiến trình" });
    // Tokens stand out: a bolt badge.
    const badges = [...strip.querySelectorAll("[data-token-badge]")].map((el) => el.textContent);
    expect(badges).toContain("3.5 token");
    expect(strip.querySelector("[data-token-badge] svg")).not.toBeNull();
    const badge = within(strip)
      .getByText(new RegExp(`Hạn ${formatDeadline(STEP_DEADLINE)}`))
      .closest(".st");
    expect(badge).toHaveClass("st-red");
    expect(badge).toHaveAttribute("title", "Quá hạn");
    // The header carries the current step's deadline and a loud "Quá hạn".
    expect(screen.getAllByText("Quá hạn").length).toBeGreaterThan(0);
    // The wished deadline and the counter come as ordinary fields.
    expect(screen.getByText("Deadline mong muốn")).toBeInTheDocument();
    expect(screen.getByText("1 lần")).toBeInTheDocument();
  });

  it("assigns with tokens and a deadline, labelling each person's load", async () => {
    const stub = stubDetail();
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Giao Dựng" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Giao Dựng" });

    const picker = within(dialog).getByRole("combobox");
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn —",
      "Quỳnh Như · ⚡ còn 3/8 hôm nay · ⚡ đang ôm 5 (2 task)",
      "Vy · ⚡ vượt 2/8 hôm nay · ⚡ đang ôm 12 (4 task)",
    ]);
    // Over budget reads red, in the list and once chosen.
    expect(within(picker).getByRole("option", { name: /Vy/ })).toHaveClass("text-[var(--bad)]");
    await userEvent.selectOptions(picker, VY_ID);
    expect(within(dialog).getByTestId("assignee-load")).toHaveClass("text-[var(--bad)]");
    await userEvent.selectOptions(picker, NHU_ID);
    expect(within(dialog).getByTestId("assignee-load")).not.toHaveClass("text-[var(--bad)]");

    // Tokens and deadline are both required.
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("spinbutton", { name: "Token (bắt buộc)" }), "2.5");
    expect(confirm).toBeDisabled();
    expect(
      within(dialog).getByText(`Deadline mong muốn: ${formatDeadline(DESIRED)}`),
    ).toBeInTheDocument();

    const deadline = within(dialog).getByLabelText("Deadline (bắt buộc)");
    // Before the wished deadline: no warning.
    fireEvent.change(deadline, { target: { value: "2099-11-30T09:00" } });
    expect(within(dialog).queryByTestId("over-desired")).not.toBeInTheDocument();
    // After it: an amber warning, and the button stays enabled.
    fireEvent.change(deadline, { target: { value: "2099-12-02T13:00" } });
    const warning = within(dialog).getByTestId("over-desired");
    expect(warning).toHaveTextContent("Vượt deadline mong muốn 1 ngày 3 giờ");
    expect(warning).toHaveClass("text-[var(--warn)]");
    expect(confirm).toBeEnabled();

    await userEvent.click(confirm);
    await waitFor(() => expect(posts(stub)).toHaveLength(1));
    expect(posts(stub)[0].body).toEqual({
      key: "ads:ASSIGN:n-dung",
      version: 3,
      assignee_user_id: NHU_ID,
      tokens: 2.5,
      deadline_at: new Date("2099-12-02T13:00").toISOString(),
    });
  });

  it("returns a node with revision tokens and no deadline", async () => {
    const stub = stubDetail();
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Trả sửa" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Trả sửa" });
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Lý do/ }),
      "Nhạc chưa khớp",
    );
    // "Token sửa" is required; "Deadline sửa" is not.
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByLabelText("Deadline sửa")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("spinbutton", { name: "Token sửa (bắt buộc)" }), "1");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(posts(stub)).toHaveLength(1));
    expect(posts(stub)[0].body).toEqual({
      key: "ads:RETURN_NODE:n-dung",
      version: 3,
      note: "Nhạc chưa khớp",
      tokens: 1,
    });
  });

  it("refuses a negative token amount", async () => {
    stubDetail();
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Trả sửa" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Lý do/ }), "Sửa");
    await userEvent.type(within(dialog).getByRole("spinbutton", { name: /Token sửa/ }), "-1");
    expect(within(dialog).getByRole("button", { name: "Trả sửa" })).toBeDisabled();
  });

  it("prefills a plan change from the server's defaults", async () => {
    const stub = stubDetail();
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sửa token/deadline · Dựng" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("spinbutton", { name: "Token (bắt buộc)" })).toHaveValue(3.5);
    expect(within(dialog).getByLabelText("Deadline")).toHaveValue(DESIRED_LOCAL);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Sửa token/deadline · Dựng" }),
    );
    await waitFor(() => expect(posts(stub)).toHaveLength(1));
    expect(posts(stub)[0].body).toEqual({
      key: "ads:SET_NODE_PLAN:n-dung",
      version: 3,
      tokens: 3.5,
      deadline_at: DESIRED,
    });
  });
});

// --- The task table and the dashboard -----------------------------------------

const ADS_SETTINGS = {
  urgent_days: 7,
  media_nas_url: null,
  design_nas_url: null,
  telegram_enabled: false,
  review_bien_tap: false,
  review_thiet_ke: false,
  review_dung: true,
  review_video_by_script_lead: false,
  default_daily_tokens: 8,
  perf_weights: { output: 0.5, on_time: 0.3, quality: 0.2 },
  output_target: null,
};

const entry = (role: string, isLead = false) => ({
  code: "ADS",
  label: "Luồng Order (ORD)",
  role,
  role_label: role,
  is_lead: isLead,
  member_code: "TUAN",
  personal_nas_url: null,
  settings: ADS_SETTINGS,
});

const ME_AS = (role: string, isLead = false, extra: Record<string, unknown> = {}) => ({
  units: [entry(role, isLead)],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  ...extra,
});

const ROW: TaskRow = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  id: "o-1",
  code: "TUAN-D-261010-01",
  title: "Video serum mùa đông",
  kind: "D",
  kind_label: "Short video",
  owner_user_id: SESSION.user_id,
  owner_name: "Tuấn",
  created_at: "2026-10-10T02:00:00Z",
  phase: "PRODUCTION",
  phase_label: "Sản xuất",
  status: "DUNG",
  status_label: "Dựng · Đang làm",
  cells: [],
  product_link: null,
  returned_at: null,
  is_priority: false,
  urgent: false,
  detail_path: "/tasks/TUAN-D-261010-01",
  version: 3,
  stage_since: null,
  revisions: 0,
  current_person_name: "Quỳnh Như",
  latest_link: null,
  extras: [],
  desired_deadline_at: DESIRED,
  deadline_at: STEP_DEADLINE,
  deadline_status: "OVERDUE",
  over_deadline_count: 2,
};

const PAGE = { unit: "ADS", items: [ROW], total: 1, limit: 10, offset: 0, phases: [] };

describe("the task table's deadlines", () => {
  it("draws the Deadline column and filters to the overdue rows", async () => {
    pathname = "/tasks";
    const stub = stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      { match: "/api/units/ADS/members", body: { unit: "ADS", unit_label: "ORD", members: [], assignable_roles: [] } },
      { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    expect(screen.getByRole("columnheader", { name: "Deadline" })).toBeInTheDocument();
    const cell = screen.getByTestId("row-deadline");
    expect(within(cell).getByText(new RegExp(`Hạn ${formatDeadline(STEP_DEADLINE)}`)).closest(".st")).toHaveClass("st-red");
    expect(within(cell).getByText("Quá hạn")).toBeInTheDocument();
    expect(within(cell).getByText("Vượt mong muốn 2 lần")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Trễ hạn" }));
    await waitFor(() => expect(SEARCH.value.get("overdue")).toBe("true"));
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) => call.url.includes("/api/board/tasks") && call.url.includes("overdue=true"),
        ),
      ).toBe(true),
    );
  });

  it("has no deadline column or filter on the PR table", async () => {
    pathname = "/tasks";
    SEARCH.value = new URLSearchParams("unit=PR&overdue=true");
    const stub = stubFetch([
      {
        match: "/api/units/me",
        body: {
          units: [{ ...entry("MEMBER"), code: "PR", label: "Luồng PR" }],
          default_unit: "PR",
          can_view_all: false,
          can_admin: [],
        },
      },
      { match: "/api/units/PR/members", body: { unit: "PR", unit_label: "PR", members: [], assignable_roles: [] } },
      { match: "/api/board/tasks", body: { ...PAGE, unit: "PR", items: [{ ...ROW, unit: "PR", deadline_at: null, deadline_status: null }] } },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    expect(screen.queryByRole("columnheader", { name: "Deadline" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Trễ hạn" })).not.toBeInTheDocument();
    expect(calls(stub).every((call) => !call.url.includes("overdue="))).toBe(true);
  });

  it("puts a Trễ hạn tile on the dashboard", async () => {
    pathname = "/dashboard";
    SEARCH.value = new URLSearchParams("unit=ADS");
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      { match: "/api/units/ADS/members", body: { unit: "ADS", unit_label: "ORD", members: [], assignable_roles: [] } },
      {
        match: "/api/board/dashboard",
        body: {
          unit: "ADS",
          date_from: "2026-10-01",
          date_to: "2026-10-31",
          total: 5,
          completed: 1,
          pending_review: 1,
          urgent: 0,
          overdue: 3,
          progress_percent: 20,
          by_phase: [],
          by_owner: [],
          by_worker: [{ user_id: "u-1", name: "Quỳnh Như", opened: 2, done: 1, late: 1 }],
        },
      },
      { match: "/api/board/tasks", body: { ...PAGE, items: [], total: 0 } },
    ]);
    renderWithQuery(<DashboardPage />);
    const tile = (await screen.findByText("Trễ hạn", { selector: "p" })).closest("a");
    expect(tile).toHaveTextContent("3");
    expect(tile?.getAttribute("href")).toContain("overdue=true");
  });
});

// --- The effort grid ------------------------------------------------------------

const GRID: EffortGrid = {
  date_from: "2026-10-05",
  date_to: "2026-10-11",
  days: ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"],
  today: "2026-10-09",
  people: [
    {
      user_id: NHU_ID,
      full_name: "Quỳnh Như",
      role_label: "Dựng",
      function_tag: "D",
      is_lead: false,
      daily_tokens: 8,
      open_tokens: 5,
      open_tasks: 2,
      days: [
        { date: "2026-10-05", budget: 8, used: 3, left: 5 },
        { date: "2026-10-06", budget: 8, used: 10, left: -2 },
        { date: "2026-10-07", budget: 8, used: 0, left: 8 },
        { date: "2026-10-08", budget: 8, used: 8, left: 0 },
        { date: "2026-10-09", budget: 8, used: 1.5, left: 6.5 },
        { date: "2026-10-10", budget: 0, used: 0, left: 0 },
        { date: "2026-10-11", budget: 0, used: 0, left: 0 },
      ],
    },
  ],
};

describe("the effort page", () => {
  it("shows used/budget per day, what is left green or red, the open load and today", async () => {
    pathname = "/tasks/effort";
    stubFetch([{ match: "/api/units/ADS/effort", body: GRID }]);
    renderWithQuery(<EffortPage />);
    const monday = await screen.findByTestId("effort-2026-10-05");
    expect(monday).toHaveTextContent("3/8");
    expect(within(monday).getByText("còn 5")).toHaveClass("text-[var(--good)]");
    const over = screen.getByTestId("effort-2026-10-06");
    expect(over).toHaveTextContent("10/8");
    expect(within(over).getByText("vượt 2")).toHaveClass("text-[var(--bad)]");
    expect(within(screen.getByTestId("effort-2026-10-08")).getByText("còn 0")).toHaveClass(
      "text-[var(--good)]",
    );
    // The weekend has no budget.
    expect(screen.getAllByText("nghỉ")).toHaveLength(2);
    expect(screen.getByTestId("open-load")).toHaveTextContent("5");
    expect(screen.getByTestId("open-load")).toHaveTextContent("2 task");
    expect(screen.getByRole("columnheader", { name: /T6 09\/10/ })).toHaveAttribute(
      "aria-current",
      "date",
    );
    expect(screen.getByRole("columnheader", { name: "Đang ôm" })).toBeInTheDocument();
  });

  it("filters by ban and adds the people on screen up", async () => {
    pathname = "/tasks/effort";
    const writer = {
      ...GRID.people[0],
      user_id: "u-writer",
      full_name: "Hà Biên",
      role_label: "Biên tập",
      function_tag: "BT",
      open_tokens: 1,
      open_tasks: 1,
    };
    stubFetch([
      { match: "/api/units/ADS/effort", body: { ...GRID, people: [...GRID.people, writer] } },
    ]);
    renderWithQuery(<EffortPage />);
    await screen.findByText("Hà Biên");
    const tabs = screen.getByRole("tablist", { name: "Lọc theo ban" });
    // Only the bans somebody belongs to, plus "Tất cả".
    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Tất cả",
      "Biên kịch",
      "Dựng",
    ]);
    expect(screen.getByTestId("effort-total")).toHaveTextContent("Tổng (2 người)");
    await userEvent.click(within(tabs).getByRole("tab", { name: "Dựng" }));
    expect(SEARCH.value.get("ban")).toBe("D");
  });

  it("shows one ban from the URL", async () => {
    pathname = "/tasks/effort";
    SEARCH.value = new URLSearchParams("ban=BT");
    const writer = { ...GRID.people[0], user_id: "u-writer", full_name: "Hà Biên", function_tag: "BT" };
    stubFetch([
      { match: "/api/units/ADS/effort", body: { ...GRID, people: [...GRID.people, writer] } },
    ]);
    renderWithQuery(<EffortPage />);
    expect(await screen.findByText("Hà Biên")).toBeInTheDocument();
    expect(screen.queryByText("Quỳnh Như")).not.toBeInTheDocument();
    expect(screen.queryByTestId("effort-total")).not.toBeInTheDocument();
  });

  it("moves a week at a time and comes back to this week", async () => {
    pathname = "/tasks/effort";
    const stub = stubFetch([{ match: "/api/units/ADS/effort", body: GRID }]);
    renderWithQuery(<EffortPage />);
    await screen.findByTestId("effort-2026-10-05");
    // The first request leaves the week to the server.
    expect(calls(stub)[0].url).toBe("/api/units/ADS/effort");
    await userEvent.click(screen.getByRole("button", { name: "Tuần sau ›" }));
    expect(SEARCH.value.get("from")).toBe("2026-10-12");
    await waitFor(() =>
      expect(calls(stub).map((call) => call.url)).toContain(
        "/api/units/ADS/effort?date_from=2026-10-12&date_to=2026-10-18",
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Tuần này" }));
    expect(SEARCH.value.get("from")).toBeNull();
  });
});

// --- The nav ----------------------------------------------------------------------

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

describe("the Effort entry in the nav", () => {
  const navHrefs = () =>
    within(screen.getByRole("navigation", { name: "Điều hướng chính" }))
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));

  it("is there for a function's Leader, and marks itself, not the task table, as current", async () => {
    pathname = "/tasks/effort";
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: ME_AS("DUNG", true) },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const effort = await screen.findByRole("link", { name: /Effort/ });
    expect(navHrefs()).toEqual(["/dashboard", "/tasks", "/orders/new", "/tasks/effort"]);
    expect(effort).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Quản lý task/ })).not.toHaveAttribute("aria-current");
  });

  it("is there for the Trưởng phòng ORD", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: ME_AS("HEAD") },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    expect(await screen.findByRole("link", { name: /Effort/ })).toHaveAttribute("href", "/tasks/effort");
  });

  it("is not there for a staff member", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: ME_AS("DUNG") },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await screen.findByText(/Le Trưởng Nhóm|Thành viên|Nhân viên/);
    await waitFor(() => expect(navHrefs()).toEqual(["/dashboard", "/tasks", "/orders/new"]));
    expect(screen.queryByRole("link", { name: /Effort/ })).not.toBeInTheDocument();
  });
});

// --- The unit panel ---------------------------------------------------------------

describe("the ORD token budget settings", () => {
  const MEMBER = {
    user_id: NHU_ID,
    full_name: "Quỳnh Như",
    base_role: "EMPLOYEE",
    base_role_label: "Nhân viên",
    role: "DUNG",
    role_label: "Dựng",
    is_lead: false,
    function_tag: "D",
    member_code: "NHU",
    personal_nas_url: null,
    joined_at: "2026-10-01T02:00:00Z",
    left_at: null,
    active: true,
    daily_tokens: null,
  };

  function stubPanel() {
    return stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      {
        match: "/api/units/me",
        body: ME_AS("HEAD", false, {
          can_view_all: true,
          can_admin: ["ADS"],
          can_tag: ["ADS"],
          units: [{ ...entry("HEAD"), settings: { ...ADS_SETTINGS, default_daily_tokens: 6 } }],
        }),
      },
      { match: "/api/units/ADS/members", body: { unit: "ADS", unit_label: "ORD", members: [MEMBER], assignable_roles: [] } },
      { match: `/api/units/ADS/members/${NHU_ID}`, method: "PATCH", body: { ...MEMBER, daily_tokens: 4 } },
      { match: "/api/units/ADS/settings", method: "PATCH", body: ADS_SETTINGS },
      { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
      { match: "/api/units/directory", body: [] },
      { match: "/api/units/untagged", body: { users: [] } },
      { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
      { match: "/api/units/ADS/platforms", body: { platforms: [] } },
      { match: "/api/units/ADS/durations", body: { durations: [] } },
    ]);
  }

  it("saves a member's own Token/ngày, the unit default as the placeholder", async () => {
    const stub = stubPanel();
    renderWithQuery(<UnitPanel code="ADS" />);
    const box = await screen.findByRole("spinbutton", { name: "Token/ngày của Quỳnh Như" });
    expect(box).toHaveValue(null);
    await waitFor(() => expect(box).toHaveAttribute("placeholder", "6"));
    await userEvent.type(box, "4");
    await userEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const patch = calls(stub).find((call) => call.method === "PATCH" && call.url.includes("/members/"));
      expect(patch?.body).toEqual({ member_code: "NHU", personal_nas_url: null, daily_tokens: 4 });
    });
  });

  it("saves the default budget, the weights and the output benchmark", async () => {
    const stub = stubPanel();
    renderWithQuery(<UnitPanel code="ADS" />);
    const budget = await screen.findByRole("spinbutton", { name: /Token\/ngày mặc định/ });
    expect(budget).toHaveValue(6);
    await userEvent.clear(budget);
    await userEvent.type(budget, "7");
    const target = screen.getByRole("spinbutton", { name: /Mốc sản lượng/ });
    expect(target).toHaveAttribute("placeholder", "Người làm nhiều nhất");
    await userEvent.type(target, "12");
    const output = screen.getByRole("spinbutton", { name: "Trọng số Sản lượng" });
    await userEvent.clear(output);
    await userEvent.type(output, "0.4");
    expect(screen.getByText(/Tổng trọng số đang là 0.9/)).toBeInTheDocument();
    const quality = screen.getByRole("spinbutton", { name: "Trọng số Không bị trả" });
    await userEvent.clear(quality);
    await userEvent.type(quality, "0.3");
    expect(screen.queryByText(/Tổng trọng số đang là/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Lưu thiết lập" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const patch = calls(stub).find((call) => call.method === "PATCH" && call.url.includes("/settings"));
      expect(patch?.body).toEqual({
        default_daily_tokens: 7,
        output_target: 12,
        perf_weights: { output: 0.4, on_time: 0.3, quality: 0.3 },
      });
    });
  });
});
