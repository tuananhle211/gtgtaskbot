/**
 * The OWNER's controls and the task table's live counts.
 *
 * Effort (tokens) on / off: one flag on `/api/units/me` (`effort_enabled`)
 * hides every token figure - the nav entry and its page, the badges on a
 * task's steps and cells, the load beside an assignee, the "Theo ban" token
 * figures, the account's effort cards and the unit panel's token fields. The
 * switch itself is the OWNER's; anybody else sees it disabled.
 *
 * Delete: "Xoá task" is drawn only when the server says `can_delete`, and
 * "Xoá" / "Xoá thành viên" only for an OWNER looking at somebody who is
 * neither an OWNER nor themselves. Both ask first and send a DELETE.
 *
 * Counts: every quick filter of `/tasks` carries "(n)" from
 * `/api/board/counts`, asked for the unit on screen and again whenever the
 * list changes.
 */

import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskDetailPage from "@/app/tasks/[ref]/page";
import TasksPage from "@/app/tasks/page";
import EffortPage from "@/app/tasks/effort/page";
import AccountPage from "@/app/account/page";
import { BanStatsPanel } from "@/components/ban-stats";
import { Shell } from "@/components/shell";
import { SettingsForm, UnitPanel } from "@/components/unit-panel";
import type { TaskRow, UnifiedTaskDetail } from "@/lib/api";
import { assigneeLoadLabel } from "@/lib/deadline";
import { effortEnabled } from "@/lib/effort";
import { confirm, renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";


/** "Thành viên luồng" and "Thiết lập ORD" side by side: tokens show on both. */
function RosterAndSettings() {
  return (
    <>
      <UnitPanel code="ADS" />
      <SettingsForm code="ADS" onDone={() => {}} />
    </>
  );
}
const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const PUSHED: string[] = [];
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: "TUAN-D-261010-01" }),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => {
      PUSHED.push(url);
      URL_BAR.navigate(url);
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

const TASK_ID = "7c1d6f0e-bbbb-4c3b-9a3e-000000000001";
const NHU_ID = "7c1d6f0e-bbbb-4c3b-9a3e-000000000003";
const BOSS_ID = "7c1d6f0e-bbbb-4c3b-9a3e-000000000004";

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

/** `/api/units/me` for somebody in ORD; `effort` is the stream's switch. */
const ME_AS = (role: string, effort = true, extra: Record<string, unknown> = {}) => ({
  units: [
    {
      code: "ADS",
      label: "Luồng Order (ORD)",
      role,
      role_label: role,
      is_lead: false,
      member_code: "TUAN",
      personal_nas_url: null,
      settings: { ...ADS_SETTINGS, effort_enabled: effort },
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  effort_enabled: effort,
  ...extra,
});

const ADMIN_ME = (effort = true) =>
  ME_AS("HEAD", effort, { can_view_all: true, can_admin: ["ADS"], can_tag: ["ADS"] });

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
    current_person: { user_id: BOSS_ID, name: "Lan Leader" },
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
    },
  ],
  people: [],
  fields: [],
  submissions: [],
  timeline: [],
  actions: [
    {
      key: "ads:ASSIGN:n-dung",
      label: "Giao Dựng",
      emphasis: "PRIMARY",
      requires_note: false,
      inputs: ["assignee", "deadline"],
      required_inputs: ["deadline"],
      assignee_options: [
        {
          user_id: NHU_ID,
          name: "Quỳnh Như",
          tokens_budget_today: 8,
          tokens_left_today: 3,
          tokens_open: 5,
          open_tasks: 2,
        },
      ],
    },
  ],
};

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

// --- 1. Effort on / off -------------------------------------------------------------

