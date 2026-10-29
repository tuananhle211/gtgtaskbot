/**
 * Phòng Ads (0082): Ngân sách chạy ads theo tuần - the Trưởng phòng types
 * four weekly budgets per marketer, and the table colours each week by
 * where the spend stands ("Vượt ngưỡng" red, "Chưa đến ngưỡng" amber).
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdsBudgetPage from "@/app/ads/budget/page";
import { BudgetBoard } from "@/components/ads-budget";
import type { AdsBudgetMonth } from "@/lib/api";
import { renderWithQuery, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/ads/budget",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter(
    (call) => call.method === method && call.url.includes(fragment),
  );

const WEEKS = [
  { index: 1, start: "2026-10-01", end: "2026-10-07", label: "Tuần 1" },
  { index: 2, start: "2026-10-08", end: "2026-10-14", label: "Tuần 2" },
  { index: 3, start: "2026-10-15", end: "2026-10-21", label: "Tuần 3" },
  { index: 4, start: "2026-10-22", end: "2026-10-31", label: "Tuần 4" },
];

const LABELS = {
  OVER: "Vượt ngưỡng",
  REACHED: "Đạt ngưỡng",
  UNDER: "Chưa đến ngưỡng",
  RUNNING: "Đang chạy",
  UPCOMING: "Chưa tới tuần",
  NO_BUDGET: "Chưa đặt ngân sách",
} as const;

type Status = keyof typeof LABELS;

const cell = (
  week: number,
  budget: number | null,
  spend: number,
  status: Status,
) => ({
  week,
  budget,
  spend,
  rate: budget ? spend / budget : null,
  status,
  status_label: LABELS[status],
});

const MONTH: AdsBudgetMonth = {
  month: "2026-10",
  today: "2026-10-25",
  weeks: WEEKS,
  can_edit: true,
  lines: [
    {
      user_id: "u-1",
      name: "Mạnh Ngọc",
      member_code: "NGOC",
      cells: [
        cell(1, 5_000_000, 4_800_000, "REACHED"),
        cell(2, 5_000_000, 6_000_000, "OVER"),
        cell(3, 5_000_000, 2_000_000, "UNDER"),
        cell(4, 6_000_000, 1_000_000, "RUNNING"),
      ],
      budget: 21_000_000,
      spend: 13_800_000,
    },
    {
      user_id: "u-2",
      name: "Hoàng Anh",
      member_code: null,
      cells: [
        cell(1, null, 300_000, "NO_BUDGET"),
        cell(2, null, 0, "NO_BUDGET"),
        cell(3, null, 0, "NO_BUDGET"),
        cell(4, null, 0, "NO_BUDGET"),
      ],
      budget: null,
      spend: 300_000,
    },
  ],
  total: {
    user_id: null,
    name: "Cả phòng",
    member_code: null,
    cells: [
      cell(1, 5_000_000, 5_100_000, "OVER"),
      cell(2, 5_000_000, 6_000_000, "OVER"),
      cell(3, 5_000_000, 2_000_000, "UNDER"),
      cell(4, 6_000_000, 1_000_000, "RUNNING"),
    ],
    budget: 21_000_000,
    spend: 14_100_000,
  },
};

describe("Ngân sách tuần", () => {
  it("the table colours each week by its status", async () => {
    stubFetch([{ match: "/api/ads/budget", body: MONTH }]);
    const picked: string[] = [];
    renderWithQuery(
      <BudgetBoard month="2026-10" onPick={(id) => picked.push(id)} />,
    );
    const row = await screen.findByTestId("budget-u-1");
    const cells = within(row).getAllByRole("cell");
    expect(cells.map((item) => item.dataset.status)).toEqual([
      "REACHED",
      "OVER",
      "UNDER",
      "RUNNING",
      undefined,
    ]);
    expect(cells[1]).toHaveClass("bg-[var(--bad-soft)]");
    expect(cells[2]).toHaveClass("bg-[var(--warn-soft)]");
    expect(within(cells[1]).getByText("Vượt ngưỡng")).toHaveClass("st-red");
    expect(within(cells[2]).getByText("Chưa đến ngưỡng")).toHaveClass(
      "st-amber",
    );
    expect(within(cells[1]).getByText("120,0%")).toBeInTheDocument();
    // The week's days are spelled out, the 4th to the month's end.
    expect(screen.getByText("22/10 – 31/10")).toBeInTheDocument();
    expect(within(screen.getByTestId("budget-total")).getByText("Cả phòng"));
    expect(screen.getByRole("link", { name: "Đặt ngân sách tuần" })).toHaveAttribute(
      "href",
      "/ads/budget?month=2026-10",
    );
    await userEvent.click(screen.getByRole("button", { name: "Hoàng Anh" }));
    expect(picked).toEqual(["u-2"]);
  });

  it("one person gets four cards", async () => {
    const one = { ...MONTH, lines: [MONTH.lines[0]], total: null, can_edit: false };
    const stub = stubFetch([{ match: "/api/ads/budget", body: one }]);
    renderWithQuery(<BudgetBoard month="2026-10" userId="u-1" />);
    const week2 = await screen.findByRole("article", { name: "Tuần 2" });
    expect(week2).toHaveAttribute("data-status", "OVER");
    expect(within(week2).getByText("6.000.000 ₫")).toBeInTheDocument();
    expect(sent(stub, "GET", "/api/ads/budget")[0].url).toContain("user_id=u-1");
    expect(
      screen.queryByRole("link", { name: "Đặt ngân sách tuần" }),
    ).not.toBeInTheDocument();
  });

  it("the head types the four weeks and saves the changed cells", async () => {
    const stub = stubFetch([
      { match: "/api/ads/budget", method: "PUT", body: MONTH },
      { match: "/api/ads/budget", method: "GET", body: MONTH },
    ]);
    renderWithQuery(<AdsBudgetPage />);
    const week4 = await screen.findByLabelText("Ngân sách Mạnh Ngọc tuần 4");
    expect(week4).toHaveValue("6.000.000");
    const anh = screen.getByLabelText("Ngân sách Hoàng Anh tuần 1");
    await userEvent.type(anh, "7000000");
    expect(anh).toHaveValue("7.000.000");
    await userEvent.clear(week4);
    const entry = screen.getByTestId("budget-entry-u-1");
    expect(within(entry).getByText("15.000.000 ₫")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu ngân sách (2 ô thay đổi)" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lưu ngân sách" }),
    );
    await waitFor(() =>
      expect(sent(stub, "PUT", "/api/ads/budget")).toHaveLength(1),
    );
    expect(sent(stub, "PUT", "/api/ads/budget")[0].body).toEqual({
      month: "2026-10",
      entries: { "u-1:4": null, "u-2:1": "7000000" },
    });
  });
});
