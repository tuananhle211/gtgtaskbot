/**
 * Inline UX patch: the Work card opens **in place**, and evidence is one text.
 *
 * ## What used to happen, and why
 *
 * The ledger was a two-column grid - the list on the left, one detail panel
 * on the right. Below the `lg` breakpoint that grid stacked into *the whole
 * list, then the panel*, so on a phone a tap on the third card opened its
 * detail several screens further down, and every URL change scrolled the page
 * to the top on the way. The tests here pin the replacement: each row owns
 * its detail, one row is open at a time, the `item` query parameter still
 * says which, and nothing on the page moves the viewport.
 *
 * ## The evidence half
 *
 * The dedicated evidence editor is one textarea. It does not ask for a label
 * and a link, it does not require a URL, and when a saved text contains one,
 * the rendering makes it clickable and leaves the words around it as words.
 *
 * Numbered 1-24 against the task's own list. Nothing here contacts a network.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, renderWithQuery, SESSION, serverWorkActions, stubFetch } from "./helpers";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const EMPLOYEE = ["PR_WORK_EXECUTE"];
const MANAGER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const WORK_TYPE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "HALF_DAY_SHOOT",
  name: "Quay nửa buổi",
  category: "PRODUCTION",
  category_label: "Sản xuất",
  description: null,
  default_unit: "SESSION",
  default_unit_label: "buổi",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 0,
};

const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const HAO = "22222222-2222-2222-2222-222222222222";

const contribution = (itemId: string) => ({
  id: `${itemId.slice(0, 8)}-0000-0000-0000-000000000001`,
  work_item_id: itemId,
  user_id: HAO,
  user_name: "Hảo",
  contribution_role: "PRIMARY",
  contribution_role_label: "Phụ trách chính",
  credit_weight: "1.0000",
  assigned_at: "2026-09-01T02:00:00Z",
  count_status: "PENDING",
  count_status_label: "Chưa ghi nhận",
  counted_at: null,
  excluded_reason: null,
});

const workItem = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  id,
  code: `WRK-2026-${id.slice(0, 6)}`,
  title,
  description: null,
  work_type_id: WORK_TYPE.id,
  work_type_code: WORK_TYPE.code,
  work_type_name: WORK_TYPE.name,
  work_type_category: WORK_TYPE.category,
  source_type: "MANUAL",
  source_label: "Nhập thủ công",
  is_source_derived: false,
  is_period_container: false,
  period_container: null,
  status: "ACCEPTED",
  status_label: "Được giao",
  priority: "NORMAL",
  priority_label: "Bình thường",
  quantity: null,
  unit: null,
  unit_label: null,
  due_at: "2026-09-20T09:00:00Z",
  execution_at: null,
  is_overdue: false,
  created_by_user_id: SESSION.user_id,
  assigned_by_user_id: SESSION.user_id,
  assigned_at: "2026-09-01T02:00:00Z",
  accepted_at: "2026-09-01T02:00:00Z",
  started_at: null,
  completed_at: null,
  approved_at: null,
  approved_by_user_id: null,
  cancelled_at: null,
  cancel_reason: null,
  channel_id: null,
  content_id: null,
  content_code: null,
  recurring_template_id: null,
  recurring_template_name: null,
  created_at: "2026-09-01T02:00:00Z",
  contributors: [contribution(id)],
  ...over,
});

const evidence = (over: Record<string, unknown> = {}) => ({
  id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  work_item_id: A,
  label: "Đã liên hệ 3 khách từ group cộng đồng.",
  location: "https://docs.google.com/spreadsheets/d/abc/edit",
  note: null,
  text: "Đã liên hệ 3 khách từ group cộng đồng.\nDanh sách: https://docs.google.com/spreadsheets/d/abc/edit\nKhách thứ 2 phản hồi qua Zalo.",
  added_by_user_id: HAO,
  created_at: "2026-09-08T02:00:00Z",
  ...over,
});

const detail = (item: Record<string, unknown>, over: Record<string, unknown> = {}) => ({
  item,
  work_type: WORK_TYPE,
  evidence: [],
  content_code: null,
  can_manage: false,
  can_validate: false,
  can_execute: true,
  results: [],
  is_subject: true,
  can_report_result: false,
  can_validate_results: false,
  // The server's action contract for these flags and this status.
  ...serverWorkActions(item, { can_manage: false, can_validate: false, can_execute: true, ...over }),
  ...over,
});

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-30T00:00:00Z",
  created: 3,
  accepted: 3,
  completed: 0,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 3,
  in_progress: 0,
  awaiting_validation: 0,
  proposed: 0,
  overdue: 0,
};

const ITEMS = [
  workItem(A, "Quay TVC Apexmed"),
  workItem(B, "Dựng video trend"),
  workItem(C, "Seeding nhóm kín"),
];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  {
    capabilities = EMPLOYEE,
    details = {},
    extra = [],
  }: {
    capabilities?: string[];
    details?: Record<string, { status?: number; body: unknown }>;
    extra?: Array<{ match: string; method?: string; status?: number; body?: unknown }>;
  } = {},
  items: Array<Record<string, unknown>> = ITEMS,
) => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/api/pr/work/types", body: [WORK_TYPE] },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  ...items.map((one) => ({
    match: `/api/pr/work/${one.id}`,
    method: "GET",
    status: details[one.id as string]?.status ?? 200,
    body: details[one.id as string]?.body ?? detail(one),
  })),
  {
    match: "/api/pr/work/periods",
    body: [
      {
        id: "66666666-6666-6666-6666-666666666666",
        code: "2026-09",
        period_type: "MONTH",
        date_start: "2026-09-01",
        date_end: "2026-09-30",
        status: "OPEN",
        status_label: "Đang mở",
        closed_at: null,
        locked_at: null,
      },
    ],
  },
  { match: "/api/pr/work", body: { items, total: items.length, limit: 50, offset: 0 } },
];

/** The card buttons, in list order. */
const cards = () =>
  screen
    .getAllByRole("button", { expanded: false })
    .concat(screen.queryAllByRole("button", { expanded: true }))
    .filter((node) => node.hasAttribute("aria-controls"));

