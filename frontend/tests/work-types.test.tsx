/**
 * M2.5 - Loại công việc, the screen that gives the department its taxonomy.
 *
 * The bug this milestone was opened for is a frontend symptom with a backend
 * cause: `pr_work_types` shipped empty, so "Giao công việc" rendered a picker
 * with nothing in it. Tests 10-12 are the ones that close it - the guidance
 * that replaces the empty selector, and the two pickers that read the list.
 *
 * The rest is the ownership rule. **Test 2** is the one that carries it: a
 * Trưởng nhóm holding `PR_WORK_MANAGE` picks from the list and does not see the
 * tab that edits it. **Test 6** is the other half - a used type draws its
 * structural fields disabled, and the reason is on the screen rather than
 * discovered when the save fails.
 *
 * As everywhere else in this suite, the browser decides nothing: which words a
 * row shows are `*_label` fields from the server, and whether the structural
 * fields are locked is `structure_locked`, which the server computed. A test
 * that passed because the component recomputed the lock in TypeScript would be
 * testing a second implementation of the rule.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, dialog, renderWithQuery, stubFetch } from "./helpers";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const OWNER = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE", "PR_WORK_CONFIGURE"];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const EMPLOYEE = ["PR_WORK_EXECUTE"];

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const SCRIPT = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "SHORT_VIDEO_SCRIPT",
  name: "Kịch bản video ngắn",
  category: "CONTENT",
  category_label: "Nội dung",
  description: null,
  default_unit: "ITEM",
  default_unit_label: "sản phẩm",
  default_quota_basis: "ITEM_COUNT",
  default_quota_basis_label: "Theo số đầu việc",
  requires_evidence: false,
  is_active: true,
  display_order: 10,
};

const SEEDING = {
  ...SCRIPT,
  id: "22222222-2222-2222-2222-222222222222",
  code: "SEEDING_COMMENT",
  name: "Comment seeding",
  category: "COMMUNITY",
  category_label: "Cộng đồng",
  default_unit: "COMMENT",
  default_unit_label: "bình luận",
  default_quota_basis: "QUANTITY",
  default_quota_basis_label: "Theo số lượng",
  display_order: 10,
};

const RETIRED = {
  ...SCRIPT,
  id: "33333333-3333-3333-3333-333333333333",
  code: "OLD_FORMAT",
  name: "Định dạng cũ",
  is_active: false,
};

const summary = {
  period_from: "2026-09-01T00:00:00Z",
  period_to: "2026-09-16T00:00:00Z",
  created: 0,
  accepted: 0,
  completed: 0,
  approved: 0,
  counted_work_items: 0,
  counted_contributions: 0,
  open: 0,
  in_progress: 0,
  awaiting_validation: 0,
  overdue: 0,
};

/**
 * The stub table.
 *
 * Order matters and is not cosmetic: `stubFetch` takes the **first** substring
 * hit, and `/api/pr/work` is a prefix of every route below it. The lifecycle
 * routes come before the detail route for the same reason - a POST to
 * `…/types/<id>/activate` contains `…/types/` too.
 */
const routes = (
  types: Array<Record<string, unknown>>,
  {
    capabilities = OWNER,
    detail,
    extra = [],
  }: {
    capabilities?: string[];
    detail?: Record<string, unknown>;
    extra?: Array<{ match: string; method?: string; status?: number; body?: unknown }>;
  } = {},
) => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [] },
  { match: "/activate", method: "POST", body: { ...types[0], is_active: true } },
  { match: "/deactivate", method: "POST", body: { ...types[0], is_active: false } },
  { match: "/api/pr/work/types/bootstrap", method: "POST", body: { created: [], work_types: [] } },
  {
    match: "/api/pr/work/types/",
    method: "GET",
    body: detail ?? { ...types[0], is_in_use: false, structure_locked: false },
  },
  { match: "/api/pr/work/types", method: "PATCH", body: types[0] },
  { match: "/api/pr/work/types", method: "POST", body: types[0] },
  { match: "/api/pr/work/types", method: "GET", body: types },
  { match: "/api/pr/work/summary", body: summary },
  { match: "/history", body: [] },
  // Post-M4. The Work screen's outer scope is a reporting month, so it asks for
  // the period list. Stubbed **before** the generic `/api/pr/work` entry, which
  // would otherwise swallow it - `stubFetch` takes the first substring hit.
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
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

