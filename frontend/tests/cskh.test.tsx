/**
 * "Chăm sóc khách hàng" (0102): the page inbox (list, a conversation, send,
 * the phone confirmation with its duplicate check), the Zalo inbox's page side
 * (masked when matched by code), the Zalo QR login, the MANAGE-only screens.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InboxPage from "@/app/cskh/page";
import ZaloPage from "@/app/cskh/zalo/page";
import ZaloAccountsPage from "@/app/cskh/zalo/accounts/page";
import PagesPage from "@/app/cskh/pages/page";
import { renderWithQuery, stubFetch } from "./helpers";

const params = { value: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/cskh",
  useSearchParams: () => params.value,
  useRouter: () => ({
    replace: (url: string) => {
      params.value = new URLSearchParams(url.split("?")[1] ?? "");
    },
    push: () => undefined,
  }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  params.value = new URLSearchParams();
});

type Call = { url: string; method: string; body: unknown };
const sent = (stub: unknown, method: string, fragment: string) =>
  (stub as { calls: Call[] }).calls.filter((call) => call.method === method && call.url.includes(fragment));

const me = (cskh: Record<string, unknown>) => ({
  units: [],
  can_view_all: false,
  cskh: { inbox: false, zalo: false, manage: false, unread: 0, zalo_unread: 0, ...cskh },
});

const PAGE = {
  id: "421752",
  name: "Page Thẩm Mỹ",
  connected: true,
  unread: 2,
  label_ids: [],
  members: [{ user_id: "u-lan", name: "Lan", role: "STAFF", role_label: "Nhân viên trực" }],
  marketer_user_id: "u-mkt",
  marketer_name: "Mai Marketing",
  pos_shop_id: "430144632",
  pancake_tags: [{ id: "7", text: "Đã tư vấn", color: "#3366ff" }],
  pancake_users: [{ id: "pu1", name: "Lan Pancake" }],
};

const STATE = {
  pages: [PAGE],
  labels: [],
  quick_replies: [{ id: "q1", shortcut: "chao", text: "Dạ em chào {ten} ạ", page_id: null }],
  campaigns: [],
  order_statuses: ["Đã chốt lịch", "Hẹn gọi lại"],
  platforms: ["Facebook", "TikTok", "Google"],
  work_now: true,
  auto_reply_enabled: true,
  unread_total: 2,
};

const ROW = {
  id: "c1",
  page_id: PAGE.id,
  page_name: PAGE.name,
  psid: "99887766",
  customer_name: "Ngô Trọng Huy",
  last_text: "Cho em hỏi giá",
  last_at: "2026-11-10T03:00:00Z",
  last_out: false,
  unread: 2,
  callback: false,
  phones: ["0912345678"],
  has_qr: false,
  ad_id: null,
  ad_name: null,
  pancake_tags: [],
  assignee_ids: [],
  zalo_linked: false,
};

const DETAIL = {
  conversation: { ...ROW, unread: 0, qrs: [], ads: [], last_in_at: "2026-11-10T03:00:00Z" },
  page: {
    id: PAGE.id,
    name: PAGE.name,
    connected: true,
    members: PAGE.members,
    marketer_user_id: "u-mkt",
    marketer_name: "Mai Marketing",
    pos_shop_id: "430144632",
    pos_shop_name: "Shop 1",
  },
  messages: [
    {
      id: "m1",
      mid: "m_1",
      direction: "in",
      kind: "customer",
      text: "Cho em hỏi giá",
      at: "2026-11-10T03:00:00Z",
      status: "ok",
      attachments: [{ kind: "image", url: "https://content.pancake.vn/a.jpg" }],
      qr: ["https://zaloapp.com/qr/p/abc"],
      sender_name: "",
    },
  ],
  orders: [],
  zalo_threads: 0,
};

const CHECK_DUP = {
  phones: ["0912345678"],
  is_new: false,
  need_ack: true,
  own_confirmed: [],
  seen: [
    {
      conversation_id: "c9",
      page_id: "p2",
      page_name: "Page Khác",
      customer_name: "Lê Chung",
      phone: "0912345678",
      first_at: "2026-11-08T01:00:00Z",
      times: 1,
      ad_id: "",
    },
  ],
  orders: [],
  self: [],
  repeat_in_conversation: false,
  pos_error: "",
  qrs: [],
  page_marketer: { user_id: "u-mkt", name: "Mai Marketing" },
  old_marketer: null,
};

describe("Hộp chat page", () => {
  it("lists conversations, opens one and sends with a quick reply", async () => {
    params.value = new URLSearchParams("c=c1");
    const stub = stubFetch([
      { match: "/api/units/me", body: me({ inbox: true }) },
      { match: "/api/cskh/state", body: STATE },
      {
        match: "/api/cskh/conversations/c1/messages",
        method: "POST",
        body: { ...DETAIL.messages[0], id: "m2" },
      },
      {
        match: "/api/cskh/conversations/c1/zalo",
        body: { threads: [], codes: [], suggest: [], may_link: false },
      },
      { match: "/api/cskh/conversations/c1", body: DETAIL },
      { match: "/api/cskh/conversations", body: { conversations: [ROW], next_before: null } },
    ]);
    renderWithQuery(<InboxPage />);
    const list = await screen.findByTestId("cskh-conversations");
    expect(await within(list).findByText("Ngô Trọng Huy")).toBeInTheDocument();
    expect(await screen.findByTestId("cskh-message")).toHaveTextContent("Cho em hỏi giá");
    expect(screen.getByTestId("cskh-qr-label")).toHaveTextContent("zaloapp.com");
    const box = screen.getByLabelText("Nội dung tin nhắn");
    await userEvent.type(box, "/ch");
    await userEvent.keyboard("{Enter}");
    expect(box).toHaveValue("Dạ em chào Ngô Trọng Huy ạ");
    // Enter alone breaks a line now; Ctrl+Enter sends (12/11 design).
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/cskh/conversations/c1/messages")[0]?.body).toEqual({
        text: "Dạ em chào Ngô Trọng Huy ạ",
        clear_callback: false,
      }),
    );
  });

  it("asks to check a repeated number, then confirms with dup_ack", async () => {
    params.value = new URLSearchParams("c=c1");
    let confirmCalls = 0;
    const calls: Call[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, method, body });
        const json = (value: unknown, status = 200) =>
          new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
        if (url.includes("/api/units/me")) return json(me({ inbox: true }));
        if (url.includes("/api/cskh/state")) return json(STATE);
        if (url.includes("/phone-check"))
          return json({ ...CHECK_DUP, need_ack: false, is_new: true, seen: [] });
        if (url.includes("/confirm")) {
          confirmCalls += 1;
          if (!body.dup_ack)
            return json(
              {
                error: {
                  code: "conflict",
                  message: "Khách đã có trên hệ thống",
                  details: { reason: "cskh_duplicate", dup: CHECK_DUP },
                },
              },
              409,
            );
          return json({ order: { id: "o1" }, action: "create" });
        }
        if (url.includes("/zalo")) return json({ threads: [], codes: [], suggest: [], may_link: false });
        if (url.includes("/api/cskh/conversations/c1")) return json(DETAIL);
        if (url.includes("/api/cskh/conversations")) return json({ conversations: [ROW], next_before: null });
        throw new Error(`No stub for ${method} ${url}`);
      }),
    );
    renderWithQuery(<InboxPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Xác nhận đơn" }));
    const dialog = await screen.findByRole("dialog", { name: "Xác nhận số điện thoại" });
    expect(await within(dialog).findByTestId("cskh-new-customer")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Xác nhận đơn" }));
    await userEvent.click(document.querySelector<HTMLElement>("[data-confirm-accept]") as HTMLElement);
    const warning = await within(dialog).findByTestId("cskh-duplicate");
    expect(warning).toHaveTextContent("Page Khác");
    expect(warning).toHaveTextContent("Lê Chung");
    const again = within(dialog).getByRole("button", { name: "Xác nhận đơn" });
    expect(again).toBeDisabled();
    await userEvent.click(within(dialog).getByLabelText("Đã kiểm tra, vẫn xác nhận đơn này"));
    await userEvent.click(again);
    await userEvent.click(document.querySelector<HTMLElement>("[data-confirm-accept]") as HTMLElement);
    await waitFor(() => expect(confirmCalls).toBe(2));
    const last = calls.filter((c) => c.url.includes("/confirm")).at(-1)!;
    expect(last.body).toMatchObject({
      confirm_type: "phone",
      phone: "0912345678",
      dup_ack: true,
      platform: "Facebook",
    });
  });
});

describe("Hộp chat page - bản cải tiến 12/11", () => {
  const NOW = Date.parse("2026-11-12T03:00:00Z");

  it("draws 3-line rows: page label, waiting clock red past the threshold, filters as chips", async () => {
    vi.spyOn(Date, "now").mockReturnValue(NOW);
    const stub = stubFetch([
      { match: "/api/units/me", body: me({ inbox: true }) },
      {
        match: "/api/cskh/state",
        body: {
          ...STATE,
          pages: [{ ...PAGE, short_name: "Tiểu Eva", color: "pink" }],
          wait_alert_minutes: 8,
          callback_total: 3,
          stages: [
            { id: "NEW", label: "Mới" },
            { id: "CONSULTING", label: "Đang tư vấn" },
            { id: "CALLBACK", label: "Chờ gọi lại" },
            { id: "CLOSED", label: "Đã chốt" },
          ],
        },
      },
      {
        match: "/api/cskh/conversations",
        body: {
          conversations: [
            { ...ROW, last_at: "2026-11-12T02:50:00Z", page_short: "Tiểu Eva", page_color: "pink" },
            {
              ...ROW,
              id: "c2",
              customer_name: "Thu Trang",
              unread: 0,
              last_at: "2026-11-12T02:57:00Z",
            },
            { ...ROW, id: "c3", customer_name: "Ngọc Anh", unread: 0, last_out: true },
          ],
          next_before: null,
          next_offset: null,
        },
      },
    ]);
    renderWithQuery(<InboxPage />);
    const list = await screen.findByTestId("cskh-conversations");
    await within(list).findByText("Thu Trang");
    const [late, fresh] = within(list).getAllByTestId("cskh-wait");
    expect(late).toHaveTextContent("10 phút");
    expect(late).toHaveAttribute("data-late", "true");
    expect(fresh).toHaveTextContent("3 phút");
    expect(fresh).not.toHaveAttribute("data-late");
    // An answered customer waits for nobody.
    expect(within(list).getAllByTestId("cskh-wait")).toHaveLength(2);
    expect(within(list).getAllByTestId("cskh-page-tag")[0]).toHaveTextContent("Tiểu Eva");
    expect(screen.getByRole("tab", { name: /Gọi lại/ })).toHaveTextContent("3");
    // Default order "Chờ lâu nhất"; filters go in the request and show as chips.
    expect(sent(stub, "GET", "/api/cskh/conversations?")[0]?.url).toContain("sort=wait");
    await userEvent.click(screen.getByRole("button", { name: /^Lọc/ }));
    const panel = screen.getByRole("dialog", { name: "Lọc hội thoại" });
    await userEvent.selectOptions(within(panel).getByLabelText("Lọc theo trạng thái"), "CALLBACK");
    await userEvent.click(within(panel).getByRole("checkbox"));
    await waitFor(() =>
      expect(sent(stub, "GET", "mine=true").at(-1)?.url).toContain("stage=CALLBACK"),
    );
    const chips = screen.getAllByTestId("cskh-filter-chip");
    expect(chips.map((chip) => chip.textContent)).toEqual(["Chờ gọi lại", "Của tôi"]);
    await userEvent.click(screen.getByRole("button", { name: "Bỏ lọc Của tôi" }));
    expect(screen.getAllByTestId("cskh-filter-chip")).toHaveLength(1);
    vi.restoreAllMocks();
  });

  it("confirms a phone from the bubble, shows the code and Pancake notes, changes the stage", async () => {
    params.value = new URLSearchParams("c=c1");
    const detail = {
      ...DETAIL,
      conversation: {
        ...DETAIL.conversation,
        stage: "NEW",
        codes: ["KH1426035"],
        notes: [
          {
            id: "n1",
            text: "KH1426035 — khách hỏi giá gói tân trang, hẹn gọi lại sau 20h",
            by: "Care page",
            at: "2026-11-11T15:45:00+00:00",
          },
        ],
      },
      messages: [
        {
          ...DETAIL.messages[0],
          text: "Số của chị đây 032 593 7871",
          attachments: [],
          qr: null,
        },
      ],
    };
    const stub = stubFetch([
      { match: "/api/units/me", body: me({ inbox: true }) },
      { match: "/api/cskh/state", body: STATE },
      { match: "/api/cskh/conversations/c1/stage", method: "POST", body: { stage: "CONSULTING" } },
      { match: "/phone-check", body: { ...CHECK_DUP, need_ack: false, is_new: true, seen: [] } },
      {
        match: "/api/cskh/conversations/c1/zalo",
        body: { threads: [], codes: [], suggest: [], may_link: false },
      },
      { match: "/api/cskh/conversations/c1", body: detail },
      { match: "/api/cskh/conversations", body: { conversations: [ROW], next_before: null } },
    ]);
    renderWithQuery(<InboxPage />);
    const card = await screen.findByTestId("cskh-customer");
    expect(within(card).getByTestId("cskh-code")).toHaveTextContent("KH1426035");
    expect(within(card).getByText("từ ghi chú Pancake")).toBeInTheDocument();
    expect(within(card).getByTestId("cskh-notes")).toHaveTextContent("Care page");
    await userEvent.selectOptions(screen.getByLabelText("Trạng thái hội thoại"), "CONSULTING");
    await waitFor(() =>
      expect(sent(stub, "POST", "/conversations/c1/stage")[0]?.body).toEqual({ stage: "CONSULTING" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận số 0325937871" }));
    const dialog = await screen.findByRole("dialog", { name: "Xác nhận số điện thoại" });
    expect(within(dialog).getByDisplayValue("0325937871")).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue("KH1426035")).toBeInTheDocument();
  });
});

describe("Hộp chat Zalo", () => {
  it("shows the matched page conversation masked when matched by code", async () => {
    params.value = new URLSearchParams("t=z1");
    const thread = {
      id: "z1",
      account_id: "acc1",
      account_name: "Apexmed - Ekip Bs Tiến",
      account_status: "connected",
      sale_user_id: "u-tele",
      sale_name: "Hà Tele",
      peer_uid: "555",
      peer_name: "Anna Hồ",
      peer_avatar: "",
      is_group: false,
      last_text: "Mã khách KH1241242",
      last_out: true,
      last_at: "2026-11-10T04:00:00Z",
      unread: 0,
      conversation_id: "c1",
      customer_name: "Ngô Trọng Huy",
      page_id: PAGE.id,
      page_name: PAGE.name,
      conversation_phones: ["0912 34* ***"],
      customer_code: "KH1241242",
      link_how: "Mã KH KH1241242 trong tin Zalo",
      link_kind: "code",
      masked: true,
    };
    const stub = stubFetch([
      { match: "/api/units/me", body: me({ zalo: true }) },
      {
        match: "/api/cskh/zalo/accounts",
        body: {
          accounts: [],
          bridge: { configured: true, alive: true },
          may_manage: false,
          may_scan: true,
          sales: [],
        },
      },
      {
        match: "/api/cskh/zalo/threads/z1/page-send",
        method: "POST",
        body: { ...DETAIL.messages[0], id: "m3", direction: "out" },
      },
      {
        match: "/api/cskh/zalo/threads/z1/page",
        body: {
          conversation: {
            id: "c1",
            page_id: PAGE.id,
            page_name: PAGE.name,
            customer_name: "Ngô Trọng Huy",
            phones: ["0912 34* ***"],
            connected: true,
          },
          messages: [{ ...DETAIL.messages[0], text: "Số em 0912 34* ***", attachments: [], qr: null }],
          masked: true,
        },
      },
      {
        match: "/api/cskh/zalo/threads/z1",
        body: {
          thread,
          messages: [
            {
              id: "zm1",
              direction: "out",
              sender_name: "",
              text: "Mã khách KH1241242",
              kind: "text",
              at: "2026-11-10T04:00:00Z",
            },
          ],
        },
      },
      { match: "/api/cskh/zalo/threads", body: { threads: [thread] } },
    ]);
    renderWithQuery(<ZaloPage />);
    expect(await screen.findByTestId("cskh-masked")).toHaveTextContent("Ẩn 4 số cuối");
    expect(screen.getByTestId("cskh-zalo-linked")).toHaveTextContent("KH1241242");
    expect(screen.getAllByText(/0912 34\* \*\*\*/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/0912345678/)).toBeNull();
    await userEvent.type(screen.getByLabelText("Nhắn khách trên page"), "Em gọi lại ạ");
    await userEvent.click(screen.getByRole("button", { name: "Gửi" }));
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/cskh/zalo/threads/z1/page-send")[0]?.body).toEqual({
        text: "Em gọi lại ạ",
      }),
    );
  });
});

