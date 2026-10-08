/**
 * The screens units added: the nav per unit, the task table, the order
 * detail's action panel and the unit admin page.
 *
 * Every row, label and button here comes from the server's own shapes; the
 * tests pin that the screens draw what they are given and send back exactly
 * what the API expects (the version, the note, the person).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Shell } from "@/components/shell";
import TasksPage from "@/app/tasks/page";
import DashboardPage from "@/app/dashboard/page";
import OrderDetailPage from "@/app/orders/[ref]/page";
import UnitsAdminPage from "@/app/admin/units/page";
import { renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: "TUAN-BTD-261007-01" }),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  pathname = "/tasks";
});

const ADS_SETTINGS = {
  urgent_days: 7,
  media_nas_url: null,
  design_nas_url: null,
  btd_link_attacher: "DUNG",
  telegram_enabled: false,
};

const ADS_ONLY = {
  units: [
    {
      code: "ADS",
      label: "Phòng Ads",
      role: "HEAD",
      role_label: "Trưởng phòng",
      is_lead: false,
      member_code: null,
      personal_nas_url: null,
      settings: ADS_SETTINGS,
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
};

const OWNER_OF_BOTH = {
  units: [
    {
      ...ADS_ONLY.units[0],
      code: "PR",
      label: "Phòng PR",
      role: "MEMBER",
      role_label: "Thành viên",
    },
    ADS_ONLY.units[0],
  ],
  default_unit: "PR",
  can_view_all: true,
  can_admin: ["PR", "ADS"],
};

const ORDER_ID = "22222222-2222-2222-2222-222222222222";
const NODE_ID = "33333333-3333-3333-3333-333333333333";

const ROW = {
  unit: "ADS",
  unit_label: "Phòng Ads",
  id: ORDER_ID,
  code: "TUAN-BTD-261007-01",
  title: "Kịch bản A",
  kind: "BTD",
  kind_label: "Biên kịch › Design › Dựng",
  owner_user_id: SESSION.user_id,
  owner_name: "Tuấn",
  created_at: "2026-10-07T02:00:00Z",
  phase: "REVIEW",
  phase_label: "Duyệt",
  status: "ORDER_PENDING",
  status_label: "Chờ duyệt",
  cells: [
    {
      key: "BIEN_TAP",
      label: "Biên tập",
      person_name: "Hiền Lương",
      status: "CHUA_TOI",
      status_label: "Chưa tới",
      is_current: false,
    },
    {
      key: "THIET_KE",
      label: "Thiết kế",
      person_name: null,
      status: "CHUA_TOI",
      status_label: "Chưa tới",
      is_current: false,
    },
    {
      key: "DUNG",
      label: "Dựng",
      person_name: null,
      status: "BO_QUA",
      status_label: "Bỏ qua",
      is_current: false,
    },
    {
      key: "GAN_LINK",
      label: "Gắn link",
      person_name: null,
      status: "CHUA_TOI",
      status_label: "Chưa tới",
      is_current: false,
    },
  ],
  product_link: null,
  returned_at: null,
  is_priority: true,
  urgent: false,
  detail_path: "/tasks/TUAN-BTD-261007-01",
  version: 1,
};

const PAGE = {
  unit: "ADS",
  items: [ROW],
  total: 1,
  limit: 10,
  offset: 0,
  phases: [
    { value: "ORDER", label: "Order" },
    { value: "REVIEW", label: "Duyệt" },
    { value: "PRODUCTION", label: "Sản xuất" },
    { value: "FINAL_REVIEW", label: "Duyệt" },
    { value: "DONE", label: "Hoàn thành" },
  ],
};

const DETAIL = {
  order: {
    id: ORDER_ID,
    code: "TUAN-BTD-261007-01",
    title: "Kịch bản A",
    video_type: "BTD",
    video_type_label: "Biên kịch › Design › Dựng",
    order_content: "Ý tưởng, hook.",
    script_source: "AI",
    design_link: null,
    reference_link: "https://example.com/ref",
    source_link: null,
    owner_user_id: SESSION.user_id,
    owner_name: "Tuấn",
    stage: "ORDER_PENDING",
    stage_label: "Chờ duyệt",
    submitted_at: "2026-10-07T02:00:00Z",
    order_approved_at: null,
    returned_reason: null,
    is_priority: false,
    urgent: false,
    product_link: null,
    completed_at: null,
    cancelled_reason: null,
    version: 1,
    created_at: "2026-10-07T02:00:00Z",
    updated_at: "2026-10-07T02:00:00Z",
  },
  nodes: [
    {
      id: NODE_ID,
      node_type: "BIEN_TAP",
      node_type_label: "Biên tập",
      status: "CHUA_TOI",
      status_label: "Chưa tới",
      is_current: false,
      preassigned_user_id: null,
      preassigned_name: null,
      assignee_user_id: null,
      assignee_name: null,
      approved_by_name: null,
      activated_at: null,
      assigned_at: null,
      accepted_at: null,
      submitted_at: null,
      approved_at: null,
      revision_count: 0,
      submission_count: 0,
      version: 1,
    },
  ],
  submissions: [],
  approvals: [],
  events: [
    {
      id: "44444444-4444-4444-4444-444444444444",
      kind: "SUBMITTED",
      kind_label: "Gửi order",
      node_type: null,
      actor_name: "Tuấn",
      assignee_name: null,
      note: null,
      created_at: "2026-10-07T02:00:00Z",
    },
  ],
  available_actions: [
    {
      kind: "APPROVE_ORDER",
      label: "Duyệt order",
      node_id: null,
      requires_note: false,
    },
    {
      kind: "RETURN_ORDER",
      label: "Không duyệt",
      node_id: null,
      requires_note: true,
    },
  ],
};

describe("the nav follows the person's units", () => {
  it("hides the PR screens from an Ads-only person and shows the unit switch to an owner", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ADS_ONLY },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() =>
      expect(screen.getByText(/Le Trưởng Nhóm/)).toBeInTheDocument(),
    );
    await waitFor(() => {
      const primary = screen.getByRole("navigation", {
        name: "Điều hướng chính",
      });
      const hrefs = within(primary)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"));
      expect(hrefs).toEqual(["/dashboard", "/tasks", "/orders/new"]);
    });
    expect(
      screen.queryByRole("navigation", { name: "Chọn ban" }),
    ).not.toBeInTheDocument();
  });

  it("shows every unit and the admin screen to the owner", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: OWNER_OF_BOTH },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    await waitFor(() => {
      const primary = screen.getByRole("navigation", {
        name: "Điều hướng chính",
      });
      const hrefs = within(primary)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"));
      expect(hrefs).toContain("/admin/units");
      expect(hrefs).toContain("/pr/channels");
    });
    // The unit switch moved beside the page title; the top bar has none.
    expect(
      screen.queryByRole("navigation", { name: "Chọn ban" }),
    ).not.toBeInTheDocument();
  });

  it("folds the sidebar to icons and keeps it folded across screens", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: OWNER_OF_BOTH },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    const first = renderWithQuery(<Shell>{null}</Shell>);
    const fold = await screen.findByRole("button", { name: "Thu gọn menu" });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(fold);
    expect(document.querySelector("aside")).toHaveAttribute("data-collapsed", "true");
    // Labels stay in the DOM for screen readers; links get a hover title.
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    expect(within(primary).getByRole("link", { name: /Quản lý task/ })).toHaveAttribute(
      "title",
      "Quản lý task",
    );
    // Another screen mounts its own Shell: it opens folded too.
    first.unmount();
    renderWithQuery(<Shell>{null}</Shell>);
    const unfold = await screen.findByRole("button", { name: "Mở rộng menu" });
    await userEvent.click(unfold);
    expect(screen.getByRole("button", { name: "Thu gọn menu" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("puts the unit switch beside the task page title", async () => {
    stubFetch([
      { match: "/api/units/me", body: OWNER_OF_BOTH },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    const switcher = await screen.findByRole("navigation", { name: "Chọn ban" });
    expect(
      within(switcher)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Phòng PR", "Phòng Ads", "Tất cả"]);
    expect(within(switcher).getByText("Phòng Ads")).toHaveAttribute(
      "href",
      "/tasks?unit=ADS",
    );
    expect(
      screen.getByRole("heading", { name: "Quản lý task" }).parentElement,
    ).toContainElement(switcher);
  });
});

describe("the task table", () => {
  it("draws the rows the server shaped and lets a head decide on a pending order", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ADS_ONLY },
      { match: "/api/board/tasks", body: PAGE },
      { match: "/approve", method: "POST", body: DETAIL },
    ]);
    renderWithQuery(<TasksPage />);
    await waitFor(() =>
      expect(screen.getByText("Kịch bản A")).toBeInTheDocument(),
    );
    expect(screen.getByText("TUAN-BTD-261007-01")).toHaveAttribute(
      "href",
      "/tasks/TUAN-BTD-261007-01",
    );
    expect(screen.getByText("Hiền Lương")).toBeInTheDocument();
    // Three cells in the table; the legend under it repeats the word once.
    expect(
      within(screen.getByRole("table")).getAllByText("Chưa tới"),
    ).toHaveLength(3);
    expect(screen.getByText("Bỏ qua")).toBeInTheDocument();
    // The pill on the row, beside the quick filter of the same name.
    expect(
      within(screen.getByRole("table")).getByText("Ưu tiên"),
    ).toBeInTheDocument();
    const pager = screen.getByRole("navigation", { name: "Phân trang" });
    expect(within(pager).getByText(/1–1 \/ 1 task/)).toBeInTheDocument();
    expect(within(pager).getByRole("button", { name: "Sau" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Duyệt" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Duyệt" }),
    );
    await waitFor(() => {
      const sent = (
        fetchMock as unknown as { calls: Array<{ url: string; body: unknown }> }
      ).calls.find((call) => call.url.includes("/approve"));
      expect(sent?.url).toBe(`/api/orders/${ORDER_ID}/approve`);
      expect(sent?.body).toEqual({ version: 1 });
    });
  });

  it("asks for 10 rows a page and moves through the pages", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ADS_ONLY },
      { match: "/api/board/tasks", body: { ...PAGE, total: 75 } },
    ]);
    renderWithQuery(<TasksPage />);
    const pager = await screen.findByRole("navigation", { name: "Phân trang" });
    const calls = () =>
      (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.filter(
        (call) => call.url.includes("/api/board/tasks"),
      );
    expect(calls()[0].url).toContain("limit=10");
    expect(calls()[0].url).toContain("offset=0");
    // 75 rows: eight pages, the far ones folded behind "…".
    expect(
      within(pager).getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["«", "Trước", "1", "2", "3", "8", "Sau", "»"]);
    await userEvent.click(within(pager).getByRole("button", { name: "3" }));
    expect(SEARCH.value.get("page")).toBe("3");
  });

  it("names the holder, and flags a step nobody holds as Chờ giao", async () => {
    stubFetch([
      { match: "/api/units/me", body: ADS_ONLY },
      {
        match: "/api/board/tasks",
        body: {
          ...PAGE,
          items: [
            {
              ...ROW,
              current_person_name: "Chờ giao",
              current_person_user_id: null,
              awaiting_assignment: true,
            },
          ],
        },
      },
    ]);
    renderWithQuery(<TasksPage />);
    const table = await screen.findByRole("table");
    const holder = within(table).getByText("Đang giữ:").parentElement as HTMLElement;
    expect(within(holder).getByText("Chờ giao")).toBeInTheDocument();
    expect(within(table).queryByText("Trưởng phòng")).not.toBeInTheDocument();
  });

  it("filters Ads by its four steps and sends them as step", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ADS_ONLY },
      {
        match: "/api/board/tasks",
        body: {
          ...PAGE,
          steps: [
            { value: "ORDER", label: "Order" },
            { value: "BIEN_TAP", label: "Biên tập" },
            { value: "THIET_KE", label: "Thiết kế" },
            { value: "DUNG", label: "Dựng" },
          ],
        },
      },
    ]);
    renderWithQuery(<TasksPage />);
    await screen.findByRole("table");
    const pha = screen.getByLabelText("Pha");
    expect(
      within(pha)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Tất cả", "Order", "Biên tập", "Thiết kế", "Dựng"]);
    await userEvent.selectOptions(pha, "THIET_KE");
    expect(SEARCH.value.get("step")).toBe("THIET_KE");
    SEARCH.value = new URLSearchParams("step=THIET_KE");
    renderWithQuery(<TasksPage />);
    await waitFor(() => {
      const urls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls
        .map((call) => call.url)
        .filter((url) => url.includes("/api/board/tasks"));
      expect(urls.some((url) => url.includes("step=THIET_KE"))).toBe(true);
      expect(urls.every((url) => !url.includes("phase="))).toBe(true);
    });
  });

  it("writes the quick filters to the URL", async () => {
    stubFetch([
      { match: "/api/units/me", body: ADS_ONLY },
      { match: "/api/board/tasks", body: PAGE },
    ]);
    renderWithQuery(<TasksPage />);
    await waitFor(() =>
      expect(screen.getByText("Kịch bản A")).toBeInTheDocument(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Chờ tôi xử lý" }),
    );
    await waitFor(() => expect(SEARCH.value.get("awaiting_me")).toBe("true"));
    await userEvent.click(
      screen.getByRole("button", { name: "Chờ tôi xử lý" }),
    );
    await waitFor(() => expect(SEARCH.value.get("awaiting_me")).toBeNull());
  });
});

describe("the order detail", () => {
  it("offers the server's actions and sends the version and the note", async () => {
    pathname = "/orders/TUAN-BTD-261007-01";
    const fetchMock = stubFetch([
      { match: "/api/orders/TUAN-BTD-261007-01", body: DETAIL },
      { match: "/return", method: "POST", body: DETAIL },
    ]);
    renderWithQuery(<OrderDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Kịch bản A" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Ý tưởng, hook.")).toBeInTheDocument();
    expect(screen.getByText(/Tuấn: Gửi order/)).toBeInTheDocument();
    const panel = screen.getByText(/Thao tác của bạn/).closest("section");
    expect(panel).not.toBeNull();
    expect(
      within(panel as HTMLElement).getByRole("button", { name: "Duyệt order" }),
    ).toBeInTheDocument();

    await userEvent.click(
      within(panel as HTMLElement).getByRole("button", { name: "Không duyệt" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText(/Lý do/),
      "Thiếu source",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Không duyệt" }),
    );
    await waitFor(() => {
      const sent = (
        fetchMock as unknown as { calls: Array<{ url: string; body: unknown }> }
      ).calls.find((call) => call.url.includes("/return"));
      expect(sent?.url).toBe(`/api/orders/${ORDER_ID}/return`);
      expect(sent?.body).toEqual({ version: 1, note: "Thiếu source" });
    });
  });

  it("says so when there is nothing to do", async () => {
    stubFetch([
      {
        match: "/api/orders/TUAN-BTD-261007-01",
        body: { ...DETAIL, available_actions: [] },
      },
    ]);
    renderWithQuery(<OrderDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByText("Bạn không có thao tác nào trên order này lúc này."),
      ).toBeInTheDocument(),
    );
  });
});

describe("the unit admin page", () => {
  it("lists members with their roles and tags a new one", async () => {
    pathname = "/admin/units";
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: OWNER_OF_BOTH },
      {
        match: "/api/units/PR/members",
        body: {
          unit: "PR",
          unit_label: "Phòng PR",
          members: [],
          assignable_roles: [{ role: "MEMBER", label: "Thành viên" }],
        },
      },
      {
        match: "/api/units/ADS/members",
        method: "GET",
        body: {
          unit: "ADS",
          unit_label: "Phòng Ads",
          members: [
            {
              user_id: "55555555-5555-5555-5555-555555555555",
              full_name: "Hiền Lương",
              base_role: "EMPLOYEE",
              base_role_label: "Nhân viên",
              role: "BIEN_TAP",
              role_label: "Biên tập",
              is_lead: true,
              member_code: null,
              personal_nas_url: null,
              joined_at: "2026-10-07T02:00:00Z",
              left_at: null,
              active: true,
            },
          ],
          assignable_roles: [
            { role: "HEAD", label: "Trưởng phòng Ads" },
            { role: "BIEN_TAP", label: "Trưởng phòng Biên kịch", is_lead: true },
            { role: "ORDERER", label: "Marketing (người order)" },
            { role: "BIEN_TAP", label: "Biên tập" },
          ],
        },
      },
      { match: "/api/units/ADS/members", method: "POST", body: {} },
      {
        match: "/api/units/ADS/health",
        body: {
          unit: "ADS",
          warnings: [{ code: "no_head", message: "Ban chưa có Trưởng phòng." }],
        },
      },
      { match: "/api/units/PR/health", body: { unit: "PR", warnings: [] } },
      {
        match: "/api/units/directory",
        body: [
          {
            user_id: "66666666-6666-6666-6666-666666666666",
            full_name: "Tuấn",
            base_role: "EMPLOYEE",
            base_role_label: "Nhân viên",
            active: true,
            units: [],
          },
        ],
      },
    ]);
    renderWithQuery(<UnitsAdminPage />);
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: "Phòng Ads" }),
      ).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Phòng Ads" }));
    await waitFor(() =>
      expect(screen.getByText("Hiền Lương")).toBeInTheDocument(),
    );
    expect(screen.getByText("Ban chưa có Trưởng phòng.")).toBeInTheDocument();
    // A function's lead is shown as that function's head, in the role picker.
    expect(screen.getByLabelText("Vai trò của Hiền Lương")).toHaveValue("BIEN_TAP:LEAD");
    expect(screen.getByText("Nhận việc của ban để phân công")).toBeInTheDocument();

    await userEvent.selectOptions(
      screen.getByLabelText("Thêm thành viên"),
      "66666666-6666-6666-6666-666666666666",
    );
    await userEvent.selectOptions(screen.getByLabelText("Vai trò"), "ORDERER");
    await userEvent.type(screen.getByLabelText("Mã thành viên"), "tuan");
    await userEvent.click(screen.getByRole("button", { name: "Gắn tag" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Gắn tag" }),
    );
    await waitFor(() => {
      const sent = (
        fetchMock as unknown as {
          calls: Array<{ url: string; method: string; body: unknown }>;
        }
      ).calls.find(
        (call) =>
          call.method === "POST" && call.url.includes("/api/units/ADS/members"),
      );
      expect(sent?.body).toEqual({
        user_id: "66666666-6666-6666-6666-666666666666",
        role: "ORDERER",
        is_lead: false,
        member_code: "TUAN",
      });
    });
  });

  it("edits the Ads permission matrix and saves it whole", async () => {
    pathname = "/admin/units";
    const roles = ["HEAD", "ADMIN", "LEAD", "STAFF", "ORDERER"];
    const permissions = Object.fromEntries(
      roles.map((role) => [
        role,
        { NODE_ASSIGN: role === "LEAD" ? "OWN" : role === "HEAD" ? "ALL" : "NONE" },
      ]),
    );
    const settings = {
      ...ADS_SETTINGS,
      permissions,
      permission_catalog: [
        {
          key: "NODE_ASSIGN",
          label: "Giao việc",
          own_meaning: "Công đoạn của ban mình",
          scopes: ["NONE", "OWN", "ALL"],
        },
      ],
      permission_roles: [
        { key: "HEAD", label: "Trưởng phòng" },
        { key: "ADMIN", label: "Admin" },
        { key: "LEAD", label: "Leader" },
        { key: "STAFF", label: "Nhân viên" },
        { key: "ORDERER", label: "Marketing" },
      ],
      scope_labels: { NONE: "Không", OWN: "Trong ban mình", ALL: "Tất cả" },
    };
    const me = {
      ...OWNER_OF_BOTH,
      units: [
        OWNER_OF_BOTH.units[0],
        { ...OWNER_OF_BOTH.units[1], settings },
      ],
    };
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: me },
      {
        match: "/api/units/ADS/members",
        body: { unit: "ADS", unit_label: "Phòng Ads", members: [], assignable_roles: [] },
      },
      {
        match: "/api/units/PR/members",
        body: { unit: "PR", unit_label: "Phòng PR", members: [], assignable_roles: [] },
      },
      { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
      { match: "/api/units/PR/health", body: { unit: "PR", warnings: [] } },
      { match: "/api/units/directory", body: [] },
      { match: "/api/units/ADS/settings", method: "PATCH", body: settings },
    ]);
    renderWithQuery(<UnitsAdminPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Phòng Ads" }));
    const matrix = await screen.findByRole("region", { name: "Phân quyền ban Ads" });
    const cell = within(matrix).getByLabelText("Giao việc · Admin");
    expect(cell).toHaveValue("NONE");
    await userEvent.selectOptions(cell, "ALL");
    await userEvent.click(within(matrix).getByRole("button", { name: "Lưu phân quyền" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const sent = (
        fetchMock as unknown as {
          calls: Array<{ url: string; method: string; body: unknown }>;
        }
      ).calls.find(
        (call) => call.method === "PATCH" && call.url.includes("/api/units/ADS/settings"),
      );
      expect(sent?.body).toEqual({
        permissions: { ...permissions, ADMIN: { NODE_ASSIGN: "ALL" } },
      });
    });
  });
});


describe("the dashboard", () => {
  it("narrows every figure to one person picked from the dropdown", async () => {
    const member = (user_id: string, full_name: string) => ({
      user_id,
      full_name,
      base_role: "EMPLOYEE",
      base_role_label: "Nhân viên",
      role: "DUNG",
      role_label: "Dựng",
      is_lead: false,
      member_code: null,
      personal_nas_url: null,
      joined_at: "2026-10-07T02:00:00Z",
      left_at: null,
      active: true,
    });
    const summary = {
      unit: "ADS",
      unit_label: "Phòng Ads",
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      total: 3,
      completed: 1,
      pending_review: 1,
      urgent: 0,
      progress_percent: 33,
      by_phase: [],
      by_owner: [],
      by_worker: [],
    };
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: OWNER_OF_BOTH },
      {
        match: "/api/units/ADS/members",
        body: { unit: "ADS", unit_label: "Phòng Ads", members: [member("u-2", "Tiến Đạt"), member("u-1", "Bảo Khánh")], assignable_roles: [] },
      },
      {
        match: "/api/units/PR/members",
        body: { unit: "PR", unit_label: "Phòng PR", members: [member("u-1", "Bảo Khánh"), member("u-3", "Hà Chi")], assignable_roles: [] },
      },
      { match: "/api/board/dashboard", body: summary },
      { match: "/api/board/tasks", body: { ...PAGE, items: [], total: 0 } },
    ]);
    SEARCH.value = new URLSearchParams("unit=ALL");
    renderWithQuery(<DashboardPage />);
    const select = await screen.findByRole("combobox", { name: "Xem số liệu của một người" });
    await waitFor(() =>
      expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
        "Tất cả mọi người",
        "Bảo Khánh",
        "Hà Chi",
        "Tiến Đạt",
      ]),
    );
    await userEvent.selectOptions(select, "u-2");
    expect(SEARCH.value.get("person")).toBe("u-2");
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
      expect(calls.some((call) => call.url.includes("/api/board/dashboard") && call.url.includes("person=u-2"))).toBe(true);
    });
  });
});
