/**
 * "Nhân sự" (0096): one's own profile (all tabs), a head's view of somebody
 * else (no sensitive tab), the list's bulk edit, and a change request.
 */
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HrProfileView } from "@/components/hr-profile";
import HrProfilesPage from "@/app/hr/profiles/page";
import { renderWithQuery, stubFetch } from "./helpers";

const ACCESS = {
  lists: true,
  overview: true,
  edit: true,
  sensitive: true,
  export: true,
  choices: {
    branches: [{ code: "CS_SG", label: "CS - Sài Gòn" }],
    statuses: [
      { code: "WORKING", label: "Đang làm" },
      { code: "MATERNITY", label: "Nghỉ thai sản" },
    ],
    genders: [{ code: "FEMALE", label: "Nữ" }],
    marital: ["Độc thân"],
    work_forms: ["Toàn thời gian"],
    salary_types: ["Lương tháng"],
    document_kinds: [{ code: "CCCD", label: "CCCD / CMND" }],
    request_fields: [{ code: "phone", label: "Điện thoại" }],
    contract_types: [{ code: "KXD", name: "HĐ không xác định thời hạn", months: 0 }],
    contract_states: [],
    max_document_mb: 10,
  },
  settings: { contract_warn_days: 30, required_documents: ["CCCD"] },
};

const ROW = {
  user_id: "u1",
  member_code: "D050PHUONG",
  full_name: "Phạm Thị Phương",
  job_title: "Trưởng nhóm Telesales",
  departments: [{ department_id: "d1", code: "TELE", name: "Phòng Telesales", position: "MEMBER" }],
  branch: "VP_HN",
  branch_label: "VP - Hà Nội",
  legal_entity_id: null,
  legal_entity_code: "GPI",
  employment_status: "WORKING",
  employment_status_label: "Đang làm",
  active: true,
  telegram_linked: false,
  gender: "FEMALE",
  gender_label: "Nữ",
  grade: "T3",
  work_email: "phuong@apmtech.vn",
  joined_on: "2022-11-03",
  official_on: "2022-12-03",
  seniority: "4 năm",
  contract: {
    type_name: "HĐ không xác định thời hạn",
    number: "0309/2025",
    end_on: null,
    state: "ACTIVE",
    state_label: "Đang hiệu lực",
    days_left: null,
  },
  contract_warning: false,
  missing_documents: ["CCCD"],
  sensitive: true,
};

const PROFILE = {
  ...ROW,
  contracts: [],
  work: {
    grade: "T3",
    work_form: null,
    joined_on: "2022-11-03",
    official_on: "2022-12-03",
    seniority: "4 năm",
    left_on: null,
    leave_reason: null,
    work_email: "phuong@apmtech.vn",
    note: null,
  },
  can_edit: false,
  personal: {
    gender: "FEMALE",
    birth_date: "2001-12-30",
    age: 24,
    birth_place: null,
    nationality: null,
    ethnicity: null,
    religion: null,
    marital_status: null,
    id_number: "038301009918",
    id_issued_on: null,
    id_issued_by: null,
    phone: "0376618533",
    personal_email: null,
    drive_email: null,
    permanent_address: null,
    current_address: null,
    bank_name: null,
    bank_account: null,
    bank_holder: null,
    secret_unreadable: false,
  },
  pay: { base_salary: 9000000, allowance: null, total: 9000000, salary_type: null, salary_note: null },
  insurance: {
    social_insurance_no: null,
    social_insurance_from: null,
    insurance_salary: null,
    health_insurance_no: null,
    tax_code: null,
    dependants: null,
  },
  documents: [],
  requests: [],
};

const ENTITIES = { entities: [], branches: [], can_edit: false };