const card = (title: string) => screen.getByRole("button", { name: new RegExp(title) });

/** Every rendered detail region. The rule is that there is at most one. */
const details = () => document.querySelectorAll('[id^="work-detail-"]');

/**
 * Make the stub behave like a server: once `postMatch` has been POSTed, the
 * GET of `getMatch` answers with `next`. A mutation writes the returned detail
 * into the cache *and* invalidates it, so the refetch must agree with the
 * mutation or the test would be asserting against a stub that contradicts
 * itself.
 */
function afterPost(
  fetchMock: ReturnType<typeof stubFetch>,
  postMatch: string,
  getMatch: string,
  next: unknown,
): void {
  const original = fetchMock.getMockImplementation()!;
  let mutated = false;
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "POST" && url.includes(postMatch)) mutated = true;
    if (mutated && method === "GET" && url.includes(getMatch)) {
      return new Response(JSON.stringify(next), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return original(input, init);
  });
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-5: THE CARD OPENS IN PLACE ------------------------------------------

describe("1-2. tapping a card opens its detail directly beneath it", () => {
  it("puts A's detail after A's card and before B's card", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    const a = card("Quay TVC Apexmed");
    const b = card("Dựng video trend");
    // A precedes its detail, and the detail precedes B - so B is *below* A's
    // detail, not above a panel at the bottom of the page.
    expect(a.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(region.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(a.closest("li")).toBe(region.closest("li"));
    expect(a).toHaveAttribute("aria-expanded", "true");
    expect(a).toHaveAttribute("aria-controls", `work-detail-${A}`);
    expect(within(a).getByText("Thu gọn ▴")).toBeInTheDocument();
    expect(within(b).getByText("Xem chi tiết ▾")).toBeInTheDocument();
  });
});

describe("3. tapping the open card collapses it", () => {
  it("removes the detail and clears the item from the URL", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    expect(NAV.current()).toContain(`item=${A}`);

    await userEvent.click(card("Quay TVC Apexmed"));
    await waitFor(() => expect(details()).toHaveLength(0));
    expect(NAV.current()).not.toContain("item=");
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "false");
  });
});

