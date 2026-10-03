/**
 * Step 1F.2.3/1F.2.3a/1F.2.3b - the production card, deleting, and undo.
 *
 * Numbered 40-61 for the delete and production halves, then 62-73 for Step
 * 1F.2.3b's handoff and "Hoàn tác".
 *
 * What these assert is narrow on purpose: **the browser decides nothing here.**
 * Every button on the production card is drawn because `/available-actions`
 * named it, the producer is rendered by name because `/people` supplied one, and
 * whether a file reference is a link is the server's `is_link` rather than a
 * regex over the string. So most of the load-bearing assertions are of the shape
 * "given this server answer, this appears" and its mirror, "given the answer
 * without it, this does not" - because a panel that renders a delete button from
 * a role string looks identical on screen to one that asks.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  CONTENT,
  SESSION,
  VERSION,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
} from "./helpers";

const PUSHED: string[] = [];

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr/content",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: (url: string) => PUSHED.push(url) }),
}));

const { default: ContentDetailPage } = await import("@/app/pr/content/[id]/page");

const PRODUCER = {
  user_id: "88888888-8888-8888-8888-888888888888",
  full_name: "Phương Nhung",
  role: "EMPLOYEE",
};
const PEOPLE = [
  { user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role },
  PRODUCER,
];

const DRIVE = "https://drive.google.com/file/d/1AbCdEf/view";

const SUBMISSION = {
  id: "99999999-9999-9999-9999-999999999999",
  submission_no: 1,
  artifact_type: "DRIVE_LINK",
  location: DRIVE,
  is_link: true,
  label: null,
  note: "Bản 30 giây",
  producer_user_id: PRODUCER.user_id,
  submitted_by_user_id: PRODUCER.user_id,
  content_version_id: VERSION.id,
  created_at: "2026-08-10T03:00:00+00:00",
};

/** The server's `handoff_state`, mirrored so the stubs stay honest. */
function handoffState(stage: string, producer: string | null): string | null {
  if (stage === "APPROVED") {
    return producer ? "READY_FOR_PRODUCTION" : "WAITING_FOR_PRODUCER";
  }
  if (stage === "PRODUCTION") return "IN_PRODUCTION";
  if (stage === "INTERNAL_REVIEW") return "IN_INTERNAL_REVIEW";
  return null;
}

const action = (kind: string, emphasis = "PRIMARY") => ({
  action: kind,
  target_stage: null,
  decision: null,
  emphasis,
  undo_kind: null,
});

/**
 * The detail page's five reads, plus production.
 *
 * `producer` and `submissions` are what the server would say; the page is given
 * them and nothing else, so anything it renders about production came from here.
 */
function detailRoutes({
  stage = "PRODUCTION",
  actions = [] as Array<Record<string, unknown>>,
  producer = null as string | null,
  submissions = [] as unknown[],
  approvals = [] as unknown[],
  deletedAt = null as string | null,
} = {}) {
  const content = {
    ...CONTENT,
    workflow_stage: stage,
    producer_user_id: producer,
    deleted_at: deletedAt,
    // Derived by the server from those two, and sent - the panel must not work
    // it out, so the stub does what the API does.
    production_state: handoffState(stage, producer),
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
        approvals,
      },
    },
    { match: "/versions", method: "GET", body: [VERSION] },
    { match: "/ai-review", body: { run: null, review: null, active: false, can_retry: false } },
    { match: "/api/pr/people", body: PEOPLE },
    {
      match: "/production",
      body: {
        content_id: CONTENT.id,
        workflow_stage: stage,
        producer_user_id: producer,
        submissions,
      },
    },
    {
      match: "/api/pr/contents/",
      body: { content, current_version: VERSION, targets: [], brand: null },
    },
  ];
}

