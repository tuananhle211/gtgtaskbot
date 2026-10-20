/**
 * "Tài nguyên" (0065): the nav branch follows the access flags; the list
 * paints a warned row red and sends its filters; the profile draws the two
 * timelines and the Phòng TN's buttons; the settings add an entry; the
 * employment status is a column of the members screen.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResourcesPage from "@/app/resources/page";
import ResourcePage from "@/app/resources/[id]/page";
import ResourceSettingsPage from "@/app/resources/settings/page";
import ResourceImportPage from "@/app/resources/import/page";
import { navFor } from "@/components/shell";
import { looksLikeSecret, resourceScreens } from "@/lib/resources";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

const search = { value: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "r-1" }),
  usePathname: () => "/resources",
  useSearchParams: () => search.value,
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const ACCESS = {
  view: true,
  view_all: true,
  edit: true,
  import_excel: true,
  settings: true,
  report: true,
  hr: true,
};

const ME: UnitsMe = {
  units: [],
  default_unit: null,
  can_view_all: false,
  can_admin: [],
  resources: ACCESS,
};

const option = (kind: string, code: string, name: string, group: string | null = null) => ({
  id: `${kind}-${code}`,
  kind,
  code,
  name,
  status_group: group,
  status_group_label: group === "LOST" ? "Mất" : group === "NORMAL" ? "Bình thường" : null,
  sort_order: 0,
  active: true,
});

const OPTIONS = {
  kinds: [
    { kind: "TYPE", label: "Loại tài nguyên", options: [option("TYPE", "VIA", "Via"), option("TYPE", "PAGE", "Page")] },
    { kind: "ROLE", label: "Quyền đang có", options: [option("ROLE", "ADMIN", "Chủ sở hữu/Admin")] },
    {
      kind: "STATUS",
      label: "Trạng thái tài nguyên",
      options: [option("STATUS", "ACTIVE", "Hoạt động", "NORMAL"), option("STATUS", "DIE", "Die/Mất", "LOST")],
    },
    { kind: "ORIGIN", label: "Nguồn gốc", options: [option("ORIGIN", "COMPANY", "Công ty cấp")] },
    { kind: "TWO_FACTOR", label: "Bảo mật 2 lớp", options: [option("TWO_FACTOR", "YES", "Có")] },
    { kind: "HANDOVER", label: "Bàn giao về Tài nguyên", options: [option("HANDOVER", "NOT_YET", "Chưa bàn giao")] },
  ],
  departments: [
    { id: "d-ads", code: "ADS", name: "ADS" },
    { id: "d-tn", code: "TN", name: "Tài nguyên" },
  ],
  handover_reasons: [
    { value: "NEW", label: "Cấp mới" },
    { value: "TRANSFER", label: "Chuyển người" },
    { value: "RETURN", label: "Thu hồi" },
  ],
  employment_statuses: [
    { value: "WORKING", label: "Đang làm" },
    { value: "LEFT", label: "Đã nghỉ việc" },
  ],
  status_groups: [
    { value: "NORMAL", label: "Bình thường" },
    { value: "ATTENTION", label: "Cần chú ý" },
    { value: "LOST", label: "Mất" },
    { value: "CLOSED", label: "Đã đóng" },
  ],
  alert_kinds: [],
  review_days: 30,
  can_manage: true,
};

const PERSON = (over: Record<string, unknown> = {}) => ({
  user_id: "u-1",
  full_name: "Nguyễn Văn A",
  active: true,
  employment_status: "WORKING",
  employment_status_label: "Đang làm",
  departments: [{ id: "d-ads", code: "ADS", name: "ADS" }],
  ...over,
});

const ROW = (over: Record<string, unknown> = {}) => ({
  id: "r-1",
  code: "ADS-001",
  type: option("TYPE", "VIA", "Via"),
  external_id: "104000000000000",
  name: "Via chạy ads",
  link: null,
  department: { id: "d-ads", code: "ADS", name: "ADS" },
  holder: PERSON(),
  role: null,
  email: null,
  phone: "0912000111",
  two_factor: null,
  two_factor_holder: null,
  origin: null,
  started_on: null,
  purpose: null,
  handover: option("HANDOVER", "NOT_YET", "Chưa bàn giao"),
  status: option("STATUS", "ACTIVE", "Hoạt động", "NORMAL"),
  reviewed_at: "2026-10-10T03:00:00Z",
  note: null,
  alerts: [],
  flagged: false,
  secret_warning: false,
  created_at: "2026-10-10T03:00:00Z",
  updated_at: "2026-10-10T03:00:00Z",
  detail_path: "/resources/r-1",
  ...over,
});

const LEFT_ROW = ROW({
  id: "r-2",
  code: "ADS-002",
  external_id: "77",
  name: "Page B",
  holder: PERSON({ user_id: "u-2", full_name: "Trần B", employment_status: "LEFT", employment_status_label: "Đã nghỉ việc" }),
  alerts: [{ kind: "HOLDER_LEFT", label: "Người giữ đã nghỉ việc", level: "bad" }],
  flagged: true,
  detail_path: "/resources/r-2",
});

describe("the Tài nguyên nav branch", () => {
  it("lists the screens the access flags allow, and nothing without view", () => {
    expect(resourceScreens(undefined)).toEqual([]);
    expect(resourceScreens({ ...ME, resources: { ...ACCESS, view: false } })).toEqual([]);
    const staff = resourceScreens({
      ...ME,
      resources: { ...ACCESS, import_excel: false, settings: false, report: false },
    }).map((screen) => screen.href);
    expect(staff).toEqual(["/resources", "/resources/mine"]);
    const all = resourceScreens(ME).map((screen) => screen.href);
    expect(all).toEqual([
      "/resources",
      "/resources/mine",
      "/resources/people",
      "/resources/reports",
      "/resources/import",
      "/resources/settings",
    ]);
    const nav = navFor(ME, "EMPLOYEE");
    const branch = nav.find((entry) => "children" in entry && entry.label === "Tài nguyên");
    expect(branch).toBeDefined();
    const none = navFor({ ...ME, resources: null }, "EMPLOYEE");
    expect(none.some((entry) => "children" in entry && entry.label === "Tài nguyên")).toBe(false);
  });

  it("warns about text that looks like a password", () => {
    expect(looksLikeSecret("pass: abc123")).toBe(true);
    expect(looksLikeSecret("Mật khẩu 123456")).toBe(true);
    expect(looksLikeSecret("mã khôi phục: 1234")).toBe(true);
    expect(looksLikeSecret("Dùng cho TKQC Apexmed")).toBe(false);
  });
});

describe("Danh sách tài nguyên", () => {
  it("draws the rows, paints a leaver's row red and sends the filters", async () => {
    search.value = new URLSearchParams("employee=LEFT&alerts=true");
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/people", body: { people: [PERSON()] } },
      {
        match: "/api/resources?",
        body: { items: [ROW(), LEFT_ROW], total: 2, limit: 10, offset: 0, review_days: 30 },
      },
    ]);
    renderWithQuery(<ResourcesPage />);
    const table = await screen.findByTestId("resource-table");
    expect(within(table).getByText("ADS-001")).toBeInTheDocument();
    expect(within(table).getByText("104000000000000")).toBeInTheDocument();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).not.toHaveAttribute("data-tone");
    expect(rows[1]).toHaveAttribute("data-tone", "bad");
    expect(within(rows[1]).getByText("Người giữ đã nghỉ việc")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Đã nghỉ việc")).toBeInTheDocument();
    // The filters on the URL reached the request.
    const request = calls(stub).find((call) => call.url.includes("/api/resources?"));
    expect(request?.url).toContain("employment_status=LEFT");
    expect(request?.url).toContain("alerts=true");
    // The export link carries the same filter; "Thêm tài nguyên" is there for an editor.
    const exportLink = screen.getByRole("link", { name: "Xuất Excel" });
    expect(exportLink.getAttribute("href")).toContain("/api/resources/export.xlsx?");
    expect(exportLink.getAttribute("href")).toContain("employment_status=LEFT");
    expect(screen.getByRole("button", { name: "+ Thêm tài nguyên" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Trạng thái nhân viên" })).toHaveValue("LEFT");
  });

  it("opens the form and sends a new resource with its id as text", async () => {
    search.value = new URLSearchParams();
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/people", body: { people: [PERSON()] } },
      { match: "/api/resources?", body: { items: [], total: 0, limit: 10, offset: 0, review_days: 30 } },
      {
        match: "/api/resources",
        method: "POST",
        status: 201,
        body: { resource: ROW({ id: "r-9", code: "ADS-009" }), handovers: [], statuses: [], changes: [], can_edit: true },
      },
    ]);
    renderWithQuery(<ResourcesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Thêm tài nguyên" }));
    const dialog = await screen.findByRole("dialog", { name: "Thêm tài nguyên" });
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Loại/), "TYPE-VIA");
    await userEvent.type(within(dialog).getByLabelText(/ID tài nguyên/), "0012345678901234");
    await userEvent.type(within(dialog).getByLabelText(/Tên tài nguyên/), "Via mới");
    await userEvent.type(within(dialog).getByLabelText("Ghi chú"), "pass: abc");
    expect(within(dialog).getByRole("alert")).toHaveTextContent(/giống mật khẩu/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Thêm tài nguyên" }));
    await waitFor(() =>
      expect(calls(stub).filter((call) => call.method === "POST")).toHaveLength(1),
    );
    const sent = calls(stub).find((call) => call.method === "POST")?.body as Record<string, unknown>;
    expect(sent.external_id).toBe("0012345678901234");
    expect(sent.type_id).toBe("TYPE-VIA");
    expect(sent.department_id).toBe("d-ads");
    expect(sent.status_id).toBe("STATUS-ACTIVE");
    expect(sent.handover_id).toBe("HANDOVER-NOT_YET");
    expect(sent.note).toBe("pass: abc");
  });

  it("tells somebody without access, instead of asking the server", async () => {
    search.value = new URLSearchParams();
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { ...ME, resources: { ...ACCESS, view: false } } },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/people", body: { people: [] } },
    ]);
    renderWithQuery(<ResourcesPage />);
    expect(await screen.findByText(/Bạn chưa được xem mục Tài nguyên/)).toBeInTheDocument();
    expect(calls(stub).some((call) => call.url.includes("/api/resources?"))).toBe(false);
  });
});

describe("Hồ sơ tài nguyên", () => {
  const DETAIL = {
    resource: LEFT_ROW,
    handovers: [
      {
        id: "h-1",
        at: "2026-10-12T03:00:00Z",
        from_person: null,
        to_person: PERSON({ user_id: "u-2", full_name: "Trần B" }),
        reason: "NEW",
        reason_label: "Cấp mới",
        note: "Cấp cho MKT",
        actor: PERSON({ user_id: "u-9", full_name: "TP Tài nguyên" }),
      },
    ],
    statuses: [
      {
        id: "s-2",
        at: "2026-10-13T03:00:00Z",
        from_status: option("STATUS", "ACTIVE", "Hoạt động", "NORMAL"),
        to_status: option("STATUS", "ACTIVE", "Hoạt động", "NORMAL"),
        review_only: true,
        note: "Đã rà soát",
        actor: PERSON({ user_id: "u-9", full_name: "TP Tài nguyên" }),
      },
      {
        id: "s-1",
        at: "2026-10-10T03:00:00Z",
        from_status: null,
        to_status: option("STATUS", "ACTIVE", "Hoạt động", "NORMAL"),
        review_only: false,
        note: "Tạo mới",
        actor: null,
      },
    ],
    changes: [
      {
        id: "c-1",
        at: "2026-10-11T03:00:00Z",
        field: "name",
        field_label: "Tên tài nguyên",
        old_value: "Page",
        new_value: "Page B",
        actor: PERSON({ user_id: "u-9", full_name: "TP Tài nguyên" }),
      },
    ],
    can_edit: true,
  };

  it("draws the fields, the alert, both timelines and the change log, with the buttons", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/people", body: { people: [PERSON()] } },
      { match: "/api/resources/r-1", body: DETAIL },
    ]);
    renderWithQuery(<ResourcePage />);
    expect(await screen.findByRole("heading", { name: /ADS-002 · Page B/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Người giữ đã nghỉ việc");
    const handovers = screen.getByRole("region", { name: "Lịch sử bàn giao" });
    expect(within(handovers).getByText("Cấp mới")).toBeInTheDocument();
    expect(within(handovers).getByText(/Cấp cho MKT/)).toBeInTheDocument();
    const statuses = screen.getByRole("region", { name: "Lịch sử trạng thái" });
    expect(within(statuses).getByText("Đã rà soát")).toBeInTheDocument();
    const changes = screen.getByRole("region", { name: "Nhật ký thay đổi" });
    expect(within(changes).getByText("Tên tài nguyên")).toBeInTheDocument();
    expect(within(changes).getByText("Page B")).toBeInTheDocument();
    for (const name of ["Sửa", "Đổi người được bàn giao", "Cập nhật trạng thái", "Đã rà soát"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    // The 2FA: who holds it, never the code - there is no such field anywhere.
    expect(screen.getByText("Người giữ mã 2 lớp")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/mã 2 lớp:/i);
  });

  it("hides the buttons from a reader", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/r-1", body: { ...DETAIL, can_edit: false } },
    ]);
    renderWithQuery(<ResourcePage />);
    await screen.findByRole("heading", { name: /ADS-002/ });
    expect(screen.queryByRole("button", { name: "Đổi người được bàn giao" })).not.toBeInTheDocument();
  });

  it("sends a status update from its dialog", async () => {
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      { match: "/api/resources/people", body: { people: [PERSON()] } },
      { match: "/api/resources/r-1/status", method: "POST", body: DETAIL },
      { match: "/api/resources/r-1", body: DETAIL },
    ]);
    renderWithQuery(<ResourcePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Cập nhật trạng thái" }));
    const dialog = await screen.findByRole("dialog", { name: /Cập nhật trạng thái/ });
    await userEvent.selectOptions(within(dialog).getByLabelText("Trạng thái mới"), "STATUS-DIE");
    await userEvent.type(within(dialog).getByLabelText("Ghi chú"), "FB khoá");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cập nhật trạng thái" }));
    await waitFor(() =>
      expect(calls(stub).filter((call) => call.url.endsWith("/status"))).toHaveLength(1),
    );
    expect(calls(stub).find((call) => call.url.endsWith("/status"))?.body).toEqual({
      status_id: "STATUS-DIE",
      note: "FB khoá",
    });
  });
});

describe("Cài đặt tài nguyên", () => {
  it("adds an entry to a pick-list after confirming", async () => {
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
      { match: "/api/resources/options", body: OPTIONS },
      {
        match: "/api/resources/options",
        method: "POST",
        status: 201,
        body: option("TYPE", "ZALO_OA", "Zalo OA"),
      },
    ]);
    renderWithQuery(<ResourceSettingsPage />);
    const types = await screen.findByRole("region", { name: "Loại tài nguyên" });
    expect(within(types).getByDisplayValue("Via")).toBeInTheDocument();
    await userEvent.type(within(types).getByLabelText("Thêm lựa chọn"), "Zalo OA");
    await userEvent.click(within(types).getByRole("button", { name: "Thêm" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Thêm lựa chọn" }));
    await waitFor(() =>
      expect(calls(stub).filter((call) => call.method === "POST")).toHaveLength(1),
    );
    expect(calls(stub).find((call) => call.method === "POST")?.body).toEqual({
      kind: "TYPE",
      name: "Zalo OA",
      status_group: null,
    });
    // A status entry asks for its group.
    const statuses = screen.getByRole("region", { name: "Trạng thái tài nguyên" });
    expect(within(statuses).getByLabelText("Nhóm trạng thái")).toBeInTheDocument();
  });
});

describe("the import page", () => {
  it('offers "Tải file mẫu" as a download of the server\'s template (2026-10-19)', async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: ME },
    ]);
    renderWithQuery(<ResourceImportPage />);
    const link = await screen.findByRole("link", { name: /Tải file mẫu/ });
    expect(link).toHaveAttribute("href", "/api/resources/import/template.xlsx");
    expect(link).toHaveAttribute("download");
    expect(screen.getByText(/chỉ có một sheet/)).toBeInTheDocument();
  });
});
