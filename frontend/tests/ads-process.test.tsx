/**
 * The Ads "Quy trình" and "Loại video": the create form builds the process
 * from checkboxes and sends it with the chosen video kind; a unit admin
 * keeps the catalogue of kinds and their points on the unit admin page.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewOrderPage from "@/app/orders/new/page";
import OrdSettingsPage from "@/app/admin/settings/page";
import TasksPage from "@/app/tasks/page";
import { VideoKindsManager } from "@/components/video-kinds";
import { renderWithQuery, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/orders/new",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
});

const ADS_SETTINGS = {
  urgent_days: 7,
  media_nas_url: null,
  design_nas_url: null,
  btd_link_attacher: "DUNG",
  telegram_enabled: false,
  review_bien_tap: false,
  review_thiet_ke: false,
  review_dung: true,
  review_video_by_script_lead: false,
};

const ADS_ENTRY = {
  code: "ADS",
  label: "Phòng Ads",
  role: "ORDERER",
  role_label: "Marketing",
  is_lead: false,
  member_code: "TUAN",
  personal_nas_url: null,
  settings: ADS_SETTINGS,
};

const ORDERER = {
  units: [ADS_ENTRY],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
};

const ADMIN = { ...ORDERER, can_admin: ["ADS"] };

const MEMBERS = {
  unit: "ADS",
  unit_label: "Phòng Ads",
  members: [
    {
      user_id: "11111111-1111-1111-1111-111111111111",
      full_name: "Hiền Biên kịch",
      base_role: "EMPLOYEE",
      base_role_label: "Nhân viên",
      role: "BIEN_TAP",
      role_label: "Biên kịch",
      is_lead: false,
      member_code: null,
      personal_nas_url: null,
      joined_at: "2026-10-07T02:00:00Z",
      left_at: null,
      active: true,
    },
  ],
  assignable_roles: [],
};

const KIND_FULL = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const KIND_SHORT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const KIND_OLD = "cccccccc-cccc-cccc-cccc-cccccccccccc";

const KINDS = {
  kinds: [
    { id: KIND_FULL, name: "Video full diễn hoạt", points: "1.00", active: true, sort_order: 1 },
    { id: KIND_SHORT, name: "Short video", points: 0.5, active: true, sort_order: 2 },
  ],
};

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const N3 = "eeeeeeee-0000-4000-8000-000000000003";
const D4 = "eeeeeeee-0000-4000-8000-000000000004";
const FORMS = [
  { key: "NEW", label: "Làm mới", pct: 100, note: "Mặc định.", phases: ["BIEN_TAP", "THIET_KE", "DUNG"], active: true },
  { key: "AFTER_EFFECTS", label: "Video có After Effects", pct: 150, note: "Chỉ dùng cho D1-D7.", phases: ["DUNG"], active: true },
];
const TASK_CODE_ROUTES = [
  {
    match: "/api/units/ADS/task-codes?phase=BIEN_TAP",
    body: {
      codes: [{ id: N3, phase: "BIEN_TAP", code: "N3", name: "Kịch bản video đến 3 phút", title: "Kịch bản video đến 3 phút", points: 120, active: true, sort_order: 1 }],
      forms: FORMS,
    },
  },
  {
    match: "/api/units/ADS/task-codes?phase=DUNG",
    body: {
      codes: [{ id: D4, phase: "DUNG", code: "D4", name: "Video trên 3 đến 5 phút", title: "Video trên 3 đến 5 phút", points: 480, active: true, sort_order: 1 }],
      forms: FORMS,
    },
  },
  { match: "/api/units/ADS/task-codes", body: { codes: [], forms: FORMS } },
];

function stubCreateForm(kinds: unknown = KINDS) {
  return stubFetch([
    { match: "/api/units/me", body: ORDERER },
    { match: "/api/units/ADS/members", body: MEMBERS },
    { match: "/api/units/ADS/video-kinds", body: kinds },
    ...TASK_CODE_ROUTES,
    {
      match: "/api/orders",
      method: "POST",
      body: { order: { code: "TUAN-BD-261008-01" } },
    },
  ]);
}

const route = () => screen.getByTestId("process-route").textContent;
const codePreview = () => screen.getByTestId("order-code-preview").textContent;

describe("the order code", () => {
  it("is five characters the server picks", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await screen.findByRole("checkbox", { name: "Dựng" });
    expect(codePreview()).toContain("5 ký tự");
    expect(codePreview()).not.toContain("yymmdd");
  });
});
const submit = () => screen.getByRole("button", { name: "Gửi order" });

/** The "Deadline mong muốn" box (datetime-local: jsdom takes a change event). */
const desiredDeadline = () => screen.getByLabelText(/Deadline mong muốn/);
const FUTURE_DEADLINE = "2099-12-01T17:00";

