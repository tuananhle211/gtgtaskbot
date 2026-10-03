/**
 * Step 1F.2.3g - open derivative contributions, and the comment thread.
 *
 * Numbered 141-180, continuing the frontend numbering
 * `derivatives-and-publications.test.tsx` left at 140.
 *
 * The discipline is the one the rest of this suite keeps and this step makes
 * load-bearing: **the browser decides nothing.** Both halves of 1F.2.3g widen a
 * rule to "anybody who may view the piece", and the tempting shortcut for both
 * is the same - there is a session, so draw the control. Every assertion here is
 * of the shape "given this server answer, this appears", and its mirror, because
 * a panel that renders a composer from the existence of a login looks identical
 * on screen to one that asks.
 *
 * Three things get their own sections because they are the ones a rewrite would
 * quietly lose:
 *
 * * the comment section is **outside every tab**, so it survives all six;
 * * a failed send **keeps what was typed**;
 * * a tombstoned root still renders, with its replies underneath.
 *
 * The last section is the board regression Step 1F.2.3c2 requires: no lane
 * request may grow a comment, a comment count or a derivative.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  CONTENT,
  SESSION,
  VERSION,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
} from "./helpers";

const ROOT = path.resolve(__dirname, "..");
const read = (relative: string) => readFileSync(path.join(ROOT, "src", relative), "utf8");

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const { default: ContentDetailPage } = await import("@/app/pr/content/[id]/page");

const NHUNG = {
  user_id: "88888888-8888-8888-8888-888888888888",
  full_name: "Phương Nhung",
  role: "EMPLOYEE",
};
const HAO = {
  user_id: "77777777-7777-7777-7777-777777777777",
  full_name: "Hào",
  role: "EMPLOYEE",
};
const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  NHUNG,
  HAO,
];

const CUT = "https://drive.google.com/file/d/1CutDownTwentyFive/view";
const DRIVE = "https://drive.google.com/file/d/1MasterSixtySeconds/view";

const MASTER = {
  id: "99999999-9999-9999-9999-999999999999",
  content_id: CONTENT.id,
  submission_no: 1,
  artifact_type: "DRIVE_LINK",
  location: DRIVE,
  is_link: true,
  label: "Video final 60s",
  note: null,
  producer_user_id: NHUNG.user_id,
  submitted_by_user_id: NHUNG.user_id,
  content_version_id: VERSION.id,
  created_at: "2026-08-10T03:00:00+00:00",
  can_correct: false,
};

/**
 * The October cutdown, recorded by somebody outside the production team.
 *
 * `created_by_name` is joined by the server. Before Step 1F.2.3g a panel could
 * resolve the id against `/people` and usually be right; now that any reader may
 * record one, "usually" includes a colleague who has left, whom `/people` does
 * not list at all.
 */
const CUTDOWN = {
  id: "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
  content_id: CONTENT.id,
  derivative_type: "CUTDOWN",
  label: "TikTok cut 25s",
  location: CUT,
  is_link: true,
  source_submission_id: MASTER.id,
  note: null,
  created_by_user_id: HAO.user_id,
  created_by_name: HAO.full_name,
  can_edit: false,
  can_delete: false,
  is_published_output: false,
  created_at: "2026-08-18T10:20:00+00:00",
  updated_at: "2026-08-18T10:20:00+00:00",
};

/** The same row, as the person who recorded it sees it. */
const CUTDOWN_MINE = { ...CUTDOWN, can_edit: true, can_delete: true };

/** The same row once a publication names it: correctable, never removable. */
const CUTDOWN_PUBLISHED = {
  ...CUTDOWN,
  can_edit: true,
  can_delete: false,
  is_published_output: true,
};

const NOW = "2026-08-18T12:00:00+00:00";

const ROOT_COMMENT = {
  id: "cccccccc-1111-1111-1111-cccccccccccc",
  content_id: CONTENT.id,
  parent_comment_id: null,
  author_user_id: NHUNG.user_id,
  author_name: NHUNG.full_name,
  body: "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé.",
  created_at: "2026-08-18T11:55:00+00:00",
  edited_at: null,
  is_deleted: false,
  can_edit: false,
  can_delete: false,
  replies: [] as unknown[],
};

