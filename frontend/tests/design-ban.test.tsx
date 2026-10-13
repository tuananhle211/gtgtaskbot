/**
 * The Design ban: the nav tree, the simplified task table, "mã video" and
 * "Order sinh nhật".
 *
 * Nav: "Quản lý task" became the branch "Bảng order" - "Order tổng" for
 * everybody, "Order sinh nhật" and "Bảng mã video" by the `design` block of
 * `/api/units/me`. There is no "Tạo order" entry any more: an order is created
 * from the board of its own kind.
 *
 * Task table: a `design.view` viewer reads ORD's rows as Order / Tên · Loại /
 * Trạng thái · Đang giữ / Deadline / Nội dung / Tên task / Thiết kế; everybody
 * else keeps the per-step cells and "Kết quả".
 *
 * Mã video: the picker (add, quantity, remove, required) on its own and inside
 * the task page's action dialog, where the chosen list is sent as
 * `design_codes`; the task page's "Mã video" block and the catalogue screen.
 *
 * Order sinh nhật: the board asks for `category=BIRTHDAY&order=birthday`, and
 * its modal creates and edits.
 */

import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskDetailPage from "@/app/tasks/[ref]/page";
import TasksPage from "@/app/tasks/page";
import BirthdayOrdersPage from "@/app/tasks/birthday/page";
import DesignCodesPage from "@/app/tasks/design-codes/page";
import {
  DesignCodesPicker,
  designCodesValid,
  toDesignCodePicks,
} from "@/components/design-codes-picker";
import { Shell } from "@/components/shell";
import type { DesignCode, DesignCodeItem, TaskRow, UnifiedTaskDetail } from "@/lib/api";
import { formatBirthday, genderLabel } from "@/lib/birthday";
import { confirm, renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: "MY-T-261012-01" }),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
    refresh: vi.fn(),
    back: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  pathname = "/tasks";
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const ADS_SETTINGS = {
  urgent_days: 7,
  media_nas_url: null,
  design_nas_url: null,
  telegram_enabled: false,
  review_bien_tap: false,
  review_thiet_ke: false,
  review_dung: false,
  review_video_by_script_lead: false,
  effort_enabled: true,
  default_daily_tokens: 8,
  perf_weights: { output: 0.5, on_time: 0.3, quality: 0.2 },
  output_target: null,
};

type Design = { view: boolean; manage_codes: boolean; birthday: boolean };

