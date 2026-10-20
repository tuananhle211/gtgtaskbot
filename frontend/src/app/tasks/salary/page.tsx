"use client";

import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import {
  BILL_BONUSES,
  COST_ZONES,
  KPI_STEPS,
  SALARY_BASE,
  WEEK_PRIZES,
  WORK_DAYS,
  accumulatedBonus,
  adsCost,
  commissionRate,
  formatFull,
  formatMoney,
  formatPercent,
  gameBonus,
  monthlySalary,
  seesAdsSalary,
  type RegionInput,
  type SalaryGrade,
} from "@/lib/ads-salary";

/**
 * "Tính lương Ads" (2026-10-20): the Ads MKT pay calculator, formulas fixed
 * in `lib/ads-salary.ts`. For people seated in the ADS department (and the
 * OWNER / ADMIN). Everything happens in the browser: nothing is saved, nothing
 * is sent - it is an estimate a person makes of their own month.
 */
type Tab = "luong" | "game" | "quy-nam" | "cost";
const TABS: ReadonlyArray<{ key: Tab; label: string }> = [
  { key: "luong", label: "Lương tháng" },
  { key: "game", label: "Game thi đua" },
  { key: "quy-nam", label: "Quý & Năm" },
  { key: "cost", label: "Máy tính cost" },
];

