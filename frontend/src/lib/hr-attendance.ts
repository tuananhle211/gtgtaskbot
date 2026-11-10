/**
 * "Nhân sự" đợt 3-4 (0100): đơn từ (nghỉ / đi muộn / về sớm), phép năm, ca,
 * ngày lễ, chấm công. Mirrors `src/meobot/api/routers/hr_attendance.py`.
 */
import { http } from "@/lib/api";

export type Choice = { code: string; label: string };
export type PersonRef = { id: string; name: string; member_code?: string | null };

export type AttendanceAccess = {
  timesheet: boolean;
  lists: boolean;
  awaiting: number;
  locked: string[];
  choices: {
    kinds: Choice[];
    parts: Choice[];
    statuses: Choice[];
    levels: Choice[];
    branches: Choice[];
    weekdays: string[];
  };
};

export type LeaveType = {
  id: string;
  code: string;
  name: string;
  paid: boolean;
  annual: boolean;
  max_days: number | null;
  symbol: string;
  active: boolean;
};

export type AnnualBalance = {
  year: number;
  entitlement: number;
  seniority_days: number;
  months: number;
  earned: number;
  adjustment: number;
  used: number;
  pending: number;
  remaining: number;
  year_remaining: number;
};

export type ChainStep = { level: string; label: string; people: { id: string; name: string }[] };

export type ShiftWeek = Record<string, { morning?: [string, string]; afternoon?: [string, string] }>;

export type AbsenceForm = {
  leave_types: LeaveType[];
  chain: Record<"LEAVE" | "LATE" | "EARLY", ChainStep[]>;
  balance: AnnualBalance;
  shift: { name: string; grace_minutes: number; week: ShiftWeek };
};

export type AbsenceStep = {
  id: string;
  position: number;
  level: string;
  level_label: string;
  status: "WAITING" | "PENDING" | "APPROVED" | "REJECTED" | "SKIPPED" | "CLOSED";
  status_label: string;
  approvers: PersonRef[];
  decided_by: PersonRef | null;
  decided_at: string | null;
  note: string | null;
};

export type Absence = {
  id: string;
  user: PersonRef | null;
  departments: string[];
  kind: "LEAVE" | "LATE" | "EARLY";
  kind_label: string;
  leave_type: LeaveType | null;
  start_on: string;
  end_on: string;
  part: "FULL" | "MORNING" | "AFTERNOON";
  part_label: string;
  at_time: string | null;
  minutes: number | null;
  days: number | null;
  reason: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "CANCELLED";
  status_label: string;
  created_at: string;
  decided_at: string | null;
  cancelled_by: PersonRef | null;
  cancel_note: string | null;
  current_level: string | null;
  current_label: string | null;
  current_step_id: string | null;
  waiting_on: PersonRef[];
  steps: AbsenceStep[];
  /** The caller sent it. */
  own: boolean;
  can_decide: boolean;
  can_cancel: boolean;
  balance?: AnnualBalance;
};

export type BalanceRow = AnnualBalance & {
  user_id: string;
  name: string;
  member_code: string | null;
  departments: string[];
  joined_on: string | null;
  official_on: string | null;
};

export type Adjustment = {
  id: string;
  days: number;
  note: string;
  created_at: string;
  created_by: string | null;
};

export type Shift = {
  id: string;
  name: string;
  week: ShiftWeek;
  grace_minutes: number;
  branches: string[];
  department_ids: string[];
  is_default: boolean;
  active: boolean;
};

export type Holiday = { id: string; day: string; name: string };

export type DayCell = {
  day: string;
  symbol: string;
  in: string | null;
  out: string | null;
  late: number;
  early: number;
  missing: boolean;
  edited: boolean;
};

export type DayDetail = DayCell & {
  weekday: string;
  expected: number;
  worked: number;
  paid_leave: number;
  unpaid_leave: number;
  holiday: number;
  absent: number;
  late_raw: number;
  early_raw: number;
  late_excused: number;
  early_excused: number;
  leave_names: string[];
  note: string | null;
};

export type Totals = {
  standard: number;
  worked: number;
  paid_leave: number;
  unpaid_leave: number;
  holiday: number;
  absent: number;
  payable: number;
  late_count: number;
  late_minutes: number;
  early_count: number;
  early_minutes: number;
  missing: number;
  leave_by_type?: Record<string, number>;
};

export type MonthSheet = {
  month: string;
  locked: boolean;
  days: { day: string; weekday: string; holiday: string | null }[];
  people: {
    user_id: string;
    name: string;
    member_code: string | null;
    departments: string[];
    shift: string;
    totals: Totals;
    days: DayCell[];
  }[];
  symbols: { symbol: string; label: string }[];
  can_manage: boolean;
};

export type PersonSheet = {
  month: string;
  locked: boolean;
  user_id: string;
  name: string;
  member_code: string | null;
  departments: string[];
  shift: string;
  totals: Totals;
  days: DayDetail[];
  can_manage: boolean;
};

export type ImportReport = {
  sheets: { name: string; read: boolean; reason: string | null; rows: number; bad_rows: number }[];
  problems: string[];
  rows: number;
  people: number;
  days: number;
  from: string | null;
  to: string | null;
  months: string[];
  locked: string[];
  new_days: number;
  updated_days: number;
  kept_manual: number;
  unmatched: { code: string; rows: number }[];
  committed: boolean;
};

