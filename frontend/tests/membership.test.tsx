/**
 * Thành viên & Phân quyền, phase 1 - the members and roles tabs.
 *
 * What the screen must get right, in the order the requirement listed it:
 *
 * 1. the roster renders every state with the server's labels, filters by
 *    search, role and status, and draws only the controls the server's
 *    `may_*` flags admit;
 * 2. adding sends a Telegram id and a role to `POST /api/pr/members` and
 *    the button names the role it will assign;
 * 3. a role change goes through a dialog naming both roles and posts only
 *    the role; a grant is not mentioned as changing;
 * 4. deactivation fetches what the person holds, shows the counts in the
 *    dialog, and posts to `/deactivate`; reactivation and revoke
 *    each have their own verb and their own route;
 * 5. the owner's card offers none of the three;
 * 6. the effective-permissions panel is fetched on demand and renders the
 *    server's provenance - `ROLE`, `SCOPED_GRANT`, `NONE` - without deriving it;
 * 7. the roles tab is read-only and grouped by domain;
 * 8. a refusal from the server is worded from its reason code.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { channelsNavigation, confirm, dialog, renderWithQuery, stubFetch } from "./helpers";
import { errorMessage } from "@/lib/labels";
import { ACTION_INVENTORY, deactivateMemberConfirmation } from "@/lib/confirmations";

const NAV = channelsNavigation("/pr/permissions");
vi.mock("next/navigation", () => NAV.module);
const { default: PermissionsPage } = await import("@/app/pr/permissions/page");

beforeEach(() => NAV.reset());

const OWNER = "11111111-1111-4111-8111-111111111111";
const HAO = "22222222-2222-4222-8222-222222222222";
const LEAD = "33333333-3333-4333-8333-333333333333";
const GONE = "44444444-4444-4444-8444-444444444444";
const TIKTOK = "55555555-5555-4555-8555-555555555555";

const member = (over: Record<string, unknown>) => ({
  user_id: HAO,
  full_name: "Hảo",
  telegram_user_id: 700001,
  telegram_username: "hao",
  telegram_linked: true,
  role: "EMPLOYEE",
  role_label: "Nhân viên",
  status: "active",
  status_label: "Đang hoạt động",
  is_active: true,
  active_grant_count: 0,
  last_status_changed_at: null,
  created_at: "2026-06-01T09:00:00Z",
  ...over,
});

const MEMBERS = [
  member({ user_id: OWNER, full_name: "Chị Chủ", role: "OWNER", role_label: "Chủ sở hữu" }),
  member({}),
  member({
    user_id: LEAD,
    full_name: "Lê Trưởng Nhóm",
    role: "TEAM_LEAD",
    role_label: "Trưởng nhóm",
    status: "suspended",
    status_label: "Tạm khoá",
    is_active: false,
    active_grant_count: 1,
  }),
  member({
    user_id: GONE,
    full_name: "Đã Nghỉ",
    status: "revoked",
    status_label: "Đã loại khỏi PR",
    is_active: false,
    telegram_username: null,
  }),
];

const ASSIGNABLE = [
  { role: "EMPLOYEE", label: "Nhân viên" },
  { role: "TEAM_LEAD", label: "Trưởng nhóm" },
  { role: "ADMIN", label: "Quản trị viên" },
];

const roster = (over: Record<string, unknown> = {}) => ({
  members: MEMBERS,
  counts: { total: 4, active: 2, suspended: 1, revoked: 1, pending: 0 },
  may_add: true,
  may_change_status: true,
  may_change_role: true,
  assignable_roles: ASSIGNABLE,
  ...over,
});

const capability = (over: Record<string, unknown>) => ({
  capability: "PR_CONTENT_EDIT",
  label: "Sửa nội dung",
  domain: "CONTENT",
  domain_label: "Nội dung",
  allowed: true,
  source: "ROLE",
  grants: [],
  ...over,
});

const PERMISSIONS = {
  user_id: HAO,
  full_name: "Hảo",
  role: "EMPLOYEE",
  role_label: "Nhân viên",
  status: "active",
  status_label: "Đang hoạt động",
  is_active: true,
  as_of: "2026-06-01",
  capabilities: [
    capability({}),
    capability({
      capability: "PR_CHANNEL_MANAGE",
      label: "Quản lý kênh",
      domain: "CHANNELS",
      domain_label: "Kênh",
      allowed: false,
      source: "NONE",
    }),
    capability({
      capability: "PR_TEAM_LEAD_REVIEW",
      label: "Duyệt Trưởng nhóm",
      domain: "REVIEW",
      domain_label: "Duyệt",
      allowed: true,
      source: "SCOPED_GRANT",
      grants: [
        {
          id: "66666666-6666-4666-8666-666666666666",
          user_id: HAO,
          capability: "PR_TEAM_LEAD_REVIEW",
          scope: {
            content_type_scope: "SELECTED",
            content_types: ["SHORT_VIDEO_SCRIPT"],
            include_unclassified_content: false,
            channel_scope: "SELECTED",
            channel_ids: [TIKTOK],
            include_unassigned_channel: false,
          },
          effective_from: null,
          effective_to: "2026-09-01",
          requires_role_baseline: false,
          granted_by_user_id: null,
        },
      ],
    }),
  ],
};

const HELD = {
  user_id: HAO,
  content_owned: 2,
  open_work: 0,
  open_tasks: 1,
  kpi_drafts: 1,
  kpi_awaiting_review: 1,
  active_grants: 0,
  total: 4,
};

const ROLES = {
  roles: [
    {
      role: "OWNER",
      label: "Chủ sở hữu",
      active_member_count: 1,
      assignable: false,
      capabilities: [
        capability({}),
        capability({
          capability: "PR_CHANNEL_MANAGE",
          label: "Quản lý kênh",
          domain: "CHANNELS",
          domain_label: "Kênh",
        }),
      ],
    },
    {
      role: "EMPLOYEE",
      label: "Nhân viên",
      active_member_count: 1,
      assignable: true,
      capabilities: [capability({})],
    },
  ],
  note: "Quyền duyệt cấp thêm chỉ áp dụng trong đúng phạm vi đã chọn và không thay đổi vai trò nền của thành viên.",
};

const CHANNEL = {
  id: TIKTOK,
  code: "CH-0001",
  name: "TikTok BS Tiến",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: null,
  platform_id: "77777777-7777-4777-8777-777777777777",
  tier: null,
  url: null,
  platform_code: null,
  policy_grounded_platform: false,
  platform: null,
};

type Route = { match: string; status?: number; body?: unknown; method?: string };

const routes = (list = roster(), extra: Route[] = []): Route[] => [
  ...extra,
  { match: "/api/pr/members", method: "GET", body: list },
  { match: "/api/pr/roles", body: ROLES },
  { match: "/api/pr/channels", body: [CHANNEL] },
];

type Calls = { calls: Array<{ url: string; method: string; body: never }> };

async function open(list = roster(), extra: Route[] = []): Promise<Calls> {
  const fetchMock = stubFetch(routes(list, extra));
  renderWithQuery(<PermissionsPage />);
  await waitFor(() => expect(screen.getAllByTestId("member-card").length).toBeGreaterThan(0));
  return fetchMock as unknown as Calls;
}

const card = (name: string) =>
  within(screen.getByText(name).closest('[data-testid="member-card"]') as HTMLElement);

const lastCall = (fetchMock: Calls, path: string, method = "POST") =>
  fetchMock.calls.filter((call) => call.url.includes(path) && call.method === method).at(-1)!;

describe("1. the roster", () => {
  it("renders every member with the server's role and status labels, and the counts", async () => {
    await open();
    expect(screen.getAllByTestId("member-card")).toHaveLength(4);
    expect(card("Lê Trưởng Nhóm").getByText("Trưởng nhóm")).toBeInTheDocument();
    expect(card("Lê Trưởng Nhóm").getByText("Tạm khoá")).toBeInTheDocument();
    expect(card("Lê Trưởng Nhóm").getByText("1 quyền duyệt cấp thêm")).toBeInTheDocument();
    expect(card("Đã Nghỉ").getByText("Đã loại khỏi PR")).toBeInTheDocument();
    // Canonical role labels, from the server, on every card.
    expect(card("Chị Chủ").getByText("Chủ sở hữu")).toBeInTheDocument();
    expect(card("Hảo").getByText("Nhân viên")).toBeInTheDocument();
    expect(screen.queryByText("Trưởng phòng")).not.toBeInTheDocument();
    expect(screen.getByText("Đang hoạt động: 2")).toBeInTheDocument();
    expect(screen.getByText("Tạm khoá: 1")).toBeInTheDocument();
    expect(screen.getByText("Đã loại khỏi PR: 1")).toBeInTheDocument();
    // The strip's count is the server's active count, not a client tally.
    expect(screen.getByRole("tab", { name: /Thành viên/ })).toHaveTextContent("2");
  });

  it("filters by search, role and status without asking the server again", async () => {
    const fetchMock = await open();
    const before = fetchMock.calls.length;
    fireEvent.change(screen.getByLabelText(/Tìm theo tên/), { target: { value: "hảo" } });
    expect(screen.getAllByTestId("member-card")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(/Tìm theo tên/), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Vai trò"), { target: { value: "TEAM_LEAD" } });
    expect(screen.getAllByTestId("member-card")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Vai trò"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Trạng thái"), { target: { value: "revoked" } });
    expect(screen.getAllByTestId("member-card")).toHaveLength(1);
    expect(screen.getByText("Đã Nghỉ")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Trạng thái"), { target: { value: "pending" } });
    expect(screen.getByText("Không có thành viên nào khớp bộ lọc.")).toBeInTheDocument();
    expect(fetchMock.calls.length).toBe(before);
  });

  it("draws no management control when the server says the actor may not manage", async () => {
    await open(roster({ may_add: false, may_change_status: false, may_change_role: false }));
    expect(screen.queryByText("Thêm thành viên")).not.toBeInTheDocument();
    expect(screen.queryByText("Đổi vai trò")).not.toBeInTheDocument();
    expect(screen.queryByText("Vô hiệu hóa")).not.toBeInTheDocument();
    expect(screen.queryByText("Loại khỏi PR")).not.toBeInTheDocument();
    expect(screen.queryByText("Kích hoạt lại")).not.toBeInTheDocument();
    expect(screen.queryByText("Khôi phục")).not.toBeInTheDocument();
    // Reading permissions is always offered; the server decides per person.
    expect(screen.getAllByText("Xem quyền hiện tại")).toHaveLength(4);
  });

  it("offers status controls that fit the state: deactivate for active, reactivate for suspended, nothing for revoked", async () => {
    await open();
    expect(card("Hảo").getByText("Vô hiệu hóa")).toBeInTheDocument();
    expect(card("Hảo").getByText("Loại khỏi PR")).toBeInTheDocument();
    expect(card("Hảo").queryByText("Kích hoạt lại")).not.toBeInTheDocument();
    expect(card("Lê Trưởng Nhóm").getByText("Kích hoạt lại")).toBeInTheDocument();
    expect(card("Lê Trưởng Nhóm").queryByText("Vô hiệu hóa")).not.toBeInTheDocument();
    // Revoked is terminal in phase 1: no dead "Khôi phục", no "Kích hoạt lại",
    // and the row still reads its history.
    const gone = card("Đã Nghỉ");
    expect(gone.queryByText("Khôi phục")).not.toBeInTheDocument();
    expect(gone.queryByText("Kích hoạt lại")).not.toBeInTheDocument();
    expect(gone.queryByText("Loại khỏi PR")).not.toBeInTheDocument();
    expect(gone.queryByText("Đổi vai trò")).not.toBeInTheDocument();
    expect(gone.queryByText("Vô hiệu hóa")).not.toBeInTheDocument();
    expect(gone.getByText(/không kích hoạt lại được trong giai đoạn này/)).toBeInTheDocument();
    expect(gone.getByText("Xem quyền hiện tại")).toBeInTheDocument();
  });

  it("moves between tabs through the URL", async () => {
    await open();
    fireEvent.click(screen.getByRole("tab", { name: /Vai trò & quyền/ }));
    expect(NAV.current()).toBe("tab=roles");
    await waitFor(() => expect(screen.getAllByTestId("role-card")).toHaveLength(2));
    fireEvent.click(screen.getByRole("tab", { name: /Thành viên/ }));
    expect(NAV.current()).toBe("");
  });
});

describe("2. adding a member", () => {
  it("posts the Telegram id and the role, and names the role on the button", async () => {
    const fetchMock = await open(roster(), [
      {
        match: "/api/pr/members",
        method: "POST",
        status: 201,
        body: member({ user_id: "88888888-8888-4888-8888-888888888888" }),
      },
    ]);
    fireEvent.click(screen.getByText("Thêm thành viên"));
    const form = within(screen.getByTestId("add-member-form"));
    const submit = form.getByRole("button", { name: /Thêm với vai trò/ });
    expect(submit).toBeDisabled();
    fireEvent.change(form.getByLabelText("Telegram ID"), { target: { value: "700009" } });
    fireEvent.change(form.getByLabelText(/Họ tên/), { target: { value: " Người Mới " } });
    fireEvent.change(form.getByLabelText("Vai trò"), { target: { value: "TEAM_LEAD" } });
    expect(form.getByRole("button", { name: "Thêm với vai trò Trưởng nhóm" })).toBeEnabled();
    // It says how the person is identified and what they do next.
    expect(form.getByText(/Telegram ID — đây là cách duy nhất/)).toBeInTheDocument();
    expect(form.getByText("/start")).toBeInTheDocument();
    fireEvent.submit(form.getByRole("button", { name: "Thêm với vai trò Trưởng nhóm" }));
    await waitFor(() => expect(lastCall(fetchMock, "/api/pr/members")).toBeTruthy());
    expect(lastCall(fetchMock, "/api/pr/members").body).toEqual({
      telegram_user_id: 700009,
      role: "TEAM_LEAD",
      full_name: "Người Mới",
    });
    await waitFor(() => expect(screen.queryByTestId("add-member-form")).not.toBeInTheDocument());
  });

  it("never offers OWNER: the picker is the server's assignable list", async () => {
    await open();
    fireEvent.click(screen.getByText("Thêm thành viên"));
    const form = within(screen.getByTestId("add-member-form"));
    const options = form.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Nhân viên", "Trưởng nhóm", "Quản trị viên"]);
  });

  it("words a refusal from the server's reason code", async () => {
    await open(roster(), [
      {
        match: "/api/pr/members",
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "conflict_error",
            message: "Tài khoản Telegram này đã được đăng ký.",
            details: { reason: "member_already_registered", user_id: HAO },
          },
        },
      },
    ]);
    fireEvent.click(screen.getByText("Thêm thành viên"));
    const form = within(screen.getByTestId("add-member-form"));
    fireEvent.change(form.getByLabelText("Telegram ID"), { target: { value: "700001" } });
    fireEvent.submit(form.getByRole("button", { name: /Thêm với vai trò/ }));
    await waitFor(() =>
      expect(screen.getByText("Tài khoản Telegram này đã là thành viên.")).toBeInTheDocument(),
    );
  });
});

describe("3. changing the base role", () => {
  it("asks with both roles named and posts only the role", async () => {
    const fetchMock = await open(roster(), [
      {
        match: `/api/pr/members/${HAO}/role`,
        method: "POST",
        body: member({ role: "TEAM_LEAD", role_label: "Trưởng nhóm" }),
      },
    ]);
    fireEvent.click(card("Hảo").getByText("Đổi vai trò"));
    const panel = within(screen.getByTestId("change-role-panel"));
    // The current role is not offered again.
    expect(panel.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Trưởng nhóm",
      "Quản trị viên",
    ]);
    expect(panel.getByText(/Quyền duyệt cấp thêm của Hảo giữ nguyên/)).toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Đổi vai trò" }));
    expect(
      dialog().getByText("Đổi vai trò của Hảo từ Nhân viên sang Trưởng nhóm?"),
    ).toBeInTheDocument();
    expect(dialog().getByText(/không đổi theo vai trò/)).toBeInTheDocument();
    await confirm();
    await waitFor(() => expect(lastCall(fetchMock, `/members/${HAO}/role`)).toBeTruthy());
    expect(lastCall(fetchMock, `/members/${HAO}/role`).body).toEqual({ role: "TEAM_LEAD" });
  });

  it("words a role refusal from its reason code", () => {
    expect(errorMessage("authorization_error", { reason: "role_change_forbidden" })).toBe(
      "Chỉ Chủ sở hữu mới đổi được vai trò thành viên.",
    );
    expect(errorMessage("authorization_error", { reason: "owner_protected" })).toMatch(
      /Chủ sở hữu/,
    );
    expect(errorMessage("authorization_error", { reason: "self_change_forbidden" })).toMatch(
      /chính mình/,
    );
    expect(errorMessage("conflict_error", { reason: "role_unchanged" })).toMatch(/đã ở vai trò đó/);
    expect(errorMessage("validation_error", { reason: "invalid_telegram_id" })).toMatch(
      /số nguyên dương/,
    );
    expect(errorMessage("conflict_error", { reason: "member_revoked" })).toMatch(
      /đã bị loại khỏi PR/,
    );
    expect(errorMessage("conflict_error", { reason: "member_revoked" })).not.toMatch(/Khôi phục/);
  });
});

describe("4. deactivating, reactivating, revoking, restoring", () => {
  it("shows what the person holds before asking, and posts to /deactivate with the reason", async () => {
    const fetchMock = await open(roster(), [
      { match: `/api/pr/members/${HAO}/responsibilities`, body: HELD },
      {
        match: `/api/pr/members/${HAO}/deactivate`,
        method: "POST",
        body: member({ status: "suspended", status_label: "Tạm khoá", is_active: false }),
      },
    ]);
    fireEvent.click(card("Hảo").getByText("Vô hiệu hóa"));
    await waitFor(() => expect(screen.getByTestId("responsibilities")).toBeInTheDocument());
    const held = within(screen.getByTestId("responsibilities"));
    expect(held.getByText("2 nội dung đang phụ trách")).toBeInTheDocument();
    expect(held.getByText("1 task chưa hoàn thành")).toBeInTheDocument();
    expect(held.getByText("1 bản nháp KPI (1 đang chờ duyệt)")).toBeInTheDocument();
    expect(held.queryByText(/công việc đang mở/)).not.toBeInTheDocument();
    expect(held.getByText(/không tự chuyển cho ai khác/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Lý do/), { target: { value: "Nghỉ phép dài" } });
    fireEvent.click(screen.getByRole("button", { name: "Vô hiệu hóa thành viên" }));
    expect(dialog().getByText("Vô hiệu hóa thành viên Hảo?")).toBeInTheDocument();
    expect(dialog().getByText(/2 nội dung đang phụ trách/)).toBeInTheDocument();
    expect(
      dialog().getByText(/giữ lại và hoạt động trở lại khi kích hoạt lại/),
    ).toBeInTheDocument();
    await confirm();
    await waitFor(() => expect(lastCall(fetchMock, `/members/${HAO}/deactivate`)).toBeTruthy());
    expect(lastCall(fetchMock, `/members/${HAO}/deactivate`).body).toEqual({
      reason: "Nghỉ phép dài",
    });
  });

  it("reactivates through its own dialog and route", async () => {
    const fetchMock = await open(roster(), [
      {
        match: `/api/pr/members/${LEAD}/reactivate`,
        method: "POST",
        body: member({ user_id: LEAD, status: "active" }),
      },
    ]);
    fireEvent.click(card("Lê Trưởng Nhóm").getByText("Kích hoạt lại"));
    expect(dialog().getByText("Kích hoạt lại Lê Trưởng Nhóm?")).toBeInTheDocument();
    expect(
      dialog().getByText(/đúng vai trò và quyền duyệt cấp thêm như trước/),
    ).toBeInTheDocument();
    await confirm();
    await waitFor(() => expect(lastCall(fetchMock, `/members/${LEAD}/reactivate`)).toBeTruthy());
  });

  it("revokes as 'Loại khỏi PR' with its own warning, and revocation is terminal", async () => {
    const fetchMock = await open(roster(), [
      {
        match: `/api/pr/members/${HAO}/revoke`,
        method: "POST",
        body: member({ status: "revoked" }),
      },
    ]);
    fireEvent.click(card("Hảo").getByText("Loại khỏi PR"));
    const panel = within(screen.getByTestId("revoke-panel"));
    expect(panel.getByText(/Nếu chỉ cần tạm ngưng, dùng “Vô hiệu hóa”/)).toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Loại khỏi PR" }));
    expect(dialog().getByText("Loại Hảo khỏi PR?")).toBeInTheDocument();
    expect(
      dialog().getByText(/lịch sử nội dung, công việc và KPI của họ được giữ nguyên/),
    ).toBeInTheDocument();
    await confirm();
    await waitFor(() => expect(lastCall(fetchMock, `/members/${HAO}/revoke`)).toBeTruthy());
    expect(lastCall(fetchMock, `/members/${HAO}/revoke`).body).toEqual({ reason: null });
    // The dialog promised no undo, and the screen offers none.
    expect(fetchMock.calls.some((call) => call.url.includes("/restore"))).toBe(false);
    expect(ACTION_INVENTORY.some((entry) => /Khôi phục/.test(entry.action))).toBe(false);
  });

  it("never says 'team' on a button or in a dialog", () => {
    for (const entry of ACTION_INVENTORY) expect(entry.action).not.toMatch(/team/i);
    const spec = deactivateMemberConfirmation("Hảo", null);
    expect(`${spec.title} ${spec.description}`).not.toMatch(/team/i);
    expect(spec.title).toBe("Vô hiệu hóa thành viên Hảo?");
  });
});

describe("5. the owner", () => {
  it("cannot be deactivated, removed or re-roled from the screen", async () => {
    await open();
    const owner = card("Chị Chủ");
    expect(owner.queryByText("Đổi vai trò")).not.toBeInTheDocument();
    expect(owner.queryByText("Vô hiệu hóa")).not.toBeInTheDocument();
    expect(owner.queryByText("Loại khỏi PR")).not.toBeInTheDocument();
    expect(owner.getByText(/không vô hiệu hóa, loại khỏi PR hay đổi/)).toBeInTheDocument();
    expect(owner.getByText("Xem quyền hiện tại")).toBeInTheDocument();
  });
});

describe("6. effective permissions", () => {
  it("is fetched on demand and renders the server's provenance", async () => {
    const fetchMock = await open(roster(), [
      { match: `/api/pr/members/${HAO}/effective-permissions`, body: PERMISSIONS },
    ]);
    expect(fetchMock.calls.some((call) => call.url.includes("effective-permissions"))).toBe(false);
    fireEvent.click(card("Hảo").getByText("Xem quyền hiện tại"));
    await waitFor(() => expect(screen.getByTestId("effective-permissions")).toBeInTheDocument());
    const panel = within(screen.getByTestId("effective-permissions"));
    const rows = panel.getAllByTestId("effective-row");
    expect(rows.map((row) => row.getAttribute("data-source"))).toEqual([
      "ROLE",
      "NONE",
      "SCOPED_GRANT",
    ]);
    expect(within(rows[0]).getByText("Vai trò")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Không có")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Quyền cấp thêm")).toBeInTheDocument();
    // The grant's scope, with the channel named from the channel list.
    expect(within(rows[2]).getByText(/Kịch bản video ngắn · TikTok BS Tiến/)).toBeInTheDocument();
    expect(within(rows[2]).getByText(/đến 01\/09\/2026/)).toBeInTheDocument();
    // Domains from the server, as group headings.
    expect(panel.getByText("Nội dung")).toBeInTheDocument();
    expect(panel.getByText("Kênh")).toBeInTheDocument();
    expect(panel.getByText("Duyệt")).toBeInTheDocument();
    expect(panel.queryByText(/không quyền nào dưới đây dùng được/)).not.toBeInTheDocument();
  });

  it("says a suspended member's permissions are what returns, not what applies", async () => {
    await open(roster(), [
      {
        match: `/api/pr/members/${LEAD}/effective-permissions`,
        body: {
          ...PERMISSIONS,
          user_id: LEAD,
          full_name: "Lê Trưởng Nhóm",
          is_active: false,
          status: "suspended",
          status_label: "Tạm khoá",
        },
      },
    ]);
    fireEvent.click(card("Lê Trưởng Nhóm").getByText("Xem quyền hiện tại"));
    await waitFor(() => expect(screen.getByTestId("effective-permissions")).toBeInTheDocument());
    expect(
      screen.getByText(/Tài khoản đang tạm khoá: không quyền nào dưới đây dùng được/),
    ).toBeInTheDocument();
  });
});

describe("7. the roles tab", () => {
  it("is read-only, grouped by domain, and marks the owner as not assignable", async () => {
    stubFetch(routes());
    NAV.arriveAt("/pr/permissions?tab=roles");
    renderWithQuery(<PermissionsPage />);
    await waitFor(() => expect(screen.getAllByTestId("role-card")).toHaveLength(2));
    const owner = within(screen.getAllByTestId("role-card")[0]);
    expect(owner.getByText("Chủ sở hữu")).toBeInTheDocument();
    expect(owner.getByText("Không gán được")).toBeInTheDocument();
    expect(owner.getByText("1 thành viên đang hoạt động")).toBeInTheDocument();
    expect(owner.getByText("Nội dung")).toBeInTheDocument();
    expect(owner.getByText("Kênh")).toBeInTheDocument();
    expect(owner.getByText("Quản lý kênh")).toBeInTheDocument();
    expect(screen.getByText(/không thay đổi vai trò nền của thành viên/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sửa|Lưu|Tạo vai trò/ })).not.toBeInTheDocument();
  });
});

describe("9. the two teams on one roster", () => {
  const UNITS_ME = {
    units: [
      { code: "PR", label: "Luồng PR", role: "MEMBER", role_label: "Thành viên", is_lead: false, member_code: null, personal_nas_url: null, settings: {} },
      { code: "ADS", label: "Luồng Order (ORD)", role: "HEAD", role_label: "Trưởng phòng", is_lead: false, member_code: null, personal_nas_url: null, settings: {} },
    ],
    default_unit: "PR",
    can_view_all: true,
    can_admin: ["PR", "ADS"],
  };
  const directory = (units: Record<string, string[]>) =>
    MEMBERS.map((row) => ({
      user_id: row.user_id,
      full_name: row.full_name,
      base_role: row.role,
      base_role_label: row.role_label,
      active: true,
      units: units[row.user_id] ?? [],
    }));
  const extra = (units: Record<string, string[]>): Route[] => [
    { match: "/api/units/me", body: UNITS_ME },
    { match: "/api/units/directory", body: directory(units) },
    { match: "/api/units/PR/members", method: "POST", body: {} },
  ];

  it("tags every member with their streams and adds an ORD member to PR", async () => {
    const fetchMock = await open(roster(), extra({ [HAO]: ["ADS"], [OWNER]: ["ADS", "PR"] }));
    await waitFor(() => expect(card("Chị Chủ").getByText("PR")).toBeInTheDocument());
    expect(card("Chị Chủ").getByText("ORD")).toBeInTheDocument();
    const hao = card(MEMBERS[1].full_name);
    expect(hao.getByText("ORD")).toBeInTheDocument();
    // ORD only: may be added to PR; already in ORD: no "+ Luồng ORD".
    expect(hao.queryByRole("button", { name: "+ Luồng ORD" })).not.toBeInTheDocument();
    fireEvent.click(hao.getByRole("button", { name: "+ Luồng PR" }));
    await confirm();
    await waitFor(() =>
      expect(lastCall(fetchMock, "/api/units/PR/members").body).toEqual({
        user_id: HAO,
        role: "MEMBER",
      }),
    );
  });

  it("keeps three tabs, splits Thành viên and Quyền duyệt by Luồng PR / Luồng Order (ORD)", async () => {
    await open(roster(), extra({ [HAO]: ["PR"] }));
    expect(
      screen
        .getByRole("tablist", { name: "Thành viên & Phân quyền" })
        .querySelectorAll('[role="tab"]').length,
    ).toBe(3);
    const teams = screen.getByRole("tablist", { name: "Chọn luồng" });
    expect(within(teams).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Luồng PR",
      "Luồng Order (ORD)",
    ]);
    fireEvent.click(within(teams).getByRole("tab", { name: "Luồng Order (ORD)" }));
    expect(NAV.current()).toBe("team=ads");
    // "Vai trò & quyền" has no stream split.
    fireEvent.click(screen.getByRole("tab", { name: /Vai trò & quyền/ }));
    expect(NAV.current()).toBe("tab=roles");
  });

  it("sends a PR member to Luồng Order (ORD) with them picked, and filters by stream", async () => {
    await open(roster(), extra({ [HAO]: ["PR"] }));
    const hao = card(MEMBERS[1].full_name);
    await waitFor(() => expect(hao.getByRole("button", { name: "+ Luồng ORD" })).toBeInTheDocument());
    // A never-tagged account says so (no stream - no longer "PR by default").
    expect(card("Chị Chủ").getByText("Chưa có luồng")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Luồng"), { target: { value: "PR" } });
    expect(screen.getAllByTestId("member-card")).toHaveLength(1);
    fireEvent.click(hao.getByRole("button", { name: "+ Luồng ORD" }));
    expect(NAV.current()).toBe(`team=ads&add=${HAO}`);
  });
});
