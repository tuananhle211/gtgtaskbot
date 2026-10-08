/**
 * Step 1F.2.3f - the complete content record: outputs, destinations, publications.
 *
 * Numbered 101-140, continuing the frontend numbering `work-queue.test.tsx` left
 * at 100.
 *
 * What these assert is narrow on purpose, and it is the same discipline the rest
 * of this suite keeps: **the browser decides nothing.** Every control here is
 * drawn because `/available-actions` named it, every file is rendered as a link
 * or as text because the server's `is_link` said which, and the label under
 * "Sản phẩm" on a publication row comes from joining two lists the page already
 * loaded rather than from a copy travelling on the publication. So most of the
 * load-bearing assertions are of the shape "given this server answer, this
 * appears", and its mirror - because a panel that renders a control from a role
 * string looks identical on screen to one that asks.
 *
 * The last section is a lane regression. Step 1F.2.3c2's board is authoritative
 * and this step must not touch it, so 139-140 assert that no board request grew
 * a derivative, a publication or a destination.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import { DERIVATIVE_TYPE_ORDER, derivativeTypeLabel } from "@/lib/labels";
import {
  CONTENT,
  SESSION,
  VERSION,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
  PR_CONTENT_DETAIL_FILES,
  readPrContentDetailSource,
} from "./helpers";

const ROOT = path.resolve(__dirname, "..");
const read = (relative: string) =>
  readFileSync(path.join(ROOT, "src", relative), "utf8");

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const { default: ContentDetailPage } =
  await import("@/app/pr/content/[id]/page");

const PRODUCER = {
  user_id: "88888888-8888-8888-8888-888888888888",
  full_name: "Phương Nhung",
  role: "EMPLOYEE",
};
const PEOPLE = [
  {
    user_id: SESSION.user_id,
    full_name: SESSION.full_name,
    role: SESSION.role,
  },
  PRODUCER,
];

const DRIVE = "https://drive.google.com/file/d/1MasterSixtySeconds/view";
const CUT = "https://drive.google.com/file/d/1CutDownTwentyFive/view";
const NAS = "/volume1/PR/2026/reel-30s.mp4";
const POST_URL = "https://www.tiktok.com/@apexmed/video/7300000000000000000";
const LANDING = "https://apexmed.vn/dich-vu/nang-mui";

/** The 60-second master an internal reviewer approved. */
const MASTER = {
  id: "99999999-9999-9999-9999-999999999999",
  content_id: CONTENT.id,
  submission_no: 1,
  artifact_type: "DRIVE_LINK",
  location: DRIVE,
  is_link: true,
  label: "Video final 60s",
  note: "Bản đã duyệt nội bộ",
  producer_user_id: PRODUCER.user_id,
  submitted_by_user_id: PRODUCER.user_id,
  content_version_id: VERSION.id,
  created_at: "2026-08-10T03:00:00+00:00",
  // Step 1F.2.3f.2: the server's per-row answer to "may this session fix where
  // this file lives".
  can_correct: false,
};

/** The October cutdown, made for a channel that did not exist in August. */
const CUTDOWN = {
  id: "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
  content_id: CONTENT.id,
  derivative_type: "CUTDOWN",
  label: "TikTok cut 25s",
  location: CUT,
  is_link: true,
  source_submission_id: MASTER.id,
  note: "Cắt cho kênh mới",
  created_by_user_id: PRODUCER.user_id,
  // Step 1F.2.3g: the server joins the name and answers the two per-row
  // questions, so nothing about this row is worked out in the browser.
  created_by_name: PRODUCER.full_name,
  can_edit: false,
  can_delete: false,
  is_published_output: false,
  created_at: "2026-10-18T03:00:00+00:00",
  updated_at: "2026-10-18T03:00:00+00:00",
};

/** The same cutdown, as the person who recorded it sees it. */
const CUTDOWN_MINE = { ...CUTDOWN, can_edit: true, can_delete: true };

/** The same cutdown once something was published from it. */
const CUTDOWN_PUBLISHED = {
  ...CUTDOWN,
  can_edit: true,
  can_delete: false,
  is_published_output: true,
};

/** A derivative living on the NAS, so the link/text branch has both cases. */
const REEL = {
  ...CUTDOWN,
  id: "aaaaaaaa-2222-2222-2222-aaaaaaaaaaaa",
  derivative_type: "REFORMAT",
  label: "Facebook Reel 30s",
  location: NAS,
  is_link: false,
  source_submission_id: null,
  note: null,
};

const DESTINATION = {
  id: "bbbbbbbb-1111-1111-1111-bbbbbbbbbbbb",
  content_id: CONTENT.id,
  label: "Landing page dịch vụ",
  url: LANDING,
  note: "Chạy từ tháng 10",
  added_by_user_id: SESSION.user_id,
  created_at: "2026-08-01T03:00:00+00:00",
  updated_at: "2026-08-01T03:00:00+00:00",
};

const CHANNELS = [
  {
    id: "cccccccc-1111-1111-1111-cccccccccccc",
    code: "CH-FB",
    name: "Apexmed Facebook",
    category: "SCALE",
    status: "ACTIVE",
    brand_id: CONTENT.brand_id,
    platform_id: "dddddddd-1111-1111-1111-dddddddddddd",
    tier: null,
    url: null,
    platform_code: "FACEBOOK",
    policy_grounded_platform: true,
  },
  {
    id: "cccccccc-2222-2222-2222-cccccccccccc",
    code: "CH-TT",
    name: "Apexmed TikTok",
    category: "SCALE",
    status: "ACTIVE",
    brand_id: CONTENT.brand_id,
    platform_id: "dddddddd-2222-2222-2222-dddddddddddd",
    tier: null,
    url: null,
    platform_code: "TIKTOK",
    policy_grounded_platform: true,
  },
];

/** The August publication: the master, on the planned channel. */
const AUGUST = {
  id: "eeeeeeee-1111-1111-1111-eeeeeeeeeeee",
  code: "PUB-2026-000001",
  content_id: CONTENT.id,
  channel_id: CHANNELS[0].id,
  production_submission_id: MASTER.id,
  derivative_id: null,
  published_at: "2026-08-18T13:10:00+00:00",
  url: "https://www.facebook.com/apexmed/posts/123",
  note: null,
  platform_post_id: null,
  publisher_user_id: SESSION.user_id,
  // Step 1F.2.3f.1: the server sends both on every row, so the stub does too -
  // `is_active` is its answer, not a string the client compares.
  status: "PUBLISHED",
  is_active: true,
  // Step 1F.2.3f.2: and so are these. Whether a row may be corrected or taken
  // back is per publication now that contributors record their own postings, so
  // the answer travels on the row rather than on the content.
  can_edit: false,
  can_reverse: false,
};

/** The same publication, as its own publisher sees it. */
const AUGUST_MINE = { ...AUGUST, can_edit: true };

/** The same publication, as an administrator sees it. */
const AUGUST_MANAGED = { ...AUGUST, can_edit: true, can_reverse: true };

/** The October publication: the cutdown, on a channel nobody planned for. */
const OCTOBER = {
  ...AUGUST,
  id: "eeeeeeee-2222-2222-2222-eeeeeeeeeeee",
  code: "PUB-2026-000002",
  channel_id: CHANNELS[1].id,
  production_submission_id: null,
  derivative_id: CUTDOWN.id,
  published_at: "2026-10-18T13:10:00+00:00",
  url: POST_URL,
  note: "Đăng lại dịp khai trương",
};

/** A row from before this step: no output reference at all, and legitimately so. */
const LEGACY = {
  ...AUGUST,
  id: "eeeeeeee-3333-3333-3333-eeeeeeeeeeee",
  code: "PUB-2025-000009",
  production_submission_id: null,
  derivative_id: null,
  url: null,
  note: null,
};

const act = (kind: string, emphasis = "SECONDARY") => ({
  action: kind,
  target_stage: null,
  decision: null,
  emphasis,
  undo_kind: null,
});

/**
 * The detail page's reads, with the four Step 1F.2.3f collections.
 *
 * Every list is what the server would say and nothing else, so anything the page
 * renders about outputs, links or publications came from here.
 */
