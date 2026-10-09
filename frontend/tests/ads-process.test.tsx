/**
 * The Ads "Quy trình" and "Loại video": the create form builds the process
 * from checkboxes and sends it with the chosen video kind; a unit admin
 * keeps the catalogue of kinds and their points on the unit admin page.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewOrderPage from "@/app/orders/new/page";
import UnitsAdminPage from "@/app/admin/units/page";
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

function stubCreateForm(kinds: unknown = KINDS) {
  return stubFetch([
    { match: "/api/units/me", body: ORDERER },
    { match: "/api/units/ADS/members", body: MEMBERS },
    { match: "/api/units/ADS/video-kinds", body: kinds },
    {
      match: "/api/orders",
      method: "POST",
      body: { order: { code: "TUAN-BD-261008-01" } },
    },
  ]);
}

const route = () => screen.getByTestId("process-route").textContent;
const codePreview = () => screen.getByTestId("order-code-preview").textContent;
const submit = () => screen.getByRole("button", { name: "Gửi order" });

async function fillBasics() {
  await userEvent.type(
    await screen.findByRole("textbox", { name: /Tên kịch bản/ }),
    "Video serum",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Nội dung order" }),
    "Key: da căng bóng",
  );
}

describe("the create form's Quy trình", () => {
  it("starts with Order and Dựng, and Order cannot be unticked", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    const order = await screen.findByRole("checkbox", { name: /^Order/ });
    expect(order).toBeChecked();
    expect(order).toBeDisabled();
    await userEvent.click(order);
    expect(order).toBeChecked();
    expect(screen.getByText("(luôn có)")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Dựng" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Biên kịch" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Design" })).not.toBeChecked();
    expect(route()).toBe("Order › Dựng › Người order duyệt final");
    expect(codePreview()).toBe("TUAN-D-yymmdd-nn");
  });

  it("changes the route and the code preview as boxes are ticked", async () => {
    stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Biên kịch" }));
    expect(route()).toBe(
      "Order › Biên kịch › Dựng › Người order duyệt final",
    );
    expect(codePreview()).toBe("TUAN-BD-yymmdd-nn");

    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    expect(codePreview()).toBe("TUAN-BTD-yymmdd-nn");
    expect(route()).toBe(
      "Order › Biên kịch › Design › Dựng › Người order duyệt final",
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    expect(codePreview()).toBe("TUAN-BT-yymmdd-nn");
    // The side panel names the orderer as the final reviewer.
    expect(screen.getByText("Bạn (người order) duyệt final")).toBeInTheDocument();
  });

  it("sends the process and the video kind", async () => {
    const fetchMock = stubCreateForm();
    renderWithQuery(<NewOrderPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Biên kịch" }));
    await fillBasics();
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link thiết kế/ }),
      "https://drive.example.com/design",
    );
    const kind = screen.getByRole("combobox", { name: /Loại video/ });
    expect(
      within(kind)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Chọn loại video…",
      "Video full diễn hoạt · 1 điểm",
      "Short video · 0,5 điểm",
    ]);
    // The kind is required while the unit has any.
    expect(submit()).toBeDisabled();
    await userEvent.selectOptions(kind, KIND_FULL);
    expect(submit()).toBeEnabled();
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
        video_kind_id: KIND_FULL,
        design_link: "https://drive.example.com/design",
      });
      expect(sent?.body).not.toHaveProperty("video_type");
      expect(sent?.body).not.toHaveProperty("preassigned");
    });
  });

  it("requires the design link only for Dựng without Design", async () => {
    stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    // Dựng alone: a design must come with the order.
    expect(screen.getByText("Bắt buộc khi có Dựng mà không có Design")).toBeInTheDocument();
    expect(submit()).toBeDisabled();
    // Design in the process: optional.
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    expect(screen.getByText("Không bắt buộc")).toBeInTheDocument();
    expect(submit()).toBeEnabled();
    // Biên kịch alone has no Dựng: optional too.
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Biên kịch" }));
    expect(screen.getByText("Không bắt buộc")).toBeInTheDocument();
    expect(submit()).toBeEnabled();
  });

  it("blocks the submit with a hint when no production node is ticked", async () => {
    stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await fillBasics();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    expect(submit()).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Design" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Dựng" }));
    expect(submit()).toBeDisabled();
    expect(
      screen.getByText("Chọn ít nhất một công đoạn: Biên kịch, Design hoặc Dựng."),
    ).toBeInTheDocument();
    expect(codePreview()).toBe("TUAN-?-yymmdd-nn");
  });

  it("hides Loại video when the unit has no kinds", async () => {
    stubCreateForm({ kinds: [] });
    renderWithQuery(<NewOrderPage />);
    await screen.findByRole("checkbox", { name: "Dựng" });
    await waitFor(() =>
      expect(screen.queryByRole("combobox", { name: /Loại video/ })).not.toBeInTheDocument(),
    );
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

  it("lists every kind, inactive ones greyed, from the admin listing", async () => {
    const fetchMock = stubAdmin();
    renderWithQuery(<UnitsAdminPage />);
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
    renderWithQuery(<UnitsAdminPage />);
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
    renderWithQuery(<UnitsAdminPage />);
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
  it("filters by process code and by video kind", async () => {
    const fetchMock = stubFetch([
      { match: "/api/units/me", body: ORDERER },
      { match: "/api/units/ADS/members", body: MEMBERS },
      { match: "/api/units/ADS/video-kinds", body: KINDS },
      {
        match: "/api/board/tasks",
        body: { unit: "ADS", items: [], total: 0, limit: 10, offset: 0, phases: [] },
      },
    ]);
    SEARCH.value = new URLSearchParams("unit=ADS");
    renderWithQuery(<TasksPage />);
    const process = await screen.findByRole("combobox", { name: "Quy trình" });
    expect(
      within(process)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Tất cả",
      "B · Biên kịch",
      "T · Design",
      "D · Dựng",
      "BT · Biên kịch › Design",
      "BD · Biên kịch › Dựng",
      "TD · Design › Dựng",
      "BTD · Biên kịch › Design › Dựng",
    ]);
    await userEvent.selectOptions(process, "BD");
    expect(SEARCH.value.get("kind")).toBe("BD");
    const kind = await screen.findByRole("combobox", { name: "Loại video" });
    await userEvent.selectOptions(kind, KIND_SHORT);
    expect(SEARCH.value.get("video_kind_id")).toBe(KIND_SHORT);
    await waitFor(() =>
      expect(
        calls(fetchMock).some(
          (call) =>
            call.url.includes("/api/board/tasks") &&
            call.url.includes(`video_kind_id=${KIND_SHORT}`) &&
            call.url.includes("kind=BD"),
        ),
      ).toBe(true),
    );
  });
});
