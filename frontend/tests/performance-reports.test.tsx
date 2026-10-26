/**
 * The points reports ("Hiệu suất", docs/perf/PERFORMANCE_REPORTS_PLAN.md):
 * "Điểm của tôi", "Phòng của tôi" (a Trưởng phòng's direct reports),
 * "Toàn công ty", and the nav branch that follows `performance` on
 * `/api/units/me`.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Shell } from "@/components/shell";
import MyPerformancePage from "@/app/performance/me/page";
import TeamPerformancePage from "@/app/performance/team/page";
import CompanyPerformancePage from "@/app/performance/company/page";
import CoefficientsPage from "@/app/performance/coefficients/page";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/performance/me",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

const FIGURES = {
  points: 240,
  steps_done: 1,
  pending_points: 480,
  pending_count: 1,
  returns_fault: 1,
  returns_other: 0,
  returns_codes: 2,
  late_count: 0,
  with_deadline: 1,
  on_time_rate: 1,
  previous_points: 100,
};

const D3 = {
  code_id: "c-d3",
  code: "D3",
  name: "Video 90 giây - 3 phút",
  quantity: 1,
  form: "NEW",
  form_label: "Làm mới",
  form_pct: 100,
  points: 240,
};

const PERSON = {
  user_id: "u-editor",
  name: "Editor C",
  month: "2026-10",
  departments: ["Media Dựng"],
  figures: FIGURES,
  by_phase: { DUNG: 240 },
  by_week: [0, 240, 0, 0, 0],
  lines: [
    {
      node_id: "n1",
      order_id: "o1",
      order_code: "K7Q2M",
      order_title: "Video ra mắt",
      phase: "DUNG",
      phase_label: "Dựng",
      items: [D3],
      points: 240,
      status: "EARNED",
      at: "2026-10-08T03:00:00Z",
    },
    {
      node_id: "n2",
      order_id: "o2",
      order_code: "P4X9A",
      order_title: "Video thứ hai",
      phase: "DUNG",
      phase_label: "Dựng",
      items: [{ ...D3, code: "D4", code_id: "c-d4", points: 480 }],
      points: 480,
      status: "IN_PROGRESS",
      at: null,
    },
  ],
};

const UNITS_ME = { units: [], default_unit: null, can_view_all: false, can_admin: [], can_view_profiles: true };

describe("Điểm của tôi", () => {
  it("shows the month's total, the pending points apart, and the counted steps first", async () => {
    stubFetch([
      { match: "/api/performance/people/me", body: PERSON },
      { match: "/api/units/me", body: UNITS_ME },
    ]);
    renderWithQuery(<MyPerformancePage />);
    const tiles = await screen.findByRole("list", { name: "Điểm trong tháng" });
    expect(within(tiles).getByText("Tổng điểm").parentElement).toHaveTextContent("240");
    expect(within(tiles).getByText("Tổng điểm").parentElement).toHaveTextContent("Tháng trước 100 · +140");
    expect(within(tiles).getByText("Đang làm (dự kiến)").parentElement).toHaveTextContent(
      "không cộng vào tổng",
    );
    expect(within(tiles).getByText("Bị trả").parentElement).toHaveTextContent("Mã chưa hợp lý 2");
    // Counted steps first; the step under way behind "Đang chờ".
    expect(screen.getByRole("link", { name: "K7Q2M" })).toHaveAttribute("href", "/tasks/K7Q2M");
    expect(screen.queryByRole("link", { name: "P4X9A" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Đang chờ/ }));
    expect(screen.getByRole("link", { name: "P4X9A" })).toBeInTheDocument();
    expect(screen.getByText("Đang làm", { selector: "span" })).toBeInTheDocument();
    // The weeks and the phases.
    expect(screen.getByRole("listitem", { name: "Ngày 8-14: 240 điểm" })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Điểm theo bước" })).getByText("Dựng").parentElement?.parentElement).toHaveTextContent("100%");
  });
});

const TEAM = {
  month: "2026-10",
  department_id: null,
  departments: [{ id: "d-dung", name: "Media Dựng" }],
  rows: [{ user_id: "u-editor", name: "Editor C", departments: ["Media Dựng"], figures: FIGURES }],
  totals: FIGURES,
  trend: [
    { month: "2026-09", points: 100, by_department: {} },
    { month: "2026-10", points: 240, by_department: {} },
  ],
  view_all: false,
  export: false,
};

describe("Phòng của tôi", () => {
  it("lists the head's direct reports with links to their pages, and no Excel without the right", async () => {
    stubFetch([
      { match: "/api/performance/team", body: TEAM },
      { match: "/api/units/me", body: UNITS_ME },
    ]);
    renderWithQuery(<TeamPerformancePage />);
    const table = await screen.findByRole("region", { name: "Điểm từng người" });
    expect(await within(table).findByRole("link", { name: "Editor C" })).toHaveAttribute("href", "/people/u-editor");
    expect(within(table).getByText(/Tổng · 1 người/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Xuất Excel/ })).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "T10: 240 điểm" })).toBeInTheDocument();
  });

  it("offers the Excel file to whoever may export", async () => {
    stubFetch([
      { match: "/api/performance/team", body: { ...TEAM, view_all: true, export: true } },
      { match: "/api/units/me", body: UNITS_ME },
    ]);
    renderWithQuery(<TeamPerformancePage />);
    expect(await screen.findByRole("link", { name: /Xuất Excel/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/performance/export.xlsx"),
    );
  });

  it("draws a refusal as a sentence", async () => {
    stubFetch([
      {
        match: "/api/performance/team",
        status: 403,
        body: { code: "performance_forbidden", message: "Chỉ Trưởng phòng xem được điểm của phòng.", details: {} },
      },
    ]);
    renderWithQuery(<TeamPerformancePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Không xem được trang này");
  });
});

describe("Toàn công ty", () => {
  it("shows the departments, the top scorers and the codes", async () => {
    stubFetch([
      {
        match: "/api/performance/overview",
        body: {
          month: "2026-10",
          points: 360,
          steps_done: 2,
          people: 2,
          departments: [
            { department_id: "d-dung", name: "Media Dựng", people: 1, points: 240, steps_done: 1, average: 240, late_count: 0, on_time_rate: 1 },
            { department_id: "d-bt", name: "Biên tập", people: 1, points: 120, steps_done: 1, average: 120, late_count: 0, on_time_rate: null },
          ],
          top: [{ user_id: "u-editor", name: "Editor C", departments: ["Media Dựng"], figures: FIGURES }],
          codes: [{ phase: "DUNG", code: "D3", name: "Video 90 giây - 3 phút", quantity: 1, points: 240 }],
          trend: [{ month: "2026-10", points: 360, by_department: {} }],
          export: true,
        },
      },
      { match: "/api/units/me", body: UNITS_ME },
    ]);
    renderWithQuery(<CompanyPerformancePage />);
    const departments = await screen.findByRole("region", { name: "Các phòng" });
    expect(within(departments).getByRole("link", { name: "Media Dựng" })).toHaveAttribute(
      "href",
      expect.stringContaining("department=d-dung"),
    );
    expect(screen.getByRole("region", { name: "10 người điểm cao nhất" })).toHaveTextContent("Editor C");
    expect(screen.getByRole("region", { name: "Mã task nhiều điểm nhất" })).toHaveTextContent("D3");
    expect(screen.getByRole("link", { name: /Xuất Excel/ })).toBeInTheDocument();
  });
});

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

describe("the Hiệu suất branch of the nav", () => {
  const branchLinks = async (performance: unknown, count: number) => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: { ...UNITS_ME, performance } },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const branch = await screen.findByRole("group", { name: "Hiệu suất" });
    await waitFor(() => expect(within(branch).getAllByRole("link")).toHaveLength(count));
    return within(branch)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
  };

  it("gives everybody their own month", async () => {
    expect(await branchLinks({ view_team: false, view_all: false, export: false }, 1)).toEqual(["/performance/me"]);
  });

  it("adds the team for a Trưởng phòng and the company for a company viewer", async () => {
    expect(await branchLinks({ view_team: true, view_all: true, export: true }, 3)).toEqual([
      "/performance/me",
      "/performance/team",
      "/performance/company",
    ]);
  });

  it('adds "Hệ số điểm" for the OWNER', async () => {
    expect(
      await branchLinks({ view_team: true, view_all: true, export: true, manage_coefficients: true }, 4),
    ).toContain("/performance/coefficients");
  });
});

const COEFFICIENT_ROWS = [
  {
    user_id: "u-editor",
    name: "Editor C",
    member_code: "EDC",
    departments: ["Media Dựng"],
    coefficient: "1.50",
    effective_month: "2026-09",
    note: "Nhân sự chính",
    next_coefficient: null,
    next_month: null,
  },
  {
    user_id: "u-writer",
    name: "Biên tập B",
    member_code: null,
    departments: ["Biên Tập"],
    coefficient: "1",
    effective_month: null,
    note: null,
    next_coefficient: "2.00",
    next_month: "2026-12",
  },
];

describe("Hệ số điểm", () => {
  it("lists each member's coefficient and saves a change from a month on, after confirming", async () => {
    const stub = stubFetch([
      { match: "/api/performance/coefficients/u-writer", method: "PUT", body: { id: "c1" } },
      { match: "/api/performance/coefficients/u-writer", body: [] },
      { match: "/api/performance/coefficients", body: { month: "2026-10", rows: COEFFICIENT_ROWS } },
      { match: "/api/units/me", body: UNITS_ME },
    ]);
    renderWithQuery(<CoefficientsPage />);
    const table = await screen.findByRole("region", { name: "Hệ số từng người" });
    expect(within(table).getByText("× 1,5")).toBeInTheDocument();
    expect(within(table).getByText("Tháng 09/2026")).toBeInTheDocument();
    expect(within(table).getByText(/Từ tháng 12\/2026: × 2/)).toBeInTheDocument();
    expect(screen.getByText(/1 người đang khác 1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Sửa hệ số của Biên tập B" }));
    const box = screen.getByRole("textbox", { name: /Hệ số/ });
    await userEvent.clear(box);
    await userEvent.type(box, "1,2");
    expect(await screen.findByText(/Chưa đổi lần nào/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Lưu hệ số" }));
    const accept = screen
      .getAllByRole("dialog")
      .map((dialog) => dialog.querySelector<HTMLElement>("[data-confirm-accept]"))
      .find(Boolean);
    expect(screen.getByText("Đặt hệ số 1,2 cho Biên tập B?")).toBeInTheDocument();
    await userEvent.click(accept!);
    const put = (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls.find(
      (call) => call.method === "PUT",
    );
    expect(put?.body).toEqual({ coefficient: "1,2", month: "2026-10", note: null });
  });

  it("draws a refusal as a sentence", async () => {
    stubFetch([
      {
        match: "/api/performance/coefficients",
        status: 403,
        body: { code: "performance_coefficient_forbidden", message: "Chỉ chủ sở hữu (OWNER) cấu hình được hệ số điểm.", details: {} },
      },
    ]);
    renderWithQuery(<CoefficientsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Không xem được trang này");
  });
});
