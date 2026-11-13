/**
 * Tài chính (0105): the revenue read from the CRM in the one run shared with
 * Phòng Ads, and its settings. Mirrors `src/meobot/api/routers/finance.py`.
 */
import { http } from "@/lib/api";

export type FinanceAccess = {
  revenue: boolean;
  customers: boolean;
  settings: boolean;
  /** Đợt 2: type the four fees on the bảng kê. */
  fees?: boolean;
  /** Đợt 3: budget codes, routes, finance roles. */
  codes?: boolean;
};

/** Money in whole đồng. */
export type Figures = {
  /** Thực thu (tiền về, VAT included). */
  collected: number;
  collected_vat: number;
  /** Thực thu - VAT. */
  collected_net: number;
  refunds: number;
  payments: number;
  /** Doanh số (orders created in the month, VAT included). */
  sales: number;
  /** Doanh thu = Doanh số - VAT. */
  revenue: number;
  sales_vat: number;
  owed: number;
  orders: number;
};

export type Line = { key: string; label: string; figures: Figures; kpi?: number | null };
export type PersonLine = {
  crm_id: number;
  name: string;
  user_id: string | null;
  user_name: string | null;
  collected: number;
  payments: number;
};
export type RoleLines = { role: string; label: string; people: PersonLine[] };
export type ShareLine = {
  crm_id: number | null;
  name: string;
  department: string | null;
  user_name: string | null;
  shared: number;
  lines: number;
};
export type TourLine = {
  crm_id: number | null;
  name: string;
  user_name: string | null;
  primary: number;
  support: number;
};

export type MonthRevenue = {
  month: string;
  figures: Figures;
  previous: Figures;
  /** The span `previous` covers: the same days of last month while this one runs. */
  previous_from: string;
  previous_to: string;
  by_entity: Line[];
  by_site: Line[];
  by_branch: Line[];
  by_team: Line[];
  days: { day: string; collected: number }[];
  roles: RoleLines[];
  shares: ShareLine[];
  tours: TourLine[];
  unmapped_branches: number;
  /** Đợt 2: the month's four fees, thực thu after VAT and them, the KPI. */
  fees?: number;
  collected_after_fees?: number;
  kpi?: number | null;
  synced_at: string | null;
  sync_status: string | null;
  sync_message: string | null;
};

export type CrmBranch = {
  id: number;
  name: string;
  location_id: number | null;
  legal_entity_id: string | null;
  apm_branch: string | null;
};
export type Branches = {
  branches: CrmBranch[];
  entities: { id: string; code: string; name: string; active: boolean }[];
  sites: { code: string; label: string }[];
  can_edit: boolean;
};

export type CrmStaff = {
  id: number;
  name: string;
  roles: string[];
  department_name: string | null;
  user_id: string | null;
  user_name: string | null;
};
export type StaffPage = {
  rows: CrmStaff[];
  total: number;
  unmapped: number;
  page_size: number;
  can_edit: boolean;
};

export type Vat = { vat_default: string; vat_by_entity: Record<string, string>; can_edit: boolean };

export type SyncStatus = {
  configured: boolean;
  at: string | null;
  status: string | null;
  message: string | null;
  synced_from: string | null;
  synced_to: string | null;
  running: boolean;
  can_sync: boolean;
};

/** Where a CRM person was seen. */
export const STAFF_ROLE_LABELS: Record<string, string> = {
  marketing: "Marketing",
  telesales: "Telesale",
  cskh: "CSKH",
  carepage: "Carepage",
  owner: "Lên đơn",
  share: "Chia doanh thu",
  tour: "Công tua",
};

// --- đợt 2 ---------------------------------------------------------------------------