describe("effort (tokens) switched off", () => {
  it("is on unless /api/units/me says otherwise", () => {
    expect(effortEnabled(undefined)).toBe(true);
    expect(effortEnabled(ME_AS("HEAD") as never)).toBe(true);
    expect(effortEnabled({ ...ME_AS("HEAD"), effort_enabled: undefined } as never)).toBe(true);
    expect(effortEnabled(ME_AS("HEAD", false) as never)).toBe(false);
  });

  it("drops the Effort entry from the nav, even for the Trưởng phòng ADS", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: ADMIN_ME(false) },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    // The admin entry arrives with /api/units/me: the nav is complete by then.
    await screen.findByRole("link", { name: /Quản trị/ });
    expect(screen.queryByRole("link", { name: /Effort/ })).not.toBeInTheDocument();
  });

  it("keeps the Effort entry while it is on", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: ADMIN_ME(true) },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    expect(await screen.findByRole("link", { name: /Effort/ })).toHaveAttribute(
      "href",
      "/tasks/effort",
    );
  });

  it("only says so on the effort page, and asks for no grid", async () => {
    pathname = "/tasks/effort";
    const stub = stubFetch([{ match: "/api/units/me", body: ME_AS("HEAD", false) }]);
    renderWithQuery(<EffortPage />);
    expect(await screen.findByText("Chế độ tính effort (token) đang tắt.")).toBeInTheDocument();
    expect(calls(stub).some((call) => call.url.includes("/effort"))).toBe(false);
  });

  it("says so too when the grid itself answers effort_disabled", async () => {
    pathname = "/tasks/effort";
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      {
        match: "/api/units/ADS/effort",
        status: 404,
        body: {
          error: {
            code: "not_found",
            message: "Chế độ tính effort (token) đang tắt.",
            details: { reason: "effort_disabled" },
          },
        },
      },
    ]);
    renderWithQuery(<EffortPage />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Chế độ tính effort (token) đang tắt.",
    );
    expect(screen.queryByRole("group", { name: "Chọn tuần" })).not.toBeInTheDocument();
  });

  it("draws no token badge on a task's steps, nor a load beside an assignee", async () => {
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD", false) },
      { match: `/api/tasks/${DETAIL.task.code}`, body: DETAIL },
    ]);
    renderWithQuery(<TaskDetailPage />);
    const strip = await screen.findByRole("region", { name: "Tiến trình" });
    // Even a payload that still carries tokens is not drawn.
    await waitFor(() => expect(strip.querySelector("[data-token-badge]")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Giao Dựng" }));
    const dialog = await screen.findByRole("dialog");
    const picker = within(dialog).getByRole("combobox");
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn —",
      "Quỳnh Như",
    ]);
    await userEvent.selectOptions(picker, NHU_ID);
    expect(screen.queryByTestId("assignee-load")).not.toBeInTheDocument();
    // The inputs are the server's list: no token box was asked for.
    expect(within(dialog).queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("draws the badge and the load while it is on", async () => {
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      { match: `/api/tasks/${DETAIL.task.code}`, body: DETAIL },
    ]);
    renderWithQuery(<TaskDetailPage />);
    const strip = await screen.findByRole("region", { name: "Tiến trình" });
    expect(strip.querySelector("[data-token-badge]")).toHaveTextContent("3.5 token");
    await userEvent.click(screen.getByRole("button", { name: "Giao Dựng" }));
    const picker = within(await screen.findByRole("dialog")).getByRole("combobox");
    await userEvent.selectOptions(picker, NHU_ID);
    expect(screen.getByTestId("assignee-load")).toHaveTextContent("còn 3/8 hôm nay");
  });

  it("reads a null load as no load", () => {
    expect(
      assigneeLoadLabel({
        name: "Quỳnh Như",
        tokens_budget_today: null,
        tokens_left_today: null,
        tokens_open: null,
        open_tasks: null,
      }),
    ).toBe("Quỳnh Như");
  });

  it("keeps only the work figures in Theo ban", async () => {
    const ban = (role: string, label: string, members: unknown[] = []) => ({
      role,
      label,
      budget: 0,
      used: 0,
      left: 0,
      open_tokens: 0,
      open_tasks: 1,
      done: 3,
      in_progress: 2,
      overdue: 1,
      late: 0,
      members,
    });
    stubFetch([
      {
        match: "/api/units/ADS/ban-stats",
        body: {
          date_from: "2026-10-01",
          date_to: "2026-10-31",
          today: "2026-10-10",
          my_ban: "DUNG",
          effort_enabled: false,
          bans: [
            ban("BIEN_TAP", "Biên kịch"),
            ban("DUNG", "Dựng", [
              {
                user_id: "u-1",
                full_name: "Quỳnh Như",
                is_lead: false,
                budget: 0,
                used: 0,
                left: 0,
                today_left: 0,
                open_tokens: 0,
                open_tasks: 1,
                done: 2,
              },
            ]),
          ],
        },
      },
    ]);
    function Host() {
      const [chosen, setChosen] = useState<string | null>(null);
      return (
        <BanStatsPanel dateFrom="2026-10-01" dateTo="2026-10-31" ban={chosen} onBan={setChosen} />
      );
    }
    renderWithQuery(<Host />);
    const mine = await screen.findByTestId("ban-DUNG");
    for (const label of ["Việc xong", "Đang làm", "Trễ hạn"]) {
      expect(within(mine).getByText(label)).toBeInTheDocument();
    }
    for (const label of ["Quỹ token", "Đã dùng", "Còn lại", /Đang ôm/]) {
      expect(within(mine).queryByText(label)).not.toBeInTheDocument();
    }
    expect(mine.querySelector("[data-token-badge]")).toBeNull();
    // A member row: the name and "Xong", nothing else.
    expect(
      within(mine).getAllByRole("columnheader").map((head) => head.textContent),
    ).toEqual(["Thành viên", "Xong"]);
    expect(
      within(within(mine).getByText("Quỳnh Như").closest("tr") as HTMLElement)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Quỳnh Như", "2"]);

    // "Tất cả": the bans with their people and work, no token column.
    await userEvent.click(screen.getByRole("tab", { name: "Tất cả" }));
    const all = await screen.findByTestId("ban-all");
    expect(
      within(all).getAllByRole("columnheader").map((head) => head.textContent),
    ).toEqual(["Ban", "Thành viên", "Xong", "Đang làm", "Trễ hạn"]);
    expect(
      within(within(all).getByText("Tổng").closest("tr") as HTMLElement)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Tổng", "1", "6", "4", "2"]);
  });

  it("hides the token fields of the unit panel", async () => {
    stubPanel("OWNER", ADMIN_ME(false));
    renderWithQuery(<RosterAndSettings />);
    const toggle = await screen.findByRole("switch", { name: "Tính effort (token)" });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText("Quỳnh Như")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Token/ngày" })).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: /Token\/ngày/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Thứ 7 làm buổi sáng/ })).not.toBeInTheDocument();
    // What is not about tokens stays.
    expect(screen.getByRole("spinbutton", { name: /Mốc sản lượng/ })).toBeInTheDocument();
  });

  it("shows them while it is on", async () => {
    stubPanel("OWNER", ADMIN_ME(true));
    renderWithQuery(<RosterAndSettings />);
    expect(await screen.findByRole("switch", { name: "Tính effort (token)" })).toBeChecked();
    expect(screen.getByRole("columnheader", { name: "Token/ngày" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: /Token\/ngày mặc định/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Thứ 7 làm buổi sáng/ })).toBeInTheDocument();
  });

  it("leaves the account page without its effort card and column", async () => {
    pathname = "/account";
    stubFetch([
      { match: "/api/units/me", body: ME_AS("DUNG", false) },
      { match: "/api/account/members", body: { month: "2026-10", members: ACCOUNT_ROWS } },
      { match: "/api/account/me", body: ACCOUNT_ME("OWNER") },
    ]);
    renderWithQuery(<AccountPage />);
    const cards = await screen.findByRole("list", { name: "Chỉ số chính" });
    await waitFor(() => expect(within(cards).queryByText("Effort")).not.toBeInTheDocument());
    expect(within(cards).getByText("Điểm hiệu suất")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Effort tuần này" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Thành viên" }));
    const table = await screen.findByRole("table");
    expect(within(table).queryByRole("columnheader", { name: "Effort" })).not.toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Điểm HS" })).toBeInTheDocument();
  });
});