function detailRoutes({
  stage = "PUBLISHED",
  actions = [] as Array<Record<string, unknown>>,
  masters = [MASTER] as unknown[],
  derivatives = [] as unknown[],
  destinations = [] as unknown[],
  publications = [] as unknown[],
  postFails = false,
} = {}) {
  const content = {
    ...CONTENT,
    workflow_stage: stage,
    producer_user_id: PRODUCER.user_id,
    production_state: null,
  };
  return [
    {
      match: "/available-actions",
      body: {
        content_id: CONTENT.id,
        workflow_stage: stage,
        available_actions: actions,
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
    { match: "/versions", method: "GET", body: [VERSION] },
    {
      match: "/ai-review",
      body: { run: null, review: null, active: false, can_retry: false },
    },
    { match: "/api/pr/people", body: PEOPLE },
    { match: "/api/pr/channels", body: CHANNELS },
    { match: "/production-outputs", body: masters },
    // Declared before the bare `/derivatives` GET so a POST is matched here.
    {
      match: "/derivatives",
      method: "POST",
      status: postFails ? 422 : 201,
      body: postFails
        ? {
            error: {
              code: "validation_error",
              message: "Đường dẫn không hợp lệ.",
            },
          }
        : CUTDOWN,
    },
    { match: "/derivatives", body: derivatives },
    {
      match: "/destinations",
      method: "POST",
      status: 201,
      body: DESTINATION,
    },
    { match: "/destinations", body: destinations },
    {
      match: "/publications",
      method: "POST",
      status: postFails ? 422 : 201,
      body: postFails
        ? {
            error: {
              code: "validation_error",
              message: "Link bài đăng không hợp lệ.",
            },
          }
        : { content, current_version: VERSION, targets: [], brand: null },
    },
    { match: "/publications", body: publications },
    // Step 1F.2.3g. Declared before the catch-all so the comment thread is
    // answered by a comment shape rather than by the content detail.
    {
      match: "/comments",
      body: {
        content_id: CONTENT.id,
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
      },
    },
    {
      match: "/api/pr/contents/",
      body: { content, current_version: VERSION, targets: [], brand: null },
    },
  ];
}

/** Render the detail page and switch to one of its tabs. */
async function openTab(
  label: string,
  options: Parameters<typeof detailRoutes>[0] = {},
): Promise<ReturnType<typeof stubFetch>> {
  const stub = stubFetch(detailRoutes(options));
  renderWithQuery(<ContentDetailPage />);
  const tab = await screen.findByRole("tab", { name: label });
  await userEvent.click(tab);
  return stub;
}

const requests = (stub: ReturnType<typeof stubFetch>) =>
  (
    stub as unknown as {
      calls: Array<{ url: string; method: string; body: unknown }>;
    }
  ).calls;

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ===========================================================================
// 101-104: THE SIX SECTIONS
// ===========================================================================

describe("101. the detail page is six sections, and none of them disappears", () => {
  it("offers Sản phẩm and Xuất bản beside the four that were there", async () => {
    stubFetch(detailRoutes());
    renderWithQuery(<ContentDetailPage />);
    await screen.findByRole("tab", { name: "Nội dung" });

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Tổng quan",
      "Nội dung",
      "Duyệt",
      "Sản phẩm",
      "Xuất bản",
      "Lịch sử",
    ]);
  });

  for (const stage of [
    "READY_TO_PUBLISH",
    "PUBLISHED",
    "MEASURED",
    "ARCHIVED",
  ]) {
    it(`keeps every section at ${stage}`, async () => {
      // The regression that matters most for this step: a page that quietly
      // dropped the script once the piece was published would be useless at
      // exactly the moment it becomes the operational record.
      stubFetch(detailRoutes({ stage }));
      renderWithQuery(<ContentDetailPage />);
      await screen.findByRole("tab", { name: "Nội dung" });

      for (const label of [
        "Tổng quan",
        "Nội dung",
        "Duyệt",
        "Sản phẩm",
        "Xuất bản",
        "Lịch sử",
      ]) {
        expect(
          screen.getByRole("tab", { name: label }),
          `${stage}/${label}`,
        ).toBeInTheDocument();
      }
    });
  }
});

describe("102. Tổng quan still carries what the piece is", () => {
  it("shows type, priority, brand, responsible person and producer", async () => {
    stubFetch(detailRoutes({ stage: "MEASURED" }));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(
      await screen.findByRole("tab", { name: "Tổng quan" }),
    );

    const page = document.body;
    // The header carries the title, the code, the stage and the priority; the
    // overview carries the rest. Both are still drawn at a late stage.
    expect(page.textContent).toContain(CONTENT.title);
    expect(page.textContent).toContain(CONTENT.code);
    expect(screen.getAllByText("Đã đo hiệu quả").length).toBeGreaterThan(0);
    expect(screen.getByText("Kịch bản video ngắn")).toBeInTheDocument();
    expect(screen.getByText("Bình thường")).toBeInTheDocument();
    // Người phụ trách and Người sản xuất are two rows, because they are two
    // people as often as they are one. Before Step 1F.2.3f the producer was on
    // the header and only while the piece had a handoff state, so a published
    // item stopped saying who produced it at exactly the point that becomes a
    // historical question.
    expect(screen.getByText("Người phụ trách")).toBeInTheDocument();
    expect(screen.getByText("Người sản xuất")).toBeInTheDocument();
    await waitFor(() => expect(page.textContent).toContain("Phương Nhung"));
    // Names, never ids.
    expect(page.textContent).not.toContain(PRODUCER.user_id);
  });
});