/** `/api/units/me` for somebody in ORD, with or without the `design` block. */
const ME_AS = (role: string, design?: Design, extra: Record<string, unknown> = {}) => ({
  units: [
    {
      code: "ADS",
      label: "Luồng Order (ORD)",
      role,
      role_label: role,
      is_lead: false,
      member_code: "MY",
      personal_nas_url: null,
      settings: ADS_SETTINGS,
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  effort_enabled: true,
  ...(design ? { design } : {}),
  ...extra,
});

const DESIGNER: Design = { view: true, manage_codes: false, birthday: true };
const DESIGN_LEAD: Design = { view: true, manage_codes: true, birthday: true };
const ORDERER: Design = { view: false, manage_codes: false, birthday: true };

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

const TKBTS: DesignCode = {
  id: "c0000000-0000-4000-8000-000000000001",
  code: "TKBTS",
  name: "SOCIAL - Thiết kế Banner, Thumb Video, Avatar",
  active: true,
  sort_order: 1,
};
const CA: DesignCode = {
  id: "c0000000-0000-4000-8000-000000000002",
  code: "CA",
  name: "Cover Album",
  active: true,
  sort_order: 2,
};
const OLD: DesignCode = {
  id: "c0000000-0000-4000-8000-000000000003",
  code: "OLD",
  name: "Mã đã ngừng",
  active: false,
  sort_order: 3,
};

const item = (code: DesignCode, quantity: number): DesignCodeItem => ({
  code_id: code.id,
  code: code.code,
  name: code.name,
  quantity,
});

// --- 1. The nav tree -----------------------------------------------------------

describe('the "Bảng order" branch of the nav', () => {
  const primary = () => screen.getByRole("navigation", { name: "Điều hướng chính" });
  const navHrefs = () =>
    within(primary())
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
  const shell = (design?: Design) => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: ME_AS("THIET_KE", design) },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
  };

  it('holds only "Order tổng" without the design flags, and no "Tạo order" entry', async () => {
    shell();
    await screen.findByText("Luồng của bạn");
    await waitFor(() => expect(navHrefs()).toEqual(["/dashboard", "/tasks"]));
    const branch = within(primary()).getByRole("group", { name: "Bảng order" });
    expect(within(branch).getByRole("button", { name: "Bảng order" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(
      within(branch)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Order tổng"]);
    // Creating moved to the boards: the sidebar has no entry for it.
    expect(within(primary()).queryByRole("link", { name: /Tạo order/ })).not.toBeInTheDocument();
    expect(navHrefs()).not.toContain("/orders/new");
    expect(within(primary()).queryByText("Quản lý task")).not.toBeInTheDocument();
  });

  it('adds "Order sinh nhật" for design.birthday, and not the catalogue', async () => {
    shell(DESIGNER);
    expect(await screen.findByRole("link", { name: "Order sinh nhật" })).toHaveAttribute(
      "href",
      "/tasks/birthday",
    );
    expect(navHrefs()).toEqual(["/dashboard", "/tasks", "/tasks/birthday"]);
    expect(screen.queryByRole("link", { name: "Bảng mã video" })).not.toBeInTheDocument();
  });

  it('adds "Bảng mã video" as the third child for design.manage_codes', async () => {
    shell(DESIGN_LEAD);
    await screen.findByRole("link", { name: "Bảng mã video" });
    const branch = within(primary()).getByRole("group", { name: "Bảng order" });
    expect(
      within(branch)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Order tổng", "/tasks"],
      ["Order sinh nhật", "/tasks/birthday"],
      ["Bảng mã video", "/tasks/design-codes"],
    ]);
  });

  it("shows the catalogue alone when only manage_codes is set", async () => {
    shell({ view: false, manage_codes: true, birthday: false });
    await screen.findByRole("link", { name: "Bảng mã video" });
    expect(navHrefs()).toEqual(["/dashboard", "/tasks", "/tasks/design-codes"]);
  });

  it("marks the board on screen, not the one it is nested under", async () => {
    pathname = "/tasks/birthday";
    shell(DESIGN_LEAD);
    const birthday = await screen.findByRole("link", { name: "Order sinh nhật" });
    expect(birthday).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Order tổng" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Bảng mã video" })).not.toHaveAttribute("aria-current");
  });

  it('keeps a task\'s own page under "Order tổng"', async () => {
    pathname = "/tasks/MY-T-261012-01";
    shell(DESIGN_LEAD);
    await screen.findByRole("link", { name: "Order sinh nhật" });
    expect(screen.getByRole("link", { name: "Order tổng" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Order sinh nhật" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("folds and unfolds the branch", async () => {
    shell(DESIGNER);
    await screen.findByRole("link", { name: "Order sinh nhật" });
    const heading = screen.getByRole("button", { name: "Bảng order" });
    await userEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "true");
  });
});

// --- 2. The task table ---------------------------------------------------------

const ROW: TaskRow = {
  unit: "ADS",
  unit_label: "Luồng Order (ORD)",
  id: "o-1",
  code: "TUAN-BT-261012-01",
  title: "Banner khai trương",
  kind: "BT",
  kind_label: "Biên kịch › Design",
  owner_user_id: "u-owner",
  owner_name: "Tuấn",
  created_at: "2026-10-12T02:00:00Z",
  phase: "PRODUCTION",
  phase_label: "Sản xuất",
  status: "THIET_KE",
  status_label: "Design · Đang làm",
  cells: [
    {
      key: "BIEN_TAP",
      label: "Biên kịch",
      person_name: "Hà Biên Kịch",
      status: "HOAN_THANH",
      status_label: "Hoàn thành",
      is_current: false,
      since: null,
      revisions: 0,
    },
    {
      key: "THIET_KE",
      label: "Design",
      person_name: "Diễm My",
      status: "DANG_LAM",
      status_label: "Đang làm",
      is_current: true,
      since: null,
      revisions: 0,
    },
  ],
  product_link: null,
  returned_at: null,
  is_priority: false,
  urgent: false,
  detail_path: "/tasks/TUAN-BT-261012-01",
  version: 4,
  stage_since: null,
  revisions: 0,
  current_person_name: "Diễm My",
  latest_link: null,
  extras: [{ label: "Loại video", value: "Banner" }],
  deadline_at: "2026-10-20T10:00:00Z",
  deadline_status: "ON_TRACK",
  category: "STANDARD",
  design: {
    script_link: "https://docs.example.com/kich-ban",
    script_by: "Hà Biên Kịch",
    reference_link: "https://drive.example.com/tham-khao",
    note: "Tông xanh",
    codes: [item(TKBTS, 3), item(CA, 1)],
    proposed_codes: null,
    designer: "Diễm My",
    design_link: "https://drive.example.com/thiet-ke",
  },
};

const PAGE = (items: TaskRow[]) => ({
  unit: "ADS",
  items,
  total: items.length,
  limit: 10,
  offset: 0,
  phases: [],
});
const COUNTS = { mine: 2, awaiting_me: 1, priority: 0, urgent: 0, overdue: 0 };
const ADS_ROSTER = {
  match: "/api/units/ADS/members",
  body: { unit: "ADS", unit_label: "ORD", members: [], assignable_roles: [] },
};
const KINDS = { match: "/api/units/ADS/video-kinds", body: { kinds: [] } };

const headers = () =>
  within(screen.getByRole("table"))
    .getAllByRole("columnheader")
    .map((cell) => cell.textContent);

const tasksPage = (me: unknown, rows: TaskRow[] = [ROW]) => {
  const stub = stubFetch([
    { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
    { match: "/api/units/me", body: me },
    ADS_ROSTER,
    KINDS,
    { match: "/api/board/counts", body: COUNTS },
    { match: "/api/board/tasks", body: PAGE(rows) },
  ]);
  renderWithQuery(<TasksPage />);
  return stub;
};

describe("the task table as the Design ban reads it", () => {
  it("keeps the first four columns, then Nội dung / Tên task / Thiết kế", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER));
    await screen.findByText("Banner khai trương");
    expect(headers()).toEqual([
      "Order",
      "Tên · Loại",
      "Trạng thái · Đang giữ",
      "Deadline",
      "Nội dung",
      "Tên task",
      "Thiết kế",
      "Thao tác",
    ]);
    // The per-step cells, "Kết quả" and the row's extras are gone.
    expect(screen.queryByText("Trả sửa")).not.toBeInTheDocument();
    expect(screen.queryByText("Loại video: Banner")).not.toBeInTheDocument();
    expect(screen.queryByText("Hà Biên Kịch", { selector: ".person.text-\\[13px\\]" })).toBeNull();
    // What the first four still show.
    expect(screen.getByRole("link", { name: "TUAN-BT-261012-01" })).toHaveAttribute(
      "href",
      "/tasks/TUAN-BT-261012-01",
    );
    expect(screen.getByTestId("row-deadline")).toBeInTheDocument();
    // No "Mở" button: the whole row opens the task.
    expect(screen.queryByRole("link", { name: "Mở" })).not.toBeInTheDocument();
  });

  it("draws the script, the codes with their quantities and the design hand-in", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER));
    const content = await screen.findByTestId("design-content");
    expect(within(content).getByRole("link", { name: /Kịch bản/ })).toHaveAttribute(
      "href",
      "https://docs.example.com/kich-ban",
    );
    expect(content).toHaveTextContent("Biên kịch: Hà Biên Kịch");
    // The reference link is only the fallback.
    expect(within(content).queryByRole("link", { name: /Link tham khảo/ })).toBeNull();

    const codes = screen.getByTestId("design-codes");
    expect(
      within(codes)
        .getAllByRole("listitem")
        .map((line) => line.textContent?.replace(/\s+/g, " ").trim()),
    ).toEqual([
      "SOCIAL - Thiết kế Banner, Thumb Video, Avatar TKBTS× 3",
      "Cover Album CA× 1",
    ]);
    expect(within(codes).queryByText("Chờ duyệt đổi mã")).not.toBeInTheDocument();

    const designer = screen.getByTestId("design-designer");
    expect(designer).toHaveTextContent("Diễm My");
    expect(within(designer).getByRole("link", { name: /Link thiết kế/ })).toHaveAttribute(
      "href",
      "https://drive.example.com/thiet-ke",
    );
  });

  it("falls back to the reference link, and marks a pending change of codes", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [
      {
        ...ROW,
        design: {
          ...ROW.design,
          script_link: null,
          script_by: null,
          proposed_codes: [item(CA, 2)],
          designer: null,
          design_link: null,
        },
      },
    ]);
    const content = await screen.findByTestId("design-content");
    expect(within(content).getByRole("link", { name: /Link tham khảo/ })).toHaveAttribute(
      "href",
      "https://drive.example.com/tham-khao",
    );
    expect(within(screen.getByTestId("design-codes")).getByText("Chờ duyệt đổi mã")).toBeInTheDocument();
    expect(screen.getByTestId("design-designer")).toHaveTextContent("Chưa có người thiết kế");
  });

  it("renders a row from an API that sends no design block", async () => {
    tasksPage(ME_AS("THIET_KE", DESIGNER), [{ ...ROW, design: undefined }]);
    await screen.findByText("Banner khai trương");
    expect(screen.getByTestId("design-content")).toHaveTextContent("–");
    expect(screen.getByTestId("design-codes")).toHaveTextContent("–");
  });

  it("is the ordinary table for everybody else", async () => {
    // An orderer may open the birthday board, but is not in the Design ban.
    tasksPage(ME_AS("ORDERER", ORDERER));
    await screen.findByText("Banner khai trương");
    expect(headers()).toEqual([
      "Order",
      "Tên · Loại",
      "Trạng thái · Đang giữ",
      "Deadline",
      "Biên kịch",
      "Design",
      "Kết quả",
      "Thao tác",
    ]);
    expect(screen.getByText("Trả sửa")).toBeInTheDocument();
    expect(screen.getByText("Loại video: Banner")).toBeInTheDocument();
    expect(screen.queryByTestId("design-codes")).not.toBeInTheDocument();
  });

  it("is the ordinary table without the design block at all", async () => {
    tasksPage(ME_AS("THIET_KE"));
    await screen.findByText("Banner khai trương");
    expect(headers()).toContain("Kết quả");
    expect(headers()).not.toContain("Tên task");
  });

  it('offers "+ Tạo order" on the board, now that the nav has none', async () => {
    tasksPage(ME_AS("ORDERER", ORDERER));
    await screen.findByText("Banner khai trương");
    expect(screen.getByRole("link", { name: "+ Tạo order" })).toHaveAttribute(
      "href",
      "/orders/new?unit=ADS",
    );
  });
});