// --- The switch -----------------------------------------------------------------------

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

/** The viewer's own row, and another OWNER: neither may be deleted. */
const SELF = {
  ...MEMBER,
  user_id: SESSION.user_id,
  full_name: "Chính Tôi",
  base_role: "OWNER",
  base_role_label: "Chủ sở hữu",
  role: "HEAD",
  role_label: "Trưởng phòng ADS",
  function_tag: null,
  member_code: "TOI",
};
const OTHER_OWNER = { ...SELF, user_id: BOSS_ID, full_name: "Sếp Lớn", member_code: "SEP" };

function stubPanel(
  role: string,
  me: unknown,
  extra: Array<{ match: string; status?: number; body?: unknown; method?: string }> = [],
) {
  return stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role } },
    { match: "/api/units/me", body: me },
    {
      match: "/api/units/ADS/members",
      body: {
        unit: "ADS",
        unit_label: "ORD",
        members: [MEMBER, SELF, OTHER_OWNER],
        assignable_roles: [],
      },
    },
    { match: "/api/units/ADS/settings", method: "PATCH", body: ADS_SETTINGS },
    { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
    { match: "/api/units/directory", body: [] },
    { match: "/api/units/untagged", body: { users: [] } },
    { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
    { match: "/api/units/ADS/platforms", body: { platforms: [] } },
    { match: "/api/units/ADS/durations", body: { durations: [] } },
  ]);
}