export type FeeSet = { card_fee: number; referral_fee: number; car_fee: number; cost: number; total: number };
export type FeeKey = "card_fee" | "referral_fee" | "car_fee" | "cost";
export const FEE_LABELS: Record<FeeKey, string> = {
  card_fee: "Phí quẹt",
  referral_fee: "HH giới thiệu",
  car_fee: "CP xe",
  cost: "Viện phí / giá cost",
};
export type LedgerRow = {
  order_id: number | null;
  day: string;
  kind: "NEW" | "TTCN";
  order_code: string | null;
  product: boolean;
  customer_code: string | null;
  customer_name: string | null;
  branch: string | null;
  sales: number;
  owed: number;
  owed_days: number | null;
  collected: number;
  methods: Record<string, number>;
  refunds: number;
  vat: number;
  fees: FeeSet;
  auto: FeeSet;
  typed: FeeKey[];
  note: string | null;
  net: number;
  sellers: { name: string; amount: number }[];
  doctors: Record<string, { name: string; amount: number }>;
  sale: string | null;
  marketing: string | null;
  carepage: string | null;
};
export type LedgerDay = {
  day: string;
  sales: number;
  owed: number;
  service_sales: number;
  product_sales: number;
  received: number;
  refunds: number;
  collected: number;
  vat: number;
  fees: number;
  net: number;
  kpi: number | null;
};
export type Ledger = {
  since: string;
  until: string;
  rows: LedgerRow[];
  days: LedgerDay[];
  kpi_month: number | null;
  methods: Record<string, string>;
  can_edit_fees: boolean;
};
export type DebtLine = {
  order_id: number;
  code: string | null;
  created_on: string;
  age: number;
  bucket: number | null;
  customer_code: string | null;
  customer_name: string | null;
  branch: string | null;
  owner: string | null;
  total: number;
  owed: number;
};
export type DebtReport = {
  today: string;
  milestones: number[];
  lines: DebtLine[];
  buckets: { milestone: number | null; orders: number; owed: number }[];
  owed: number;
};
export type DebtSettings = { milestones: number[]; recipients: string[]; can_edit: boolean };
export type FeeRules = { methods: Record<string, string>; rates: Record<string, string>; can_edit: boolean };
export type ServiceCost = { service_id: number; name: string; cost: number | null };
export type Kpi = {
  month: string;
  values: Record<string, number>;
  cells: Record<string, string[]>;
  labels: Record<string, string>;
  can_edit: boolean;
};

