/**
 * "Đề xuất tài nguyên" (2026-10-21): the list, the create form's route
 * notice, and the detail page's step panel.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RequestsPage from "@/app/resources/requests/page";
import NewRequestPage from "@/app/resources/requests/new/page";
import RequestPage from "@/app/resources/requests/[id]/page";
import type { ResourceRequest } from "@/lib/api";
import { confirm, renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "rq-1" }),
  usePathname: () => "/resources/requests",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const REQUEST: ResourceRequest = {
  id: "rq-1",
  code: "DX-0015",
  type_id: "t-via",
  type_name: "Via",
  quantity: 2,
  site: "ALL",
  site_label: "Toàn hệ thống",
  purpose: "Chiến dịch tháng 11",
  requirements: null,
  reason: "Chia cho 3 nhân sự",
  replaces: null,
  budget: 6_000_000,
  needed_by: "2026-10-28",
  stage: "PURCHASING",
  stage_label: "Chờ mua & nhập kho",
  requester: { user_id: "u-1", full_name: "Nguyễn Xuân Dũng" },
  approvers: [{ user_id: "u-2", full_name: "Trưởng ADS" }],
  actual_cost: null,
  supplier: null,
  created_at: "2026-10-21T03:00:00Z",
  awaiting_me: true,
  actions: ["deliver"],
  delivered: 0,
  detail_path: "/resources/requests/rq-1",
  steps: [
    { key: "CREATED", label: "Tạo đề xuất", state: "done", person_name: "Nguyễn Xuân Dũng", person_user_id: "u-1", status_label: "Đã gửi", at: "2026-10-21T03:00:00Z" },
    { key: "HEAD", label: "Trưởng phòng duyệt", state: "done", person_name: "Trưởng ADS", person_user_id: "u-2", status_label: "Đã duyệt", at: "2026-10-21T04:00:00Z", note: "Ok" },
    { key: "FUNDING", label: "Duyệt chi", state: "done", person_name: "Nhân viên TN", person_user_id: "u-3", status_label: "Đã duyệt chi", at: "2026-10-21T05:00:00Z", note: "Phiếu chi PC-01" },
    { key: "PURCHASING", label: "Mua & nhập kho", state: "current", person_name: "Phòng Tài nguyên", status_label: "Chờ Phòng Tài nguyên mua & nhập kho" },
    { key: "DELIVERED", label: "Xác nhận nhận", state: "todo", status_label: "Chưa tới" },
  ],
  events: [],
  resources: [],
  holding: 11,
};

describe("the list", () => {
  it("tints what waits on me and counts it on its tab", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      {
        match: "/api/resources/requests",
        body: { items: [REQUEST, { ...REQUEST, id: "rq-2", code: "DX-0012", awaiting_me: false, stage: "COMPLETED", stage_label: "Hoàn tất" }], awaiting_count: 1 },
      },
    ]);
    renderWithQuery(<RequestsPage />);
    const first = (await screen.findByRole("link", { name: "DX-0015" })).closest("tr")!;
    expect(first).toHaveAttribute("data-awaiting", "true");
    expect(screen.getByRole("link", { name: "DX-0012" }).closest("tr")).not.toHaveAttribute("data-awaiting");
    expect(screen.getByRole("tab", { name: "Chờ tôi xử lý (1)" })).toBeInTheDocument();
    expect(within(first).getByText("6.000.000 đ")).toBeInTheDocument();
  });
});

describe("the create form", () => {
  it("shows the route and the three sites", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      {
        match: "/api/resources/requests/form",
        body: {
          types: [{ value: "t-via", label: "Via" }],
          sites: [
            { value: "HANOI", label: "Hà Nội" },
            { value: "SAIGON", label: "Sài Gòn" },
            { value: "ALL", label: "Toàn hệ thống" },
          ],
          approvers: [{ user_id: "u-2", full_name: "Trưởng ADS" }],
          max_quantity: 500,
        },
      },
    ]);
    renderWithQuery(<NewRequestPage />);
    expect(await screen.findByText(/Trưởng phòng \(Trưởng ADS\) → Duyệt chi/)).toBeInTheDocument();
    const site = screen.getByLabelText(/Cơ sở sử dụng/);
    expect(within(site).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn —",
      "Hà Nội",
      "Sài Gòn",
      "Toàn hệ thống",
    ]);
    expect(screen.getByLabelText(/Mục đích sử dụng/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Gửi đề xuất" })).toBeDisabled();
  });
});

describe("the detail page", () => {
  it("puts the bought list into the inventory, one line per resource", async () => {
    const stub = stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      {
        match: "/api/resources/requests/rq-1/actions",
        method: "POST",
        body: { ...REQUEST, stage: "DELIVERED", stage_label: "Chờ xác nhận nhận", actions: [] },
      },
      { match: "/api/resources/requests/rq-1", body: REQUEST },
    ]);
    renderWithQuery(<RequestPage />);
    expect(await screen.findByRole("heading", { name: /DX-0015/ })).toBeInTheDocument();
    expect(screen.getByText(/đang giữ 11 Via/)).toBeInTheDocument();
    // The strip: who approved, when, the note; the current step names who it waits on.
    const strip = screen.getByRole("region", { name: "Tiến trình" });
    const head = within(strip).getByText("Trưởng phòng duyệt").closest("li")!;
    expect(head).toHaveTextContent("Trưởng ADS");
    expect(head).toHaveTextContent("Đã duyệt");
    expect(head).toHaveTextContent("21/10/2026 11:00");
    expect(within(strip).getByText("Phiếu chi PC-01")).toBeInTheDocument();
    const now = within(strip).getByText("Mua & nhập kho").closest("li")!;
    expect(now).toHaveAttribute("aria-current", "step");
    expect(now).toHaveTextContent("Chờ Phòng Tài nguyên mua & nhập kho");
    await userEvent.type(screen.getByLabelText(/Danh sách Via/), "111 | https://a{enter}222 | https://b");
    expect(screen.getByLabelText(/2\/2 dòng/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Tổng tiền mua thực tế/), "5400000");
    await userEvent.click(screen.getByRole("button", { name: /Nhập kho 2 Via/ }));
    await confirm();
    await waitFor(() =>
      expect(calls(stub).some((call) => call.method === "POST" && call.url.endsWith("/actions"))).toBe(true),
    );
    const sent = calls(stub).find((call) => call.url.endsWith("/actions"))!.body as Record<string, unknown>;
    expect(sent).toMatchObject({ action: "deliver", lines: "111 | https://a\n222 | https://b", actual_cost: 5_400_000 });
  });

  it("needs a reason before a refusal", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      { match: "/api/resources/requests/rq-1", body: { ...REQUEST, stage: "HEAD_REVIEW", actions: ["approve", "reject"] } },
    ]);
    renderWithQuery(<RequestPage />);
    const reject = await screen.findByRole("button", { name: "Từ chối" });
    expect(reject).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Ghi chú/), "Chưa cần");
    expect(reject).toBeEnabled();
    expect(screen.getByRole("button", { name: "Duyệt" })).toBeEnabled();
  });
});
