/**
 * Password login and the account screen.
 *
 * The login page sends the id and password in the body and nothing else, goes
 * where the server's `must_change_password` says, and shows the server's own
 * sentence for a refusal. The Shell sends an account still on the default
 * password to /account from every other screen. "Quên mật khẩu?" posts only the
 * id and shows the server's one sentence. The account page has no password
 * rules of its own (only the retype must match; the strength meter is a hint),
 * changes the password in a modal (or, while required, in the only card on the
 * page), saves the name, draws the month's figures, offers the roster only to
 * somebody the server shows it to, and crops, uploads and removes the avatar.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "@/app/login/page";
import AccountPage from "@/app/account/page";
import { Shell } from "@/components/shell";
import { Avatar, initials } from "@/components/avatar";
import { renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const REPLACED: string[] = [];
let pathname = "/account";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => {
      REPLACED.push(url);
      URL_BAR.navigate(url);
    },
    push: (url: string) => URL_BAR.navigate(url),
    refresh: vi.fn(),
    back: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  REPLACED.length = 0;
  pathname = "/account";
  vi.unstubAllGlobals();
});

type Calls = Array<{ url: string; method: string; body: unknown }>;
const callsOf = (fetchMock: unknown) => (fetchMock as { calls: Calls }).calls;

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

const ME = {
  user_id: SESSION.user_id,
  telegram_user_id: 123456789,
  telegram_username: "tuan_ads",
  full_name: "Lê Tuấn",
  role: "MEMBER",
  role_label: "Thành viên",
  units: [
    { code: "ADS", label: "Phòng Ads", role_label: "Biên kịch", member_code: "TUAN" },
  ],
  must_change_password: false,
  password_changed_at: "2026-10-01T03:00:00Z",
  stats: STATS,
};

const ROWS = [
  {
    user_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    full_name: "Hà Chi",
    telegram_user_id: 111,
    units: ["PR"],
    role_label: "Thành viên",
    last_login_at: null,
    has_custom_password: false,
    locked: false,
    stats: { ...STATS, points: 3 },
  },
  {
    user_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    full_name: "Minh Đức",
    telegram_user_id: 222,
    units: ["ADS"],
    role_label: "Dựng",
    last_login_at: "2026-10-07T03:00:00Z",
    has_custom_password: true,
    locked: false,
    stats: { ...STATS, points: 20 },
  },
  {
    user_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    full_name: "Thu Trang",
    telegram_user_id: 333,
    units: ["PR", "ADS"],
    role_label: "Thiết kế",
    last_login_at: null,
    has_custom_password: true,
    locked: true,
    stats: { ...STATS, points: 8 },
  },
  {
    user_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    full_name: "Lê Khôi",
    telegram_user_id: 444,
    units: ["ADS"],
    role_label: "Biên kịch",
    last_login_at: null,
    has_custom_password: false,
    password_temporary: true,
    locked: false,
    stats: { ...STATS, points: 1 },
  },
];

const FORBIDDEN = {
  error: { code: "account_members_forbidden", message: "Không có quyền xem.", details: {} },
};

describe("the login page", () => {
  const fill = async (id: string, password: string) => {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("ID Telegram"), id);
    await user.type(screen.getByLabelText("Mật khẩu"), password);
    await user.click(screen.getByRole("button", { name: "Đăng nhập" }));
  };

  it("posts the trimmed id and the password, then opens the dashboard", async () => {
    const fetchMock = stubFetch([
      { match: "/api/auth/password-login", method: "POST", body: { must_change_password: false } },
    ]);
    renderWithQuery(<LoginPage />);
    expect(screen.getByRole("heading", { name: "Đăng nhập TasksBot" })).toBeInTheDocument();
    const id = screen.getByLabelText("ID Telegram");
    expect(id).toHaveAttribute("inputmode", "numeric");
    expect(id).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Mật khẩu")).toHaveAttribute("autocomplete", "current-password");
    // The bot route is still offered.
    expect(screen.getByText(/Hoặc đăng nhập bằng Telegram/)).toHaveTextContent("/web");

    await fill("  123456789 ", "Bi-mat-2026");
    await waitFor(() => expect(REPLACED).toEqual(["/dashboard"]));
    const [call] = callsOf(fetchMock);
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ username: "123456789", password: "Bi-mat-2026" });
  });

  it("sends a default-password account to the password change", async () => {
    stubFetch([
      { match: "/api/auth/password-login", method: "POST", body: { must_change_password: true } },
    ]);
    renderWithQuery(<LoginPage />);
    await fill("123456789", "Apm@2026");
    await waitFor(() => expect(REPLACED).toEqual(["/account?doi-mat-khau=1"]));
  });

  it("toggles the password between hidden and shown", async () => {
    renderWithQuery(<LoginPage />);
    const field = screen.getByLabelText("Mật khẩu");
    expect(field).toHaveAttribute("type", "password");
    await userEvent.setup().click(screen.getByRole("button", { name: "Hiện mật khẩu" }));
    expect(field).toHaveAttribute("type", "text");
  });

  it("shows the server's sentence for a wrong id or password", async () => {
    stubFetch([
      {
        match: "/api/auth/password-login",
        method: "POST",
        status: 401,
        body: { error: { code: "login_failed", message: "Sai ID Telegram hoặc mật khẩu.", details: {} } },
      },
    ]);
    renderWithQuery(<LoginPage />);
    await fill("123456789", "sai-mat-khau1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Sai ID Telegram hoặc mật khẩu.");
    expect(REPLACED).toEqual([]);
    // Not the session-expired prompt: this page *is* the way back in.
    expect(screen.queryByText("Bạn cần đăng nhập lại.")).not.toBeInTheDocument();
  });

  it("shows the server's sentence for a locked account", async () => {
    stubFetch([
      {
        match: "/api/auth/password-login",
        method: "POST",
        status: 429,
        body: {
          error: {
            code: "login_locked",
            message: "Tài khoản tạm khoá 15 phút do nhập sai nhiều lần.",
            details: {},
          },
        },
      },
    ]);
    renderWithQuery(<LoginPage />);
    await fill("123456789", "sai-mat-khau1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Tài khoản tạm khoá 15 phút");
  });

  it("asks for a numeric id before sending anything", async () => {
    const fetchMock = stubFetch([]);
    renderWithQuery(<LoginPage />);
    await fill("@tuan", "Bi-mat-2026");
    expect(await screen.findByRole("alert")).toHaveTextContent("chỉ gồm chữ số");
    expect(callsOf(fetchMock)).toHaveLength(0);
  });
});

describe("forgot password", () => {
  const RESET_MESSAGE = "Nếu ID tồn tại, mật khẩu tạm đã được gửi qua Telegram.";

  it("posts only the id and shows the server's sentence", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/auth/password-reset",
        method: "POST",
        status: 202,
        body: { message: RESET_MESSAGE },
      },
    ]);
    renderWithQuery(<LoginPage />);
    const user = userEvent.setup();
    // The id already typed into the login form carries over.
    await user.type(screen.getByLabelText("ID Telegram"), "123456789");
    await user.click(screen.getByRole("button", { name: "Quên mật khẩu?" }));
    expect(screen.getByRole("heading", { name: "Quên mật khẩu" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Mật khẩu")).not.toBeInTheDocument();
    const id = screen.getByLabelText("ID Telegram");
    expect(id).toHaveValue("123456789");
    await user.clear(id);
    await user.type(id, " 987654321 ");
    await user.click(screen.getByRole("button", { name: "Gửi mật khẩu tạm" }));

    expect(await screen.findByRole("status")).toHaveTextContent(RESET_MESSAGE);
    const [call] = callsOf(fetchMock);
    expect(call.url).toBe("/api/auth/password-reset");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ username: "987654321" });
    expect(REPLACED).toEqual([]);

    // And back to the login form.
    await user.click(screen.getByRole("button", { name: "Quay lại đăng nhập" }));
    expect(screen.getByRole("heading", { name: "Đăng nhập TasksBot" })).toBeInTheDocument();
    expect(screen.getByLabelText("ID Telegram")).toHaveValue(" 987654321 ");
  });

  it("asks for a numeric id before sending anything", async () => {
    const fetchMock = stubFetch([]);
    renderWithQuery(<LoginPage />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Quên mật khẩu?" }));
    await user.type(screen.getByLabelText("ID Telegram"), "@tuan");
    await user.click(screen.getByRole("button", { name: "Gửi mật khẩu tạm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("chỉ gồm chữ số");
    expect(callsOf(fetchMock)).toHaveLength(0);
  });
});

describe("the forced password change", () => {
  it("leaves every other screen for /account and renders none of it", async () => {
    pathname = "/tasks";
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, must_change_password: true } },
    ]);
    renderWithQuery(
      <Shell>
        <p>Bảng task</p>
      </Shell>,
    );
    await waitFor(() => expect(REPLACED).toEqual(["/account?doi-mat-khau=1"]));
    expect(screen.queryByText("Bảng task")).not.toBeInTheDocument();
  });

  it("renders /account itself", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, must_change_password: true } },
    ]);
    renderWithQuery(
      <Shell>
        <p>Trang tài khoản</p>
      </Shell>,
    );
    expect(await screen.findByText("Trang tài khoản")).toBeInTheDocument();
    expect(REPLACED).toEqual([]);
  });

  it("does not redirect a session that has changed its password", async () => {
    pathname = "/tasks";
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, must_change_password: false } },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(
      <Shell>
        <p>Bảng task</p>
      </Shell>,
    );
    expect(await screen.findByText("Bảng task")).toBeInTheDocument();
    expect(REPLACED).toEqual([]);
  });

  it("links the sign-in prompt to the password login", async () => {
    stubFetch([{ match: "/api/auth/session", status: 401, body: { detail: "Not authenticated" } }]);
    renderWithQuery(<Shell>{null}</Shell>);
    expect(
      await screen.findByRole("link", { name: /đăng nhập bằng ID Telegram và mật khẩu/ }),
    ).toHaveAttribute("href", "/login");
  });
});

describe("the account entry and sign-out", () => {
  it("puts the account button where sign-out was, and sign-out on the account page", async () => {
    const fetchMock = stubFetch([
      { match: "/api/auth/logout", method: "POST", status: 204, body: null },
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    expect(await screen.findByRole("link", { name: "Tài khoản" })).toHaveAttribute("href", "/account");
    expect(screen.queryByRole("button", { name: "Đăng xuất" })).not.toBeInTheDocument();
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    expect(within(primary).queryByRole("link", { name: /Tài khoản/ })).not.toBeInTheDocument();

    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Đăng xuất" }));
    await waitFor(() =>
      expect(
        (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.some((call) =>
          call.url.includes("/api/auth/logout"),
        ),
      ).toBe(true),
    );
  });
});

describe("the forced password change card", () => {
  it("shows only the form and sign-out, and only checks the retype", async () => {
    const fetchMock = stubFetch([
      { match: "/api/account/password", method: "POST", status: 204 },
      { match: "/api/account/me", body: { ...ME, must_change_password: true, password_changed_at: null } },
    ]);
    renderWithQuery(<AccountPage />);
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Bạn cần đổi mật khẩu");
    expect(banner).toHaveTextContent("mật khẩu mặc định");
    expect(screen.getByRole("heading", { name: "Đặt mật khẩu mới" })).toBeInTheDocument();
    // A focused card: no profile header, no tabs, no modal, no roster request.
    expect(screen.queryByRole("region", { name: "Hồ sơ của tôi" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Đổi ảnh đại diện" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đăng xuất" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Đổi mật khẩu" })).toHaveLength(1);
    // No rules checklist: whatever the person likes is the server's call.
    expect(screen.queryByRole("list", { name: "Yêu cầu mật khẩu" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Ít nhất 8 ký tự/)).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Mật khẩu hiện tại"), "Apm@2026");
    await user.type(screen.getByLabelText("Mật khẩu mới"), "meo");
    // The meter is a hint, not a gate.
    expect(screen.getByText("Yếu")).toBeInTheDocument();
    expect(screen.getByText(/chỉ để tham khảo, không bắt buộc/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Nhập lại mật khẩu mới"), "meow");
    await user.click(screen.getByRole("button", { name: "Đổi mật khẩu" }));
    expect(screen.getByText("Nhập lại chưa khớp mật khẩu mới.")).toBeInTheDocument();
    expect(screen.getByLabelText("Nhập lại mật khẩu mới")).toHaveAttribute("aria-invalid", "true");
    expect(callsOf(fetchMock).filter((call) => call.method === "POST")).toHaveLength(0);

    // A three-letter password is fine as long as the retype matches.
    await user.clear(screen.getByLabelText("Nhập lại mật khẩu mới"));
    await user.type(screen.getByLabelText("Nhập lại mật khẩu mới"), "meo");
    expect(screen.getByText("Đã khớp mật khẩu mới.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Đổi mật khẩu" }));
    await waitFor(() => expect(REPLACED).toEqual(["/account"]));
    const post = callsOf(fetchMock).find((call) => call.method === "POST");
    expect(post?.url).toBe("/api/account/password");
    expect(post?.body).toEqual({ current_password: "Apm@2026", new_password: "meo" });
    expect(callsOf(fetchMock).some((call) => call.url.includes("/api/account/members"))).toBe(false);
  });

  it("says what is missing before sending anything", async () => {
    const fetchMock = stubFetch([
      { match: "/api/account/me", body: { ...ME, must_change_password: true } },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Đổi mật khẩu" }));
    expect(screen.getByText("Nhập mật khẩu hiện tại.")).toBeInTheDocument();
    expect(screen.getByText("Nhập mật khẩu mới.")).toBeInTheDocument();
    expect(screen.getByLabelText("Mật khẩu hiện tại")).toHaveFocus();
    expect(callsOf(fetchMock).filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("shows the server's sentence for the default password", async () => {
    stubFetch([
      {
        match: "/api/account/password",
        method: "POST",
        status: 422,
        body: {
          error: {
            code: "password_is_default",
            message: "Mật khẩu mới không được trùng mật khẩu mặc định.",
            details: { reason: "password_is_default" },
          },
        },
      },
      { match: "/api/account/me", body: { ...ME, must_change_password: true } },
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Mật khẩu hiện tại"), "Apm@2026");
    await user.type(screen.getByLabelText("Mật khẩu mới"), "Apm@2026");
    await user.type(screen.getByLabelText("Nhập lại mật khẩu mới"), "Apm@2026");
    await user.click(screen.getByRole("button", { name: "Đổi mật khẩu" }));
    expect(
      await screen.findByText("Mật khẩu mới không được trùng mật khẩu mặc định."),
    ).toBeInTheDocument();
    expect(REPLACED).toEqual([]);
  });

  it("says when the account is on a temporary password", async () => {
    stubFetch([
      {
        match: "/api/account/me",
        body: {
          ...ME,
          must_change_password: true,
          password_temporary: true,
          has_custom_password: false,
          password_changed_at: null,
        },
      },
    ]);
    renderWithQuery(<AccountPage />);
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("mật khẩu tạm gửi qua Telegram");
    expect(banner).not.toHaveTextContent("mật khẩu mặc định");
  });
});

describe("the change-password dialog", () => {
  const ROUTES = [
    { match: "/api/account/members", status: 403, body: FORBIDDEN },
    { match: "/api/account/me", body: ME },
  ];

  it("opens from the header, closes on Escape, and sends the two passwords", async () => {
    const fetchMock = stubFetch([
      { match: "/api/account/password", method: "POST", status: 204 },
      ...ROUTES,
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    // Performance is the landing view; the password form is not on the page.
    expect(await screen.findByRole("tab", { name: "Hiệu suất của tôi" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByLabelText("Mật khẩu hiện tại")).not.toBeInTheDocument();

    const hero = screen.getByRole("region", { name: "Hồ sơ của tôi" });
    await user.click(within(hero).getByRole("button", { name: "Đổi mật khẩu" }));
    const dialog = screen.getByRole("dialog", { name: "Đổi mật khẩu" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByLabelText("Mật khẩu hiện tại")).toHaveFocus();
    await user.type(within(dialog).getByLabelText("Mật khẩu hiện tại"), "bo-qua");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Focus goes back to the button that opened it.
    expect(within(hero).getByRole("button", { name: "Đổi mật khẩu" })).toHaveFocus();

    // Reopened, it has forgotten what was typed.
    await user.click(within(hero).getByRole("button", { name: "Đổi mật khẩu" }));
    const again = screen.getByRole("dialog", { name: "Đổi mật khẩu" });
    const currentField = within(again).getByLabelText("Mật khẩu hiện tại");
    expect(currentField).toHaveValue("");
    expect(currentField).toHaveAttribute("type", "password");
    await user.click(within(again).getByRole("button", { name: "Hiện mật khẩu hiện tại" }));
    expect(currentField).toHaveAttribute("type", "text");
    expect(within(again).getByRole("button", { name: "Ẩn mật khẩu hiện tại" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.type(currentField, "Cu-2026");
    await user.type(within(again).getByLabelText("Mật khẩu mới"), "TasksBot#2026-moi");
    expect(within(again).getByText("Mạnh")).toBeInTheDocument();
    await user.type(within(again).getByLabelText("Nhập lại mật khẩu mới"), "TasksBot#2026-moi");
    await user.click(within(again).getByRole("button", { name: "Đổi mật khẩu" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("status")).toHaveTextContent("Đã đổi mật khẩu.");
    const post = callsOf(fetchMock).find((call) => call.method === "POST");
    expect(post?.url).toBe("/api/account/password");
    expect(post?.body).toEqual({ current_password: "Cu-2026", new_password: "TasksBot#2026-moi" });
    // Not the forced flow: nowhere to be sent.
    expect(REPLACED).toEqual([]);
  });

  it("shows the server's refusal inside the dialog and stays open", async () => {
    stubFetch([
      {
        match: "/api/account/password",
        method: "POST",
        status: 422,
        body: { error: { code: "current_password_wrong", message: "Mật khẩu hiện tại không đúng.", details: {} } },
      },
      ...ROUTES,
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole("button", { name: "Đổi mật khẩu" }))[0]);
    const dialog = screen.getByRole("dialog", { name: "Đổi mật khẩu" });
    await user.type(within(dialog).getByLabelText("Mật khẩu hiện tại"), "cu-sai-1");
    await user.type(within(dialog).getByLabelText("Mật khẩu mới"), "TasksBot2026");
    await user.type(within(dialog).getByLabelText("Nhập lại mật khẩu mới"), "TasksBot2026");
    await user.click(within(dialog).getByRole("button", { name: "Đổi mật khẩu" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Mật khẩu hiện tại không đúng.");
    expect(screen.getByRole("dialog", { name: "Đổi mật khẩu" })).toBeInTheDocument();
  });

  it("is also offered from the security section", async () => {
    stubFetch(ROUTES);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Thông tin tài khoản" }));
    const security = screen.getByRole("region", { name: "Bảo mật" });
    expect(within(security).getByText("Đã đổi")).toHaveClass("st-green");
    await user.click(within(security).getByRole("button", { name: "Đổi mật khẩu" }));
    expect(screen.getByRole("dialog", { name: "Đổi mật khẩu" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Thôi" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("the account page", () => {
  it("leads with a profile header: picture, name, role, Telegram id and units", async () => {
    stubFetch([
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    const hero = await screen.findByRole("region", { name: "Hồ sơ của tôi" });
    expect(within(hero).getByRole("heading", { level: 1, name: "Lê Tuấn" })).toBeInTheDocument();
    expect(hero).toHaveTextContent("Thành viên");
    expect(hero).toHaveTextContent("123456789");
    expect(hero).toHaveTextContent("@tuan_ads");
    const units = within(hero).getByRole("list", { name: "Luồng của tôi" });
    expect(within(units).getByText("ORD")).toHaveClass("unit-tag");
    expect(units).toHaveTextContent("Biên kịch");
    expect(units).toHaveTextContent("TUAN");
    // No picture yet: the initials.
    expect(within(hero).getByRole("button", { name: "Đổi ảnh đại diện" })).toHaveTextContent("LT");
    expect(within(hero).getByRole("button", { name: "Đăng xuất" })).toBeInTheDocument();
  });

  it("shows who I am and saves the display name", async () => {
    const fetchMock = stubFetch([
      { match: "/api/account/profile", method: "PATCH", body: { ...ME, full_name: "Tuấn Lê" } },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Thông tin tài khoản" }));
    const identity = await screen.findByRole("region", { name: "Thông tin tài khoản" });
    expect(within(identity).getByText("123456789")).toBeInTheDocument();
    expect(within(identity).getByText("ORD")).toHaveClass("unit-tag");
    expect(within(identity).getByText("TUAN")).toBeInTheDocument();
    // No picture: nothing to remove.
    expect(screen.queryByRole("button", { name: "Xoá ảnh" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tải ảnh lên" })).toBeInTheDocument();

    const user = userEvent.setup();
    const name = screen.getByRole("textbox", { name: "Tên hiển thị" });
    await user.clear(name);
    await user.type(name, "  Tuấn Lê ");
    await user.click(screen.getByRole("button", { name: "Lưu tên" }));
    expect(await screen.findByText("Đã lưu tên hiển thị.")).toBeInTheDocument();
    const patch = callsOf(fetchMock).find((call) => call.method === "PATCH");
    expect(patch?.body).toEqual({ full_name: "Tuấn Lê" });
  });

  it("draws the month's figures, and asks for another month", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/account/me/stats",
        body: { ...STATS, month: "2026-09", points: 4, on_time_rate: null, performance_score: null },
      },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Hiệu suất của tôi" }));
    const performance = screen.getByRole("region", { name: "Hiệu suất của tôi" });
    const card = (label: string) =>
      within(performance).getByText(label).closest("li") as HTMLElement;
    // Six headline tiles, then the rest grouped by unit.
    const headline = within(performance).getByRole("list", { name: "Chỉ số chính" });
    expect(within(headline).getAllByRole("listitem").map((item) => item.querySelector("p")?.textContent)).toEqual([
      "Điểm hiệu suất",
      "Sản lượng",
      "Đúng hạn",
      "Không bị trả",
      "Effort",
      "Mục KPI được tính",
    ]);
    const tile = (label: string) =>
      within(headline).getByText(label).closest("li") as HTMLElement;
    expect(tile("Điểm hiệu suất")).toHaveTextContent("82,5/100");
    expect(tile("Sản lượng")).toHaveTextContent("7");
    expect(tile("Sản lượng")).toHaveTextContent("mốc 10");
    // "Đúng hạn" is the deadline now; the old "no return" share moved.
    expect(tile("Đúng hạn")).toHaveTextContent("75%");
    expect(tile("Đúng hạn")).toHaveTextContent("Số lần trễ hạn: 2");
    expect(tile("Không bị trả")).toHaveTextContent("60%");
    expect(tile("Effort")).toHaveTextContent("30/40");
    expect(tile("Effort")).toHaveTextContent("75%");
    expect(tile("Mục KPI được tính")).toHaveTextContent("11");
    const ads = within(performance).getByRole("region", { name: "Order ORD" });
    expect(within(ads).getByText("Đang làm").closest("li")).toHaveTextContent("3");
    expect(within(ads).getByText("Bị trả sửa").closest("li")).toHaveTextContent("2");
    expect(within(ads).getByText("Số lần trễ hạn").closest("li")).toHaveTextContent("2");
    expect(within(ads).getByText("Điểm loại video").closest("li")).toHaveTextContent("12,5");
    expect(within(ads).getByText("Order đã tạo / hoàn thành").closest("li")).toHaveTextContent("4 / 1");
    const pr = within(performance).getByRole("region", { name: "Nội dung PR" });
    expect(within(pr).getByText("Nội dung phụ trách").closest("li")).toHaveTextContent("5");
    expect(within(pr).getByText("Sản xuất").closest("li")).toHaveTextContent("6");
    expect(within(pr).getByText("Lượt duyệt").closest("li")).toHaveTextContent("9");
    // The current month came with /me: no second request for it.
    expect(callsOf(fetchMock).some((call) => call.url.includes("/me/stats"))).toBe(false);

    // jsdom has no month picker to type into; a change event is what one sends.
    fireEvent.change(screen.getByLabelText("Tháng"), { target: { value: "2026-09" } });
    await waitFor(() =>
      expect(
        callsOf(fetchMock).some((call) => call.url === "/api/account/me/stats?month=2026-09"),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Chỉ số chính" }))
          .getByText("Đúng hạn")
          .closest("li"),
      ).toHaveTextContent("–"),
    );
    expect(
      within(screen.getByRole("list", { name: "Chỉ số chính" }))
        .getByText("Điểm hiệu suất")
        .closest("li"),
    ).toHaveTextContent("–");
  });

  it("shows this week's ORD tokens, day by day, red once over", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/units/ADS/effort",
        body: {
          date_from: "2026-10-05",
          date_to: "2026-10-11",
          days: ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"],
          today: "2026-10-06",
          people: [
            {
              user_id: SESSION.user_id,
              full_name: "Lê Tuấn",
              role_label: "Biên kịch",
              is_lead: false,
              daily_tokens: 8,
              open_tokens: 4,
              open_tasks: 1,
              days: [
                { date: "2026-10-05", budget: 8, used: 6, left: 2 },
                { date: "2026-10-06", budget: 8, used: 9.5, left: -1.5 },
                { date: "2026-10-07", budget: 8, used: 0, left: 8 },
                { date: "2026-10-08", budget: 8, used: 0, left: 8 },
                { date: "2026-10-09", budget: 8, used: 0, left: 8 },
                { date: "2026-10-10", budget: 0, used: 0, left: 0 },
                { date: "2026-10-11", budget: 0, used: 0, left: 0 },
              ],
            },
          ],
        },
      },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    const week = await screen.findByRole("region", { name: "Effort tuần này" });
    expect(
      callsOf(fetchMock).some((call) => call.url === `/api/units/ADS/effort?user_id=${SESSION.user_id}`),
    ).toBe(true);
    expect(week.querySelector("header p")).toHaveTextContent("Đang ôm 4 token (1 task) · 8 token/ngày");
    const day = (heading: string) => within(week).getByText(heading).closest("li") as HTMLElement;
    expect(day("T2 05/10")).toHaveTextContent("6/8");
    expect(within(day("T2 05/10")).getByText("còn 2")).toHaveClass("text-[var(--good)]");
    expect(day("T3 06/10")).toHaveAttribute("aria-current", "date");
    expect(within(day("T3 06/10")).getByText("vượt 1.5")).toHaveClass("text-[var(--bad)]");
    expect(day("CN 11/10")).toHaveTextContent("nghỉ");
  });

  it("hides the members tab from somebody the server refuses", async () => {
    stubFetch([
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    expect(await screen.findByRole("tab", { name: "Thông tin tài khoản" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Hiệu suất của tôi" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("tab", { name: "Thành viên" })).not.toBeInTheDocument();
    expect(screen.queryByText("Không có quyền xem.")).not.toBeInTheDocument();
  });

  it("lists members, sorts by points and resets a password behind a confirmation", async () => {
    const fetchMock = stubFetch([
      { match: "/reset-password", method: "POST", status: 204 },
      {
        match: "/api/account/members",
        body: {
          month: "2026-10",
          members: ROWS.map((row, index) =>
            index === 1 ? { ...row, avatar_url: `/api/account/avatar/${row.user_id}?v=3` } : row,
          ),
        },
      },
      { match: "/api/account/me", body: { ...ME, role: "OWNER", role_label: "Chủ sở hữu" } },
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Thành viên" }));
    expect(
      callsOf(fetchMock).some((call) => call.url === "/api/account/members?month=2026-10&unit=ALL"),
    ).toBe(true);

    const table = screen.getByRole("table");
    const names = () =>
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.querySelector(".person")?.textContent);
    expect(names()).toEqual(["Hà Chi", "Minh Đức", "Thu Trang", "Lê Khôi"]);
    await user.click(within(table).getByRole("button", { name: /Điểm/ }));
    expect(names()).toEqual(["Minh Đức", "Thu Trang", "Hà Chi", "Lê Khôi"]);
    await user.click(within(table).getByRole("button", { name: /Điểm/ }));
    expect(names()).toEqual(["Lê Khôi", "Hà Chi", "Thu Trang", "Minh Đức"]);

    const rowOf = (name: string) => within(table).getByText(name).closest("tr") as HTMLElement;
    // A picture before the name when there is one, the initials otherwise.
    expect(rowOf("Minh Đức").querySelector("[data-avatar] img")).toHaveAttribute(
      "src",
      `/api/account/avatar/${ROWS[1].user_id}?v=3`,
    );
    expect(rowOf("Hà Chi").querySelector("[data-avatar]")).toHaveTextContent("HC");
    expect(rowOf("Hà Chi").querySelector("[data-avatar] img")).toBeNull();

    expect(within(rowOf("Hà Chi")).getByText("Mặc định")).toHaveClass("st-amber");
    expect(within(rowOf("Minh Đức")).getByText("Đã đổi")).toHaveClass("st-green");
    expect(within(rowOf("Thu Trang")).getByText("Đang khoá")).toHaveClass("st-red");
    expect(within(rowOf("Lê Khôi")).getByText("Mật khẩu tạm")).toHaveClass("st-amber");

    await user.click(screen.getByRole("button", { name: "Đặt lại mật khẩu của Hà Chi" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Đặt lại mật khẩu của Hà Chi?");
    // A temporary password goes to the member's Telegram - not the shared default.
    expect(dialog).toHaveTextContent("mật khẩu tạm và gửi qua Telegram");
    expect(dialog).not.toHaveTextContent("mật khẩu mặc định");
    // Nothing is sent until the dialog is confirmed.
    expect(callsOf(fetchMock).some((call) => call.method === "POST")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Đặt lại mật khẩu" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "POST")?.url).toBe(
        `/api/account/members/${ROWS[0].user_id}/reset-password`,
      ),
    );
    expect(
      await screen.findByText("Đã gửi mật khẩu tạm qua Telegram cho Hà Chi."),
    ).toBeInTheDocument();
  });

  it("offers no reset to somebody who is not an owner or admin", async () => {
    stubFetch([
      { match: "/api/account/members", body: { month: "2026-10", members: ROWS } },
      { match: "/api/account/me", body: ME },
    ]);
    renderWithQuery(<AccountPage />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Thành viên" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Đặt lại mật khẩu/ })).not.toBeInTheDocument();
  });
});

describe("the avatar", () => {
  it("draws the picture when there is one, and the initials otherwise", () => {
    const { container, rerender } = renderWithQuery(
      <Avatar name="Lê Tuấn" src="/api/account/avatar/abc?v=2" size={40} />,
    );
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "/api/account/avatar/abc?v=2");
    expect(img).toHaveAttribute("alt", "");
    expect(container.querySelector("[data-avatar]")).not.toHaveTextContent("LT");

    rerender(<Avatar name="Lê Tuấn" src={null} size={40} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[data-avatar]")).toHaveTextContent("LT");
  });

  it("falls back to the initials when the picture fails to load", () => {
    const { container } = renderWithQuery(<Avatar name="Hà Chi" src="/api/account/avatar/gone?v=1" />);
    fireEvent.error(container.querySelector("img") as HTMLImageElement);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[data-avatar]")).toHaveTextContent("HC");
  });

  it("takes the first and last word, Vietnamese letters included", () => {
    expect(initials("Tuấn Anh Lê")).toBe("TL");
    expect(initials("  đức  ")).toBe("Đ");
    expect(initials("")).toBe("?");
  });

  it("is in the top bar's account button", async () => {
    stubFetch([
      {
        match: "/api/auth/session",
        body: { ...SESSION, avatar_url: `/api/account/avatar/${SESSION.user_id}?v=4` },
      },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const account = await screen.findByRole("link", { name: "Tài khoản" });
    expect(account.querySelector("[data-avatar] img")).toHaveAttribute(
      "src",
      `/api/account/avatar/${SESSION.user_id}?v=4`,
    );
  });

  it("is the initials in the top bar without a picture", async () => {
    stubFetch([
      { match: "/api/auth/session", body: SESSION },
      { match: "/api/units/me", body: { units: [], default_unit: null, can_view_all: false, can_admin: [] } },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const account = await screen.findByRole("link", { name: "Tài khoản" });
    expect(account.querySelector("[data-avatar] img")).toBeNull();
    // "Le Trưởng Nhóm".
    expect(account.querySelector("[data-avatar]")).toHaveTextContent("LN");
  });
});

describe("changing the avatar", () => {
  const PREVIEW_URL = "blob:http://localhost/avatar-preview";
  const NEW_URL = `/api/account/avatar/${SESSION.user_id}?v=1`;
  let drawImage: ReturnType<typeof vi.fn>;
  let fillRect: ReturnType<typeof vi.fn>;

  /** jsdom has no canvas and no object URLs: stand both in. */
  const stubCanvas = (webp: boolean) => {
    drawImage = vi.fn();
    fillRect = vi.fn();
    const context = {
      drawImage,
      fillRect,
      clearRect: vi.fn(),
      fillStyle: "",
      imageSmoothingEnabled: false,
      imageSmoothingQuality: "low",
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (() => context) as unknown as HTMLCanvasElement["getContext"],
    );
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation((type?: string) =>
      type === "image/webp" && webp
        ? "data:image/webp;base64,UklGRlZQOA=="
        : type === "image/jpeg"
          ? "data:image/jpeg;base64,/9j/4AAQ"
          : // What a browser that cannot encode the type hands back.
            "data:image/png;base64,iVBORw0K",
    );
  };

  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn(() => PREVIEW_URL),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
  });

  /** Pick a file and let the preview "load" an 800x600 picture. */
  const pick = async (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) => {
    const file = new File([new Uint8Array([137, 80, 78, 71])], "meo.png", { type: "image/png" });
    await user.upload(within(dialog).getByLabelText("Chọn ảnh"), file);
    const preview = within(dialog).getByAltText("Ảnh đang chọn") as HTMLImageElement;
    expect(preview).toHaveAttribute("src", PREVIEW_URL);
    Object.defineProperty(preview, "naturalWidth", { configurable: true, value: 800 });
    Object.defineProperty(preview, "naturalHeight", { configurable: true, value: 600 });
    fireEvent.load(preview);
    return preview;
  };

  const ROUTES = [
    { match: "/api/account/members", status: 403, body: FORBIDDEN },
    { match: "/api/account/me", body: ME },
  ];

  it("previews the chosen file, crops it to 256x256 WebP and uploads it", async () => {
    stubCanvas(true);
    const fetchMock = stubFetch([
      { match: "/api/account/avatar", method: "PUT", body: { avatar_url: NEW_URL } },
      ...ROUTES,
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Đổi ảnh đại diện" }));
    const dialog = screen.getByRole("dialog", { name: "Ảnh đại diện" });
    expect(within(dialog).getByLabelText("Chọn ảnh")).toHaveAttribute(
      "accept",
      "image/png,image/jpeg,image/webp,image/gif",
    );
    expect(within(dialog).getByRole("button", { name: "Lưu ảnh" })).toBeDisabled();

    await pick(user, dialog);
    // The round previews show the same picture.
    const previews = within(dialog).getByRole("group", { name: "Xem trước ảnh đại diện" });
    expect(previews.querySelectorAll("img")).toHaveLength(3);
    expect(within(dialog).getByLabelText("Thu phóng")).toHaveValue("1");

    await user.click(within(dialog).getByRole("button", { name: "Lưu ảnh" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // The centred square of an 800x600 picture: x 100..700, all of y.
    const [, sx, sy, side, sideY, dx, dy, width, height] = drawImage.mock.calls[0];
    expect(sx).toBeCloseTo(100);
    expect(sy).toBeCloseTo(0);
    expect(side).toBeCloseTo(600);
    expect(sideY).toBeCloseTo(600);
    expect([dx, dy, width, height]).toEqual([0, 0, 256, 256]);
    expect(HTMLCanvasElement.prototype.toDataURL).toHaveBeenCalledWith("image/webp", 0.85);

    const put = callsOf(fetchMock).find((call) => call.method === "PUT");
    expect(put?.url).toBe("/api/account/avatar");
    // Base64 without the data: prefix.
    expect(put?.body).toEqual({ content_type: "image/webp", data: "UklGRlZQOA==" });

    // The new picture is on the page straight away.
    expect(await screen.findByRole("status")).toHaveTextContent("Đã cập nhật ảnh đại diện.");
    const hero = screen.getByRole("region", { name: "Hồ sơ của tôi" });
    expect(within(hero).getByRole("button", { name: "Đổi ảnh đại diện" }).querySelector("img")).toHaveAttribute(
      "src",
      NEW_URL,
    );
  });

  it("falls back to JPEG on white where the browser cannot encode WebP", async () => {
    stubCanvas(false);
    const fetchMock = stubFetch([
      { match: "/api/account/avatar", method: "PUT", body: { avatar_url: NEW_URL } },
      ...ROUTES,
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Đổi ảnh đại diện" }));
    const dialog = screen.getByRole("dialog", { name: "Ảnh đại diện" });
    await pick(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Lưu ảnh" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "PUT")?.body).toEqual({
        content_type: "image/jpeg",
        data: "/9j/4AAQ",
      }),
    );
    expect(HTMLCanvasElement.prototype.toDataURL).toHaveBeenCalledWith("image/jpeg", 0.85);
    expect(fillRect).toHaveBeenCalledWith(0, 0, 256, 256);
  });

  it("refuses a file that is not a picture before reading it", async () => {
    stubCanvas(true);
    stubFetch(ROUTES);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Đổi ảnh đại diện" }));
    const dialog = screen.getByRole("dialog", { name: "Ảnh đại diện" });
    fireEvent.change(within(dialog).getByLabelText("Chọn ảnh"), {
      target: { files: [new File(["x"], "ghi-chu.txt", { type: "text/plain" })] },
    });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Chỉ nhận ảnh PNG, JPG, WEBP hoặc GIF.");
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(within(dialog).queryByAltText("Ảnh đang chọn")).not.toBeInTheDocument();
  });

  it("shows the server's refusal and keeps the dialog open", async () => {
    stubCanvas(true);
    stubFetch([
      {
        match: "/api/account/avatar",
        method: "PUT",
        status: 422,
        body: { error: { code: "avatar_too_large", message: "Ảnh quá lớn (tối đa 300 KB).", details: {} } },
      },
      ...ROUTES,
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Đổi ảnh đại diện" }));
    const dialog = screen.getByRole("dialog", { name: "Ảnh đại diện" });
    await pick(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Lưu ảnh" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Ảnh quá lớn (tối đa 300 KB).");
    expect(screen.getByRole("dialog", { name: "Ảnh đại diện" })).toBeInTheDocument();
  });

  it("removes the picture behind a confirmation", async () => {
    const withAvatar = { ...ME, avatar_url: NEW_URL };
    const fetchMock = stubFetch([
      { match: "/api/account/avatar", method: "DELETE", status: 204 },
      { match: "/api/account/members", status: 403, body: FORBIDDEN },
      { match: "/api/account/me", body: withAvatar },
    ]);
    renderWithQuery(<AccountPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Thông tin tài khoản" }));
    const profile = screen.getByRole("region", { name: "Hồ sơ" });
    expect(profile.querySelector("[data-avatar] img")).toHaveAttribute("src", NEW_URL);
    await user.click(within(profile).getByRole("button", { name: "Xoá ảnh" }));
    const dialog = screen.getByRole("dialog", { name: "Xoá ảnh đại diện?" });
    // Nothing is sent until the dialog is confirmed.
    expect(callsOf(fetchMock).some((call) => call.method === "DELETE")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Xoá ảnh" }));
    await waitFor(() =>
      expect(callsOf(fetchMock).find((call) => call.method === "DELETE")?.url).toBe("/api/account/avatar"),
    );
    // Back to the initials, and nothing left to remove.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Xoá ảnh" })).not.toBeInTheDocument());
    const hero = screen.getByRole("region", { name: "Hồ sơ của tôi" });
    expect(within(hero).getByRole("button", { name: "Đổi ảnh đại diện" })).toHaveTextContent("LT");
    expect(screen.getByRole("status")).toHaveTextContent("Đã xoá ảnh đại diện.");
  });
});