describe("103. the script survives publication", () => {
  it("still renders the current version at MEASURED", async () => {
    stubFetch(detailRoutes({ stage: "MEASURED" }));
    renderWithQuery(<ContentDetailPage />);

    // The content tab is the initial one, so this is the first render.
    await waitFor(() =>
      expect(
        screen.getByText(
          new RegExp(`Kịch bản hiện tại \\(v${VERSION.version_no}\\)`),
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(VERSION.script_text)).toBeInTheDocument();
  });

  it("still renders Tài nguyên & tham khảo under Duyệt", async () => {
    // Review material lives with the decision it supports, which is where Step
    // 1F.2.3e put it. What matters here is only that an archived piece still
    // has it: workflow controls what you may do, never what you may see.
    stubFetch(detailRoutes({ stage: "ARCHIVED" }));
    renderWithQuery(<ContentDetailPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Duyệt" }));

    expect(
      await screen.findByRole("region", { name: "Tài nguyên & tham khảo" }),
    ).toBeInTheDocument();
  });
});

// ===========================================================================
// 105-113: SẢN PHẨM
// ===========================================================================

describe("105. Sản phẩm holds the masters and the derivatives, separately", () => {
  it("renders both sections", async () => {
    await openTab("Sản phẩm", { derivatives: [CUTDOWN] });

    expect(
      await screen.findByRole("region", { name: "Sản phẩm gốc" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Sản phẩm phái sinh" }),
    ).toBeInTheDocument();
  });

  it("shows the original production output after publication", async () => {
    // Append-only, and a published piece is exactly when somebody wants to see
    // which file was approved.
    await openTab("Sản phẩm", { stage: "PUBLISHED", publications: [AUGUST] });

    const masters = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    expect(within(masters).getByText("Video final 60s")).toBeInTheDocument();
    expect(within(masters).getByRole("link", { name: DRIVE })).toHaveAttribute(
      "href",
      DRIVE,
    );
  });

  it("names an unlabelled master by its submission number, never by its id", async () => {
    await openTab("Sản phẩm", { masters: [{ ...MASTER, label: null }] });

    const masters = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    expect(within(masters).getByText("Bản nộp #1")).toBeInTheDocument();
    expect(masters.textContent).not.toContain(MASTER.id);
  });
});

describe("106. a derivative says what it is, where it is and what it came from", () => {
  it("renders the type in Vietnamese, the location and the lineage", async () => {
    await openTab("Sản phẩm", { derivatives: [CUTDOWN] });

    const lane = await screen.findByRole("region", {
      name: "Sản phẩm phái sinh",
    });
    expect(within(lane).getByText("TikTok cut 25s")).toBeInTheDocument();
    expect(within(lane).getByText("Cắt ngắn")).toBeInTheDocument();
    expect(within(lane).getByRole("link", { name: CUT })).toHaveAttribute(
      "href",
      CUT,
    );
    // The master it was cut from, by **its label** - resolved against the list
    // already on screen rather than sent a second time.
    expect(
      within(lane).getByText("Cắt từ: Video final 60s"),
    ).toBeInTheDocument();
    expect(
      within(lane).getByText("Ghi chú: Cắt cho kênh mới"),
    ).toBeInTheDocument();
  });

  it("renders a NAS path as text to copy, because the server said so", async () => {
    await openTab("Sản phẩm", { derivatives: [REEL] });

    const lane = await screen.findByRole("region", {
      name: "Sản phẩm phái sinh",
    });
    expect(
      within(lane).queryByRole("link", { name: NAS }),
    ).not.toBeInTheDocument();
    expect(within(lane).getByText(NAS)).toBeInTheDocument();
  });

  it("prints no raw enum and no UUID", async () => {
    await openTab("Sản phẩm", { derivatives: [CUTDOWN, REEL] });

    const board = await screen.findByRole("region", {
      name: "Sản phẩm phái sinh",
    });
    for (const raw of [
      "CUTDOWN",
      "REFORMAT",
      "DRIVE_LINK",
      CUTDOWN.id,
      MASTER.id,
    ]) {
      expect(board.textContent, raw).not.toContain(raw);
    }
  });

  it("says so plainly when there is nothing yet", async () => {
    await openTab("Sản phẩm", { derivatives: [] });
    expect(
      await screen.findByText("Chưa có sản phẩm phái sinh nào."),
    ).toBeInTheDocument();
  });
});

describe("107. the add control is the server's decision", () => {
  it("appears when ADD_CONTENT_DERIVATIVE is offered", async () => {
    // Step 1F.2.3g moved this off ``MANAGE_CONTENT_DERIVATIVES``. The two are
    // now different questions - "may you record one" reaches every reader, "are
    // you production management" does not - and this control is the first.
    await openTab("Sản phẩm", { actions: [act("ADD_CONTENT_DERIVATIVE")] });
    expect(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    ).toBeInTheDocument();
  });

  it("stays hidden for management authority alone", async () => {
    // The offer that used to draw it no longer does, on purpose: a client that
    // kept reading the old kind would show the button to production management
    // and withhold it from the colleague who actually made the cut.
    await openTab("Sản phẩm", { actions: [act("MANAGE_CONTENT_DERIVATIVES")] });
    await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(
      screen.queryByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    ).not.toBeInTheDocument();
  });

  it("does not appear when it is not, and the rows carry no controls either", async () => {
    // The same page, the same person, one difference in the server's answer -
    // which is the only thing that may decide this.
    await openTab("Sản phẩm", { actions: [], derivatives: [CUTDOWN] });

    const lane = await screen.findByRole("region", {
      name: "Sản phẩm phái sinh",
    });
    expect(
      screen.queryByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    ).not.toBeInTheDocument();
    expect(
      within(lane).queryByRole("button", { name: "Sửa" }),
    ).not.toBeInTheDocument();
    expect(
      within(lane).queryByRole("button", { name: "Xóa" }),
    ).not.toBeInTheDocument();
  });

  it("keeps no capability check of its own", () => {
    const source = readPrContentDetailSource();
    for (const forbidden of [
      "PR_PRODUCTION_EXECUTE",
      "PR_PUBLICATION_REGISTER",
      "role ===",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });
});

describe("108. the derivative form offers the six types and the masters", () => {
  it("lists every type by its Vietnamese label", async () => {
    await openTab("Sản phẩm", { actions: [act("ADD_CONTENT_DERIVATIVE")] });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    );

    const picker = screen.getByRole("combobox", {
      name: /Loại sản phẩm phái sinh/,
    });
    expect(
      [...picker.querySelectorAll("option")].map(
        (option) => option.textContent,
      ),
    ).toEqual(DERIVATIVE_TYPE_ORDER.map((code) => derivativeTypeLabel(code)));
    // And none of the codes reaches the screen.
    for (const code of DERIVATIVE_TYPE_ORDER) {
      expect(picker.textContent, code).not.toContain(code);
    }
  });

  it("offers the masters by label, with an explicit 'unknown' option", async () => {
    await openTab("Sản phẩm", { actions: [act("ADD_CONTENT_DERIVATIVE")] });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    );

    const picker = screen.getByRole("combobox", {
      name: /Cắt từ sản phẩm gốc/,
    });
    expect(
      [...picker.querySelectorAll("option")].map(
        (option) => option.textContent,
      ),
    ).toEqual([
      // Optional on purpose: a file re-cut from raw footage came from no tracked
      // submission, and requiring a link would store a guess.
      "Không xác định",
      "Video final 60s",
    ]);
  });

  it("sends what was typed, and nothing the browser inferred", async () => {
    const stub = await openTab("Sản phẩm", {
      actions: [act("ADD_CONTENT_DERIVATIVE")],
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Loại sản phẩm phái sinh/ }),
      "REFORMAT",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Tên \/ nhãn/ }),
      "Reel 30s",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link \/ đường dẫn sản phẩm/ }),
      NAS,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Cắt từ sản phẩm gốc/ }),
      MASTER.id,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Thêm sản phẩm phái sinh" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect(post.url).toContain(`/contents/${CONTENT.id}/derivatives`);
    expect(post.body).toMatchObject({
      derivative_type: "REFORMAT",
      label: "Reel 30s",
      location: NAS,
      source_submission_id: MASTER.id,
    });
  });

  it("keeps the form filled when the server refuses", async () => {
    await openTab("Sản phẩm", {
      actions: [act("ADD_CONTENT_DERIVATIVE")],
      postFails: true,
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    );

    await userEvent.type(
      screen.getByRole("textbox", { name: /Tên \/ nhãn/ }),
      "Reel 30s",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link \/ đường dẫn sản phẩm/ }),
      "javascript:alert(1)",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Thêm sản phẩm phái sinh" }),
    );

    await waitFor(() =>
      expect(screen.getByText(/Đường dẫn không hợp lệ/)).toBeInTheDocument(),
    );
    // Nothing was cleared: somebody who pasted a long URL does not paste it twice.
    expect(screen.getByRole("textbox", { name: /Tên \/ nhãn/ })).toHaveValue(
      "Reel 30s",
    );
  });
});

// ===========================================================================
// 114-118: SẢN PHẨM / ĐÍCH ĐẾN
// ===========================================================================

