/**
 * Tài chính đợt 1 (0105): the "Tài chính" menu, the "Doanh thu" month read
 * from the CRM, and "Cấu hình" (a CRM branch -> pháp nhân / cơ sở, "Đồng bộ
 * ngay" for the one run shared with Phòng Ads).
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RevenuePage from "@/app/finance/revenue/page";
import SettingsPage from "@/app/finance/settings/page";
import { navFor } from "@/components/shell";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/finance/revenue",
  useSearchParams: () => new URLSearchParams("month=2026-10"),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter((call) => call.method === method && call.url.includes(fragment));

const figures = (collected: number, sales = 0) => ({
  collected,
  collected_vat: Math.round(collected * 0.074),
  collected_net: collected - Math.round(collected * 0.074),
  refunds: -108000,
  payments: 3,
  sales,
  revenue: Math.round(sales / 1.08),
  sales_vat: sales - Math.round(sales / 1.08),
  owed: 1000000,
  orders: 2,
});

const MONTH = {
  month: "2026-10",
  figures: figures(2972000, 4320000),
  previous: figures(2000000, 5000000),
  previous_from: "2026-09-01",
  previous_to: "2026-09-30",
  by_entity: [
    { key: "e1", label: "APEX - Apexmed", figures: figures(972000, 1080000) },
    { key: "", label: "Chưa gắn", figures: figures(2000000, 3240000) },
  ],
  by_site: [],
  by_branch: [],
  by_team: [],
  days: [
    { day: "2026-10-01", collected: 0 },
    { day: "2026-10-02", collected: 1080000 },
  ],
  roles: [
    {
      role: "telesales",
      label: "Telesale (người phụ trách)",
      people: [
        { crm_id: 20, name: "Sale B", user_id: "u1", user_name: "Lê Thu Hà", collected: 2972000, payments: 3 },
      ],
    },
  ],
  shares: [{ crm_id: 700, name: "BS An", department: "Bác Sĩ", user_name: null, shared: 300000, lines: 1 }],
  tours: [{ crm_id: 800, name: "KTV Bình", user_name: null, primary: 1, support: 0 }],
  unmapped_branches: 1,
  synced_at: "2026-10-21T05:00:00Z",
  sync_status: "SUCCESS",
  sync_message: "ok",
};

const ACCESS = { revenue: true, customers: true, settings: true };
const CATALOG = {
  roles: [
    { value: "DEPT_HEAD", label: "Trưởng phòng / GĐ trung tâm" },
    { value: "CFO", label: "Phòng Tài chính (CFO)" },
    { value: "CASHIER_VP", label: "Thủ quỹ VP" },
  ],
  assignable: [],
  purchase_roles: [],
  payment_roles: [],
  executor_roles: [],
  cash_roles: [],
  documents: [{ value: "INVOICE", label: "Hoá đơn VAT / Hoá đơn bán hàng" }],
  phases: [],
  statuses: [],
  branches: [{ value: "CS_HN", label: "CS - Hà Nội" }],
  entities: [{ value: "e1", label: "APEXMED - Apexmed" }],
  departments: [{ value: "d1", label: "QHKH - Phòng QHKH" }],
  access: { view_requests: true, codes: true, budget: true, misa: true, report: true },
};

type Entry = ReturnType<typeof navFor>[number];
const flat = (entries: Entry[]): string[] =>
  entries.flatMap((entry) =>
    "children" in entry
      ? entry.children.map((child: { href: string }) => child.href)
      : [(entry as { href: string }).href],
  );

describe("Tài chính", () => {
  it("shows the menu only to who holds a finance right", () => {
    const base = { units: [], can_admin: [], departments: [], nav_pins: [] } as unknown as UnitsMe;
    expect(flat(navFor(base, "EMPLOYEE"))).not.toContain("/finance/revenue");
    const reader = { ...base, finance: { revenue: true, customers: false, settings: false } } as UnitsMe;
    expect(flat(navFor(reader, "EMPLOYEE"))).toEqual(
      expect.arrayContaining(["/finance/revenue", "/finance/settings"]),
    );
  });

  it("draws the month: tiles, the split by pháp nhân, people, shares", async () => {
    stubFetch([
      { match: "/api/finance/access", body: ACCESS },
      { match: "/api/finance/revenue?month=2026-10", body: MONTH },
    ]);
    renderWithQuery(<RevenuePage />);
    const tiles = await screen.findByTestId("finance-tiles");
    expect(tiles).toHaveTextContent("Thực thu (tiền về, gồm VAT)");
    expect(tiles).toHaveTextContent("2.972.000 ₫");
    expect(tiles).toHaveTextContent("+49% so với tháng 9");
    expect(screen.getByTestId("unmapped-note")).toHaveTextContent("1 chi nhánh CRM chưa gắn");
    const rows = screen.getAllByTestId("split-row");
    expect(rows[0]).toHaveTextContent("APEX - Apexmed");
    expect(rows[1]).toHaveTextContent("Chưa gắn");
    await userEvent.click(screen.getByRole("tab", { name: "Theo người" }));
    expect(await screen.findByTestId("person-row")).toHaveTextContent("APM: Lê Thu Hà");
    await userEvent.click(screen.getByRole("tab", { name: "Chia doanh thu" }));
    expect(await screen.findByTestId("share-row")).toHaveTextContent("300.000 ₫");
    await userEvent.click(screen.getByRole("tab", { name: "Công tua" }));
    expect(await screen.findByTestId("tour-row")).toHaveTextContent("KTV Bình");
  });

  it("maps a CRM branch to a pháp nhân and a cơ sở, then syncs", async () => {
    const stub = stubFetch([
      { match: "/api/finance/access", body: ACCESS },
      { match: "/api/finance/codes", body: { codes: [], can_edit: false } },
      { match: "/api/finance/spend/catalog", body: CATALOG },
      {
        match: "/api/finance/branches",
        body: {
          branches: [{ id: 1, name: "HN1", location_id: 1, legal_entity_id: null, apm_branch: null }],
          entities: [{ id: "e1", code: "APEX", name: "Apexmed", active: true }],
          sites: [
            { code: "CS_HN", label: "CS - Hà Nội" },
            { code: "CS_SG", label: "CS - Sài Gòn" },
          ],
          can_edit: true,
        },
      },
      {
        match: "/api/finance/branches/1",
        method: "PATCH",
        body: { id: 1, name: "HN1", location_id: 1, legal_entity_id: "e1", apm_branch: "CS_HN" },
      },
      {
        match: "/api/finance/sync",
        body: {
          configured: true,
          at: null,
          status: null,
          message: null,
          synced_from: null,
          synced_to: null,
          running: false,
          can_sync: true,
        },
      },
      {
        match: "/api/finance/sync",
        method: "POST",
        body: { task_id: "t1", job_key: "ads.sync_crm_revenue" },
      },
    ]);
    renderWithQuery(<SettingsPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Chi nhánh CRM" }));
    const row = await screen.findByTestId("branch-row");
    await userEvent.selectOptions(within(row).getByLabelText("Pháp nhân của HN1"), "e1");
    await userEvent.selectOptions(within(row).getByLabelText("Cơ sở của HN1"), "CS_HN");
    await userEvent.click(screen.getByRole("button", { name: "Lưu thay đổi (1)" }));
    await userEvent.click(await screen.findByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/finance/branches/1")[0]?.body).toEqual({
        legal_entity_id: "e1",
        apm_branch: "CS_HN",
      }),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Đồng bộ CRM" }));
    expect(await screen.findByText("Chưa đồng bộ lần nào")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Đồng bộ ngay" }));
    await userEvent.click(await screen.findByRole("button", { name: "Đồng bộ" }));
    await waitFor(() => expect(sent(stub, "POST", "/api/finance/sync")).toHaveLength(1));
    expect(await screen.findByRole("status")).toHaveTextContent("Đã gửi yêu cầu đồng bộ");
  });
});

describe("Tính năng đang phát triển", () => {
  it("lets the OWNER release Tài chính, after confirming", async () => {
    const { default: DevelopingPage } = await import("@/app/admin/developing/page");
    const stub = stubFetch([
      { match: "/api/session", body: { role: "OWNER", user_id: "o", full_name: "Chủ", active: true, capabilities: [] } },
      { match: "/api/units/me", body: { units: [], can_admin: [], departments: [] } },
      {
        match: "/api/permissions/developing-modules",
        body: { modules: [{ module: "FINANCE", label: "Tài chính", developing: true }] },
      },
      {
        match: "/api/permissions/developing-modules",
        method: "PUT",
        body: { modules: [{ module: "FINANCE", label: "Tài chính", developing: false }] },
      },
    ]);
    renderWithQuery(<DevelopingPage />);
    const row = await screen.findByTestId("developing-row");
    expect(row).toHaveTextContent("Đang ẩn");
    await userEvent.click(within(row).getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Lưu" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Tài chính: mở cho người được cấp quyền.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/permissions/developing-modules")[0]?.body).toEqual({ modules: [] }),
    );
  });
});

describe("Tài chính đợt 2", () => {
  const FEES = { card_fee: 44800, referral_fee: 0, car_fee: 0, cost: 0, total: 44800 };
  const LEDGER = {
    since: "2026-10-01",
    until: "2026-10-31",
    rows: [
      {
        order_id: 101,
        day: "2026-10-21",
        kind: "NEW",
        order_code: "DH101",
        product: false,
        customer_code: "KH101",
        customer_name: "Khách 101",
        branch: "SG",
        sales: 3240000,
        owed: 1000000,
        owed_days: 10,
        collected: 2240000,
        methods: { "2": 2240000 },
        refunds: 0,
        vat: 165926,
        fees: FEES,
        auto: FEES,
        typed: [],
        note: null,
        net: 2029274,
        sellers: [{ name: "KTV Bình", amount: 100000 }],
        doctors: {},
        sale: "Sale B",
        marketing: "HN - MKT A",
        carepage: null,
      },
    ],
    days: [
      {
        day: "2026-10-21",
        sales: 3240000,
        owed: 1000000,
        service_sales: 3240000,
        product_sales: 0,
        received: 2240000,
        refunds: 0,
        collected: 2240000,
        vat: 165926,
        fees: 44800,
        net: 2029274,
        kpi: 1000000,
      },
    ],
    kpi_month: 31000000,
    methods: { "5": "Trả góp", "2": "Thẻ", "6": "Nhà xe thu hộ", "4": "Chuyển khoản", "1": "Tiền mặt" },
    can_edit_fees: true,
  };

  it("lists the bảng kê and types a fee over the automatic one", async () => {
    const { default: LedgerPage } = await import("@/app/finance/ledger/page");
    const stub = stubFetch([
      { match: "/api/finance/access", body: { ...ACCESS, fees: true } },
      { match: "/api/finance/ledger?since=2026-10-01&until=2026-10-31", body: LEDGER },
      { match: "/api/finance/fees", method: "PATCH", body: LEDGER.rows[0] },
    ]);
    renderWithQuery(<LedgerPage />);
    const row = await screen.findByTestId("ledger-row");
    expect(row).toHaveTextContent("DH101");
    expect(row).toHaveTextContent("Đơn mới");
    expect(row).toHaveTextContent("44.800 ₫");
    expect(screen.getByRole("link", { name: "Xuất Excel" })).toHaveAttribute(
      "href",
      "/api/finance/ledger.xlsx?month=2026-10",
    );
    await userEvent.click(within(row).getByRole("button", { name: "Sửa phí DH101 ngày 21/10" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Viện phí \/ giá cost/), "500000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu phí" }));
    await userEvent.click(await screen.findByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/finance/fees")[0]?.body).toEqual({
        order_id: 101,
        day: "2026-10-21",
        card_fee: null,
        referral_fee: null,
        car_fee: null,
        cost: "500000",
        note: null,
      }),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Tổng hợp theo ngày" }));
    expect(await screen.findByTestId("summary-row")).toHaveTextContent("7,2%");
  });

  it("shows công nợ by age bucket", async () => {
    const { default: DebtsPage } = await import("@/app/finance/debts/page");
    stubFetch([
      { match: "/api/finance/access", body: ACCESS },
      {
        match: "/api/finance/debts",
        body: {
          today: "2026-12-15",
          milestones: [30, 45, 60],
          lines: [
            {
              order_id: 101,
              code: "DH101",
              created_on: "2026-10-21",
              age: 55,
              bucket: 45,
              customer_code: "KH101",
              customer_name: "Khách 101",
              branch: "SG",
              owner: "Người lên đơn",
              total: 3240000,
              owed: 1000000,
            },
          ],
          buckets: [
            { milestone: null, orders: 0, owed: 0 },
            { milestone: 30, orders: 0, owed: 0 },
            { milestone: 45, orders: 1, owed: 1000000 },
            { milestone: 60, orders: 0, owed: 0 },
          ],
          owed: 1000000,
        },
      },
    ]);
    renderWithQuery(<DebtsPage />);
    expect(await screen.findByTestId("debt-buckets")).toHaveTextContent("Quá 45 ngày1.000.000 ₫1 đơn");
    expect(screen.getByTestId("debt-row")).toHaveTextContent("55 ngày");
  });
});


describe("Tài chính đợt 3: hồ sơ chi", () => {
  const DETAIL = {
    id: "r1",
    code: "DC-2611-0001",
    title: "Vật tư tiêu hao",
    amount: 4000000,
    budget_code: "CO.03.01",
    status: "PENDING",
    status_label: "Đang xử lý",
    phase: "PURCHASE",
    phase_label: "Duyệt đề nghị mua sắm",
    requester: "Staff",
    created_at: "2026-11-13T03:00:00Z",
    paid_at: null,
    docs_due_on: null,
    description: "",
    vendor: null,
    payee: null,
    legal_entity_id: "e1",
    legal_entity: "APEXMED - Apexmed",
    department_id: "d1",
    department: "Phòng QHKH",
    apm_branch: null,
    branch: null,
    code_name: "Chi phí vật tư tiêu hao trực tiếp",
    budget_month: "2026-11",
    needed_on: null,
    purchase_note: null,
    realized_at: null,
    version: 1,
    route: {},
    steps: [
      { id: 1, round: 1, phase: "PURCHASE", phase_label: "Duyệt đề nghị mua sắm", role: "DEPT_HEAD", role_label: "Trưởng phòng / GĐ trung tâm", status: "PENDING", approver: "Head", standin: null, decided_by: null, decided_at: null, note: null, remark: null },
      { id: 2, round: 1, phase: "CASH", phase_label: "Chi tiền", role: "CASHIER_VP", role_label: "Thủ quỹ VP", status: "WAITING", approver: null, standin: null, decided_by: null, decided_at: null, note: null, remark: null },
    ],
    open_step_id: 1,
    events: [{ at: "2026-11-13T03:00:00Z", by: "Staff", action: "submitted", note: null }],
    documents: [],
    required: [{ value: "INVOICE", label: "Hoá đơn VAT / Hoá đơn bán hàng" }],
    documents_note: "",
    missing: [{ value: "INVOICE", label: "Hoá đơn VAT / Hoá đơn bán hàng" }],
    budget: { budget: 10000000, committed: 4000000, realized: 0, remaining: 6000000 },
    can: ["approve", "return", "reject"],
    can_paper: false,
  };

  it("shows the steps and the budget, and the approver approves", async () => {
    const { default: RequestPage } = await import("@/app/finance/requests/[id]/page");
    const { act } = await import("@testing-library/react");
    const stub = stubFetch([
      { match: "/api/finance/requests/r1/actions", method: "POST", body: { ...DETAIL, can: [] } },
      { match: "/api/finance/requests/r1", body: DETAIL },
    ]);
    await act(async () => {
      renderWithQuery(<RequestPage params={Promise.resolve({ id: "r1" })} />);
    });
    const steps = await screen.findAllByTestId("request-step");
    expect(steps[0]).toHaveTextContent("Trưởng phòng / GĐ trung tâm");
    expect(steps[0]).toHaveTextContent("Đang chờ");
    expect(steps[1]).toHaveTextContent("Chưa tới lượt");
    expect(screen.getByTestId("request-budget")).toHaveTextContent("6.000.000 ₫");
    expect(screen.getByTestId("required-documents")).toHaveTextContent("Thiếu");
    // "Không duyệt" needs a reason first.
    expect(screen.getByRole("button", { name: "Không duyệt" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Duyệt" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Duyệt" }));
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/finance/requests/r1/actions")[0]?.body).toEqual({
        action: "approve",
        note: null,
        step_id: 1,
        purchase_note: null,
      }),
    );
  });

  it("files a request: the code's route is shown, the amount suggested", async () => {
    const { default: NewRequestPage } = await import("@/app/finance/requests/new/page");
    const code = {
      code: "CO.06.02.01",
      name: "Chi mua văn phòng phẩm",
      parent_code: "CO.06.02",
      kind: "Định phí",
      leaf: true,
      active: true,
      no_route: false,
      route: {
        purchase: ["DEPT_HEAD"],
        executor: null,
        payment: ["DEPT_HEAD"],
        cash: ["CASHIER_VP"],
        documents: ["INVOICE"],
        documents_note: "",
        no_approval: false,
        proposer: "",
      },
    };
    const stub = stubFetch([
      { match: "/api/finance/spend/catalog", body: CATALOG },
      {
        match: "/api/finance/requests/form",
        body: { codes: [code], my_departments: ["d1"], my_entity: "e1", my_branch: null },
      },
      { match: "/api/finance/requests", method: "POST", body: { id: "r9", code: "DC-2611-0009" } },
    ]);
    renderWithQuery(<NewRequestPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Mã ngân sách"), "CO.06.02.01");
    expect(screen.getByTestId("route-summary")).toHaveTextContent("Duyệt mua sắm: Trưởng phòng / GĐ trung tâm");
    expect(screen.getByTestId("route-summary")).toHaveTextContent("Chứng từ cần có: Hoá đơn VAT");
    await userEvent.type(screen.getByLabelText("Nội dung chi"), "Giấy in");
    await userEvent.type(screen.getByLabelText("Số tiền"), "15");
    await userEvent.click(screen.getByRole("button", { name: "15.000.000 ₫" }));
    await userEvent.click(screen.getByRole("button", { name: "Gửi hồ sơ" }));
    await userEvent.click(await screen.findByRole("button", { name: "Gửi" }));
    await waitFor(() => {
      const body = sent(stub, "POST", "/api/finance/requests")[0]?.body as Record<string, unknown>;
      expect(body).toMatchObject({
        legal_entity_id: "e1",
        department_id: "d1",
        budget_code: "CO.06.02.01",
        title: "Giấy in",
        amount: "15000000",
      });
    });
  });
});