export default function AdsSalaryPage() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const session = useQuery({ queryKey: ["session"], queryFn: api.session });
  const [tab, setTab] = useState<Tab>("luong");

  if (me.isPending || session.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (!seesAdsSalary(me.data, session.data?.role)) {
    return (
      <section role="alert" className="panel p-6">
        <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          Công thức tính lương này dành cho nhân sự phòng ADS.
        </p>
      </section>
    );
  }
  return (
    <div className="space-y-4">
      <PageHeader
        title="Tính lương Ads"
        subtitle="Kéo thông số của bạn để ước lượng thu nhập: P1+P2 + Phụ cấp + Game + Hoa hồng P3. Chỉ là ước tính, không lưu lại."
      />
      <div
        role="tablist"
        aria-label="Máy tính"
        className="flex gap-1 overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1 [scrollbar-width:none]"
      >
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`min-h-10 flex-1 shrink-0 rounded-lg px-3 text-sm font-medium ${
              tab === item.key
                ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "luong" ? <MonthlyTab /> : null}
      {tab === "game" ? <GameTab /> : null}
      {tab === "quy-nam" ? <QuarterYearTab /> : null}
      {tab === "cost" ? <CostTab /> : null}
    </div>
  );
}

// --- shared bits ----------------------------------------------------------------

function Slider({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  display,
  unit,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  display?: string;
  unit?: string;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-[var(--text-muted)]">
        {label}
      </label>
      <p className="mt-0.5 text-xl font-semibold tabular-nums">
        {display ?? value.toLocaleString("vi-VN")}
        {unit ? <span className="ml-1 text-xs font-normal text-[var(--text-muted)]">{unit}</span> : null}
      </p>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 w-full accent-[var(--accent)]"
      />
    </div>
  );
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="panel p-4 sm:p-6">
      <h2 className="text-base font-semibold">{title}</h2>
      {subtitle ? <p className="mt-0.5 text-sm text-[var(--text-muted)]">{subtitle}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Line({ label, value, tone }: { label: React.ReactNode; value: string; tone?: "good" | "accent" }) {
  return (
    <div
      className={`flex items-center justify-between gap-3 rounded-lg border border-[var(--border)] px-3 py-2 text-sm ${
        tone === "accent" ? "bg-[var(--accent-soft)] sm:col-span-2" : "bg-[var(--surface-muted)]"
      }`}
    >
      <span className="text-[var(--text-muted)]">{label}</span>
      <b
        className={`tabular-nums ${
          tone === "good" ? "text-[var(--good)]" : tone === "accent" ? "text-[var(--accent-strong)]" : ""
        }`}
      >
        {value}
      </b>
    </div>
  );
}

function Result({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-4 sm:p-5">
      <p className="brand-eyebrow">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight text-[var(--accent-strong)] sm:text-4xl">
        {value}
      </p>
      {children}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-4 rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">{children}</p>
  );
}

const billions = (value: number) =>
  (value / 1_000_000_000).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

// --- Lương tháng ------------------------------------------------------------------

const DEFAULT_REGION: RegionInput = { revenue: 1_500_000_000, kpi: 100, target: 45, cost: 44 };

function MonthlyTab() {
  const [grade, setGrade] = useState<SalaryGrade>(1);
  const [days, setDays] = useState(WORK_DAYS);
  const [bills, setBills] = useState<number[]>([3, 0, 0, 0]);
  const [regions, setRegions] = useState<RegionInput[]>([DEFAULT_REGION, DEFAULT_REGION]);
  const result = monthlySalary({ grade, days, bills, regions });
  const names = ["Miền Bắc", "Miền Nam"];

  const setRegion = (index: number, change: Partial<RegionInput>) =>
    setRegions((current) => current.map((region, at) => (at === index ? { ...region, ...change } : region)));

  return (
    <div className="space-y-4">
      <Section title="Bậc, ngày công và Game" subtitle="Lương cứng, phụ cấp và Game tính một lần cho cả hai miền.">
        <p className="text-xs font-medium text-[var(--text-muted)]">Bậc lương (Trưởng phòng xếp theo năng lực)</p>
        <div role="radiogroup" aria-label="Bậc lương" className="mt-1 grid grid-cols-3 gap-2">
          {([1, 2, 3] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={grade === value}
              onClick={() => setGrade(value)}
              className={`min-h-14 rounded-lg border px-2 text-sm ${
                grade === value
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                  : "border-[var(--border)] bg-[var(--surface)]"
              }`}
            >
              Bậc {value}
              <span className="block text-base font-semibold tabular-nums">{formatMoney(SALARY_BASE[value])}</span>
            </button>
          ))}
        </div>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <Slider
            label="Ngày công thực tế / tháng"
            value={days}
            onChange={setDays}
            min={0}
            max={WORK_DAYS}
            step={0.5}
            display={days.toLocaleString("vi-VN")}
            unit={`/ ${WORK_DAYS} công (sáng T7 = 0,5)`}
          />
        </div>
        <p className="mt-5 text-xs font-medium text-[var(--text-muted)]">Thưởng nóng bill lớn (bill ≥ 100tr đã thanh toán đủ)</p>
        <div className="mt-2 grid gap-5 sm:grid-cols-2">
          {BILL_BONUSES.map((bonus, index) => (
            <Slider
              key={bonus.label}
              label={`Số bill ${bonus.label} (× ${formatMoney(bonus.amount)})`}
              value={bills[index]}
              onChange={(value) => setBills((current) => current.map((n, at) => (at === index ? value : n)))}
              min={0}
              max={bonus.max}
              unit="bill"
            />
          ))}
        </div>
        <Note>
          Đi đủ từ 90% công (≥ 21/23) nhận đủ lương cứng và hoa hồng; dưới 90% tính theo tỷ lệ thực. Phần 20% thưởng mốc
          doanh thu tính trên toàn bộ doanh thu, không trừ ngày công, không nhân hệ số vùng.
        </Note>
      </Section>

      <Section
        title="Hoa hồng P3 theo miền"
        subtitle="Mỗi miền có doanh thu, KPI, khoán chi phí X và chi phí thực tế riêng. Chỉ chạy một miền thì kéo doanh thu miền kia về 0."
      >
        <div className="grid gap-4 md:grid-cols-2">
          {regions.map((region, index) => {
            const zone = COST_ZONES[result.regions[index].zone];
            return (
              <div key={names[index]} className="rounded-xl border border-[var(--border)] p-4">
                <h3 className="text-sm font-semibold">{names[index]}</h3>
                <div className="mt-3 space-y-4">
                  <Slider
                    label="Doanh thu / tháng"
                    value={region.revenue / 1_000_000}
                    onChange={(value) => setRegion(index, { revenue: value * 1_000_000 })}
                    min={0}
                    max={15_000}
                    step={50}
                    display={billions(region.revenue)}
                    unit="tỷ đồng"
                  />
                  <Slider
                    label="KPI doanh thu chi nhánh"
                    value={region.kpi}
                    onChange={(value) => setRegion(index, { kpi: value })}
                    min={50}
                    max={150}
                    step={5}
                    unit={`% · hoa hồng ${formatPercent(commissionRate(region.kpi))}`}
                  />
                  <Slider
                    label="Chỉ tiêu khoán chi phí (X)"
                    value={region.target}
                    onChange={(value) => setRegion(index, { target: value })}
                    min={25}
                    max={65}
                    unit="%"
                  />
                  <Slider
                    label="Chi phí Ads thực tế"
                    value={region.cost}
                    onChange={(value) => setRegion(index, { cost: value })}
                    min={20}
                    max={75}
                    unit="%"
                  />
                </div>
                <p className="mt-3 text-xs text-[var(--text-muted)]">
                  Vùng chi phí: <b className="text-[var(--text)]">{zone.name}</b> · hệ số ×
                  {zone.factor.toLocaleString("vi-VN")}
                </p>
              </div>
            );
          })}
        </div>
        <ZoneLegend />
        <Result label="Tổng lương ước tính" value={formatMoney(result.total)}>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            <Line label="Lương cứng (P1+P2)" value={formatMoney(result.base)} />
            <Line label="Phụ cấp ăn + xe" value={formatMoney(result.allowance)} />
            {result.regions.map((region, index) => (
              <div key={names[index]} className="contents">
                <Line label={`${names[index]} · HH chi phí 80%`} value={formatMoney(region.onCost)} />
                <Line
                  label={`${names[index]} · Thưởng mốc DT 20% · ${formatPercent(region.salesRate)}`}
                  value={formatMoney(region.onSales)}
                  tone="good"
                />
              </div>
            ))}
            <Line label="Thưởng nóng Game" value={formatMoney(result.game)} />
            <Line label="Tổng hoa hồng P3 (2 miền)" value={formatMoney(result.commission)} tone="good" />
          </div>
        </Result>
      </Section>
    </div>
  );
}

function ZoneLegend() {
  return (
    <div className="mt-4">
      <p className="text-xs font-medium text-[var(--text-muted)]">
        Vùng chi phí (chênh lệch = chi phí thực tế − khoán X) và hệ số nhân phần 80%
      </p>
      <ul className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {COST_ZONES.map((zone, index) => {
          const from = index === 0 ? null : COST_ZONES[index - 1].upTo;
          const range =
            from === null ? `≤ ${zone.upTo}` : Number.isFinite(zone.upTo) ? `${from} → ${zone.upTo}` : `> ${from}`;
          const tone =
            zone.tone === "good"
              ? "border-[var(--good)]/40 bg-[var(--good-soft)]"
              : zone.tone === "bad"
                ? "border-[var(--bad)]/40 bg-[var(--bad-soft)]"
                : zone.tone === "warn"
                  ? "border-[var(--warn)]/40 bg-[var(--warn-soft)]"
                  : "border-[var(--border)] bg-[var(--surface-muted)]";
          return (
            <li key={zone.name} className={`rounded-full border px-2.5 py-1 ${tone}`}>
              {zone.name} · {range} điểm % · ×{zone.factor.toLocaleString("vi-VN")}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- Game thi đua ------------------------------------------------------------------

function GameTab() {
  const [bills, setBills] = useState<number[]>([5, 0, 0, 0]);
  const [weeks, setWeeks] = useState<number[]>([0, 0, 0]);
  const result = gameBonus(bills, weeks);
  return (
    <Section title="Thưởng Game thi đua" subtitle="Quỹ thưởng = doanh thu tháng × 0,1% · thi đua theo tuần.">
      <p className="text-xs font-medium text-[var(--text-muted)]">Thưởng nóng bill lớn (bill đã thanh toán đủ)</p>
      <div className="mt-2 grid gap-5 sm:grid-cols-2">
        {BILL_BONUSES.map((bonus, index) => (
          <Slider
            key={bonus.label}
            label={`Số bill ${bonus.label} (× ${formatMoney(bonus.amount)})`}
            value={bills[index]}
            onChange={(value) => setBills((current) => current.map((n, at) => (at === index ? value : n)))}
            min={0}
            max={bonus.max}
            unit="bill"
          />
        ))}
      </div>
      <p className="mt-5 text-xs font-medium text-[var(--text-muted)]">Giải tuần thi đua (số tuần thắng trong tháng)</p>
      <div className="mt-2 grid gap-5 sm:grid-cols-2">
        {WEEK_PRIZES.map((prize, index) => (
          <Slider
            key={prize.label}
            label={`${prize.label} (× ${formatMoney(prize.amount)} / tuần)`}
            value={weeks[index]}
            onChange={(value) => setWeeks((current) => current.map((n, at) => (at === index ? value : n)))}
            min={0}
            max={4}
            unit="/ 4 tuần"
          />
        ))}
      </div>
      <Result label="Tổng thưởng Game tháng này" value={formatMoney(result.total)}>
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <Line label="Thưởng nóng bill lớn" value={formatMoney(result.hot)} />
          <Line label="Giải tuần thi đua" value={formatMoney(result.week)} />
        </div>
      </Result>
    </Section>
  );
}

// --- Quý & Năm ------------------------------------------------------------------------

function QuarterYearTab() {
  const [quarterRevenue, setQuarterRevenue] = useState(6_000);
  const [quarterStep, setQuarterStep] = useState(4);
  const [yearRevenue, setYearRevenue] = useState(24_000);
  const [yearStep, setYearStep] = useState(4);
  const quarter = KPI_STEPS[quarterStep];
  const year = KPI_STEPS[yearStep];
  const pct = (value: number, digits: number) =>
    `${value.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
  return (
    <Section
      title="Thưởng tích luỹ Quý & Năm"
      subtitle="Khoản cộng thêm ngoài hoa hồng tháng, cho người gắn bó trọn kỳ."
    >
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold">Quý (3 tháng)</h3>
          <div className="mt-3 space-y-4">
            <Slider
              label="Tổng doanh thu cả quý"
              value={quarterRevenue}
              onChange={setQuarterRevenue}
              min={0}
              max={20_000}
              step={100}
              display={billions(quarterRevenue * 1_000_000)}
              unit="tỷ đồng"
            />
            <Slider
              label="KPI doanh thu quý"
              value={quarterStep}
              onChange={setQuarterStep}
              min={0}
              max={KPI_STEPS.length - 1}
              display={quarter.label}
            />
          </div>
          <Result label="Thưởng tích luỹ quý" value={formatMoney(accumulatedBonus(quarterRevenue * 1_000_000, quarter.quarter))}>
            <p className="mt-1 text-xs text-[var(--text-muted)]">{pct(quarter.quarter, 2)} × tổng doanh thu quý</p>
          </Result>
        </div>
        <div>
          <h3 className="text-sm font-semibold">Năm (12 tháng)</h3>
          <div className="mt-3 space-y-4">
            <Slider
              label="Tổng doanh thu cả năm"
              value={yearRevenue}
              onChange={setYearRevenue}
              min={0}
              max={80_000}
              step={500}
              display={billions(yearRevenue * 1_000_000)}
              unit="tỷ đồng"
            />
            <Slider
              label="KPI doanh thu năm"
              value={yearStep}
              onChange={setYearStep}
              min={0}
              max={KPI_STEPS.length - 1}
              display={year.label}
            />
          </div>
          <Result label="Thưởng tích luỹ năm" value={formatMoney(accumulatedBonus(yearRevenue * 1_000_000, year.year))}>
            <p className="mt-1 text-xs text-[var(--text-muted)]">{pct(year.year, 3)} × tổng doanh thu năm</p>
          </Result>
        </div>
      </div>
      <Note>
        Phần thưởng quý và năm là khoản cộng thêm ngoài hoa hồng tháng. Điều kiện: gắn bó đến thời điểm chi trả của kỳ;
        nghỉ trước kỳ chi trả thì không được xét.
      </Note>
      <div className="table-scroll-x mt-4">
        <table className="table-dense w-full">
          <thead>
            <tr>
              <th scope="col">KPI DT chi nhánh</th>
              <th scope="col">% HH tháng</th>
              <th scope="col">+ % HH quý</th>
              <th scope="col">+ % HH năm</th>
            </tr>
          </thead>
          <tbody>
            {[...KPI_STEPS].reverse().map((step) => (
              <tr key={step.label}>
                <td className="font-medium">{step.label}</td>
                <td className="tabular-nums">{formatPercent(commissionRate(step.kpi))}</td>
                <td className="tabular-nums text-[var(--good)]">+{pct(step.quarter, 2)}</td>
                <td className="tabular-nums">+{pct(step.year, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

// --- Máy tính cost --------------------------------------------------------------------

function MoneyField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-[var(--text-muted)]">
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        value={value ? value.toLocaleString("vi-VN") : ""}
        onChange={(event) => {
          const digits = event.target.value.replace(/[^\d]/g, "");
          onChange(digits ? Number.parseInt(digits, 10) : 0);
        }}
        className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-right text-lg font-semibold tabular-nums text-[var(--text)]"
      />
    </div>
  );
}

function CostTab() {
  const [rent, setRent] = useState(200_000_000);
  const [tax, setTax] = useState(150_000_000);
  const [revenue, setRevenue] = useState(800_000_000);
  const result = adsCost(rent, tax, revenue);
  return (
    <Section title="Máy tính cost" subtitle="Chi phí Ads trên doanh thu, gộp hai nguồn chi tiêu và phí (đơn vị: đồng).">
      <div className="grid gap-4 sm:grid-cols-2">
        <MoneyField label="Chi tiêu tài khoản thuê · phí 2%" value={rent} onChange={setRent} />
        <MoneyField label="Chi tiêu tài khoản thuế công ty · phí 11,1%" value={tax} onChange={setTax} />
        <div className="sm:col-span-2">
          <MoneyField label="Doanh thu" value={revenue} onChange={setRevenue} />
        </div>
      </div>
      <Result
        label="Cost = tổng chi phí ÷ doanh thu"
        value={
          result.percent === null
            ? "—"
            : `${result.percent.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
        }
      >
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <Line label="Chi tiêu TK thuê" value={formatFull(rent)} />
          <Line label="+ Phí thuê (2%)" value={formatFull(result.rentFee)} />
          <Line label="Chi tiêu TK thuế công ty" value={formatFull(tax)} />
          <Line label="+ Phí thuế (11,1%)" value={formatFull(result.taxFee)} />
          <Line label="Tổng chi phí Ads" value={formatFull(result.total)} tone="accent" />
        </div>
      </Result>
      <Note>
        Chi phí mỗi nguồn = chi tiêu + phí (thuê ×1,02 · thuế ×1,111). Lấy con số Cost % này nhập vào ô &quot;Chi phí Ads
        thực tế&quot; ở tab Lương tháng.
      </Note>
    </Section>
  );
}
