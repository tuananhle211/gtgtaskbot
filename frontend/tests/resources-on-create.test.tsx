/**
 * Step 1F.2.3e.1, browser half: references prepared while creating the content.
 *
 * Section 109 is the section itself - it has to start empty, grow only when
 * asked, and speak the same Vietnamese the detail page does. Section 110 is the
 * request, and it is the one that matters: everything the person typed leaves in
 * **one** POST, because a create followed by three resource posts is how a piece
 * of content ends up existing with two of its three references.
 *
 * Section 111 is failure, which is where a form like this is actually judged.
 * Somebody who has pasted four links and got one wrong must get the sentence
 * under the link that is wrong, and must still have the other three when they
 * come back to fix it.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CONTENT, SESSION, VERSION, renderWithQuery, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
const pushed: string[] = [];

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => pushed.push(url),
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
  policy_grounded_platform: true,
};
const DRIVE = "https://drive.google.com/file/d/1AbCdEf/view";

const board = {
  items: [],
  total: 0,
  scope: "ALL",
  stage_counts: [],
  production_state_counts: [],
  limit: 60,
  offset: 0,
};

type Route = { match: string; status?: number; body?: unknown; method?: string };

const boardRoutes = (extra: Route[] = []): Route[] => [
  ...extra,
  { match: "/api/pr/brands", body: [BRAND] },
  {
    match: "/api/pr/platforms",
    body: [{ id: CHANNEL.platform_id, code: "TIKTOK", name: "TikTok" }],
  },
  { match: "/api/pr/channels", body: [CHANNEL] },
  { match: "/api/pr/people", body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }] },
  { match: "/api/pr/contents/board", body: board },
];

const CREATED: Route = {
  match: "/api/pr/contents",
  method: "POST",
  status: 201,
  body: { content: CONTENT, current_version: VERSION, targets: [], brand: BRAND },
};

/** A refusal shaped exactly as the API sends one about draft `index`. */
const refusal = (index: number, reason = "unsafe_scheme"): Route => ({
  match: "/api/pr/contents",
  method: "POST",
  status: 422,
  body: {
    error: {
      code: "pr_validation_error",
      message: "Content resource location is not acceptable",
      details: {
        field: "location",
        reason,
        resource_type: "REFERENCE",
        initial_resource_index: index,
      },
    },
  },
});

type Stub = ReturnType<typeof stubFetch> & {
  calls: Array<{ url: string; method: string; body: unknown }>;
};

const calls = (stub: ReturnType<typeof stubFetch>) => (stub as unknown as Stub).calls;

async function openCreateForm(extra: Route[] = []) {
  const stub = stubFetch(boardRoutes(extra));
  renderWithQuery(<ContentBoardPage />);
  await userEvent.click(await screen.findByRole("button", { name: /Tạo nội dung/ }));
  await screen.findByRole("combobox", { name: /Loại nội dung/ });
  return stub;
}

/** The resource section of the create form. */
const section = () => screen.getByRole("region", { name: "Tài nguyên & tham khảo" });

const drafts = () => within(section()).queryAllByRole("listitem");

const addDraft = async () =>
  userEvent.click(within(section()).getByRole("button", { name: "+ Thêm tài nguyên" }));

/** Fill one draft's three required boxes. */
async function fillDraft(
  position: number,
  { label, location, note }: { label: string; location: string; note?: string },
) {
  await userEvent.type(
    screen.getByRole("textbox", { name: `Tài nguyên ${position} — Tên / nhãn` }),
    label,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: `Tài nguyên ${position} — Đường dẫn / liên kết` }),
    location,
  );
  if (note !== undefined) {
    await userEvent.type(
      screen.getByRole("textbox", { name: `Tài nguyên ${position} — Ghi chú` }),
      note,
    );
  }
}

