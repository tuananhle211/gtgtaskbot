/**
 * Step 1F.2.3e, browser half: content types and review resources.
 *
 * Sections 103-105 are the type; 106-108 are the resources, and 108 is the one
 * worth reading first. It is the review-screen regression: a Team Lead opening a
 * piece to approve it must see the brief without leaving the page, the required
 * item must be impossible to miss, and the reference material must never be
 * mistaken for the production submissions - which live in a different section
 * with a different heading and are the thing being judged rather than the thing
 * to judge it against.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONTENT,
  confirm,
  dialog,
  renderWithQuery,
  SESSION,
  settleLanes,
  stubFetch,
  urlStore,
  VERSION,
} from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const replaced: string[] = [];

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => {
      replaced.push(url);
      URL_BAR.navigate(url);
    },
    push: vi.fn(),
  }),
}));

const { default: ContentBoardPage } = await import("@/app/pr/content/page");
const { default: ContentDetailPage } = await import("@/app/pr/content/[id]/page");

const BRAND = { id: CONTENT.brand_id, code: "BR-1", name: "Apexmed" };
const CHANNEL = {
  id: "77777777-7777-7777-7777-777777777777",
  code: "CH-1",
  name: "Apexmed TikTok",
  platform_id: "88888888-8888-8888-8888-888888888888",
};

const RESOURCE = {
  id: "99999999-9999-9999-9999-999999999999",
  content_id: CONTENT.id,
  resource_type: "REFERENCE",
  label: "Brief khách hàng",
  location: "https://drive.google.com/file/d/1AbCdEf/view",
  note: "Xem mục 3",
  required_for_review: true,
  is_link: true,
  added_by_user_id: SESSION.user_id,
  created_at: "2026-08-01T03:00:00+00:00",
  updated_at: "2026-08-01T03:00:00+00:00",
};

const OPTIONAL_RESOURCE = {
  ...RESOURCE,
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  resource_type: "IMAGE",
  label: "Ảnh packshot sản phẩm",
  location: "/volume1/PR/2026/packshot.jpg",
  note: null,
  required_for_review: false,
  is_link: false,
};

// --- Board harness ----------------------------------------------------------

const boardBody = (items: unknown[], total = items.length) => ({
  items,
  total,
  scope: "ALL",
  stage_counts: [{ stage: "IDEA", count: total }],
  production_state_counts: [],
  limit: 60,
  offset: 0,
});

const boardRoutes = (body: unknown) => [
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/api/pr/platforms", body: [{ id: CHANNEL.platform_id, code: "TIKTOK", name: "TikTok" }] },
  { match: "/api/pr/channels", body: [CHANNEL] },
  { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }] },
  { match: "/api/pr/contents/board", body },
];

async function openBoard(items: unknown[]) {
  const stub = stubFetch(boardRoutes(boardBody(items)));
  renderWithQuery(<ContentBoardPage />);
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Bảng nội dung" })).toBeInTheDocument(),
  );
  // Step 1F.2.3c2: the cards arrive per lane, after the figures do.
  await settleLanes();
  return stub;
}

function boardRequests(stub: ReturnType<typeof stubFetch>): URLSearchParams[] {
  return (stub as unknown as { calls: Array<{ url: string }> }).calls
    .filter((call) => call.url.includes("/api/pr/contents/board"))
    .map((call) => new URLSearchParams(call.url.split("?")[1] ?? ""));
}

// --- Detail harness ---------------------------------------------------------

const detailRoutes = (
  actions: Array<{ action: string; decision?: string }>,
  content: Record<string, unknown> = CONTENT,
  resources: unknown[] = [],
) => [
  {
    match: `/api/pr/contents/${CONTENT.id}/available-actions`,
    body: {
      content_id: CONTENT.id,
      workflow_stage: content.workflow_stage,
      available_actions: actions,
    },
  },
  { match: `/api/pr/contents/${CONTENT.id}/resources`, body: resources },
  {
    match: `/api/pr/contents/${CONTENT.id}/review-context`,
    body: { content, current_version: VERSION, approvals: [], tasks: [] },
  },
  { match: `/api/pr/contents/${CONTENT.id}/versions`, body: [VERSION] },
  { match: `/api/pr/contents/${CONTENT.id}/history`, body: { transitions: [] } },
  { match: `/api/pr/contents/${CONTENT.id}/production`, body: { production_state: null } },
  { match: `/api/pr/contents/${CONTENT.id}/ai-review`, body: { active: false, runs: [] } },
  {
    match: `/api/pr/contents/${CONTENT.id}`,
    body: { content, current_version: VERSION, targets: [], brand: BRAND },
  },
  { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }] },
];

async function openDetail(
  actions: Array<{ action: string; decision?: string }>,
  options: {
    content?: Record<string, unknown>;
    resources?: unknown[];
    tab?: string;
    extra?: Array<{ match: string; status?: number; body?: unknown; method?: string }>;
  } = {},
) {
  const content = options.content ?? CONTENT;
  const stub = stubFetch([
    ...(options.extra ?? []),
    ...detailRoutes(actions, content, options.resources ?? []),
  ]);
  renderWithQuery(<ContentDetailPage />);
  await screen.findByRole("tab", { name: "Tổng quan" });
  if (options.tab) {
    await userEvent.click(screen.getByRole("tab", { name: options.tab }));
  }
  return stub;
}

/** The `<dd>` beside a `<dt>` in the overview grid. */
async function overviewCell(term: string) {
  const label = await screen.findByText(term);
  const cell = label.parentElement?.querySelector("dd");
  expect(cell).toBeTruthy();
  return cell as HTMLElement;
}