export type AbsenceBody = {
  kind: "LEAVE" | "LATE" | "EARLY";
  leave_type?: string | null;
  start_on: string;
  end_on?: string | null;
  part?: "FULL" | "MORNING" | "AFTERNOON";
  at_time?: string | null;
  reason: string;
};

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const attendanceApi = {
  access: () => http.get<AttendanceAccess>("/api/hr/attendance/access"),
  form: () => http.get<AbsenceForm>("/api/hr/absences/form"),
  absences: (filters: {
    tab: "mine" | "awaiting" | "all";
    status?: string;
    kind?: string;
    month?: string;
    q?: string;
  }) => http.get<{ items: Absence[]; awaiting: number }>(`/api/hr/absences${query(filters)}`),
  absence: (id: string) => http.get<Absence>(`/api/hr/absences/${id}`),
  submit: (body: AbsenceBody) => http.post<Absence>("/api/hr/absences", body),
  decide: (id: string, approve: boolean, stepId: string | null, note?: string) =>
    http.post<Absence>(`/api/hr/absences/${id}/decide`, { approve, step_id: stepId, note }),
  cancel: (id: string, note?: string) =>
    http.post<Absence>(`/api/hr/absences/${id}/cancel`, { note }),
  leaveTypes: () => http.get<{ items: LeaveType[]; can_edit: boolean }>("/api/hr/leave-types"),
  addLeaveType: (body: Partial<LeaveType>) => http.post<{ id: string }>("/api/hr/leave-types", body),
  editLeaveType: (id: string, body: Partial<LeaveType>) =>
    http.patch<{ id: string }>(`/api/hr/leave-types/${id}`, body),
  myLeave: (year?: number) =>
    http.get<{ balance: AnnualBalance; adjustments: Adjustment[] }>(`/api/hr/leave/me${query({ year })}`),
  balances: (filters: { year?: number; department_id?: string; q?: string }) =>
    http.get<{ year: number; items: BalanceRow[]; can_adjust: boolean }>(
      `/api/hr/leave/balances${query(filters)}`,
    ),
  adjustments: (userId: string, year: number) =>
    http.get<{ items: Adjustment[] }>(`/api/hr/leave/adjustments${query({ user_id: userId, year })}`),
  adjust: (body: { user_id: string; year: number; days: number; note: string }) =>
    http.post<{ id: string }>("/api/hr/leave/adjustments", body),
  deleteAdjustment: (id: string) => http.del<void>(`/api/hr/leave/adjustments/${id}`),
  shifts: () => http.get<{ items: Shift[]; can_edit: boolean }>("/api/hr/shifts"),
  addShift: (body: Partial<Shift>) => http.post<Shift>("/api/hr/shifts", body),
  editShift: (id: string, body: Partial<Shift>) => http.patch<Shift>(`/api/hr/shifts/${id}`, body),
  holidays: (year: number) =>
    http.get<{ year: number; items: Holiday[]; can_edit: boolean }>(`/api/hr/holidays${query({ year })}`),
  addHolidays: (body: { start: string; end: string | null; name: string }) =>
    http.post<{ days: number }>("/api/hr/holidays", body),
  deleteHoliday: (id: string) => http.del<void>(`/api/hr/holidays/${id}`),
  month: (filters: { month: string; department_id?: string; q?: string }) =>
    http.get<MonthSheet>(`/api/hr/timesheet${query(filters)}`),
  person: (userId: string, month: string) =>
    http.get<PersonSheet>(`/api/hr/timesheet/people/${userId}${query({ month })}`),
  lock: (month: string, locked: boolean) =>
    http.put<{ locked: string[] }>("/api/hr/timesheet/lock", { month, locked }),
  editDay: (userId: string, day: string, body: { check_in: string | null; check_out: string | null; note: string }) =>
    http.put<PersonSheet>(`/api/hr/attendance/${userId}/${day}`, body),
};

export const timesheetExportUrl = (month: string, departmentId?: string) =>
  `/api/hr/timesheet/export.xlsx${query({ month, department_id: departmentId })}`;

/** Preview (`commit` false) or load the time clock's workbook: the raw bytes are the body. */
export async function importAttendance(file: File, commit: boolean): Promise<ImportReport> {
  const response = await fetch(`/api/hr/attendance/import?commit=${commit ? "true" : "false"}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/octet-stream" },
    body: file,
  });
  const body = (await response.json().catch(() => ({}))) as {
    data?: ImportReport;
    error?: { message?: string };
  } & Partial<ImportReport>;
  if (!response.ok) throw new Error(body.error?.message ?? "Không nhập được file.");
  return (body.data ?? body) as ImportReport;
}

/** "2026-10" for a date (Vietnam time is close enough for a month picker). */
export function monthOf(date: Date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(month: string, step: number): string {
  const [year, value] = month.split("-").map(Number);
  const moved = new Date(year, value - 1 + step, 1);
  return monthOf(moved);
}

/** 2.5 -> "2,5". */
export function days(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value);
}

/** The pill tone of a request's status. */
export function statusTone(status: Absence["status"]): "warn" | "good" | "bad" | "neutral" {
  if (status === "PENDING") return "warn";
  if (status === "APPROVED") return "good";
  if (status === "REJECTED") return "bad";
  return "neutral";
}

/** "Nghỉ phép năm 09/11 - 10/11" / "Đi muộn 09/11, đến 09:00 (60 phút)". */
export function describeAbsence(item: Absence): string {
  const day = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`;
  if (item.kind === "LEAVE") {
    const when = item.start_on === item.end_on ? day(item.start_on) : `${day(item.start_on)} - ${day(item.end_on)}`;
    const part = item.part === "FULL" ? "" : ` (${item.part_label.toLowerCase()})`;
    return `${item.leave_type?.name ?? "Nghỉ"} ${when}${part}`;
  }
  const verb = item.kind === "LATE" ? "đến" : "về";
  return `${item.kind_label} ${day(item.start_on)}, ${verb} ${item.at_time ?? ""} (${item.minutes ?? 0} phút)`;
}