/** The basics of a sendable order. Nothing is ticked when the form opens
 * (2026-10-24): with no step ticked yet, Dựng is ticked here (``tick: false``
 * leaves the process alone). */
async function fillBasics({ tick = true }: { tick?: boolean } = {}) {
  await userEvent.type(
    await screen.findByRole("textbox", { name: /Tên kịch bản/ }),
    "Video serum",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Nội dung order" }),
    "Key: da căng bóng",
  );
  fireEvent.change(desiredDeadline(), { target: { value: FUTURE_DEADLINE } });
  const boxes = ["Biên kịch", "Design", "Dựng"].map((name) =>
    screen.getByRole("checkbox", { name }),
  );
  if (tick && boxes.every((box) => !(box as HTMLInputElement).checked)) {
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
  }
}

const DESIGN_CODE = "dddddddd-dddd-dddd-dddd-dddddddddddd";

describe("a Design-only order", () => {
  it("asks for its Task công việc, no approver and a plain Nội dung", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/units/me",
        body: {
          ...ORDERER,
          units: [{ ...ADS_ENTRY, settings: { ...ADS_SETTINGS, script_ai_review: true } }],
        },
      },
      { match: "/api/units/ADS/members", body: MEMBERS },
      { match: "/api/units/ADS/video-kinds", body: KINDS },
      {
        match: "/api/units/ADS/task-codes",
        body: {
          codes: [
            {
              id: DESIGN_CODE,
              phase: "THIET_KE",
              code: "TKBTS",
              name: "SOCIAL - Banner",
              points: 90,
              active: true,
              sort_order: 1,
            },
          ],
          forms: FORMS,
        },
      },
      { match: "/api/orders", method: "POST", body: { order: { code: "TUAN-T-261008-01" } } },
    ]);
    renderWithQuery(<NewOrderPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Design" }));
    await fillBasics();
    // No kind / platform / duration, no script source, no AI check, no approver.
    expect(screen.queryByRole("combobox", { name: /Loại video/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI duyệt kịch bản" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Quay thực tế" })).not.toBeInTheDocument();
    expect(screen.getByText("4 · Nội dung")).toBeInTheDocument();
    expect(screen.getByTestId("design-only-route")).toHaveTextContent("Trưởng phòng Design");
    // The tasks are a suggestion now (0074): optional.
    expect(submit()).toBeEnabled();
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Mã task công việc Design" }),
      DESIGN_CODE,
    );
    expect(submit()).toBeEnabled();
    await userEvent.click(submit());
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Gửi order" }));
    await waitFor(() => {
      const sent = calls(fetchMock).find(
        (call) => call.method === "POST" && call.url.includes("/api/orders"),
      );
      expect(sent?.body).toMatchObject({
        process: ["THIET_KE"],
        task_codes: { THIET_KE: [{ code_id: DESIGN_CODE, quantity: 1 }] },
      });
      expect(sent?.body).not.toHaveProperty("video_kind_id");
      expect(sent?.body).not.toHaveProperty("script_source");
    });
  });
});