function query(params: Record<string, string | number | boolean | undefined>): string {
  const pairs = Object.entries(params).filter(([, value]) => value !== undefined && value !== "");
  if (!pairs.length) return "";
  return `?${pairs.map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`).join("&")}`;
}

/** "Tính năng đang phát triển" (13/11, OWNER only): modules hidden from all but the OWNER. */
export type DevelopingModules = { modules: { module: string; label: string; developing: boolean }[] };

export const developingApi = {
  list: () => http.get<DevelopingModules>("/api/permissions/developing-modules"),
  save: (modules: string[]) =>
    http.put<DevelopingModules>("/api/permissions/developing-modules", { modules }),
};

export const financeApi = {
  access: () => http.get<FinanceAccess>("/api/finance/access"),
  revenue: (month: string) => http.get<MonthRevenue>(`/api/finance/revenue${query({ month })}`),
  branches: () => http.get<Branches>("/api/finance/branches"),
  updateBranch: (id: number, body: { legal_entity_id?: string | null; apm_branch?: string | null }) =>
    http.patch<CrmBranch>(`/api/finance/branches/${id}`, body),
  staff: (params: { q?: string; unmapped?: boolean; page?: number }) =>
    http.get<StaffPage>(`/api/finance/staff${query(params)}`),
  updateStaff: (id: number, userId: string | null) =>
    http.patch<CrmStaff>(`/api/finance/staff/${id}`, { user_id: userId }),
  people: () =>
    http.get<{ id: string; name: string; member_code: string | null }[]>("/api/finance/people"),
  vat: () => http.get<Vat>("/api/finance/vat"),
  setVat: (body: { vat_default: string; vat_by_entity: Record<string, string | null> }) =>
    http.put<Vat>("/api/finance/vat", body),
  syncStatus: () => http.get<SyncStatus>("/api/finance/sync"),
  ledger: (since: string, until: string) =>
    http.get<Ledger>(`/api/finance/ledger${query({ since, until })}`),
  ledgerExportUrl: (month: string) => `/api/finance/ledger.xlsx${query({ month })}`,
  setFees: (body: { order_id: number; day: string } & Partial<Record<FeeKey, string | null>> & { note?: string | null }) =>
    http.patch<LedgerRow | null>("/api/finance/fees", body),
  debts: (params: { min_bucket?: number; q?: string }) =>
    http.get<DebtReport>(`/api/finance/debts${query(params)}`),
  debtSettings: () => http.get<DebtSettings>("/api/finance/debts/settings"),
  setDebtSettings: (body: { milestones: number[]; recipients: string[] }) =>
    http.put<DebtSettings>("/api/finance/debts/settings", body),
  feeRules: () => http.get<FeeRules>("/api/finance/fee-rules"),
  setFeeRules: (rates: Record<string, string | null>) =>
    http.put<FeeRules>("/api/finance/fee-rules", { rates }),
  serviceCosts: (q: string) => http.get<ServiceCost[]>(`/api/finance/service-costs${query({ q })}`),
  setServiceCost: (id: number, body: { name: string; cost: string | null }) =>
    http.put<ServiceCost>(`/api/finance/service-costs/${id}`, body),
  kpi: (month: string) => http.get<Kpi>(`/api/finance/kpi${query({ month })}`),
  setKpi: (month: string, entries: Record<string, string | null>) =>
    http.put<Kpi>("/api/finance/kpi", { month, entries }),
  sync: (body: { since?: string; until?: string } = {}) =>
    http.post<{ task_id: string; job_key: string }>("/api/finance/sync", body),
};

/** "2026-11" -> the first and last day ("2026-11-01", "2026-11-30"). */
export function monthBounds(month: string): [string, string] {
  const [year, value] = month.split("-").map(Number);
  const last = new Date(year, value, 0).getDate();
  return [`${month}-01`, `${month}-${String(last).padStart(2, "0")}`];
}

/** 1234567 -> "1.234.567 ₫". */
export function money(value: number): string {
  return `${Math.round(value).toLocaleString("vi-VN")} ₫`;
}

/** Change against the month before, as "+12,3%" / "-4%" ("—" without a base). */
export function change(now: number, before: number): string {
  if (!before) return "—";
  const pct = ((now - before) / Math.abs(before)) * 100;
  const text = pct.toFixed(Math.abs(pct) < 10 ? 1 : 0).replace(".", ",");
  return `${pct > 0 ? "+" : ""}${text}%`;
}

// --- đợt 3: hồ sơ chi, mã chi, vai trò, ngân sách, MISA, thu - chi ---------------------

export type Option = { value: string; label: string };
export type SpendCatalog = {
  roles: Option[];
  assignable: Option[];
  purchase_roles: Option[];
  payment_roles: Option[];
  executor_roles: Option[];
  cash_roles: Option[];
  documents: Option[];
  phases: Option[];
  statuses: Option[];
  branches: Option[];
  entities: Option[];
  departments: Option[];
  access: { view_requests: boolean; codes: boolean; budget: boolean; misa: boolean; report: boolean };
};
export type RouteValue = {
  purchase: string[];
  executor: string | null;
  payment: string[];
  cash: string[];
  documents: string[];
  documents_note: string;
  no_approval: boolean;
  proposer: string;
};
export type FinanceCode = {
  code: string;
  name: string;
  parent_code: string | null;
  kind: string;
  leaf: boolean;
  active: boolean;
  route: RouteValue;
  no_route: boolean;
};
export type RoleAssignment = {
  id: number;
  role: string;
  role_label: string;
  user_id: string;
  user_name: string;
  legal_entity_id: string | null;
  legal_entity: string | null;
  apm_branch: string | null;
  standin_user_id: string | null;
  standin_name: string | null;
  note: string | null;
};
export type Person = { id: string; name: string; member_code: string | null };
export type RequestRow = {
  id: string;
  code: string;
  title: string;
  amount: number;
  budget_code: string;
  status: string;
  status_label: string;
  phase: string | null;
  phase_label: string | null;
  requester: string;
  created_at: string;
  paid_at: string | null;
  docs_due_on: string | null;
};
export type RequestStep = {
  id: number;
  round: number;
  phase: string;
  phase_label: string;
  role: string;
  role_label: string;
  status: string;
  approver: string | null;
  standin: string | null;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  remark: string | null;
};
export type RequestDetail = RequestRow & {
  description: string;
  vendor: string | null;
  payee: string | null;
  legal_entity_id: string;
  legal_entity: string;
  department_id: string | null;
  department: string;
  apm_branch: string | null;
  branch: string | null;
  code_name: string;
  budget_month: string;
  needed_on: string | null;
  purchase_note: string | null;
  realized_at: string | null;
  version: number;
  steps: RequestStep[];
  open_step_id: number | null;
  events: { at: string; by: string | null; action: string; note: string | null }[];
  documents: {
    id: string;
    kind: string;
    kind_label: string;
    name: string;
    size: number;
    storage: string;
    by: string | null;
    at: string;
  }[];
  required: Option[];
  documents_note: string;
  missing: Option[];
  budget: { budget: number; committed: number; realized: number; remaining: number } | null;
  can: string[];
  can_paper: boolean;
};
export type BudgetLine = {
  legal_entity_id: string;
  legal_entity: string;
  department_id: string;
  department: string;
  code: string;
  code_name: string;
  budget: number;
  committed: number;
  realized: number;
  remaining: number;
};
export type Misa = {
  month: string;
  imported: boolean;
  misa_revenue: number;
  apm_revenue: number;
  revenue_gap: number;
  misa_cost: number;
  apm_cost: number;
  cost_gap: number;
  lines: { account: string; name: string; debit: number; credit: number; closing_debit: number; closing_credit: number }[];
  can_import: boolean;
};
export type PnlLine = {
  key: string;
  label: string;
  income: number;
  realized: number;
  committed: number;
  budget: number;
  result: number;
};

async function upload<T>(path: string, file: File | Blob): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/octet-stream" },
    body: file,
  });
  const body = (await response.json().catch(() => ({}))) as T & {
    error?: { message?: string; details?: { problems?: string[] } };
  };
  if (!response.ok) {
    const problems = body.error?.details?.problems;
    throw new Error(
      [body.error?.message ?? "Không tải lên được.", ...(problems ?? [])].join("\n"),
    );
  }
  return body;
}

export const spendApi = {
  catalog: () => http.get<SpendCatalog>("/api/finance/spend/catalog"),
  codes: () => http.get<{ codes: FinanceCode[]; can_edit: boolean }>("/api/finance/codes"),
  updateCode: (code: string, body: { name?: string; active?: boolean; route?: RouteValue }) =>
    http.patch<FinanceCode>(`/api/finance/codes/${encodeURIComponent(code)}`, body),
  codeHistory: (code: string) =>
    http.get<{ at: string; by: string | null; before: unknown; after: unknown }[]>(
      `/api/finance/codes/${encodeURIComponent(code)}/history`,
    ),
  roles: () =>
    http.get<{ assignments: RoleAssignment[]; people: Person[]; can_edit: boolean }>(
      "/api/finance/roles",
    ),
  addRole: (body: {
    role: string;
    user_id: string;
    legal_entity_id?: string | null;
    apm_branch?: string | null;
    standin_user_id?: string | null;
    note?: string | null;
  }) => http.post<{ id: number }>("/api/finance/roles", body),
  removeRole: (id: number) => http.del<void>(`/api/finance/roles/${id}`),
  spendSettings: () =>
    http.get<{ ceo_threshold: number | null; drive_folder_id: string | null; can_edit: boolean }>(
      "/api/finance/spend-settings",
    ),
  setSpendSettings: (body: { ceo_threshold: string | null; drive_folder_id: string | null }) =>
    http.put<{ ceo_threshold: number | null; drive_folder_id: string | null; can_edit: boolean }>(
      "/api/finance/spend-settings",
      body,
    ),
  requests: (params: { tab: string; status?: string; q?: string }) =>
    http.get<{ rows: RequestRow[]; awaiting: number }>(`/api/finance/requests${query(params)}`),
  form: () =>
    http.get<{
      codes: FinanceCode[];
      my_departments: string[];
      my_entity: string | null;
      my_branch: string | null;
    }>("/api/finance/requests/form"),
  create: (body: Record<string, unknown>) =>
    http.post<{ id: string; code: string }>("/api/finance/requests", body),
  request: (id: string) => http.get<RequestDetail>(`/api/finance/requests/${id}`),
  edit: (id: string, body: Record<string, unknown>) =>
    http.patch<{ id: string; version: number }>(`/api/finance/requests/${id}`, body),
  act: (
    id: string,
    body: { action: string; note?: string | null; step_id?: number | null; purchase_note?: string | null },
  ) => http.post<RequestDetail>(`/api/finance/requests/${id}/actions`, body),
  uploadDocument: (id: string, kind: string, file: File) =>
    upload<{ id: string; storage: string }>(
      `/api/finance/requests/${id}/documents${query({ kind, name: file.name })}`,
      file,
    ),
  paper: (id: string, kind: string) =>
    http.post<{ id: string }>(`/api/finance/requests/${id}/papers`, { kind }),
  removeDocument: (id: string) => http.del<void>(`/api/finance/documents/${id}`),
  documentUrl: (id: string) => `/api/finance/documents/${id}`,
  budgets: (month: string, entity?: string) =>
    http.get<{ month: string; lines: BudgetLine[]; can_edit: boolean }>(
      `/api/finance/budgets${query({ month, legal_entity_id: entity })}`,
    ),
  setBudget: (body: {
    month: string;
    legal_entity_id: string;
    department_id: string;
    code: string;
    amount: string | null;
  }) => http.put<void>("/api/finance/budgets/cell", body),
  budgetTemplateUrl: (month: string) => `/api/finance/budgets/template.xlsx${query({ month })}`,
  importBudgets: (file: File) => upload<{ lines: number }>("/api/finance/budgets/import", file),
  misa: (entity: string, month: string) =>
    http.get<Misa>(`/api/finance/misa${query({ legal_entity_id: entity, month })}`),
  importMisa: (entity: string, month: string, file: File) =>
    upload<{ lines: number }>(
      `/api/finance/misa/import${query({ legal_entity_id: entity, month })}`,
      file,
    ),
  pnl: (month: string) =>
    http.get<{ month: string; entities: PnlLine[]; total: PnlLine; departments: PnlLine[] }>(
      `/api/finance/pnl${query({ month })}`,
    ),
};

/** "15tr" / "1,5tr" / "500k" / "2ty" / "1.500.000" -> 1500000 (M6: suggestions). */
export function amountSuggestions(text: string): number[] {
  const digits = text.replace(/[^\d]/g, "");
  if (!digits) return [];
  const base = Number(digits);
  if (!Number.isFinite(base) || base <= 0) return [];
  const found = new Set<number>();
  for (const scale of [1, 1_000, 10_000, 100_000, 1_000_000, 10_000_000]) {
    const value = base * scale;
    if (value >= 1_000 && value <= 100_000_000_000) found.add(value);
  }
  return [...found].sort((a, b) => a - b).slice(0, 5);
}