const sent = (stub: ReturnType<typeof stubFetch>) =>
  (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;

beforeEach(() => {
  PUSHED.length = 0;
});

// ===========================================================================
// 40-49: PERMANENT DELETE
// ===========================================================================

describe("40-43. the delete button is the server's decision, never the browser's", () => {
  it("renders it when the action list carries DELETE_CONTENT", async () => {
    stubFetch(detailRoutes({ stage: "IDEA", actions: [action("DELETE_CONTENT", "DANGER")] }));
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
  });

  it("renders nothing for a member whose content has reached production", async () => {
    // The same page, the same person, one difference in the server's answer -
    // which is the only thing that may decide this. The rule behind the absence
    // (``production_started_at`` is set) is a stored date the browser has never
    // seen and must not try to infer from the stage.
    stubFetch(detailRoutes({ stage: "PRODUCTION", producer: PRODUCER.user_id, actions: [] }));
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Xóa nội dung" })).not.toBeInTheDocument();
  });

  it("renders it at READY_TO_PUBLISH, where management may still delete", async () => {
    stubFetch(
      detailRoutes({
        stage: "READY_TO_PUBLISH",
        actions: [action("DELETE_CONTENT", "DANGER")],
      }),
    );
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
  });

  it("renders no permanent delete on a published item, whoever is looking", async () => {
    // The server withholds it for everybody past this boundary, including an
    // OWNER, so there is nothing here to hide or show conditionally.
    stubFetch(detailRoutes({ stage: "PUBLISHED", actions: [] }));
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() => expect(screen.getByText(CONTENT.title)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Xóa nội dung" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Xóa vĩnh viễn/)).not.toBeInTheDocument();
  });
});

describe("44. published work is archived, not deleted", () => {
  it("offers the archive transition the server listed, and no delete beside it", async () => {
    // Archiving is an ordinary workflow move (`MEASURED -> ARCHIVED`), offered
    // by the same action list. It is deliberately *not* dressed up as a delete:
    // "Lưu trữ nội dung" keeps the piece and says so.
    stubFetch(
      detailRoutes({
        stage: "MEASURED",
        actions: [
          {
            action: "TRANSITION",
            target_stage: "ARCHIVED",
            decision: null,
            emphasis: "PRIMARY",
          },
        ],
      }),
    );
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Lưu trữ nội dung" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Xóa nội dung" })).not.toBeInTheDocument();
  });
});