const REPLY = {
  ...ROOT_COMMENT,
  id: "cccccccc-2222-2222-2222-cccccccccccc",
  parent_comment_id: ROOT_COMMENT.id,
  author_user_id: HAO.user_id,
  author_name: HAO.full_name,
  body: "Đã sửa bản cut mới.",
  created_at: "2026-08-18T11:58:00+00:00",
  replies: [] as unknown[],
};

/** The root as its own author sees it. */
const ROOT_MINE = { ...ROOT_COMMENT, can_edit: true, can_delete: true };

/** A root taken down: no words, no name, and its replies intact. */
const ROOT_TOMBSTONE = {
  ...ROOT_COMMENT,
  author_user_id: null,
  author_name: null,
  body: null,
  edited_at: null,
  is_deleted: true,
  can_edit: false,
  can_delete: false,
};

const act = (kind: string, emphasis = "SECONDARY") => ({
  action: kind,
  target_stage: null,
  decision: null,
  emphasis,
  undo_kind: null,
});

function detailRoutes({
  stage = "PUBLISHED",
  actions = [] as Array<Record<string, unknown>>,
  derivatives = [] as unknown[],
  comments = [] as unknown[],
  total = null as number | null,
  postFails = false,
  patchFails = false,
} = {}) {
  const content = {
    ...CONTENT,
    workflow_stage: stage,
    producer_user_id: NHUNG.user_id,
    production_state: null,
  };
  return [
    {
      match: "/available-actions",
      body: { content_id: CONTENT.id, workflow_stage: stage, available_actions: actions },
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
    { match: "/ai-review", body: { run: null, review: null, active: false, can_retry: false } },
    { match: "/api/pr/people", body: PEOPLE },
    { match: "/api/pr/channels", body: [] },
    { match: "/production-outputs", body: [MASTER] },
    // The Lịch sử tab asks for these; without them the stub throws and the
    // page renders nothing, which would make "the section survives this tab"
    // pass for the wrong reason on the other five.
    { match: "/history", body: [] },
    { match: "/approvals", body: [] },
    { match: "/derivatives", method: "POST", status: 201, body: CUTDOWN_MINE },
    { match: "/derivatives", body: derivatives },
    { match: "/destinations", body: [] },
    { match: "/publications", body: [] },
    // Declared before the bare GET so a write is matched here.
    {
      match: "/comments",
      method: "POST",
      status: postFails ? 422 : 201,
      body: postFails
        ? { error: { code: "validation_error", message: "Bình luận quá dài." } }
        : { ...ROOT_MINE, id: "dddddddd-1111-1111-1111-dddddddddddd", body: "Mới gửi" },
    },
    {
      match: "/comments",
      method: "PATCH",
      status: patchFails ? 422 : 200,
      body: patchFails
        ? { error: { code: "validation_error", message: "Bình luận quá dài." } }
        : { ...ROOT_MINE, body: "Đã sửa lại" },
    },
    { match: "/comments", method: "DELETE", status: 204 },
    {
      match: "/comments",
      body: {
        content_id: CONTENT.id,
        items: comments,
        total: total ?? comments.length,
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

/** Render the detail page, optionally switching to one of its tabs. */
async function open(
  options: Parameters<typeof detailRoutes>[0] = {},
  tab?: string,
): Promise<ReturnType<typeof stubFetch>> {
  const stub = stubFetch(detailRoutes(options));
  renderWithQuery(<ContentDetailPage />);
  if (tab) {
    await userEvent.click(await screen.findByRole("tab", { name: tab }));
  }
  return stub;
}

const requests = (stub: ReturnType<typeof stubFetch>) =>
  (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;

/**
 * The comment section, once it has answered.
 *
 * The section itself renders immediately - it is outside every tab, which is
 * the point of it - so `findByRole` alone would hand back a box still saying
 * "Đang tải bình luận…" and every assertion below would race the fetch.
 */
async function commentSection(): Promise<HTMLElement> {
  const section = await screen.findByRole("region", { name: "Bình luận" });
  await waitFor(() =>
    expect(within(section).queryByText("Đang tải bình luận…")).not.toBeInTheDocument(),
  );
  return section;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ===========================================================================
// 141-146: THE DERIVATIVE CONTROLS ARE THE SERVER'S ANSWERS
// ===========================================================================

describe("141. an ordinary member is offered the add control", () => {
  it("draws it from ADD_CONTENT_DERIVATIVE alone", async () => {
    // No production offer, no ownership, no role - one action kind, and the
    // control appears. That is the whole of Step 1F.2.3g's frontend contract.
    await open({ actions: [act("ADD_CONTENT_DERIVATIVE")] }, "Sản phẩm");
    expect(
      await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    ).toBeInTheDocument();
  });

  it("keeps no rule of its own about who may contribute", () => {
    const source = read("app/pr/content/[id]/page.tsx");
    for (const forbidden of [
      "PR_PRODUCTION_EXECUTE",
      "PR_PRODUCTION_ASSIGN",
      "producer_user_id ===",
      "owner_user_id ===",
      "role ===",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });
});

describe("142. the add form still asks for the same five fields", () => {
  it("opens with the label, type, source, location and note, and no workflow field", async () => {
    await open({ actions: [act("ADD_CONTENT_DERIVATIVE")] }, "Sản phẩm");
    await userEvent.click(await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }));

    expect(screen.getByRole("textbox", { name: /Tên \/ nhãn/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Link \/ đường dẫn sản phẩm/ })).toBeInTheDocument();
    // Nothing here moves the workflow, so nothing here offers to.
    for (const gone of ["Bước tiếp theo", "Gửi duyệt", "Bắt đầu sản xuất"]) {
      expect(screen.queryByRole("button", { name: gone }), gone).not.toBeInTheDocument();
    }
  });

  it("sends what was typed", async () => {
    const stub = await open({ actions: [act("ADD_CONTENT_DERIVATIVE")] }, "Sản phẩm");
    await userEvent.click(await screen.findByRole("button", { name: "+ Thêm sản phẩm phái sinh" }));
    await userEvent.type(screen.getByRole("textbox", { name: /Tên \/ nhãn/ }), "Reel 30s");
    await userEvent.type(screen.getByRole("textbox", { name: /Link \/ đường dẫn sản phẩm/ }), CUT);
    await userEvent.click(screen.getByRole("button", { name: "Thêm sản phẩm phái sinh" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    const posted = requests(stub).find((call) => call.method === "POST");
    expect(posted?.body).toMatchObject({ label: "Reel 30s", location: CUT });
  });
});

describe("143. a derivative says who added it, and when", () => {
  it("renders the name the server joined and the timestamp", async () => {
    await open({ derivatives: [CUTDOWN] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });

    expect(within(section).getByText(/Thêm bởi: Hào/)).toBeInTheDocument();
    // The instant, in the business timezone the rest of the panel uses.
    expect(within(section).getByText(/18\/08\/2026/)).toBeInTheDocument();
  });

  it("never renders the creator's UUID", async () => {
    await open({ derivatives: [CUTDOWN] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(section.textContent).not.toContain(CUTDOWN.created_by_user_id);
    expect(section.textContent).not.toContain(CUTDOWN.id);
  });

  it("falls back to the plain timestamp when the user row has gone", async () => {
    // `created_by_name: null` is a real state - the person left - and the row
    // renders an absence rather than an id.
    await open({ derivatives: [{ ...CUTDOWN, created_by_name: null }] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(within(section).getByText(/Thêm lúc/)).toBeInTheDocument();
    expect(section.textContent).not.toContain(CUTDOWN.created_by_user_id);
  });
});

describe("144. the row controls come from the row", () => {
  it("shows Sửa and Xóa on a derivative this session recorded", async () => {
    await open({ derivatives: [CUTDOWN_MINE] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(within(section).getByRole("button", { name: "Sửa" })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Xóa" })).toBeInTheDocument();
  });

  it("shows neither on somebody else's, even with the add control offered", async () => {
    // The distinction Step 1F.2.3g draws: contributing is open, managing is
    // not, and both answers are on this one screen at the same time.
    await open(
      { actions: [act("ADD_CONTENT_DERIVATIVE")], derivatives: [CUTDOWN] },
      "Sản phẩm",
    );
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(
      screen.getByRole("button", { name: "+ Thêm sản phẩm phái sinh" }),
    ).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Sửa" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Xóa" })).not.toBeInTheDocument();
  });

  it("draws the two flags independently down one list", async () => {
    await open({ derivatives: [CUTDOWN_MINE, CUTDOWN] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    // One row of two has controls. A content-level flag could not do this.
    expect(within(section).getAllByRole("button", { name: "Sửa" })).toHaveLength(1);
  });
});

describe("145. a published output keeps its correction and loses its delete", () => {
  it("offers Sửa and withholds Xóa", async () => {
    await open({ derivatives: [CUTDOWN_PUBLISHED] }, "Sản phẩm");
    const section = await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    expect(within(section).getByRole("button", { name: "Sửa" })).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Xóa" })).not.toBeInTheDocument();
  });
});

describe("146. nothing on this page duplicates the content", () => {
  it("offers no clone control, at any stage", async () => {
    await open({ stage: "ARCHIVED", actions: [act("ADD_CONTENT_DERIVATIVE")] }, "Sản phẩm");
    await screen.findByRole("region", { name: "Sản phẩm phái sinh" });
    for (const gone of ["Nhân bản", "Tạo bản sao"]) {
      expect(screen.queryByRole("button", { name: gone }), gone).not.toBeInTheDocument();
    }
  });
});

// ===========================================================================
// 147-152: THE COMMENT SECTION IS ALWAYS THERE
// ===========================================================================

describe("147. Bình luận appears on the content detail page", () => {
  it("renders the section with its heading", async () => {
    await open();
    expect(await commentSection()).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bình luận" })).toBeInTheDocument();
  });

  for (const tab of ["Tổng quan", "Nội dung", "Duyệt", "Sản phẩm", "Xuất bản", "Lịch sử"]) {
    it(`survives the ${tab} tab`, async () => {
      // The section is rendered outside every tab, so switching cannot remove
      // it - which is the point: a note about the hook is typed while reading
      // the script and answered by whoever is looking at the cut.
      await open({ comments: [ROOT_COMMENT] }, tab);
      const section = await commentSection();
      expect(within(section).getByText(ROOT_COMMENT.body)).toBeInTheDocument();
    });
  }
});

describe("148. it is there at every stage", () => {
  for (const stage of ["IDEA", "AI_REVIEW", "PRODUCTION", "PUBLISHED", "MEASURED", "ARCHIVED"]) {
    it(`renders at ${stage}, composer included`, async () => {
      // Including ARCHIVED. The server offers ADD_CONTENT_COMMENT there because
      // this repository's ARCHIVED is terminal for transitions and blocks
      // deletion - it has never meant "nobody may write about this".
      await open({ stage, actions: [act("ADD_CONTENT_COMMENT")] });
      const section = await commentSection();
      expect(
        within(section).getByRole("textbox", { name: "Viết bình luận" }),
      ).toBeInTheDocument();
    });
  }
});

describe("149. the empty state says so", () => {
  it("renders Chưa có bình luận nào.", async () => {
    await open();
    const section = await commentSection();
    expect(within(section).getByText("Chưa có bình luận nào.")).toBeInTheDocument();
  });
});

describe("150. the composer is the server's decision", () => {
  it("appears when ADD_CONTENT_COMMENT is offered", async () => {
    await open({ actions: [act("ADD_CONTENT_COMMENT")] });
    const section = await commentSection();
    expect(within(section).getByRole("textbox", { name: "Viết bình luận" })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Gửi bình luận" })).toBeInTheDocument();
  });

  it("is absent when it is not - a session is not a permission", async () => {
    await open({ actions: [], comments: [ROOT_COMMENT] });
    const section = await commentSection();
    // The thread still reads, because reading and writing are separate answers
    // even though they happen to share a rule on the server.
    expect(within(section).getByText(ROOT_COMMENT.body)).toBeInTheDocument();
    expect(
      within(section).queryByRole("textbox", { name: "Viết bình luận" }),
    ).not.toBeInTheDocument();
  });
});

describe("151. a comment shows who and when", () => {
  it("renders the author's name and a relative time", async () => {
    vi.setSystemTime(new Date(NOW));
    await open({ comments: [ROOT_COMMENT] });
    const section = await commentSection();

    expect(within(section).getByText(NHUNG.full_name)).toBeInTheDocument();
    expect(within(section).getByText("5 phút trước")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("never renders an author UUID or a comment id", async () => {
    await open({ comments: [{ ...ROOT_COMMENT, replies: [REPLY] }] });
    const section = await commentSection();
    expect(section.textContent).not.toContain(NHUNG.user_id);
    expect(section.textContent).not.toContain(ROOT_COMMENT.id);
  });

  it("marks a reworded comment", async () => {
    await open({ comments: [{ ...ROOT_COMMENT, edited_at: "2026-08-18T11:57:00+00:00" }] });
    const section = await commentSection();
    expect(within(section).getByText(/đã sửa/)).toBeInTheDocument();
  });
});

describe("152. the thread is what the server sent", () => {
  it("reports how many roots were not shown", async () => {
    await open({ comments: [ROOT_COMMENT], total: 13 });
    const section = await commentSection();
    expect(within(section).getByText(/Còn 12 bình luận nữa/)).toBeInTheDocument();
  });
});

// ===========================================================================
// 153-158: WRITING, REPLYING, EDITING, DELETING
// ===========================================================================

describe("153. writing a comment", () => {
  it("posts the body and clears the box", async () => {
    const stub = await open({ actions: [act("ADD_CONTENT_COMMENT")] });
    const section = await commentSection();
    const box = within(section).getByRole("textbox", { name: "Viết bình luận" });

    await userEvent.type(box, "Link này dùng bản TikTok mới nhé.");
    await userEvent.click(within(section).getByRole("button", { name: "Gửi bình luận" }));

    await waitFor(() =>
      expect(
        requests(stub).some(
          (call) => call.method === "POST" && call.url.includes("/comments"),
        ),
      ).toBe(true),
    );
    const posted = requests(stub).find(
      (call) => call.method === "POST" && call.url.includes("/comments"),
    );
    expect(posted?.body).toEqual({ body: "Link này dùng bản TikTok mới nhé." });
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("cannot send an empty one", async () => {
    await open({ actions: [act("ADD_CONTENT_COMMENT")] });
    const section = await commentSection();
    const send = within(section).getByRole("button", { name: "Gửi bình luận" });
    expect(send).toBeDisabled();

    await userEvent.type(within(section).getByRole("textbox", { name: "Viết bình luận" }), "   ");
    // Whitespace is not a comment. The server says so too; this only saves the
    // request.
    expect(send).toBeDisabled();
  });

  it("keeps the text when the server refuses", async () => {
    // The assertion this feature is worth having. A 422 on a long comment must
    // not cost somebody their paragraph.
    const typed = "Đoạn này cần cắt lại, phần hook hơi dài so với bản gốc.";
    await open({ actions: [act("ADD_CONTENT_COMMENT")], postFails: true });
    const section = await commentSection();
    const box = within(section).getByRole("textbox", { name: "Viết bình luận" });

    await userEvent.type(box, typed);
    await userEvent.click(within(section).getByRole("button", { name: "Gửi bình luận" }));

    await waitFor(() => expect(within(section).getByText(/Bình luận quá dài/)).toBeInTheDocument());
    expect(box).toHaveValue(typed);
  });
});

describe("154. replying", () => {
  it("offers Trả lời on a root and sends parent_comment_id", async () => {
    const stub = await open({
      actions: [act("ADD_CONTENT_COMMENT")],
      comments: [ROOT_COMMENT],
    });
    const section = await commentSection();
    await userEvent.click(
      within(section).getByRole("button", { name: `Trả lời bình luận của ${NHUNG.full_name}` }),
    );

    const box = within(section).getByRole("textbox", {
      name: `Trả lời bình luận của ${NHUNG.full_name}`,
    });
    await userEvent.type(box, "Đã sửa bản cut mới.");
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Gửi trả lời cho ${NHUNG.full_name}`,
      }),
    );

    await waitFor(() =>
      expect(
        requests(stub).some((call) => call.method === "POST" && call.url.includes("/comments")),
      ).toBe(true),
    );
    const posted = requests(stub).find(
      (call) => call.method === "POST" && call.url.includes("/comments"),
    );
    expect(posted?.body).toEqual({
      body: "Đã sửa bản cut mới.",
      parent_comment_id: ROOT_COMMENT.id,
    });
  });

  it("renders replies one level in, and offers no second level", async () => {
    await open({
      actions: [act("ADD_CONTENT_COMMENT")],
      comments: [{ ...ROOT_COMMENT, replies: [REPLY] }],
    });
    const section = await commentSection();

    expect(within(section).getByText(REPLY.body)).toBeInTheDocument();
    // Exactly one "Trả lời" on the page: the root's. A reply may not be
    // replied to, so offering one would be offering a 422.
    expect(
      within(section).getAllByRole("button", { name: /^Trả lời bình luận của/ }),
    ).toHaveLength(1);
  });

  it("indents modestly, and only once", () => {
    // A thread that indents per level runs out of width on a phone, which is
    // where most of these are read. One border, one padding step, no recursion.
    const source = read("components/comments.tsx");
    expect(source).toContain("pl-4");
    expect(source).not.toContain("pl-16");
  });
});

describe("155. editing your own", () => {
  it("shows Sửa only where the server said can_edit", async () => {
    await open({ comments: [ROOT_MINE, { ...REPLY, id: "e1", parent_comment_id: null }] });
    const section = await commentSection();
    expect(within(section).getAllByRole("button", { name: "Sửa bình luận" })).toHaveLength(1);
  });

  it("opens a composer with the current wording and saves it", async () => {
    const stub = await open({ comments: [ROOT_MINE] });
    const section = await commentSection();
    await userEvent.click(within(section).getByRole("button", { name: "Sửa bình luận" }));

    const box = within(section).getByRole("textbox", { name: "Sửa bình luận" });
    expect(box).toHaveValue(ROOT_COMMENT.body);
    await userEvent.clear(box);
    await userEvent.type(box, "Hook hơi dài, cắt còn 3 giây nhé.");
    await userEvent.click(within(section).getByRole("button", { name: "Lưu bình luận" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "PATCH")).toBe(true),
    );
    expect(requests(stub).find((call) => call.method === "PATCH")?.body).toEqual({
      body: "Hook hơi dài, cắt còn 3 giây nhé.",
    });
  });

  it("keeps the new wording when the save is refused", async () => {
    await open({ comments: [ROOT_MINE], patchFails: true });
    const section = await commentSection();
    await userEvent.click(within(section).getByRole("button", { name: "Sửa bình luận" }));

    const box = within(section).getByRole("textbox", { name: "Sửa bình luận" });
    await userEvent.clear(box);
    await userEvent.type(box, "Bản sửa của tôi");
    await userEvent.click(within(section).getByRole("button", { name: "Lưu bình luận" }));

    await waitFor(() => expect(within(section).getByText(/Bình luận quá dài/)).toBeInTheDocument());
    // Not reverted to the old text: the edit is still there to be retried.
    expect(box).toHaveValue("Bản sửa của tôi");
  });
});

describe("156. deleting", () => {
  it("asks before it does it, then sends the DELETE", async () => {
    const stub = await open({ comments: [ROOT_MINE] });
    const section = await commentSection();
    await userEvent.click(within(section).getByRole("button", { name: "Xoá bình luận" }));

    // Step 1F.2.8: the shared dialog, like every other delete in the panel.
    expect(dialog().getByText("Xóa bình luận này?")).toBeInTheDocument();
    await confirm();

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "DELETE")).toBe(true),
    );
  });

  it("is absent where the server said can_delete is false", async () => {
    await open({ comments: [ROOT_COMMENT] });
    const section = await commentSection();
    expect(within(section).queryByRole("button", { name: "Xoá bình luận" })).not.toBeInTheDocument();
  });
});

describe("157. a deleted comment is a gap, not a hole", () => {
  it("renders Đã xoá bình luận. and keeps the replies", async () => {
    await open({ comments: [{ ...ROOT_TOMBSTONE, replies: [REPLY] }] });
    const section = await commentSection();

    expect(within(section).getByText("Đã xoá bình luận.")).toBeInTheDocument();
    // Somebody else's answers survive the question being taken down.
    expect(within(section).getByText(REPLY.body)).toBeInTheDocument();
    expect(within(section).getByText(HAO.full_name)).toBeInTheDocument();
    // And the tombstone offers nothing.
    expect(within(section).queryByRole("button", { name: "Sửa bình luận" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Xoá bình luận" })).not.toBeInTheDocument();
  });

  it("shows no name for the removed comment", async () => {
    await open({ comments: [ROOT_TOMBSTONE] });
    const section = await commentSection();
    expect(within(section).queryByText(NHUNG.full_name)).not.toBeInTheDocument();
  });
});

describe("158. a comment body is text", () => {
  it("renders markup as characters", async () => {
    const payload = '<script>alert("xin chào")</script>';
    await open({ comments: [{ ...ROOT_COMMENT, body: payload }] });
    const section = await commentSection();

    // The characters are on screen, and no element was created from them.
    expect(within(section).getByText(payload)).toBeInTheDocument();
    expect(section.querySelector("script")).toBeNull();
  });

  it("hands no raw HTML to the DOM", () => {
    const source = read("components/comments.tsx");
    expect(source).not.toContain("innerHTML");
  });
});

// ===========================================================================
// 159-160: WHAT MUST NOT HAVE CHANGED
// ===========================================================================

describe("159. commenting is not a workflow event", () => {
  it("refetches the thread and nothing else", async () => {
    const stub = await open({ actions: [act("ADD_CONTENT_COMMENT")] });
    const section = await commentSection();
    await userEvent.type(
      within(section).getByRole("textbox", { name: "Viết bình luận" }),
      "Ghi chú",
    );
    await userEvent.click(within(section).getByRole("button", { name: "Gửi bình luận" }));

    await waitFor(() =>
      expect(requests(stub).some((call) => call.method === "POST")).toBe(true),
    );
    // No board request, no transition, no version write. A comment moves
    // nothing, so nothing about Step 1F.2.3c2's lanes can be stale.
    expect(requests(stub).some((call) => call.url.includes("/contents/board"))).toBe(false);
    expect(requests(stub).some((call) => call.url.includes("/transition"))).toBe(false);
    expect(
      requests(stub).some((call) => call.method === "POST" && call.url.includes("/versions")),
    ).toBe(false);
  });

  it("invalidates only the comment query", () => {
    const source = read("components/comments.tsx");
    expect(source).toContain('queryKey: ["content-comments", contentId]');
    expect(source).not.toContain('queryKey: ["content-board"]');
    expect(source).not.toContain('queryKey: ["available-actions"');
  });
});

describe("160. the board carries no comments", () => {
  it("asks for none of it from the work queue", () => {
    // Step 1F.2.3c2's per-lane pagination is authoritative and this step must
    // not touch it. The board's own query builder must know nothing about
    // comments, and the API client must expose no board comment count.
    const board = read("lib/board.ts");
    for (const forbidden of ["comment", "Comment", "derivative"]) {
      expect(board, forbidden).not.toContain(forbidden);
    }
  });

  it("puts the comment call on the content detail route only", () => {
    const client = read("lib/api.ts");
    expect(client).toContain("/comments");
    // No count field on a card, and no comment parameter on the board request.
    //
    // Asserted on the interface rather than on the whole file, because Step
    // 1F.2.9 put a `comment_count` in this client for an entirely different
    // object - a TikTok video, whose counters come from the Display API and
    // have nothing to do with MeoChat's own comments. A whole-file grep would
    // have failed on that and said nothing true about the board.
    const start = client.indexOf("export interface ContentSummary");
    expect(start).toBeGreaterThan(-1);
    const summary = client.slice(
      start,
      client.indexOf("export interface", start + 1),
    );
    expect(summary).not.toContain("comment_count");
  });
});