beforeEach(() => {
  SEARCH.value = new URLSearchParams({ scope: "ALL" });
  replaced.length = 0;
});

// =============================================================================
// 103. Content type when creating
// =============================================================================

describe("103. creating content asks what kind of thing it is", () => {
  it("offers the six formats in Vietnamese and nothing else", async () => {
    await openBoard([]);
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));

    const select = await screen.findByRole("combobox", { name: /Loại nội dung/ });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— chọn —",
      "Kịch bản siêu ngắn",
      "Kịch bản video ngắn",
      "Bài đăng Facebook",
      "Kịch bản YouTube dài",
      "Báo chí",
      "TVC doanh nghiệp",
    ]);
    // "Chưa phân loại" is where a historical row starts, never something to
    // choose - so the create form must not offer it.
    expect(within(select).queryByText("Chưa phân loại")).not.toBeInTheDocument();
  });

  it("starts unanswered rather than guessing a format", async () => {
    await openBoard([]);
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));
    // Unlike priority, which is pre-set to its default: there is no sensible
    // default format, and a pre-filled one would record a choice nobody made.
    expect(await screen.findByRole("combobox", { name: /Loại nội dung/ })).toHaveValue("");
  });

  it("does not show the raw codes to whoever is filling the form", async () => {
    await openBoard([]);
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo nội dung" }));
    const select = await screen.findByRole("combobox", { name: /Loại nội dung/ });
    expect(select.textContent).not.toContain("SHORT_VIDEO_SCRIPT");
    expect(select.textContent).not.toContain("CORPORATE_TVC");
  });
});

// =============================================================================
// 104. Content type on cards and in the filter
// =============================================================================

