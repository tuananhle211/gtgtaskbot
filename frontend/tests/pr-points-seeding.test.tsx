/**
 * PR task codes and the seeding group (0085): the "Điểm hiệu suất (PR)" panel
 * on a PR piece, the seeding page, and the menu entries that follow
 * `/api/units/me`.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PrPointsPanel } from "@/components/pr-points-panel";
import SeedingPage from "@/app/seeding/page";
import { Shell } from "@/components/shell";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/seeding",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

async function accept() {
  const button = screen
    .getAllByRole("dialog")
    .map((dialog) => dialog.querySelector<HTMLElement>("[data-confirm-accept]"))
    .find(Boolean);
  await userEvent.click(button!);
}

const N3 = { id: "c-n3", phase: "BIEN_TAP", code: "N3", name: "Kịch bản đến 3 phút", points: 120, active: true, sort_order: 1 };
const ME = { ...SESSION, user_id: "u-writer", units: ["PR"], must_change_password: false, password_changed_at: null };

const POINTS = {
  content_id: "pc-1",
  code: "PR-001",
  title: "Video ra mắt",
  may_review: false,
  stages: [
    { stage: "CONTENT", label: "Nội dung", phases: ["BIEN_TAP"], open: true, waiting: null, may_claim: true, claims: [] },
    {
      stage: "PRODUCTION",
      label: "Sản xuất",
      phases: ["THIET_KE", "DUNG"],
      open: false,
      waiting: "Nhập mã sau khi sản xuất qua bước duyệt nội bộ.",
      may_claim: false,
      claims: [],
    },
  ],
};

describe("Điểm hiệu suất (PR) on a PR piece", () => {
  it("lets the owner type the content's codes once it is done, and waits on production", async () => {
    const stub = stubFetch([
      { match: "/api/performance/pr/contents/pc-1/claims", method: "POST", body: { id: "cl-1" } },
      { match: "/api/performance/pr/contents/pc-1", body: POINTS },
      { match: "/api/performance/pr/catalog", body: { phases: { BIEN_TAP: [N3], THIET_KE: [], DUNG: [] }, forms: [] } },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<PrPointsPanel contentId="pc-1" />);
    const production = await screen.findByRole("article", { name: "Điểm Sản xuất" });
    expect(production).toHaveTextContent("Nhập mã sau khi sản xuất qua bước duyệt nội bộ.");
    const content = screen.getByRole("article", { name: "Điểm Nội dung" });
    await userEvent.click(within(content).getByRole("button", { name: "+ Nhập mã task của tôi" }));
    await waitFor(() =>
      expect(within(content).getByRole("combobox", { name: "Mã task công việc Biên kịch" })).toBeInTheDocument(),
    );
    await userEvent.selectOptions(within(content).getByRole("combobox", { name: "Mã task công việc Biên kịch" }), "c-n3");
    await userEvent.click(within(content).getByRole("button", { name: "Gửi Trưởng phòng duyệt" }));
    expect(screen.getByText("Gửi mã task nội dung (120 điểm)?")).toBeInTheDocument();
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    const post = calls(stub).find((call) => call.method === "POST");
    expect(post?.body).toEqual({ stage: "CONTENT", task_codes: { BIEN_TAP: [{ code_id: "c-n3", quantity: 1 }] }, note: null });
  });

  it("lets a Trưởng phòng PR refuse with a reason", async () => {
    const pending = {
      id: "cl-9",
      content_id: "pc-1",
      stage: "PRODUCTION",
      stage_label: "Sản xuất",
      user_id: "u-maker",
      name: "Dựng PR",
      items: { DUNG: [{ code_id: "c-d3", code: "D3", name: "Video", quantity: 1, points: 240 }] },
      points: 240,
      note: null,
      status: "PENDING",
      submitted_at: "2026-10-30T03:00:00Z",
      decided_by: null,
      decided_at: null,
      return_reason: null,
      decision_note: null,
    };
    const stub = stubFetch([
      { match: "/api/performance/pr/claims/cl-9/decide", method: "POST", body: { ...pending, status: "REJECTED" } },
      {
        match: "/api/performance/pr/contents/pc-1",
        body: {
          ...POINTS,
          may_review: true,
          stages: [POINTS.stages[0], { ...POINTS.stages[1], open: true, waiting: null, claims: [pending] }],
        },
      },
      { match: "/api/account/me", body: { ...ME, user_id: "u-lead" } },
    ]);
    renderWithQuery(<PrPointsPanel contentId="pc-1" />);
    const production = await screen.findByRole("article", { name: "Điểm Sản xuất" });
    expect(within(production).getByText("Chờ duyệt")).toBeInTheDocument();
    const refuse = within(production).getByRole("button", { name: "Không duyệt" });
    expect(refuse).toBeDisabled();
    await userEvent.selectOptions(within(production).getByRole("combobox", { name: /Lý do không duyệt/ }), "WRONG_CODES");
    await userEvent.click(within(production).getByRole("button", { name: "Không duyệt" }));
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")?.body).toEqual({
      approve: false,
      return_reason: "WRONG_CODES",
      note: null,
    });
  });
});

const OVERVIEW = {
  day: "2026-10-30",
  week: { kind: "WEEK", start: "2026-10-26", end: "2026-11-01", target: 4, approved: 2, pending: 1, rate: 0.5 },
  month: { kind: "MONTH", start: "2026-10-01", end: "2026-10-31", target: null, approved: 2, pending: 1, rate: null },
  members: [
    {
      person: { user_id: "u-maker", name: "Dựng PR", departments: ["PR Thương Hiệu"] },
      approved_week: 2,
      approved_month: 2,
      pending: 1,
      phone_points_month: 400,
      other_points_month: 150,
    },
  ],
  phone_points: 200,
  access: { member: true, manage: false },
};

describe("Seeding", () => {
  it("shows the team KPI as a bar, and hands numbers in line by line", async () => {
    const stub = stubFetch([
      {
        match: "/api/seeding/phones",
        method: "POST",
        body: {
          accepted: 1,
          lines: [
            { number: 1, raw: "0912 345 678", phone: "0912345678", status: "NEW" },
            { number: 2, raw: "0987654321", phone: "0987654321", status: "DUPLICATE" },
          ],
        },
      },
      { match: "/api/seeding/phones", body: [] },
      { match: "/api/seeding/overview", body: OVERVIEW },
    ]);
    renderWithQuery(<SeedingPage />);
    const week = await screen.findByRole("article", { name: "KPI tuần" });
    expect(within(week).getByRole("meter")).toHaveAttribute("aria-valuenow", "2");
    expect(week).toHaveTextContent("Đạt 50%");
    expect(screen.getByRole("article", { name: "KPI tháng" })).toHaveTextContent("Chưa giao KPI");
    // A member has no decisions nor people to manage.
    expect(screen.queryByRole("tab", { name: "Duyệt" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Từng thành viên" })).getByText("Dựng PR")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Nộp SĐT" }));
    await userEvent.type(screen.getByRole("textbox", { name: /mỗi số một dòng/ }), "0912 345 678{enter}0987654321");
    await userEvent.click(screen.getByRole("button", { name: "Nộp" }));
    const result = await screen.findByLabelText("Kết quả nộp");
    expect(result).toHaveTextContent("1/2 số được nhận");
    expect(result).toHaveTextContent("Đã có trong hệ thống");
    expect(calls(stub).find((call) => call.method === "POST")?.body).toEqual({ text: "0912 345 678\n0987654321" });
  });
});

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

describe("the menu", () => {
  it('adds "Seeding" under Phòng PR for the group and "Duyệt điểm PR" for a Trưởng phòng PR', async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      {
        match: "/api/units/me",
        body: {
          units: [{ code: "PR", label: "Luồng PR", role: "MEMBER", role_label: "Thành viên", is_lead: false, member_code: null, personal_nas_url: null, settings: {} }],
          default_unit: "PR",
          can_view_all: false,
          can_admin: [],
          performance: { view_team: true, view_all: false, export: false, review_pr: true },
          seeding: { member: true, manage: true },
        },
      },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const pr = await screen.findByRole("group", { name: "Phòng PR" });
    await waitFor(() =>
      expect(within(pr).getByRole("link", { name: /Seeding/ })).toHaveAttribute("href", "/seeding"),
    );
    const performance = screen.getByRole("group", { name: "Hiệu suất" });
    expect(within(performance).getByRole("link", { name: /Duyệt điểm PR/ })).toHaveAttribute(
      "href",
      "/performance/pr-claims",
    );
  });
});