/** Everything the content half of the form insists on. */
async function fillContent({ priority }: { priority?: string } = {}) {
  await userEvent.type(screen.getByRole("textbox", { name: /Tiêu đề/ }), "Bài mới");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: /Thương hiệu/ }), BRAND.id);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: /Người phụ trách/ }),
    SESSION.user_id,
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: /Loại nội dung/ }),
    "SHORT_VIDEO_SCRIPT",
  );
  if (priority) {
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Mức độ ưu tiên/ }), priority);
  }
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Chọn kênh" }), CHANNEL.id);
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: /Hình thức đăng cho/ }),
    "ORGANIC",
  );
}

const submit = async () =>
  userEvent.click(screen.getByRole("button", { name: "Tạo nội dung" }));

beforeEach(() => {
  SEARCH.value = new URLSearchParams({ scope: "ALL" });
  pushed.length = 0;
});

// =============================================================================
// 109. The section on the create form
// =============================================================================

describe("109. Tài nguyên & tham khảo while creating", () => {
  it("is on the form, under its own heading", async () => {
    await openCreateForm();
    expect(section()).toBeInTheDocument();
    expect(within(section()).getByText("Tài nguyên & tham khảo")).toBeInTheDocument();
  });

  it("starts with no draft at all", async () => {
    // The create form is already dense. Seven resource forms nobody asked for
    // would be seven things to scroll past for the majority of items that need
    // none - so the initial state is the empty state.
    await openCreateForm();
    expect(within(section()).getByText("Chưa có tài nguyên tham khảo.")).toBeInTheDocument();
    expect(drafts()).toHaveLength(0);
  });

  it("adds a draft when asked", async () => {
    await openCreateForm();
    await addDraft();

    expect(drafts()).toHaveLength(1);
    expect(within(section()).queryByText("Chưa có tài nguyên tham khảo.")).not.toBeInTheDocument();
  });

  it("adds as many as somebody has material for", async () => {
    await openCreateForm();
    await addDraft();
    await addDraft();
    await addDraft();
    expect(drafts()).toHaveLength(3);
  });

  it("offers the seven types in Vietnamese, and never a raw code", async () => {
    await openCreateForm();
    await addDraft();

    const select = screen.getByRole("combobox", { name: "Tài nguyên 1 — Loại tài nguyên" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Tài liệu tham khảo",
      "Hình ảnh",
      "Video tham khảo",
      "File / Google Drive",
      "Nguồn thông tin",
      "Tài nguyên thương hiệu",
      "Khác",
    ]);
    // The vocabulary is the same one the detail page renders, and the codes stay
    // on the wire where they belong.
    expect(section().textContent).not.toContain("BRAND_ASSET");
    expect(section().textContent).not.toContain("DRIVE_FILE");
  });

  it("asks for a label, a location, a note and the review flag", async () => {
    await openCreateForm();
    await addDraft();

    expect(
      screen.getByRole("textbox", { name: "Tài nguyên 1 — Tên / nhãn" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Tài nguyên 1 — Đường dẫn / liên kết" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Tài nguyên 1 — Ghi chú" })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Tài nguyên 1 — Bắt buộc xem khi duyệt" }),
    ).toBeInTheDocument();
    // And the words are on screen, not only in the accessible name.
    expect(within(section()).getByText("Bắt buộc xem khi duyệt")).toBeInTheDocument();
  });

  it("names every control by which resource it belongs to", async () => {
    // Three drafts means three boxes labelled "Tên / nhãn". Without the number
    // in the accessible name, a screen reader moving between them has no way to
    // say which resource it has reached.
    await openCreateForm();
    await addDraft();
    await addDraft();

    expect(
      screen.getByRole("textbox", { name: "Tài nguyên 2 — Tên / nhãn" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xóa tài nguyên 2" })).toBeInTheDocument();
  });

  it("removes a draft, and tells no server about it", async () => {
    const stub = await openCreateForm();
    await addDraft();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });

    await userEvent.click(screen.getByRole("button", { name: "Xóa tài nguyên 1" }));

    expect(drafts()).toHaveLength(1);
    // An unsaved draft is form state and nothing else: no row was ever created,
    // so there is nothing to delete, nothing to audit and nothing to undo.
    expect(
      calls(stub).filter(
        (call) => call.method === "DELETE" || call.url.includes("/resources"),
      ),
    ).toEqual([]);
  });
});

// =============================================================================
// 110. What the create request carries
// =============================================================================

describe("110. one request, everything in it", () => {
  it("sends the drafts as initial_resources", async () => {
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await addDraft();
    await fillDraft(1, {
      label: "Brief khách hàng",
      location: DRIVE,
      note: "Xem mục 3",
    });
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Tài nguyên 1 — Bắt buộc xem khi duyệt" }),
    );
    await addDraft();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Tài nguyên 2 — Loại tài nguyên" }),
      "IMAGE",
    );
    await fillDraft(2, { label: "Ảnh packshot", location: "/volume1/PR/packshot.jpg" });

    await submit();

    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    const created = calls(stub).find((call) => call.method === "POST")!.body as {
      initial_resources: unknown;
    };
    expect(created.initial_resources).toEqual([
      {
        resource_type: "REFERENCE",
        label: "Brief khách hàng",
        location: DRIVE,
        note: "Xem mục 3",
        required_for_review: true,
      },
      {
        resource_type: "IMAGE",
        label: "Ảnh packshot",
        location: "/volume1/PR/packshot.jpg",
        note: null,
        required_for_review: false,
      },
    ]);
  });

  it("still sends the format and the priority beside them", async () => {
    const stub = await openCreateForm([CREATED]);
    await fillContent({ priority: "URGENT" });
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });

    await submit();

    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    const created = calls(stub).find((call) => call.method === "POST")!.body as Record<
      string,
      unknown
    >;
    expect(created.content_type).toBe("SHORT_VIDEO_SCRIPT");
    expect(created.priority).toBe("URGENT");
    expect(created.targets).toEqual([
      { channel_id: CHANNEL.id, distribution_mode: "ORGANIC" },
    ]);
  });

  it("sends an empty list when nobody added anything", async () => {
    // Resources are optional, and this is the create most items get.
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await submit();

    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    const created = calls(stub).find((call) => call.method === "POST")!.body as {
      initial_resources: unknown;
    };
    expect(created.initial_resources).toEqual([]);
  });

  it("never posts a resource to its own endpoint", async () => {
    // The whole point: content and references are one transaction. A second
    // request here would be the shape that leaves half-referenced content behind.
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });
    await submit();

    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).filter((call) => call.url.includes("/resources"))).toEqual([]);
  });
});

