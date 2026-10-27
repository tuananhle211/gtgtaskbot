/**
 * Task codes with points on every step (0074, docs/perf/TASK_CODES_PLAN.md):
 *
 * - "Danh mục điểm": read by the whole order flow, a step's codes kept by its
 *   Trưởng phòng (`design.task_phases`), the forms by the OWNER / ADMIN;
 * - the picker: one code for Dựng, several for Design and Biên kịch, a form per line with its rule
 *   under it, "Việc khác" with its work and points, the line and total points;
 * - the task page: each step's codes and points, a different hand-in waiting
 *   with how it differs;
 * - "Không duyệt" names one of three reasons; a hand-in sent back for its codes
 *   is sent again with the same link.
 */

import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskDetailPage from "@/app/tasks/[ref]/page";
import { TaskCodesCatalogue } from "@/components/task-codes";
import {
  DesignCodesPicker,
  designCodesValid,
  toDesignCodePicks,
} from "@/components/design-codes-picker";
import type { DesignCode, DesignCodeItem, TaskForm, UnifiedTaskDetail } from "@/lib/api";
import { confirm, renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: "K7Q2M" }),
  usePathname: () => "/tasks/K7Q2M",
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
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const FORMS: TaskForm[] = [
  { key: "NEW", label: "Làm mới", pct: 100, note: "Mặc định cho mọi việc.", phases: ["BIEN_TAP", "THIET_KE", "DUNG"], active: true },
  { key: "ADAPT", label: "Adapt / sửa từ mẫu có sẵn", pct: 50, note: "Thiết kế, video dùng lại khung có sẵn.", phases: ["THIET_KE", "DUNG"], active: true },
  { key: "RESIZE", label: "Resize / xuất thêm kích thước", pct: 20, note: "Chỉ đổi kích thước, chỉnh bố cục nhẹ.", phases: ["THIET_KE", "DUNG"], active: true },
  { key: "AFTER_EFFECTS", label: "Video có After Effects", pct: 150, note: "Chỉ dùng cho D1-D7.", phases: ["DUNG"], active: true },
];

const code = (over: Partial<DesignCode> & { id: string; code: string }): DesignCode => ({
  phase: "DUNG",
  name: over.code,
  title: null,
  points: 0,
  unit_label: null,
  examples: null,
  note: null,
  free_points: false,
  active: true,
  sort_order: 1,
  ...over,
});

const D3 = code({ id: "c-d3", code: "D3", name: "Video trên 90 giây đến 3 phút", title: "Video trên 90 giây đến 3 phút", points: 240, unit_label: "video", examples: "Hỏi đáp bác sĩ", sort_order: 3 });
const D4 = code({ id: "c-d4", code: "D4", name: "Video trên 3 đến 5 phút", title: "Video trên 3 đến 5 phút", points: 480, unit_label: "video", sort_order: 4 });
const D0 = code({ id: "c-d0", code: "D0", name: "Việc khác", title: "Việc khác", free_points: true, sort_order: 9 });
const T1 = code({ id: "c-t1", phase: "THIET_KE", code: "T1", name: "Ảnh post, banner, thumbnail", title: null, examples: "Ảnh post, banner, thumbnail", points: 90, unit_label: "mẫu" });