describe("Hồ sơ của tôi", () => {
  it("shows every tab of one's own profile and sends a change request", async () => {
    const stub = stubFetch([
      { match: "/api/hr/requests", method: "POST", body: { id: "r1", status: "PENDING" } },
      { match: "/api/hr/access", body: { ...ACCESS, lists: false, overview: false, edit: false, sensitive: false } },
      { match: "/api/hr/profiles/me", body: PROFILE },
      { match: "/api/legal-entities", body: ENTITIES },
    ]);
    renderWithQuery(<HrProfileView userId="me" me />);
    expect(await screen.findByRole("heading", { name: "Phạm Thị Phương" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual([
      "Thông tin công việc",
      "Thông tin cá nhân",
      "Hợp đồng lao động",
      "Lương & phụ cấp",
      "Bảo hiểm & thuế",
      "Tài liệu đính kèm",
    ]);
    expect(screen.getByText("Chưa liên kết Telegram")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Lương & phụ cấp" }));
    expect(screen.getAllByText(/9\.000\.000/).length).toBeGreaterThan(0);
    // No edit button for a person on their own profile.
    expect(screen.queryByRole("button", { name: "Sửa" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Yêu cầu sửa" }));
    const dialog = screen.getByRole("dialog", { name: "Yêu cầu sửa thông tin" });
    expect(within(dialog).getByText("0376618533")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Giá trị mới"), "0988000111");
    await userEvent.click(within(dialog).getByRole("button", { name: "Gửi yêu cầu" }));
    const accept = document.querySelector<HTMLElement>("[data-confirm-accept]");
    await userEvent.click(accept as HTMLElement);
    await waitFor(() => {
      const sent = (stub as unknown as { calls: { url: string; body: unknown }[] }).calls.find(
        (call) => call.url === "/api/hr/requests",
      );
      expect(sent?.body).toEqual({ field: "phone", value: "0988000111" });
    });
  });

  it("hides the sensitive tabs from a head looking at somebody else", async () => {
    stubFetch([
      { match: "/api/hr/access", body: { ...ACCESS, edit: false, sensitive: false, overview: false } },
      {
        match: "/api/hr/profiles/u1",
        body: { ...PROFILE, personal: null, pay: null, insurance: null, documents: null },
      },
      { match: "/api/legal-entities", body: ENTITIES },
    ]);
    renderWithQuery(<HrProfileView userId="u1" />);
    await screen.findByRole("heading", { name: "Phạm Thị Phương" });
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Thông tin công việc",
      "Hợp đồng lao động",
    ]);
    expect(screen.queryByText("038301009918")).not.toBeInTheDocument();
  });
});

describe("Quản lý hồ sơ", () => {
  it("ticks rows and edits them together", async () => {
    const stub = stubFetch([
      { match: "/api/hr/profiles/bulk", method: "POST", body: { changed: 1 } },
      { match: "/api/hr/access", body: ACCESS },
      { match: "/api/hr/profiles", body: { total: 1, page: 1, page_size: 50, items: [ROW] } },
      { match: "/api/departments", body: { departments: [] } },
      { match: "/api/legal-entities", body: ENTITIES },
    ]);
    renderWithQuery(<HrProfilesPage />);
    expect(await screen.findByText("Phạm Thị Phương")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Xuất Excel" })).toHaveAttribute("href", "/api/hr/export.xlsx");
    await userEvent.click(screen.getByRole("checkbox", { name: "Chọn Phạm Thị Phương" }));
    expect(screen.getByText("Đã chọn 1 hồ sơ")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sửa hàng loạt" }));
    const dialog = screen.getByRole("dialog", { name: "Sửa hàng loạt" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Trạng thái"), "MATERNITY");
    await userEvent.click(within(dialog).getByRole("button", { name: "Áp dụng" }));
    await userEvent.click(document.querySelector<HTMLElement>("[data-confirm-accept]") as HTMLElement);
    await waitFor(() => {
      const sent = (stub as unknown as { calls: { url: string; body: unknown }[] }).calls.find(
        (call) => call.url === "/api/hr/profiles/bulk",
      );
      expect(sent?.body).toEqual({ user_ids: ["u1"], fields: { employment_status: "MATERNITY" } });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Đã sửa 1 hồ sơ.");
  });
});