describe('the "Tính effort (token)" switch', () => {
  it("is disabled, with the reason, for anybody but the OWNER", async () => {
    stubPanel("ADMIN", ADMIN_ME(true));
    renderWithQuery(<RosterAndSettings />);
    const toggle = await screen.findByRole("switch", { name: "Tính effort (token)" });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getByText("Chỉ chủ sở hữu đổi được")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lưu thiết lập" })).toBeDisabled();
  });

  it("saves effort_enabled for the OWNER, through the settings save", async () => {
    const stub = stubPanel("OWNER", ADMIN_ME(true));
    renderWithQuery(<RosterAndSettings />);
    const toggle = await screen.findByRole("switch", { name: "Tính effort (token)" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(screen.queryByText("Chỉ chủ sở hữu đổi được")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Lưu thiết lập" }));
    await confirm();
    await waitFor(() => {
      const patch = calls(stub).find(
        (call) => call.method === "PATCH" && call.url.includes("/settings"),
      );
      expect(patch?.body).toEqual({ effort_enabled: false });
    });
  });
});

// --- 3. Delete ------------------------------------------------------------------------

describe('"Xoá task"', () => {
  it("is not drawn without can_delete", async () => {
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      { match: `/api/tasks/${DETAIL.task.code}`, body: DETAIL },
    ]);
    renderWithQuery(<TaskDetailPage />);
    await screen.findByRole("button", { name: "Giao Dựng" });
    expect(screen.queryByRole("button", { name: "Xoá task" })).not.toBeInTheDocument();
  });

  it("asks, sends DELETE and goes back to the stream's task table", async () => {
    const stub = stubFetch([
      { match: `/api/tasks/${TASK_ID}`, method: "DELETE", status: 204 },
      { match: "/api/units/me", body: ME_AS("HEAD") },
      { match: `/api/tasks/${DETAIL.task.code}`, body: { ...DETAIL, can_delete: true } },
    ]);
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Xoá task" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Xoá hẳn task TUAN-D-261010-01?");
    expect(dialog).toHaveTextContent(
      "Không khôi phục được: bài nộp, lịch sử, token đã tính đều mất.",
    );
    // Nothing is sent until the dialog is confirmed.
    expect(calls(stub).some((call) => call.method === "DELETE")).toBe(false);
    await confirm();
    await waitFor(() => expect(PUSHED).toEqual(["/tasks?unit=ADS"]));
    expect(calls(stub).filter((call) => call.method === "DELETE").map((call) => call.url)).toEqual([
      `/api/tasks/${TASK_ID}`,
    ]);
  });

  it("is there for a task with no action left, and shows a refusal on the page", async () => {
    stubFetch([
      {
        match: `/api/tasks/${TASK_ID}`,
        method: "DELETE",
        status: 403,
        body: {
          error: {
            code: "task_delete_forbidden",
            message: "Chỉ chủ sở hữu được xoá task.",
            details: {},
          },
        },
      },
      { match: "/api/units/me", body: ME_AS("HEAD") },
      {
        match: `/api/tasks/${DETAIL.task.code}`,
        body: { ...DETAIL, actions: [], can_delete: true },
      },
    ]);
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Xoá task" }));
    await confirm();
    expect(await screen.findByText("Chỉ chủ sở hữu được xoá task.")).toBeInTheDocument();
    expect(PUSHED).toEqual([]);
  });
});

