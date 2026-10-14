/**
 * "Phòng ban": the departments screen (list / create / edit, seats with a
 * position and a "Trưởng quản lý", the order-flow grants per position), its
 * nav entry, and what the stream member panels still offer now that placing
 * people is the OWNER's (`can_tag`) while the rest of a row follows
 * `can_edit_members`.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DepartmentsPage from "@/app/admin/departments/page";
import { Shell } from "@/components/shell";
import { UnitPanel } from "@/components/unit-panel";
import { canEditMembersIn } from "@/lib/units";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

let pathname = "/admin/departments";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

beforeEach(() => {
  pathname = "/admin/departments";
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const sent = (stub: unknown, method: string, fragment: string) =>
  calls(stub).filter((call) => call.method === method && call.url.includes(fragment));

const OWNER = { ...SESSION, role: "OWNER" };
const ADMIN = { ...SESSION, role: "ADMIN" };

const DEPARTMENTS = [
  {
    id: "d-bt",
    code: "BT",
    name: "Biên Tập",
    function: "BIEN_TAP",
    function_label: "Biên kịch",
    active: true,
    sort_order: 2,
    member_count: 0,
  },
  {
    id: "d-ads",
    code: "ADS",
    name: "ADS",
    function: "NONE",
    function_label: "Không",
    active: true,
    sort_order: 1,
    member_count: 3,
  },
  {
    id: "d-old",
    code: "OLD",
    name: "Phòng cũ",
    function: "NONE",
    function_label: "Không",
    active: false,
    sort_order: 9,
    member_count: 0,
  },
];

const CATALOG = {
  module: "ORDER",
  module_label: "Luồng order",
  permissions: [
    {
      key: "ORDER_CREATE",
      label: "Tạo order",
      scopes: ["NONE", "OWN", "ALL"],
      own_meaning: "order của chính mình",
    },
    {
      key: "ORDER_APPROVE",
      label: "Duyệt order",
      scopes: ["NONE", "OWN", "ALL"],
      own_meaning: "order của nhóm mình",
    },
    { key: "ORDER_CANCEL", label: "Huỷ order", scopes: ["NONE", "ALL"], own_meaning: null },
  ],
  positions: [
    { value: "HEAD", label: "Trưởng phòng" },
    { value: "MEMBER", label: "Nhân viên" },
  ],
  functions: [
    { value: "NONE", label: "Không" },
    { value: "BIEN_TAP", label: "Biên kịch" },
    { value: "THIET_KE", label: "Design" },
    { value: "DUNG", label: "Dựng" },
    { value: "PR", label: "PR" },
    { value: "BOARD", label: "Ban Giám đốc" },
    { value: "RESOURCES", label: "Tài nguyên" },
  ],
  scope_labels: { NONE: "Không", OWN: "Của mình / nhóm mình", ALL: "Toàn bộ" },
};

const SEATS = {
  members: [
    {
      user_id: "u-a",
      full_name: "Trưởng A",
      active: true,
      position: "HEAD",
      position_label: "Trưởng phòng",
      manager_user_id: null,
    },
    {
      user_id: "u-b",
      full_name: "Trưởng B",
      active: true,
      position: "HEAD",
      position_label: "Trưởng phòng",
      manager_user_id: null,
    },
    {
      user_id: "u-nhu",
      full_name: "Quỳnh Như",
      active: true,
      position: "MEMBER",
      position_label: "Nhân viên",
      manager_user_id: "u-a",
    },
  ],
};

const GRANTS = {
  module: "ORDER",
  cells: {
    HEAD: { ORDER_CREATE: "OWN", ORDER_APPROVE: "OWN", ORDER_CANCEL: "ALL" },
    MEMBER: { ORDER_CREATE: "OWN", ORDER_APPROVE: "NONE", ORDER_CANCEL: "NONE" },
  },
};

const DIRECTORY = [
  {
    user_id: "u-new",
    full_name: "Người Mới",
    base_role: "EMPLOYEE",
    base_role_label: "Nhân viên",
    active: true,
    units: [],
  },
  {
    user_id: "u-nhu",
    full_name: "Quỳnh Như",
    base_role: "EMPLOYEE",
    base_role_label: "Nhân viên",
    active: true,
    units: ["ADS"],
  },
  {
    user_id: "u-off",
    full_name: "Đã nghỉ",
    base_role: "EMPLOYEE",
    base_role_label: "Nhân viên",
    active: false,
    units: [],
  },
];

const refusal = (code: string, field: string | null, message: string) => ({
  error: {
    code,
    message,
    details: field ? { reason: code, field } : { reason: code },
  },
});

function stubPage(
  session: unknown = OWNER,
  extra: Array<{ match: string; method?: string; status?: number; body?: unknown }> = [],
) {
  return stubFetch([
    ...extra,
    { match: "/api/auth/session", body: session },
    { match: "/api/departments/catalog", body: CATALOG },
    { match: "/api/departments/d-ads/members/u-nhu", method: "DELETE", status: 204 },
    {
      match: "/api/departments/d-ads/members",
      method: "PUT",
      body: SEATS.members[2],
    },
    { match: "/api/departments/d-ads/members", body: SEATS },
    { match: "/api/departments/d-ads/grants", method: "PUT", body: GRANTS },
    { match: "/api/departments/d-ads/grants", body: GRANTS },
    { match: "/api/departments/d-ads", method: "PATCH", body: DEPARTMENTS[1] },
    { match: "/api/departments/d-bt/members", body: { members: [] } },
    { match: "/api/departments/d-bt/grants", body: { module: "ORDER", cells: {} } },
    {
      match: "/api/departments",
      method: "POST",
      status: 201,
      body: { ...DEPARTMENTS[0], id: "d-hr", code: "HR", name: "Hành chính nhân sự" },
    },
    { match: "/api/departments", body: { departments: DEPARTMENTS } },
    { match: "/api/units/directory", body: DIRECTORY },
  ]);
}

const confirmIn = async (label: string) =>
  userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", { name: label }),
  );

describe("Phòng ban: the list and a new department", () => {
  it("lists every department, in order, the inactive one greyed", async () => {
    stubPage();
    renderWithQuery(<DepartmentsPage />);
    const list = await screen.findByRole("navigation", { name: "Danh sách phòng ban" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((row) => row.textContent)).toEqual([
      "ADSADS3 người",
      "Biên TậpBT · Biên kịch0 người",
      "Phòng cũOLD · Ngừng hoạt động0 người",
    ]);
    expect(rows[2]).toHaveAttribute("data-inactive", "true");
    // The first is open: its header, seats and grants.
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(await screen.findByText("Quỳnh Như")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Quyền trong luồng order" })).toBeInTheDocument();
    expect(screen.queryByText("Chỉ chủ sở hữu chỉnh phòng ban.")).not.toBeInTheDocument();
  });

  it("creates a department with a code, a name and a function", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const form = await screen.findByRole("form", { name: "Thêm phòng" });
    const add = within(form).getByRole("button", { name: "Thêm phòng" });
    expect(add).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Mã phòng"), "hr");
    expect(within(form).getByLabelText("Mã phòng")).toHaveValue("HR");
    await userEvent.type(within(form).getByLabelText("Tên phòng"), "Hành chính nhân sự");
    await userEvent.selectOptions(within(form).getByLabelText("Chức năng"), "RESOURCES");
    await userEvent.click(add);
    await confirmIn("Thêm phòng");
    await waitFor(() => expect(sent(stub, "POST", "/api/departments")).toHaveLength(1));
    expect(sent(stub, "POST", "/api/departments")[0].body).toEqual({
      code: "HR",
      name: "Hành chính nhân sự",
      function: "RESOURCES",
    });
  });

  it("puts a taken code's refusal on the code box", async () => {
    stubPage(OWNER, [
      {
        match: "/api/departments",
        method: "POST",
        status: 422,
        body: refusal("department_code_taken", "code", "Mã phòng HR đã có."),
      },
    ]);
    renderWithQuery(<DepartmentsPage />);
    const form = await screen.findByRole("form", { name: "Thêm phòng" });
    await userEvent.type(within(form).getByLabelText("Mã phòng"), "HR");
    await userEvent.type(within(form).getByLabelText("Tên phòng"), "Nhân sự");
    await userEvent.click(within(form).getByRole("button", { name: "Thêm phòng" }));
    await confirmIn("Thêm phòng");
    const code = within(form).getByLabelText("Mã phòng");
    await waitFor(() => expect(code).toHaveAttribute("aria-invalid", "true"));
    expect(code).toHaveAccessibleDescription("Mã phòng HR đã có.");
    expect(within(form).getByLabelText("Tên phòng")).not.toHaveAttribute("aria-invalid");
  });

  it("saves only what changed in the department's header", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const header = await screen.findByRole("form", { name: "Thông tin phòng" });
    const save = within(header).getByRole("button", { name: "Lưu phòng" });
    expect(save).toBeDisabled();
    const name = within(header).getByLabelText("Tên phòng");
    await userEvent.clear(name);
    await userEvent.type(name, "Phòng ADS");
    await userEvent.click(save);
    await confirmIn("Lưu");
    await waitFor(() => expect(sent(stub, "PATCH", "/api/departments/d-ads")).toHaveLength(1));
    expect(sent(stub, "PATCH", "/api/departments/d-ads")[0].body).toEqual({ name: "Phòng ADS" });
  });
});

describe("Phòng ban: seats", () => {
  it("changes a position, sending nothing about the manager", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const position = await screen.findByRole("combobox", { name: "Chức vụ của Quỳnh Như" });
    expect(position).toHaveValue("MEMBER");
    await userEvent.selectOptions(position, "HEAD");
    await waitFor(() => expect(sent(stub, "PUT", "/api/departments/d-ads/members")).toHaveLength(1));
    expect(sent(stub, "PUT", "/api/departments/d-ads/members")[0].body).toEqual({
      user_id: "u-nhu",
      position: "HEAD",
    });
  });

  it("offers this department's heads as Trưởng quản lý, and clears with null", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const manager = await screen.findByRole("combobox", { name: "Trưởng quản lý của Quỳnh Như" });
    expect(manager).toHaveValue("u-a");
    expect(within(manager).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— Không có —",
      "Trưởng A",
      "Trưởng B",
    ]);
    // A head has no Trưởng quản lý to pick.
    expect(
      screen.queryByRole("combobox", { name: "Trưởng quản lý của Trưởng A" }),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(manager, "u-b");
    await waitFor(() => expect(sent(stub, "PUT", "/members")).toHaveLength(1));
    expect(sent(stub, "PUT", "/members")[0].body).toEqual({
      user_id: "u-nhu",
      manager_user_id: "u-b",
    });
    await userEvent.selectOptions(manager, "");
    await waitFor(() => expect(sent(stub, "PUT", "/members")).toHaveLength(2));
    expect(sent(stub, "PUT", "/members")[1].body).toEqual({
      user_id: "u-nhu",
      manager_user_id: null,
    });
  });

  it("puts a refused manager's reason on the Trưởng quản lý select", async () => {
    stubPage(OWNER, [
      {
        match: "/api/departments/d-ads/members",
        method: "PUT",
        status: 422,
        body: refusal(
          "department_manager_invalid",
          "manager_user_id",
          "Trưởng quản lý phải là Trưởng phòng đang hoạt động của phòng này.",
        ),
      },
    ]);
    renderWithQuery(<DepartmentsPage />);
    const manager = await screen.findByRole("combobox", { name: "Trưởng quản lý của Quỳnh Như" });
    await userEvent.selectOptions(manager, "u-b");
    await waitFor(() => expect(manager).toHaveAttribute("aria-invalid", "true"));
    expect(manager).toHaveAccessibleDescription(
      "Trưởng quản lý phải là Trưởng phòng đang hoạt động của phòng này.",
    );
    expect(screen.getByRole("combobox", { name: "Chức vụ của Quỳnh Như" })).not.toHaveAttribute(
      "aria-invalid",
    );
  });

  it("adds somebody from the directory with a position", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const form = await screen.findByRole("form", { name: "Thêm người" });
    const picker = within(form).getByRole("combobox", { name: "Thêm người" });
    // Active accounts not seated here yet.
    await waitFor(() =>
      expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
        "— chọn tài khoản —",
        "Người Mới",
      ]),
    );
    const add = within(form).getByRole("button", { name: "Thêm vào phòng" });
    expect(add).toBeDisabled();
    await userEvent.selectOptions(picker, "u-new");
    await userEvent.selectOptions(
      within(form).getByRole("combobox", { name: "Chức vụ của người thêm" }),
      "HEAD",
    );
    await userEvent.click(add);
    await confirmIn("Thêm vào phòng");
    await waitFor(() => expect(sent(stub, "PUT", "/members")).toHaveLength(1));
    expect(sent(stub, "PUT", "/members")[0].body).toEqual({ user_id: "u-new", position: "HEAD" });
  });

  it("removes somebody after a confirmation", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Gỡ Quỳnh Như khỏi phòng" }),
    );
    expect(sent(stub, "DELETE", "/members")).toHaveLength(0);
    await confirmIn("Gỡ khỏi phòng");
    await waitFor(() =>
      expect(sent(stub, "DELETE", "/api/departments/d-ads/members/u-nhu")).toHaveLength(1),
    );
  });
});

describe("Phòng ban: grants in the order flow", () => {
  it("sends only the cells changed, each offering its own scopes", async () => {
    const stub = stubPage();
    renderWithQuery(<DepartmentsPage />);
    const grants = await screen.findByRole("region", { name: "Quyền trong luồng order" });
    const save = within(grants).getByRole("button", { name: "Lưu quyền" });
    const cell = (label: string) => within(grants).getByRole("combobox", { name: label });
    await waitFor(() => expect(cell("Tạo order · Trưởng phòng")).toHaveValue("OWN"));
    expect(save).toBeDisabled();
    // A permission without "Của mình" offers only Không / Toàn bộ.
    expect(
      within(cell("Huỷ order · Nhân viên")).getAllByRole("option").map((o) => o.textContent),
    ).toEqual(["Không", "Toàn bộ"]);
    expect(within(grants).getByText(/order của nhóm mình/)).toBeInTheDocument();

    await userEvent.selectOptions(cell("Duyệt order · Nhân viên"), "ALL");
    await userEvent.selectOptions(cell("Huỷ order · Trưởng phòng"), "NONE");
    // Put back as it was: not a change any more.
    await userEvent.selectOptions(cell("Huỷ order · Trưởng phòng"), "ALL");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await confirmIn("Lưu quyền");
    await waitFor(() => expect(sent(stub, "PUT", "/grants")).toHaveLength(1));
    expect(sent(stub, "PUT", "/api/departments/d-ads/grants")[0].body).toEqual({
      cells: { MEMBER: { ORDER_APPROVE: "ALL" } },
    });
  });
});

describe("Phòng ban for an ADMIN: read only", () => {
  it("shows everything with no input and no button but the list", async () => {
    const stub = stubPage(ADMIN);
    renderWithQuery(<DepartmentsPage />);
    expect(await screen.findByText("Chỉ chủ sở hữu chỉnh phòng ban.")).toBeInTheDocument();
    expect(await screen.findByText("Quỳnh Như")).toBeInTheDocument();
    const grants = screen.getByRole("region", { name: "Quyền trong luồng order" });
    await waitFor(() => expect(within(grants).getAllByText("Toàn bộ").length).toBeGreaterThan(0));
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    for (const name of [/Thêm phòng/, /Lưu phòng/, /Lưu quyền/, /Thêm vào phòng/, /^Gỡ/]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    // Only the department list is clickable.
    const list = screen.getByRole("navigation", { name: "Danh sách phòng ban" });
    expect(screen.getAllByRole("button")).toEqual(within(list).getAllByRole("button"));
    // The roster's manager is named, not picked; the directory is not read.
    expect(screen.getAllByText("Trưởng A").length).toBeGreaterThan(1);
    expect(calls(stub).some((call) => call.url.includes("/api/units/directory"))).toBe(false);
  });
});

// --- The nav ------------------------------------------------------------------

const ME = {
  units: [
    {
      code: "ADS",
      label: "Luồng Order (ORD)",
      role: "HEAD",
      role_label: "Trưởng phòng ADS",
      is_lead: false,
      member_code: null,
      personal_nas_url: null,
      settings: {},
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
};

describe("the Phòng ban nav entry", () => {
  const hrefs = () =>
    within(screen.getByRole("navigation", { name: "Điều hướng chính" }))
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));

  it.each([
    ["OWNER", true],
    ["ADMIN", true],
    ["TEAM_LEAD", false],
    ["EMPLOYEE", false],
  ])("is in the nav for %s: %s", async (role, shown) => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role } },
      {
        match: "/api/units/me",
        body:
          role === "OWNER" || role === "ADMIN"
            ? { ...ME, can_view_all: true, can_admin: ["ADS", "PR"] }
            : ME,
      },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() => expect(hrefs()).toContain("/tasks"));
    await waitFor(() =>
      expect(hrefs().includes("/admin/departments")).toBe(shown),
    );
    if (shown) {
      expect(screen.getByRole("link", { name: /Phòng ban/ })).toHaveAttribute(
        "href",
        "/admin/departments",
      );
    }
  });
});

// --- The stream member panels -------------------------------------------------

const ORD_MEMBERS = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  members: [
    {
      user_id: "u-a",
      full_name: "Trưởng A",
      base_role: "EMPLOYEE",
      base_role_label: "Nhân viên",
      role: "HEAD",
      role_label: "Trưởng phòng ADS",
      is_lead: false,
      member_code: "TA",
      personal_nas_url: null,
      joined_at: "2026-10-01T00:00:00Z",
      left_at: null,
      active: true,
      manager_user_id: null,
    },
    {
      user_id: "u-nhu",
      full_name: "Quỳnh Như",
      base_role: "EMPLOYEE",
      base_role_label: "Nhân viên",
      role: "ORDERER",
      role_label: "Nhân viên ADS (người order)",
      is_lead: false,
      member_code: "NHU",
      personal_nas_url: null,
      joined_at: "2026-10-01T00:00:00Z",
      left_at: null,
      active: true,
      manager_user_id: "u-a",
      birthday_orders: false,
      birthday_orders_default: false,
    },
  ],
  assignable_roles: [
    { role: "HEAD", label: "Trưởng phòng ADS" },
    { role: "ORDERER", label: "Nhân viên ADS (người order)" },
  ],
};

function stubPanel(session: unknown, me: Partial<UnitsMe>) {
  return stubFetch([
    { match: "/api/auth/session", body: session },
    { match: "/api/units/me", body: { ...ME, ...me } },
    { match: "/api/units/ADS/members", body: ORD_MEMBERS },
    { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
    { match: "/api/units/directory", body: [] },
    { match: "/api/units/untagged", body: { users: [] } },
  ]);
}

describe("the stream member panel: placing people vs editing a row", () => {
  it("lets an ADMIN edit a row but not place people, pointing at Phòng ban", async () => {
    stubPanel(ADMIN, { can_view_all: true, can_admin: ["ADS"], can_tag: [], can_edit_members: ["ADS"] });
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    // The rest of the row is editable.
    expect(await screen.findByRole("textbox", { name: "Mã thành viên của Quỳnh Như" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Kho cá nhân của Quỳnh Như" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Order sinh nhật của Quỳnh Như" })).toBeEnabled();
    // Placing people is not.
    expect(screen.queryByRole("combobox", { name: "Vai trò của Quỳnh Như" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Trưởng quản lý của Quỳnh Như" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Trưởng quản lý: Trưởng A")).toBeInTheDocument();
    expect(screen.queryByText("Thêm thành viên")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Gỡ" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Chưa có luồng" })).not.toBeInTheDocument();
    const hint = screen.getByTestId("placement-hint");
    expect(hint).toHaveTextContent(
      "Gắn người, vai trò và trưởng quản lý ở màn Phòng ban (chủ sở hữu).",
    );
    expect(within(hint).getByRole("link")).toHaveAttribute("href", "/admin/departments");
  });

  it("gives the OWNER every control and no hint", async () => {
    stubPanel(
      { ...OWNER, user_id: "u-owner" },
      { can_view_all: true, can_admin: ["ADS"], can_tag: ["ADS"], can_edit_members: ["ADS"] },
    );
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    expect(await screen.findByRole("combobox", { name: "Vai trò của Quỳnh Như" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Trưởng quản lý của Quỳnh Như" })).toHaveValue("u-a");
    expect(screen.getByRole("textbox", { name: "Mã thành viên của Quỳnh Như" })).toBeInTheDocument();
    expect(screen.getByText("Thêm thành viên")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Gỡ" }).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("placement-hint")).not.toBeInTheDocument();
  });

  it("leaves a team lead with the row edits, and the hint without a link", async () => {
    stubPanel(SESSION, { can_admin: [], can_tag: [], can_edit_members: ["ADS"] });
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    expect(await screen.findByRole("textbox", { name: "Mã thành viên của Quỳnh Như" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Vai trò của Quỳnh Như" })).not.toBeInTheDocument();
    const hint = screen.getByTestId("placement-hint");
    expect(within(hint).queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows a reader with neither right a plain roster", async () => {
    stubPanel(SESSION, { can_admin: [], can_tag: [], can_edit_members: [] });
    renderWithQuery(<UnitPanel code="ADS" settings={false} />);
    expect(await screen.findByText("Quỳnh Như")).toBeInTheDocument();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
    expect(screen.getByText("Order sinh nhật: –")).toBeInTheDocument();
  });

  it("reads an API without can_edit_members as can_tag", () => {
    const base = ME as unknown as UnitsMe;
    expect(canEditMembersIn({ ...base, can_tag: ["ADS"] }, "ADS")).toBe(true);
    expect(canEditMembersIn({ ...base, can_tag: [] }, "ADS")).toBe(false);
    expect(canEditMembersIn({ ...base, can_tag: [], can_edit_members: ["ADS"] }, "ADS")).toBe(true);
    expect(canEditMembersIn({ ...base, can_tag: ["ADS"], can_edit_members: [] }, "ADS")).toBe(false);
  });
});