const ADS_ME = (design: Record<string, unknown>) => ({
  units: [
    {
      code: "ADS",
      label: "Luồng Order (ORD)",
      role: "DUNG",
      role_label: "Dựng",
      is_lead: false,
      member_code: "MY",
      personal_nas_url: null,
      settings: { urgent_days: 7, telegram_enabled: false, effort_enabled: false },
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  effort_enabled: false,
  design: { view: false, manage_codes: false, birthday: false, ...design },
});

// --- 1. "Danh mục điểm" --------------------------------------------------------------

describe('"Danh mục điểm"', () => {
  const page = (design: Record<string, unknown>, extra: Parameters<typeof stubFetch>[0] = []) => {
    const stub = stubFetch([
      ...extra,
      { match: "/api/units/me", body: ADS_ME(design) },
      { match: "/api/units/ADS/task-forms", method: "GET", body: { forms: FORMS } },
      { match: "/api/units/ADS/task-codes?phase=DUNG", body: { codes: [D3, D4, D0], forms: FORMS } },
      { match: "/api/units/ADS/task-codes?phase=THIET_KE", body: { codes: [T1], forms: FORMS } },
      { match: "/api/units/ADS/task-codes?phase=BIEN_TAP", body: { codes: [], forms: FORMS } },
    ]);
    return stub;
  };

  it("lets anybody in the order flow read a step's codes and points", async () => {
    const stub = page({});
    renderWithQuery(<TaskCodesCatalogue initial="DUNG" />);
    const rows = await screen.findAllByTestId("task-code-row");
    expect(rows.map((row) => row.textContent)).toEqual([
      "D3Video trên 90 giây đến 3 phútHỏi đáp bác sĩvideo240",
      "D4Video trên 3 đến 5 phút—video480",
      "Việc khác———tự điền",
    ]);
    // The code carries its details as a tooltip.
    expect(within(rows[0]).getByText("D3")).toHaveAttribute(
      "title",
      expect.stringContaining("Điểm chuẩn: 240 / video"),
    );
    // Read only: no inputs, no retired rows asked for - the OWNER alone edits.
    expect(screen.queryByRole("button", { name: "Thêm" })).not.toBeInTheDocument();
    expect(screen.getByText(/Chỉ chủ sở hữu \(OWNER\) sửa được danh mục điểm/)).toBeInTheDocument();
    expect(calls(stub).some((call) => call.url.includes("include_inactive=true"))).toBe(false);
    // Design has no "Tên" column: the examples name the code.
    await userEvent.click(screen.getByRole("tab", { name: "Design" }));
    const design = await screen.findByRole("region", { name: "Danh mục điểm Design" });
    expect(within(design).queryByRole("columnheader", { name: "Tên" })).not.toBeInTheDocument();
    expect(within(design).getByText("Ảnh post, banner, thumbnail")).toBeInTheDocument();
  });

  it("lets the OWNER add a code with its points", async () => {
    const stub = page({ task_phases: ["BIEN_TAP", "THIET_KE", "DUNG"], manage_forms: true }, [
      { match: "/api/units/ADS/task-codes", method: "POST", status: 201, body: { ...D4, id: "c-d5", code: "D5" } },
    ]);
    renderWithQuery(<TaskCodesCatalogue initial="DUNG" />);
    expect(await screen.findByRole("textbox", { name: "Mã · D3" })).toHaveValue("D3");
    expect(calls(stub).some((call) => call.url.includes("phase=DUNG&include_inactive=true"))).toBe(true);
    const add = screen.getByRole("button", { name: "Thêm" });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Mã · mã mới" }), "D5");
    await userEvent.type(screen.getByRole("textbox", { name: "Tên · mã mới" }), "Video trên 5 đến 15 phút");
    const points = screen.getByRole("spinbutton", { name: "Điểm · mã mới" });
    await userEvent.clear(points);
    await userEvent.type(points, "480");
    await userEvent.click(add);
    expect(await screen.findByRole("dialog")).toHaveTextContent('Thêm mã "D5" cho Dựng?');
    await confirm();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "POST")).toBe(true));
    expect(calls(stub).find((call) => call.method === "POST")!.body).toMatchObject({
      phase: "DUNG",
      code: "D5",
      name: "Video trên 5 đến 15 phút",
      points: 480,
    });
  });

  it("shows the forms, and lets the OWNER / ADMIN change their %", async () => {
    const stub = page({ manage_forms: true }, [
      { match: "/api/units/ADS/task-forms", method: "PUT", body: { forms: FORMS } },
    ]);
    renderWithQuery(<TaskCodesCatalogue initial="FORMS" />);
    const rows = await screen.findAllByTestId("task-form-row");
    expect(rows).toHaveLength(4);
    // "Làm mới" stays fixed at 100%.
    expect(within(rows[0]).queryByRole("spinbutton")).not.toBeInTheDocument();
    const adapt = screen.getByRole("spinbutton", { name: "% điểm · ADAPT" });
    await userEvent.clear(adapt);
    await userEvent.type(adapt, "40");
    await userEvent.click(screen.getByRole("button", { name: "Lưu hình thức" }));
    await confirm();
    await waitFor(() => expect(calls(stub).some((call) => call.method === "PUT")).toBe(true));
    const sent = calls(stub).find((call) => call.method === "PUT")!.body as { forms: TaskForm[] };
    expect(sent.forms.find((form) => form.key === "ADAPT")?.pct).toBe(40);
  });
});

