/**
 * "Quản trị › Quản lý tài khoản" (2026-10-17): accounts on one flat table,
 * department chips instead of stream tags, seats changed from the row.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountsAdminPage from "@/app/admin/accounts/page";
import UnitsAdminPage from "@/app/admin/units/page";
import { adminTabsFor, headsDepartments } from "@/components/admin-tabs";
import type { UnitsMe } from "@/lib/api";
import { confirm, renderWithQuery, SESSION, stubFetch } from "./helpers";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/accounts",
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const TK = "11111111-1111-1111-1111-111111111111";
const BT = "22222222-2222-2222-2222-222222222222";
const NHU = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const LUONG = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const NEW = "cccccccc-cccc-cccc-cccc-cccccccccccc";

const DEPARTMENTS = {
  departments: [
    { id: BT, code: "BT", name: "Biên Tập", function: "BIEN_TAP", active: true, sort_order: 1 },
    { id: TK, code: "TK", name: "Thiết Kế", function: "THIET_KE", active: true, sort_order: 2 },
  ],
};

const seat = (id: string, code: string, name: string, position: string) => ({
  department_id: id,
  code,
  name,
  function: code === "TK" ? "THIET_KE" : "BIEN_TAP",
  position,
  position_label: position === "HEAD" ? "Trưởng phòng" : "Nhân viên",
});

const STATS = {
  month: "2026-10",
  points: 0,
  nodes_done: 0,
  nodes_in_progress: 0,
  revisions: 0,
  on_time_rate: null,
  orders_created: 0,
  orders_completed: 0,
  pr_contents_owned: 0,
  pr_productions_done: 0,
  pr_approvals: 0,
  work_items_counted: 0,
};

const row = (
  user_id: string,
  full_name: string,
  extra: Record<string, unknown> = {},
) => ({
  user_id,
  full_name,
  telegram_user_id: 1000,
  units: [],
  role: "EMPLOYEE",
  role_label: "Nhân viên",
  active: true,
  last_login_at: null,
  has_custom_password: false,
  locked: false,
  stats: STATS,
  departments: [],
  ...extra,
});

const ROWS = {
  month: "2026-10",
  can_view_all: true,
  members: [
    row(NHU, "Quỳnh Như", {
      units: ["ADS"],
      role_label: "Thiết kế",
      member_code: "NHU",
      departments: [{ ...seat(TK, "TK", "Thiết Kế", "MEMBER"), manager_user_id: SESSION.user_id }],
    }),
    row(LUONG, "Hiền Lương", {
      units: ["ADS"],
      role_label: "Trưởng phòng Biên kịch",
      departments: [seat(BT, "BT", "Biên Tập", "HEAD")],
    }),
    row(NEW, "Người Mới"),
  ],
};

const ADS_ENTRY = (role: string, is_lead = false) => ({
  code: "ADS",
  label: "Luồng Order (ORD)",
  role,
  role_label: role,
  is_lead,
  member_code: null,
  personal_nas_url: null,
  settings: { default_daily_tokens: 6, effort_enabled: true },
});

const OWNER_ME = {
  units: [ADS_ENTRY("HEAD")],
  default_unit: "ADS",
  can_view_all: true,
  can_admin: ["PR", "ADS"],
  can_tag: ["PR", "ADS"],
  can_edit_members: ["PR", "ADS"],
  departments: [],
  effort_enabled: true,
};

/** The Design Leader: an EMPLOYEE heading Thiết Kế. */
const HEAD_ME = {
  units: [ADS_ENTRY("THIET_KE", true)],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  can_tag: [],
  can_edit_members: ["ADS"],
  departments: [seat(TK, "TK", "Thiết Kế", "HEAD")],
  effort_enabled: true,
};

const ORD_ROWS = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  assignable_roles: [],
  members: [
    {
      user_id: NHU,
      full_name: "Quỳnh Như",
      base_role: "EMPLOYEE",
      base_role_label: "Nhân viên",
      role: "THIET_KE",
      role_label: "Thiết kế",
      is_lead: false,
      member_code: "NHU",
      personal_nas_url: null,
      joined_at: "2026-10-01T02:00:00Z",
      left_at: null,
      active: true,
      daily_tokens: null,
      departments: [seat(TK, "TK", "Thiết Kế", "MEMBER")],
    },
  ],
};

