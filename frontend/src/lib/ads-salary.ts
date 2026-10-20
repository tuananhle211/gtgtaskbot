/**
 * "Tính lương Ads" (2026-10-20): the Ads MKT pay formula, fixed in code from
 * the department's own calculator (MayTinhLuong_Ads.html). Nothing here is
 * stored or sent anywhere: a person drags their own figures and reads an
 * estimate. Changing a rate means changing this file.
 *
 * Monthly pay = P1+P2 (base by grade, scaled by attendance)
 *             + meal & travel allowance (per working day)
 *             + Game (big-bill bonuses)
 *             + P3 commission, per region:
 *                 80% on cost  = revenue × attendance × rate(KPI) × cost-zone factor
 *                 20% on sales = revenue × rate(KPI)   (no attendance, no zone)
 */

/** P1+P2 by grade ("Bậc"), in đồng. */
export const SALARY_BASE: Readonly<Record<1 | 2 | 3, number>> = {
  1: 10_500_000,
  2: 9_500_000,
  3: 8_000_000,
};
export type SalaryGrade = keyof typeof SALARY_BASE;

/** Working days of a full month (a Saturday morning counts 0,5). */
export const WORK_DAYS = 23;
/** Meal + travel allowance per working day. */
export const DAILY_ALLOWANCE = 35_000;

/** Big-bill bonuses (bills paid in full), by bill size. */
export const BILL_BONUSES: ReadonlyArray<{ label: string; amount: number; max: number }> = [
  { label: "100 – 200tr", amount: 300_000, max: 15 },
  { label: "200 – 300tr", amount: 500_000, max: 10 },
  { label: "300 – 500tr", amount: 1_000_000, max: 10 },
  { label: "≥ 500tr", amount: 2_000_000, max: 5 },
];

/** The weekly contest prizes, per week won. */
export const WEEK_PRIZES: ReadonlyArray<{ label: string; amount: number }> = [
  { label: "Tuần đội về Nhất", amount: 900_000 },
  { label: "Tuần đội về Nhì", amount: 500_000 },
  { label: "Tuần đạt Bill cao nhất cá nhân", amount: 350_000 },
];

/** The commission rate of the branch's revenue KPI (%), as a fraction. */
export function commissionRate(kpi: number): number {
  if (kpi >= 120) return 0.014;
  if (kpi >= 110) return 0.013;
  if (kpi >= 100) return 0.012;
  if (kpi >= 90) return 0.011;
  if (kpi >= 80) return 0.009;
  if (kpi >= 70) return 0.007;
  return 0.005;
}

/**
 * The cost zones: d = actual Ads cost % − the cost target X %. The zone's
 * factor scales the 80% "on cost" part of the commission.
 */
export const COST_ZONES: ReadonlyArray<{ name: string; factor: number; upTo: number; tone: "good" | "neutral" | "warn" | "bad" }> = [
  { name: "Tiết kiệm XS", factor: 1.2, upTo: -5, tone: "good" },
  { name: "Tiết kiệm tốt", factor: 1.1, upTo: -2, tone: "good" },
  { name: "Đúng khoán", factor: 1.0, upTo: 2, tone: "neutral" },
  { name: "Vượt chi nhẹ", factor: 0.8, upTo: 5, tone: "warn" },
  { name: "Vượt chi nhiều", factor: 0.7, upTo: 10, tone: "warn" },
  { name: "Vượt chi nặng", factor: 0.5, upTo: Number.POSITIVE_INFINITY, tone: "bad" },
];

export function costZone(costPercent: number, targetPercent: number): number {
  const d = costPercent - targetPercent;
  return COST_ZONES.findIndex((zone) => d <= zone.upTo);
}

/** ≥ 90% of the month's days = full pay; below, pay in proportion. */
export function attendanceFactor(days: number): number {
  const share = days / WORK_DAYS;
  return share >= 0.9 ? 1 : share;
}

export type RegionInput = {
  /** Revenue of the month, in đồng. */
  revenue: number;
  /** The branch's revenue KPI, %. */
  kpi: number;
  /** The cost target X, % of revenue. */
  target: number;
  /** The actual Ads cost, % of revenue. */
  cost: number;
};