const deleteButton = (name: string) =>
  screen.queryByRole("button", { name: `Xoá hẳn tài khoản ${name}` });

describe('"Xoá" on a unit roster', () => {
  it("is the OWNER's, for somebody who is neither an OWNER nor themselves", async () => {
    const stub = stubPanel("OWNER", ADMIN_ME(true), [
      { match: `/api/account/members/${NHU_ID}`, method: "DELETE", status: 204 },
    ]);
    renderWithQuery(<UnitPanel code="ADS" />);
    const button = await screen.findByRole("button", { name: "Xoá hẳn tài khoản Quỳnh Như" });
    expect(button).toHaveTextContent("Xoá");
    expect(deleteButton("Chính Tôi")).not.toBeInTheDocument();
    expect(deleteButton("Sếp Lớn")).not.toBeInTheDocument();

    await userEvent.click(button);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Xoá hẳn tài khoản Quỳnh Như?");
    expect(dialog).toHaveTextContent(
      "Không khôi phục được. Order và công việc cũ vẫn giữ, ghi tên kèm “(đã xoá)”. Muốn tạm khoá thì dùng Vô hiệu hoá.",
    );
    expect(calls(stub).some((call) => call.method === "DELETE")).toBe(false);
    const before = calls(stub).filter((call) => call.url.includes("/api/units/ADS/members")).length;
    await confirm();
    await waitFor(() =>
      expect(
        calls(stub).filter((call) => call.method === "DELETE").map((call) => call.url),
      ).toEqual([`/api/account/members/${NHU_ID}`]),
    );
    // The roster is read again.
    await waitFor(() =>
      expect(
        calls(stub).filter((call) => call.url.includes("/api/units/ADS/members")).length,
      ).toBeGreaterThan(before),
    );
  });

  it("is not drawn for an ADMIN", async () => {
    stubPanel("ADMIN", ADMIN_ME(true));
    renderWithQuery(<UnitPanel code="ADS" />);
    // The ADMIN's own control on the row: the session has arrived.
    await screen.findByRole("button", { name: "Vô hiệu hoá tài khoản Quỳnh Như" });
    expect(screen.queryByRole("button", { name: /Xoá hẳn tài khoản/ })).not.toBeInTheDocument();
  });

  it("shows the server's sentence when the member still holds open work", async () => {
    stubPanel("OWNER", ADMIN_ME(true), [
      {
        match: `/api/account/members/${NHU_ID}`,
        method: "DELETE",
        status: 422,
        body: {
          error: {
            code: "member_has_open_work",
            message: "Thành viên này còn order hoặc công đoạn đang mở. Bàn giao xong rồi xoá.",
            details: { reason: "member_has_open_work" },
          },
        },
      },
    ]);
    renderWithQuery(<UnitPanel code="ADS" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Xoá hẳn tài khoản Quỳnh Như" }),
    );
    await confirm();
    expect(
      await screen.findByText(
        "Thành viên này còn order hoặc công đoạn đang mở. Bàn giao xong rồi xoá.",
      ),
    ).toBeInTheDocument();
    // Refused: the member is still on the roster, the button still there.
    expect(deleteButton("Quỳnh Như")).toBeInTheDocument();
  });
});

const ACCOUNT_STATS = {
  month: "2026-10",
  points: 12.5,
  nodes_done: 7,
  nodes_in_progress: 3,
  revisions: 2,
  orders_created: 4,
  orders_completed: 1,
  pr_contents_owned: 5,
  pr_productions_done: 6,
  pr_approvals: 9,
  work_items_counted: 11,
  on_time_rate: 0.75,
  first_pass_rate: 0.6,
  late_count: 2,
  tokens_used: 30,
  tokens_budget: 40,
  effort_rate: 0.75,
  performance_score: 82.5,
  output_target: 10,
};

const ACCOUNT_ME = (role: string) => ({
  user_id: SESSION.user_id,
  telegram_user_id: 123456789,
  telegram_username: "tuan_ads",
  full_name: "Chính Tôi",
  role,
  role_label: role,
  units: [{ code: "ADS", label: "Luồng Order (ORD)", role_label: "Dựng", member_code: "TOI" }],
  must_change_password: false,
  password_changed_at: "2026-10-01T03:00:00Z",
  stats: ACCOUNT_STATS,
});

