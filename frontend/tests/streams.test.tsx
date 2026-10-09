/**
 * Streams ("Luồng"): the ORD rename, the untagged account, who may tag whom,
 * account deactivation, invites and the staff default of the task table.
 *
 * Every rule here is the server's (contract v1); the screens only draw what
 * `/api/units/me` and the session say, and send back exactly what the API
 * expects.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Shell } from "@/components/shell";
import { UnitPanel } from "@/components/unit-panel";
import TasksPage from "@/app/tasks/page";
import DashboardPage from "@/app/dashboard/page";
import AccountPage from "@/app/account/page";
import { renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
    refresh: vi.fn(),
    back: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  pathname = "/tasks";
  vi.unstubAllGlobals();
});

type Calls = Array<{ url: string; method: string; body: unknown }>;
const callsOf = (fetchMock: unknown) => (fetchMock as { calls: Calls }).calls;
const boardCalls = (fetchMock: unknown) =>
  callsOf(fetchMock)
    .map((call) => call.url)
    .filter((url) => url.startsWith("/api/board/tasks"));

const entry = (code: string, role: string, extra: Record<string, unknown> = {}) => ({
  code,
  label: code === "ADS" ? "Luồng Order (ORD)" : "Luồng PR",
  short_label: code === "ADS" ? "ORD" : "PR",
  role,
  role_label: role,
  is_lead: false,
  member_code: null,
  personal_nas_url: null,
  settings: {},
  ...extra,
});

const UNTAGGED_ME = {
  units: [],
  default_unit: null,
  can_view_all: false,
  can_admin: [],
  can_tag: [],
  is_untagged: true,
};

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

const ROW = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  unit_short_label: "ORD",
  id: "22222222-2222-2222-2222-222222222222",
  code: "TUAN-BT-261007-01",
  title: "Kịch bản A",
  kind: "BT",
  kind_label: "Biên kịch › Design",
  owner_user_id: "99999999-9999-9999-9999-999999999999",
  owner_name: "Tuấn",
  created_at: "2026-10-07T02:00:00Z",
  phase: "PRODUCTION",
  phase_label: "Sản xuất",
  status: "IN_PRODUCTION",
  status_label: "Đang làm",
  cells: [],
  product_link: null,
  returned_at: null,
  is_priority: false,
  urgent: false,
  detail_path: "/tasks/TUAN-BT-261007-01",
  version: 1,
  stage_since: null,
  revisions: 0,
  current_person_name: "Le Trưởng Nhóm",
  current_person_user_id: SESSION.user_id,
  latest_link: null,
  extras: [],
};

const PAGE = { unit: "ADS", items: [ROW], total: 1, limit: 10, offset: 0, phases: [] };

describe("an account with no stream", () => {
  it("gets a friendly empty state on /tasks and asks the board nothing", async () => {
    const fetchMock = stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: UNTAGGED_ME },
    ]);
    renderWithQuery(<TasksPage />);
    expect(
      await screen.findByText(
        "Tài khoản chưa được gắn luồng. Trưởng nhóm sẽ gắn luồng cho bạn.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Chọn luồng" })).not.toBeInTheDocument();
    expect(boardCalls(fetchMock)).toEqual([]);
  });

  it("gets the same empty state on /dashboard, also when units is just empty", async () => {
    const fetchMock = stubFetch([
      // An API that predates `is_untagged`: units=[] for a non-admin says it.
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
    ]);
    pathname = "/dashboard";
    renderWithQuery(<DashboardPage />);
    expect(await screen.findByRole("status", { name: "Chưa có luồng" })).toHaveTextContent(
      "Trưởng nhóm sẽ gắn luồng cho bạn.",
    );
    expect(callsOf(fetchMock).some((call) => call.url.startsWith("/api/board"))).toBe(false);
  });

  it("sees only the shared screens in the nav, and no PR chip", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: UNTAGGED_ME },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    expect(await screen.findByText("Chưa có luồng")).toBeInTheDocument();
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    expect(within(primary).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/dashboard",
      "/tasks",
      "/orders/new",
    ]);
  });
});

describe("the nav and the stream switch follow the tags", () => {
  it("shows only the shared screens while /api/units/me has not answered", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", status: 500, body: { detail: "lỗi" } },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await screen.findByText(/Le Trưởng Nhóm/);
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    expect(within(primary).queryByRole("link", { name: /Công việc/ })).not.toBeInTheDocument();
  });

  it("gives an ADMIN the PR screens and the admin screen, and the ORD chip", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "ADMIN" } },
      {
        match: "/api/units/me",
        body: {
          units: [entry("ADS", "HEAD", { function_tag: null })],
          default_unit: "ADS",
          can_view_all: true,
          can_admin: ["PR", "ADS"],
          can_tag: ["PR", "ADS"],
        },
      },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    await waitFor(() =>
      expect(within(primary).getByRole("link", { name: /Công việc/ })).toBeInTheDocument(),
    );
    expect(within(primary).getByRole("link", { name: /Quản trị đơn vị/ })).toBeInTheDocument();
    expect(screen.getByText("ORD")).toHaveClass("unit-tag-ads");
  });

  it("lists only the tagged streams, and no Tất cả without can_view_all", async () => {
    stubFetch([
      {
        match: "/api/units/me",
        body: {
          units: [entry("PR", "MEMBER"), entry("ADS", "HEAD")],
          default_unit: "PR",
          can_view_all: false,
          can_admin: [],
        },
      },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    const switcher = await screen.findByRole("navigation", { name: "Chọn luồng" });
    expect(within(switcher).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Luồng Order (ORD)",
      "Luồng PR",
    ]);
  });

  it("shows a one-stream person the plain ORD chip instead of a switch", async () => {
    stubFetch([
      {
        match: "/api/units/me",
        body: { units: [entry("ADS", "HEAD")], default_unit: "ADS", can_view_all: false, can_admin: [] },
      },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Kịch bản A");
    expect(screen.queryByRole("navigation", { name: "Chọn luồng" })).not.toBeInTheDocument();
    const heading = screen.getByRole("heading", { name: "Quản lý task" });
    expect(within(heading.parentElement as HTMLElement).getByText("ORD")).toHaveClass("unit-tag-ads");
    expect(screen.getByText(/Luồng Order \(ORD\) ·/)).toBeInTheDocument();
  });
});

describe("the task table's staff default", () => {
  const staffMe = {
    units: [entry("ADS", "BIEN_TAP")],
    default_unit: "ADS",
    can_view_all: false,
    can_admin: [],
  };

  it("opens a plain staff member on Task của tôi, rows awaiting them first", async () => {
    const fetchMock = stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: staffMe },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Kịch bản A");
    const calls = boardCalls(fetchMock);
    expect(calls.length).toBeGreaterThan(0);
    for (const url of calls) {
      expect(url).toContain("mine=true");
      expect(url).toContain("order=todo_first");
    }
    expect(screen.getByRole("button", { name: "Task của tôi" })).toHaveAttribute("aria-pressed", "true");
    // Held by the viewer: marked.
    const row = screen.getByText("TUAN-BT-261007-01").closest("tr") as HTMLElement;
    expect(within(row).getByText("Cần làm")).toBeInTheDocument();

    // Turning it off is said on the URL, so the default does not come back.
    await userEvent.click(screen.getByRole("button", { name: "Task của tôi" }));
    expect(SEARCH.value.get("mine")).toBe("false");
  });

  it("keeps an explicit mine=false off", async () => {
    SEARCH.value = new URLSearchParams("mine=false");
    const fetchMock = stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: staffMe },
      {
        match: "/api/board/tasks",
        body: { ...PAGE, items: [{ ...ROW, current_person_user_id: "someone-else", awaiting_me: false }] },
      },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByText("Kịch bản A");
    for (const url of boardCalls(fetchMock)) {
      expect(url).not.toContain("mine=");
      expect(url).not.toContain("order=");
    }
    expect(screen.getByRole("button", { name: "Task của tôi" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText("Cần làm")).not.toBeInTheDocument();
  });

  it("opens everything for a function lead, a head or a team lead", async () => {
    for (const [role, unitEntry] of [
      ["EMPLOYEE", entry("ADS", "BIEN_TAP", { is_lead: true })],
      ["EMPLOYEE", entry("ADS", "HEAD")],
      ["TEAM_LEAD", entry("ADS", "DUNG")],
    ] as const) {
      const fetchMock = stubFetch([
        { match: "/api/auth/session", body: { ...SESSION, role } },
        { match: "/api/units/me", body: { ...staffMe, units: [unitEntry] } },
        { match: "/api/board/tasks", body: PAGE },
      ]);
      const view = renderWithQuery(<TasksPage />);
      await screen.findByText("Kịch bản A");
      for (const url of boardCalls(fetchMock)) {
        expect(url).not.toContain("mine=");
        expect(url).not.toContain("order=");
      }
      view.unmount();
    }
  });

  it("uses the server's awaiting_me flag when it sends one", async () => {
    stubFetch([
      { match: "/api/units/me", body: staffMe },
      {
        match: "/api/board/tasks",
        body: { ...PAGE, items: [{ ...ROW, current_person_user_id: null, awaiting_me: true }] },
      },
    ]);
    renderWithQuery(<TasksPage />);
    expect(await screen.findByText("Cần làm")).toBeInTheDocument();
  });
});

// --- Member management --------------------------------------------------------

const ADS_ID = "55555555-5555-5555-5555-555555555555";
const OWNER_ID = "77777777-7777-7777-7777-777777777777";
const NEW_ID = "88888888-8888-8888-8888-888888888888";

const member = (user_id: string, full_name: string, extra: Record<string, unknown> = {}) => ({
  user_id,
  full_name,
  base_role: "EMPLOYEE",
  base_role_label: "Nhân viên",
  role: "BIEN_TAP",
  role_label: "Biên kịch",
  is_lead: false,
  member_code: null,
  personal_nas_url: null,
  joined_at: "2026-10-07T02:00:00Z",
  left_at: null,
  active: true,
  ...extra,
});

const ADS_MEMBERS = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  members: [
    member(ADS_ID, "Hiền Lương", { function_tag: "BT", is_lead: true, role_label: "Trưởng phòng Biên kịch" }),
    member(OWNER_ID, "Chị Chủ", { base_role: "OWNER", base_role_label: "Chủ sở hữu", role: "HEAD" }),
    member(SESSION.user_id, "Le Trưởng Nhóm", { base_role: "TEAM_LEAD", role: "HEAD" }),
  ],
  assignable_roles: [
    { role: "HEAD", label: "Trưởng phòng ORD" },
    { role: "BIEN_TAP", label: "Trưởng phòng Biên kịch", is_lead: true },
    { role: "BIEN_TAP", label: "Biên kịch" },
  ],
};

const PR_MEMBERS = {
  unit: "PR",
  unit_label: "Luồng PR",
  members: [
    member("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Hà Chi", { role: "MEMBER", role_label: "Thành viên" }),
    member(OWNER_ID, "Chị Chủ", { base_role: "OWNER", role: "MEMBER", role_label: "Thành viên" }),
    member(SESSION.user_id, "Le Trưởng Nhóm", { base_role: "TEAM_LEAD", role: "MEMBER", role_label: "Thành viên" }),
  ],
  assignable_roles: [{ role: "MEMBER", label: "Thành viên" }],
};

const UNTAGGED = {
  users: [
    {
      user_id: NEW_ID,
      full_name: "Người Mới",
      telegram_username: "nguoimoi",
      role: "EMPLOYEE",
      role_label: "Nhân viên",
      created_at: "2026-10-07T02:00:00Z",
      avatar_url: null,
    },
  ],
};

const leadMe = (canTag: string[]) => ({
  units: [entry("PR", "MEMBER"), entry("ADS", "HEAD")],
  default_unit: "PR",
  can_view_all: false,
  can_admin: [],
  can_tag: canTag,
});

const panelRoutes = (canTag: string[], role = "TEAM_LEAD") => [
  { match: "/api/auth/session", body: { ...SESSION, role } },
  { match: "/api/units/me", body: leadMe(canTag) },
  { match: "/api/units/ADS/members", method: "GET", body: ADS_MEMBERS },
  { match: "/api/units/PR/members", method: "GET", body: PR_MEMBERS },
  { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
  { match: "/api/units/PR/health", body: { unit: "PR", warnings: [] } },
  { match: "/api/units/untagged", body: UNTAGGED },
  {
    match: "/api/units/directory",
    body: [
      { user_id: ADS_ID, full_name: "Hiền Lương", base_role: "EMPLOYEE", base_role_label: "Nhân viên", active: true, units: ["ADS"] },
      { user_id: OWNER_ID, full_name: "Chị Chủ", base_role: "OWNER", base_role_label: "Chủ sở hữu", active: true, units: ["PR", "ADS"] },
    ],
  },
];

describe("tagging follows can_tag", () => {
  it("draws no tag controls in a stream the lead may not tag in", async () => {
    stubFetch(panelRoutes(["PR"]));
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    expect(await screen.findByText("Hiền Lương")).toBeInTheDocument();
    expect(screen.queryByLabelText("Thêm thành viên")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Gỡ" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Vai trò của Hiền Lương")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Chưa có luồng" })).not.toBeInTheDocument();
    // Nobody but OWNER / ADMIN changes an account's status.
    expect(screen.queryByRole("button", { name: /Vô hiệu hoá/ })).not.toBeInTheDocument();
    // [ORD][BT★]: the function tag, starred for its lead.
    const row = screen.getByText("Hiền Lương").closest("tr") as HTMLElement;
    expect(within(row).getByText("ORD")).toHaveClass("unit-tag-ads");
    const fn = row.querySelector('[data-function-tag="BT"]');
    expect(fn).toHaveTextContent("BT★");
    expect(fn).toHaveAttribute("title", "Trưởng phòng Biên kịch");
  });

  it("lets a lead tag in their own stream, but not untag an owner or themselves", async () => {
    stubFetch(panelRoutes(["PR"]));
    renderWithQuery(<UnitPanel code="PR" settings={false} />);
    expect(await screen.findByLabelText("Thêm thành viên")).toBeInTheDocument();
    const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;
    expect(within(rowOf("Hà Chi")).getByRole("button", { name: "Gỡ" })).toBeInTheDocument();
    expect(within(rowOf("Chị Chủ")).queryByRole("button", { name: "Gỡ" })).not.toBeInTheDocument();
    expect(within(rowOf("Le Trưởng Nhóm")).queryByRole("button", { name: "Gỡ" })).not.toBeInTheDocument();
    // The owner is not offered in the add picker either.
    const picker = screen.getByLabelText("Thêm thành viên");
    expect(within(picker).queryByText(/Chị Chủ/)).not.toBeInTheDocument();
    expect(within(picker).getByText(/Hiền Lương · đang ở ORD/)).toBeInTheDocument();
  });

  it("tags an untagged account into a stream picked in the dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/ADS/members", method: "POST", body: {} },
      ...panelRoutes(["PR", "ADS"]),
    ]);
    renderWithQuery(<UnitPanel code="PR" settings={false} />);
    const section = await screen.findByRole("region", { name: "Chưa có luồng" });
    await within(section).findByText("Người Mới");
    await userEvent.click(within(section).getByRole("button", { name: "Gắn Người Mới vào luồng" }));
    const dialog = await screen.findByRole("dialog");
    // It opens on the panel's own stream.
    expect(within(dialog).getByLabelText("Luồng")).toHaveValue("PR");
    await userEvent.selectOptions(within(dialog).getByLabelText("Luồng"), "ADS");
    await waitFor(() =>
      expect(
        within(within(dialog).getByLabelText("Vai trò trong luồng"))
          .getAllByRole("option")
          .map((option) => option.textContent),
      ).toEqual(["Trưởng phòng ORD", "Trưởng phòng Biên kịch", "Biên kịch"]),
    );
    await userEvent.selectOptions(within(dialog).getByLabelText("Vai trò trong luồng"), "BIEN_TAP:LEAD");
    expect(dialog).toHaveTextContent("Gắn Người Mới vào Luồng Order (ORD)?");
    expect(callsOf(fetchMock).some((call) => call.method === "POST")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Gắn vào luồng" }));
    await waitFor(() => {
      const sent = callsOf(fetchMock).find((call) => call.method === "POST");
      expect(sent?.url).toBe("/api/units/ADS/members");
      expect(sent?.body).toEqual({ user_id: NEW_ID, role: "BIEN_TAP", is_lead: true });
    });
  });
});

describe("deactivating and reactivating an account", () => {
  it("lets an ADMIN deactivate a member behind a destructive confirmation", async () => {
    const fetchMock = stubFetch([
      { match: `/api/account/members/${ADS_ID}/deactivate`, method: "POST", status: 204 },
      ...panelRoutes(["PR", "ADS"], "ADMIN"),
    ]);
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    await screen.findByText("Hiền Lương");
    // Not on themselves, and an ADMIN never on an OWNER.
    expect(screen.queryByRole("button", { name: "Vô hiệu hoá tài khoản Chị Chủ" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Vô hiệu hoá tài khoản Le Trưởng Nhóm" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Vô hiệu hoá tài khoản Hiền Lương" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Vô hiệu hoá tài khoản Hiền Lương?");
    expect(dialog).toHaveTextContent("đăng xuất khỏi mọi phiên web");
    expect(callsOf(fetchMock).some((call) => call.method === "POST")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Vô hiệu hoá" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "POST")?.url).toBe(
        `/api/account/members/${ADS_ID}/deactivate`,
      ),
    );
  });

  it("hides deactivated accounts until asked, then greys them and offers Kích hoạt lại", async () => {
    const gone = member(NEW_ID, "Đã Nghỉ", { active: false, account_active: false });
    const fetchMock = stubFetch([
      { match: `/api/account/members/${NEW_ID}/reactivate`, method: "POST", status: 204 },
      {
        match: "/api/units/ADS/members?include_inactive=true",
        method: "GET",
        body: { ...ADS_MEMBERS, members: [...ADS_MEMBERS.members, gone] },
      },
      ...panelRoutes(["PR", "ADS"], "OWNER").map((route) =>
        route.match === "/api/units/ADS/members"
          ? { ...route, body: { ...ADS_MEMBERS, members: [...ADS_MEMBERS.members, gone] } }
          : route,
      ),
    ]);
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    await screen.findByText("Hiền Lương");
    expect(screen.queryByText("Đã Nghỉ")).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Hiện cả tài khoản đã vô hiệu hoá"));
    const name = await screen.findByText("Đã Nghỉ");
    expect(
      callsOf(fetchMock).some((call) => call.url === "/api/units/ADS/members?include_inactive=true"),
    ).toBe(true);
    const row = name.closest("tr") as HTMLElement;
    expect(row).toHaveAttribute("data-inactive", "true");
    expect(within(row).getByText("Đã vô hiệu hoá")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Kích hoạt lại tài khoản Đã Nghỉ" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Kích hoạt lại" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "POST")?.url).toBe(
        `/api/account/members/${NEW_ID}/reactivate`,
      ),
    );
  });

  it("does the same on the account member list, with include_inactive on the request", async () => {
    const rows = [
      {
        user_id: ADS_ID,
        full_name: "Hiền Lương",
        telegram_user_id: 111,
        units: ["ADS"],
        role: "EMPLOYEE",
        role_label: "Nhân viên",
        active: false,
        function_tag: "D",
        is_lead: false,
        last_login_at: null,
        has_custom_password: true,
        locked: false,
        stats: ACCOUNT_STATS,
      },
    ];
    const fetchMock = stubFetch([
      { match: "/api/account/members", body: { month: "2026-10", members: rows } },
      { match: "/api/account/me", body: { ...ACCOUNT_ME, role: "ADMIN", role_label: "Quản trị viên" } },
      { match: "/api/units/me", status: 404, body: { detail: "không" } },
      { match: "/api/invites", body: { items: [], total: 0 } },
    ]);
    pathname = "/account";
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Thành viên" }));
    await userEvent.click(screen.getByLabelText("Hiện cả tài khoản đã vô hiệu hoá"));
    await waitFor(() =>
      expect(
        callsOf(fetchMock).some(
          (call) => call.url === "/api/account/members?month=2026-10&unit=ALL&include_inactive=true",
        ),
      ).toBe(true),
    );
    const row = screen.getByText("Hiền Lương").closest("tr") as HTMLElement;
    expect(within(row).getByText("Đã vô hiệu hoá")).toBeInTheDocument();
    expect(row.querySelector('[data-function-tag="D"]')).toHaveTextContent("D");
    expect(within(row).getByRole("button", { name: "Kích hoạt lại tài khoản Hiền Lương" })).toBeInTheDocument();
  });
});

// --- Invites --------------------------------------------------------------------

const ACCOUNT_STATS = {
  month: "2026-10",
  points: 0,
  nodes_done: 0,
  nodes_in_progress: 0,
  revisions: 0,
  orders_created: 0,
  orders_completed: 0,
  pr_contents_owned: 0,
  pr_productions_done: 0,
  pr_approvals: 0,
  work_items_counted: 0,
  on_time_rate: null,
};

const ACCOUNT_ME = {
  user_id: SESSION.user_id,
  telegram_user_id: 123456789,
  telegram_username: null,
  full_name: "Le Trưởng Nhóm",
  role: "TEAM_LEAD",
  role_label: "Trưởng nhóm",
  units: [{ code: "PR", label: "Luồng PR", short_label: "PR", role_label: "Thành viên", member_code: null }],
  must_change_password: false,
  password_changed_at: null,
  stats: ACCOUNT_STATS,
};

const FORBIDDEN = {
  error: { code: "account_members_forbidden", message: "Không có quyền xem.", details: {} },
};

const OPEN_INVITE = {
  id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  role: "EMPLOYEE",
  role_label: "Nhân viên",
  scope: null,
  note: "Dựng tháng 9",
  expires_at: "2026-10-14T02:00:00Z",
  max_uses: 1,
  use_count: 0,
  active: true,
  created_at: "2026-10-07T02:00:00Z",
};

describe("the invite panel", () => {
  beforeEach(() => {
    pathname = "/account";
  });

  it("is not offered to an employee", async () => {
    stubFetch([
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: { ...ACCOUNT_ME, role: "EMPLOYEE", role_label: "Nhân viên" } },
    ]);
    renderWithQuery(<AccountPage />);
    await screen.findByRole("tab", { name: "Thông tin tài khoản" });
    expect(screen.queryByRole("tab", { name: "Mời thành viên" })).not.toBeInTheDocument();
  });

  it("lets a team lead create a staff invite, shows the code once with a copy button and lists open ones", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const fetchMock = stubFetch([
      {
        match: "/api/invites",
        method: "POST",
        body: { ...OPEN_INVITE, id: "ffffffff-ffff-ffff-ffff-ffffffffffff", note: "Dựng mới", code: "ABC123XYZ", bot_username: "meobot" },
      },
      { match: "/api/invites", method: "GET", body: { items: [OPEN_INVITE, { ...OPEN_INVITE, id: "x", active: false, note: "Cũ" }], total: 2 } },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ACCOUNT_ME },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Mời thành viên" }));
    expect(
      screen.getByText("Người được mời sẽ chưa thuộc luồng nào cho đến khi trưởng nhóm gắn luồng."),
    ).toBeInTheDocument();
    // Only open invites are listed.
    const list = await screen.findByRole("list", { name: "Mã mời còn hiệu lực" });
    expect(within(list).getByText("Dựng tháng 9")).toBeInTheDocument();
    expect(within(list).queryByText("Cũ")).not.toBeInTheDocument();
    // A team lead invites staff only.
    const role = screen.getByLabelText("Vai trò");
    expect(role).toBeDisabled();
    expect(within(role).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual(["EMPLOYEE"]);

    await userEvent.type(screen.getByLabelText("Ghi chú (không bắt buộc)"), "Dựng mới");
    await userEvent.click(screen.getByRole("button", { name: "Tạo mã mời" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Tạo mã mời" }));
    await waitFor(() => {
      const sent = callsOf(fetchMock).find((call) => call.method === "POST");
      expect(sent?.url).toBe("/api/invites");
      expect(sent?.body).toEqual({ role: "EMPLOYEE", note: "Dựng mới" });
    });
    const shown = await screen.findByRole("status", { name: "Mã mời vừa tạo" });
    expect(within(shown).getByText("ABC123XYZ")).toBeInTheDocument();
    expect(within(shown).getByRole("link", { name: "https://t.me/meobot?start=ABC123XYZ" })).toHaveAttribute(
      "href",
      "https://t.me/meobot?start=ABC123XYZ",
    );
    fireEvent.click(within(shown).getByRole("button", { name: "Sao chép mã" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("ABC123XYZ"));
    expect(await within(shown).findByRole("button", { name: "Đã sao chép" })).toBeInTheDocument();
  });

  it("lets an OWNER invite a team lead", async () => {
    const fetchMock = stubFetch([
      { match: "/api/invites", method: "POST", body: { ...OPEN_INVITE, role: "TEAM_LEAD", role_label: "Trưởng nhóm", code: "LEAD42" } },
      { match: "/api/invites", method: "GET", body: { items: [], total: 0 } },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: { ...ACCOUNT_ME, role: "OWNER", role_label: "Chủ sở hữu" } },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Mời thành viên" }));
    expect(await screen.findByText("Chưa có mã mời nào còn hiệu lực.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Vai trò"), "TEAM_LEAD");
    await userEvent.click(screen.getByRole("button", { name: "Tạo mã mời" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Tạo mã mời" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "POST")?.body).toEqual({
        role: "TEAM_LEAD",
        note: null,
      }),
    );
    // No bot name from the API: the code alone, no link.
    const shown = await screen.findByRole("status", { name: "Mã mời vừa tạo" });
    expect(within(shown).getByText("LEAD42")).toBeInTheDocument();
    expect(within(shown).queryByRole("link")).not.toBeInTheDocument();
  });
});