describe("104. content type on the board", () => {
  it("badges every card, unclassified ones included", async () => {
    await openBoard([
      { ...CONTENT, id: "aaa", title: "Bài một", workflow_stage: "IDEA" },
      { ...CONTENT, id: "bbb", title: "Bài hai", workflow_stage: "IDEA", content_type: null },
    ]);
    const board = screen.getByRole("region", { name: "Bảng nội dung" });

    expect(within(board).getByText("Kịch bản video ngắn")).toBeInTheDocument();
    // A blank would be indistinguishable from a bug; "Chưa phân loại" is a real
    // state somebody can act on.
    expect(within(board).getByText("Chưa phân loại")).toBeInTheDocument();
    expect(board.textContent).not.toContain("SHORT_VIDEO_SCRIPT");
  });

  it("puts the priority badge before the type badge", async () => {
    // Priority is the operational signal and keeps the front of the reading
    // order; the type is classification.
    await openBoard([
      { ...CONTENT, id: "aaa", title: "Bài một", workflow_stage: "IDEA", priority: "CRITICAL" },
    ]);
    const board = screen.getByRole("region", { name: "Bảng nội dung" });
    const text = board.textContent ?? "";
    expect(text.indexOf("Rất gấp")).toBeGreaterThan(-1);
    expect(text.indexOf("Rất gấp")).toBeLessThan(text.indexOf("Kịch bản video ngắn"));
  });

  it("offers the filter in Bộ lọc, with Chưa phân loại last", async () => {
    SEARCH.value = new URLSearchParams();
    await openBoard([]);
    const filters = screen.getByRole("region", { name: "Bộ lọc" });

    const select = within(filters).getByRole("combobox", { name: "Lọc theo loại nội dung" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Mọi loại nội dung",
      "Kịch bản siêu ngắn",
      "Kịch bản video ngắn",
      "Bài đăng Facebook",
      "Kịch bản YouTube dài",
      "Báo chí",
      "TVC doanh nghiệp",
      "Chưa phân loại",
    ]);
  });

  it("sends the chosen format to the server and writes it to the URL", async () => {
    const stub = await openBoard([]);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo loại nội dung" }),
      "PRESS_ARTICLE",
    );

    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    expect(new URLSearchParams(replaced.at(-1)!.split("?")[1]).get("content_type")).toBe(
      "PRESS_ARTICLE",
    );
    await waitFor(() =>
      expect(boardRequests(stub).at(-1)!.get("content_type")).toBe("PRESS_ARTICLE"),
    );
  });

  it("asks the server for the unclassified slice too", async () => {
    const stub = await openBoard([]);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo loại nội dung" }),
      "UNCLASSIFIED",
    );
    await waitFor(() =>
      expect(boardRequests(stub).at(-1)!.get("content_type")).toBe("UNCLASSIFIED"),
    );
  });

  it("restores the filter from the URL and drops the page when it changes", async () => {
    SEARCH.value = new URLSearchParams({ scope: "ALL", content_type: "FACEBOOK_POST", page: "2" });
    const stub = await openBoard([]);
    expect(boardRequests(stub).at(-1)!.get("content_type")).toBe("FACEBOOK_POST");
    expect(screen.getByRole("combobox", { name: "Lọc theo loại nội dung" })).toHaveValue(
      "FACEBOOK_POST",
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Lọc theo loại nội dung" }),
      "PRESS_ARTICLE",
    );
    await waitFor(() => expect(replaced.length).toBeGreaterThan(0));
    expect(new URLSearchParams(replaced.at(-1)!.split("?")[1]).get("page")).toBeNull();
  });

  it("does not sort or group the board by content type", () => {
    // Priority is the sorting signal; the type classifies and filters. A second
    // ordering here would disagree with the pagination the server sent.
    const source = readFileSync(
      path.resolve(__dirname, "../src/app/pr/content/page.tsx"),
      "utf8",
    );
    expect(source).not.toContain(".sort(");
  });
});

// =============================================================================
// 105. Content type on the detail page
// =============================================================================