describe("the create form's Quy trình", () => {
  it("starts with only Order ticked, which cannot be unticked, and no error yet", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    const order = await screen.findByRole("checkbox", { name: /^Order/ });
    expect(order).toBeChecked();
    expect(order).toBeDisabled();
    await userEvent.click(order);
    expect(order).toBeChecked();
    expect(screen.getByText("(luôn có)")).toBeInTheDocument();
    // Nothing else ticked by default (2026-10-24) - and nothing red on opening.
    for (const name of ["Biên kịch", "Design", "Dựng"]) {
      expect(screen.getByRole("checkbox", { name })).not.toBeChecked();
      expect(screen.getByRole("checkbox", { name })).toHaveAttribute("aria-invalid", "false");
    }
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(route()).toBe("Order › Người order duyệt final");
  });

  it("changes the route as boxes are ticked", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Biên kịch" }));
    expect(route()).toBe("Order › Biên kịch › Người order duyệt final");

    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    expect(route()).toBe(
      "Order › Biên kịch › Design › Dựng › Người order duyệt final",
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    // The side panel names the orderer as the final reviewer.
    expect(screen.getByText("Bạn (người order) duyệt final")).toBeInTheDocument();
  });

  it("sends the process and the suggested Task công việc, never a video kind (0074)", async () => {
    const fetchMock = stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Biên kịch" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    await fillBasics();
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link thiết kế/ }),
      "https://drive.example.com/design",
    );
    // "Loại video" and "Thời lượng" left the form.
    expect(screen.queryByRole("combobox", { name: /Loại video/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /Thời lượng/ })).not.toBeInTheDocument();
    // One optional "Task công việc" per ticked step: nothing chosen may be sent.
    expect(submit()).toBeEnabled();
    const dung = await screen.findByRole("combobox", { name: "Mã task công việc Dựng" });
    await waitFor(() => expect(within(dung).getAllByRole("option")).toHaveLength(2));
    await userEvent.selectOptions(dung, D4);
    // A form of Dựng, with its rule under it, and the points.
    await userEvent.click(
      within(screen.getByRole("radiogroup", { name: "Hình thức · D4" })).getByRole("radio", {
        name: /After Effects/,
      }),
    );
    expect(screen.getByText("Chỉ dùng cho D1-D7.")).toBeInTheDocument();
    expect(screen.getAllByTestId("design-codes-total").at(-1)).toHaveTextContent("720 điểm");
    expect(screen.getByRole("combobox", { name: "Mã task công việc Biên kịch" })).toBeInTheDocument();
    await userEvent.click(submit());
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Gửi order" }));
    await waitFor(() => {
      const sent = calls(fetchMock).find(
        (call) => call.method === "POST" && call.url.includes("/api/orders"),
      );
      expect(sent?.body).toMatchObject({
        title: "Video serum",
        process: ["BIEN_TAP", "DUNG"],
        task_codes: { DUNG: [{ code_id: D4, quantity: 1, form: "AFTER_EFFECTS" }] },
        design_link: "https://drive.example.com/design",
        desired_deadline_at: new Date(FUTURE_DEADLINE).toISOString(),
      });
      expect(sent?.body).not.toHaveProperty("video_kind_id");
      expect(sent?.body).not.toHaveProperty("duration_id");
      expect(sent?.body).not.toHaveProperty("video_type");
      expect(sent?.body).not.toHaveProperty("preassigned");
    });
  });

  it("never requires the design link, Dựng without Design included", async () => {
    stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    // Dựng alone, no link: the order may still be sent.
    expect(screen.queryByText(/Bắt buộc khi có Dựng/)).not.toBeInTheDocument();
    expect(screen.getByText("Không bắt buộc")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Link thiết kế/ })).toHaveValue("");
    expect(submit()).toBeEnabled();
    // With Design, and with Biên kịch alone: the same.
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    expect(submit()).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Biên kịch" }));
    expect(screen.getByText("Không bắt buộc")).toBeInTheDocument();
    expect(submit()).toBeEnabled();
  });

  it("says in red that a step is missing only when Gửi order is pressed", async () => {
    const stub = stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await fillBasics({ tick: false });
    // Nothing ticked, nothing red yet; the button may be pressed.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(submit()).toBeEnabled();
    await userEvent.click(submit());
    // Pressed: no confirmation, nothing sent - the boxes and an alert say why.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Chọn ít nhất một công đoạn: Biên kịch, Design hoặc Dựng.",
    );
    expect(screen.getByRole("checkbox", { name: "Biên kịch" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByText(/Chưa gửi được: hãy tích ít nhất một công đoạn/)).toBeInTheDocument();
    expect(calls(stub).some((call) => call.method === "POST")).toBe(false);
    // Ticking one clears it, and the button asks before sending again.
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/Chưa gửi được/)).not.toBeInTheDocument();
    await userEvent.click(submit());
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  // "Người duyệt order" comes from GET /api/orders/approvers: who approves
  // this person's order by default, whom they may choose, and whether they
  // must (nobody of theirs may approve orders) or need nobody (they may).
  const APPROVERS = {
    own_gate: false,
    required: false,
    default: [{ user_id: "h-1", name: "Trưởng A" }],
    candidates: [
      { user_id: "h-1", name: "Trưởng A" },
      { user_id: "h-2", name: "Trưởng B" },
    ],
  };
  const approverRoutes = (approvers: unknown) => [
    { match: "/api/orders/approvers", body: approvers },
    { match: "/api/units/me", body: ORDERER },
    { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
    { match: "/api/orders", method: "POST", body: { order: { code: "TUAN-TD-261008-01" } } },
  ];
  const sendOrder = async () => {
    await userEvent.click(submit());
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Gửi order" }),
    );
  };
  const postedOrder = (stub: unknown) =>
    calls(stub).find((call) => call.method === "POST" && call.url.includes("/api/orders"));

  it("starts on the orderer's own Trưởng phòng and lets them pick another", async () => {
    // Every Trưởng phòng is listed; their own one is picked from the start.
    const first = stubFetch(approverRoutes(APPROVERS));
    const { unmount } = renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    const picker = await screen.findByLabelText(/Người duyệt order/);
    await waitFor(() => expect(picker).toHaveValue("h-1"));
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Trưởng A",
      "Trưởng B",
    ]);
    // Nothing is read from the stream roster any more.
    expect(calls(first).some((call) => call.url.includes("/api/units/ADS/members"))).toBe(false);
    expect(submit()).toBeEnabled();
    await sendOrder();
    await waitFor(() => expect(postedOrder(first)).toBeDefined());
    expect(postedOrder(first)?.body).toMatchObject({ approver_user_id: "h-1" });
    unmount();

    // Picked: that person is sent.
    const second = stubFetch(approverRoutes(APPROVERS));
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    await userEvent.selectOptions(await screen.findByLabelText(/Người duyệt order/), "h-2");
    await sendOrder();
    await waitFor(() =>
      expect(postedOrder(second)?.body).toMatchObject({ approver_user_id: "h-2" }),
    );
  });

  it("names every default approver in the Tự động option", async () => {
    stubFetch(
      approverRoutes({
        ...APPROVERS,
        default: [
          { user_id: "h-1", name: "Trưởng A" },
          { user_id: "h-2", name: "Trưởng B" },
        ],
      }),
    );
    renderWithQuery(<NewOrderPage />);
    const picker = await screen.findByLabelText(/Người duyệt order/);
    expect(within(picker).getAllByRole("option")[0]).toHaveTextContent(
      "Tự động: Trưởng A, Trưởng B",
    );
  });

  it("makes the orderer choose when nobody of theirs may approve orders", async () => {
    const stub = stubFetch(
      approverRoutes({ ...APPROVERS, required: true, default: [] }),
    );
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    const picker = await screen.findByLabelText(/Người duyệt order/);
    // No "Tự động": a placeholder, then the candidates.
    const options = within(picker).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Chọn người duyệt…",
      "Trưởng A",
      "Trưởng B",
    ]);
    expect(screen.queryByText(/Tự động/)).not.toBeInTheDocument();
    expect(
      screen.getByText("Trưởng phòng của bạn không có quyền duyệt order: chọn người duyệt."),
    ).toBeInTheDocument();
    // Everything else is filled in: only the approver holds the submit back.
    expect(submit()).toBeDisabled();
    await userEvent.selectOptions(picker, "h-1");
    expect(submit()).toBeEnabled();
    await sendOrder();
    await waitFor(() =>
      expect(postedOrder(stub)?.body).toMatchObject({ approver_user_id: "h-1" }),
    );
  });

  it("asks nobody who approves orders themselves for an approver", async () => {
    stubFetch(
      approverRoutes({ own_gate: true, required: false, default: [], candidates: [] }),
    );
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await waitFor(() =>
      expect(screen.getByText(/order của bạn không cần duyệt/)).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText(/Người duyệt order/)).not.toBeInTheDocument();
    expect(submit()).toBeEnabled();
  });

  it("asks a Trưởng phòng ADS for no approver while the route has not answered", async () => {
    stubFetch([
      {
        match: "/api/units/me",
        body: { ...ORDERER, units: [{ ...ADS_ENTRY, role: "HEAD", role_label: "Trưởng phòng ADS" }] },
      },
      { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
    ]);
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    expect(screen.queryByLabelText(/Người duyệt order/)).not.toBeInTheDocument();
    expect(screen.getByText(/order của bạn không cần duyệt/)).toBeInTheDocument();
  });

  it("shows what the AI found in the script when it refuses the order", async () => {
    stubFetch([
      { match: "/api/units/me", body: ORDERER },
      { match: "/api/units/ADS/members", body: MEMBERS },
      { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
      {
        match: "/api/orders",
        method: "POST",
        status: 422,
        body: {
          error: {
            code: "order_validation_error",
            message: "AI duyệt kịch bản: chưa đạt (2 lỗi). Sửa các chỗ được chỉ ra rồi gửi lại.",
            details: {
              reason: "script_review_failed",
              field: "order_content",
              violations: [
                {
                  rule: 2,
                  quote: "cam kết",
                  reason: "Khẳng định tuyệt đối.",
                  suggestion: "Bỏ từ này.",
                },
                { rule: 0, quote: "Harmonic", reason: "", suggestion: "" },
              ],
            },
          },
        },
      },
    ]);
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    // The unit's check is off here: no button, the order goes as it is (the
    // server still reads the script when it is sent).
    expect(screen.queryByRole("button", { name: "AI duyệt kịch bản" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("script-violations")).not.toBeInTheDocument();
    await userEvent.click(submit());
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Gửi order" }));
    const panel = await screen.findByTestId("script-violations");
    expect(panel).toHaveTextContent("chưa đạt (2 lỗi)");
    const items = within(panel).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("“cam kết” · mục cấm 2");
    expect(items[0]).toHaveTextContent("Khẳng định tuyệt đối.");
    expect(items[0]).toHaveTextContent("Gợi ý: Bỏ từ này.");
    // No rule number, no reason: only the quoted passage.
    expect(items[1]).toHaveTextContent("“Harmonic”");
    expect(items[1]).not.toHaveTextContent("mục cấm");
    // The script the orderer typed is still there to fix.
    expect(screen.getByRole("textbox", { name: "Nội dung order" })).toHaveValue(
      "Key: da căng bóng",
    );
  });

  it("requires the desired deadline, and refuses one in the past", async () => {
    stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    expect(submit()).toBeEnabled();
    // Cleared: the order cannot be sent without one.
    fireEvent.change(desiredDeadline(), { target: { value: "" } });
    expect(submit()).toBeDisabled();
    // In the past: blocked, with a hint.
    fireEvent.change(desiredDeadline(), { target: { value: "2020-01-01T09:00" } });
    expect(submit()).toBeDisabled();
    expect(
      screen.getByText("Deadline mong muốn đã qua — chọn một thời điểm sau bây giờ."),
    ).toBeInTheDocument();
    fireEvent.change(desiredDeadline(), { target: { value: FUTURE_DEADLINE } });
    expect(submit()).toBeEnabled();
  });

  it("sends a desired deadline with a resubmit only once it is changed", async () => {
    const ORDER_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
    const returned = (desired: string | null) => ({
      order: {
        id: ORDER_ID,
        code: "TUAN-D-261008-01",
        title: "Video serum",
        video_type: "D",
        video_type_label: "Dựng",
        process: ["DUNG"],
        video_kind_id: null,
        order_content: "Key: da căng bóng",
        script_source: "AI",
        design_link: "https://drive.example.com/design",
        reference_link: null,
        source_link: null,
        note: null,
        owner_user_id: "u-owner",
        owner_name: "Tuấn",
        stage: "ORDER_RETURNED",
        stage_label: "Bị trả",
        submitted_at: "2026-10-08T02:00:00Z",
        order_approved_at: null,
        returned_reason: "Thiếu key",
        is_priority: false,
        urgent: false,
        product_link: null,
        completed_at: null,
        cancelled_reason: null,
        version: 2,
        created_at: "2026-10-08T02:00:00Z",
        updated_at: "2026-10-08T03:00:00Z",
        desired_deadline_at: desired,
        over_deadline_count: 0,
      },
      nodes: [],
      submissions: [],
      approvals: [],
      events: [],
      available_actions: [],
    });
    const resubmit = () => screen.getByRole("button", { name: "Gửi lại order" });
    const sent = (stub: unknown) =>
      calls(stub).find((call) => call.method === "POST" && call.url.includes("/resubmit"))?.body;
    window.history.pushState({}, "", "/orders/new?edit=TUAN-D-261008-01");
    try {
      // An old order with no wish: one has to be given before it goes back.
      let stub = stubFetch([
        { match: "/api/units/me", body: ORDERER },
        { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
        { match: `/api/orders/${ORDER_ID}/resubmit`, method: "POST", body: returned(null) },
        { match: "/api/orders/TUAN-D-261008-01", body: returned(null) },
      ]);
      const first = renderWithQuery(<NewOrderPage />);
      await screen.findByText(/Lý do: Thiếu key/);
      expect(resubmit()).toBeDisabled();
      fireEvent.change(desiredDeadline(), { target: { value: FUTURE_DEADLINE } });
      expect(resubmit()).toBeEnabled();
      await userEvent.click(resubmit());
      await userEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Gửi lại order" }),
      );
      await waitFor(() =>
        expect(sent(stub)).toMatchObject({
          version: 2,
          desired_deadline_at: new Date(FUTURE_DEADLINE).toISOString(),
        }),
      );
      first.unmount();

      // A wish already set (even one now past) is kept as it is: not sent.
      const PAST = "2020-01-01T02:00:00.000Z";
      stub = stubFetch([
        { match: "/api/units/me", body: ORDERER },
        { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
        { match: `/api/orders/${ORDER_ID}/resubmit`, method: "POST", body: returned(PAST) },
        { match: "/api/orders/TUAN-D-261008-01", body: returned(PAST) },
      ]);
      renderWithQuery(<NewOrderPage />);
      await screen.findByText(/Lý do: Thiếu key/);
      expect(resubmit()).toBeEnabled();
      await userEvent.click(resubmit());
      await userEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Gửi lại order" }),
      );
      await waitFor(() => expect(sent(stub)).toBeDefined());
      expect(sent(stub)).not.toHaveProperty("desired_deadline_at");
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("never asks for Loại video, even when the unit has kinds", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await screen.findByRole("checkbox", { name: "Dựng" });
    await waitFor(() =>
      expect(screen.queryByRole("combobox", { name: /Loại video/ })).not.toBeInTheDocument(),
    );
  });

  it("edits a not-yet-approved order in place, via /edit (not /resubmit)", async () => {
    const ORDER_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff";
    const fresh = {
      order: {
        id: ORDER_ID,
        code: "K7Q2M",
        title: "Video serum",
        video_type: "D",
        video_type_label: "Dựng",
        process: ["DUNG"],
        video_kind_id: null,
        order_content: "Key: da căng bóng",
        script_source: "AI",
        design_link: "https://drive.example.com/design",
        reference_link: null,
        source_link: null,
        note: null,
        owner_user_id: "u-owner",
        owner_name: "Tuấn",
        stage: "ORDER_PENDING",
        stage_label: "Chờ duyệt",
        submitted_at: "2026-10-08T02:00:00Z",
        order_approved_at: null,
        returned_reason: null,
        is_priority: false,
        urgent: false,
        product_link: null,
        completed_at: null,
        cancelled_reason: null,
        version: 1,
        created_at: "2026-10-08T02:00:00Z",
        updated_at: "2026-10-08T02:00:00Z",
        desired_deadline_at: "2030-01-01T00:00:00Z",
        over_deadline_count: 0,
      },
      nodes: [],
      submissions: [],
      approvals: [],
      events: [],
      available_actions: [],
    };
    const save = () => screen.getByRole("button", { name: "Lưu thay đổi" });
    window.history.pushState({}, "", "/orders/new?edit=K7Q2M");
    try {
      const stub = stubFetch([
        { match: "/api/units/me", body: ORDERER },
        { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
        { match: `/api/orders/${ORDER_ID}/edit`, method: "POST", body: fresh },
        { match: "/api/orders/K7Q2M", body: fresh },
      ]);
      renderWithQuery(<NewOrderPage />);
      // The fresh-edit header, not the "fix per feedback" returned notice.
      await screen.findByText(/giữ nguyên trạng thái/);
      expect(screen.queryByText(/Lý do:/)).toBeNull();
      await userEvent.click(save());
      await userEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Lưu thay đổi" }),
      );
      await waitFor(() =>
        expect(
          calls(stub).some(
            (call) => call.method === "POST" && call.url.includes(`/orders/${ORDER_ID}/edit`),
          ),
        ).toBe(true),
      );
      // It never falls back to resubmit for a fresh order.
      expect(
        calls(stub).some((call) => call.method === "POST" && call.url.includes("/resubmit")),
      ).toBe(false);
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});

describe("the AI script check before sending", () => {
  const AI_ON = {
    ...ORDERER,
    units: [{ ...ADS_ENTRY, settings: { ...ADS_SETTINGS, script_ai_review: true } }],
  };
  const answer = (over: Record<string, unknown> = {}) => ({
    enabled: true,
    reviewed: true,
    passed: true,
    violations: [],
    problem: null,
    message: "AI duyệt kịch bản: đạt. Bạn có thể gửi order.",
    ...over,
  });
  const REVISED = "💥 Video serum\n◾️ Da căng bóng\n\n#Apexmed #EkipBacSiTien";
  const REFUSED = answer({
    passed: false,
    violations: [
      {
        rule: 2,
        rule_name: "Khẳng định tuyệt đối",
        quote: "cam kết",
        reason: "Khẳng định tuyệt đối.",
        suggestion: "Bỏ từ này.",
      },
    ],
    revised_script: REVISED,
    note: "Đã bổ sung hashtag và chân trang chuẩn.",
    message: "AI duyệt kịch bản: chưa đạt (1 lỗi).",
  });
  type Calls = { calls: Array<{ url: string; method: string; body: unknown }> };
  /** The server, answering "AI duyệt kịch bản" with `checked`. */
  const stubAi = (checked: unknown) =>
    stubFetch([
      { match: "/api/units/me", body: AI_ON },
      { match: "/api/units/ADS/members", body: MEMBERS },
      { match: "/api/units/ADS/video-kinds", body: { kinds: [] } },
      { match: "/api/orders/script-check", method: "POST", body: checked },
      { match: "/api/orders", method: "POST", body: { order: { code: "TUAN-D-261015-01" } } },
    ]) as unknown as Calls;
  const checkButton = () => screen.getByRole("button", { name: "AI duyệt kịch bản" });

  it("cannot send until the AI passed the script as it stands now", async () => {
    const first = stubAi(REFUSED);
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    expect(submit()).toBeDisabled();
    expect(screen.getByTestId("needs-script-check")).toBeInTheDocument();
    // Refused: a window opens with the errors and a corrected version.
    await userEvent.click(checkButton());
    const dialog = await screen.findByRole("dialog", { name: "KHÔNG ĐẠT - có 1 lỗi" });
    const errors = within(dialog).getByTestId("script-review-errors");
    expect(errors).toHaveTextContent("Lỗi 1:");
    expect(errors).toHaveTextContent("• Câu/cụm vi phạm: “cam kết”");
    expect(errors).toHaveTextContent(
      "• Vi phạm: Mục 2 - Khẳng định tuyệt đối (Khẳng định tuyệt đối.)",
    );
    expect(errors).toHaveTextContent("• Gợi ý sửa: Bỏ từ này.");
    expect(within(dialog).getByTestId("script-review-note")).toHaveTextContent(
      "(Đã bổ sung hashtag và chân trang chuẩn.)",
    );
    expect(within(dialog).getByTestId("script-review-revised").textContent).toBe(REVISED);
    await userEvent.click(within(dialog).getByRole("button", { name: "Quay lại sửa" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // The findings stay under the script, and the window opens again from there.
    const panel = screen.getByTestId("script-violations");
    await userEvent.click(within(panel).getByRole("button", { name: "Xem lỗi & bản sửa gợi ý" }));
    const again = screen.getByRole("dialog");
    await userEvent.click(within(again).getByRole("button", { name: "Quay lại sửa" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(panel).toHaveTextContent("“cam kết” · mục cấm 2");
    expect(submit()).toBeDisabled();
    expect(first.calls.find((call) => call.url.includes("/script-check"))?.body).toEqual({
      title: "Video serum",
      order_content: "Key: da căng bóng",
    });
    // Fixed and checked again: passed, the order may go.
    const script = screen.getByRole("textbox", { name: "Nội dung order" });
    await userEvent.clear(script);
    await userEvent.type(script, "Key: da căng bóng, đội ngũ bác sĩ Apexmed");
    // Typing passed through the refused text again: the window stayed shut.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/bấm duyệt lại/)).toBeInTheDocument();
    const second = stubAi(answer());
    await userEvent.click(checkButton());
    expect(await screen.findByTestId("script-check")).toHaveTextContent(
      "đạt. Bạn có thể gửi order",
    );
    expect(screen.queryByTestId("script-violations")).not.toBeInTheDocument();
    expect(submit()).toBeEnabled();
    expect(second.calls.find((call) => call.url.includes("/script-check"))?.body).toEqual({
      title: "Video serum",
      order_content: "Key: da căng bóng, đội ngũ bác sĩ Apexmed",
    });
    // An edit after the pass locks sending again.
    await userEvent.type(script, " cam kết");
    expect(submit()).toBeDisabled();
  });

  it("puts the corrected version into the script field, to be checked again", async () => {
    stubAi(REFUSED);
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(checkButton());
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Dùng bản sửa" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Nội dung order" })).toHaveValue(REVISED);
    // A new text: it has to be checked again before it may go.
    expect(screen.getByText(/bấm duyệt lại/)).toBeInTheDocument();
    expect(submit()).toBeDisabled();
  });

  it("lets the order go when the AI could not answer, saying so", async () => {
    stubAi(
      answer({
        reviewed: false,
        passed: false,
        problem: "AI đang lỗi",
        message: "AI chưa duyệt được (AI đang lỗi). Bạn vẫn gửi được order.",
      }),
    );
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(checkButton());
    expect(await screen.findByTestId("script-check")).toHaveTextContent("Bạn vẫn gửi được order");
    expect(submit()).toBeEnabled();
  });
});

describe("the video kinds manager", () => {
  const ALL_KINDS = {
    kinds: [
      ...KINDS.kinds,
      { id: KIND_OLD, name: "Quay khác", points: 2, active: false, sort_order: 3 },
    ],
  };

  function stubAdmin() {
    return stubFetch([
      { match: "/api/units/me", body: ADMIN },
      { match: "/api/units/ADS/members", body: { ...MEMBERS, members: [] } },
      { match: "/api/units/ADS/health", body: { unit: "ADS", warnings: [] } },
      { match: "/api/units/directory", body: [] },
      { match: "/api/units/ADS/video-kinds", method: "GET", body: ALL_KINDS },
      {
        match: "/api/units/ADS/video-kinds",
        method: "POST",
        body: { id: "dddddddd-dddd-dddd-dddd-dddddddddddd", name: "Kịch bản quay mới", points: 1.5, active: true, sort_order: 4 },
      },
      {
        match: `/api/units/ADS/video-kinds/${KIND_SHORT}`,
        method: "PATCH",
        body: { ...KINDS.kinds[1], points: 0.75 },
      },
    ]);
  }

  it("is no longer on Thiết lập ORD (0074): points live on Danh mục điểm", async () => {
    stubAdmin();
    renderWithQuery(<OrdSettingsPage />);
    await screen.findByRole("heading", { name: "Thiết lập ORD" });
    expect(
      screen.queryByRole("region", { name: "Loại video & điểm hiệu suất" }),
    ).not.toBeInTheDocument();
  });

  it("lists every kind, inactive ones greyed, from the admin listing", async () => {
    const fetchMock = stubAdmin();
    renderWithQuery(<VideoKindsManager code="ADS" />);
    const section = await screen.findByRole("region", {
      name: "Loại video & điểm hiệu suất",
    });
    expect(await within(section).findByDisplayValue("Video full diễn hoạt")).toBeInTheDocument();
    expect(within(section).getByLabelText("Điểm · Video full diễn hoạt")).toHaveValue(1);
    expect(within(section).getByLabelText("Đang dùng · Video full diễn hoạt")).toBeChecked();
    const old = within(section).getByLabelText("Đang dùng · Quay khác");
    expect(old).not.toBeChecked();
    expect(old.closest("tr")).toHaveClass("opacity-60");
    expect(
      calls(fetchMock).some(
        (call) =>
          call.url.includes("/api/units/ADS/video-kinds") &&
          call.url.includes("include_inactive=true"),
      ),
    ).toBe(true);
  });

  it("edits a kind and sends only what changed", async () => {
    const fetchMock = stubAdmin();
    renderWithQuery(<VideoKindsManager code="ADS" />);
    const points = await screen.findByLabelText("Điểm · Short video");
    const save = screen.getByRole("button", { name: "Lưu Short video" });
    expect(save).toBeDisabled();
    await userEvent.clear(points);
    await userEvent.type(points, "0.75");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const sent = calls(fetchMock).find((call) => call.method === "PATCH");
      expect(sent?.url).toContain(`/api/units/ADS/video-kinds/${KIND_SHORT}`);
      expect(sent?.body).toEqual({ points: 0.75 });
    });
  });

  it("reactivates a retired kind", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ADMIN },
      { match: "/api/units/ADS/video-kinds", method: "GET", body: ALL_KINDS },
      {
        match: `/api/units/ADS/video-kinds/${KIND_OLD}`,
        method: "PATCH",
        body: { ...ALL_KINDS.kinds[2], active: true },
      },
    ]);
    renderWithQuery(<VideoKindsManager code="ADS" />);
    await userEvent.click(await screen.findByLabelText("Đang dùng · Quay khác"));
    await userEvent.click(screen.getByRole("button", { name: "Lưu Quay khác" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => {
      const sent = calls(fetchMock).find((call) => call.method === "PATCH");
      expect(sent?.body).toEqual({ active: true });
    });
  });

  it("adds a new kind after the last one", async () => {
    const fetchMock = stubAdmin();
    renderWithQuery(<VideoKindsManager code="ADS" />);
    const name = await screen.findByLabelText("Tên loại video mới");
    const add = screen.getByRole("button", { name: "Thêm" });
    expect(add).toBeDisabled();
    await userEvent.type(name, "Kịch bản quay mới");
    const points = screen.getByLabelText("Điểm loại video mới");
    await userEvent.clear(points);
    await userEvent.type(points, "1.5");
    await userEvent.click(add);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Thêm" }));
    await waitFor(() => {
      const sent = calls(fetchMock).find((call) => call.method === "POST");
      expect(sent?.url).toContain("/api/units/ADS/video-kinds");
      expect(sent?.body).toEqual({
        name: "Kịch bản quay mới",
        points: 1.5,
        active: true,
        sort_order: 4,
      });
    });
  });

  it("is not shown to somebody who cannot administer the unit", async () => {
    const fetchMock = stubFetch([{ match: "/api/units/me", body: ORDERER }]);
    const { container } = renderWithQuery(<VideoKindsManager code="ADS" />);
    await waitFor(() =>
      expect(calls(fetchMock).some((call) => call.url.includes("/api/units/me"))).toBe(true),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(container).toBeEmptyDOMElement();
    expect(calls(fetchMock).some((call) => call.url.includes("video-kinds"))).toBe(false);
  });
});

describe("the task table's Ads filters", () => {
  it('no longer offers "Quy trình" or "Loại video" (2026-10-19), and never asks for the kinds', async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ORDERER },
      { match: "/api/units/ADS/members", body: MEMBERS },
      { match: "/api/units/ADS/video-kinds", body: KINDS },
      {
        match: "/api/board/tasks",
        body: { unit: "ADS", items: [], total: 0, limit: 10, offset: 0, phases: [] },
      },
    ]);
    SEARCH.value = new URLSearchParams("unit=ADS&kind=BD&video_kind_id=" + KIND_SHORT);
    renderWithQuery(<TasksPage />);
    expect(await screen.findByRole("combobox", { name: "Trạng thái task" })).toBeInTheDocument();
    for (const gone of ["Quy trình", "Loại video", "Marketing"]) {
      expect(screen.queryByRole("combobox", { name: gone })).not.toBeInTheDocument();
    }
    await waitFor(() =>
      expect(calls(fetchMock).some((call) => call.url.includes("/api/board/tasks"))).toBe(true),
    );
    // The old params left on the URL are not sent either.
    expect(
      calls(fetchMock).some(
        (call) =>
          call.url.includes("/api/board/tasks") &&
          (call.url.includes("video_kind_id=") || call.url.includes("kind=BD")),
      ),
    ).toBe(false);
    expect(calls(fetchMock).some((call) => call.url.includes("video-kinds"))).toBe(false);
  });
});
