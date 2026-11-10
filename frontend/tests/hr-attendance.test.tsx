/**
 * "Nhân sự" đợt 3-4 (0100): a leave request (form, chain preview, sent), the
 * approver's "Duyệt", the timesheet grid and the time clock's import preview.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LeavePage from "@/app/hr/leave/page";
import TimesheetPage from "@/app/hr/timesheet/page";
import { renderWithQuery, stubFetch } from "./helpers";

const params = { value: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/hr/leave",
  useSearchParams: () => params.value,
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter((call) => call.method === method && call.url.includes(fragment));

const ACCESS = {
  timesheet: true,
  lists: true,
  awaiting: 1,
  locked: [],
  choices: {
    kinds: [],
    parts: [],
    statuses: [{ code: "PENDING", label: "Đang duyệt" }],
    levels: [],
    branches: [{ code: "VP_HN", label: "VP - Hà Nội" }],
    weekdays: [],
  },
};
const BALANCE = {
  year: 2026,
  entitlement: 13,
  seniority_days: 1,
  months: 11,
  earned: 11.9,
  adjustment: 0,
  used: 2,
  pending: 0,
  remaining: 9.9,
  year_remaining: 11,
};
const FORM = {
  leave_types: [
    { id: "t1", code: "ANNUAL", name: "Nghỉ phép năm", paid: true, annual: true, max_days: null, symbol: "P", active: true },
    { id: "t2", code: "UNPAID", name: "Nghỉ không lương", paid: false, annual: false, max_days: null, symbol: "KL", active: true },
  ],
  chain: {
    LEAVE: [
      { level: "MANAGER", label: "Trưởng phòng", people: [{ id: "h", name: "Đỗ Thị Chinh" }] },
      { level: "BLOCK", label: "Giám đốc khối", people: [{ id: "b", name: "Nguyễn Thị Tuyết" }] },
      { level: "DIRECTOR", label: "Giám đốc", people: [{ id: "d", name: "Trần Hoàng Liên" }] },
      { level: "HR", label: "HCNS xác nhận", people: [{ id: "n", name: "Ngô Tuyết Nhung" }] },
    ],
    LATE: [
      { level: "MANAGER", label: "Trưởng phòng", people: [{ id: "h", name: "Đỗ Thị Chinh" }] },
      { level: "HR", label: "HCNS xác nhận", people: [{ id: "n", name: "Ngô Tuyết Nhung" }] },
    ],
    EARLY: [],
  },
  balance: BALANCE,
  shift: { name: "Hành chính", grace_minutes: 5, week: {} },
};
const REQUEST = {
  id: "a1",
  user: { id: "s", name: "Phạm Hồng Sơn", member_code: "D094SON" },
  departments: ["Phòng QHKH"],
  kind: "LEAVE",
  kind_label: "Nghỉ",
  leave_type: FORM.leave_types[0],
  start_on: "2026-11-09",
  end_on: "2026-11-10",
  part: "FULL",
  part_label: "Cả ngày",
  at_time: null,
  minutes: null,
  days: 2,
  reason: "Việc gia đình",
  status: "PENDING",
  status_label: "Đang duyệt",
  created_at: "2026-11-05T02:00:00Z",
  decided_at: null,
  cancelled_by: null,
  cancel_note: null,
  current_level: "MANAGER",
  current_label: "Trưởng phòng",
  current_step_id: "st1",
  waiting_on: [{ id: "h", name: "Đỗ Thị Chinh" }],
  steps: [],
  own: false,
  can_decide: true,
  can_cancel: false,
};

describe("Đơn từ", () => {
  it("sends a leave request with the chain shown", async () => {
    params.value = new URLSearchParams();
    const stub = stubFetch([
      { match: "/api/hr/attendance/access", body: ACCESS },
      { match: "/api/hr/leave/me", body: { balance: BALANCE, adjustments: [] } },
      { match: "/api/hr/absences/form", body: FORM },
      { match: "/api/hr/absences", method: "POST", body: { ...REQUEST, own: true, can_decide: false } },
      { match: "/api/hr/absences?tab=mine", body: { items: [], awaiting: 1 } },
    ]);
    renderWithQuery(<LeavePage />);
    expect(await screen.findByTestId("balance-card")).toHaveTextContent("9,9");
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo đơn" }));
    const dialog = await screen.findByRole("dialog", { name: "Tạo đơn" });
    const chain = await within(dialog).findByTestId("chain-preview");
    expect(chain).toHaveTextContent("Trưởng phòng: Đỗ Thị Chinh");
    expect(chain).toHaveTextContent("Giám đốc khối: Nguyễn Thị Tuyết");
    expect(chain).toHaveTextContent("HCNS xác nhận: Ngô Tuyết Nhung");
    expect(within(dialog).getByText(/Phép năm còn lại/)).toHaveTextContent("9,9");
    const send = within(dialog).getByRole("button", { name: "Gửi đơn" });
    expect(send).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Từ ngày"), "09112026");
    await userEvent.type(within(dialog).getByLabelText("Đến ngày"), "10112026");
    await userEvent.type(within(dialog).getByLabelText("Lý do"), "Việc gia đình");
    await userEvent.click(send);
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/hr/absences")[0]?.body).toEqual({
        kind: "LEAVE",
        leave_type: "ANNUAL",
        start_on: "2026-11-09",
        end_on: "2026-11-10",
        part: "FULL",
        at_time: null,
        reason: "Việc gia đình",
      }),
    );
    expect(await screen.findByRole("dialog", { name: "Đã gửi đơn" })).toHaveTextContent("Đỗ Thị Chinh");
  });

  it("a late request asks for the time, and the approver approves", async () => {
    params.value = new URLSearchParams("tab=awaiting");
    const stub = stubFetch([
      { match: "/api/hr/attendance/access", body: ACCESS },
      { match: "/api/hr/leave/me", body: { balance: BALANCE, adjustments: [] } },
      { match: "/api/hr/absences/a1/decide", method: "POST", body: { ...REQUEST, can_decide: false } },
      { match: "/api/hr/absences?tab=awaiting", body: { items: [REQUEST], awaiting: 1 } },
    ]);
    renderWithQuery(<LeavePage />);
    const row = await screen.findByTestId("absence-row");
    expect(row).toHaveTextContent("Phạm Hồng Sơn");
    expect(row).toHaveTextContent("Chờ Trưởng phòng: Đỗ Thị Chinh");
    await userEvent.click(within(row).getByRole("button", { name: "Duyệt" }));
    await userEvent.click(document.querySelector<HTMLElement>("[data-confirm-accept]") as HTMLElement);
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/hr/absences/a1/decide")[0]?.body).toEqual({
        approve: true,
        step_id: "st1",
      }),
    );
  });
});

describe("Bảng công", () => {
  it("draws the month and previews the time clock's file", async () => {
    params.value = new URLSearchParams("month=2026-10");
    stubFetch([
      { match: "/api/hr/attendance/access", body: ACCESS },
      { match: "/api/hr/access", body: { choices: { departments: [] } } },
      {
        match: "/api/hr/timesheet?month=2026-10",
        body: {
          month: "2026-10",
          locked: false,
          days: [
            { day: "2026-10-01", weekday: "T5", holiday: null },
            { day: "2026-10-02", weekday: "T6", holiday: null },
          ],
          people: [
            {
              user_id: "s",
              name: "Phạm Hồng Sơn",
              member_code: "D094SON",
              departments: ["Phòng HCNS"],
              shift: "Hành chính",
              totals: {
                standard: 2,
                worked: 1,
                paid_leave: 0,
                unpaid_leave: 0,
                holiday: 0,
                absent: 0,
                payable: 1,
                late_count: 1,
                late_minutes: 13,
                early_count: 0,
                early_minutes: 0,
                missing: 1,
              },
              days: [
                { day: "2026-10-01", symbol: "X", in: "08:13", out: "18:00", late: 13, early: 0, missing: false, edited: false },
                { day: "2026-10-02", symbol: "!", in: "08:00", out: null, late: 0, early: 0, missing: true, edited: false },
              ],
            },
          ],
          symbols: [{ symbol: "X", label: "Đi làm đủ" }],
          can_manage: true,
        },
      },
    ]);
    renderWithQuery(<TimesheetPage />);
    const row = await screen.findByTestId("timesheet-row");
    expect(row).toHaveTextContent("Phạm Hồng Sơn");
    expect(row).toHaveTextContent("1 (13′)");
    expect(screen.getByRole("link", { name: "Xuất Excel" })).toHaveAttribute(
      "href",
      "/api/hr/timesheet/export.xlsx?month=2026-10",
    );

    // The import: preview first, nothing written until "Nhập".
    const report = {
      sheets: [
        { name: "MCC CBNV", read: false, reason: "Không có cột Mã nhân viên / Ngày / Vào / Ra", rows: 0, bad_rows: 0 },
        { name: "VP - T10", read: true, reason: null, rows: 3816, bad_rows: 0 },
      ],
      problems: [],
      rows: 3816,
      people: 120,
      days: 3720,
      from: "2026-10-01",
      to: "2026-10-31",
      months: ["2026-10"],
      locked: [],
      new_days: 3720,
      updated_days: 0,
      kept_manual: 0,
      unmatched: [{ code: "D042THAP", rows: 31 }],
      committed: false,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(report), { status: 200 }));
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", ((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("/api/hr/attendance/import") ? fetchMock() : original(input, init)) as typeof fetch);
    await userEvent.click(screen.getByRole("button", { name: "Nhập file chấm công" }));
    const dialog = await screen.findByRole("dialog", { name: "Nhập file chấm công" });
    const file = new File([new Uint8Array([1, 2, 3])], "cham-cong.xlsx");
    await userEvent.upload(within(dialog).getByLabelText("Chọn file chấm công"), file);
    const shown = await within(dialog).findByTestId("import-report");
    expect(shown).toHaveTextContent("VP - T10: 3816 dòng");
    expect(shown).toHaveTextContent("1 mã nhân viên không có trên APM");
    expect(within(dialog).getByRole("button", { name: "Nhập vào bảng công" })).toBeEnabled();
  });
});