describe("Kết nối Zalo", () => {
  it("starts a QR login for the picked telesale and follows it to the end", async () => {
    const stub = stubFetch([
      { match: "/api/units/me", body: me({ zalo: true, manage: true }) },
      {
        match: "/api/cskh/zalo/accounts",
        body: {
          accounts: [],
          bridge: { configured: true, alive: true },
          may_manage: true,
          may_scan: true,
          sales: [{ user_id: "u-tele", name: "Hà Tele" }],
        },
      },
      { match: "/api/cskh/zalo/qr/s1", body: { state: "ok", accountId: "123", name: "Nick Công ty" } },
      {
        match: "/api/cskh/zalo/qr",
        method: "POST",
        body: { session: "s1", state: "qr", image: "iVBORw0KGgo=" },
      },
    ]);
    renderWithQuery(<ZaloAccountsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Kết nối nick" }));
    const dialog = await screen.findByRole("dialog", { name: "Kết nối nick Zalo" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox"), "u-tele");
    await userEvent.click(within(dialog).getByRole("button", { name: "Tạo mã QR" }));
    await waitFor(() =>
      expect(sent(stub, "POST", "/api/cskh/zalo/qr")[0]?.body).toEqual({ user_id: "u-tele" }),
    );
    expect(
      await within(dialog).findByText(/Đã kết nối nick Nick Công ty/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
  });
});

describe("Kết nối page", () => {
  it("is refused to somebody without MANAGE", async () => {
    stubFetch([{ match: "/api/units/me", body: me({ inbox: true }) }]);
    renderWithQuery(<PagesPage />);
    expect(await screen.findByTestId("cskh-forbidden")).toBeInTheDocument();
  });
});