describe("4-5. opening another card closes the first, and one detail exists", () => {
  it("moves the single detail from A to B", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });

    await userEvent.click(card("Dựng video trend"));
    await screen.findByRole("region", { name: "Chi tiết Dựng video trend" });
    expect(screen.queryByRole("region", { name: "Chi tiết Quay TVC Apexmed" })).toBeNull();
    expect(details()).toHaveLength(1);
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "false");
    expect(card("Dựng video trend")).toHaveAttribute("aria-expanded", "true");
    expect(NAV.current()).toContain(`item=${B}`);
  });
});

// --- 6: DEEP LINKS -----------------------------------------------------------

describe("6. arriving at ?item=<id> opens that card", () => {
  it("expands B in place, and nothing else", async () => {
    stubFetch(routes());
    NAV.arriveAt(`/pr/work?item=${B}`);
    renderWithQuery(<WorkPage />);

    const region = await screen.findByRole("region", { name: "Chi tiết Dựng video trend" });
    expect(card("Dựng video trend").closest("li")).toBe(region.closest("li"));
    expect(details()).toHaveLength(1);
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "false");
  });

  it("renders no detail anywhere when the item is not in the list", async () => {
    stubFetch(routes());
    NAV.arriveAt("/pr/work?item=99999999-9999-9999-9999-999999999999");
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");
    expect(details()).toHaveLength(0);
    expect(cards()).toHaveLength(3);
  });
});

// --- 7: CONTROLS INSIDE THE ROW DO NOT TOGGLE IT ----------------------------

describe("7. a control inside the open row does not collapse it", () => {
  it("keeps A open when its history summary is clicked", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });

    await userEvent.click(within(region).getByText("Lịch sử"));
    await userEvent.click(within(region).getByText("Thông tin thêm"));
    expect(screen.getByRole("region", { name: "Chi tiết Quay TVC Apexmed" })).toBeInTheDocument();
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
    expect(NAV.current()).toContain(`item=${A}`);
  });

  it("keeps A open when its Bắt đầu button is pressed", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    await userEvent.click(within(region).getByRole("button", { name: "Bắt đầu" }));
    // The confirmation dialog opened; the card did not toggle.
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
  });
});

// --- 8-9: LOADING AND FAILURE LIVE INSIDE THE ROW ---------------------------

describe("8. while the detail loads, the loader is inside the selected row", () => {
  it("draws the loader in A's row and leaves B and C alone", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = stubFetch(routes());
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes(`/api/pr/work/${A}`) && (init?.method ?? "GET") === "GET") {
        await gate;
      }
      return original(input, init);
    });
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));

    const loader = await screen.findByText("Đang tải chi tiết…");
    expect(loader.closest("li")).toBe(card("Quay TVC Apexmed").closest("li"));
    expect(
      within(card("Dựng video trend").closest("li")!).queryByText("Đang tải chi tiết…"),
    ).toBeNull();
    // The rest of the list is still usable while A loads.
    expect(card("Seeding nhóm kín")).toBeEnabled();
    release();
    await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
  });
});