describe("105. content type on the detail page", () => {
  it("shows the format in words", async () => {
    await openDetail([], { tab: "Tổng quan" });
    expect(await overviewCell("Loại nội dung")).toHaveTextContent("Kịch bản video ngắn");
  });

  it("shows Chưa phân loại for a historical item, and still opens it", async () => {
    await openDetail([], { content: { ...CONTENT, content_type: null }, tab: "Tổng quan" });
    expect(await overviewCell("Loại nội dung")).toHaveTextContent("Chưa phân loại");
  });

  it("is read-only when the server does not offer the action", async () => {
    await openDetail([], { tab: "Tổng quan" });
    const cell = await overviewCell("Loại nội dung");
    expect(within(cell).queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("becomes a picker when the server offers SET_CONTENT_TYPE", async () => {
    await openDetail([{ action: "SET_CONTENT_TYPE" }], { tab: "Tổng quan" });
    expect(await screen.findByRole("combobox", { name: "Loại nội dung" })).toHaveValue(
      "SHORT_VIDEO_SCRIPT",
    );
  });

  it("patches the chosen format", async () => {
    const stub = await openDetail([{ action: "SET_CONTENT_TYPE" }], {
      tab: "Tổng quan",
      extra: [
        {
          match: `/api/pr/contents/${CONTENT.id}/content-type`,
          method: "PATCH",
          body: { content: { ...CONTENT, content_type: "PRESS_ARTICLE" }, targets: [] },
        },
      ],
    });

    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Loại nội dung" }),
      "PRESS_ARTICLE",
    );
    // Step 1F.2.8. The content type is matched by scoped approval grants, so
    // changing it can change who may approve the item - the confirmation says
    // so, and nothing is sent until it is accepted.
    expect(dialog().getByText(/có thể đổi ai duyệt được nội dung này/)).toBeInTheDocument();
    await confirm();

    const calls = (
      stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
    ).calls;
    const patch = calls.find((call) => call.method === "PATCH" && call.url.includes("content-type"));
    expect(patch).toBeTruthy();
    expect(patch!.body).toEqual({ content_type: "PRESS_ARTICLE" });
  });

  it("never offers Chưa phân loại as something to choose", async () => {
    await openDetail([{ action: "SET_CONTENT_TYPE" }], {
      content: { ...CONTENT, content_type: null },
      tab: "Tổng quan",
    });
    const select = await screen.findByRole("combobox", { name: "Loại nội dung" });
    // It appears as the current value and is disabled - unclassified is where a
    // row starts, not somewhere to put one back.
    const placeholder = within(select).getByRole("option", { name: "Chưa phân loại" });
    expect(placeholder).toBeDisabled();
  });
});

// =============================================================================
// 106. The resources section
// =============================================================================

describe("106. Tài nguyên & tham khảo", () => {
  it("says plainly when there is nothing", async () => {
    await openDetail([], { tab: "Duyệt" });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    expect(within(section).getByText("Chưa có tài nguyên tham khảo.")).toBeInTheDocument();
  });

  it("shows the label, the type, the location and the note", async () => {
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    expect(within(section).getByText("Brief khách hàng")).toBeInTheDocument();
    expect(within(section).getByText("Tài liệu tham khảo")).toBeInTheDocument();
    expect(within(section).getByText(RESOURCE.location)).toBeInTheDocument();
    expect(within(section).getByText(/Xem mục 3/)).toBeInTheDocument();
  });

  it("renders the server's order rather than re-sorting it", async () => {
    // Required first. The server decided; this asserts the panel does not
    // second-guess it.
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE, OPTIONAL_RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    const text = section.textContent ?? "";
    expect(text.indexOf("Brief khách hàng")).toBeLessThan(text.indexOf("Ảnh packshot sản phẩm"));
  });

  it("marks required material with the words, not only a colour", async () => {
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE, OPTIONAL_RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    expect(within(section).getAllByText("Bắt buộc xem khi duyệt")).toHaveLength(1);
  });

  it("opens an http link safely, and leaves a NAS path as text", async () => {
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE, OPTIONAL_RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    const link = within(section).getByRole("link", { name: RESOURCE.location });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));

    // A path is not a URL. No link element, so nothing tries to open it.
    expect(
      within(section).queryByRole("link", { name: OPTIONAL_RESOURCE.location }),
    ).not.toBeInTheDocument();
    expect(within(section).getByText(OPTIONAL_RESOURCE.location)).toBeInTheDocument();
  });

  it("never shows a resource id as something to read", async () => {
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    expect(section.textContent).not.toContain(RESOURCE.id);
    expect(section.textContent).not.toContain("REFERENCE");
  });
});