describe("114. the destination links are their own section under Nội dung", () => {
  it("renders the section with its own heading, on the content tab", async () => {
    stubFetch(detailRoutes({ destinations: [DESTINATION] }));
    renderWithQuery(<ContentDetailPage />);

    // The content tab is where the page opens, so this is the first render.
    const section = await screen.findByRole("region", {
      name: "Sản phẩm / đích đến",
    });
    expect(
      within(section).getByRole("heading", { name: "Sản phẩm / đích đến" }),
    ).toBeInTheDocument();
    // Its own section and never a row inside the review material, which lives
    // under "Duyệt": a landing page is not something somebody reads in order to
    // write, and a list mixing the brief with the booking page is unscannable.
    expect(
      screen.queryByRole("region", { name: "Tài nguyên & tham khảo" }),
    ).not.toBeInTheDocument();
  });

  it("renders the label and a clickable link, and no ids", async () => {
    stubFetch(detailRoutes({ destinations: [DESTINATION] }));
    renderWithQuery(<ContentDetailPage />);

    const section = await screen.findByRole("region", {
      name: "Sản phẩm / đích đến",
    });
    expect(
      await within(section).findByText("Landing page dịch vụ"),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: LANDING }),
    ).toHaveAttribute("href", LANDING);
    expect(
      within(section).getByRole("link", { name: LANDING }),
    ).toHaveAttribute("rel", "noreferrer noopener");
    expect(section.textContent).not.toContain(DESTINATION.id);
  });

  it("is visible at every late stage, because it is durable metadata", async () => {
    for (const stage of ["PUBLISHED", "MEASURED", "ARCHIVED"]) {
      stubFetch(detailRoutes({ stage, destinations: [DESTINATION] }));
      const view = renderWithQuery(<ContentDetailPage />);
      const section = await screen.findByRole("region", {
        name: "Sản phẩm / đích đến",
      });
      expect(
        await within(section).findByText("Landing page dịch vụ"),
        stage,
      ).toBeInTheDocument();
      view.unmount();
    }
  });

  it("offers the add control only when the server did", async () => {
    stubFetch(detailRoutes({ actions: [act("MANAGE_CONTENT_DESTINATIONS")] }));
    renderWithQuery(<ContentDetailPage />);
    const section = await screen.findByRole("region", {
      name: "Sản phẩm / đích đến",
    });
    expect(
      within(section).getByRole("button", { name: "+ Thêm link" }),
    ).toBeInTheDocument();
  });

  it("hides it when the server did not", async () => {
    stubFetch(detailRoutes({ actions: [], destinations: [DESTINATION] }));
    renderWithQuery(<ContentDetailPage />);
    const section = await screen.findByRole("region", {
      name: "Sản phẩm / đích đến",
    });
    expect(
      within(section).queryByRole("button", { name: "+ Thêm link" }),
    ).not.toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: "Sửa" }),
    ).not.toBeInTheDocument();
  });

  it("sends the label and the URL as typed", async () => {
    const stub = stubFetch(
      detailRoutes({ actions: [act("MANAGE_CONTENT_DESTINATIONS")] }),
    );
    renderWithQuery(<ContentDetailPage />);
    const section = await screen.findByRole("region", {
      name: "Sản phẩm / đích đến",
    });
    await userEvent.click(
      within(section).getByRole("button", { name: "+ Thêm link" }),
    );

    await userEvent.type(
      within(section).getByRole("textbox", { name: /Tên \/ nhãn/ }),
      "Trang đặt lịch",
    );
    await userEvent.type(
      within(section).getByRole("textbox", { name: /^Link/ }),
      LANDING,
    );
    await userEvent.click(
      within(section).getByRole("button", { name: "Thêm link" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect(post.url).toContain(`/contents/${CONTENT.id}/destinations`);
    expect(post.body).toMatchObject({ label: "Trang đặt lịch", url: LANDING });
  });
});

// ===========================================================================
// 119-130: XUẤT BẢN
// ===========================================================================

describe("119. the publication history says which file went where", () => {
  it("renders the channel, the output label, the output location and the post URL", async () => {
    await openTab("Xuất bản", {
      derivatives: [CUTDOWN],
      publications: [OCTOBER],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    // The channel by name, never by id.
    await waitFor(() =>
      expect(within(section).getByText("Apexmed TikTok")).toBeInTheDocument(),
    );
    // Which file - resolved from the derivative list the page already loaded.
    expect(within(section).getByText("TikTok cut 25s")).toBeInTheDocument();
    // Where that file lives …
    expect(within(section).getByRole("link", { name: CUT })).toHaveAttribute(
      "href",
      CUT,
    );
    // … and, separately, where the post is. Two different things.
    expect(
      within(section).getByRole("link", { name: POST_URL }),
    ).toHaveAttribute("href", POST_URL);
    expect(
      within(section).getByText("Ghi chú: Đăng lại dịp khai trương"),
    ).toBeInTheDocument();
    expect(section.textContent).not.toContain(OCTOBER.id);
    expect(section.textContent).not.toContain(CHANNELS[1].id);
  });

  it("resolves a master-backed publication too", async () => {
    await openTab("Xuất bản", { publications: [AUGUST] });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Apexmed Facebook")).toBeInTheDocument(),
    );
    expect(within(section).getByText("Video final 60s")).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: DRIVE }),
    ).toBeInTheDocument();
  });

  it("says so in words when a legacy row names no output", async () => {
    // Rows written before this step reference no file, and that is a real,
    // permanent state - not missing data, and never a blank.
    await openTab("Xuất bản", { publications: [LEGACY] });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(within(section).getByText("Không rõ sản phẩm")).toBeInTheDocument();
  });

  it("renders zero publications as a sentence, not as a failure", async () => {
    await openTab("Xuất bản", { publications: [] });
    expect(
      await screen.findByText("Chưa có bài đăng nào."),
    ).toBeInTheDocument();
  });

  it("renders the whole history, newest first as the server sent it", async () => {
    await openTab("Xuất bản", {
      derivatives: [CUTDOWN],
      publications: [OCTOBER, AUGUST],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Apexmed TikTok")).toBeInTheDocument(),
    );
    const text = section.textContent ?? "";
    // Order as given - re-sorting here would be a second opinion about the same
    // list, and the server's is total.
    expect(text.indexOf("TikTok cut 25s")).toBeLessThan(
      text.indexOf("Video final 60s"),
    );
  });
});

describe("120. the publication form", () => {
  const withForm = () =>
    openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      derivatives: [CUTDOWN, REEL],
    });

  it("appears only when the server offered RECORD_PUBLICATION", async () => {
    await openTab("Xuất bản", { actions: [] });
    expect(
      screen.queryByRole("button", { name: "+ Thêm kênh đã đăng" }),
    ).not.toBeInTheDocument();
  });

  it("offers every field the step asked for", async () => {
    await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    expect(screen.getByRole("combobox", { name: /^Kênh/ })).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Thời gian đăng/)).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /Ghi chú/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    ).toBeInTheDocument();
  });

  it("lists both masters and derivatives in the output picker, by label", async () => {
    await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    const picker = screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ });
    expect(
      [...picker.querySelectorAll("option")].map(
        (option) => option.textContent,
      ),
    ).toEqual([
      "— chọn sản phẩm —",
      "Video final 60s",
      "TikTok cut 25s",
      "Facebook Reel 30s",
    ]);
    // Labels only - the two id fields the API takes are derived from the choice.
    expect(picker.textContent).not.toContain(MASTER.id);
    expect(picker.textContent).not.toContain(CUTDOWN.id);
  });

  it("offers every active channel, not only the content's planned targets", async () => {
    // The reuse case: a channel created after the plan was written is exactly
    // where a re-cut goes. The stub's content has no targets at all.
    await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    const picker = screen.getByRole("combobox", { name: /^Kênh/ });
    expect(
      [...picker.querySelectorAll("option")].map(
        (option) => option.textContent,
      ),
    ).toEqual(["— chọn kênh —", "Apexmed Facebook", "Apexmed TikTok"]);
  });

  it("sends exactly one output reference - the derivative case", async () => {
    const stub = await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[1].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `DERIVATIVE:${CUTDOWN.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect(post.url).toContain(`/contents/${CONTENT.id}/publications`);
    expect(post.body).toMatchObject({
      channel_id: CHANNELS[1].id,
      derivative_id: CUTDOWN.id,
      production_submission_id: null,
      url: POST_URL,
    });
  });

  it("sends exactly one output reference - the master case", async () => {
    const stub = await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[0].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `SUBMISSION:${MASTER.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect(post.body).toMatchObject({
      production_submission_id: MASTER.id,
      derivative_id: null,
    });
  });

  it("defaults the time to now and lets it be changed", async () => {
    const stub = await withForm();
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    const when = screen.getByLabelText(/Thời gian đăng/) as HTMLInputElement;
    expect(when.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);

    await userEvent.clear(when);
    await userEvent.type(when, "2026-10-18T20:10");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[1].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `DERIVATIVE:${CUTDOWN.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect((post.body as { published_at: string }).published_at).toContain(
      "2026-10-18",
    );
  });

  it("keeps the form filled when the server refuses", async () => {
    await openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      derivatives: [CUTDOWN],
      postFails: true,
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[1].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `DERIVATIVE:${CUTDOWN.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      "tiktok.com/x",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/Link bài đăng không hợp lệ/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("textbox", { name: /Link bài đăng/ })).toHaveValue(
      "tiktok.com/x",
    );
  });

  it("says why the form cannot be used when nothing has been produced", async () => {
    await openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      masters: [],
      derivatives: [],
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    expect(
      screen.getByText(/Chưa có sản phẩm nào để chọn/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    ).toBeDisabled();
  });
});