const openConfig = async () => {
  await userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));
};

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-2: WHO OWNS THE TAXONOMY -------------------------------------------

describe("1. an owner reaches the work-type configuration", () => {
  it("shows the tab, and the list with its columns", async () => {
    stubFetch(routes([SCRIPT, SEEDING]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(await screen.findByRole("heading", { name: "Loại công việc" })).toBeInTheDocument();
    for (const column of ["Tên", "Nhóm", "Cách tính", "Đơn vị", "Trạng thái"]) {
      expect(screen.getByRole("columnheader", { name: column })).toBeInTheDocument();
    }
    // Scoped to the table: the mapping panel below it now names *content* types
    // in Vietnamese too, and a work type may legitimately share a content
    // type's words - "Kịch bản video ngắn" is a plausible name for both. The
    // collision is real rather than accidental, so the query says which one it
    // means instead of relying on there being only one.
    const table = within(screen.getByRole("table"));
    expect(table.getByText("Kịch bản video ngắn")).toBeInTheDocument();
    // The words are the server's labels, not a table in the browser.
    expect(table.getByText("Theo số lượng")).toBeInTheDocument();
    expect(table.getByText("bình luận")).toBeInTheDocument();
  });
});

describe("2. managing work does not confer configuring it", () => {
  it("hides the tab from a Trưởng nhóm and from an employee", async () => {
    stubFetch(routes([SCRIPT], { capabilities: LEAD }));
    const view = renderWithQuery(<WorkPage />);
    expect(await screen.findByRole("button", { name: "Công việc" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
    view.unmount();

    vi.unstubAllGlobals();
    stubFetch(routes([SCRIPT], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    expect(await screen.findByRole("button", { name: "Công việc" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
  });
});

// --- 3-4: CREATING ---------------------------------------------------------

describe("3. the create form asks for the fields the taxonomy needs", () => {
  it("sends the code, name, group and measurement", async () => {
    const fetchMock = stubFetch(routes([SCRIPT]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: /Thêm loại công việc/ }));
    await userEvent.type(screen.getByLabelText("Mã"), "video_edit");
    await userEvent.type(screen.getByLabelText("Tên"), "Dựng video");
    await userEvent.selectOptions(screen.getByLabelText("Nhóm"), "PRODUCTION");
    await userEvent.click(screen.getByRole("button", { name: "Tạo loại công việc" }));

    await waitFor(() => {
      const sent = (fetchMock as unknown as { calls: Array<{ method: string; body: any }> }).calls
        .filter((call) => call.method === "POST")
        .at(-1);
      expect(sent?.body).toMatchObject({
        code: "video_edit",
        name: "Dựng video",
        category: "PRODUCTION",
        default_quota_basis: "ITEM_COUNT",
      });
    });
  });
});

describe("4. the form will not submit without the fields that identify a type", () => {
  it("keeps the create button disabled until a code and a name are typed", async () => {
    stubFetch(routes([SCRIPT]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: /Thêm loại công việc/ }));
    const submit = screen.getByRole("button", { name: "Tạo loại công việc" });
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Mã"), "VIDEO_EDIT");
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Tên"), "Dựng video");
    expect(submit).toBeEnabled();
  });
});

// --- 5-6: THE STRUCTURAL LOCK ---------------------------------------------

describe("5. an unused type is fully editable", () => {
  it("leaves the code and the measurement enabled", async () => {
    stubFetch(
      routes([SCRIPT], { detail: { ...SCRIPT, is_in_use: false, structure_locked: false } }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(
      await screen.findByRole("button", { name: "Chỉnh sửa Kịch bản video ngắn" }),
    );
    await waitFor(() => expect(screen.getByLabelText("Mã")).toBeEnabled());
    expect(screen.getByLabelText("Cách tính")).toBeEnabled();
    expect(screen.queryByText(/đã phát sinh dữ liệu/)).not.toBeInTheDocument();
  });
});

describe("6. a used type locks its structural fields and says why", () => {
  it("disables code and basis, and keeps the name and the unit editable", async () => {
    stubFetch(
      routes([SEEDING], {
        detail: { ...SEEDING, is_in_use: true, structure_locked: true },
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: "Chỉnh sửa Comment seeding" }));
    await waitFor(() => expect(screen.getByLabelText("Mã")).toBeDisabled());
    expect(screen.getByLabelText("Cách tính")).toBeDisabled();
    // Period-container patch: the unit is what a result is *called*, every
    // stored amount keeps its own copy, and "sản phẩm" to "khách hàng" on a
    // type in use is exactly the correction the screen has to allow.
    expect(screen.getByLabelText("Đơn vị")).toBeEnabled();
    // The label half stays editable - a taxonomy nobody may retitle is one
    // nobody keeps tidy.
    expect(screen.getByLabelText("Tên")).toBeEnabled();
    expect(screen.getByText(/đã phát sinh dữ liệu nên không đổi được mã/)).toBeInTheDocument();
  });

  it("does not send the locked fields back", async () => {
    const fetchMock = stubFetch(
      routes([SEEDING], { detail: { ...SEEDING, is_in_use: true, structure_locked: true } }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: "Chỉnh sửa Comment seeding" }));
    await waitFor(() => expect(screen.getByLabelText("Mã")).toBeDisabled());
    await userEvent.clear(screen.getByLabelText("Tên"));
    await userEvent.type(screen.getByLabelText("Tên"), "Seeding bình luận");
    await userEvent.click(screen.getByRole("button", { name: "Lưu" }));

    await waitFor(() => {
      const sent = (fetchMock as unknown as { calls: Array<{ method: string; body: any }> }).calls
        .filter((call) => call.method === "PATCH")
        .at(-1);
      expect(sent?.body).toMatchObject({ name: "Seeding bình luận" });
      expect(sent?.body).not.toHaveProperty("code");
      expect(sent?.body).not.toHaveProperty("default_quota_basis");
    });
  });
});

// --- 7-9: RETIRING AND REVIVING -------------------------------------------

describe("7. deactivating asks first, and says the history is kept", () => {
  it("shows the dialog and only calls the route on confirm", async () => {
    const fetchMock = stubFetch(routes([SCRIPT]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: "Tắt Kịch bản video ngắn" }));
    expect(dialog().getByText("Tắt loại công việc này?")).toBeInTheDocument();
    // The sentence people actually worry about.
    expect(dialog().getByText(/Dữ liệu lịch sử vẫn được giữ nguyên/)).toBeInTheDocument();

    const before = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.length;
    await confirm();
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
      expect(calls.length).toBeGreaterThan(before);
      expect(calls.some((call) => call.url.includes("/deactivate"))).toBe(true);
    });
  });
});

describe("8. a retired type is badged rather than hidden from configuration", () => {
  it("shows it as tắt, and offers to bring it back", async () => {
    stubFetch(routes([RETIRED]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    const row = (await screen.findByText("Định dạng cũ")).closest("tr")!;
    expect(within(row).getByText("Đã tắt")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Bật lại Định dạng cũ" })).toBeInTheDocument();
  });
});

describe("8b. a type the content projector provisioned says so, and says it is unpriced", () => {
  const AUTO = {
    ...SCRIPT,
    id: "44444444-4444-4444-4444-444444444444",
    code: "CONTENT_AUTO_CONTENT_CREATION_LONG_YOUTUBE_SCRIPT",
    name: "Kịch bản YouTube dài",
    auto_provisioned: true,
  };
  const RULE = {
    id: "55555555-5555-5555-5555-555555555555",
    work_type_id: SCRIPT.id,
    work_type_code: SCRIPT.code,
    work_type_name: SCRIPT.name,
    version_no: 1,
    mode: "STANDARD_MINUTES",
    standard_minutes_per_unit: "30",
    measurement_mode: "ITEM_COUNT",
    measurement_mode_label: "Theo số đầu việc",
    unit_label: "đầu việc",
    rule_label: "30 phút / đầu việc",
    effective_from: "2026-09-01",
    effective_to: null,
    status: "APPROVED",
    note: null,
    approved_at: "2026-09-01T02:00:00Z",
    created_at: "2026-09-01T02:00:00Z",
  };

  it("badges the provenance and the missing workload rule, on that row only", async () => {
    stubFetch(
      routes([SCRIPT, AUTO], {
        extra: [{ match: "/api/pr/performance/scoring-rules", method: "GET", body: [RULE] }],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();

    // The name also sits in the pickers further down the page; the row is
    // the one inside the table.
    const auto = (await screen.findAllByText("Kịch bản YouTube dài"))
      .map((node) => node.closest("tr"))
      .find((node) => node !== null)!;
    expect(within(auto).getByText("Hệ thống tự tạo từ nội dung")).toBeInTheDocument();
    expect(await within(auto).findByText("Chưa có quy tắc workload")).toBeInTheDocument();
    // The priced, hand-made type carries neither.
    const manual = screen
      .getAllByText("Kịch bản video ngắn")
      .map((node) => node.closest("tr"))
      .find((node) => node !== null)!;
    expect(within(manual).queryByText("Hệ thống tự tạo từ nội dung")).not.toBeInTheDocument();
    expect(within(manual).queryByText("Chưa có quy tắc workload")).not.toBeInTheDocument();
  });
});

describe("9. reactivating asks too", () => {
  it("calls activate after the dialog", async () => {
    const fetchMock = stubFetch(routes([RETIRED]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: "Bật lại Định dạng cũ" }));
    expect(dialog().getByText("Bật lại loại công việc này?")).toBeInTheDocument();
    await confirm();
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
      expect(calls.some((call) => call.url.includes("/activate"))).toBe(true);
    });
  });
});

// --- 10-12: THE EMPTY DEPARTMENT, WHICH IS THE BUG -------------------------

describe("10. an empty taxonomy is explained, not left as a blank selector", () => {
  it("offers the owner the bootstrap, and tells them where to create types", async () => {
    stubFetch(routes([]));
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(await screen.findByText(/Chưa có loại công việc nào/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Khởi tạo bộ mặc định" }),
    ).toBeInTheDocument();
  });
});

describe("11. the work-creation picker explains an empty taxonomy", () => {
  it("tells an owner to configure it, and an employee that there is nothing yet", async () => {
    stubFetch(routes([]));
    const view = renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
    expect(
      await screen.findByText("Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình."),
    ).toBeInTheDocument();
    // And no mysterious empty selector beside it.
    expect(screen.queryByLabelText("Loại công việc")).not.toBeInTheDocument();
    view.unmount();

    vi.unstubAllGlobals();
    NAV.reset();
    stubFetch(routes([], { capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Đề xuất công việc/ }));
    expect(
      await screen.findByText("Hiện chưa có loại công việc khả dụng."),
    ).toBeInTheDocument();
  });
});

describe("12. a populated taxonomy reaches the work picker", () => {
  it("offers every active type, grouped by its server-rendered category label", async () => {
    stubFetch(routes([SCRIPT, SEEDING]));
    renderWithQuery(<WorkPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
    const picker = await screen.findByLabelText("Loại công việc");
    expect(within(picker).getByRole("option", { name: /Kịch bản video ngắn/ })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: /Comment seeding/ })).toBeInTheDocument();
  });

  it("asks the server for active types only", async () => {
    const fetchMock = stubFetch(routes([SCRIPT]));
    renderWithQuery(<WorkPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Giao công việc" }));
    await screen.findByLabelText("Loại công việc");

    const asked = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls.filter((call) =>
      call.url.includes("/api/pr/work/types"),
    );
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((call) => !call.url.includes("include_inactive=true"))).toBe(true);
  });
});
