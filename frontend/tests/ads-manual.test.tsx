/**
 * Phòng Ads (0081): Google / TikTok Ads typed by hand - the "Nền tảng"
 * filter, adding an ad account, typing a day's spend and invoice, the
 * per-day tables, "Lịch sử sửa", and the team dashboard on one platform.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdsDashboardPage from "@/app/ads/page";
import { vnToday } from "@/components/ads-manual";
import { renderWithQuery, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/ads",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter(
    (call) => call.method === method && call.url.includes(fragment),
  );

const kpi = (key: string, label: string, value: number | null) => ({
  key,
  label,
  value,
  previous: null,
  change: null,
  lower_is_better: false,
});

const figures = (over: Record<string, unknown> = {}) => ({
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

const line = (over: Record<string, unknown>) => ({
  connection_name: "Nhập tay",
  site: "Sài Gòn",
  brand: "APEX",
  brand_label: "APEX",
  user_id: "u-1",
  user_name: "Mạnh Ngọc",
  messages: 0,
  phones: 0,
  charged: 0,
  difference: 0,
  matched: true,
  platform: "GOOGLE",
  ...over,
});

const FACEBOOK = {
  start: "2026-10-25",
  end: "2026-10-26",
  previous_start: "2026-10-23",
  previous_end: "2026-10-24",
  grouping: "day",
  user_id: "u-1",
  kpis: [kpi("spend", "Tiền chi tiêu", 5_000_000)],
  series: [],
  best_day: null,
  review_day: null,
  accounts: [],
  people: [],
  connections: [{ id: "bm-1", name: "BM Apex", site: "Sài Gòn" }],
  sites: ["Sài Gòn", "Hà Nội"],
  brands: [],
  last_synced_at: null,
  revenue: null,
  days: ["2026-10-25", "2026-10-26"],
  platform: "FACEBOOK",
};

const GOOGLE = {
  ...FACEBOOK,
  kpis: [
    kpi("revenue", "Doanh số (xét KPI)", null),
    kpi("spend", "Tiền chi tiêu", 2_000_000),
    kpi("invoice", "Số tiền lập hoá đơn", 2_100_000),
    kpi("messages", "Số mess", 0),
  ],
  connections: [],
  revenue_note: "platform",
  platform: "GOOGLE",
  can_add_account: true,
  last_synced_at: "2026-10-27T03:00:00Z",
  accounts: [
    line({
      account_id: "google:1234567890",
      external_id: "123-456-7890",
      name: "GG Apex 01",
      spend: 2_000_000,
      invoice: 2_100_000,
      can_edit: true,
      spend_by_day: [2_000_000, 0],
      invoice_by_day: [2_100_000, 0],
      spend_entered: [true, false],
      invoice_entered: [true, false],
    }),
    line({
      account_id: "google:999",
      external_id: "999",
      name: "GG của người khác",
      user_id: "u-2",
      user_name: "Hoàng Anh",
      spend: 500_000,
      invoice: 0,
      can_edit: false,
      spend_by_day: [500_000, 0],
      invoice_by_day: [0, 0],
      spend_entered: [true, false],
      invoice_entered: [false, false],
    }),
  ],
};

const HISTORY = {
  month: "2026-10",
  start: "2026-10-01",
  end: "2026-10-31",
  user_id: "u-1",
  months: [
    {
      month: "2026-10-01",
      figures: figures({ spend: 2_500_000, invoice: 2_100_000 }),
      kpi: null,
      kpi_rate: null,
    },
  ],
  days: [
    {
      day: "2026-10-25",
      figures: figures({ spend: 2_500_000, invoice: 2_100_000 }),
    },
  ],
  revenue_note: "platform",
  platform: "GOOGLE",
};

const MEMBER = [
  { match: "/api/units/me", body: { ads: { view: true, view_all: false } } },
  { match: "/api/ads/history", body: HISTORY },
  { match: "platform=GOOGLE", body: GOOGLE },
  { match: "/api/ads/dashboard", body: FACEBOOK },
];

async function onGoogle() {
  renderWithQuery(<AdsDashboardPage />);
  await screen.findByTestId("kpi-spend");
  await userEvent.selectOptions(
    screen.getByLabelText("Nền tảng"),
    "GOOGLE",
  );
  await screen.findByText("+ Thêm tài khoản Google Ads");
}

describe("Google / TikTok Ads typed by hand", () => {
  it("vnToday is the Vietnam date", () => {
    expect(vnToday(new Date("2026-10-27T18:30:00Z"))).toBe("2026-10-28");
  });

  it("the platform filter reads Google, and its revenue waits for the ◆ line", async () => {
    const stub = stubFetch(MEMBER);
    await onGoogle();
    expect(sent(stub, "GET", "platform=GOOGLE").length).toBeGreaterThan(0);
    expect(screen.getByTestId("kpi-revenue")).toHaveTextContent(
      "dòng ◆ của nguồn",
    );
    expect(screen.getByTestId("kpi-invoice")).toHaveTextContent("Nhập tay");
    expect(screen.getByTestId("kpi-manual-leads")).toBeInTheDocument();
    expect(screen.queryByTestId("kpi-messages")).toBeNull();
    // No BM for Google; the reports follow the platform.
    expect(screen.queryByLabelText("BM")).toBeNull();
    await waitFor(() =>
      expect(sent(stub, "GET", "/api/ads/history")).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            url: expect.stringContaining("platform=GOOGLE"),
          }),
        ]),
      ),
    );
    expect(screen.getByText(/nhập tay lần cuối lúc/)).toBeInTheDocument();
    const row = screen.getByTestId("ads-line-google:1234567890");
    expect(row).toHaveTextContent("123-456-7890");
    expect(
      within(row).getByRole("button", { name: "Sửa tài khoản GG Apex 01" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("ads-line-google:999")).queryByRole("button"),
    ).toBeNull();
  });

  it("a marketer adds their Google account", async () => {
    const stub = stubFetch([
      {
        match: "/api/ads/manual/accounts",
        method: "POST",
        body: { account_id: "google:42" },
      },
      ...MEMBER,
    ]);
    await onGoogle();
    await userEvent.click(screen.getByText("+ Thêm tài khoản Google Ads"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText("ID tài khoản"),
      "111-222-3333",
    );
    await userEvent.type(
      within(dialog).getByLabelText("Tên tài khoản"),
      "GG Apex 02",
    );
    await userEvent.type(within(dialog).getByLabelText("4 số cuối thẻ"), "12a34");
    // A member adds for themselves: no "Người chạy".
    expect(within(dialog).queryByLabelText("Người chạy")).toBeNull();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Thêm tài khoản" }),
    );
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/ads/manual/accounts")).toHaveLength(1),
    );
    expect(sent(stub, "POST", "/api/ads/manual/accounts")[0].body).toEqual({
      platform: "GOOGLE",
      external_id: "111-222-3333",
      name: "GG Apex 02",
      card_last4: "1234",
    });
  });

  it("types one day's spend and invoice, confirmed, only the changed cells", async () => {
    const stub = stubFetch([
      { match: "/api/ads/manual/days", method: "PUT", body: { changed: 2 } },
      ...MEMBER,
    ]);
    await onGoogle();
    await userEvent.click(screen.getByRole("tab", { name: "Nhập theo ngày" }));
    const form = screen.getByTestId("manual-day-form");
    // Only the accounts the viewer types; the latest day first.
    expect(form).not.toHaveTextContent("GG của người khác");
    expect(within(form).getByLabelText("Ngày nhập")).toHaveValue("2026-10-26");
    const spend = within(form).getByLabelText(
      "Chi tiêu GG Apex 01 ngày 26/10",
    );
    await userEvent.type(spend, "1500000");
    expect(spend).toHaveValue("1.500.000");
    await userEvent.type(
      within(form).getByLabelText("Lập hoá đơn GG Apex 01 ngày 26/10"),
      "1600000",
    );
    await userEvent.click(
      within(form).getByRole("button", { name: "Lưu (2 ô thay đổi)" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/ads/manual/days")).toHaveLength(1),
    );
    expect(sent(stub, "PUT", "/api/ads/manual/days")[0].body).toEqual({
      entries: [
        {
          account_id: "google:1234567890",
          day: "2026-10-26",
          field: "spend",
          amount: "1500000",
        },
        {
          account_id: "google:1234567890",
          day: "2026-10-26",
          field: "invoice",
          amount: "1600000",
        },
      ],
    });
  });

  it("the per-day table: the viewer's cells are fields, a blank clears one", async () => {
    const stub = stubFetch([
      { match: "/api/ads/manual/days", method: "PUT", body: { changed: 1 } },
      ...MEMBER,
    ]);
    await onGoogle();
    await userEvent.click(
      screen.getByRole("tab", { name: "Chi tiêu theo ngày" }),
    );
    const table = screen.getByTestId("manual-table-spend");
    const cell = within(table).getByLabelText(
      "Chi tiêu GG Apex 01 ngày 25/10",
    );
    expect(cell).toHaveValue("2.000.000");
    // Somebody else's account is shown, not typed.
    expect(
      within(table).queryByLabelText("Chi tiêu GG của người khác ngày 25/10"),
    ).toBeNull();
    expect(table).toHaveTextContent("500.000");
    await userEvent.clear(cell);
    await userEvent.click(
      within(table).getByRole("button", { name: "Lưu (1 ô thay đổi)" }),
    );
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Lưu",
      }),
    );
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/ads/manual/days")).toHaveLength(1),
    );
    expect(sent(stub, "PUT", "/api/ads/manual/days")[0].body).toEqual({
      entries: [
        {
          account_id: "google:1234567890",
          day: "2026-10-25",
          field: "spend",
          amount: null,
        },
      ],
    });
  });

  it("Lịch sử sửa lists who changed what", async () => {
    stubFetch([
      {
        match: "/api/ads/manual/changes",
        body: {
          start: "2026-10-25",
          end: "2026-10-26",
          changes: [
            {
              id: "c-1",
              day: "2026-10-25",
              account_id: "google:1234567890",
              account_name: "GG Apex 01",
              external_id: "123-456-7890",
              platform: "GOOGLE",
              field: "spend",
              old_amount: null,
              new_amount: 2_000_000,
              changed_by: "Mạnh Ngọc",
              changed_at: "2026-10-26T02:00:00Z",
            },
          ],
        },
      },
      ...MEMBER,
    ]);
    await onGoogle();
    await userEvent.click(screen.getByRole("button", { name: "Lịch sử sửa" }));
    const row = await screen.findByTestId("manual-change-c-1");
    expect(row).toHaveTextContent("26/10/2026 09:00");
    expect(row).toHaveTextContent("Mạnh Ngọc");
    expect(row).toHaveTextContent("25/10/2026 · Chi tiêu");
    expect(row).toHaveTextContent("chưa nhập → 2.000.000 ₫");
  });

  it("the team dashboard on Google: its ◆ revenue, no KPI, its own total", async () => {
    const google = {
      month: "2026-10",
      start: "2026-10-01",
      end: "2026-10-31",
      site: "all",
      current: figures({
        spend: 2_000_000,
        invoice: 2_100_000,
        revenue: 5_400_000,
        revenue_net: 5_000_000,
        ads_ratio: 0.42,
      }),
      before: figures(),
      revenue_hcm: 5_400_000,
      revenue_hn_transfer: null,
      marketers_hn: null,
      team_kpi: null,
      team_kpi_rate: null,
      month_progress: 0.9,
      sources: [],
      marketers: [
        {
          user_id: "u-1",
          name: "Mạnh Ngọc",
          member_code: null,
          figures: figures({ spend: 2_000_000, invoice: 2_100_000 }),
          kpi: null,
          kpi_rate: null,
          ads_ratio: null,
          tone: null,
        },
      ],
      facebook: figures(),
      attention: [],
      connections: [],
      revenue_note: "platform",
      platform: "GOOGLE",
      channels: [
        {
          platform: "GOOGLE",
          label: "Google Ads",
          manual: true,
          figures: figures({
            spend: 2_000_000,
            invoice: 2_100_000,
            revenue: 5_400_000,
            revenue_net: 5_000_000,
          }),
          people: [],
          accounts: 1,
        },
      ],
    };
    const stub = stubFetch([
      { match: "/api/units/me", body: { ads: { view: true, view_all: true } } },
      { match: "/api/ads/history", body: HISTORY },
      { match: "platform=GOOGLE", body: google },
      {
        match: "/api/ads/team",
        body: { ...google, platform: "FACEBOOK", revenue_note: null },
      },
    ]);
    renderWithQuery(<AdsDashboardPage />);
    await screen.findByTestId("team-revenue");
    await userEvent.selectOptions(
      screen.getByLabelText("Nền tảng"),
      "GOOGLE",
    );
    await waitFor(() =>
      expect(screen.getByTestId("team-revenue")).toHaveTextContent(
        "Doanh thu Google Ads",
      ),
    );
    expect(sent(stub, "GET", "/api/ads/team?").at(-1)?.url).toContain(
      "platform=GOOGLE",
    );
    expect(screen.getByTestId("team-revenue")).toHaveTextContent(
      "5.400.000 ₫",
    );
    expect(screen.getByTestId("team-kpi")).toHaveTextContent(
      "Không theo nền tảng",
    );
    expect(screen.getByText("TỔNG GOOGLE ADS")).toBeInTheDocument();
    expect(screen.queryByLabelText("BM")).toBeNull();
  });
});
