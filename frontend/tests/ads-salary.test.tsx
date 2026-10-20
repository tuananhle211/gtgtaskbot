/**
 * "Tính lương Ads" (2026-10-20): the fixed Ads MKT pay formulas - the same
 * numbers the department's own MayTinhLuong_Ads.html gives - the menu entry
 * for ADS seats only, and the page itself.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdsSalaryPage from "@/app/tasks/salary/page";
import { navFor } from "@/components/shell";
import {
  accumulatedBonus,
  adsCost,
  attendanceFactor,
  commissionRate,
  costZone,
  COST_ZONES,
  formatMoney,
  gameBonus,
  monthlySalary,
  seesAdsSalary,
} from "@/lib/ads-salary";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/tasks/salary",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

const REGION = { revenue: 1_500_000_000, kpi: 100, target: 45, cost: 44 };

describe("the formulas", () => {
  it("gives the calculator's default month: 48,2tr", () => {
    const result = monthlySalary({ grade: 1, days: 23, bills: [3, 0, 0, 0], regions: [REGION, REGION] });
    expect(result.base).toBe(10_500_000);
    expect(result.allowance).toBe(805_000);
    expect(result.game).toBe(900_000);
    expect(result.regions[0].onCost).toBeCloseTo(14_400_000);
    expect(result.regions[0].onSales).toBeCloseTo(3_600_000);
    expect(result.total).toBeCloseTo(48_205_000);
    expect(formatMoney(result.total)).toBe("48,2tr");
  });

  it("pays in full from 90% attendance and in proportion below, never on the 20% part", () => {
    expect(attendanceFactor(21)).toBe(1);
    expect(attendanceFactor(20)).toBeCloseTo(20 / 23);
    const short = monthlySalary({ grade: 3, days: 20, bills: [0, 0, 0, 0], regions: [REGION] });
    expect(short.base).toBeCloseTo((8_000_000 * 20) / 23);
    expect(short.regions[0].onSales).toBeCloseTo(3_600_000);
    expect(short.regions[0].onCost).toBeCloseTo(14_400_000 * (20 / 23));
  });

  it("reads the KPI steps and the cost zones", () => {
    expect([69, 70, 80, 90, 100, 110, 120].map(commissionRate)).toEqual([
      0.005, 0.007, 0.009, 0.011, 0.012, 0.013, 0.014,
    ]);
    const zone = (cost: number) => COST_ZONES[costZone(cost, 45)].factor;
    expect([40, 43, 47, 50, 55, 56].map(zone)).toEqual([1.2, 1.1, 1, 0.8, 0.7, 0.5]);
  });

  it("adds the game, the quarter and year bonuses, and the cost with fees", () => {
    expect(gameBonus([5, 0, 0, 0], [1, 1, 1]).total).toBe(1_500_000 + 1_750_000);
    expect(accumulatedBonus(6_000_000_000, 0.1)).toBe(6_000_000);
    expect(accumulatedBonus(24_000_000_000, 0.05)).toBe(12_000_000);
    const cost = adsCost(200_000_000, 150_000_000, 800_000_000);
    expect(cost.total).toBeCloseTo(370_650_000);
    expect(cost.percent).toBeCloseTo(46.33, 2);
  });
});

const ME = (codes: string[], seesAll = false) =>
  ({
    units: [],
    default_unit: null,
    can_view_all: seesAll,
    can_admin: [],
    departments: codes.map((code) => ({
      department_id: `d-${code}`,
      code,
      name: code,
      position: "MEMBER",
    })),
  }) as unknown as UnitsMe;

describe("who sees it", () => {
  it("is for ADS seats and the OWNER / ADMIN only", () => {
    expect(seesAdsSalary(ME(["ADS"]))).toBe(true);
    expect(seesAdsSalary(ME(["BT", "ADS"]))).toBe(true);
    expect(seesAdsSalary(ME(["BT"]))).toBe(false);
    expect(seesAdsSalary(ME([]), "OWNER")).toBe(true);
    expect(seesAdsSalary(ME([], true))).toBe(true);
    expect(seesAdsSalary(undefined)).toBe(false);
  });

  it('puts "Tính lương Ads" in "Bảng order" for them', () => {
    const hrefs = (me: UnitsMe, role = "EMPLOYEE") =>
      navFor({ ...me, units: [{ code: "ADS" }] } as unknown as UnitsMe, role).flatMap((entry) =>
        "children" in entry ? entry.children.map((child) => child.href) : [entry.href],
      );
    expect(hrefs(ME(["ADS"]))).toContain("/tasks/salary");
    expect(hrefs(ME(["TK"]))).not.toContain("/tasks/salary");
  });
});

describe("the page", () => {
  const open = (me: UnitsMe, role = "EMPLOYEE") => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role } },
      { match: "/api/units/me", body: me },
    ]);
    renderWithQuery(<AdsSalaryPage />);
  };

  it("estimates the month and follows the sliders", async () => {
    open(ME(["ADS"]));
    expect(await screen.findByRole("heading", { level: 1, name: "Tính lương Ads" })).toBeInTheDocument();
    expect(screen.getByText("48,2tr")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Bậc 3/ }));
    expect(screen.getByText("45,7tr")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Ngày công thực tế / tháng"), { target: { value: "20" } });
    expect(screen.queryByText("45,7tr")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Máy tính cost" }));
    expect(screen.getByText("46,33%")).toBeInTheDocument();
  });

  it("refuses somebody outside ADS", async () => {
    open(ME(["TK"]));
    expect(await screen.findByRole("alert")).toHaveTextContent("dành cho nhân sự phòng ADS");
    expect(screen.queryByText("48,2tr")).not.toBeInTheDocument();
  });
});