describe("9. a failed detail fetch is shown inside the selected row only", () => {
  it("draws the error in B's row; A and C stay as cards", async () => {
    stubFetch(
      routes({
        details: { [B]: { status: 500, body: { error: { code: "internal", message: "Lỗi" } } } },
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Dựng video trend"));

    const error = await screen.findByText("Lỗi");
    expect(error.closest("li")).toBe(card("Dựng video trend").closest("li"));
    expect(details()).toHaveLength(1);
    expect(screen.queryByRole("region", { name: /Chi tiết/ })).toBeNull();
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "false");
  });
});

// --- 10-12: MUTATIONS, THE OLD PANEL, HISTORY --------------------------------

describe("10. a mutation refreshes the detail and keeps the row open", () => {
  it("shows the server's new state inside the same row after Bắt đầu", async () => {
    const started = workItem(A, "Quay TVC Apexmed", {
      status: "IN_PROGRESS",
      status_label: "Đang làm",
      started_at: "2026-09-02T02:00:00Z",
    });
    const fetchMock = stubFetch(
      routes({
        extra: [{ match: `/api/pr/work/${A}/start`, method: "POST", body: detail(started) }],
      }),
    );
    afterPost(fetchMock, `/api/pr/work/${A}/start`, `/api/pr/work/${A}`, detail(started));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    await userEvent.click(within(region).getByRole("button", { name: "Bắt đầu" }));
    await screen.findByRole("dialog");
    await confirm();

    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Chi tiết Quay TVC Apexmed" })).queryByRole(
          "button",
          { name: "Bắt đầu" },
        ),
      ).toBeNull(),
    );
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
    expect(details()).toHaveLength(1);
  });
});

describe("11. the distant detail panel is gone", () => {
  it("never renders 'Chọn một công việc' and renders no detail with nothing selected", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await screen.findByText("Quay TVC Apexmed");
    expect(screen.queryByText("Chọn một công việc để xem chi tiết.")).toBeNull();
    expect(details()).toHaveLength(0);
  });
});

describe("12. history toggles without touching the selection", () => {
  it("opens Lịch sử inside A's detail and A stays selected", async () => {
    const fetchMock = stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    const history = within(region).getByText("Lịch sử").closest("details")!;
    expect(history.open).toBe(false);

    await userEvent.click(within(region).getByText("Lịch sử"));
    await waitFor(() => expect(history.open).toBe(true));
    const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
    expect(calls.some((one) => one.url.includes(`/api/pr/work/${A}/history`))).toBe(true);
    expect(NAV.current()).toContain(`item=${A}`);
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
  });
});

// --- 13-24: EVIDENCE IS ONE TEXT --------------------------------------------

describe("13-15. an empty evidence panel is one textarea, and nothing else", () => {
  it("says there is none, offers one field, and no label/link pair", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });

    expect(within(region).getByText("Chưa có minh chứng.")).toBeInTheDocument();
    expect(within(region).getByRole("textbox", { name: "Minh chứng" }).tagName).toBe("TEXTAREA");
    expect(within(region).queryByRole("textbox", { name: "Nhãn minh chứng" })).toBeNull();
    expect(within(region).queryByRole("textbox", { name: "Liên kết minh chứng" })).toBeNull();
    expect(within(region).queryByPlaceholderText("Nhãn")).toBeNull();
    expect(within(region).queryByPlaceholderText("Liên kết")).toBeNull();
  });
});

describe("16-18, 21. plain, multiline and URL-bearing text all save as one text", () => {
  it.each([
    ["plain text", "Gọi điện xác nhận với khách"],
    ["multiline text", "Dòng một\nDòng hai\nDòng ba"],
    ["text with a link", "Danh sách https://docs.google.com/x và ghi chú"],
  ])("saves %s without asking for a URL", async (_, typed) => {
    const saved = detail(ITEMS[0], { evidence: [evidence({ text: typed, location: null })] });
    const fetchMock = stubFetch(
      routes({
        extra: [{ match: `/api/pr/work/${A}/evidence`, method: "POST", status: 201, body: saved }],
      }),
    );
    afterPost(fetchMock, `/api/pr/work/${A}/evidence`, `/api/pr/work/${A}`, saved);
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    const field = within(region).getByRole("textbox", { name: "Minh chứng" });
    // `paste` keeps the newlines a typed Enter would also produce in a textarea.
    await userEvent.click(field);
    await userEvent.paste(typed);
    await userEvent.click(within(region).getByRole("button", { name: "Lưu minh chứng" }));

    const calls = (
      fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
    ).calls;
    const sent = calls.find((one) => one.method === "POST" && one.url.includes("/evidence"));
    expect(sent?.body).toEqual({ text: typed });
    // And the row stays open, with the saved text now listed.
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(within(region).queryByText("Chưa có minh chứng.")).toBeNull());
  });
});

