/**
 * "Quản trị › Kết nối AI" (0064): the OWNER's AI connections and, per
 * purpose, the main AI and its fallbacks. Keys go in and never come back.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AiConnectionsPage from "@/app/admin/ai/page";
import { adminTabsFor } from "@/components/admin-tabs";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/admin/ai",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const sent = (stub: unknown, method: string, fragment: string) =>
  calls(stub).filter((call) => call.method === method && call.url.includes(fragment));

const OWNER = { ...SESSION, role: "OWNER" };

const KINDS = [
  {
    kind: "OPENAI",
    label: "ChatGPT (OpenAI)",
    base_url: "https://api.openai.com/v1",
    model_hint: "gpt-4o-mini",
    key_hint: "API key tạo ở platform.openai.com.",
  },
  {
    kind: "DEEPSEEK",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com",
    model_hint: "deepseek-chat",
    key_hint: "API key tạo ở platform.deepseek.com.",
  },
  {
    kind: "OPENAI_COMPATIBLE",
    label: "9router / chuẩn OpenAI khác",
    base_url: null,
    model_hint: "cx/gpt-5.5 hoặc tên combo",
    key_hint: "API key tạo trong dashboard của 9router.",
  },
];

const connection = (over: Record<string, unknown> = {}) => ({
  id: "c-deepseek",
  name: "DeepSeek chính",
  kind: "DEEPSEEK",
  kind_label: "DeepSeek",
  base_url: null,
  endpoint: "https://api.deepseek.com",
  model: "deepseek-chat",
  api_key_hint: "••••1234",
  active: true,
  last_tested_at: null,
  last_test_ok: null,
  last_test_message: null,
  ...over,
});

const ROUTES = [
  { purpose: "DEFAULT", label: "Mặc định (mọi việc chưa chọn riêng)", connection_ids: [] },
  { purpose: "ORDER_SCRIPT", label: "AI duyệt kịch bản order", connection_ids: [] },
];

const overview = (over: Record<string, unknown> = {}) => ({
  connections: [
    connection(),
    connection({ id: "c-gemini", name: "Gemini", kind: "GEMINI", kind_label: "Gemini (Google)", model: "gemini-2.5-flash", api_key_hint: "••••abcd", last_test_ok: false, last_test_message: "Bị giới hạn: hết hạn mức / hết credit." }),
  ],
  routes: ROUTES,
  kinds: KINDS,
  max_chain: 3,
  secret_ready: true,
  env_provider: "openai",
  env_model: "gpt-4o-mini",
  ...over,
});

function open(data = overview(), extra: Array<{ match: string; method?: string; status?: number; body?: unknown }> = []) {
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: OWNER },
    { match: "/api/units/me", body: { units: [], can_admin: ["ADS", "PR"], can_view_all: true, default_unit: null } },
    { match: "/api/ai", method: "GET", body: data },
  ]);
  renderWithQuery(<AiConnectionsPage />);
  return stub;
}

describe("the Kết nối AI tab", () => {
  it("is the OWNER's alone", () => {
    const me = { units: [], can_admin: ["ADS"], can_view_all: true } as unknown as UnitsMe;
    expect(adminTabsFor(me, "OWNER").map((tab) => tab.href)).toContain("/admin/ai");
    expect(adminTabsFor(me, "ADMIN").map((tab) => tab.href)).not.toContain("/admin/ai");
  });
});

describe("the connections", () => {
  it("lists each with its key hint, never a key, and the last test", async () => {
    open();
    const rows = await screen.findAllByTestId("ai-connection");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("••••1234")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Chưa kiểm tra")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Lỗi")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Bị giới hạn: hết hạn mức / hết credit.")).toBeInTheDocument();
    // What answers when nothing is chosen.
    expect(screen.getByText(/Khi chưa chọn AI nào cho một việc/)).toHaveTextContent(
      "openai · gpt-4o-mini",
    );
  });

  it("adds one: the kind fills the endpoint, a 9router one needs its address", async () => {
    const stub = open(overview(), [
      { match: "/api/ai/connections", method: "POST", status: 201, body: connection({ id: "c-new" }) },
    ]);
    const form = within(await screen.findByRole("region", { name: "Thêm kết nối AI" }));
    const add = form.getByRole("button", { name: "Thêm kết nối" });
    await userEvent.selectOptions(form.getByLabelText("Loại"), "OPENAI_COMPATIBLE");
    await userEvent.type(form.getByLabelText("Model"), "cx/gpt-5.5");
    await userEvent.type(form.getByLabelText("API key"), "sk-9router");
    // No address yet: a 9router connection cannot be added.
    expect(add).toBeDisabled();
    await userEvent.type(form.getByLabelText("Địa chỉ API"), "http://9router:20128/v1");
    expect(add).toBeEnabled();
    await userEvent.click(add);
    await waitFor(() => expect(sent(stub, "POST", "/api/ai/connections")).toHaveLength(1));
    expect(sent(stub, "POST", "/api/ai/connections")[0].body).toEqual({
      kind: "OPENAI_COMPATIBLE",
      name: "9router / chuẩn OpenAI khác",
      model: "cx/gpt-5.5",
      api_key: "sk-9router",
      base_url: "http://9router:20128/v1",
    });
  });

  it("cannot store a key while the server has no encryption key", async () => {
    open(overview({ secret_ready: false }));
    expect(await screen.findByTestId("ai-no-secret")).toHaveTextContent("PR_SECRET_ENCRYPTION_KEY");
    const form = within(screen.getByRole("region", { name: "Thêm kết nối AI" }));
    await userEvent.type(form.getByLabelText("Model"), "gpt-4o-mini");
    await userEvent.type(form.getByLabelText("API key"), "sk-x");
    expect(form.getByRole("button", { name: "Thêm kết nối" })).toBeDisabled();
  });

  it("tests one and says how it went", async () => {
    const stub = open(overview(), [
      {
        match: "/api/ai/connections/c-deepseek/test",
        method: "POST",
        body: { ok: true, message: "Kết nối được, AI đã trả lời.", latency_ms: 1234, connection: connection({ last_test_ok: true }) },
      },
    ]);
    const row = within((await screen.findAllByTestId("ai-connection"))[0]);
    await userEvent.click(row.getByRole("button", { name: "Kiểm tra kết nối" }));
    expect(await row.findByText("Kết nối được, AI đã trả lời. (1.2 giây)")).toBeInTheDocument();
    expect(sent(stub, "POST", "/test")).toHaveLength(1);
  });

  it("keeps the stored key when an edit leaves it blank", async () => {
    const stub = open(overview(), [
      { match: "/api/ai/connections/c-deepseek", method: "PATCH", body: connection({ model: "deepseek-reasoner" }) },
    ]);
    const row = within((await screen.findAllByTestId("ai-connection"))[0]);
    await userEvent.click(row.getByRole("button", { name: "Sửa" }));
    expect(row.getByLabelText("API key mới")).toHaveAttribute("placeholder", "Để trống: giữ key ••••1234");
    await userEvent.clear(row.getByLabelText("Model"));
    await userEvent.type(row.getByLabelText("Model"), "deepseek-reasoner");
    await userEvent.click(row.getByRole("button", { name: "Lưu thay đổi" }));
    await waitFor(() => expect(sent(stub, "PATCH", "/api/ai/connections/c-deepseek")).toHaveLength(1));
    expect(sent(stub, "PATCH", "/api/ai/connections/c-deepseek")[0].body).toEqual({
      name: "DeepSeek chính",
      model: "deepseek-reasoner",
      base_url: null,
    });
  });
});

describe("what each connection is used for", () => {
  it("warns while no connection serves anything: the server's AI still answers", async () => {
    open();
    expect(await screen.findByTestId("ai-unassigned")).toHaveTextContent(
      "vẫn chạy bằng cấu hình server (openai · gpt-4o-mini)",
    );
    const rows = screen.getAllByTestId("ai-connection");
    expect(within(rows[0]).getByTestId("ai-used-for")).toHaveTextContent("Chưa dùng cho việc nào");
  });

  it("names the purposes a saved chain gives it", async () => {
    open(
      overview({
        routes: [
          { ...ROUTES[0], connection_ids: [] },
          { ...ROUTES[1], connection_ids: ["c-deepseek"] },
        ],
      }),
    );
    const rows = await screen.findAllByTestId("ai-connection");
    expect(within(rows[0]).getByTestId("ai-used-for")).toHaveTextContent(
      "Đang dùng cho: AI duyệt kịch bản order",
    );
    expect(screen.queryByTestId("ai-unassigned")).not.toBeInTheDocument();
  });
});

describe("the AI for each purpose", () => {
  it("chains a main AI and a fallback, and saves them behind a confirmation", async () => {
    const stub = open(overview(), [{ match: "/api/ai/routes", method: "PUT", body: overview() }]);
    const routes = within(await screen.findByRole("region", { name: "AI cho từng việc" }));
    const main = routes.getByLabelText("AI duyệt kịch bản order · AI chính");
    expect(main).toHaveValue("");
    expect(within(main).getAllByRole("option")[0]).toHaveTextContent("Dùng Mặc định");
    // A fallback opens once the main one is chosen.
    expect(routes.queryByLabelText("AI duyệt kịch bản order · Dự phòng 1")).not.toBeInTheDocument();
    await userEvent.selectOptions(main, "c-deepseek");
    const backup = routes.getByLabelText("AI duyệt kịch bản order · Dự phòng 1");
    // An AI already in the chain is not offered again.
    expect(within(backup).queryByRole("option", { name: "DeepSeek chính" })).not.toBeInTheDocument();
    await userEvent.selectOptions(backup, "c-gemini");
    expect(routes.getByText(/Có thay đổi chưa lưu/)).toBeInTheDocument();
    // The fallback whose last test failed says so.
    expect(routes.getByText("Lần kiểm tra gần nhất lỗi")).toBeInTheDocument();
    await userEvent.click(routes.getByRole("button", { name: "Lưu phân công AI" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(sent(stub, "PUT", "/api/ai/routes")).toHaveLength(1));
    expect(sent(stub, "PUT", "/api/ai/routes")[0].body).toEqual({
      routes: { DEFAULT: [], ORDER_SCRIPT: ["c-deepseek", "c-gemini"] },
    });
  });
});
