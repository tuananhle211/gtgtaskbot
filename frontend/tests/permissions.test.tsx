/**
 * "Quyền" of one person (0066): every module with the scope held and its
 * source; a grant, a revocation of a personal grant; the PR review gates
 * point at the PR screen; the roles tab shows the PR positions. The page is
 * the OWNER's alone (2026-10-31): everybody else gets the server's refusal; on
 * an ADMIN's page only what their role gives is locked.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PersonPermissionsPage from "@/app/admin/people/[id]/page";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "u-1" }),
  usePathname: () => "/admin/people/u-1",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const OWNER = { ...SESSION, role: "OWNER" };
const ME = { units: [], default_unit: null, can_view_all: true, can_admin: ["ADS", "PR"] };

const row = (permission: string, label: string, scope: string, sources: unknown[] = [], extra: Record<string, unknown> = {}) => ({
  permission,
  label,
  scope,
  scope_label: scope === "ALL" ? "Toàn bộ" : scope === "OWN" ? "Của mình / phòng mình" : scope === "SCOPED" ? "Theo phạm vi đã cấp" : "Không",
  sources,
  gate_grants: [],
  personal_only: false,
  ...extra,
});

const SEAT = {
  source: "DEPARTMENT",
  scope: "OWN",
  department_id: "d-ads",
  department_code: "ADS",
  department_name: "ADS",
  position: "HEAD",
  position_label: "Trưởng phòng",
};
const PERSONAL = {
  source: "PERSONAL",
  scope: "ALL",
  grant_id: "g-1",
  granted_by_user_id: "u-owner",
  effective_to: "2026-12-31",
  note: "Kiểm kê tháng 10",
};

const PAGE = {
  user_id: "u-1",
  full_name: "Nguyễn Văn A",
  role: "TEAM_LEAD",
  role_label: "Trưởng nhóm",
  employment_status: "WORKING",
  employment_status_label: "Đang làm",
  sees_all: false,
  seats: [{ department_id: "d-ads", code: "ADS", name: "ADS", position: "HEAD", position_label: "Trưởng phòng" }],
  modules: [
    {
      module: "ORDER",
      label: "Luồng order",
      rows: [row("ORDER_APPROVE", "Duyệt / trả order", "OWN", [SEAT]), row("CANCEL", "Huỷ order", "NONE")],
      can_manage: true,
      grantable: { CANCEL: ["OWN", "ALL"], PRIORITY: ["ALL"] },
    },
    {
      module: "PR",
      label: "Nội dung PR",
      rows: [
        row("PR_CONTENT_CREATE", "Tạo nội dung", "NONE"),
        row("PR_TEAM_LEAD_REVIEW", "Duyệt Trưởng nhóm", "NONE", [], { personal_only: true }),
      ],
      can_manage: false,
      grantable: {},
    },
    {
      module: "RESOURCES",
      label: "Quản lý tài nguyên",
      rows: [
        row("VIEW", "Xem tài nguyên", "ALL", [SEAT, PERSONAL]),
        row("EDIT", "Sửa tài nguyên", "ALL", [{ ...PERSONAL, grant_id: "g-2", note: null, effective_to: null }]),
      ],
      can_manage: true,
      grantable: { VIEW: ["OWN", "ALL"], EDIT: ["ALL"] },
    },
  ],
  personal: [
    {
      id: "g-1",
      module: "RESOURCES",
      module_label: "Quản lý tài nguyên",
      permission: "VIEW",
      permission_label: "Xem tài nguyên",
      scope: "ALL",
      scope_label: "Toàn bộ",
      note: "Kiểm kê tháng 10",
      granted_by_user_id: "u-owner",
      effective_to: "2026-12-31",
      created_at: "2026-10-16T03:00:00Z",
    },
  ],
  scope_labels: { NONE: "Không", OWN: "Của mình / phòng mình", ALL: "Toàn bộ", SCOPED: "Theo phạm vi đã cấp" },
};

const stubPage = (page: unknown = PAGE) =>
  stubFetch([
    { match: "/api/auth/session", body: OWNER },
    { match: "/api/units/me", body: ME },
    { match: "/api/permissions/people/u-1/grants", method: "PUT", body: page },
    { match: "/api/permissions/people/u-1", body: page },
  ]);

describe("Quyền của một người", () => {
  it("draws every module as a checklist: defaults ticked and locked, extras editable", async () => {
    stubPage();
    renderWithQuery(<PersonPermissionsPage />);
    expect(await screen.findByRole("heading", { name: "Quyền · Nguyễn Văn A" })).toBeInTheDocument();
    const order = screen.getByRole("region", { name: "Luồng order" });
    const approve = within(order).getByRole("checkbox", { name: "Duyệt / trả order" });
    expect(approve).toBeChecked();
    expect(approve).toBeDisabled();
    expect(within(order).getByText(/mặc định · Trưởng phòng ADS/)).toBeInTheDocument();
    const cancel = within(order).getByRole("checkbox", { name: "Huỷ order" });
    expect(cancel).not.toBeChecked();
    expect(cancel).toBeEnabled();
    const resources = screen.getByRole("region", { name: "Quản lý tài nguyên" });
    const view = within(resources).getByRole("checkbox", { name: "Xem tài nguyên" });
    expect(view).toBeChecked();
    expect(view).toBeDisabled();
    // A PR review gate has no tick: it points at the PR screen.
    const pr = screen.getByRole("region", { name: "Nội dung PR" });
    expect(within(pr).queryByRole("checkbox", { name: "Duyệt Trưởng nhóm" })).not.toBeInTheDocument();
    expect(within(pr).getByRole("link", { name: /cấp theo kênh/ })).toHaveAttribute("href", "/pr/permissions?tab=grants");
    // Nothing to save where the viewer may not manage.
    expect(within(pr).queryByRole("button", { name: "Lưu quyền" })).not.toBeInTheDocument();
    expect(within(order).getByRole("button", { name: "Lưu quyền" })).toBeDisabled();
  });

  it("ticks a permission, picks its scope and saves the module's checklist", async () => {
    const stub = stubPage();
    renderWithQuery(<PersonPermissionsPage />);
    const order = await screen.findByRole("region", { name: "Luồng order" });
    await userEvent.click(within(order).getByRole("checkbox", { name: "Huỷ order" }));
    await userEvent.selectOptions(within(order).getByRole("combobox", { name: "Phạm vi Huỷ order" }), "OWN");
    const save = within(order).getByRole("button", { name: "Lưu quyền" });
    expect(save).toBeEnabled();
    await userEvent.click(save);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent('Cấp "Huỷ order" (Của mình / phòng mình).');
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu quyền" }));
    await waitFor(() => expect(calls(stub).filter((call) => call.method === "PUT")).toHaveLength(1));
    expect(calls(stub).find((call) => call.method === "PUT")?.body).toEqual({
      module: "ORDER",
      permissions: { CANCEL: "OWN" },
    });
  });

  it("unticks a personal grant to revoke it", async () => {
    const stub = stubPage();
    renderWithQuery(<PersonPermissionsPage />);
    const resources = await screen.findByRole("region", { name: "Quản lý tài nguyên" });
    // EDIT is held personally only (not by a seat): it can be unticked.
    const edit = within(resources).getByRole("checkbox", { name: "Sửa tài nguyên" });
    expect(edit).toBeChecked();
    expect(edit).toBeEnabled();
    await userEvent.click(edit);
    // VIEW: the seat gives "Của mình", a personal grant widened it to "Toàn bộ"; back to default.
    await userEvent.selectOptions(within(resources).getByRole("combobox", { name: "Phạm vi Xem tài nguyên" }), "OWN");
    await userEvent.click(within(resources).getByRole("button", { name: "Lưu quyền" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent('Thu hồi "Sửa tài nguyên".');
    expect(dialog).toHaveTextContent('Thu hồi "Xem tài nguyên".');
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu quyền" }));
    await waitFor(() => expect(calls(stub).filter((call) => call.method === "PUT")).toHaveLength(1));
    expect(calls(stub).find((call) => call.method === "PUT")?.body).toEqual({
      module: "RESOURCES",
      permissions: { VIEW: null, EDIT: null },
    });
  });

  it("locks what an ADMIN's role gives and leaves the OWNER-only permissions tickable", async () => {
    const CHAT = "Chat trợ lý AI trên Telegram (xem báo cáo toàn công ty)";
    const stub = stubPage({
      ...PAGE,
      role: "ADMIN",
      role_label: "Quản trị viên",
      sees_all: true,
      seats: [],
      personal: [],
      modules: [
        {
          module: "ORDER",
          label: "Luồng order",
          rows: [row("CANCEL", "Huỷ order", "ALL", [], { by_role: true })],
          can_manage: true,
          grantable: { CANCEL: ["OWN", "ALL"] },
        },
        {
          module: "ASSISTANT",
          label: "Trợ lý AI",
          rows: [row("CHAT", CHAT, "NONE", [], { by_role: false })],
          can_manage: true,
          grantable: { CHAT: ["ALL"] },
        },
      ],
    });
    renderWithQuery(<PersonPermissionsPage />);
    expect(await screen.findByText("Mọi quyền, trừ quyền chỉ chủ sở hữu cấp")).toBeInTheDocument();
    const order = screen.getByRole("region", { name: "Luồng order" });
    const cancel = within(order).getByRole("checkbox", { name: "Huỷ order" });
    expect(cancel).toBeChecked();
    expect(cancel).toBeDisabled();
    expect(within(order).getByText(/mặc định · vai trò Quản trị viên/)).toBeInTheDocument();
    const assistant = screen.getByRole("region", { name: "Trợ lý AI" });
    const chat = within(assistant).getByRole("checkbox", { name: CHAT });
    expect(chat).not.toBeChecked();
    expect(chat).toBeEnabled();
    await userEvent.click(chat);
    await userEvent.click(within(assistant).getByRole("button", { name: "Lưu quyền" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu quyền" }));
    await waitFor(() => expect(calls(stub).filter((call) => call.method === "PUT")).toHaveLength(1));
    expect(calls(stub).find((call) => call.method === "PUT")?.body).toEqual({
      module: "ASSISTANT",
      permissions: { CHAT: "ALL" },
    });
  });

  it("shows the server's refusal to anybody but the OWNER", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "ADMIN" } },
      { match: "/api/units/me", body: ME },
      {
        match: "/api/permissions/people/u-1",
        status: 403,
        body: {
          error: {
            code: "permission_forbidden",
            message: "Chỉ chủ sở hữu xem và sửa được phân quyền cá nhân.",
            details: {},
          },
        },
      },
    ]);
    renderWithQuery(<PersonPermissionsPage />);
    expect(await screen.findByText("Chỉ chủ sở hữu xem và sửa được phân quyền cá nhân.")).toBeInTheDocument();
  });
});