// ===========================================================================
// 131-134: THE REUSE SCENARIO, IN THE PANEL
// ===========================================================================

describe("131. a published piece is reused without any workflow reset", () => {
  it("keeps the old publication and shows the new one beside it", async () => {
    // The October state: same content, both publications, the cutdown recorded.
    await openTab("Xuất bản", {
      stage: "PUBLISHED",
      derivatives: [CUTDOWN],
      publications: [OCTOBER, AUGUST],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Apexmed TikTok")).toBeInTheDocument(),
    );
    // Both, and each naming the file that actually went out.
    expect(within(section).getByText("Apexmed Facebook")).toBeInTheDocument();
    expect(within(section).getByText("Video final 60s")).toBeInTheDocument();
    expect(within(section).getByText("TikTok cut 25s")).toBeInTheDocument();
  });

  it("shows no workflow reset anywhere on the page", async () => {
    stubFetch(
      detailRoutes({
        stage: "PUBLISHED",
        derivatives: [CUTDOWN],
        publications: [OCTOBER, AUGUST],
        actions: [act("ADD_CONTENT_DERIVATIVE")],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await screen.findByRole("tab", { name: "Sản phẩm" });

    // The stage badge still reads "Đã đăng", and nothing offers to restart
    // production - because the server offered no such action.
    expect(screen.getByText("Đã đăng")).toBeInTheDocument();
    for (const gone of [
      "Bắt đầu sản xuất",
      "Gửi duyệt nội bộ",
      "Nhận sản xuất",
    ]) {
      expect(
        screen.queryByRole("button", { name: gone }),
        gone,
      ).not.toBeInTheDocument();
    }
  });

  it("never offers to duplicate the content", () => {
    // Reuse happens on the same content record. A "nhân bản" control would be
    // the workaround this whole step exists to remove.
    const source = readPrContentDetailSource();
    for (const forbidden of ["duplicateContent", "cloneContent", "Nhân bản"]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });
});

// ===========================================================================
// 135-138: WHAT REFRESHES, AND WHAT MUST NOT
// ===========================================================================

describe("135. a detail mutation refreshes detail data and nothing else", () => {
  it("refetches only the derivative and output lists after adding a derivative", async () => {
    const stub = await openTab("Sản phẩm", {
      actions: [act("ADD_CONTENT_DERIVATIVE")],
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Tên \/ nhãn/ }),
      "Reel 30s",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link \/ đường dẫn sản phẩm/ }),
      CUT,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Thêm sản phẩm phái sinh" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    // No board request at all: a derivative is not a transition, so nothing
    // about the work queue can have changed. Step 1F.2.3c2's lanes are untouched.
    expect(
      requests(stub).some((call) => call.url.includes("/contents/board")),
    ).toBe(false);
  });

  it("invalidates the board only where the stage can actually move", () => {
    const source = readPrContentDetailSource();
    // The publication form calls the page-wide `invalidate`, which does touch
    // the board - correctly, because the first publication from
    // READY_TO_PUBLISH moves the item to PUBLISHED. The derivative and
    // destination sections have their own narrow invalidation instead.
    expect(source).toContain('queryKey: ["derivatives", contentId]');
    expect(source).toContain('queryKey: ["destinations", contentId]');
    expect(source).toContain("onRecorded();");
  });
});

// ===========================================================================
// 139-140: THE WORK QUEUE IS UNTOUCHED
// ===========================================================================

describe("139. the board still asks for lanes and nothing heavier", () => {
  it("fetches no derivative, publication or destination data for cards", () => {
    const source = read("app/pr/content/page.tsx");
    for (const forbidden of [
      "contentDerivatives",
      "listPublications",
      "contentDestinations",
      "productionOutputs",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
    // And the lane request shape from Step 1F.2.3c2 is exactly as it was.
    expect(source).toContain("lane, limit: LANE_PAGE_SIZE, offset: pageParam");
    expect(source).toContain("limit: 0");
  });

  it("keeps the global pager gone", () => {
    const source = read("app/pr/content/page.tsx");
    expect(source).not.toContain("Sau →");
    expect(source).not.toContain("← Trước");
  });

  it("keeps the card free of any collection of it", () => {
    // `ContentSummary` is what a lane returns, sixty at a time. A **collection**
    // on it would be the N+1 this step was told not to create - which is the
    // rule, and it is narrower than "never mentions a publication".
    //
    // Step 1F.2.3f.4 made the board month-scoped, which cannot be shown
    // without saying when a piece went out. So the summary carries one
    // **scalar** - `published_at`, resolved server-side - at the cost of one
    // grouped query per page rather than one per card. Step 1F.2.3f.6 removed
    // `archive_reason` with the virtual archive it described. Derivatives and
    // destinations remain absent entirely: nothing on a card needs them.
    const source = read("lib/api.ts");
    const summary = source.slice(
      source.indexOf("export interface ContentSummary"),
      source.indexOf("export interface ContentTarget"),
    );
    for (const forbidden of ["derivative", "destination"]) {
      expect(summary.toLowerCase(), forbidden).not.toContain(forbidden);
    }
    // No array of any of the three, which is the shape that would be the N+1.
    for (const collection of ["derivatives", "publications", "destinations"]) {
      expect(summary, collection).not.toMatch(
        new RegExp(`\\b${collection}\\s*[?:]`),
      );
    }
    expect(summary).toContain("published_at: string | null;");
    expect(summary).not.toContain("archive_reason");
  });
});

// ===========================================================================
// 141-152: ONE PUBLISH ACTION, AND SAFE CORRECTIONS (STEP 1F.2.3f.1)
// ===========================================================================

/** A publication taken back: still in the history, no longer counted. */
const REVERSED = {
  ...OCTOBER,
  id: "eeeeeeee-4444-4444-4444-eeeeeeeeeeee",
  code: "PUB-2026-000003",
  status: "REVERSED",
  is_active: false,
};

describe("141. the standalone 'Đánh dấu đã đăng' is gone", () => {
  it("does not appear at READY_TO_PUBLISH", async () => {
    // The server no longer offers the transition - the edge needs a publication
    // record to drive it - so the button cannot be drawn. Asserted against a
    // stage that used to show it, with the action list the server would send.
    stubFetch(
      detailRoutes({
        stage: "READY_TO_PUBLISH",
        actions: [act("RECORD_PUBLICATION")],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await screen.findByRole("tab", { name: "Xuất bản" });

    expect(
      screen.queryByRole("button", { name: "Đánh dấu đã đăng" }),
    ).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Đánh dấu đã đăng");
  });

  it("removes the wording as well as the wiring", () => {
    // The label table is where a stale button would come back from, so the
    // entry is removed rather than merely unreferenced.
    expect(read("lib/labels.ts")).not.toContain("Đánh dấu đã đăng");
    expect(readPrContentDetailSource()).not.toContain("Đánh dấu đã đăng");
  });

  it("still offers the one authoritative action", async () => {
    await openTab("Xuất bản", {
      stage: "READY_TO_PUBLISH",
      actions: [act("RECORD_PUBLICATION")],
      publications: [],
    });

    expect(
      await screen.findByText("Chưa có bài đăng nào."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "+ Thêm kênh đã đăng" }),
    ).toBeInTheDocument();
  });

  it("still records a first publication through it", async () => {
    const stub = await openTab("Xuất bản", {
      stage: "READY_TO_PUBLISH",
      actions: [act("RECORD_PUBLICATION")],
      publications: [],
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[0].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `SUBMISSION:${MASTER.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    expect(
      requests(stub).find((call) => call.method === "POST")!.url,
    ).toContain(`/contents/${CONTENT.id}/publications`);
  });
});

describe("142. correcting a publication", () => {
  const authorized = () =>
    openTab("Xuất bản", {
      actions: [act("EDIT_ANY_PUBLICATION"), act("REVERSE_PUBLICATION")],
      publications: [AUGUST_MANAGED],
    });

  it("shows Sửa when the server said this row may be corrected", async () => {
    await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(
      await within(section).findByRole("button", { name: "Sửa" }),
    ).toBeInTheDocument();
  });

  it("hides it when the server did not", async () => {
    await openTab("Xuất bản", { actions: [], publications: [AUGUST] });
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await within(section).findByText("Video final 60s");
    expect(
      within(section).queryByRole("button", { name: "Sửa" }),
    ).not.toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: "Hoàn tác đăng bài" }),
    ).not.toBeInTheDocument();
  });

  it("offers the URL, the time and the note - and nothing that defines the row", async () => {
    await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Sửa" }),
    );

    expect(screen.getByRole("textbox", { name: /Link bài đăng/ })).toHaveValue(
      AUGUST.url,
    );
    expect(screen.getByLabelText(/Thời gian đăng/)).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /Ghi chú/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Lưu thay đổi" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hủy" })).toBeInTheDocument();
    // The channel and the output define what the row means. A wrong one is
    // fixed by reversing and re-recording, not by rewriting history.
    expect(
      screen.queryByRole("combobox", { name: /^Kênh/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: /Sản phẩm đã đăng/ }),
    ).not.toBeInTheDocument();
  });

  it("sends only the correctable fields", async () => {
    const stub = await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Sửa" }),
    );

    const url = screen.getByRole("textbox", { name: /Link bài đăng/ });
    await userEvent.clear(url);
    await userEvent.type(url, POST_URL);
    await userEvent.click(screen.getByRole("button", { name: "Lưu thay đổi" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "PATCH")).toBe(true),
    );
    const patch = requests(stub).find((call) => call.method === "PATCH")!;
    expect(patch.url).toContain(`/publications/${AUGUST.id}`);
    expect(patch.body).toMatchObject({ url: POST_URL });
    expect(patch.body).not.toHaveProperty("channel_id");
    expect(patch.body).not.toHaveProperty("production_submission_id");
    expect(patch.body).not.toHaveProperty("derivative_id");
  });
});

describe("143. taking a publication back", () => {
  const authorized = (publications: unknown[] = [AUGUST_MANAGED]) =>
    openTab("Xuất bản", {
      actions: [act("EDIT_ANY_PUBLICATION"), act("REVERSE_PUBLICATION")],
      publications,
    });

  it("shows Hoàn tác đăng bài, never Xóa", async () => {
    await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(
      await within(section).findByRole("button", { name: "Hoàn tác đăng bài" }),
    ).toBeInTheDocument();
    // Nothing is deleted, and the control must not say otherwise.
    expect(
      within(section).queryByRole("button", { name: "Xóa" }),
    ).not.toBeInTheDocument();
  });

  it("confirms first, and explains what will and will not happen", async () => {
    await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Hoàn tác đăng bài" }),
    );

    // Step 1F.2.8: the shared dialog, so this is asserted on the dialog rather
    // than inside the section. The channel is now named in the question, which
    // is the part a page listing four publications makes worth having.
    const box = dialog();
    expect(box.getByText(/Thu hồi bản đăng trên/)).toBeInTheDocument();
    expect(box.getByText(/vẫn nằm trong lịch sử/)).toBeInTheDocument();
    expect(box.getByText(/Sẵn sàng đăng/)).toBeInTheDocument();
    expect(
      box.getByRole("button", { name: "Thu hồi bản đăng" }),
    ).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Thôi" })).toBeInTheDocument();
  });

  it("posts to the reverse sub-resource rather than deleting anything", async () => {
    const stub = await authorized();
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Hoàn tác đăng bài" }),
    );
    await confirm();

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const post = requests(stub).find((call) => call.method === "POST")!;
    expect(post.url).toContain(`/publications/${AUGUST.id}/reverse`);
    expect(requests(stub).some((call) => call.method === "DELETE")).toBe(false);
  });
});

describe("144. a reversed publication stays in the history", () => {
  it("labels it Đã hoàn tác and keeps every fact about it", async () => {
    await openTab("Xuất bản", {
      derivatives: [CUTDOWN],
      publications: [REVERSED],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Apexmed TikTok")).toBeInTheDocument(),
    );
    expect(within(section).getByText("Đã hoàn tác")).toBeInTheDocument();
    // Channel, output, output location, post URL and instant all still readable.
    expect(within(section).getByText("TikTok cut 25s")).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: CUT }),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: POST_URL }),
    ).toBeInTheDocument();
    expect(within(section).getByText(/Đăng lúc/)).toBeInTheDocument();
  });

  it("offers no controls on it, even to an authorized actor", async () => {
    // It is history. There is nothing left to correct, and the server refuses.
    await openTab("Xuất bản", {
      actions: [act("EDIT_ANY_PUBLICATION"), act("REVERSE_PUBLICATION")],
      derivatives: [CUTDOWN],
      publications: [{ ...REVERSED, can_edit: true, can_reverse: true }],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Đã hoàn tác")).toBeInTheDocument(),
    );
    expect(
      within(section).queryByRole("button", { name: "Sửa" }),
    ).not.toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: "Hoàn tác đăng bài" }),
    ).not.toBeInTheDocument();
  });

  it("keeps an active publication beside it, with its own controls", async () => {
    await openTab("Xuất bản", {
      actions: [act("EDIT_ANY_PUBLICATION"), act("REVERSE_PUBLICATION")],
      derivatives: [CUTDOWN],
      publications: [REVERSED, AUGUST_MANAGED],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Video final 60s")).toBeInTheDocument(),
    );
    expect(within(section).getByText("Đã hoàn tác")).toBeInTheDocument();
    // One row still counts, so exactly one pair of controls is drawn.
    expect(
      within(section).getAllByRole("button", { name: "Sửa" }),
    ).toHaveLength(1);
  });

  it("shows no raw status code anywhere", async () => {
    await openTab("Xuất bản", {
      derivatives: [CUTDOWN],
      publications: [REVERSED, AUGUST],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(within(section).getByText("Đã hoàn tác")).toBeInTheDocument(),
    );
    for (const code of ["REVERSED", "PUBLISHED", "UNAVAILABLE", "REMOVED"]) {
      expect(section.textContent, code).not.toContain(code);
    }
  });
});

describe("145. the board follows a stage change and nothing else", () => {
  it("refetches the board after a reversal, because the stage may have moved", () => {
    // A safe reversal takes the content back to READY_TO_PUBLISH, so the card
    // moves within Hoàn tất. The row hands the page-wide `invalidate` - which
    // touches the board - rather than reading `stage_reverted` and deciding: the
    // panel keeps no copy of the rule behind it.
    const source = readPrContentDetailSource();
    expect(source).toContain("api.reversePublication");
    expect(source).toContain("onChanged();");
  });

  it("leaves the board's own query structure untouched", () => {
    // Step 1F.2.3c2 is authoritative. Publication management lives on the
    // detail page and nowhere near a lane.
    const source = read("app/pr/content/page.tsx");
    for (const forbidden of [
      "reversePublication",
      "updatePublication",
      "Hoàn tác đăng bài",
      "listPublications",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
    expect(source).toContain("lane, limit: LANE_PAGE_SIZE, offset: pageParam");
  });
});

// ===========================================================================
// 146-152: FLEXIBLE ASSET LOCATIONS & CONTRIBUTOR PUBLISHING (STEP 1F.2.3f.2)
// ===========================================================================

/** The four storage shapes the team actually types. */
const POSIX_PATH = "/volume1/media/2026/video-final.mp4";
const WINDOWS_PATH = "M:\\XAY KENH\\video-final.mp4";
const RELATIVE_PATH = "shared/campaign/video-final.mp4";

/** A master this session may correct - the server's per-row answer. */
const MASTER_MINE = { ...MASTER, can_correct: true };

/** A master on a NAS path, so the text-not-anchor branch has a case. */
const MASTER_ON_NAS = {
  ...MASTER,
  id: "99999999-8888-8888-8888-999999999999",
  artifact_type: "NAS_PATH",
  location: WINDOWS_PATH,
  is_link: false,
  label: "Bản gốc trên NAS",
};

describe("146. an asset location may be a path, not only a link", () => {
  it("renders a mapped-drive path as text rather than as a dead anchor", async () => {
    await openTab("Sản phẩm", { masters: [MASTER_ON_NAS] });

    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await within(section).findByText("Bản gốc trên NAS");
    // `is_link` is the server's answer. A browser deciding from the string would
    // read `M:\…` as a scheme and wrap it in an anchor that opens nothing.
    expect(
      within(section).queryByRole("link", { name: WINDOWS_PATH }),
    ).not.toBeInTheDocument();
    expect(within(section).getByText(WINDOWS_PATH)).toBeInTheDocument();
  });

  it("still renders an http(s) location as a safe external link", async () => {
    await openTab("Sản phẩm", { masters: [MASTER] });

    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    const link = await within(section).findByRole("link", { name: DRIVE });
    expect(link).toHaveAttribute("href", DRIVE);
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("never renders an unsafe scheme as an anchor", async () => {
    // Such a value cannot be stored - every validator refuses it - so the
    // server would send `is_link: false` if one ever appeared. The display
    // rule is positive ("names a scheme we allow"), so this is belt and braces.
    await openTab("Sản phẩm", {
      masters: [
        {
          ...MASTER,
          location: "javascript:alert(1)",
          is_link: false,
          label: "Không an toàn",
        },
      ],
    });

    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await within(section).findByText("Không an toàn");
    expect(section.querySelector('a[href^="javascript:"]')).toBeNull();
  });
});

describe("147. the location field stops claiming to be link-only", () => {
  it("labels the submit field neutrally", () => {
    const source = readPrContentDetailSource();
    // "Link file sản xuất" told half the people using it that their value was
    // not welcome. The field takes a Drive link, a NAS link, a path on the
    // volume, a mapped drive or a shared-root location.
    expect(source).not.toContain("Link file sản xuất");
    expect(source).toContain("Link / đường dẫn sản phẩm");
  });

  it("hints per artifact type rather than saying http-only everywhere", () => {
    const labels = read("lib/labels.ts");
    expect(labels).toContain("assetLocationHint");
    // The NAS hint must not tell somebody to paste a URL.
    expect(labels).toContain("Nhập đường dẫn file hoặc thư mục");
  });

  it("offers the existing artifact-type picker rather than a second vocabulary", async () => {
    await openTab("Sản phẩm", {
      masters: [MASTER_MINE],
      actions: [act("CORRECT_PRODUCTION_OUTPUT")],
    });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Sửa link sản phẩm" }),
    );

    const picker = screen.getByRole("combobox", { name: "Loại file sản xuất" });
    expect(
      [...picker.querySelectorAll("option")].map(
        (option) => option.textContent,
      ),
    ).toEqual(["Google Drive", "Link NAS", "Đường dẫn trên NAS", "Link khác"]);
    // Words, never the codes.
    for (const code of [
      "DRIVE_LINK",
      "NAS_LINK",
      "NAS_PATH",
      "EXTERNAL_LINK",
    ]) {
      expect(picker.textContent, code).not.toContain(code);
    }
  });
});

describe("148. correcting a handed-in production file", () => {
  it("shows the control only when the row says this session may", async () => {
    await openTab("Sản phẩm", { masters: [MASTER_MINE] });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    expect(
      await within(section).findByRole("button", { name: "Sửa link sản phẩm" }),
    ).toBeInTheDocument();
  });

  it("hides it for somebody else's output", async () => {
    await openTab("Sản phẩm", { masters: [MASTER] });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await within(section).findByText("Video final 60s");
    expect(
      within(section).queryByRole("button", { name: "Sửa link sản phẩm" }),
    ).not.toBeInTheDocument();
  });

  it("hides it once something has been published from it", async () => {
    // The same submitter, the same session - one difference in the server's
    // answer, which is the only thing that may decide this.
    await openTab("Sản phẩm", {
      masters: [{ ...MASTER_MINE, can_correct: false }],
      publications: [AUGUST],
    });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await within(section).findByText("Video final 60s");
    expect(
      within(section).queryByRole("button", { name: "Sửa link sản phẩm" }),
    ).not.toBeInTheDocument();
  });

  it("sends the type and the path together, and nothing that fixes the row", async () => {
    const stub = await openTab("Sản phẩm", { masters: [MASTER_MINE] });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Sửa link sản phẩm" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Loại file sản xuất" }),
      "NAS_PATH",
    );
    const field = screen.getByRole("textbox", {
      name: /Link \/ đường dẫn sản phẩm/,
    });
    await userEvent.clear(field);
    await userEvent.type(field, POSIX_PATH);
    await userEvent.click(screen.getByRole("button", { name: "Lưu thay đổi" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "PATCH")).toBe(true),
    );
    const patch = requests(stub).find((call) => call.method === "PATCH")!;
    expect(patch.url).toContain(`/production-outputs/${MASTER.id}`);
    expect(patch.body).toMatchObject({
      artifact_type: "NAS_PATH",
      location: POSIX_PATH,
    });
    // Everything that fixes which file this is has no field on the request.
    for (const frozen of [
      "submission_no",
      "content_version_id",
      "producer_user_id",
      "submitted_by_user_id",
    ]) {
      expect(patch.body, frozen).not.toHaveProperty(frozen);
    }
  });

  it("accepts a relative path without an http-only complaint", async () => {
    const stub = await openTab("Sản phẩm", { masters: [MASTER_MINE] });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await userEvent.click(
      await within(section).findByRole("button", { name: "Sửa link sản phẩm" }),
    );

    const field = screen.getByRole("textbox", {
      name: /Link \/ đường dẫn sản phẩm/,
    });
    await userEvent.clear(field);
    await userEvent.type(field, RELATIVE_PATH);
    await userEvent.click(screen.getByRole("button", { name: "Lưu thay đổi" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "PATCH")).toBe(true),
    );
    // The browser refuses nothing structural: the shape rule is the server's,
    // and the form's only job is not to send an empty box.
    expect(
      requests(stub).find((call) => call.method === "PATCH")!.body,
    ).toMatchObject({
      location: RELATIVE_PATH,
    });
  });

  it("shows no raw storage enum on the row", async () => {
    await openTab("Sản phẩm", { masters: [MASTER_ON_NAS] });
    const section = await screen.findByRole("region", { name: "Sản phẩm gốc" });
    await within(section).findByText("Bản gốc trên NAS");
    expect(section.textContent).not.toContain("NAS_PATH");
    expect(section.textContent).toContain("Đường dẫn trên NAS");
  });
});

describe("149. a contributor records a publication", () => {
  it("sees the control because the server offered it, not because of a role", async () => {
    // No management action anywhere in the list - just the one the server hands
    // anybody who may view this piece. Step 1F.2.3f.3.
    await openTab("Xuất bản", {
      stage: "READY_TO_PUBLISH",
      actions: [act("RECORD_PUBLICATION")],
      publications: [],
    });

    expect(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    ).toBeInTheDocument();
  });

  it("keeps no role comparison anywhere on the page", () => {
    const source = readPrContentDetailSource();
    for (const forbidden of [
      'role === "OWNER"',
      'role === "ADMIN"',
      "OWNER",
      "PUBLISH_SOCIAL",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });

  it("submits the form and refreshes the detail", async () => {
    const stub = await openTab("Xuất bản", {
      stage: "READY_TO_PUBLISH",
      actions: [act("RECORD_PUBLICATION")],
      publications: [],
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[0].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `SUBMISSION:${MASTER.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    // The first publication moves the stage, so the detail is refetched.
    await waitFor(() =>
      expect(
        requests(stub).filter((call) => call.url.includes("/review-context"))
          .length,
      ).toBeGreaterThan(1),
    );
  });

  it("attributes the posting to whoever recorded it", async () => {
    // "Ai add link đăng bài" is answered on the row, from `publisher_user_id`,
    // and rendered by name.
    await openTab("Xuất bản", {
      publications: [{ ...AUGUST, publisher_user_id: PRODUCER.user_id }],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(
        within(section).getByText("Người ghi nhận: Phương Nhung"),
      ).toBeInTheDocument(),
    );
    expect(section.textContent).not.toContain(PRODUCER.user_id);
  });
});

describe("150. the publication controls follow the row, not the page", () => {
  it("shows Sửa on the contributor's own publication", async () => {
    await openTab("Xuất bản", { actions: [], publications: [AUGUST_MINE] });
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(
      await within(section).findByRole("button", { name: "Sửa" }),
    ).toBeInTheDocument();
    // Creating publications is not administering them.
    expect(
      within(section).queryByRole("button", { name: "Hoàn tác đăng bài" }),
    ).not.toBeInTheDocument();
  });

  it("hides it on somebody else's, in the same list", async () => {
    await openTab("Xuất bản", {
      actions: [],
      derivatives: [CUTDOWN],
      publications: [
        AUGUST_MINE,
        { ...OCTOBER, can_edit: false, can_reverse: false },
      ],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await within(section).findByText("Video final 60s");
    // Two rows, one control - which is the whole reason the flag is per row.
    expect(
      within(section).getAllByRole("button", { name: "Sửa" }),
    ).toHaveLength(1);
  });

  it("shows Hoàn tác only where the server said so", async () => {
    await openTab("Xuất bản", {
      actions: [act("EDIT_ANY_PUBLICATION"), act("REVERSE_PUBLICATION")],
      publications: [AUGUST_MANAGED],
    });
    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(
      await within(section).findByRole("button", { name: "Hoàn tác đăng bài" }),
    ).toBeInTheDocument();
  });

  it("reads the flags off the row rather than comparing ids", () => {
    const source = readPrContentDetailSource();
    expect(source).toContain("publication.can_edit");
    expect(source).toContain("publication.can_reverse");
    expect(source).toContain("submission.can_correct");
    // A browser comparing the publisher to a session id would be re-deriving an
    // authorization rule the server already owns.
    expect(source).not.toContain("publisher_user_id ===");
  });
});

describe("151. the hidden control is not the boundary", () => {
  it("renders the server's refusal when a write is rejected", async () => {
    await openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      derivatives: [CUTDOWN],
      postFails: true,
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[1].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `DERIVATIVE:${CUTDOWN.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    // The panel does not pre-judge the channel rule; it renders what the server
    // said, which is what makes the server the boundary.
    await waitFor(() =>
      expect(
        screen.getByText(/Link bài đăng không hợp lệ/),
      ).toBeInTheDocument(),
    );
  });
});

describe("152. the work queue is still untouched", () => {
  it("fetches no output, publication or correction data for cards", () => {
    const source = read("app/pr/content/page.tsx");
    for (const forbidden of [
      "correctProductionOutput",
      "productionOutputs",
      "listPublications",
      "can_correct",
      "can_edit",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
    expect(source).toContain("lane, limit: LANE_PAGE_SIZE, offset: pageParam");
    expect(source).toContain("limit: 0");
  });
});

// ===========================================================================
// 153: STEP 1F.2.3f.3 - ANY CONTENT VIEWER MAY RECORD A PUBLICATION
// ===========================================================================

describe("153. an ordinary member records a publication", () => {
  /** The session as a plain member: no management action anywhere in the list. */
  const asMember = (extra: Parameters<typeof detailRoutes>[0] = {}) =>
    openTab("Xuất bản", {
      stage: "READY_TO_PUBLISH",
      actions: [act("RECORD_PUBLICATION")],
      publications: [],
      ...extra,
    });

  it("sees + Thêm kênh đã đăng with no administrative action offered", async () => {
    // Requirement 51. The only offer is the narrow one, which is the point of
    // this step: the button must not need EDIT_ANY_PUBLICATION to appear.
    const stub = await asMember();
    expect(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    ).toBeInTheDocument();

    const asked = requests(stub).map((call) => call.url);
    expect(asked.some((url) => url.includes("/available-actions"))).toBe(true);
  });

  it("is never shown the old channel-permission warning", () => {
    // Requirement 52, asserted against the source rather than one render,
    // because the failure this guards is a leftover string appearing on some
    // path a test did not walk. The refusal it belonged to no longer exists.
    for (const file of [...PR_CONTENT_DETAIL_FILES, "lib/labels.ts"]) {
      const source = read(file);
      expect(source, file).not.toContain("ghi nhận bài đăng trên kênh này");
      expect(source, file).not.toContain("channel_not_yours");
    }
  });

  it("submits a valid form and gets its row back", async () => {
    // Requirements 53 and 54, in one walk: the member fills the five fields the
    // form has always had, the POST goes out, and the list is re-read.
    const stub = await asMember({ derivatives: [CUTDOWN] });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[1].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `DERIVATIVE:${CUTDOWN.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      POST_URL,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(
        requests(stub).some(
          (call) =>
            call.method === "POST" && call.url.includes("/publications"),
        ),
      ).toBe(true),
    );
    const posted = requests(stub).find(
      (call) => call.method === "POST" && call.url.includes("/publications"),
    );
    // No permission field travels with it, and exactly one output reference does.
    const body = posted?.body as Record<string, unknown>;
    expect(body.derivative_id).toBe(CUTDOWN.id);
    expect(body.production_submission_id ?? null).toBeNull();
    expect(Object.keys(body)).not.toContain("capability");
  });

  it("shows the member as the recorder, and only their own Sửa", async () => {
    // Requirements 55, 56 and 57. Two rows in one list: the member's own and a
    // colleague's, with the flags the server sent and no id comparison here.
    await openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      derivatives: [CUTDOWN],
      publications: [
        AUGUST_MINE,
        {
          ...OCTOBER,
          publisher_user_id: PRODUCER.user_id,
          can_edit: false,
          can_reverse: false,
        },
      ],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await waitFor(() =>
      expect(
        within(section).getByText("Người ghi nhận: Phương Nhung"),
      ).toBeInTheDocument(),
    );
    expect(
      within(section).getAllByRole("button", { name: "Sửa" }),
    ).toHaveLength(1);
  });

  it("is offered no Hoàn tác on any row", async () => {
    // Requirement 58. Creating is broad and reversing is not, and the panel does
    // not infer one from the other: `can_reverse` is false on both rows even
    // though the member recorded one of them.
    await openTab("Xuất bản", {
      actions: [act("RECORD_PUBLICATION")],
      derivatives: [CUTDOWN],
      publications: [AUGUST_MINE, { ...OCTOBER, can_edit: true }],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    await within(section).findByText("Video final 60s");
    expect(
      within(section).queryByRole("button", { name: "Hoàn tác đăng bài" }),
    ).not.toBeInTheDocument();
  });

  it("leaves the management view exactly as it was", async () => {
    // Requirement 59. The same page with the administrator's answers still draws
    // both controls - widening creation took nothing away from the other end.
    await openTab("Xuất bản", {
      actions: [
        act("RECORD_PUBLICATION"),
        act("EDIT_ANY_PUBLICATION"),
        act("REVERSE_PUBLICATION"),
      ],
      publications: [AUGUST_MANAGED],
    });

    const section = await screen.findByRole("region", { name: "Đã xuất bản" });
    expect(
      await within(section).findByRole("button", { name: "Sửa" }),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Hoàn tác đăng bài" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "+ Thêm kênh đã đăng" }),
    ).toBeInTheDocument();
  });

  it("shows the real reason when a write is refused", async () => {
    // Requirement 60. The panel pre-judges nothing and renders what came back -
    // so a member who typed a bad link is told that, rather than being told
    // something about a channel permission that no longer decides anything.
    await asMember({ derivatives: [CUTDOWN], postFails: true });
    await userEvent.click(
      await screen.findByRole("button", { name: "+ Thêm kênh đã đăng" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Kênh/ }),
      CHANNELS[0].id,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sản phẩm đã đăng/ }),
      `SUBMISSION:${MASTER.id}`,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Link bài đăng/ }),
      "khong-phai-link",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Lưu bài đã đăng" }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/Link bài đăng không hợp lệ/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/kênh này/)).not.toBeInTheDocument();
  });
});
