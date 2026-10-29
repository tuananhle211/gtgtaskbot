/**
 * Phòng Ads (0083): thực thu from the CRM - "Tài khoản CRM" (whose each CRM
 * account is, its region, the workbook import, "Đồng bộ ngay") and the
 * revenue entry page locking the lines the CRM fills.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdsCrmPage from "@/app/ads/crm/page";
import AdsRevenuePage from "@/app/ads/revenue/page";
import type { AdsCrmAccount, AdsCrmOverview } from "@/lib/api";
import { renderWithQuery, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/ads/crm",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter(
    (call) => call.method === method && call.url.includes(fragment),
  );

const account = (over: Partial<AdsCrmAccount>): AdsCrmAccount => ({
  crm_id: 1,
  name: "SG - MKT MẠNH NGỌC",
  active: true,
  in_directory: true,
  region: "HCM",
  region_label: "HCM",
  user_id: null,
  user_name: null,
  member_code: null,
  source: null,
  source_label: null,
  note: null,
  payments: 0,
  total: 0,
  ...over,
});

const OVERVIEW: AdsCrmOverview = {
  month: "2026-10",
  configured: true,
  synced_from: "2026-10-01",
  synced_to: "2026-10-29",
  last_sync_at: "2026-10-29T08:10:00Z",
  last_status: "SUCCESS",
  last_message: "Đồng bộ CRM 01/10 - 29/10: 1362 khoản thu.",
  accounts: [
    account({
      crm_id: 10,
      user_id: "u-1",
      user_name: "Mạnh Ngọc",
      member_code: "M064NGOC",
      payments: 3,
      total: 5_000_000,
    }),
    account({
      crm_id: 11,
      name: "MKT - VƯƠNG ANH ĐỨC",
      region: null,
      region_label: null,
      user_id: "u-2",
      user_name: "Vương Anh Đức",
      payments: 1,
      total: 900_000,
    }),
    account({
      crm_id: 12,
      name: "Hotline FB",
      region: null,
      region_label: null,
      payments: 2,
      total: 1_300_000,
      in_directory: false,
    }),
  ],
  people: [
    { user_id: "u-1", name: "Mạnh Ngọc", member_code: "M064NGOC", in_ads: true },
    { user_id: "u-2", name: "Vương Anh Đức", member_code: "M046DUC", in_ads: true },
  ],
  sources: [
    { value: "HOTLINE", label: "Hotline" },
    { value: "GOOGLE", label: "Google" },
  ],
  regions: [
    { value: "HN", label: "Hà Nội" },
    { value: "HCM", label: "HCM" },
  ],
  mapped_total: 5_900_000,
  unassigned_total: 1_300_000,
  unassigned_accounts: 1,
};

describe("Tài khoản CRM", () => {
  it("lists the accounts and sends a pick at once", async () => {
    const stub = stubFetch([
      { match: "/api/ads/crm/accounts/12", method: "PATCH", body: OVERVIEW },
      { match: "/api/ads/crm", method: "GET", body: OVERVIEW },
    ]);
    renderWithQuery(<AdsCrmPage />);
    const row = await screen.findByTestId("crm-12");
    expect(within(row).getByText("Ngoài DS marketing")).toBeInTheDocument();
    expect(within(screen.getByTestId("crm-unassigned")).getByText("1.300.000 ₫"));
    // An account given to somebody without a region is flagged.
    expect(screen.getByLabelText("Khu vực MKT - VƯƠNG ANH ĐỨC")).toHaveClass(
      "border-[var(--warn)]",
    );
    expect(screen.getByText(/01\/10\/2026 – 29\/10\/2026/)).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByLabelText("Tính cho Hotline FB"),
      "s:HOTLINE",
    );
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/ads/crm/accounts/12")).toHaveLength(1),
    );
    expect(sent(stub, "PATCH", "/api/ads/crm/accounts/12")[0].body).toEqual({
      source: "HOTLINE",
      user_id: null,
    });
    await userEvent.selectOptions(
      screen.getByLabelText("Khu vực Hotline FB"),
      "HN",
    );
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/ads/crm/accounts/12")).toHaveLength(2),
    );
    expect(sent(stub, "PATCH", "/api/ads/crm/accounts/12")[1].body).toEqual({
      region: "HN",
    });
  });

  it("warns when the server has no key and filters the loose accounts", async () => {
    stubFetch([
      { match: "/api/ads/crm", body: { ...OVERVIEW, configured: false } },
    ]);
    renderWithQuery(<AdsCrmPage />);
    expect(await screen.findByText(/chưa có CRM_API_KEY/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đồng bộ ngay" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Hiện"), "loose");
    expect(screen.queryByTestId("crm-10")).not.toBeInTheDocument();
    expect(screen.getByTestId("crm-12")).toBeInTheDocument();
  });

  it("shows every problem of a refused workbook", async () => {
    stubFetch([
      {
        match: "/api/ads/crm/import",
        method: "POST",
        status: 422,
        body: {
          error: {
            code: "ads_crm_import_invalid",
            message: "File có 2 dòng lỗi, chưa lưu gì.",
            details: {
              problems: [
                "Gắn mã dòng 3: mã nhân viên “NOPE” không có trên APM.",
                "Gắn mã dòng 5: chỉ điền mã nhân viên HOẶC nguồn ◆, không cả hai.",
              ],
            },
          },
        },
      },
      { match: "/api/ads/crm", method: "GET", body: OVERVIEW },
    ]);
    renderWithQuery(<AdsCrmPage />);
    const input = await screen.findByLabelText("Chọn file gắn mã (.xlsx)");
    await userEvent.upload(
      input,
      new File(["xlsx"], "gan-ma.xlsx", {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }),
    );
    const button = screen.getByRole("button", { name: "Nhập từ file Excel" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Nhập file" }));
    const alert = await screen.findByText(/mã nhân viên “NOPE”/);
    expect(alert.closest("[role=alert]")).toHaveTextContent("chỉ điền mã nhân viên");
  });
});

describe("Nhập doanh thu with the CRM", () => {
  it("locks the lines the CRM fills and never sends them", async () => {
    const day = {
      day: "2026-10-20",
      total: 6_000_000,
      lines: [
        {
          key: "u:u-1",
          label: "Mạnh Ngọc",
          user_id: "u-1",
          source: null,
          member_code: "M064NGOC",
          amount: 5_000_000,
          amount_hn: 1_000_000,
          from_crm: true,
        },
        {
          key: "u:u-2",
          label: "Hoàng Anh",
          user_id: "u-2",
          source: null,
          member_code: null,
          amount: null,
          amount_hn: null,
        },
      ],
    };
    const stub = stubFetch([
      { match: "/api/ads/revenue", method: "PUT", body: day },
      { match: "/api/ads/revenue", method: "GET", body: day },
    ]);
    renderWithQuery(<AdsRevenuePage />);
    const locked = await screen.findByLabelText("Doanh số SG Mạnh Ngọc");
    expect(locked).toBeDisabled();
    expect(locked).toHaveValue("5.000.000");
    expect(screen.getByLabelText("Doanh số HN Mạnh Ngọc")).toBeDisabled();
    expect(screen.getByText("CRM", { selector: ".st" })).toBeInTheDocument();
    expect(screen.getByText(/lấy\s+thực thu tự động từ CRM/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Doanh số SG Hoàng Anh"), "2000000");
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu (1 dòng thay đổi)" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lưu doanh thu" }),
    );
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/ads/revenue")).toHaveLength(1),
    );
    expect(sent(stub, "PUT", "/api/ads/revenue")[0].body).toEqual({
      day: "2026-10-20",
      entries: { "u:u-2": "2000000" },
    });
  });
});