// --- 3. The picker -------------------------------------------------------------

function PickerHarness({
  initial = [],
  required = false,
  hint,
  options = [TKBTS, CA],
}: {
  initial?: DesignCodeItem[];
  required?: boolean;
  hint?: string;
  options?: DesignCode[];
}) {
  const [value, setValue] = useState<DesignCodeItem[]>(initial);
  return (
    <>
      <DesignCodesPicker
        value={value}
        onChange={setValue}
        options={options}
        required={required}
        hint={hint}
      />
      <output data-testid="picks">{JSON.stringify(toDesignCodePicks(value))}</output>
      <output data-testid="valid">{String(designCodesValid(value, required))}</output>
    </>
  );
}

const picks = () => JSON.parse(screen.getByTestId("picks").textContent ?? "[]");
const valid = () => screen.getByTestId("valid").textContent;

describe("the mã video picker", () => {
  it("adds a code with quantity 1, and offers each code once", async () => {
    renderWithQuery(<PickerHarness required />);
    const add = screen.getByRole("combobox", { name: "Mã video (bắt buộc)" });
    expect(screen.getByText("Chưa chọn mã video nào.")).toBeInTheDocument();
    expect(valid()).toBe("false");
    expect(within(add).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— thêm mã video —",
      "TKBTS · SOCIAL - Thiết kế Banner, Thumb Video, Avatar",
      "CA · Cover Album",
    ]);
    await userEvent.selectOptions(add, TKBTS.id);
    expect(picks()).toEqual([{ code_id: TKBTS.id, quantity: 1 }]);
    expect(valid()).toBe("true");
    expect(within(add).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "— thêm mã video —",
      "CA · Cover Album",
    ]);
    await userEvent.selectOptions(add, CA.id);
    expect(screen.getAllByTestId("design-code-item")).toHaveLength(2);
    expect(within(add).getByRole("option")).toHaveTextContent("Không còn mã nào để thêm");
    expect(add).toBeDisabled();
  });

  it("changes a quantity, and refuses anything below 1", async () => {
    renderWithQuery(<PickerHarness initial={[item(TKBTS, 1)]} />);
    const quantity = screen.getByRole("spinbutton", { name: "Số lượng · TKBTS" });
    expect(quantity).toHaveValue(1);
    await userEvent.clear(quantity);
    // Being retyped: not a quantity yet.
    expect(valid()).toBe("false");
    expect(screen.getByText(/Số lượng mỗi mã là số nguyên từ 1/)).toBeInTheDocument();
    await userEvent.type(quantity, "4");
    expect(picks()).toEqual([{ code_id: TKBTS.id, quantity: 4 }]);
    expect(valid()).toBe("true");
    fireEvent.change(quantity, { target: { value: "0" } });
    expect(valid()).toBe("false");
    fireEvent.change(quantity, { target: { value: "2.5" } });
    expect(valid()).toBe("false");
    fireEvent.change(quantity, { target: { value: "1000" } });
    expect(valid()).toBe("false");
    fireEvent.change(quantity, { target: { value: "999" } });
    expect(valid()).toBe("true");
  });

  it("removes a code, which is offered again", async () => {
    renderWithQuery(<PickerHarness initial={[item(TKBTS, 3), item(CA, 1)]} required />);
    await userEvent.click(screen.getByRole("button", { name: "Bỏ mã TKBTS" }));
    expect(picks()).toEqual([{ code_id: CA.id, quantity: 1 }]);
    const add = screen.getByRole("combobox", { name: "Mã video (bắt buộc)" });
    expect(within(add).getByRole("option", { name: /TKBTS/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Bỏ mã CA" }));
    expect(picks()).toEqual([]);
    // Required: an empty list may not be sent.
    expect(valid()).toBe("false");
  });

  it("accepts an empty list when nothing is required", () => {
    renderWithQuery(<PickerHarness />);
    expect(screen.getByRole("combobox", { name: "Mã video" })).toBeInTheDocument();
    expect(valid()).toBe("true");
  });

  it("keeps a code that was retired since, without offering it again", async () => {
    renderWithQuery(<PickerHarness initial={[item(OLD, 2)]} options={[TKBTS, CA, OLD]} />);
    expect(screen.getByTestId("design-code-item")).toHaveTextContent("OLD Mã đã ngừng");
    expect(valid()).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "Bỏ mã OLD" }));
    const add = screen.getByRole("combobox", { name: "Mã video" });
    expect(within(add).queryByRole("option", { name: /OLD/ })).not.toBeInTheDocument();
  });

  it("shows the action's hint under the list", () => {
    renderWithQuery(
      <PickerHarness hint="Sửa mã video thì bài nộp cần Trưởng phòng Design duyệt." />,
    );
    expect(
      screen.getByText("Sửa mã video thì bài nộp cần Trưởng phòng Design duyệt."),
    ).toBeInTheDocument();
  });
});