describe("19-20. a saved text renders its links as links and its words as words", () => {
  it("makes the URL clickable, keeps the surrounding text, keeps the line breaks", async () => {
    stubFetch(routes({ details: { [A]: { body: detail(ITEMS[0], { evidence: [evidence()] }) } } }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });

    const link = within(region).getByRole("link", {
      name: "https://docs.google.com/spreadsheets/d/abc/edit",
    });
    expect(link).toHaveAttribute("href", "https://docs.google.com/spreadsheets/d/abc/edit");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(within(region).getByText(/Đã liên hệ 3 khách từ group cộng đồng\./)).toBeInTheDocument();
    expect(within(region).getByText(/Khách thứ 2 phản hồi qua Zalo\./)).toBeInTheDocument();
    // Line breaks are preserved as text, not collapsed or turned into markup.
    const wrapper = link.parentElement!;
    expect(wrapper.textContent).toContain("\n");
    expect(wrapper.className).toContain("whitespace-pre-wrap");
    // With evidence present the form is folded behind "Thêm minh chứng".
    expect(within(region).queryByRole("textbox", { name: "Minh chứng" })).toBeNull();
    await userEvent.click(within(region).getByRole("button", { name: "Thêm minh chứng" }));
    expect(within(region).getByRole("textbox", { name: "Minh chứng" })).toBeInTheDocument();
  });
});

describe("22. a legacy label-and-link row still renders", () => {
  it("shows the label linking to the location, beside a text row", async () => {
    const legacy = evidence({
      id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
      label: "Bản dựng",
      location: "https://drive.google.com/file/d/x/view",
      text: null,
      note: null,
    });
    stubFetch(
      routes({ details: { [A]: { body: detail(ITEMS[0], { evidence: [legacy, evidence()] }) } } }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });

    const old = within(region).getByRole("link", { name: "Bản dựng" });
    expect(old).toHaveAttribute("href", "https://drive.google.com/file/d/x/view");
    expect(within(region).getAllByRole("link").length).toBeGreaterThanOrEqual(2);
  });
});

describe("23. an actor who may not edit sees the evidence and no field", () => {
  it("draws no textarea and no Thêm minh chứng when can_execute is false", async () => {
    stubFetch(
      routes({
        details: {
          [A]: { body: detail(ITEMS[0], { can_execute: false, evidence: [evidence()] }) },
        },
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    expect(within(region).getByRole("link", { name: /docs.google.com/ })).toBeInTheDocument();
    expect(within(region).queryByRole("textbox", { name: "Minh chứng" })).toBeNull();
    expect(within(region).queryByRole("button", { name: "Thêm minh chứng" })).toBeNull();
  });
});

describe("24. requires_evidence is the server's rule, surfaced as the server says it", () => {
  it("marks the panel bắt buộc and shows the server's refusal of Hoàn thành", async () => {
    stubFetch(
      routes({
        capabilities: MANAGER,
        details: {
          [A]: { body: detail(ITEMS[0], { work_type: { ...WORK_TYPE, requires_evidence: true } }) },
        },
        extra: [
          {
            match: `/api/pr/work/${A}/complete`,
            method: "POST",
            status: 422,
            body: {
              error: {
                code: "validation_error",
                message: "Loại công việc này yêu cầu minh chứng trước khi hoàn thành.",
                details: { reason: "evidence_required" },
              },
            },
          },
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByText("Quay TVC Apexmed"));
    const region = await screen.findByRole("region", { name: "Chi tiết Quay TVC Apexmed" });
    expect(within(region).getByText("· bắt buộc")).toBeInTheDocument();

    await userEvent.click(within(region).getByRole("button", { name: "Hoàn thành" }));
    await screen.findByRole("dialog");
    await confirm();
    expect(await screen.findByText(/yêu cầu minh chứng trước khi hoàn thành/)).toBeInTheDocument();
    // Still open, still one detail, still the same row.
    expect(card("Quay TVC Apexmed")).toHaveAttribute("aria-expanded", "true");
    expect(details()).toHaveLength(1);
  });
});
