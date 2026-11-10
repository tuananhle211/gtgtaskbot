/**
 * "Nhân sự" (0096): hồ sơ nhân sự. Mirrors `src/meobot/api/routers/hr.py`.
 * Sensitive blocks (`personal`, `pay`, `insurance`, `documents`) are null
 * when the reader may not see them.
 */
import { http } from "@/lib/api";

export type Choice = { code: string; label: string };

export type HrAccess = {
  lists: boolean;
  overview: boolean;
  edit: boolean;
  sensitive: boolean;
  export: boolean;
  choices: {
    branches: Choice[];
    statuses: Choice[];
    genders: Choice[];
    marital: string[];
    work_forms: string[];
    salary_types: string[];
    document_kinds: Choice[];
    request_fields: Choice[];
    contract_types: { code: string; name: string; months: number }[];
    contract_states: Choice[];
    max_document_mb: number;
  };
  settings: { contract_warn_days: number; required_documents: string[] };
};

export type HrSeat = {
  department_id: string;
  code: string;
  name: string;
  position: string;
  position_label?: string;
  manager?: string | null;
};

export type HrContractBrief = {
  type_name: string;
  number: string | null;
  end_on: string | null;
  state: string;
  state_label: string;
  days_left: number | null;
};

export type HrRow = {
  user_id: string;
  member_code: string | null;
  full_name: string;
  job_title: string | null;
  departments: HrSeat[];
  branch: string | null;
  branch_label: string | null;
  legal_entity_id: string | null;
  legal_entity_code: string | null;
  employment_status: string;
  employment_status_label: string;
  active: boolean;
  telegram_linked: boolean;
  gender: string | null;
  gender_label: string | null;
  grade: string | null;
  work_email: string | null;
  joined_on: string | null;
  official_on: string | null;
  seniority: string | null;
  contract: HrContractBrief | null;
  contract_warning: boolean;
  missing_documents: string[];
  sensitive: boolean;
  phone?: string | null;
  birth_date?: string | null;
  personal_email?: string | null;
  id_masked?: string | null;
};

export type HrContract = HrContractBrief & {
  id: string;
  type_code: string;
  start_on: string | null;
  signed: boolean;
  signed_by: string | null;
  ended_on: string | null;
  salary: number | null;
  note: string | null;
};

export type HrDocument = {
  id: string;
  kind: string;
  kind_label: string;
  name: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
};

export type HrProfile = HrRow & {
  contracts: HrContract[];
  work: {
    grade: string | null;
    work_form: string | null;
    joined_on: string | null;
    official_on: string | null;
    seniority: string | null;
    left_on: string | null;
    leave_reason: string | null;
    work_email: string | null;
    note: string | null;
    team?: string | null;
    fingerprint_id?: string | null;
    fingerprint_on?: string | null;
    fingerprint_floor?: string | null;
  };
  can_edit: boolean;
  /** 0097: the paper file HCNS keeps (sensitive viewers only). */
  papers?: null | {
    scan_link: string | null;
    items: { code: string; label: string; handed: boolean; uploaded: boolean; required: boolean }[];
  };
  personal: null | {
    gender: string | null;
    birth_date: string | null;
    age: number | null;
    birth_place: string | null;
    nationality: string | null;
    ethnicity: string | null;
    religion: string | null;
    marital_status: string | null;
    id_number: string | null;
    id_issued_on: string | null;
    id_issued_by: string | null;
    phone: string | null;
    personal_email: string | null;
    drive_email: string | null;
    permanent_address: string | null;
    current_address: string | null;
    bank_name: string | null;
    bank_account: string | null;
    bank_holder: string | null;
    bank2_name?: string | null;
    bank_account_2?: string | null;
    bank2_holder?: string | null;
    secret_unreadable: boolean;
  };
  pay: null | {
    base_salary: number | null;
    allowance: number | null;
    total: number | null;
    salary_type: string | null;
    salary_note: string | null;
  };
  insurance: null | {
    social_insurance_no: string | null;
    insurance_title?: string | null;
    social_insurance_from: string | null;
    insurance_salary: number | null;
    health_insurance_no: string | null;
    tax_code: string | null;
    dependants: number | null;
  };
  documents: HrDocument[] | null;
  requests: { id: string; field: string; new_value: string | null; status: string; created_at: string }[];
};

export type HrBrief = {
  user_id: string;
  member_code: string | null;
  full_name: string;
  job_title: string | null;
  department: string | null;
  joined_on: string | null;
  contract: HrContractBrief | null;
  birth_date?: string | null;
};