const accountRow = (userId: string, name: string, role: string) => ({
  user_id: userId,
  full_name: name,
  telegram_user_id: 111,
  units: ["ADS"],
  role,
  role_label: role,
  active: true,
  last_login_at: null,
  has_custom_password: true,
  locked: false,
  stats: ACCOUNT_STATS,
});

const ACCOUNT_ROWS = [
  accountRow(NHU_ID, "Quỳnh Như", "EMPLOYEE"),
  accountRow(SESSION.user_id, "Chính Tôi", "OWNER"),
  accountRow(BOSS_ID, "Sếp Lớn", "OWNER"),
];

describe('"Xoá thành viên" on the account members tab', () => {
  it("is the OWNER's, and sends DELETE behind the confirmation", async () => {
    pathname = "/account";
    const stub = stubFetch([
      { match: `/api/account/members/${NHU_ID}`, method: "DELETE", status: 204 },
      { match: "/api/account/members", body: { month: "2026-10", members: ACCOUNT_ROWS } },
      { match: "/api/account/me", body: ACCOUNT_ME("OWNER") },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Thành viên" }));
    const button = await screen.findByRole("button", { name: "Xoá hẳn tài khoản Quỳnh Như" });
    expect(button).toHaveTextContent("Xoá thành viên");
    expect(deleteButton("Chính Tôi")).not.toBeInTheDocument();
    expect(deleteButton("Sếp Lớn")).not.toBeInTheDocument();
    await userEvent.click(button);
    expect(await screen.findByRole("dialog")).toHaveTextContent("Xoá hẳn tài khoản Quỳnh Như?");
    await confirm();
    await waitFor(() =>
      expect(
        calls(stub).filter((call) => call.method === "DELETE").map((call) => call.url),
      ).toEqual([`/api/account/members/${NHU_ID}`]),
    );
  });

  it("is not drawn for an ADMIN", async () => {
    pathname = "/account";
    stubFetch([
      { match: "/api/account/members", body: { month: "2026-10", members: ACCOUNT_ROWS } },
      { match: "/api/account/me", body: ACCOUNT_ME("ADMIN") },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Thành viên" }));
    await screen.findByRole("button", { name: "Vô hiệu hoá tài khoản Quỳnh Như" });
    expect(screen.queryByRole("button", { name: /Xoá hẳn tài khoản/ })).not.toBeInTheDocument();
  });
});

// --- 4. Quick-filter counts -----------------------------------------------------------

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
  cells: [
    {
      key: "DUNG",
      label: "Dựng",
      person_name: "Quỳnh Như",
      status: "DANG_LAM",
      status_label: "Đang làm",
      is_current: true,
      since: null,
      revisions: 0,
      tokens: 3.5,
    },
  ],
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
};

const PAGE = { unit: "ADS", items: [ROW], total: 1, limit: 10, offset: 0, phases: [] };
const COUNTS = { mine: 8, awaiting_me: 3, priority: 2, urgent: 1, overdue: 0 };
const ADS_ROSTER = {
  match: "/api/units/ADS/members",
  body: { unit: "ADS", unit_label: "ORD", members: [], assignable_roles: [] },
};
const KINDS = { match: "/api/units/ADS/video-kinds", body: { kinds: [] } };

const quickLabels = () =>
  within(screen.getByRole("group", { name: "Lọc nhanh" }))
    .getAllByRole("button")
    .map((button) => button.textContent);
const countCalls = (stub: unknown) =>
  calls(stub).filter((call) => call.url.includes("/api/board/counts"));

describe("the quick filters' counts", () => {
  it("puts (n) after every label, asked for the unit on screen", async () => {
    const stub = stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      ADS_ROSTER,
      KINDS,
      { match: "/api/board/counts", body: COUNTS },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    await waitFor(() =>
      expect(quickLabels()).toEqual([
        "Task của tôi (8)",
        "Chờ tôi xử lý (3)",
        "Ưu tiên (2)",
        "Gấp (1)",
        "Trễ hạn (0)",
      ]),
    );
    expect(countCalls(stub)[0].url).toBe("/api/board/counts?unit=ADS");
    // The counts ignore the other filters: only the unit is sent.
    await userEvent.click(screen.getByRole("button", { name: "Gấp (1)" }));
    await waitFor(() => expect(SEARCH.value.get("urgent")).toBe("true"));
    expect(countCalls(stub).every((call) => call.url === "/api/board/counts?unit=ADS")).toBe(true);
  });

  it("asks for PR on the PR table, which has no Trễ hạn", async () => {
    SEARCH.value = new URLSearchParams("unit=PR");
    const stub = stubFetch([
      {
        match: "/api/units/me",
        body: {
          ...ME_AS("MEMBER"),
          units: [{ ...ME_AS("MEMBER").units[0], code: "PR", label: "Luồng PR" }],
          default_unit: "PR",
        },
      },
      {
        match: "/api/units/PR/members",
        body: { unit: "PR", unit_label: "PR", members: [], assignable_roles: [] },
      },
      { match: "/api/board/counts", body: { ...COUNTS, overdue: 0 } },
      { match: "/api/board/tasks", body: { ...PAGE, unit: "PR", items: [{ ...ROW, unit: "PR" }] } },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    await waitFor(() =>
      expect(quickLabels()).toEqual([
        "Task của tôi (8)",
        "Chờ tôi xử lý (3)",
        "Ưu tiên (2)",
        "Gấp (1)",
      ]),
    );
    expect(countCalls(stub)[0].url).toBe("/api/board/counts?unit=PR");
  });

  it("shows no (n) while the first counts are on their way", async () => {
    const stub = stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      ADS_ROSTER,
      KINDS,
      { match: "/api/board/tasks", body: PAGE },
    ]);
    // The counts never answer; everything else does.
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("/api/board/counts")
        ? new Promise<Response>(() => {})
        : (stub as unknown as typeof fetch)(input, init),
    );
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    expect(quickLabels()).toEqual([
      "Task của tôi",
      "Chờ tôi xử lý",
      "Ưu tiên",
      "Gấp",
      "Trễ hạn",
    ]);
  });

  it("reads the counts again when a row action changes the list", async () => {
    const stub = stubFetch([
      { match: "/api/orders/o-1/approve", method: "POST", body: {} },
      { match: "/api/units/me", body: ME_AS("HEAD") },
      ADS_ROSTER,
      KINDS,
      { match: "/api/board/counts", body: COUNTS },
      {
        match: "/api/board/tasks",
        body: { ...PAGE, items: [{ ...ROW, status: "ORDER_PENDING" }] },
      },
    ]);
    renderWithQuery(<TasksPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Duyệt" }));
    await waitFor(() => expect(countCalls(stub).length).toBeGreaterThan(0));
    const before = countCalls(stub).length;
    await confirm();
    await waitFor(() => expect(countCalls(stub).length).toBeGreaterThan(before));
  });

  it("follows the list: a list that comes back later brings fresh counts", async () => {
    const started = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(started);
    try {
      const stub = stubFetch([
        { match: "/api/units/me", body: ME_AS("HEAD") },
        ADS_ROSTER,
        KINDS,
        { match: "/api/board/counts", body: COUNTS },
        { match: "/api/board/tasks", body: PAGE },
      ]);
      renderWithQuery(<TasksPage />);
      await screen.findByRole("button", { name: "Gấp (1)" });
      await screen.findByText("Video serum mùa đông");
      // Both arrived together: one request for the counts, not two.
      expect(countCalls(stub)).toHaveLength(1);
      // Five seconds on, a filter brings a new list - and the badges with it.
      clock.mockReturnValue(started + 5000);
      await userEvent.click(screen.getByRole("button", { name: "Gấp (1)" }));
      await waitFor(() => expect(countCalls(stub)).toHaveLength(2));
    } finally {
      clock.mockRestore();
    }
  });

  it("draws a cell's token badge only while effort is on", async () => {
    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD", false) },
      ADS_ROSTER,
      KINDS,
      { match: "/api/board/counts", body: COUNTS },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    const off = renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    expect(document.querySelector("[data-token-badge]")).toBeNull();
    off.unmount();

    stubFetch([
      { match: "/api/units/me", body: ME_AS("HEAD") },
      ADS_ROSTER,
      KINDS,
      { match: "/api/board/counts", body: COUNTS },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Video serum mùa đông");
    expect(document.querySelector("[data-token-badge]")).toHaveTextContent("3.5 token");
  });
});