// --- 4. The task page ----------------------------------------------------------

const TASK_ID = "7c1d6f0e-bbbb-4c3b-9a3e-0000000000d1";

const DETAIL: UnifiedTaskDetail = {
  task: {
    id: TASK_ID,
    unit: "ADS",
    unit_label: "Luồng Order (ORD)",
    code: "MY-T-261012-01",
    title: "Banner khai trương",
    kind: "BT",
    kind_label: "Biên kịch › Design",
    phase: "PRODUCTION",
    phase_label: "Sản xuất",
    stage: "BIEN_TAP",
    stage_label: "Biên kịch · Đang làm",
    state: "DANG_LAM",
    owner: { user_id: "u-owner", name: "Tuấn Marketing" },
    current_person: { user_id: SESSION.user_id, name: "Hà Biên Kịch" },
    is_priority: false,
    urgent: false,
    created_at: "2026-10-12T02:00:00+00:00",
    updated_at: "2026-10-12T03:00:00+00:00",
    stage_since: "2026-10-12T03:00:00+00:00",
    finished_at: null,
    product_link: null,
    latest_link: null,
    revisions: 0,
    version: 5,
    source: { type: "ORDER", id: "o-1" },
  },
  steps: [],
  people: [],
  fields: [],
  submissions: [],
  timeline: [],
  actions: [],
  category: "STANDARD",
  design: { codes: [], proposed_codes: null },
  birthday: null,
};

const SCRIPT_HAND_IN = {
  key: "ads:SUBMIT_WORK:n-bt",
  label: "Nộp bài · Biên kịch",
  emphasis: "PRIMARY" as const,
  requires_note: false,
  inputs: ["link", "design_codes"] as UnifiedTaskDetail["actions"][number]["inputs"],
  required_inputs: ["design_codes"] as UnifiedTaskDetail["actions"][number]["inputs"],
  assignee_options: [],
};

const HINT = "Sửa mã video thì bài nộp cần Trưởng phòng Design duyệt.";

const DESIGN_HAND_IN = {
  key: "ads:SUBMIT_WORK:n-tk",
  label: "Nộp bài · Design",
  emphasis: "PRIMARY" as const,
  requires_note: false,
  inputs: ["link", "design_codes"] as UnifiedTaskDetail["actions"][number]["inputs"],
  required_inputs: ["link"] as UnifiedTaskDetail["actions"][number]["inputs"],
  assignee_options: [],
  design_codes: [item(TKBTS, 3), item(OLD, 1)],
  hint: HINT,
};

const detailPage = (detail: UnifiedTaskDetail, catalogue: DesignCode[] = [TKBTS, CA]) => {
  const stub = stubFetch([
    { match: "/api/units/me", body: ME_AS("BIEN_TAP", ORDERER) },
    { match: "/api/units/ADS/design-codes", body: { codes: catalogue } },
    { match: `/api/tasks/${TASK_ID}/actions`, method: "POST", body: detail },
    { match: `/api/tasks/${detail.task.code}`, body: detail },
  ]);
  renderWithQuery(<TaskDetailPage />);
  return stub;
};

