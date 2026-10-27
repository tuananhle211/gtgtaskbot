/**
 * Phòng Ads, step 1 (0078): the nav branch, "Kết nối BM" (OWNER), the staff
 * dashboard and "Tài khoản QC".
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdsConnectionsPage from "@/app/ads/connections/page";
import AdsDashboardPage from "@/app/ads/page";
import AdsAccountsPage from "@/app/ads/accounts/page";
import AdsRevenuePage from "@/app/ads/revenue/page";
import AdsKpiPage from "@/app/ads/kpi/page";
import AdsTeamPage from "@/app/ads/team/page";
import { adminTabsFor } from "@/components/admin-tabs";
import { navFor } from "@/components/shell";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/ads",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const sent = (stub: unknown, method: string, fragment: string) =>
  calls(stub).filter(
    (call) => call.method === method && call.url.includes(fragment),
  );

const me = (ads: Record<string, boolean> | null) =>
  ({
    units: [],
    can_admin: [],
    can_view_all: false,
    ads,
  }) as unknown as UnitsMe;

const hrefs = (entries: ReturnType<typeof navFor>) =>
  entries.flatMap((entry) =>
    "children" in entry
      ? entry.children.map((child) => child.href)
      : [entry.href],
  );

describe("who sees Phòng Ads", () => {
  it("is a nav branch only with ads.view, and Kết nối BM is the OWNER's tab", () => {
    expect(
      hrefs(
        navFor(
          me({
            view: true,
            view_all: false,
            manage_accounts: false,
            manage_connections: false,
          }),
        ),
      ),
    ).toEqual(expect.arrayContaining(["/ads", "/ads/accounts"]));
    expect(hrefs(navFor(me(null)))).not.toContain("/ads");
    // Its settings sit in the same branch (27/10): Kết nối BM for the OWNER.
    const owner = me({
      view: true,
      view_all: true,
      manage_accounts: true,
      manage_connections: true,
    });
    expect(hrefs(navFor(owner, "OWNER"))).toEqual(
      expect.arrayContaining(["/ads/connections", "/ads/jobs"]),
    );
    const admin = me({
      view: true,
      view_all: true,
      manage_accounts: true,
      manage_connections: false,
    });
    expect(hrefs(navFor(admin, "ADMIN"))).not.toContain("/ads/connections");
    expect(adminTabsFor(owner, "OWNER").map((tab) => tab.href)).not.toContain(
      "/admin/ads",
    );
  });
});

const connection = (over: Record<string, unknown> = {}) => ({
  id: "bm-1",
  name: "BM Apex",
  business_id: "1234567890",
  site: "Sài Gòn",
  note: null,
  token_hint: "••••9876",
  active: true,
  meta_name: null,
  account_count: 0,
  last_tested_at: null,
  last_test_ok: null,
  last_test_message: null,
  last_spend_sync_at: null,
  last_spend_sync_ok: null,
  last_spend_sync_message: null,
  last_charge_sync_at: null,
  last_charge_sync_ok: null,
  last_charge_sync_message: null,
  ...over,
});

function openConnections(
  extra: Array<{
    match: string;
    method?: string;
    status?: number;
    body?: unknown;
  }> = [],
) {
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
    {
      match: "/api/units/me",
      body: {
        units: [],
        can_admin: [],
        can_view_all: true,
        default_unit: null,
      },
    },
    {
      match: "/api/ads/connections",
      method: "GET",
      body: {
        connections: [connection()],
        secret_ready: true,
        sites: ["Sài Gòn"],
      },
    },
  ]);
  renderWithQuery(<AdsConnectionsPage />);
  return stub;
}

describe("Kết nối BM", () => {
  it("lists each BM with its token hint and tests the connection", async () => {
    const stub = openConnections([
      {
        match: "/api/ads/connections/bm-1/test",
        method: "POST",
        body: {
          ok: true,
          message: "Kết nối được · BM Apexmed SG · 3 tài khoản quảng cáo.",
          latency_ms: 120,
          connection: connection({ last_test_ok: true, account_count: 3 }),
        },
      },
    ]);
    const row = await screen.findByTestId("ads-connection");
    expect(within(row).getByText(/••••9876/)).toBeInTheDocument();
    expect(within(row).getByText("Sài Gòn")).toBeInTheDocument();
    expect(within(row).getByText("Chưa kiểm tra kết nối")).toBeInTheDocument();
    await userEvent.click(
      within(row).getByRole("button", { name: "Test kết nối" }),
    );
    expect(
      await within(row).findByText(
        /Kết nối được · BM Apexmed SG · 3 tài khoản/,
      ),
    ).toBeInTheDocument();
    expect(sent(stub, "POST", "/api/ads/connections/bm-1/test")).toHaveLength(
      1,
    );
  });

  it("adds a BM with its cơ sở and token", async () => {
    const stub = openConnections([
      {
        match: "/api/ads/connections",
        method: "POST",
        status: 201,
        body: connection({ id: "bm-2" }),
      },
    ]);
    const form = within(await screen.findByRole("region", { name: "Thêm BM" }));
    const add = form.getByRole("button", { name: "Thêm BM" });
    expect(add).toBeDisabled();
    await userEvent.type(form.getByLabelText("Tên BM"), "BM Hà Nội");
    await userEvent.type(form.getByLabelText("Business ID"), "555");
    await userEvent.type(form.getByLabelText("Cơ sở"), "Hà Nội");
    await userEvent.type(form.getByLabelText("Token"), "EAAsecret");
    await userEvent.click(add);
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/ads/connections")).toHaveLength(1),
    );
    expect(sent(stub, "POST", "/api/ads/connections")[0].body).toEqual({
      name: "BM Hà Nội",
      business_id: "555",
      site: "Hà Nội",
      token: "EAAsecret",
      note: null,
    });
  });

  it("Đồng bộ ngay asks first, then queues the job", async () => {
    const stub = openConnections([
      {
        match: "/api/ads/sync",
        method: "POST",
        status: 202,
        body: { task_id: "t-1", job_key: "ads.sync_spend" },
      },
    ]);
    const panel = within(
      await screen.findByRole("region", { name: "Đồng bộ" }),
    );
    await userEvent.click(panel.getByRole("button", { name: "Đồng bộ ngay" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Đồng bộ ngay" }),
    );
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/ads/sync")).toHaveLength(1),
    );
    expect(sent(stub, "POST", "/api/ads/sync")[0].body).toEqual({
      kind: "spend",
    });
  });
});

const kpi = (
  key: string,
  label: string,
  value: number | null,
  change: number | null,
  lower = false,
) => ({
  key,
  label,
  value,
  previous: null,
  change,
  lower_is_better: lower,
});

const DASHBOARD = {
  start: "2026-10-13",
  end: "2026-10-26",
  previous_start: "2026-09-29",
  previous_end: "2026-10-12",
  grouping: "day",
  user_id: null,
  kpis: [
    kpi("spend", "Tiền chi tiêu", 312_500_000, 0.051),
    kpi("messages", "Số mess", 4250, 0.062),
    kpi("cost_per_message", "Giá mess", 73_500, -0.01, true),
    kpi("phones", "SĐT thu được", 1020, 0.097),
    kpi("phone_rate", "% mess ra số", 0.24, null),
    kpi("cost_per_phone", "Chi phí / SĐT", 306_400, -0.042, true),
    kpi("invoice", "Số tiền lập hoá đơn", 333_000_000, null),
  ],
  series: [
    {
      start: "2026-10-25",
      spend: 20_000_000,
      messages: 300,
      phones: 70,
      cost_per_phone: 285_714,
    },
    {
      start: "2026-10-26",
      spend: 22_000_000,
      messages: 310,
      phones: 60,
      cost_per_phone: 366_666,
    },
  ],
  best_day: {
    start: "2026-10-25",
    spend: 20_000_000,
    messages: 300,
    phones: 70,
    cost_per_phone: 285_714,
  },
  review_day: {
    start: "2026-10-26",
    spend: 22_000_000,
    messages: 310,
    phones: 60,
    cost_per_phone: 366_666,
  },
  accounts: [
    {
      account_id: "111",
      name: "[COREMIND] TK 1",
      connection_name: "BM Apex",
      site: "Sài Gòn",
      brand: "COREMIND",
      brand_label: "COREMIND",
      user_id: "u-1",
      user_name: "Mạnh Ngọc",
      spend: 58_200_000,
      messages: 900,
      phones: 200,
      charged: 55_000_000,
      invoice: 61_160_500,
      difference: -3_200_000,
      matched: false,
      card_last4: "4037",
      invoice_ratio: 1.051,
      spend_by_day: [27_000_000, 31_200_000],
      invoice_by_day: [0, 61_160_500],
    },
  ],
  days: ["2026-10-25", "2026-10-26"],
  people: [{ user_id: "u-1", name: "Mạnh Ngọc", member_code: "NGOC01" }],
  connections: [{ id: "bm-1", name: "BM Apex", site: "Sài Gòn" }],
  sites: ["Sài Gòn"],
  brands: [
    { value: "COREMIND", label: "COREMIND" },
    { value: "GPI", label: "GPI" },
  ],
  last_synced_at: "2026-10-27T01:00:00Z",
  revenue: null,
};

describe("Dashboard Ads", () => {
  it("shows the KPIs with their change, the chart and the accounts", async () => {
    const stub = stubFetch([
      { match: "/api/ads/dashboard", body: DASHBOARD },
      { match: "/api/auth/session", body: SESSION },
    ]);
    renderWithQuery(<AdsDashboardPage />);
    const spend = await screen.findByTestId("kpi-spend");
    expect(spend).toHaveTextContent("312.500.000 ₫");
    expect(spend).toHaveTextContent("▲ 5,1% so kỳ trước");
    // A cost going down is good news.
    expect(
      within(screen.getByTestId("kpi-cost_per_phone")).getByText(/▼ 4,2%/),
    ).toHaveClass("text-[var(--good)]");
    expect(screen.getByTestId("kpi-phones")).toHaveTextContent(
      "24,0% mess ra số",
    );
    expect(screen.getByTestId("kpi-revenue")).toHaveTextContent("Chưa nhập");
    const line = screen.getByTestId("ads-line-111");
    expect(within(line).getByText("Chưa khớp")).toBeInTheDocument();
    expect(within(line).getByText("•••• 4037")).toBeInTheDocument();
    expect(within(line).getByText("105,1%")).toBeInTheDocument();
    // Hotline per marketer is not entered yet: the card says so.
    expect(screen.getByTestId("kpi-hotline")).toHaveTextContent(
      "Chưa có dữ liệu",
    );
    expect(
      screen.getByRole("img", { name: "Chi tiêu và doanh số theo thời gian" }),
    ).toBeInTheDocument();
    // Spend by day: one column per day, in millions.
    await userEvent.click(
      screen.getByRole("tab", { name: "Chi tiêu theo ngày" }),
    );
    // One account: its cell and the day's total.
    expect(screen.getAllByText("31,2")).toHaveLength(2);
    expect(screen.getByText("TỔNG NGÀY")).toBeInTheDocument();
    // The table view of the chart.
    await userEvent.click(screen.getByRole("button", { name: "Xem bảng" }));
    expect(
      screen.getByRole("button", { name: "Xem biểu đồ" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Ads / DT" }),
    ).toBeInTheDocument();
    expect(screen.getByText("22.000.000 ₫")).toBeInTheDocument();
    // Picking a person narrows the query.
    await userEvent.selectOptions(screen.getByLabelText("Người"), "u-1");
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) =>
            call.url.includes("/api/ads/dashboard") &&
            call.url.includes("user_id=u-1"),
        ),
      ).toBe(true),
    );
  });
});

const ACCOUNTS = (canManage: boolean) => ({
  month: "2026-10",
  accounts: [
    {
      account_id: "111",
      name: "[COREMIND] TK 1",
      connection_id: "bm-1",
      connection_name: "BM Apex",
      site: "Sài Gòn",
      status: 1,
      active: true,
      brand: "COREMIND",
      brand_label: "COREMIND",
      user_id: null,
      user_name: null,
      spend: 3_000_000,
      charged: 1_000_000,
      last_seen_at: "2026-10-27T01:00:00Z",
    },
  ],
  people: canManage
    ? [{ user_id: "u-1", name: "Mạnh Ngọc", member_code: "NGOC01" }]
    : [],
  brands: [
    { value: "COREMIND", label: "COREMIND" },
    { value: "GPI", label: "GPI" },
  ],
  can_manage: canManage,
});

describe("Tài khoản QC", () => {
  it("a Trưởng phòng gives an account to a marketer", async () => {
    const stub = stubFetch([
      {
        match: "/api/ads/accounts/111",
        method: "PATCH",
        body: { ...ACCOUNTS(true).accounts[0], user_id: "u-1" },
      },
      { match: "/api/ads/accounts", method: "GET", body: ACCOUNTS(true) },
    ]);
    renderWithQuery(<AdsAccountsPage />);
    const row = await screen.findByTestId("ads-account-111");
    expect(screen.getByRole("status")).toHaveTextContent(
      "1 tài khoản chưa gán",
    );
    await userEvent.selectOptions(
      within(row).getByLabelText("Người phụ trách [COREMIND] TK 1"),
      "u-1",
    );
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/ads/accounts/111")).toHaveLength(1),
    );
    expect(sent(stub, "PATCH", "/api/ads/accounts/111")[0].body).toEqual({
      user_id: "u-1",
    });
  });

  it("a marketer only reads", async () => {
    stubFetch([
      { match: "/api/ads/accounts", method: "GET", body: ACCOUNTS(false) },
    ]);
    renderWithQuery(<AdsAccountsPage />);
    const row = await screen.findByTestId("ads-account-111");
    expect(within(row).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(row).getByText("Chưa gán")).toBeInTheDocument();
    expect(within(row).getByText("3.000.000 ₫")).toBeInTheDocument();
  });
});

describe("step 2: revenue, KPI, Dashboard tổng", () => {
  it("the dashboard leads with revenue and colours Ads / Doanh thu", async () => {
    stubFetch([
      {
        match: "/api/ads/dashboard",
        body: {
          ...DASHBOARD,
          revenue: 1_250_000_000,
          kpis: [
            kpi("revenue", "Doanh số", 1_250_000_000, 0.084),
            kpi("ads_ratio", "Ads / Doanh thu", 0.25, -0.03, true),
            ...DASHBOARD.kpis,
          ],
        },
      },
    ]);
    renderWithQuery(<AdsDashboardPage />);
    expect(await screen.findByTestId("kpi-revenue")).toHaveTextContent(
      "1.250.000.000 ₫",
    );
    const ratio = screen.getByTestId("kpi-ads_ratio");
    expect(ratio).toHaveTextContent("25,0%");
    expect(ratio).toHaveClass("border-[var(--good)]");
  });

  it("a brand filter says revenue cannot be split that way", async () => {
    stubFetch([
      {
        match: "/api/ads/dashboard",
        body: { ...DASHBOARD, revenue_note: "filtered" },
      },
    ]);
    renderWithQuery(<AdsDashboardPage />);
    expect(await screen.findByTestId("kpi-revenue")).toHaveTextContent(
      "Không tách được",
    );
  });

  it("the head types a day's revenue and saves only what changed", async () => {
    const day = {
      day: "2026-10-26",
      total: 4_000_000,
      lines: [
        {
          key: "u:u-1",
          label: "Mạnh Ngọc",
          user_id: "u-1",
          source: null,
          member_code: "NGOC01",
          amount: 4_000_000,
          amount_hn: null,
        },
        {
          key: "s:HOTLINE",
          label: "◆ Hotline",
          user_id: null,
          source: "HOTLINE",
          member_code: null,
          amount: null,
        },
      ],
    };
    const stub = stubFetch([
      {
        match: "/api/ads/revenue",
        method: "PUT",
        body: { ...day, total: 5_500_000 },
      },
      { match: "/api/ads/revenue", method: "GET", body: day },
    ]);
    renderWithQuery(<AdsRevenuePage />);
    const hotline = await screen.findByLabelText("Doanh thu ◆ Hotline");
    expect(screen.getByLabelText("Doanh số SG Mạnh Ngọc")).toHaveValue(
      "4.000.000",
    );
    // A ◆ source has no HN part.
    expect(
      screen.queryByLabelText("Doanh số HN ◆ Hotline"),
    ).not.toBeInTheDocument();
    await userEvent.type(hotline, "1500000");
    expect(hotline).toHaveValue("1.500.000");
    await userEvent.type(
      screen.getByLabelText("Doanh số HN Mạnh Ngọc"),
      "800000",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu (2 dòng thay đổi)" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lưu doanh thu" }),
    );
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/ads/revenue")).toHaveLength(1),
    );
    expect(sent(stub, "PUT", "/api/ads/revenue")[0].body).toEqual({
      day: "2026-10-26",
      entries: { "s:HOTLINE": "1500000", "u:u-1:hn": "800000" },
    });
  });

  it("KPIs are read-only without the right", async () => {
    stubFetch([
      {
        match: "/api/ads/kpi",
        body: {
          month: "2026-10",
          can_edit: false,
          lines: [
            {
              key: "team",
              label: "Cả phòng",
              user_id: null,
              source: null,
              member_code: null,
              amount: 20_000_000,
            },
          ],
        },
      },
    ]);
    renderWithQuery(<AdsKpiPage />);
    expect(await screen.findByLabelText("KPI Cả phòng")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /Lưu KPI/ }),
    ).not.toBeInTheDocument();
  });

  it("Dashboard tổng: revenue split, KPI progress, Facebook ranking, the table", async () => {
    const figures = (over: Record<string, unknown>) => ({
      spend: 0,
      invoice: 0,
      phones: 0,
      revenue: null,
      revenue_net: null,
      cost_per_phone: null,
      ads_ratio: null,
      ads_ratio_real: null,
      ...over,
    });
    const stub = stubFetch([
      {
        match: "/api/ads/team",
        body: {
          month: "2026-10-01",
          start: "2026-10-01",
          end: "2026-10-31",
          site: "all",
          current: figures({
            spend: 3_500_000,
            invoice: 3_600_000,
            revenue: 14_000_000,
            revenue_net: 12_962_963,
            ads_ratio: 0.25,
            ads_ratio_real: 0.257,
            phones: 15,
          }),
          before: figures({
            spend: 3_000_000,
            revenue: 10_000_000,
            ads_ratio: 0.3,
          }),
          revenue_hcm: 12_000_000,
          revenue_hn_transfer: 2_000_000,
          marketers_hn: 1_000_000,
          team_kpi: 20_000_000,
          team_kpi_rate: 0.7,
          month_progress: 0.84,
          sources: [
            { key: "sg", label: "Doanh số SG", revenue: 10_000_000 },
            { key: "s:HN", label: "Doanh số chuyển miền", revenue: 2_000_000 },
            { key: "s:HOTLINE", label: "Hotline", revenue: 2_000_000 },
          ],
          marketers: [
            {
              user_id: "u-1",
              name: "Mạnh Ngọc",
              member_code: "NGOC01",
              figures: figures({
                spend: 3_000_000,
                revenue: 12_000_000,
                revenue_net: 12_000_000,
                ads_ratio: 0.25,
                phones: 15,
              }),
              kpi: 10_000_000,
              kpi_rate: 1.2,
              ads_ratio: 0.25,
              tone: "good",
            },
            {
              user_id: "u-2",
              name: "Hoàng Anh",
              member_code: null,
              figures: figures({ spend: 500_000 }),
              kpi: null,
              kpi_rate: null,
              ads_ratio: null,
              tone: null,
            },
          ],
          facebook: figures({
            spend: 3_500_000,
            revenue: 12_000_000,
            revenue_net: 12_000_000,
            ads_ratio: 0.29,
            phones: 15,
          }),
          attention: [
            {
              user_id: "u-2",
              name: "Hoàng Anh",
              kind: "no_revenue",
              spend: 500_000,
              revenue: null,
              ads_ratio: null,
              phones: 0,
            },
          ],
          months: [
            {
              month: "2026-10-01",
              figures: figures({ spend: 3_500_000, revenue: 14_000_000 }),
              kpi: 20_000_000,
              kpi_rate: 0.7,
            },
          ],
          days: [{ day: "2026-10-26", figures: figures({ spend: 1_234_567 }) }],
        },
      },
    ]);
    renderWithQuery(<AdsTeamPage />);
    const revenue = await screen.findByTestId("team-revenue");
    expect(revenue).toHaveTextContent("14.000.000 ₫");
    expect(revenue).toHaveTextContent("HN chuyển HCM 2.000.000 ₫");
    expect(revenue).toHaveTextContent("DT hoa hồng 12.962.963 ₫");
    // A ratio's change is in points, and down is good.
    expect(screen.getByTestId("team-ratio")).toHaveTextContent(
      "▼ 5 điểm so tháng trước",
    );
    expect(screen.getByTestId("team-kpi")).toHaveTextContent(
      "đang chậm tiến độ",
    );
    expect(screen.getByTestId("team-sources")).toHaveTextContent(
      "Doanh số chuyển miền",
    );
    expect(screen.getByTestId("alert-u-2")).toHaveTextContent(
      "chưa có doanh thu",
    );
    const row = screen.getByTestId("marketer-u-1");
    expect(within(row).getByText("25,0%")).toHaveClass("text-[var(--good)]");
    expect(within(row).getByText(/120,0%/)).toBeInTheDocument();
    expect(
      within(row).getByRole("link", { name: "Mạnh Ngọc" }),
    ).toHaveAttribute("href", "/ads?user_id=u-1");
    expect(screen.getByText("TỔNG FACEBOOK ADS")).toBeInTheDocument();
    expect(screen.getByTestId("month-2026-10")).toHaveTextContent("T10/2026");
    expect(screen.getByText("1.234.567 ₫")).toBeInTheDocument();
    // The cơ sở filter narrows the query.
    await userEvent.selectOptions(screen.getByLabelText("Cơ sở"), "hn");
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) =>
            call.url.includes("/api/ads/team") && call.url.includes("site=hn"),
        ),
      ).toBe(true),
    );
  });
});