function stubPage(role: string, me: Record<string, unknown>, rows = ROWS) {
  return stubFetch([
    { match: "/api/departments/", method: "PUT", body: { user_id: NEW, full_name: "Người Mới", position: "MEMBER" } },
    { match: `/api/account/members/${NHU}/deactivate`, method: "POST", status: 204 },
    { match: "/api/departments/", method: "DELETE", status: 204 },
    { match: `/api/units/ADS/members/${NHU}`, method: "PATCH", body: { ...ORD_ROWS.members[0], daily_tokens: 4 } },
    { match: "/api/auth/session", body: { ...SESSION, role } },
    { match: "/api/units/me", body: me },
    { match: "/api/account/members", body: rows },
    { match: "/api/departments", body: DEPARTMENTS },
    { match: "/api/units/ADS/members", body: ORD_ROWS },
    { match: "/api/units/directory", body: DIRECTORY },
  ]);
}

/** `/api/units/directory`: every account, with seats, for "Thêm người vào phòng". */
const OUTSIDER = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const DIRECTORY = [
  { user_id: NHU, full_name: "Quỳnh Như", base_role: "EMPLOYEE", base_role_label: "Nhân viên", active: true, units: ["ADS"], departments: [seat(TK, "TK", "Thiết Kế", "MEMBER")] },
  { user_id: LUONG, full_name: "Hiền Lương", base_role: "EMPLOYEE", base_role_label: "Nhân viên", active: true, units: ["ADS"], departments: [seat(BT, "BT", "Biên Tập", "HEAD")] },
  { user_id: NEW, full_name: "Người Mới", base_role: "EMPLOYEE", base_role_label: "Nhân viên", active: true, units: [], departments: [] },
  { user_id: OUTSIDER, full_name: "Người Phòng Khác", base_role: "EMPLOYEE", base_role_label: "Nhân viên", active: true, units: ["ADS"], departments: [seat(BT, "BT", "Biên Tập", "MEMBER")] },
  { user_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", full_name: "Chị Chủ", base_role: "OWNER", base_role_label: "Chủ sở hữu", active: true, units: [], departments: [] },
];

/** The fixtures above are JSON bodies; the pure helpers want the typed shape. */
const asMe = (me: unknown) => me as UnitsMe;

const calls = (stub: ReturnType<typeof stubFetch>) =>
  (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;

const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

describe("Quản lý tài khoản for the OWNER", () => {
  it("lists every account with department chips, a star on a head, and filters by department", async () => {
    stubPage("OWNER", OWNER_ME);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Quỳnh Như");
    expect(within(rowOf("Quỳnh Như")).getByText("Thiết Kế")).toHaveClass("dept-tag");
    const head = within(rowOf("Hiền Lương")).getByText("Biên Tập");
    expect(head).toHaveClass("dept-tag");
    expect(within(head).getByRole("img", { name: "Trưởng phòng" })).toBeInTheDocument();
    expect(within(rowOf("Người Mới")).getByText("Chưa có phòng ban")).toBeInTheDocument();
    // No stream tag anywhere on people.
    expect(screen.queryByText("ORD")).not.toBeInTheDocument();
    expect(document.querySelector(".unit-tag-ads")).toBeNull();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Lọc theo phòng ban" }), TK);
    expect(screen.getByText("Quỳnh Như")).toBeInTheDocument();
    expect(screen.queryByText("Hiền Lương")).not.toBeInTheDocument();
    expect(screen.queryByText("Người Mới")).not.toBeInTheDocument();
  });

  it("seats somebody in a department as head or member, and takes somebody out", async () => {
    const stub = stubPage("OWNER", OWNER_ME);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Người Mới");
    await userEvent.click(screen.getByRole("button", { name: "Xếp Người Mới vào phòng" }));
    const form = screen.getByRole("form", { name: "Xếp Người Mới vào phòng" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Phòng ban" }), BT);
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Chức vụ" }), "HEAD");
    // The dialog's own button is the confirmation (parameter-modal).
    await userEvent.click(within(form).getByRole("button", { name: "Xếp vào phòng" }));
    await waitFor(() => {
      const put = calls(stub).find((call) => call.method === "PUT");
      expect(put?.url).toBe(`/api/departments/${BT}/members`);
      expect(put?.body).toEqual({ user_id: NEW, position: "HEAD" });
    });

    await userEvent.click(screen.getByRole("button", { name: "Đưa Quỳnh Như ra khỏi Thiết Kế" }));
    await confirm();
    await waitFor(() =>
      expect(calls(stub).find((call) => call.method === "DELETE")?.url).toBe(
        `/api/departments/${TK}/members/${NHU}`,
      ),
    );
  });

  it("saves an ORD member's own Token/ngày, the unit default as the placeholder", async () => {
    const stub = stubPage("OWNER", OWNER_ME);
    renderWithQuery(<AccountsAdminPage />);
    const box = await screen.findByRole("spinbutton", { name: "Token/ngày của Quỳnh Như" });
    expect(box).toHaveValue(null);
    expect(box).toHaveAttribute("placeholder", "6");
    await userEvent.type(box, "4");
    await userEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const patch = calls(stub).find((call) => call.method === "PATCH");
      expect(patch?.url).toBe(`/api/units/ADS/members/${NHU}`);
      expect(patch?.body).toEqual({ personal_nas_url: null, daily_tokens: 4 });
    });
  });

  it("lets the OWNER pick anybody not yet in the department, head or member", async () => {
    const stub = stubPage("OWNER", OWNER_ME);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Quỳnh Như");
    await userEvent.click(screen.getByRole("button", { name: "Thêm người vào phòng" }));
    const form = await screen.findByRole("form", { name: "Thêm người vào phòng" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Phòng ban" }), TK);
    const people = within(form).getByRole("combobox", { name: "Tài khoản" });
    expect(within(people).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn tài khoản —",
      "Hiền Lương (Biên Tập)",
      "Người Mới (chưa có phòng ban)",
      "Người Phòng Khác (Biên Tập)",
      "Chị Chủ (chưa có phòng ban)",
    ]);
    await userEvent.selectOptions(people, OUTSIDER);
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Chức vụ" }), "HEAD");
    await userEvent.click(within(form).getByRole("button", { name: "Xếp vào phòng" }));
    await waitFor(() =>
      expect(calls(stub).find((call) => call.method === "PUT")?.body).toEqual({
        user_id: OUTSIDER,
        position: "HEAD",
      }),
    );
  });

  it("keeps the account controls in the row's ⋯ menu: role, password reset, deactivate, delete", async () => {
    const stub = stubPage("OWNER", OWNER_ME);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Quỳnh Như");
    const nhu = rowOf("Quỳnh Như");
    expect(within(nhu).getByRole("textbox", { name: "Mã thành viên của Quỳnh Như" })).toHaveValue("NHU");
    await userEvent.click(within(nhu).getByRole("button", { name: "Thao tác với Quỳnh Như" }));
    const menu = screen.getByRole("menu", { name: "Thao tác với Quỳnh Như" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Quyền",
      "Đổi vai trò hệ thống…",
      "Đặt lại mật khẩu…",
      "Vô hiệu hoá…",
      "Xoá tài khoản…",
    ]);
    expect(within(menu).getByRole("menuitem", { name: "Quyền" })).toHaveAttribute(
      "href",
      `/admin/people/${NHU}`,
    );
    // A destructive entry still goes through its confirmation.
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Vô hiệu hoá…" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent("Vô hiệu hoá tài khoản Quỳnh Như?");
    await confirm();
    await waitFor(() =>
      expect(calls(stub).find((call) => call.method === "POST")?.url).toBe(
        `/api/account/members/${NHU}/deactivate`,
      ),
    );
    // The role change is a dialog of its own.
    await userEvent.click(within(rowOf("Quỳnh Như")).getByRole("button", { name: "Thao tác với Quỳnh Như" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Đổi vai trò hệ thống…" }));
    expect(screen.getByRole("combobox", { name: "Vai trò hệ thống của Quỳnh Như" })).toBeInTheDocument();
  });
});

describe("Quản lý tài khoản for a department head", () => {
  const rows = {
    ...ROWS,
    can_view_all: false,
    members: [ROWS.members[0], ROWS.members[2], row(SESSION.user_id, "Tôi Trưởng Design", { departments: [seat(TK, "TK", "Thiết Kế", "HEAD")] })],
  };

  it("adds members to their own department only, as members, and has no account controls", async () => {
    const stub = stubPage("EMPLOYEE", HEAD_ME, rows);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Người Mới");
    // Already in Thiết Kế: nowhere else to put them.
    expect(screen.queryByRole("button", { name: "Xếp Quỳnh Như vào phòng" })).not.toBeInTheDocument();
    // Their own head's seat is not theirs to close.
    expect(screen.queryByRole("button", { name: /ra khỏi Thiết Kế$/ })).toHaveAttribute(
      "aria-label",
      "Đưa Quỳnh Như ra khỏi Thiết Kế",
    );
    await userEvent.click(screen.getByRole("button", { name: "Xếp Người Mới vào phòng" }));
    const form = screen.getByRole("form", { name: "Xếp Người Mới vào phòng" });
    const picker = within(form).getByRole("combobox", { name: "Phòng ban" });
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual(["Thiết Kế"]);
    expect(within(form).queryByRole("combobox", { name: "Chức vụ" })).not.toBeInTheDocument();
    await userEvent.click(within(form).getByRole("button", { name: "Xếp vào phòng" }));
    await waitFor(() =>
      expect(calls(stub).find((call) => call.method === "PUT")?.body).toEqual({
        user_id: NEW,
        position: "MEMBER",
      }),
    );
    // The account controls are OWNER / ADMIN's: no ⋯ menu at all.
    expect(screen.queryByRole("button", { name: /Thao tác với/ })).not.toBeInTheDocument();
  });

  it("adds somebody from outside their roster through the toolbar's picker", async () => {
    const stub = stubPage("EMPLOYEE", HEAD_ME, rows);
    renderWithQuery(<AccountsAdminPage />);
    await screen.findByText("Người Mới");
    // Not on the roster (the head lists only their own people), but in the directory.
    expect(screen.queryByText("Người Phòng Khác")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Thêm người vào phòng" }));
    const form = await screen.findByRole("form", { name: "Thêm người vào phòng" });
    const people = within(form).getByRole("combobox", { name: "Tài khoản" });
    // A head takes in only somebody seated nowhere: not the people of other
    // departments, not a system account, not themselves.
    expect(within(people).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn tài khoản —",
      "Người Mới (chưa có phòng ban)",
    ]);
    await userEvent.selectOptions(people, NEW);
    await userEvent.click(within(form).getByRole("button", { name: "Xếp vào phòng" }));
    await waitFor(() => {
      const put = calls(stub).find((call) => call.method === "PUT");
      expect(put?.url).toBe(`/api/departments/${TK}/members`);
      expect(put?.body).toEqual({ user_id: NEW, position: "MEMBER" });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Đã xếp Người Mới vào Thiết Kế.");
  });

  it("is a Quản trị tab for a head, and the old stream address lands here", () => {
    expect(headsDepartments(asMe(HEAD_ME))).toBe(true);
    expect(adminTabsFor(asMe(HEAD_ME), "EMPLOYEE").map((tab) => tab.key)).toEqual(["accounts"]);
    expect(adminTabsFor(asMe({ ...HEAD_ME, departments: [] }), "EMPLOYEE")).toEqual([]);
    expect(adminTabsFor(asMe(OWNER_ME), "OWNER").map((tab) => tab.href)).toEqual([
      "/admin/departments",
      "/admin/accounts",
      "/admin/settings",
      "/admin/ai",
    ]);
    renderWithQuery(<UnitsAdminPage />);
    expect(replace).toHaveBeenCalledWith("/admin/accounts");
  });
});