export type RegionResult = {
  onCost: number;
  onSales: number;
  total: number;
  /** The 20% part's rate on revenue (rate × 20%). */
  salesRate: number;
  zone: number;
  factor: number;
};

export function regionCommission(region: RegionInput, attendance: number): RegionResult {
  const rate = commissionRate(region.kpi);
  const zone = costZone(region.cost, region.target);
  const factor = COST_ZONES[zone].factor;
  const onCost = region.revenue * attendance * rate * factor * 0.8;
  const onSales = region.revenue * rate * 0.2;
  return { onCost, onSales, total: onCost + onSales, salesRate: rate * 0.2, zone, factor };
}

export type MonthlyInput = {
  grade: SalaryGrade;
  days: number;
  /** How many bills of each `BILL_BONUSES` size. */
  bills: readonly number[];
  regions: readonly RegionInput[];
};

export function billBonus(bills: readonly number[]): number {
  return BILL_BONUSES.reduce((sum, bonus, index) => sum + (bills[index] ?? 0) * bonus.amount, 0);
}

export function monthlySalary(input: MonthlyInput) {
  const attendance = attendanceFactor(input.days);
  const base = SALARY_BASE[input.grade] * attendance;
  const allowance = DAILY_ALLOWANCE * input.days;
  const game = billBonus(input.bills);
  const regions = input.regions.map((region) => regionCommission(region, attendance));
  const commission = regions.reduce((sum, region) => sum + region.total, 0);
  return {
    attendance,
    base,
    allowance,
    game,
    regions,
    commission,
    total: base + allowance + game + commission,
  };
}

/** "Game thi đua": big bills + weeks won. */
export function gameBonus(bills: readonly number[], weeks: readonly number[]) {
  const hot = billBonus(bills);
  const week = WEEK_PRIZES.reduce((sum, prize, index) => sum + (weeks[index] ?? 0) * prize.amount, 0);
  return { hot, week, total: hot + week };
}

/** The KPI steps of the quarter / year bonus, and their rates in % of revenue. */
export const KPI_STEPS: ReadonlyArray<{ label: string; kpi: number; quarter: number; year: number }> = [
  { label: "0–60%", kpi: 60, quarter: 0.06, year: 0.03 },
  { label: "≥ 70%", kpi: 70, quarter: 0.07, year: 0.035 },
  { label: "≥ 80%", kpi: 80, quarter: 0.08, year: 0.04 },
  { label: "≥ 90%", kpi: 90, quarter: 0.09, year: 0.045 },
  { label: "≥ 100%", kpi: 100, quarter: 0.1, year: 0.05 },
  { label: "≥ 110%", kpi: 110, quarter: 0.11, year: 0.055 },
  { label: "≥ 120%", kpi: 120, quarter: 0.12, year: 0.06 },
];

/** A quarter's or a year's bonus: revenue × the step's % (the rates are percents). */
export function accumulatedBonus(revenue: number, percent: number): number {
  return (revenue * percent) / 100;
}

/** Ads cost with the fees: rented accounts + 2%, company (tax) accounts + 11,1%. */
export function adsCost(rent: number, tax: number, revenue: number) {
  const rentFee = rent * 0.02;
  const taxFee = tax * 0.111;
  const total = rent + rentFee + tax + taxFee;
  return { rentFee, taxFee, total, percent: revenue > 0 ? (total / revenue) * 100 : null };
}

/** "10,5tr" from a million up, "350.000đ" below. */
export function formatMoney(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(rounded) >= 1_000_000) {
    return `${(rounded / 1_000_000).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}tr`;
  }
  return `${rounded.toLocaleString("vi-VN")}đ`;
}

export function formatFull(value: number): string {
  return `${Math.round(value).toLocaleString("vi-VN")}đ`;
}

export function formatPercent(fraction: number, digits = 2): string {
  return `${(fraction * 100).toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
}

/** Who sees the calculator: anybody seated in the ADS department, and the OWNER / ADMIN. */
export function seesAdsSalary(
  me: { can_view_all?: boolean; departments?: ReadonlyArray<{ code: string }> } | undefined,
  sessionRole?: string,
): boolean {
  if (!me) return false;
  if (me.can_view_all || sessionRole === "OWNER" || sessionRole === "ADMIN") return true;
  return (me.departments ?? []).some((seat) => seat.code.toUpperCase() === "ADS");
}