export type HrOverview = {
  total: number;
  active: number;
  left: number;
  female_pct: number | null;
  by_department: { name: string; code: string; people: number }[];
  by_status: { label: string; people: number }[];
  expiring: HrBrief[];
  expiring_count: number;
  expired_count: number;
  newest: HrBrief[];
  pending_requests: number;
  missing_documents: number;
  no_contract: number;
  sensitive: boolean;
  average_age: number | null;
  birthdays: HrBrief[];
  payroll: number | null;
};

export type HrRequest = {
  id: string;
  user_id: string;
  member_code: string | null;
  full_name: string;
  field: string;
  field_label: string;
  old_value: string | null;
  new_value: string | null;
  status: string;
  status_label: string;
  note: string | null;
  created_at: string;
  decided_at: string | null;
};

export type HrOrgDepartment = {
  id: string;
  code: string;
  name: string;
  block: string | null;
  parent_id: string | null;
  heads: { user_id: string; full_name: string; job_title: string | null }[];
  people: number;
};

export type HrFilters = {
  q?: string;
  department_id?: string;
  branch?: string;
  employment_status?: string;
  legal_entity_id?: string;
  gender?: string;
  contract?: string;
  page?: number;
  page_size?: number;
};

const query = (filters: HrFilters) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "" && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
};

export const hrApi = {
  access: () => http.get<HrAccess>("/api/hr/access"),
  overview: () => http.get<HrOverview>("/api/hr/overview"),
  profiles: (filters: HrFilters) =>
    http.get<{ total: number; page: number; page_size: number; items: HrRow[] }>(
      `/api/hr/profiles${query(filters)}`,
    ),
  profile: (id: string) => http.get<HrProfile>(`/api/hr/profiles/${id}`),
  update: (id: string, fields: Record<string, unknown>) =>
    http.patch<HrProfile>(`/api/hr/profiles/${id}`, fields),
  create: (body: Record<string, unknown>) =>
    http.post<{ user_id: string; member_code: string; password: string }>("/api/hr/profiles", body),
  bulk: (userIds: string[], fields: Record<string, unknown>) =>
    http.post<{ changed: number }>("/api/hr/profiles/bulk", { user_ids: userIds, fields }),
  addContract: (id: string, body: Record<string, unknown>) =>
    http.post<{ id: string }>(`/api/hr/profiles/${id}/contracts`, body),
  editContract: (id: string, contractId: string, body: Record<string, unknown>) =>
    http.patch<{ id: string }>(`/api/hr/profiles/${id}/contracts/${contractId}`, body),
  deleteContract: (contractId: string) => http.del<void>(`/api/hr/contracts/${contractId}`),
  renew: (userIds: string[], typeCode: string, startOn: string | null, signed: boolean) =>
    http.post<{ created: number }>("/api/hr/contracts/renew", {
      user_ids: userIds,
      type_code: typeCode,
      start_on: startOn,
      signed,
    }),
  deleteDocument: (documentId: string) => http.del<void>(`/api/hr/documents/${documentId}`),
  requests: (status?: string, mine = false) =>
    http.get<{ items: HrRequest[]; can_decide: boolean }>(
      `/api/hr/requests${query({ status, mine: mine ? "true" : undefined } as HrFilters)}`,
    ),
  submitRequest: (field: string, value: string) =>
    http.post<{ id: string; status: string }>("/api/hr/requests", { field, value }),
  decide: (id: string, approve: boolean, note?: string) =>
    http.post<{ id: string; status: string }>(`/api/hr/requests/${id}/decide`, { approve, note }),
  org: () => http.get<{ departments: HrOrgDepartment[] }>("/api/hr/org"),
  saveSettings: (body: { contract_warn_days: number; required_documents: string[] }) =>
    http.put<{ contract_warn_days: number; required_documents: string[] }>("/api/hr/settings", body),
  contractTypes: () =>
    http.get<{ types: { id: string; code: string; name: string; months: number; active: boolean }[] }>(
      "/api/hr/contract-types",
    ),
  createContractType: (body: { code: string; name: string; months: number }) =>
    http.post<{ id: string; code: string }>("/api/hr/contract-types", body),
  updateContractType: (id: string, body: Record<string, unknown>) =>
    http.patch<{ id: string; code: string }>(`/api/hr/contract-types/${id}`, body),
};

/** Upload a document: the raw bytes are the body (≤ 10 MB). */
export async function uploadHrDocument(userId: string, kind: string, file: File): Promise<void> {
  const response = await fetch(
    `/api/hr/profiles/${userId}/documents?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(file.name)}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/octet-stream" },
      body: file,
    },
  );
  if (!response.ok) {
    let message = "Không tải lên được tệp.";
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // keep the generic sentence
    }
    throw new Error(message);
  }
}

export const documentUrl = (documentId: string) => `/api/hr/documents/${documentId}`;
export const exportUrl = "/api/hr/export.xlsx";

/** 9000000 -> "9.000.000 ₫". */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat("vi-VN").format(value)} ₫`;
}