// =============================================================================
// 107. Managing resources, gated by the server
// =============================================================================

describe("107. adding, editing and deleting resources", () => {
  it("offers no controls without MANAGE_CONTENT_RESOURCES", async () => {
    await openDetail([], { tab: "Duyệt", resources: [RESOURCE] });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    expect(within(section).queryByRole("button", { name: /Thêm tài nguyên/ })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Sửa" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Xóa" })).not.toBeInTheDocument();
  });

  it("offers the whole form when the server allows it", async () => {
    await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], { tab: "Duyệt" });
    await userEvent.click(await screen.findByRole("button", { name: /Thêm tài nguyên/ }));

    expect(screen.getByRole("combobox", { name: /Loại tài nguyên/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Tên \/ nhãn/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Đường dẫn \/ liên kết/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Ghi chú/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Bắt buộc xem khi duyệt" })).toBeInTheDocument();
  });

  it("offers the seven resource types in Vietnamese", async () => {
    await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], { tab: "Duyệt" });
    await userEvent.click(await screen.findByRole("button", { name: /Thêm tài nguyên/ }));

    const select = screen.getByRole("combobox", { name: /Loại tài nguyên/ });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Tài liệu tham khảo",
      "Hình ảnh",
      "Video tham khảo",
      "File / Google Drive",
      "Nguồn thông tin",
      "Tài nguyên thương hiệu",
      "Khác",
    ]);
  });

  it("posts what was filled in", async () => {
    const stub = await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], {
      tab: "Duyệt",
      extra: [
        {
          match: `/api/pr/contents/${CONTENT.id}/resources`,
          method: "POST",
          status: 201,
          body: RESOURCE,
        },
      ],
    });
    await userEvent.click(await screen.findByRole("button", { name: /Thêm tài nguyên/ }));

    await userEvent.type(screen.getByRole("textbox", { name: /Tên \/ nhãn/ }), "Brief khách hàng");
    await userEvent.type(
      screen.getByRole("textbox", { name: /Đường dẫn \/ liên kết/ }),
      "https://example.com/brief",
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Bắt buộc xem khi duyệt" }));
    await userEvent.click(screen.getByRole("button", { name: "Thêm tài nguyên" }));

    const calls = (stub as unknown as { calls: Array<{ method: string; body: unknown }> }).calls;
    const posted = calls.find((call) => call.method === "POST");
    expect(posted?.body).toEqual({
      resource_type: "REFERENCE",
      label: "Brief khách hàng",
      location: "https://example.com/brief",
      note: null,
      required_for_review: true,
    });
  });

  it("will not submit without a label and a location", async () => {
    await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], { tab: "Duyệt" });
    await userEvent.click(await screen.findByRole("button", { name: /Thêm tài nguyên/ }));

    const submit = screen.getByRole("button", { name: "Thêm tài nguyên" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: /Tên \/ nhãn/ }), "Brief");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: /Đường dẫn \/ liên kết/ }),
      "https://example.com/x",
    );
    expect(submit).toBeEnabled();
  });

  it("confirms before deleting, and does not use a browser dialog", async () => {
    const stub = await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], {
      tab: "Duyệt",
      resources: [RESOURCE],
      extra: [
        {
          match: `/api/pr/contents/${CONTENT.id}/resources/${RESOURCE.id}`,
          method: "DELETE",
          status: 204,
        },
      ],
    });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    await userEvent.click(within(section).getByRole("button", { name: "Xóa" }));
    // Step 1F.2.8: the shared dialog, and still no `window.confirm` anywhere -
    // `tests/confirmation.test.tsx` asserts that across the whole panel.
    expect(dialog().getByText("Xóa tài nguyên này?")).toBeInTheDocument();
    const calls = (stub as unknown as { calls: Array<{ method: string }> }).calls;
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    await confirm();
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  it("edits in place", async () => {
    const stub = await openDetail([{ action: "MANAGE_CONTENT_RESOURCES" }], {
      tab: "Duyệt",
      resources: [RESOURCE],
      extra: [
        {
          match: `/api/pr/contents/${CONTENT.id}/resources/${RESOURCE.id}`,
          method: "PATCH",
          body: { ...RESOURCE, label: "Brief mới" },
        },
      ],
    });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    await userEvent.click(within(section).getByRole("button", { name: "Sửa" }));
    const label = screen.getByRole("textbox", { name: /Tên \/ nhãn/ });
    expect(label).toHaveValue("Brief khách hàng");
    await userEvent.clear(label);
    await userEvent.type(label, "Brief mới");
    await userEvent.click(screen.getByRole("button", { name: "Lưu" }));

    const calls = (stub as unknown as { calls: Array<{ method: string; body: unknown }> }).calls;
    const patched = calls.find((call) => call.method === "PATCH");
    expect((patched?.body as { label: string }).label).toBe("Brief mới");
  });
});