const accept = () =>
  screen.getByRole("dialog").querySelector<HTMLButtonElement>("[data-confirm-accept]")!;
const actionBody = (stub: unknown) =>
  calls(stub).find((call) => call.method === "POST" && call.url.endsWith("/actions"))?.body;

describe("mã video in the action dialog", () => {
  it("is required at the script hand-in, and is sent as design_codes", async () => {
    const stub = detailPage({ ...DETAIL, actions: [SCRIPT_HAND_IN] });
    await userEvent.click(await screen.findByRole("button", { name: "Nộp bài · Biên kịch" }));
    const dialog = await screen.findByRole("dialog");
    const add = within(dialog).getByRole("combobox", { name: "Mã video (bắt buộc)" });
    // Nothing chosen yet: the hand-in cannot be confirmed.
    expect(accept()).toBeDisabled();
    await waitFor(() => expect(within(add).getAllByRole("option")).toHaveLength(3));
    // The picker asks for the active catalogue only.
    expect(
      calls(stub).some((call) => call.url === "/api/units/ADS/design-codes"),
    ).toBe(true);
    await userEvent.selectOptions(add, TKBTS.id);
    await userEvent.selectOptions(add, CA.id);
    const quantity = within(dialog).getByRole("spinbutton", { name: "Số lượng · TKBTS" });
    await userEvent.clear(quantity);
    expect(accept()).toBeDisabled();
    await userEvent.type(quantity, "3");
    expect(accept()).toBeEnabled();
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Link" }),
      "https://docs.example.com/kich-ban",
    );
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toBeDefined());
    expect(actionBody(stub)).toEqual({
      key: "ads:SUBMIT_WORK:n-bt",
      version: 5,
      link: "https://docs.example.com/kich-ban",
      design_codes: [
        { code_id: TKBTS.id, quantity: 3 },
        { code_id: CA.id, quantity: 1 },
      ],
    });
  });

  it("starts from the order's codes at the Design hand-in, with the hint", async () => {
    const stub = detailPage({
      ...DETAIL,
      design: { codes: [item(TKBTS, 3), item(OLD, 1)], proposed_codes: null },
      actions: [DESIGN_HAND_IN],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Nộp bài · Design" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(HINT)).toBeInTheDocument();
    // Prefilled - the retired code included, from its own snapshot.
    expect(
      within(dialog)
        .getAllByTestId("design-code-item")
        .map((line) => line.textContent),
    ).toEqual([
      "TKBTS SOCIAL - Thiết kế Banner, Thumb Video, AvatarBỏ",
      "OLD Mã đã ngừngBỏ",
    ]);
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Link sản phẩm (bắt buộc)" }),
      "https://drive.example.com/thiet-ke",
    );
    // Unchanged list: sent as it is, the server decides what it means.
    expect(accept()).toBeEnabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Bỏ mã OLD" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Bỏ mã TKBTS" }));
    // A list that was there may be changed, never emptied.
    expect(accept()).toBeDisabled();
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Mã video (bắt buộc)" }),
      CA.id,
    );
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toBeDefined());
    expect(actionBody(stub)).toEqual({
      key: "ads:SUBMIT_WORK:n-tk",
      version: 5,
      link: "https://drive.example.com/thiet-ke",
      design_codes: [{ code_id: CA.id, quantity: 1 }],
    });
  });

  it("sends no design_codes for an action that does not list the input", async () => {
    const stub = detailPage({
      ...DETAIL,
      actions: [{ ...SCRIPT_HAND_IN, inputs: ["link"], required_inputs: [] }],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Nộp bài · Biên kịch" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("group", { name: "Mã video" })).not.toBeInTheDocument();
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toBeDefined());
    expect(actionBody(stub)).toEqual({ key: "ads:SUBMIT_WORK:n-bt", version: 5 });
    expect(calls(stub).some((call) => call.url.includes("design-codes"))).toBe(false);
  });
});

describe("the task page's own blocks", () => {
  it('lists the order\'s codes under "Mã video"', async () => {
    detailPage({ ...DETAIL, design: { codes: [item(TKBTS, 3), item(CA, 1)], proposed_codes: null } });
    const block = await screen.findByRole("region", { name: "Mã video" });
    expect(within(block).getAllByRole("listitem")).toHaveLength(2);
    expect(block).toHaveTextContent("× 3");
    expect(within(block).queryByText(/Đề xuất đổi mã/)).not.toBeInTheDocument();
  });

  it("shows both lists while a change waits for the Trưởng phòng Design", async () => {
    detailPage({
      ...DETAIL,
      design: { codes: [item(TKBTS, 3)], proposed_codes: [item(TKBTS, 2), item(CA, 1)] },
    });
    const block = await screen.findByRole("region", { name: "Mã video" });
    expect(
      within(block).getByText("Đề xuất đổi mã – chờ Trưởng phòng Design duyệt"),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId("design-codes-current")).getAllByRole("listitem")).toHaveLength(1);
    expect(within(screen.getByTestId("design-codes-proposed")).getAllByRole("listitem")).toHaveLength(2);
  });

  it("draws neither block for an order that has neither", async () => {
    detailPage(DETAIL);
    await screen.findByRole("heading", { name: "Banner khai trương" });
    expect(screen.queryByRole("region", { name: "Mã video" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Order sinh nhật" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Quản lý task" })).toHaveAttribute(
      "href",
      "/tasks?unit=ADS",
    );
  });

  it("says who a birthday order is for, and leads back to the birthday board", async () => {
    detailPage({
      ...DETAIL,
      category: "BIRTHDAY",
      birthday: {
        full_name: "Trần Diễm My",
        branch: "VP - Hà Nội",
        gender: "FEMALE",
        birthday_on: "1998-10-01",
      },
    });
    const block = await screen.findByRole("region", { name: "Order sinh nhật" });
    expect(block).toHaveTextContent("Trần Diễm My");
    expect(block).toHaveTextContent("VP - Hà Nội");
    expect(block).toHaveTextContent("Nữ");
    expect(block).toHaveTextContent("01/10/1998");
    expect(
      within(screen.getByRole("navigation", { name: "Vị trí" })).getByRole("link", {
        name: "Order sinh nhật",
      }),
    ).toHaveAttribute("href", "/tasks/birthday");
  });

  it("renders a task from an API that predates all of it", async () => {
    const old = { ...DETAIL } as Partial<UnifiedTaskDetail>;
    delete old.category;
    delete old.design;
    delete old.birthday;
    detailPage(old as UnifiedTaskDetail);
    await screen.findByRole("heading", { name: "Banner khai trương" });
    expect(screen.queryByRole("region", { name: "Mã video" })).not.toBeInTheDocument();
  });
});