describe("45-47. the confirmation says what will be destroyed", () => {
  it("confirms permanently, sends DELETE, and returns to the list", async () => {
    // The DELETE stub goes **first**: `stubFetch` matches on a URL substring in
    // order, and the detail route's `/api/pr/contents/` would otherwise answer
    // the delete with a cheerful 200.
    const stub = stubFetch([
      // 204, no body: after this there is no content to describe.
      { match: "/api/pr/contents/", method: "DELETE", status: 204 },
      ...detailRoutes({ stage: "IDEA", actions: [action("DELETE_CONTENT", "DANGER")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    // Nothing has been sent yet: the first press asks. And it asks the question
    // that matters - "Xóa nội dung này?" would be a confirmation of the wrong
    // thing for an operation that destroys drafts, reviews and approvals.
    // Step 1F.2.8: the shared `ConfirmDialog`, and the question now names the
    // item as well as the act - a row of cards is where a mis-click lands on
    // the wrong one, and "nội dung này" cannot tell somebody which.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(`Xóa vĩnh viễn ${CONTENT.code}?`)).toBeInTheDocument();
    expect(screen.getByText(/phiên bản nội dung, AI review, lịch sử duyệt/)).toBeInTheDocument();
    expect(screen.getByText(/không thể khôi phục/)).toBeInTheDocument();
    expect(sent(stub).some((call) => call.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));
    await waitFor(() => expect(sent(stub).some((call) => call.method === "DELETE")).toBe(true));
    await waitFor(() => expect(PUSHED).toContain("/pr/content"));
  });
});

describe("48-49. a refusal, and a delete that already happened", () => {
  it("turns the server's reason into a Vietnamese sentence", async () => {
    stubFetch([
      {
        match: "/api/pr/contents/",
        method: "DELETE",
        status: 403,
        body: {
          error: {
            code: "pr_forbidden",
            message: "This content may not be deleted by this actor",
            details: { reason: "already_produced" },
          },
        },
      },
      ...detailRoutes({ stage: "IDEA", actions: [action("DELETE_CONTENT", "DANGER")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));

    // Not the English the service raised - that sentence is written for the
    // Telegram bot and the logs, and this is the browser's half of the boundary.
    await waitFor(() =>
      expect(
        screen.getByText(/không thể xóa nội dung đã bước vào sản xuất/i),
      ).toBeInTheDocument(),
    );
  });

  it("shows the published refusal without offering somebody to ask", async () => {
    stubFetch([
      {
        match: "/api/pr/contents/",
        method: "DELETE",
        status: 409,
        body: {
          error: {
            code: "pr_published_content",
            message: "Published content cannot be permanently deleted",
            details: { reason: "published", workflow_stage: "PUBLISHED" },
          },
        },
      },
      ...detailRoutes({ stage: "IDEA", actions: [action("DELETE_CONTENT", "DANGER")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));

    await waitFor(() =>
      expect(screen.getByText(/Hãy lưu trữ nội dung thay thế/)).toBeInTheDocument(),
    );
  });

  it("handles a 404 from a delete somebody else already did", async () => {
    // The other half of the concurrency rule: one deleter wins and the second
    // gets the ordinary not-found, which is the truth - the id is gone. It is
    // rendered as a message rather than as a crash or a stuck spinner.
    stubFetch([
      {
        match: "/api/pr/contents/",
        method: "DELETE",
        status: 404,
        body: {
          error: {
            code: "pr_not_found",
            message: "No PR content item with that id",
            details: {},
          },
        },
      },
      ...detailRoutes({ stage: "IDEA", actions: [action("DELETE_CONTENT", "DANGER")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));

    await waitFor(() =>
      expect(screen.getByText("No PR content item with that id")).toBeInTheDocument(),
    );
    expect(PUSHED).not.toContain("/pr/content");
  });
});

describe("49b-49e. a recorded-work refusal is a notice, not a broken page", () => {
  /** Approved, waiting for a producer, and the delete is refused by the rule. */
  const handoffWithRecordedWork = (extra: Parameters<typeof stubFetch>[0] = []) => [
    {
      match: "/api/pr/contents/",
      method: "DELETE",
      status: 409,
      body: {
        error: {
          code: "pr_content_delete_blocked_recorded_work",
          message: "Content that has produced recorded work cannot be permanently deleted",
          details: { reason: "has_recorded_work", workflow_stage: "APPROVED" },
        },
      },
    },
    ...extra,
    ...detailRoutes({
      stage: "APPROVED",
      producer: null,
      actions: [action("CLAIM_PRODUCTION"), action("DELETE_CONTENT", "DANGER")],
    }),
  ];

  async function refuseDelete() {
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));
    await waitFor(() =>
      expect(
        screen.getByText("Không thể xóa nội dung này vì đã phát sinh công việc được ghi nhận."),
      ).toBeInTheDocument(),
    );
  }

  it("explains the rule beside the button and offers no retry", async () => {
    stubFetch(handoffWithRecordedWork());
    await refuseDelete();

    // The dialog closed: a rule that will refuse again is not something to
    // confirm a second time.
    expect(screen.queryByRole("button", { name: "Xóa vĩnh viễn" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Thử lại" })).not.toBeInTheDocument();
    // And the "reload and retry" hint for a moved record is not shown either.
    expect(screen.queryByText(/tải lại trang rồi thử lại/)).not.toBeInTheDocument();
    const notice = screen.getByRole("status");
    expect(within(notice).getByRole("button", { name: "Đóng" })).toBeInTheDocument();
  });

  it("leaves the content, its header and the production controls in place", async () => {
    stubFetch(handoffWithRecordedWork());
    await refuseDelete();

    // The page is the same page: title, stage, handoff state, and the next
    // action the person actually came here for.
    expect(screen.getByText(CONTENT.title)).toBeInTheDocument();
    expect(screen.getAllByText("Chờ nhận sản xuất").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Nhận sản xuất" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Sản phẩm" })).toBeInTheDocument();
    expect(PUSHED).not.toContain("/pr/content");
  });

  it("lets the person accept production straight after the refusal", async () => {
    const calls = stubFetch(
      handoffWithRecordedWork([
        {
          match: "/producer/claim",
          method: "POST",
          body: { content: { ...CONTENT, producer_user_id: SESSION.user_id } },
        },
      ]),
    );
    await refuseDelete();

    await userEvent.click(screen.getByRole("button", { name: "Nhận sản xuất" }));
    expect(dialog().getByText("Nhận sản xuất nội dung này?")).toBeInTheDocument();
    await confirm();
    await waitFor(() => {
      const claim = sent(calls).find((call) => call.url.includes("/producer/claim"));
      expect(claim?.method).toBe("POST");
    });
  });

  it("goes away on Đóng, and the button is still there", async () => {
    stubFetch(handoffWithRecordedWork());
    await refuseDelete();

    await userEvent.click(within(screen.getByRole("status")).getByRole("button", { name: "Đóng" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument();
  });

  it("reports a transient failure as an error, with the delete button still there to retry", async () => {
    stubFetch([
      {
        match: "/api/pr/contents/",
        method: "DELETE",
        status: 503,
        body: { error: { code: "http_error", message: "Service Unavailable", details: {} } },
      },
      ...detailRoutes({
        stage: "APPROVED",
        producer: null,
        actions: [action("CLAIM_PRODUCTION"), action("DELETE_CONTENT", "DANGER")],
      }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Xóa nội dung" }));
    await userEvent.click(screen.getByRole("button", { name: "Xóa vĩnh viễn" }));

    // A 503 is not a decision about the request: it is reported as an error,
    // not as a rule notice, and the delete button stays as the retry.
    await waitFor(() => expect(screen.getAllByText("Service Unavailable").length).toBeGreaterThan(0));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xóa nội dung" })).toBeEnabled();
    // And the production control underneath is untouched either way.
    expect(screen.getByRole("button", { name: "Nhận sản xuất" })).toBeInTheDocument();
  });
});

// ===========================================================================
// 53-56: THE PRODUCTION CARD
// ===========================================================================

describe("53-55. the production card says who, by name", () => {
  it("says 'Chưa có người nhận' when nobody holds it", async () => {
    stubFetch(detailRoutes({ producer: null, actions: [action("CLAIM_PRODUCTION")] }));
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());
    // Twice on the page - the header line and the card - and both are words
    // rather than a blank, because "nobody yet" is a real state here.
    expect(screen.getAllByText("Chưa có người nhận").length).toBeGreaterThan(0);
  });

  it("offers 'Nhận sản xuất' only when the server listed it", async () => {
    stubFetch(detailRoutes({ producer: null, actions: [action("CLAIM_PRODUCTION")] }));
    const { unmount } = renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Nhận sản xuất" })).toBeInTheDocument(),
    );
    unmount();

    stubFetch(detailRoutes({ producer: null, actions: [] }));
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Nhận sản xuất" })).not.toBeInTheDocument();
  });

  it("shows the assigned producer by name", async () => {
    stubFetch(detailRoutes({ producer: PRODUCER.user_id, submissions: [SUBMISSION] }));
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() => expect(screen.getAllByText("Phương Nhung").length).toBeGreaterThan(0));
    expect(screen.queryByText("Chưa có người nhận")).not.toBeInTheDocument();
  });

  it("claiming sends the claim and asks for nothing else", async () => {
    const stub = stubFetch([
      ...detailRoutes({ producer: null, actions: [action("CLAIM_PRODUCTION")] }),
      {
        match: "/producer/claim",
        method: "POST",
        body: { content: CONTENT, current_version: VERSION, targets: [], brand: null },
      },
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Nhận sản xuất" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Nhận sản xuất" }));
    // Step 1F.2.8. Claiming makes somebody responsible for producing a piece,
    // so it asks first - and the question says what changes hands.
    expect(dialog().getByText("Nhận sản xuất nội dung này?")).toBeInTheDocument();
    expect(sent(stub).some((call) => call.url.includes("/producer/claim"))).toBe(false);
    await confirm();

    await waitFor(() => {
      const claim = sent(stub).find((call) => call.url.includes("/producer/claim"));
      expect(claim?.method).toBe("POST");
      // No body: who is claiming is the session, exactly as with a review.
      expect(claim?.body).toBeUndefined();
    });
  });
});

describe("56. no UUID reaches the screen", () => {
  it("renders people and files as names and links, never as ids", async () => {
    stubFetch(
      detailRoutes({
        producer: PRODUCER.user_id,
        submissions: [SUBMISSION],
        actions: [action("ASSIGN_PRODUCER", "SECONDARY")],
      }),
    );
    const { container } = renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());

    // The ids are in the DOM as `<option value>` and `href`, which is what a
    // write sends; what is *displayed* must not be one.
    const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
    expect(uuid.test(container.textContent ?? "")).toBe(false);
  });
});

// ===========================================================================
// 57-58: THE ARTIFACT
// ===========================================================================

describe("57. the file reference is required before submitting", () => {
  it("keeps the submit button disabled until something is typed", async () => {
    stubFetch(
      detailRoutes({ producer: PRODUCER.user_id, actions: [action("SUBMIT_PRODUCTION")] }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Gửi duyệt nội bộ" })).toBeInTheDocument(),
    );

    expect(screen.getByRole("button", { name: "Gửi duyệt nội bộ" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Link / đường dẫn sản phẩm"), DRIVE);
    expect(screen.getByRole("button", { name: "Gửi duyệt nội bộ" })).toBeEnabled();
  });

  it("sends the type and the location the person chose", async () => {
    // Before `detailRoutes`, whose `/production` entry is a prefix of this URL.
    const stub = stubFetch([
      {
        match: "/production-submissions",
        method: "POST",
        body: {
          content_id: CONTENT.id,
          workflow_stage: "INTERNAL_REVIEW",
          producer_user_id: PRODUCER.user_id,
          submissions: [SUBMISSION],
        },
      },
      ...detailRoutes({ producer: PRODUCER.user_id, actions: [action("SUBMIT_PRODUCTION")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByLabelText("Link / đường dẫn sản phẩm")).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText("Loại file sản xuất"), "NAS_PATH");
    await userEvent.type(screen.getByLabelText("Link / đường dẫn sản phẩm"), "/volume1/PR/cut.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Gửi duyệt nội bộ" }));

    await waitFor(() => {
      const call = sent(stub).find((entry) => entry.url.includes("/production-submissions"));
      expect(call?.body).toMatchObject({
        artifact_type: "NAS_PATH",
        location: "/volume1/PR/cut.mp4",
      });
    });
  });
});

describe("58. an unusable reference is explained, not echoed", () => {
  it("renders the sentence for the server's reason code", async () => {
    stubFetch([
      {
        match: "/production-submissions",
        method: "POST",
        status: 422,
        body: {
          error: {
            code: "pr_validation_error",
            message: "Production artifact reference is not acceptable: not_a_drive_host",
            details: { reason: "not_a_drive_host" },
          },
        },
      },
      ...detailRoutes({ producer: PRODUCER.user_id, actions: [action("SUBMIT_PRODUCTION")] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByLabelText("Link / đường dẫn sản phẩm")).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText("Link / đường dẫn sản phẩm"), "https://dropbox.com/x");
    await userEvent.click(screen.getByRole("button", { name: "Gửi duyệt nội bộ" }));

    await waitFor(() =>
      expect(screen.getByText(/không phải Google Drive/)).toBeInTheDocument(),
    );
  });
});

// ===========================================================================
// 59-61: INTERNAL REVIEW
// ===========================================================================

describe("59-61. the internal review screen shows the cut and its history", () => {
  const REVISED = {
    ...SUBMISSION,
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    submission_no: 2,
    location: "https://drive.google.com/file/d/2/view",
    note: "Đã sửa nhạc nền",
  };
  const REVISION_DECISION = {
    id: "bbbbbbbb-0000-0000-0000-000000000001",
    approval_stage: "INTERNAL_REVIEW",
    decision: "REVISION_REQUIRED",
    version_reviewed: VERSION.version_no,
    reviewer_user_id: SESSION.user_id,
    comment: "Nhạc to quá",
    decided_at: "2026-08-10T04:00:00+00:00",
    production_submission_id: SUBMISSION.id,
  };

  it("shows the latest artifact, and the previous one with what was said about it", async () => {
    stubFetch(
      detailRoutes({
        stage: "INTERNAL_REVIEW",
        producer: PRODUCER.user_id,
        submissions: [REVISED, SUBMISSION],
        approvals: [REVISION_DECISION],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("Lần 2")).toBeInTheDocument());

    expect(screen.getByText("Lần 1")).toBeInTheDocument();
    // The decision sits against the cut it was about - paired on the submission
    // id the server sends, not on time.
    expect(screen.getByText("Yêu cầu sửa")).toBeInTheDocument();
    expect(screen.getByText("Đã sửa nhạc nền", { exact: false })).toBeInTheDocument();

    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toContain(REVISED.location);
  });

  it("labels the approval 'Duyệt nội bộ' at this gate, not 'Duyệt'", async () => {
    stubFetch(
      detailRoutes({
        stage: "INTERNAL_REVIEW",
        producer: PRODUCER.user_id,
        submissions: [SUBMISSION],
        actions: [
          { action: "APPROVAL", target_stage: null, decision: "APPROVED", emphasis: "PRIMARY" },
          {
            action: "APPROVAL",
            target_stage: null,
            decision: "REVISION_REQUIRED",
            emphasis: "SECONDARY",
          },
        ],
      }),
    );
    renderWithQuery(<ContentDetailPage />);

    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Duyệt nội bộ" }).length).toBeGreaterThan(0),
    );
    expect(screen.getByRole("button", { name: "Yêu cầu sửa" })).toBeInTheDocument();
    // The word for the script gates must not appear at this one.
    expect(screen.queryByRole("button", { name: "Duyệt" })).not.toBeInTheDocument();
  });

  it("renders a NAS path as text to copy rather than as a link", async () => {
    // `is_link` is the server's answer. A browser matching on the string would
    // make `//nas/share/cut.mp4` a protocol-relative URL and produce a link to
    // nowhere.
    stubFetch(
      detailRoutes({
        stage: "INTERNAL_REVIEW",
        producer: PRODUCER.user_id,
        submissions: [
          {
            ...SUBMISSION,
            artifact_type: "NAS_PATH",
            location: "/volume1/PR/cut.mp4",
            is_link: false,
          },
        ],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("/volume1/PR/cut.mp4")).toBeInTheDocument());

    expect(
      screen.queryByRole("link", { name: "/volume1/PR/cut.mp4" }),
    ).not.toBeInTheDocument();
  });
});


// ===========================================================================
// 62-73: THE APPROVED HANDOFF, AND UNDO
// ===========================================================================

describe("62-64. approved with nobody holding it", () => {
  it("says Chờ nhận sản xuất and Chưa có người nhận", async () => {
    stubFetch(
      detailRoutes({
        stage: "APPROVED",
        producer: null,
        actions: [action("CLAIM_PRODUCTION"), action("ASSIGN_PRODUCER", "SECONDARY")],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());

    // The stage is APPROVED and the screen never says "Đã duyệt" here: what a
    // person needs to know is that nobody has picked it up.
    expect(screen.getAllByText("Chờ nhận sản xuất").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Chưa có người nhận").length).toBeGreaterThan(0);
  });

  it("does not offer Bắt đầu sản xuất while nobody holds it", async () => {
    // The server withholds `START_PRODUCTION` and the panel renders what it is
    // given - there is no client-side rule about producers here to get wrong.
    stubFetch(
      detailRoutes({ stage: "APPROVED", producer: null, actions: [action("CLAIM_PRODUCTION")] }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Nhận sản xuất" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Bắt đầu sản xuất" })).not.toBeInTheDocument();
  });

  it("offers the assignment picker to management, by name", async () => {
    stubFetch(
      detailRoutes({
        stage: "APPROVED",
        producer: null,
        actions: [action("ASSIGN_PRODUCER", "SECONDARY")],
      }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByLabelText("Người sản xuất")).toBeInTheDocument(),
    );
    expect(screen.getByRole("option", { name: "Phương Nhung" })).toBeInTheDocument();
  });
});

describe("65-66. approved with a producer", () => {
  it("says Sẵn sàng sản xuất and offers Bắt đầu sản xuất", async () => {
    stubFetch(
      detailRoutes({
        stage: "APPROVED",
        producer: PRODUCER.user_id,
        actions: [action("START_PRODUCTION")],
      }),
    );
    const { container } = renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Bắt đầu sản xuất" })).toBeInTheDocument(),
    );
    expect(screen.getAllByText("Sẵn sàng sản xuất").length).toBeGreaterThan(0);
    await waitFor(() => expect(container.textContent).toContain("Phương Nhung"));
  });

  it("starting sends the start, and nothing else", async () => {
    const stub = stubFetch([
      {
        match: "/production/start",
        method: "POST",
        body: { content: CONTENT, current_version: VERSION, targets: [], brand: null },
      },
      ...detailRoutes({
        stage: "APPROVED",
        producer: PRODUCER.user_id,
        actions: [action("START_PRODUCTION")],
      }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Bắt đầu sản xuất" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Bắt đầu sản xuất" }));
    expect(dialog().getByText("Bắt đầu sản xuất nội dung này?")).toBeInTheDocument();
    await confirm();

    await waitFor(() => {
      const call = sent(stub).find((entry) => entry.url.includes("/production/start"));
      expect(call?.method).toBe("POST");
      expect(call?.body).toBeUndefined();
    });
  });
});

describe("67-70. Hoàn tác", () => {
  const undoAction = {
    action: "UNDO_LAST_ACTION",
    target_stage: "HEAD_REVIEW",
    decision: null,
    emphasis: "SECONDARY",
    undo_kind: "UNDO_HEAD_APPROVAL",
  };

  it("renders the specific decision it would reverse", async () => {
    stubFetch(detailRoutes({ stage: "APPROVED", actions: [undoAction] }));
    renderWithQuery(<ContentDetailPage />);

    // Not a bare "Hoàn tác": the server said which decision, and a person about
    // to reverse a Head approval should read that before pressing.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Hoàn tác duyệt Trưởng phòng" }),
      ).toBeInTheDocument(),
    );
  });

  it("explains which stage the content returns to, then sends the undo", async () => {
    const stub = stubFetch([
      {
        match: "/undo",
        method: "POST",
        body: { content: CONTENT, current_version: VERSION, targets: [], brand: null },
      },
      ...detailRoutes({ stage: "APPROVED", actions: [undoAction] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Hoàn tác duyệt Trưởng phòng" }),
      ).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: "Hoàn tác duyệt Trưởng phòng" }));
    expect(dialog().getByText("Hoàn tác thao tác vừa rồi?")).toBeInTheDocument();
    // The sentence is composed from a label and a stage name, so it is asserted
    // on the rendered text rather than as one node.
    const explanation = screen.getByText(/quay lại bước/).textContent ?? "";
    // The panel's own words for the stage - `lib/labels`, not the server's -
    // which is the rule this app has followed since Step 1E: codes cross the
    // wire, sentences are the client's.
    expect(explanation).toContain("Chờ duyệt Trưởng phòng");
    expect(explanation).toContain("Lịch sử thao tác vẫn được giữ lại");
    expect(sent(stub).some((call) => call.url.includes("/undo"))).toBe(false);

    await confirm();
    await waitFor(() =>
      expect(sent(stub).some((call) => call.url.includes("/undo"))).toBe(true),
    );
  });

  it("renders nothing when the server does not offer it", async () => {
    // Blocked because production already has the piece - a rule the browser
    // does not know and must not try to reproduce.
    stubFetch(
      detailRoutes({ stage: "APPROVED", producer: PRODUCER.user_id, actions: [] }),
    );
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText("Sản xuất")).toBeInTheDocument());
    expect(screen.queryByText("Hoàn tác")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Hoàn tác duyệt/ }),
    ).not.toBeInTheDocument();
  });

  it("renders a readable sentence when the undo is refused", async () => {
    stubFetch([
      {
        match: "/undo",
        method: "POST",
        status: 409,
        body: {
          error: {
            code: "pr_undo_not_available",
            message: "This action can no longer be undone",
            details: { reason: "production_handed_off" },
          },
        },
      },
      ...detailRoutes({ stage: "APPROVED", actions: [undoAction] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Hoàn tác duyệt Trưởng phòng" }),
      ).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Hoàn tác duyệt Trưởng phòng" }));
    await confirm();

    await waitFor(() =>
      expect(screen.getByText(/đã được bàn giao cho sản xuất/)).toBeInTheDocument(),
    );
  });
});

describe("71-73. the history keeps both halves", () => {
  const HISTORY = [
    {
      id: "cccccccc-0000-0000-0000-000000000001",
      from_stage: "TEAM_LEAD_REVIEW",
      to_stage: "HEAD_REVIEW",
      trigger: "HUMAN_APPROVAL",
      actor_user_id: SESSION.user_id,
      approval_event_id: "dddddddd-0000-0000-0000-000000000001",
      production_submission_id: null,
      reverses_event_id: null,
      reversed_by_event_id: null,
      note: null,
      created_at: "2026-08-10T07:05:00+00:00",
    },
    {
      id: "cccccccc-0000-0000-0000-000000000002",
      from_stage: "HEAD_REVIEW",
      to_stage: "APPROVED",
      trigger: "HUMAN_APPROVAL",
      actor_user_id: SESSION.user_id,
      approval_event_id: "dddddddd-0000-0000-0000-000000000002",
      production_submission_id: null,
      reverses_event_id: null,
      reversed_by_event_id: "cccccccc-0000-0000-0000-000000000003",
      note: null,
      created_at: "2026-08-10T07:06:00+00:00",
    },
    {
      id: "cccccccc-0000-0000-0000-000000000003",
      from_stage: "APPROVED",
      to_stage: "HEAD_REVIEW",
      trigger: "UNDO",
      actor_user_id: SESSION.user_id,
      approval_event_id: null,
      production_submission_id: null,
      reverses_event_id: "cccccccc-0000-0000-0000-000000000002",
      reversed_by_event_id: null,
      note: "undo:UNDO_HEAD_APPROVAL",
      created_at: "2026-08-10T07:08:00+00:00",
    },
  ];

  it("shows the original move, marked as undone, and the reversal under it", async () => {
    stubFetch([
      { match: "/history", body: HISTORY },
      ...detailRoutes({ stage: "HEAD_REVIEW", actions: [] }),
    ]);
    renderWithQuery(<ContentDetailPage />);
    await waitFor(() => expect(screen.getByText(CONTENT.title)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: "Lịch sử" }));

    await waitFor(() => expect(screen.getByText("Lịch sử thao tác")).toBeInTheDocument());
    // The approval that was taken back is still listed - labelled, not deleted.
    expect(screen.getByText("Đã hoàn tác")).toBeInTheDocument();
    const trail = screen.getByText("Lịch sử thao tác").parentElement?.textContent ?? "";
    expect(trail).toContain("Hoàn tác về Chờ duyệt Trưởng phòng");
    // Three rows: the two approvals and the reversal. Nothing was removed, so
    // the stage the piece went back to appears both going and coming.
    expect(trail.match(/Chờ duyệt Trưởng phòng/g)?.length).toBeGreaterThanOrEqual(2);
  });
});
