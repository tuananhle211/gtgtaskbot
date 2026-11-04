/**
 * PR task codes and the seeding group (0085): the "Điểm hiệu suất (PR)" panel
 * on a PR piece, "Báo cáo công việc/Seeding", and the menu entries that
 * follow `/api/units/me`. 2026-10-31: the picker is open from the start, the
 * KPI page is the group's, everybody in PR hands in, a refusal says why.
 * 2026-11-03: PR codes need no approval - members edit theirs until internal
 * review, a Trưởng phòng PR edits / removes anybody's until the piece is done.
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
      open: true,
      waiting: null,
      may_claim: false,
      claims: [],
    },
  ],
};

describe("Điểm hiệu suất (PR) on a PR piece", () => {
  it("draws the owner's picker open from the start and sends the codes", async () => {
    const stub = stubFetch([
      { match: "/api/performance/pr/contents/pc-1/claims", method: "POST", body: { id: "cl-1" } },
      { match: "/api/performance/pr/contents/pc-1", body: POINTS },
      { match: "/api/performance/pr/catalog", body: { phases: { BIEN_TAP: [N3], THIET_KE: [], DUNG: [] }, forms: [] } },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<PrPointsPanel contentId="pc-1" />);
    const content = await screen.findByRole("article", { name: "Điểm Nội dung" });
    // No codes yet: the phase may not move on, and the picker is already open.
    expect(content).toHaveTextContent("Cần chọn trước khi chuyển pha");
    expect(within(content).queryByRole("button", { name: "+ Nhập mã task của tôi" })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(within(content).getByRole("combobox", { name: "Mã task công việc Biên kịch" })).toBeInTheDocument(),
    );
    await userEvent.selectOptions(within(content).getByRole("combobox", { name: "Mã task công việc Biên kịch" }), "c-n3");
    await userEvent.click(within(content).getByRole("button", { name: "Lưu mã task" }));
    expect(screen.getByText("Lưu mã task nội dung (120 điểm)?")).toBeInTheDocument();
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    const post = calls(stub).find((call) => call.method === "POST");
    expect(post?.body).toEqual({
      stage: "CONTENT",
      task_codes: { BIEN_TAP: [{ code_id: "c-n3", quantity: 1 }] },
      note: null,
      user_id: null,
    });
  });

  it("locks a member's codes once the piece is in internal review", async () => {
    const mine = {
      id: "cl-1",
      content_id: "pc-1",
      stage: "CONTENT",
      stage_label: "Nội dung",
      user_id: "u-writer",
      name: "Biên tập PR",
      items: { BIEN_TAP: [{ code_id: "c-n3", code: "N3", name: "Kịch bản đến 3 phút", quantity: 1, points: 120 }] },
      points: 120,
      note: null,
      status: "PENDING",
      submitted_at: "2026-10-30T03:00:00Z",
      decided_by: null,
      decided_at: null,
      return_reason: null,
      decision_note: null,
    };
    stubFetch([
      {
        match: "/api/performance/pr/contents/pc-1",
        body: {
          ...POINTS,
          stages: [
            {
              ...POINTS.stages[0],
              may_claim: false,
              waiting: "Đã gửi duyệt nội bộ: chỉ Trưởng phòng PR sửa được mã task đến khi task hoàn thành.",
              claims: [mine],
            },
            POINTS.stages[1],
          ],
        },
      },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<PrPointsPanel contentId="pc-1" />);
    const content = await screen.findByRole("article", { name: "Điểm Nội dung" });
    expect(within(content).getByTestId("pr-points-locked")).toHaveTextContent("chỉ Trưởng phòng PR");
    expect(within(content).getByText("Cộng khi hoàn thành")).toBeInTheDocument();
    expect(within(content).queryByRole("button", { name: "Sửa mã task" })).not.toBeInTheDocument();
    expect(within(content).queryByRole("button", { name: "Duyệt" })).not.toBeInTheDocument();
  });

  it("lets a Trưởng phòng PR edit or remove somebody's codes - no approval", async () => {
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
    const D3 = { id: "c-d3", phase: "DUNG", code: "D3", name: "Video", points: 240, active: true, sort_order: 1 };
    const stub = stubFetch([
      { match: "/api/performance/pr/claims/cl-9", method: "DELETE", status: 204, body: null },
      { match: "/api/performance/pr/contents/pc-1/claims", method: "POST", body: pending },
      {
        match: "/api/performance/pr/contents/pc-1",
        body: {
          ...POINTS,
          may_review: true,
          stages: [
            POINTS.stages[0],
            { ...POINTS.stages[1], open: true, waiting: null, may_edit_others: true, claims: [pending] },
          ],
        },
      },
      { match: "/api/performance/pr/catalog", body: { phases: { BIEN_TAP: [], THIET_KE: [], DUNG: [D3] }, forms: [] } },
      { match: "/api/account/me", body: { ...ME, user_id: "u-lead" } },
    ]);
    renderWithQuery(<PrPointsPanel contentId="pc-1" />);
    const production = await screen.findByRole("article", { name: "Điểm Sản xuất" });
    expect(within(production).getByText("Cộng khi hoàn thành")).toBeInTheDocument();
    expect(within(production).queryByRole("button", { name: "Duyệt" })).not.toBeInTheDocument();
    // Each code says what it is (2026-11-03), not a bare "D3".
    const codes = within(production).getByRole("list", { name: "Mã task đã chọn" });
    expect(codes).toHaveTextContent("D3");
    expect(codes).toHaveTextContent("Video");
    expect(codes).toHaveTextContent("240đ");
    // Remove.
    await userEvent.click(within(production).getByRole("button", { name: "Xoá" }));
    expect(screen.getByText("Xoá mã task sản xuất của Dựng PR?")).toBeInTheDocument();
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "DELETE")).toBe(true));
    // Edit somebody's: sent with their id.
    await userEvent.click(within(production).getByRole("button", { name: "Sửa mã task" }));
    await userEvent.click(within(production).getByRole("button", { name: "Lưu mã task" }));
    expect(screen.getByText(/Lưu mã task sản xuất của Dựng PR/)).toBeInTheDocument();
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")?.body).toMatchObject({
      stage: "PRODUCTION",
      user_id: "u-maker",
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
      { match: "/api/seeding/access", body: { ...OVERVIEW.access, submit: true, sees_kpi: true, awaiting: 0 } },
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

describe("Báo cáo công việc/Seeding for everybody in PR (2026-10-31)", () => {
  it("gives a PR member outside the group the hand-in tabs only", async () => {
    stubFetch([
      { match: "/api/seeding/phones", body: [] },
      { match: "/api/seeding/other", body: [] },
      {
        match: "/api/seeding/access",
        body: { member: false, manage: false, submit: true, sees_kpi: false, awaiting: 0, phone_points: 200 },
      },
    ]);
    renderWithQuery(<SeedingPage />);
    expect(await screen.findByRole("heading", { name: "Báo cáo công việc/Seeding" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual(["Nộp SĐT", "Việc khác"]);
    expect(screen.getByRole("tab", { name: "Nộp SĐT" })).toHaveAttribute("aria-selected", "true");
  });

  it("counts what waits on Duyệt and refuses only with a reason", async () => {
    const waiting = {
      id: "ph-1",
      phone: "0911222333",
      user_id: "u-writer",
      name: "Biên tập PR",
      submitted_at: "2026-10-31T03:00:00Z",
      status: "PENDING",
      decided_by: null,
      decided_at: null,
      decision_note: null,
    };
    const stub = stubFetch([
      { match: "/api/seeding/phones/decide", method: "POST", body: { decided: 1 } },
      { match: "/api/seeding/phones", body: [waiting] },
      { match: "/api/seeding/other", body: [] },
      { match: "/api/seeding/overview", body: { ...OVERVIEW, access: { member: false, manage: true } } },
      { match: "/api/seeding/members", body: [] },
      { match: "/api/seeding/candidates", body: [] },
      {
        match: "/api/seeding/access",
        body: { member: false, manage: true, submit: true, sees_kpi: true, awaiting: 3, awaiting_phones: 1, awaiting_other: 2 },
      },
    ]);
    renderWithQuery(<SeedingPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt (3)" }));
    expect(screen.getByRole("tab", { name: "Thành viên Seeding" })).toBeInTheDocument();
    const phones = await screen.findByRole("region", { name: "SĐT chờ duyệt" });
    await userEvent.click(await within(phones).findByRole("checkbox", { name: "Chọn 0911222333" }));
    await userEvent.click(within(phones).getByRole("button", { name: "Không duyệt" }));
    const dialog = screen.getAllByRole("dialog").at(-1)!;
    const accept = dialog.querySelector<HTMLElement>("[data-confirm-accept]")!;
    expect(accept).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Lý do không duyệt/ }), "Số không có thật");
    expect(accept).toBeEnabled();
    await userEvent.click(accept);
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")?.body).toEqual({
      ids: ["ph-1"],
      approve: false,
      note: "Số không có thật",
    });
  });
});

describe("Duyệt: lọc theo người gửi (2026-11-03)", () => {
  it("narrows both lists to one sender and decides only their rows", async () => {
    const phone = (id: string, number: string, user: string, name: string) => ({
      id,
      phone: number,
      user_id: user,
      name,
      submitted_at: "2026-11-03T03:00:00Z",
      status: "PENDING",
      decided_by: null,
      decided_at: null,
      decision_note: null,
    });
    const stub = stubFetch([
      { match: "/api/seeding/phones/decide", method: "POST", body: { decided: 1 } },
      {
        match: "/api/seeding/phones",
        body: [phone("ph-1", "0911000001", "u-an", "An"), phone("ph-2", "0911000002", "u-binh", "Bình")],
      },
      {
        match: "/api/seeding/other",
        body: [{ ...phone("ot-1", "", "u-binh", "Bình"), description: "Viết bài group", points: 50 }],
      },
      { match: "/api/seeding/overview", body: { ...OVERVIEW, access: { member: false, manage: true } } },
      { match: "/api/seeding/members", body: [] },
      { match: "/api/seeding/candidates", body: [] },
      {
        match: "/api/seeding/access",
        body: { member: false, manage: true, submit: true, sees_kpi: true, awaiting: 3, awaiting_phones: 2, awaiting_other: 1 },
      },
    ]);
    renderWithQuery(<SeedingPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt (3)" }));
    const phones = await screen.findByRole("region", { name: "SĐT chờ duyệt" });
    await within(phones).findByRole("checkbox", { name: "Chọn 0911000001" });
    const filter = screen.getByRole("combobox", { name: "Người gửi" });
    expect(within(filter).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Tất cả người gửi",
      "An (1)",
      "Bình (2)",
    ]);
    await userEvent.selectOptions(filter, "u-binh");
    expect(within(phones).queryByRole("checkbox", { name: "Chọn 0911000001" })).not.toBeInTheDocument();
    expect(within(phones).getByRole("heading")).toHaveTextContent("SĐT chờ duyệt · 1");
    const others = screen.getByRole("region", { name: "Việc khác chờ duyệt" });
    expect(within(others).getByText("Viết bài group")).toBeInTheDocument();
    await userEvent.click(within(phones).getByRole("button", { name: "Chọn tất cả" }));
    await userEvent.click(within(phones).getByRole("button", { name: "Duyệt 1" }));
    await accept();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")?.body).toEqual({ ids: ["ph-2"], approve: true, note: null });
  });
});

describe("the menu", () => {
  it('adds "Seeding" under Phòng PR for the group and "Điểm PR" for a Trưởng phòng PR', async () => {
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
          seeding: { member: true, manage: true, submit: true, awaiting: 4 },
        },
      },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const pr = await screen.findByRole("group", { name: "Phòng PR" });
    await waitFor(() =>
      expect(within(pr).getByRole("link", { name: /Báo cáo công việc\/Seeding/ })).toHaveAttribute("href", "/seeding"),
    );
    // What waits for the Trưởng phòng, on the menu.
    expect(within(pr).getByRole("link", { name: /Báo cáo công việc\/Seeding/ })).toHaveTextContent("4");
    const performance = screen.getByRole("group", { name: "Hiệu suất" });
    expect(within(performance).getByRole("link", { name: /Điểm PR/ })).toHaveAttribute(
      "href",
      "/performance/pr-claims",
    );
  });
});