// --- 5. The birthday board -----------------------------------------------------

const BIRTHDAY_ROW: TaskRow = {
  ...ROW,
  id: "o-b1",
  code: "MY-T-261012-01",
  title: "Trần Diễm My",
  kind: "T",
  kind_label: "Design",
  owner_user_id: SESSION.user_id,
  owner_name: "Le Trưởng Nhóm",
  status: "THIET_KE",
  status_label: "Design · Chờ Lan phân công",
  state: "CHO_PHAN_CONG",
  current_person_name: "Lan",
  detail_path: "/tasks/MY-T-261012-01",
  version: 2,
  category: "BIRTHDAY",
  design: {
    script_link: null,
    script_by: null,
    reference_link: "https://drive.example.com/anh-my",
    note: "Tông hồng, có bánh kem",
    codes: [],
    proposed_codes: null,
    designer: "Quang Design",
    design_link: "https://drive.example.com/thiep-my",
  },
  birthday: {
    full_name: "Trần Diễm My",
    branch: "VP - Hà Nội",
    gender: "FEMALE",
    birthday_on: "1998-10-01",
  },
};

const OTHERS_ROW: TaskRow = {
  ...BIRTHDAY_ROW,
  id: "o-b2",
  code: "AN-T-261012-02",
  title: "Nguyễn Văn An",
  owner_user_id: "u-somebody-else",
  owner_name: "An Orderer",
  detail_path: "/tasks/AN-T-261012-02",
  design: { ...BIRTHDAY_ROW.design, reference_link: null, note: null, designer: null, design_link: null },
  birthday: {
    full_name: "Nguyễn Văn An",
    branch: "CS - Hà Nội 2",
    gender: "MALE",
    birthday_on: "1995-10-15",
  },
};

const birthdayPage = (
  me: unknown,
  rows: TaskRow[] = [BIRTHDAY_ROW, OTHERS_ROW],
  extra: Parameters<typeof stubFetch>[0] = [],
) => {
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
    { match: "/api/units/me", body: me },
    { match: "/api/orders/birthday/options", body: { branches: ["CS - Hà Nội 2", "VP - Hà Nội"] } },
    { match: "/api/board/counts", body: COUNTS },
    { match: "/api/board/tasks", body: PAGE(rows) },
  ]);
  pathname = "/tasks/birthday";
  renderWithQuery(<BirthdayOrdersPage />);
  return stub;
};