// =============================================================================
// 111. When it is refused
// =============================================================================

describe("111. a refusal lands on the draft it is about", () => {
  it("keeps every draft when the server refuses", async () => {
    const stub = await openCreateForm([refusal(1)]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE, note: "Xem mục 3" });
    await addDraft();
    await fillDraft(2, { label: "Video tham khảo", location: "https://example.com/x" });

    await submit();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));

    // Nothing is wiped. Somebody who prepared two references still has both,
    // their notes and their flags, and can fix the one that was wrong.
    expect(drafts()).toHaveLength(2);
    expect(screen.getByRole("textbox", { name: "Tài nguyên 1 — Tên / nhãn" })).toHaveValue(
      "Brief khách hàng",
    );
    expect(screen.getByRole("textbox", { name: "Tài nguyên 1 — Ghi chú" })).toHaveValue(
      "Xem mục 3",
    );
    expect(
      screen.getByRole("textbox", { name: "Tài nguyên 2 — Đường dẫn / liên kết" }),
    ).toHaveValue("https://example.com/x");
    // And the content half of the form is untouched too.
    expect(screen.getByRole("textbox", { name: /Tiêu đề/ })).toHaveValue("Bài mới");
  });

  it("puts the server's sentence under the draft the server named", async () => {
    await openCreateForm([refusal(1)]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });
    await addDraft();
    await fillDraft(2, { label: "Video", location: DRIVE });

    await submit();

    const second = await waitFor(() => within(drafts()[1]).getByRole("alert"));
    expect(second).toHaveTextContent(/không an toàn/);
    // Not on the first, and not as a form-wide banner that would make somebody
    // re-read both.
    expect(within(drafts()[0]).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("catches an obviously incomplete draft before sending anything", async () => {
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });
    await addDraft();
    // A location and no label. The server would refuse this too - the client
    // check only saves the round trip.
    await userEvent.type(
      screen.getByRole("textbox", { name: "Tài nguyên 2 — Đường dẫn / liên kết" }),
      DRIVE,
    );

    await submit();

    expect(within(drafts()[1]).getByRole("alert")).toHaveTextContent("Tên / nhãn là bắt buộc.");
    expect(calls(stub).some((call) => call.method === "POST")).toBe(false);
  });

  it("refuses a javascript: link without asking the server", async () => {
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief", location: "javascript:alert(1)" });

    await submit();

    expect(within(drafts()[0]).getByRole("alert")).toHaveTextContent(
      "Đường dẫn / liên kết không hợp lệ.",
    );
    expect(calls(stub).some((call) => call.method === "POST")).toBe(false);
  });
});

