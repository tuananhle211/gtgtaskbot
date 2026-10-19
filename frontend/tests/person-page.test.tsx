/**
 * Somebody's profile page `/people/<id>` (2026-10-19): the card, the month's
 * figures, the account facts - and a refusal drawn as a sentence.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PersonPage from "@/app/people/[id]/page";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "u-ha" }),
  usePathname: () => "/people/u-ha",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const STATS = {
  month: "2026-10",
  points: 12.5,
  nodes_done: 7,
  nodes_in_progress: 3,
  revisions: 2,
  orders_created: 4,
  orders_completed: 1,
  pr_contents_owned: 5,
  pr_productions_done: 6,
  pr_approvals: 9,
  work_items_counted: 11,
  on_time_rate: 0.75,
  first_pass_rate: 0.6,
  late_count: 2,
  tokens_used: 30,
  tokens_budget: 40,
  effort_rate: 0.75,
  performance_score: 82.5,
  output_target: 10,
};

const HA = {
  user_id: "u-ha",
  full_name: "Hà Chi",
  telegram_user_id: 111,
  units: ["PR"],
  role: "EMPLOYEE",
  role_label: "Thành viên",
  member_code: "HACHI",
  employment_status: "PROBATION",
  employment_status_label: "Thử việc",
  last_login_at: "2026-10-18T03:00:00Z",
  has_custom_password: true,
  locked: false,
  active: true,
  departments: [
    { department_id: "d-pr", code: "PR", name: "PR Thương Hiệu", function: "PR", position: "MEMBER", position_label: "Nhân viên" },
  ],
  stats: STATS,
};

const ME = { ...SESSION, user_id: "u-me", units: [], must_change_password: false, password_changed_at: null, stats: STATS };
const UNITS_ME = { units: [], default_unit: null, can_view_all: true, can_admin: [], can_view_profiles: true };

const page = (profile: { status?: number; body: unknown }) => {
  const stub = stubFetch([
    // The profile route first: "/api/account/me" is a prefix of it.
    { match: "/api/account/members/u-ha/profile", ...profile },
    { match: "/api/auth/session", body: SESSION },
    { match: "/api/units/me", body: UNITS_ME },
    { match: "/api/account/me", body: ME },
  ]);
  renderWithQuery(<PersonPage />);
  return stub;
};

describe("the profile page", () => {
  it("shows the card, the month's figures first, and the account facts on the second tab", async () => {
    const stub = page({ body: HA });
    expect(await screen.findByRole("heading", { level: 1, name: "Hà Chi" })).toBeInTheDocument();
    expect(screen.getByText("Thử việc")).toBeInTheDocument();
    expect(screen.getByText("HACHI")).toBeInTheDocument();
    expect(screen.getByText("PR Thương Hiệu")).toBeInTheDocument();
    // The figures: the headline cards from the same StatCards as /account.
    expect(screen.getByRole("tab", { name: "Hiệu suất" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Điểm hiệu suất")).toBeInTheDocument();
    expect(screen.getByText("82,5/100")).toBeInTheDocument();
    // Not my page: no "Tài khoản của tôi" shortcut.
    expect(screen.queryByRole("link", { name: "Tài khoản của tôi" })).not.toBeInTheDocument();
    // Another month is its own request.
    fireEvent.change(screen.getByLabelText("Tháng"), { target: { value: "2026-09" } });
    await waitFor(() =>
      expect(calls(stub).some((call) => call.url.includes("/profile?month=2026-09"))).toBe(true),
    );
    // The facts.
    await userEvent.click(await screen.findByRole("tab", { name: "Thông tin tài khoản" }));
    expect(screen.getByRole("heading", { level: 2, name: "Thông tin tài khoản" })).toBeInTheDocument();
    expect(screen.getByText("111")).toBeInTheDocument();
    expect(screen.getByText("Đang hoạt động")).toBeInTheDocument();
    expect(screen.queryByText("Điểm hiệu suất")).not.toBeInTheDocument();
  });

  it("draws a refusal as a sentence, never the figures", async () => {
    page({
      status: 403,
      body: {
        error: {
          code: "account_profile_forbidden",
          message: "Bạn không có quyền xem trang cá nhân này.",
          details: { reason: "account_profile_forbidden" },
        },
      },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Không xem được trang này");
    expect(alert).toHaveTextContent("Bạn không có quyền xem trang cá nhân này.");
    expect(screen.queryByText("Điểm hiệu suất")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Về bảng task" })).toHaveAttribute("href", "/tasks");
  });
});