describe("the birthday board", () => {
  it("asks for ORD's birthday orders, nearest birthday first", async () => {
    const stub = birthdayPage(ME_AS("ORDERER", ORDERER));
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    const list = calls(stub).find((call) => call.url.includes("/api/board/tasks"))!;
    const asked = new URLSearchParams(list.url.split("?")[1]);
    expect(Object.fromEntries(asked)).toEqual({
      unit: "ADS",
      category: "BIRTHDAY",
      order: "birthday",
      limit: "10",
      offset: "0",
    });
    expect(calls(stub).find((call) => call.url.includes("/api/board/counts"))!.url).toBe(
      "/api/board/counts?unit=ADS&category=BIRTHDAY",
    );
    // No overview cards: the dashboard is never asked.
    expect(calls(stub).some((call) => call.url.includes("/api/board/dashboard"))).toBe(false);
  });

  it("draws who it is for, where it stands, what to work from and the hand-in", async () => {
    birthdayPage(ME_AS("ORDERER", ORDERER));
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    expect(headers()).toEqual([
      "Mã",
      "Chi nhánh",
      "Họ và tên",
      "Giới tính",
      "Ngày sinh",
      "Trạng thái · Đang giữ",
      "Deadline",
      "Nội dung",
      "Thiết kế",
      "Thao tác",
    ]);
    const [, first, second] = screen.getAllByRole("row");
    expect(within(first).getByRole("link", { name: "MY-T-261012-01" })).toHaveAttribute(
      "href",
      "/tasks/MY-T-261012-01",
    );
    expect(first).toHaveTextContent("VP - Hà Nội");
    expect(first).toHaveTextContent("Trần Diễm My");
    expect(first).toHaveTextContent("Nữ");
    expect(first).toHaveTextContent("01/10");
    expect(first).not.toHaveTextContent("1998");
    // The status badge, coloured as on /tasks (a waiting state is amber).
    expect(first.querySelector(".st")).toHaveClass("st-amber");
    expect(first.querySelector(".st")).toHaveTextContent("Design · Chờ Lan phân công");
    const content = within(first).getByTestId("birthday-content");
    expect(within(content).getByRole("link", { name: /Ảnh minh hoạ/ })).toHaveAttribute(
      "href",
      "https://drive.example.com/anh-my",
    );
    expect(content).toHaveTextContent("Tông hồng, có bánh kem");
    const designer = within(first).getByTestId("design-designer");
    expect(designer).toHaveTextContent("Quang Design");
    expect(within(designer).getByRole("link", { name: /Link thiết kế/ })).toHaveAttribute(
      "href",
      "https://drive.example.com/thiep-my",
    );
    expect(second).toHaveTextContent("Nam");
    expect(second).toHaveTextContent("15/10");
    expect(within(second).getByTestId("birthday-content")).toHaveTextContent("–");
  });

  it('counts and filters "Cần làm" on the URL', async () => {
    const stub = birthdayPage(ME_AS("ORDERER", ORDERER));
    const quick = await screen.findByRole("button", { name: "Cần làm (1)" });
    expect(quick).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(quick);
    expect(SEARCH.value.get("awaiting_me")).toBe("true");
    await waitFor(() =>
      expect(
        calls(stub).some(
          (call) => call.url.includes("/api/board/tasks") && call.url.includes("awaiting_me=true"),
        ),
      ).toBe(true),
    );
  });

  it('offers "Chỉnh sửa" on the viewer\'s own rows only', async () => {
    birthdayPage(ME_AS("ORDERER", ORDERER));
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    expect(screen.getByRole("button", { name: "Chỉnh sửa MY-T-261012-01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chỉnh sửa AN-T-261012-02" })).not.toBeInTheDocument();
  });

  it("offers it on every open row to whoever manages the codes, never on a finished one", async () => {
    birthdayPage(ME_AS("THIET_KE", DESIGN_LEAD), [
      BIRTHDAY_ROW,
      OTHERS_ROW,
      { ...OTHERS_ROW, id: "o-b3", code: "AN-T-261001-03", status: "COMPLETED", status_label: "Hoàn thành" },
    ]);
    await screen.findByRole("link", { name: "MY-T-261012-01" });
    expect(screen.getByRole("button", { name: "Chỉnh sửa MY-T-261012-01" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chỉnh sửa AN-T-261012-02" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chỉnh sửa AN-T-261001-03" })).not.toBeInTheDocument();
  });

  it("creates an order from the modal", async () => {
    const stub = birthdayPage(ME_AS("ORDERER", ORDERER), [], [
      {
        match: "/api/orders/birthday",
        method: "POST",
        status: 201,
        body: { id: "o-new", code: "MY-T-261012-09", version: 1 },
      },
    ]);
    await screen.findByText("Chưa có order sinh nhật nào.");
    await userEvent.click(screen.getByRole("button", { name: "+ Tạo order" }));
    const dialog = await screen.findByRole("dialog", { name: "Tạo order sinh nhật" });
    const submit = within(dialog).getByRole("button", { name: "Tạo order" });
    // Nothing is sent while a required field is empty.
    await userEvent.click(submit);
    expect(within(dialog).getByText("Nhập họ và tên.")).toBeInTheDocument();
    expect(within(dialog).getByText("Chọn ngày sinh.")).toBeInTheDocument();
    expect(calls(stub).some((call) => call.method === "POST")).toBe(false);

    await userEvent.type(within(dialog).getByLabelText("Họ và tên"), "Trần Diễm My");
    await userEvent.type(within(dialog).getByLabelText("Chi nhánh"), "VP - Hà Nội");
    await userEvent.selectOptions(within(dialog).getByLabelText("Giới tính"), "FEMALE");
    fireEvent.change(within(dialog).getByLabelText("Ngày sinh"), {
      target: { value: "1998-10-01" },
    });
    const picture = within(dialog).getByLabelText("Ảnh minh hoạ");
    expect(picture).toHaveAttribute("placeholder", "Gắn link drive vào đây");
    await userEvent.type(picture, "https://drive.example.com/anh-my");
    await userEvent.type(within(dialog).getByLabelText("Note yêu cầu"), "Tông hồng");
    await userEvent.click(submit);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const sent = calls(stub).find((call) => call.method === "POST")!;
    expect(sent.url).toBe("/api/orders/birthday");
    expect(sent.body).toEqual({
      full_name: "Trần Diễm My",
      branch: "VP - Hà Nội",
      gender: "FEMALE",
      birthday_on: "1998-10-01",
      reference_link: "https://drive.example.com/anh-my",
      note: "Tông hồng",
    });
  });

  it("edits a row from the same modal, with the row's version", async () => {
    const stub = birthdayPage(ME_AS("ORDERER", ORDERER), [BIRTHDAY_ROW], [
      {
        match: "/api/orders/o-b1/birthday",
        method: "PATCH",
        body: { id: "o-b1", code: "MY-T-261012-01", version: 3 },
      },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Chỉnh sửa MY-T-261012-01" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Chỉnh sửa order sinh nhật · MY-T-261012-01",
    });
    expect(within(dialog).getByLabelText("Họ và tên")).toHaveValue("Trần Diễm My");
    expect(within(dialog).getByLabelText("Chi nhánh")).toHaveValue("VP - Hà Nội");
    expect(within(dialog).getByLabelText("Giới tính")).toHaveValue("FEMALE");
    expect(within(dialog).getByLabelText("Ngày sinh")).toHaveValue("1998-10-01");
    expect(within(dialog).getByLabelText("Ảnh minh hoạ")).toHaveValue(
      "https://drive.example.com/anh-my",
    );
    expect(within(dialog).getByLabelText("Note yêu cầu")).toHaveValue("Tông hồng, có bánh kem");

    const branch = within(dialog).getByLabelText("Chi nhánh");
    await userEvent.clear(branch);
    await userEvent.type(branch, "CS - Hà Nội 2");
    await userEvent.clear(within(dialog).getByLabelText("Note yêu cầu"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Chỉnh sửa" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const sent = calls(stub).find((call) => call.method === "PATCH")!;
    expect(sent.url).toBe("/api/orders/o-b1/birthday");
    expect(sent.body).toEqual({
      version: 2,
      full_name: "Trần Diễm My",
      branch: "CS - Hà Nội 2",
      gender: "FEMALE",
      birthday_on: "1998-10-01",
      reference_link: "https://drive.example.com/anh-my",
      note: null,
    });
  });

  it("keeps the form open and says why when the server refuses", async () => {
    birthdayPage(ME_AS("ORDERER", ORDERER), [BIRTHDAY_ROW], [
      {
        match: "/api/orders/o-b1/birthday",
        method: "PATCH",
        status: 409,
        body: {
          error: {
            code: "order_stale_version",
            message: "Order vừa được người khác cập nhật.",
            details: { reason: "order_stale_version" },
          },
        },
      },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Chỉnh sửa MY-T-261012-01" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Chỉnh sửa" }));
    expect(
      await within(dialog).findByText("Order vừa được người khác cập nhật."),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Họ và tên")).toHaveValue("Trần Diễm My");
  });

  it("asks for nothing when the account may not open the board", async () => {
    const stub = birthdayPage(ME_AS("DUNG"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Bảng order sinh nhật dành cho ban Design",
    );
    expect(calls(stub).some((call) => call.url.includes("/api/board"))).toBe(false);
    expect(screen.queryByRole("button", { name: "+ Tạo order" })).not.toBeInTheDocument();
  });

  it("reads the gender codes and a birthday without a time zone", () => {
    expect(genderLabel("MALE")).toBe("Nam");
    expect(genderLabel("FEMALE")).toBe("Nữ");
    expect(genderLabel(null)).toBe("–");
    expect(formatBirthday("2026-10-01")).toBe("01/10");
    expect(formatBirthday("2026-01-31T00:00:00Z")).toBe("31/01");
    expect(formatBirthday(null)).toBe("–");
  });
});

// --- 6. The catalogue ----------------------------------------------------------

describe('"Bảng mã video"', () => {
  const cataloguePage = (me: unknown, extra: Parameters<typeof stubFetch>[0] = []) => {
    const stub = stubFetch([
      ...extra,
      { match: "/api/units/me", body: me },
      { match: "/api/units/ADS/design-codes", body: { codes: [TKBTS, OLD] } },
    ]);
    pathname = "/tasks/design-codes";
    renderWithQuery(<DesignCodesPage />);
    return stub;
  };

  it("lists every code, retired ones included", async () => {
    const stub = cataloguePage(ME_AS("THIET_KE", DESIGN_LEAD));
    expect(await screen.findByRole("textbox", { name: "Mã · TKBTS" })).toHaveValue("TKBTS");
    expect(screen.getByRole("textbox", { name: "Tên · OLD" })).toHaveValue("Mã đã ngừng");
    expect(screen.getByRole("checkbox", { name: "Đang dùng · TKBTS" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Đang dùng · OLD" })).not.toBeChecked();
    expect(calls(stub).find((call) => call.url.includes("design-codes"))!.url).toBe(
      "/api/units/ADS/design-codes?include_inactive=true",
    );
  });

  it("adds a code after asking", async () => {
    const stub = cataloguePage(ME_AS("THIET_KE", DESIGN_LEAD), [
      {
        match: "/api/units/ADS/design-codes",
        method: "POST",
        status: 201,
        body: { ...CA, sort_order: 4 },
      },
    ]);
    const add = await screen.findByRole("button", { name: "Thêm" });
    // A code and a name, or there is nothing to add.
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Mã video mới" }), "CA");
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Tên mã video mới" }), "Cover Album");
    await userEvent.click(add);
    expect(await screen.findByRole("dialog")).toHaveTextContent('Thêm mã video "CA"?');
    await confirm();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")!.body).toEqual({
      code: "CA",
      name: "Cover Album",
      active: true,
      sort_order: 4,
    });
  });

  it("renames and retires a code with only what changed", async () => {
    const stub = cataloguePage(ME_AS("THIET_KE", DESIGN_LEAD), [
      {
        match: `/api/units/ADS/design-codes/${TKBTS.id}`,
        method: "PATCH",
        body: { ...TKBTS, name: "SOCIAL - Banner", active: false },
      },
    ]);
    const name = await screen.findByRole("textbox", { name: "Tên · TKBTS" });
    const save = screen.getByRole("button", { name: "Lưu TKBTS" });
    expect(save).toBeDisabled();
    await userEvent.clear(name);
    await userEvent.type(name, "SOCIAL - Banner");
    await userEvent.click(screen.getByRole("checkbox", { name: "Đang dùng · TKBTS" }));
    await userEvent.click(save);
    expect(await screen.findByRole("dialog")).toHaveTextContent('Ngừng dùng mã "TKBTS"?');
    await confirm();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "PATCH")).toBe(true));
    const sent = calls(stub).find((call) => call.method === "PATCH")!;
    expect(sent.url).toBe(`/api/units/ADS/design-codes/${TKBTS.id}`);
    expect(sent.body).toEqual({ name: "SOCIAL - Banner", active: false });
  });

  it("is only a notice for somebody who may not manage it", async () => {
    const stub = cataloguePage(ME_AS("THIET_KE", DESIGNER));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Bảng mã video do Trưởng phòng Design quản lý",
    );
    expect(calls(stub).some((call) => call.url.includes("design-codes"))).toBe(false);
  });
});
