/**
 * The unified task screen (`/tasks/[ref]`): one detail page for a PR content
 * item and an Ads order alike.
 *
 * The page draws the server's lists - steps, people, fields, submissions,
 * timeline, actions - and posts an action's opaque key back with the version it
 * was shown. These tests pin that it draws what it is given (and only the
 * fields of the task's own unit), sends exactly `{key, version, ...inputs}`, and
 * puts every action - a DANGER one above all - behind a confirmation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskDetailPage from "@/app/tasks/[ref]/page";
import type { UnifiedTaskDetail } from "@/lib/api";
import {
  CONTENT,
  renderWithQuery,
  SESSION,
  stubFetch,
  VERSION,
} from "./helpers";

let currentRef = "TUAN-BTD-261007-01";

vi.mock("next/navigation", () => ({
  useParams: () => ({ ref: currentRef }),
  usePathname: () => `/tasks/${currentRef}`,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

beforeEach(() => {
  currentRef = "TUAN-BTD-261007-01";
});

const ADS_TASK_ID = "7c1d6f0e-1111-4c3b-9a3e-000000000001";
const ORDER_ID = "7c1d6f0e-2222-4c3b-9a3e-000000000002";
const PR_TASK_ID = "7c1d6f0e-3333-4c3b-9a3e-000000000003";
const CONTENT_ID = "7c1d6f0e-4444-4c3b-9a3e-000000000004";
const EDITOR_ID = "7c1d6f0e-5555-4c3b-9a3e-000000000005";
const DESIGNER_ID = "7c1d6f0e-6666-4c3b-9a3e-000000000006";

const ADS: UnifiedTaskDetail = {
  task: {
    id: ADS_TASK_ID,
    unit: "ADS",
    unit_label: "Luồng Order (ORD)",
    code: "TUAN-BTD-261007-01",
    title: "Video ra mắt serum",
    kind: "BTD",
    kind_label: "Video full diễn hoạt",
    phase: "PRODUCTION",
    phase_label: "Sản xuất",
    stage: "IN_PRODUCTION",
    stage_label: "Đang sản xuất",
    owner: { user_id: "u-owner", name: "Tuấn Marketing" },
    current_person: { user_id: EDITOR_ID, name: "Hiền Lương" },
    is_priority: true,
    urgent: true,
    created_at: "2026-10-07T02:00:00+00:00",
    updated_at: "2026-10-07T05:00:00+00:00",
    stage_since: "2026-10-07T04:00:00+00:00",
    finished_at: null,
    product_link: null,
    latest_link: "https://drive.example.com/ban-moi-nhat",
    revisions: 2,
    version: 4,
    source: { type: "ORDER", id: ORDER_ID },
  },
  steps: [
    {
      key: "ORDER",
      label: "Lên order",
      person_name: "Tuấn Marketing",
      status: "HOAN_THANH",
      status_label: "Đã gửi",
      is_current: false,
      since: "2026-10-07T02:00:00+00:00",
      revisions: 0,
    },
    {
      key: "BIEN_TAP",
      label: "Biên tập",
      person_name: "Hiền Lương",
      status: "DANG_SUA",
      status_label: "Đang sửa",
      is_current: true,
      since: "2026-10-07T04:00:00+00:00",
      revisions: 2,
    },
    {
      key: "DUNG",
      label: "Dựng",
      person_name: null,
      status: "CHUA_TOI",
      status_label: "Chưa tới",
      is_current: false,
      since: null,
      revisions: 0,
    },
  ],
  people: [
    { role_label: "Marketing", user_id: "u-owner", name: "Tuấn Marketing" },
    { role_label: "Biên tập", user_id: EDITOR_ID, name: "Hiền Lương" },
    { role_label: "Dựng", user_id: null, name: "Chưa giao" },
  ],
  fields: [
    {
      key: "content",
      label: "Nội dung",
      value: "Cảnh 1: mở hộp\nCảnh 2: thoa serum",
      type: "longtext",
      group: "common",
    },
    {
      key: "reference_link",
      label: "Link tham khảo",
      value: "https://example.com/tham-khao",
      type: "link",
      group: "common",
    },
    {
      key: "video_type",
      label: "Quy trình",
      value: "Biên kịch › Design › Dựng",
      type: "text",
      group: "ads",
    },
    {
      key: "video_kind",
      label: "Loại video",
      value: "Video full diễn hoạt",
      type: "text",
      group: "ads",
    },
    {
      key: "video_kind_points",
      label: "Điểm hiệu suất",
      value: "1",
      type: "text",
      group: "ads",
    },
    {
      key: "script_source",
      label: "Nguồn kịch bản",
      value: "AI",
      type: "text",
      group: "ads",
    },
  ],
  submissions: [
    {
      id: "s-1",
      label: "BT-1",
      step_label: "Biên tập",
      person_name: "Hiền Lương",
      link: "https://docs.example.com/kich-ban-1",
      text: null,
      note: "Bản đầu",
      submitted_at: "2026-10-07T03:00:00+00:00",
      status_label: "Bị trả",
    },
  ],
  timeline: [
    {
      at: "2026-10-07T04:00:00+00:00",
      actor_name: "Trưởng phòng ORD",
      label: "Trả sửa Biên tập",
      note: "Hook yếu",
    },
    {
      at: "2026-10-07T02:00:00+00:00",
      actor_name: "Tuấn Marketing",
      label: "Lên order",
      note: null,
    },
  ],
  actions: [
    {
      key: "ads:APPROVE_NODE:n-1",
      label: "Duyệt bài",
      emphasis: "PRIMARY",
      requires_note: false,
      inputs: [],
      assignee_options: [],
    },
    {
      key: "ads:RETURN_NODE:n-1",
      label: "Trả sửa",
      emphasis: "SECONDARY",
      requires_note: true,
      inputs: ["note"],
      assignee_options: [],
    },
    {
      key: "ads:ASSIGN:n-2",
      label: "Giao Dựng",
      emphasis: "SECONDARY",
      requires_note: false,
      inputs: ["assignee"],
      assignee_options: [{ user_id: DESIGNER_ID, name: "Minh Dựng" }],
    },
    {
      key: "ads:CANCEL",
      label: "Huỷ order",
      emphasis: "DANGER",
      requires_note: true,
      inputs: ["note"],
      assignee_options: [],
    },
  ],
};

const PR: UnifiedTaskDetail = {
  task: {
    ...ADS.task,
    id: PR_TASK_ID,
    unit: "PR",
    unit_label: "Luồng PR",
    code: "PR-000123",
    title: "Bài viết mùa thu",
    kind: "ARTICLE",
    kind_label: "Bài viết",
    phase: "REVIEW",
    phase_label: "Duyệt",
    stage: "TEAM_LEAD_REVIEW",
    stage_label: "Chờ Trưởng nhóm duyệt",
    is_priority: false,
    urgent: false,
    latest_link: null,
    revisions: 0,
    version: 9,
    source: { type: "PR_CONTENT", id: CONTENT_ID },
  },
  steps: [
    {
      key: "DRAFTING",
      label: "Viết",
      person_name: "Lan PR",
      status: "DONE",
      status_label: "Xong",
      is_current: false,
      since: null,
      revisions: 0,
    },
    {
      key: "TEAM_LEAD_REVIEW",
      label: "Trưởng nhóm duyệt",
      person_name: "Hà Lead",
      status: "CHO_DUYET",
      status_label: "Chờ duyệt",
      is_current: true,
      since: null,
      revisions: 0,
    },
  ],
  people: [{ role_label: "Phụ trách", user_id: "u-lan", name: "Lan PR" }],
  fields: [
    {
      key: "content",
      label: "Nội dung",
      value: "Mùa thu đến rồi",
      type: "longtext",
      group: "common",
    },
    {
      key: "deadline",
      label: "Hạn",
      value: "2026-10-20",
      type: "date",
      group: "common",
    },
    {
      key: "brand",
      label: "Thương hiệu",
      value: "Apexmed",
      type: "text",
      group: "pr",
    },
    {
      key: "channels",
      label: "Kênh dự kiến",
      value: "Facebook",
      type: "text",
      group: "pr",
    },
    // Never sent for a PR task; drawn here only to prove the page would not.
    {
      key: "video_type",
      label: "Loại video",
      value: "Video full diễn hoạt",
      type: "text",
      group: "ads",
    },
  ],
  submissions: [],
  timeline: [],
  actions: [
    {
      key: "pr:APPROVAL:APPROVED",
      label: "Duyệt",
      emphasis: "PRIMARY",
      requires_note: false,
      inputs: ["note"],
      assignee_options: [],
    },
  ],
};

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const posts = (stub: unknown) =>
  calls(stub).filter((call) => call.method === "POST");

describe("an Ads task", () => {
  it("draws the header, steps, Ads fields, submissions, timeline and people", async () => {
    stubFetch([{ match: `/api/tasks/${ADS.task.code}`, body: ADS }]);
    renderWithQuery(<TaskDetailPage />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Video ra mắt serum",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("TUAN-BTD-261007-01").length).toBeGreaterThan(0);
    // The chip reads ORD; the code (and the CSS class) stay ADS.
    expect(screen.getByText("ORD")).toHaveClass("unit-tag-ads");
    expect(screen.getByText("Gấp")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Bản nộp mới nhất" }),
    ).toHaveAttribute("href", "https://drive.example.com/ban-moi-nhat");

    const strip = screen.getByRole("region", { name: "Tiến trình" });
    expect(within(strip).getByText("Biên tập").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(within(strip).getByText("trả sửa 2 lần")).toBeInTheDocument();

    const info = screen
      .getByRole("heading", { name: "Thông tin" })
      .closest("section")!;
    expect(within(info).getByText("Loại video")).toBeInTheDocument();
    expect(within(info).getByText("Quy trình")).toBeInTheDocument();
    expect(within(info).getByText("Biên kịch › Design › Dựng")).toBeInTheDocument();
    expect(within(info).getByText("Điểm hiệu suất")).toBeInTheDocument();
    expect(within(info).getByText("Nguồn kịch bản")).toBeInTheDocument();
    expect(within(info).queryByText("Thương hiệu")).not.toBeInTheDocument();
    // Long text keeps its line breaks; a link field is a link.
    expect(within(info).getByText(/Cảnh 1: mở hộp/)).toHaveClass(
      "whitespace-pre-wrap",
    );
    expect(
      within(info).getByRole("link", { name: "https://example.com/tham-khao" }),
    ).toHaveAttribute("href", "https://example.com/tham-khao");

    expect(screen.getByText("BT-1")).toBeInTheDocument();
    expect(screen.getByText("Ghi chú: Bản đầu")).toBeInTheDocument();
    expect(screen.getByText(/Trả sửa Biên tập/)).toBeInTheDocument();
    const people = screen
      .getByRole("heading", { name: "Người tham gia" })
      .closest("section")!;
    expect(within(people).getByText("Chưa giao")).toBeInTheDocument();

    // No PR editors for an Ads task.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("posts a returning action with its key, the version and the mandatory note", async () => {
    const fresh: UnifiedTaskDetail = {
      ...ADS,
      task: { ...ADS.task, version: 5, stage_label: "Chờ biên tập sửa" },
      actions: [],
    };
    const stub = stubFetch([
      {
        match: `/api/tasks/${ADS_TASK_ID}/actions`,
        method: "POST",
        body: fresh,
      },
      { match: `/api/tasks/${ADS.task.code}`, body: ADS },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Trả sửa" }),
    );
    const dialog = await screen.findByRole("dialog");
    const confirmButton = within(dialog).getByRole("button", {
      name: "Trả sửa",
    });
    // The note is mandatory: nothing can be sent without it.
    expect(confirmButton).toBeDisabled();
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /bắt buộc/ }),
      "Hook yếu, viết lại",
    );
    expect(confirmButton).toBeEnabled();
    await userEvent.click(confirmButton);

    await waitFor(() => expect(posts(stub)).toHaveLength(1));
    expect(posts(stub)[0].url).toBe(`/api/tasks/${ADS_TASK_ID}/actions`);
    expect(posts(stub)[0].body).toEqual({
      key: "ads:RETURN_NODE:n-1",
      version: 4,
      note: "Hook yếu, viết lại",
    });
    // The response replaces what is on screen.
    expect(
      await screen.findByText(
        "Bạn không có thao tác nào trên task này lúc này.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Chờ biên tập sửa").length).toBeGreaterThan(0);
  });

  it("assigns a person chosen from the server's options", async () => {
    const stub = stubFetch([
      { match: `/api/tasks/${ADS_TASK_ID}/actions`, method: "POST", body: ADS },
      { match: `/api/tasks/${ADS.task.code}`, body: ADS },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Giao Dựng" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Giao Dựng" }),
    ).toBeDisabled();
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox"),
      DESIGNER_ID,
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Giao Dựng" }),
    );

    await waitFor(() =>
      expect(posts(stub)[0]?.body).toEqual({
        key: "ads:ASSIGN:n-2",
        version: 4,
        assignee_user_id: DESIGNER_ID,
      }),
    );
  });

  it("asks before a DANGER action and sends nothing when cancelled", async () => {
    const stub = stubFetch([
      { match: `/api/tasks/${ADS.task.code}`, body: ADS },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Huỷ order" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/Huỷ order · TUAN-BTD-261007-01\?/),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Thôi" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(posts(stub)).toHaveLength(0);
  });

  it("shows a stale-version refusal in place", async () => {
    const stub = stubFetch([
      {
        match: `/api/tasks/${ADS_TASK_ID}/actions`,
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "order_stale_version",
            message: "Order vừa được người khác cập nhật.",
            details: {},
          },
        },
      },
      { match: `/api/tasks/${ADS.task.code}`, body: ADS },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Duyệt bài" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Duyệt bài" }),
    );

    expect(
      (await screen.findAllByText("Order vừa được người khác cập nhật."))
        .length,
    ).toBeGreaterThan(0);
    // And the task is read again, so the next press carries the fresh version.
    await waitFor(() =>
      expect(
        calls(stub).filter(
          (call) => call.method === "GET" && call.url.includes(ADS.task.code),
        ).length,
      ).toBeGreaterThan(1),
    );
  });
});

describe("an Ads task handed out but not accepted", () => {
  const ASSIGNED: UnifiedTaskDetail = {
    ...ADS,
    task: {
      ...ADS.task,
      stage: "DUNG",
      stage_label: "Dựng · Đã giao Quỳnh Như",
      state: "DA_GIAO",
      current_person: { user_id: EDITOR_ID, name: "Quỳnh Như" },
    },
    steps: [
      {
        key: "BIEN_TAP",
        label: "Biên tập",
        person_name: "Hiền Lương",
        status: "HOAN_THANH",
        status_label: "Hoàn thành",
        is_current: false,
        since: "2026-10-07T03:00:00+00:00",
        revisions: 0,
      },
      {
        key: "THIET_KE",
        label: "Thiết kế",
        person_name: "Thảo Vy",
        status: "CHO_PHAN_CONG",
        status_label: "Chờ Thảo Vy phân công",
        is_current: false,
        since: null,
        revisions: 0,
      },
      {
        key: "DUNG",
        label: "Dựng",
        person_name: "Quỳnh Như",
        status: "DA_GIAO",
        status_label: "Đã giao Quỳnh Như",
        is_current: true,
        since: "2026-10-07T04:00:00+00:00",
        revisions: 0,
      },
      {
        key: "FINAL",
        label: "Duyệt final",
        person_name: "Tuấn Marketing",
        status: "CHO_DUYET",
        status_label: "Chờ Tuấn Marketing duyệt final",
        is_current: false,
        since: null,
        revisions: 0,
      },
    ],
    actions: [
      {
        key: "ads:SUBMIT_WORK:n-3",
        label: "Nộp sản phẩm · Dựng",
        emphasis: "PRIMARY",
        requires_note: false,
        inputs: ["link", "note"],
        assignee_options: [],
        required_inputs: ["link"],
      },
    ],
  };

  it("shows Đã giao and every waiting step in amber, with no Gắn link step", async () => {
    stubFetch([{ match: `/api/tasks/${ADS.task.code}`, body: ASSIGNED }]);
    renderWithQuery(<TaskDetailPage />);
    const strip = await screen.findByRole("region", { name: "Tiến trình" });
    expect(within(strip).queryByText(/Gắn link/)).not.toBeInTheDocument();
    expect(within(strip).getByText("Duyệt final")).toBeInTheDocument();
    const badge = (text: string) =>
      within(strip).getByText(text).closest(".st") as HTMLElement;
    expect(badge("Đã giao Quỳnh Như")).toHaveClass("st-amber");
    expect(badge("Chờ Thảo Vy phân công")).toHaveClass("st-amber");
    expect(badge("Chờ Tuấn Marketing duyệt final")).toHaveClass("st-amber");
    expect(badge("Hoàn thành")).toHaveClass("st-green");
    // The header says who it was handed to, in amber.
    const header = screen.getAllByText("Dựng · Đã giao Quỳnh Như")[0];
    expect(header).toHaveClass("bg-amber-500/15");
  });

  it("will not send the last node's hand-in without the product link", async () => {
    const stub = stubFetch([
      { match: `/api/tasks/${ADS_TASK_ID}/actions`, method: "POST", body: ASSIGNED },
      { match: `/api/tasks/${ADS.task.code}`, body: ASSIGNED },
    ]);
    renderWithQuery(<TaskDetailPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Nộp sản phẩm · Dựng" }),
    );
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Nộp sản phẩm · Dựng" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      within(dialog).getByLabelText("Link sản phẩm (bắt buộc)"),
      "https://drive.example.com/final.mp4",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(posts(stub)[0]?.body).toEqual({
        key: "ads:SUBMIT_WORK:n-3",
        version: 4,
        link: "https://drive.example.com/final.mp4",
      }),
    );
  });
});

describe("a PR task", () => {
  it("draws the PR fields only, and offers the PR editors as tabs", async () => {
    currentRef = CONTENT_ID;
    stubFetch([{ match: `/api/tasks/${CONTENT_ID}`, body: PR }]);
    renderWithQuery(<TaskDetailPage />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Bài viết mùa thu",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("PR")).toHaveClass("unit-tag-pr");

    const info = screen
      .getByRole("heading", { name: "Thông tin" })
      .closest("section")!;
    expect(within(info).getByText("Thương hiệu")).toBeInTheDocument();
    expect(within(info).getByText("Apexmed")).toBeInTheDocument();
    expect(within(info).getByText("Kênh dự kiến")).toBeInTheDocument();
    expect(within(info).getByText("Hạn")).toBeInTheDocument();
    expect(within(info).queryByText("Loại video")).not.toBeInTheDocument();
    expect(within(info).queryByText("Nguồn kịch bản")).not.toBeInTheDocument();

    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Tổng quan",
      "Nội dung & phiên bản",
      "AI review & tài liệu",
      "Sản phẩm",
      "Xuất bản",
      "Bình luận",
    ]);
  });

  it("posts a PR action with an optional note through the same endpoint", async () => {
    currentRef = CONTENT_ID;
    const stub = stubFetch([
      { match: `/api/tasks/${PR_TASK_ID}/actions`, method: "POST", body: PR },
      { match: `/api/tasks/${CONTENT_ID}`, body: PR },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Duyệt" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Ghi chú" }),
      "Ổn",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Duyệt" }),
    );

    await waitFor(() =>
      expect(posts(stub)[0]?.body).toEqual({
        key: "pr:APPROVAL:APPROVED",
        version: 9,
        note: "Ổn",
      }),
    );
  });

  it("opens the PR module's own editors on the content id", async () => {
    currentRef = PR.task.code;
    const content = {
      ...CONTENT,
      id: CONTENT_ID,
      production_state: null,
      producer_user_id: null,
    };
    const stub = stubFetch([
      { match: `/api/tasks/${PR.task.code}`, body: PR },
      {
        match: "/available-actions",
        body: {
          content_id: CONTENT_ID,
          workflow_stage: content.workflow_stage,
          available_actions: [
            {
              action: "ADD_CONTENT_COMMENT",
              target_stage: null,
              decision: null,
              emphasis: "SECONDARY",
              undo_kind: null,
            },
          ],
        },
      },
      {
        match: "/review-context",
        body: {
          content,
          current_version: VERSION,
          targets: [],
          tasks: [],
          ai_review: null,
          ai_reviews_for_version: [],
          approvals: [],
        },
      },
      { match: "/versions", body: [VERSION] },
      { match: "/history", body: [] },
      {
        match: "/api/pr/people",
        body: [
          {
            user_id: SESSION.user_id,
            full_name: SESSION.full_name,
            role: SESSION.role,
          },
        ],
      },
      {
        match: "/comments",
        body: {
          content_id: CONTENT_ID,
          items: [
            {
              id: "c-1",
              content_id: CONTENT_ID,
              parent_comment_id: null,
              author_user_id: SESSION.user_id,
              author_name: SESSION.full_name,
              body: "Đoạn mở đầu hơi dài.",
              created_at: "2026-10-07T03:00:00+00:00",
              edited_at: null,
              is_deleted: false,
              can_edit: false,
              can_delete: false,
              replies: [],
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        },
      },
      {
        match: `/api/pr/contents/${CONTENT_ID}`,
        body: { content, current_version: VERSION, targets: [], brand: null },
      },
    ]);
    renderWithQuery(<TaskDetailPage />);

    await userEvent.click(
      await screen.findByRole("tab", { name: "Bình luận" }),
    );
    expect(await screen.findByText("Đoạn mở đầu hơi dài.")).toBeInTheDocument();
    // Every PR call is addressed by the content id the task extends.
    const prCalls = calls(stub).filter((call) =>
      call.url.startsWith("/api/pr/contents/"),
    );
    expect(prCalls.length).toBeGreaterThan(0);
    expect(
      prCalls.every((call) =>
        call.url.startsWith(`/api/pr/contents/${CONTENT_ID}`),
      ),
    ).toBe(true);

    await userEvent.click(
      screen.getByRole("tab", { name: "Nội dung & phiên bản" }),
    );
    expect(
      await screen.findByText(/Kịch bản hiện tại \(v/),
    ).toBeInTheDocument();
    // The workflow buttons stay in one place: the task's own action panel.
    expect(screen.getByRole("button", { name: "Duyệt" })).toBeInTheDocument();
  });
});