// --- 2. The picker ---------------------------------------------------------------------

function Harness({
  phase,
  options,
  initial = [],
}: {
  phase: "BIEN_TAP" | "THIET_KE" | "DUNG";
  options: DesignCode[];
  initial?: DesignCodeItem[];
}) {
  const [value, setValue] = useState<DesignCodeItem[]>(initial);
  return (
    <>
      <DesignCodesPicker value={value} onChange={setValue} options={options} forms={FORMS} phase={phase} />
      <output data-testid="picks">{JSON.stringify(toDesignCodePicks(value))}</output>
      <output data-testid="valid">{String(designCodesValid(value, false, phase))}</output>
    </>
  );
}

const picks = () => JSON.parse(screen.getByTestId("picks").textContent ?? "[]");
const valid = () => screen.getByTestId("valid").textContent;

describe("the task-code picker", () => {
  it("takes one code for Dựng: picking another swaps it", async () => {
    renderWithQuery(<Harness phase="DUNG" options={[D3, D4, D0]} />);
    const add = screen.getByRole("combobox", { name: "Mã task công việc Dựng" });
    expect(within(add).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "+ Thêm mã task",
      "D3 · Video trên 90 giây đến 3 phút · 240đ",
      "D4 · Video trên 3 đến 5 phút · 480đ",
      "Việc khác (tự ghi việc và điểm)",
    ]);
    await userEvent.selectOptions(add, D3.id);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Mã task công việc Dựng" }), D4.id);
    expect(screen.getAllByTestId("design-code-item")).toHaveLength(1);
    // Its own colour block, a bold title, a red "Bỏ" (2026-10-23).
    const group = screen.getByRole("group", { name: "Mã task công việc Dựng" });
    expect(group).toHaveAttribute("data-phase", "DUNG");
    expect(group.querySelector(".bg-\\[var\\(--good\\)\\]")).not.toBeNull();
    expect(within(group).getByText("Mã task công việc Dựng")).toHaveClass("font-bold");
    expect(screen.getByRole("button", { name: "Bỏ mã D4" }).className).toContain(
      "text-[var(--bad)]",
    );
    // No quantity for a single-code step.
    expect(screen.queryByRole("spinbutton", { name: /Số lượng/ })).not.toBeInTheDocument();
    expect(picks()).toEqual([{ code_id: D4.id, quantity: 1 }]);
  });

  it("offers the step's forms with their rule, and counts the points", async () => {
    renderWithQuery(<Harness phase="DUNG" options={[D4]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Mã task công việc Dựng" }), D4.id);
    // The forms are one-tap pills, the chosen one checked.
    const form = screen.getByRole("radiogroup", { name: "Hình thức · D4" });
    expect(within(form).getAllByRole("radio").map((option) => option.textContent)).toEqual([
      "Làm mới 100%",
      "Adapt / sửa từ mẫu có sẵn 50%",
      "Resize / xuất thêm kích thước 20%",
      "Video có After Effects 150%",
    ]);
    expect(within(form).getByRole("radio", { name: /Làm mới/ })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(form).getByRole("radio", { name: /After Effects/ }));
    expect(within(form).getByRole("radio", { name: /After Effects/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("form-note")).toHaveTextContent("Chỉ dùng cho D1-D7.");
    expect(screen.getByTestId("design-code-points")).toHaveTextContent("720 điểm");
    expect(picks()).toEqual([{ code_id: D4.id, quantity: 1, form: "AFTER_EFFECTS" }]);
  });

  it("lets Biên kịch take several codes, each with a quantity (2026-10-27)", async () => {
    const n3 = code({ id: "c-n3", phase: "BIEN_TAP", code: "N3", name: "Kịch bản", title: "Kịch bản", points: 120 });
    const n1 = code({ id: "c-n1", phase: "BIEN_TAP", code: "N1", name: "Caption", title: "Caption", points: 15 });
    renderWithQuery(<Harness phase="BIEN_TAP" options={[n3, n1]} />);
    const add = () => screen.getByRole("combobox", { name: "Mã task công việc Biên kịch" });
    await userEvent.selectOptions(add(), n3.id);
    // N3 is on the list: only N1 is left to add (one form, no twice).
    expect(within(add()).queryByRole("option", { name: /^N3/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(add(), n1.id);
    expect(screen.getAllByTestId("design-code-item")).toHaveLength(2);
    const quantity = screen.getByRole("spinbutton", { name: "Số lượng · N1" });
    await userEvent.clear(quantity);
    await userEvent.type(quantity, "4");
    expect(valid()).toBe("true");
    // 120 + 15 x 4 = 180.
    expect(screen.getByTestId("design-codes-total")).toHaveTextContent("180 điểm");
    expect(picks()).toEqual([
      { code_id: n3.id, quantity: 1 },
      { code_id: n1.id, quantity: 4 },
    ]);
  });

  it("gives Biên kịch no form to choose", async () => {
    const n3 = code({ id: "c-n3", phase: "BIEN_TAP", code: "N3", name: "Kịch bản", title: "Kịch bản", points: 120 });
    renderWithQuery(<Harness phase="BIEN_TAP" options={[n3]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Mã task công việc Biên kịch" }), n3.id);
    expect(screen.queryByRole("combobox", { name: /Hình thức/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("design-codes-total")).toHaveTextContent("120 điểm");
  });

  it('asks a "Việc khác" line for its work and points', async () => {
    renderWithQuery(<Harness phase="DUNG" options={[D0]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Mã task công việc Dựng" }), D0.id);
    expect(valid()).toBe("false");
    await userEvent.type(screen.getByRole("textbox", { name: "Việc đã làm · D0" }), "Tách nền 20 ảnh");
    expect(valid()).toBe("false");
    await userEvent.type(screen.getByRole("spinbutton", { name: "Số điểm · D0" }), "90");
    expect(valid()).toBe("true");
    expect(picks()).toEqual([{ code_id: D0.id, quantity: 1, note: "Tách nền 20 ảnh", points: 90 }]);
    expect(screen.getByTestId("design-codes-total")).toHaveTextContent("90 điểm");
  });

  it("lets Design take a code twice in two forms, each with a quantity", async () => {
    renderWithQuery(<Harness phase="THIET_KE" options={[T1]} />);
    const add = () => screen.getByRole("combobox", { name: "Mã task công việc Design" });
    await userEvent.selectOptions(add(), T1.id);
    await userEvent.selectOptions(add(), T1.id);
    const forms = screen.getAllByRole("radiogroup", { name: "Hình thức · T1" });
    await userEvent.click(within(forms[1]).getByRole("radio", { name: /Resize/ }));
    const quantities = screen.getAllByRole("spinbutton", { name: "Số lượng · T1" });
    await userEvent.clear(quantities[1]);
    await userEvent.type(quantities[1], "6");
    expect(valid()).toBe("true");
    // 90 + 90 x 20% x 6 = 198.
    expect(screen.getByTestId("design-codes-total")).toHaveTextContent("198 điểm");
  });
});

// --- 3. The task page ---------------------------------------------------------------------

const TASK_ID = "7c1d6f0e-cccc-4c3b-9a3e-0000000000d1";

const item = (over: Partial<DesignCodeItem> & { code_id: string; code: string }): DesignCodeItem => ({
  name: over.code,
  quantity: 1,
  form: "NEW",
  form_label: "Làm mới",
  form_pct: 100,
  unit_points: 0,
  points: 0,
  note: null,
  free: false,
  ...over,
});

const DETAIL: UnifiedTaskDetail = {
  task: {
    id: TASK_ID,
    unit: "ADS",
    unit_label: "Luồng Order (ORD)",
    code: "K7Q2M",
    title: "Video quy trình",
    kind: "BD",
    kind_label: "Biên kịch › Dựng",
    phase: "PRODUCTION",
    phase_label: "Sản xuất",
    stage: "DUNG",
    stage_label: "Dựng · Chờ duyệt",
    state: "CHO_DUYET",
    owner: { user_id: "u-owner", name: "Tuấn Marketing" },
    current_person: { user_id: SESSION.user_id, name: "Lan Dựng" },
    is_priority: false,
    urgent: false,
    created_at: "2026-10-22T02:00:00+00:00",
    updated_at: "2026-10-22T03:00:00+00:00",
    stage_since: "2026-10-22T03:00:00+00:00",
    finished_at: null,
    product_link: null,
    latest_link: "https://e/v1",
    revisions: 0,
    version: 7,
    source: { type: "ORDER", id: "o-1" },
  },
  steps: [],
  people: [],
  fields: [],
  submissions: [],
  timeline: [],
  actions: [],
  category: "STANDARD",
  design: null,
  birthday: null,
  task_codes: [
    {
      phase: "BIEN_TAP",
      label: "Biên kịch",
      suggested: [],
      suggested_points: 0,
      current: [item({ code_id: "c-n3", code: "N3", name: "Kịch bản", unit_points: 120, points: 120 })],
      current_points: 120,
      proposed: null,
      people: [{ user_id: "u-w", name: "Hà Biên Kịch", codes: [], points: 120, earned: true }],
    },
    {
      phase: "DUNG",
      label: "Dựng",
      suggested: [item({ code_id: D3.id, code: "D3", name: D3.name, unit_points: 240, points: 240 })],
      suggested_points: 240,
      current: [item({ code_id: D3.id, code: "D3", name: D3.name, unit_points: 240, points: 240 })],
      current_points: 240,
      proposed: [
        item({
          code_id: D4.id,
          code: "D4",
          name: D4.name,
          form: "AFTER_EFFECTS",
          form_label: "Video có After Effects",
          form_pct: 150,
          unit_points: 480,
          points: 720,
        }),
      ],
      proposed_points: 720,
      change: "Đề xuất: D3 (240đ)\nNộp: D4 · Video có After Effects (720đ)\nChênh: +480 điểm",
      people: [{ user_id: "u-e", name: "Lan Dựng", codes: [], points: 720, earned: false }],
    },
  ],
};

const taskPage = (detail: UnifiedTaskDetail) => {
  const stub = stubFetch([
    { match: "/api/units/me", body: ADS_ME({ task_phases: ["DUNG"] }) },
    { match: "/api/units/ADS/task-codes", body: { codes: [D3, D4, D0], forms: FORMS } },
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

describe("task codes on the task page", () => {
  it("shows each step's codes and points, and a hand-in waiting with how it differs", async () => {
    taskPage(DETAIL);
    const panel = await screen.findByRole("region", { name: "Mã task công việc" });
    const bt = within(panel).getByTestId("task-codes-BIEN_TAP");
    expect(bt).toHaveTextContent("120 điểm");
    expect(within(bt).getByText("đã tính")).toBeInTheDocument();
    const proposed = within(panel).getByTestId("task-codes-proposed");
    expect(proposed).toHaveTextContent("chờ Trưởng phòng duyệt (720 điểm)");
    expect(proposed).toHaveTextContent("Chênh: +480 điểm");
    expect(proposed).toHaveTextContent("Video có After Effects");
  });

  it("approves after showing how the codes differ", async () => {
    const stub = taskPage({
      ...DETAIL,
      actions: [
        {
          key: "ads:APPROVE_NODE:n-d",
          label: "Duyệt · Dựng",
          emphasis: "PRIMARY",
          requires_note: false,
          inputs: ["note"],
          required_inputs: [],
          assignee_options: [],
          hint: "Nộp khác mã task đề xuất.\nĐề xuất: D3 (240đ)\nNộp: D4 · Video có After Effects (720đ)\nChênh: +480 điểm",
        },
      ],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Duyệt · Dựng" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("action-hint")).toHaveTextContent("Chênh: +480 điểm");
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toEqual({ key: "ads:APPROVE_NODE:n-d", version: 7 }));
  });

  it('asks "Không duyệt" for one of three reasons', async () => {
    const stub = taskPage({
      ...DETAIL,
      actions: [
        {
          key: "ads:RETURN_NODE:n-d",
          label: "Trả sửa · Dựng",
          emphasis: "DANGER",
          requires_note: true,
          inputs: ["return_reason", "note", "deadline"],
          required_inputs: ["return_reason"],
          assignee_options: [],
          plan_mode: "REVISION",
          defaults: {},
        },
      ],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Trả sửa · Dựng" }));
    const dialog = await screen.findByRole("dialog");
    const reasons = within(dialog).getByRole("group", { name: "Lý do không duyệt (bắt buộc)" });
    expect(within(reasons).getAllByRole("radio").map((radio) => (radio as HTMLInputElement).value)).toEqual([
      "WORKER_FAULT",
      "CHANGED_REQUEST",
      "WRONG_CODES",
    ]);
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Lý do \/ ghi chú/ }), "Sai mã");
    expect(accept()).toBeDisabled();
    await userEvent.click(within(reasons).getByRole("radio", { name: /Mã công việc chưa hợp lý/ }));
    expect(accept()).toBeEnabled();
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toBeDefined());
    expect(actionBody(stub)).toMatchObject({
      key: "ads:RETURN_NODE:n-d",
      note: "Sai mã",
      return_reason: "WRONG_CODES",
    });
  });

  it("hands Dựng in with its own code, the last link already filled in", async () => {
    const stub = taskPage({
      ...DETAIL,
      actions: [
        {
          key: "ads:SUBMIT_WORK:n-d",
          label: "Nộp sản phẩm · Dựng",
          emphasis: "PRIMARY",
          requires_note: false,
          inputs: ["link", "note", "task_codes"],
          required_inputs: ["link"],
          assignee_options: [],
          task_phase: "DUNG",
          task_codes: [item({ code_id: D3.id, code: "D3", name: D3.name, unit_points: 240, points: 240 })],
          link_default: "https://e/v1",
        },
      ],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Nộp sản phẩm · Dựng" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("textbox", { name: "Link sản phẩm (bắt buộc)" })).toHaveValue(
      "https://e/v1",
    );
    // Starts from the suggestion; the editor swaps it for D4.
    expect(within(dialog).getByTestId("design-code-item")).toHaveTextContent("D3");
    const picker = within(dialog).getByRole("combobox", { name: "Mã task công việc Dựng (bắt buộc)" });
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, D4.id);
    await confirm();
    await waitFor(() => expect(actionBody(stub)).toBeDefined());
    expect(actionBody(stub)).toEqual({
      key: "ads:SUBMIT_WORK:n-d",
      version: 7,
      link: "https://e/v1",
      task_codes: [{ code_id: D4.id, quantity: 1 }],
    });
  });
});