// =============================================================================
// 112. After it works
// =============================================================================

describe("112. a successful create", () => {
  it("closes the form and refreshes the board, as it always did", async () => {
    const stub = await openCreateForm([CREATED]);
    await fillContent();
    await addDraft();
    await fillDraft(1, { label: "Brief khách hàng", location: DRIVE });

    await submit();

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Tài nguyên & tham khảo" })).not.toBeInTheDocument(),
    );
    // Step 1F.2.3e.1 changes what a create carries, not where it goes.
    expect(pushed).toEqual([]);
    expect(calls(stub).filter((call) => call.url.includes("/contents/board")).length).toBeGreaterThan(
      1,
    );
  });

  it("shows the material on the detail page with no second save", async () => {
    const resource = {
      id: "99999999-9999-9999-9999-999999999999",
      content_id: CONTENT.id,
      resource_type: "REFERENCE",
      label: "Brief khách hàng",
      location: DRIVE,
      note: "Xem mục 3",
      required_for_review: true,
      is_link: true,
      added_by_user_id: SESSION.user_id,
      created_at: "2026-08-01T03:00:00+00:00",
      updated_at: "2026-08-01T03:00:00+00:00",
    };
    stubFetch([
      {
        match: `/api/pr/contents/${CONTENT.id}/available-actions`,
        body: {
          content_id: CONTENT.id,
          workflow_stage: CONTENT.workflow_stage,
          available_actions: [],
        },
      },
      { match: `/api/pr/contents/${CONTENT.id}/resources`, body: [resource] },
      {
        match: `/api/pr/contents/${CONTENT.id}/review-context`,
        body: { content: CONTENT, current_version: VERSION, approvals: [], tasks: [] },
      },
      { match: `/api/pr/contents/${CONTENT.id}/versions`, body: [VERSION] },
      { match: `/api/pr/contents/${CONTENT.id}/history`, body: { transitions: [] } },
      { match: `/api/pr/contents/${CONTENT.id}/production`, body: { production_state: null } },
      { match: `/api/pr/contents/${CONTENT.id}/ai-review`, body: { active: false, runs: [] } },
      {
        match: `/api/pr/contents/${CONTENT.id}`,
        body: { content: CONTENT, current_version: VERSION, targets: [], brand: BRAND },
      },
      {
        match: "/api/pr/people",
        body: [{ user_id: SESSION.user_id, full_name: SESSION.full_name }],
      },
    ]);
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));

    const panel = await screen.findByRole("region", { name: "Tài nguyên & tham khảo" });
    expect(within(panel).getByText("Brief khách hàng")).toBeInTheDocument();
    expect(within(panel).getByText("Tài liệu tham khảo")).toBeInTheDocument();
    expect(within(panel).getByText("Bắt buộc xem khi duyệt")).toBeInTheDocument();
  });
});