// =============================================================================
// 108. The review screen — the regression that matters
// =============================================================================

describe("108. a reviewer sees the material without leaving the page", () => {
  it("puts the resources on the Duyệt tab, before the approval section", async () => {
    // A Team Lead opens the piece to approve it. The brief has to be here, not
    // on another screen - and it has to come before the place the decision is
    // described, because it is what the decision is made from.
    await openDetail([{ action: "APPROVAL", decision: "APPROVED" }], {
      tab: "Duyệt",
      resources: [RESOURCE],
    });

    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    expect(within(section).getByText("Brief khách hàng")).toBeInTheDocument();

    const page = section.closest("div");
    const text = page?.textContent ?? "";
    expect(text.indexOf("Tài nguyên & tham khảo")).toBeLessThan(text.indexOf("Duyệt của người"));
  });

  it("keeps review material and production submissions apart", () => {
    // The distinction the feature is built on: a moodboard is what a cut is
    // judged against, and the cut is what is judged. They are different
    // sections with different headings, and this guards the wording that keeps
    // them legible as different things.
    const source = readFileSync(
      path.resolve(__dirname, "../src/app/pr/content/[id]/page.tsx"),
      "utf8",
    );
    expect(source).toContain("Tài nguyên &amp; tham khảo");
    expect(source).toContain("File sản xuất đã gửi");
    // The resources section says what it is not, in the panel itself.
    expect(source).toContain("Không phải file sản xuất đã gửi");
    // And it reads its own endpoint, never the submissions one.
    expect(source).toContain("api.contentResources");
  });

  it("shows a reviewer the material even with no edit rights", async () => {
    await openDetail([{ action: "APPROVAL", decision: "APPROVED" }], {
      tab: "Duyệt",
      resources: [RESOURCE, OPTIONAL_RESOURCE],
    });
    const section = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });

    expect(within(section).getByText("Brief khách hàng")).toBeInTheDocument();
    expect(within(section).getByText("Ảnh packshot sản phẩm")).toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: /Thêm tài nguyên/ }),
    ).not.toBeInTheDocument();
  });

  it("does not decide for itself who may manage resources", () => {
    const source = readFileSync(
      path.resolve(__dirname, "../src/app/pr/content/[id]/page.tsx"),
      "utf8",
    );
    expect(source).toContain('has(actions, "MANAGE_CONTENT_RESOURCES")');
    expect(source).not.toMatch(/role\s*===\s*"(ADMIN|OWNER|TEAM_LEAD)"/);
  });
});
