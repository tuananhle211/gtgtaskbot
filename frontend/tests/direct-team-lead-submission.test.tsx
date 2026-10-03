/**
 * Step 1F.2.10 - "Gửi duyệt Trưởng nhóm" beside "Gửi đi AI review".
 *
 * Numbered 20-28 after the frontend half of the step's test matrix. The
 * question is the usual one for this layer: the panel draws what the server
 * listed, sends the action the server named, and never decides for itself that
 * a script may skip the AI - and, for this step in particular, never dresses the
 * skip up as an AI result.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";

import { transitionConfirmation } from "@/lib/confirmations";
import { stageGuidance, transitionHistoryLabel, transitionLabel } from "@/lib/labels";
import ContentDetailPage from "@/app/pr/content/[id]/page";
import {
  CONTENT,
  SESSION,
  VERSION,
  cancelDialog,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
} from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: CONTENT.id }),
  usePathname: () => "/pr",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
}));

const SRC = path.resolve(__dirname, "../src");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

type Calls = Array<{ url: string; method: string; body: unknown }>;
const callsOf = (stub: unknown) => (stub as { calls: Calls }).calls;

const SCRIPTED = { ...CONTENT, workflow_stage: "SCRIPTING" };

const transition = (target: string, emphasis: string) => ({
  action: "TRANSITION",
  target_stage: target,
  decision: null,
  emphasis,
  undo_kind: null,
});
const EDIT = { action: "EDIT_CONTENT", target_stage: null, decision: null, emphasis: "SECONDARY" };

/** Both submissions, as the server offers them at SCRIPTING. */
const BOTH = [
  transition("AI_REVIEW", "PRIMARY"),
  transition("TEAM_LEAD_REVIEW", "SECONDARY"),
  EDIT,
];

const context = (content: Record<string, unknown> = SCRIPTED) => ({
  content,
  current_version: VERSION,
  targets: [],
  tasks: [],
  ai_review: null,
  ai_reviews_for_version: [],
  approvals: [],
});

const AI_STATE = {
  run: null,
  review: null,
  active: false,
  can_retry: false,
  policy_packs: [],
  policy_citations: [],
};

const PEOPLE = [{ user_id: SESSION.user_id, full_name: "Trần Minh Anh", role: "EMPLOYEE" }];

/** Every route a detail render needs, most specific first. */
const routes = (
  actions: Array<Record<string, unknown>>,
  extra: Array<{ match: string; method?: string; status?: number; body?: unknown }> = [],
  content: Record<string, unknown> = SCRIPTED,
) => [
  ...extra,
  {
    match: "/available-actions",
    body: {
      content_id: CONTENT.id,
      workflow_stage: content.workflow_stage,
      available_actions: actions,
    },
  },
  { match: "/ai-review", body: AI_STATE },
  { match: "/review-context", body: context(content) },
  { match: "/versions", method: "GET", body: [VERSION] },
  { match: "/history", body: [] },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/dashboard", body: {} },
  { match: "/api/pr/contents/", body: { content, current_version: VERSION, targets: [] } },
];

const panel = async () => {
  const heading = await screen.findByText("Việc cần làm tiếp");
  return heading.closest("section")!;
};

describe("20-21. a script-ready item offers both submissions, from the server", () => {
  it("draws AI review as the primary button and the Team Lead one beside it", async () => {
    stubFetch(routes(BOTH));
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    // The guidance no longer presumes the AI path.
    expect(within(section).getByText("Viết xong kịch bản thì gửi duyệt.")).toBeInTheDocument();
    const ai = within(section).getAllByRole("button", { name: "Gửi đi AI review" })[0];
    const lead = within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" });
    expect(within(section).getByRole("button", { name: "Chỉnh sửa nội dung" })).toBeInTheDocument();
    // Hierarchy, as the server's emphasis says: primary first, secondary after.
    expect(ai.compareDocumentPosition(lead) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The bypass is not dressed as destructive: it sits in the forward row, not
    // behind "Thao tác khác", and it is not a danger button.
    expect(within(section).queryByText("⋯ Thao tác khác")).not.toBeInTheDocument();
    expect(lead.className).not.toMatch(/danger/);
  });

  it("22. draws no Team Lead button when the server does not offer it", async () => {
    // The same stage; the server withheld the action - no draft, no channel,
    // no right. The panel does not reason about why.
    stubFetch(routes([transition("AI_REVIEW", "PRIMARY"), EDIT]));
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    expect(
      within(section).getAllByRole("button", { name: "Gửi đi AI review" }).length,
    ).toBeGreaterThan(0);
    expect(
      within(section).queryByRole("button", { name: "Gửi duyệt Trưởng nhóm" }),
    ).not.toBeInTheDocument();
    // And the page owns no rule of its own that could draw it anyway.
    const source = read("app/pr/content/[id]/page.tsx");
    for (const forbidden of [
      '=== "SCRIPTING"',
      'TEAM_LEAD_REVIEW"',
      "skipAiReview",
      "canSubmitTeamLead",
    ]) {
      expect(source, forbidden).not.toContain(forbidden);
    }
  });
});

describe("23-25. each button sends its own action, and success refreshes the item", () => {
  it("23. the AI button still posts the AI_REVIEW transition", async () => {
    const stub = stubFetch(
      routes(BOTH, [
        {
          match: "/transition",
          method: "POST",
          body: {
            content: { ...SCRIPTED, workflow_stage: "AI_REVIEW" },
            current_version: VERSION,
            targets: [],
          },
        },
      ]),
    );
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    await userEvent.click(within(section).getAllByRole("button", { name: "Gửi đi AI review" })[0]);
    await confirm();
    await waitFor(() => expect(callsOf(stub).some((call) => call.method === "POST")).toBe(true));
    const sent = callsOf(stub).find((call) => call.method === "POST")!;
    expect(sent.url).toContain(`/api/pr/contents/${CONTENT.id}/transition`);
    expect(sent.body).toEqual({ target_stage: "AI_REVIEW" });
  });

  it("24. the Team Lead button posts the TEAM_LEAD_REVIEW transition, after a plain confirmation", async () => {
    const stub = stubFetch(
      routes(BOTH, [
        {
          match: "/transition",
          method: "POST",
          body: {
            content: { ...SCRIPTED, workflow_stage: "TEAM_LEAD_REVIEW" },
            current_version: VERSION,
            targets: [],
          },
        },
      ]),
    );
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    await userEvent.click(within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" }));

    // The dialog says the one thing the button did not: the AI review is
    // skipped. In business words, with no technical flag to acknowledge.
    expect(dialog().getByText("Gửi thẳng Trưởng nhóm duyệt?")).toBeInTheDocument();
    expect(
      dialog().getByText(
        "Nội dung này sẽ bỏ qua bước AI review và chuyển sang Chờ duyệt Trưởng nhóm.",
      ),
    ).toBeInTheDocument();
    expect(dialog().getByRole("button", { name: "Gửi duyệt" })).toBeInTheDocument();
    // Nothing was sent by opening it.
    expect(callsOf(stub).some((call) => call.method === "POST")).toBe(false);

    await confirm();
    await waitFor(() => expect(callsOf(stub).some((call) => call.method === "POST")).toBe(true));
    const sent = callsOf(stub).find((call) => call.method === "POST")!;
    expect(sent.url).toContain(`/api/pr/contents/${CONTENT.id}/transition`);
    // The same endpoint and shape as every other move: no separate "skip"
    // flag, no AI field - the server decides what the edge means.
    expect(sent.body).toEqual({ target_stage: "TEAM_LEAD_REVIEW" });
    expect(JSON.stringify(sent.body)).not.toMatch(/ai|skip|bypass/i);
  });

  it("24a. cancelling the confirmation sends nothing", async () => {
    const stub = stubFetch(routes(BOTH));
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    await userEvent.click(within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" }));
    await cancelDialog();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(callsOf(stub).some((call) => call.method === "POST")).toBe(false);
  });

  it("25. success re-reads the actions and the item", async () => {
    let posted = false;
    const stub = stubFetch([
      {
        match: "/transition",
        method: "POST",
        body: {
          content: { ...SCRIPTED, workflow_stage: "TEAM_LEAD_REVIEW" },
          current_version: VERSION,
          targets: [],
        },
      },
      ...routes(BOTH),
    ]);
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    const before = callsOf(stub).filter((call) => call.url.includes("/available-actions")).length;
    await userEvent.click(within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" }));
    await confirm();
    await waitFor(() => {
      posted = callsOf(stub).some((call) => call.method === "POST");
      expect(posted).toBe(true);
    });
    // Invalidated after the write: the actions and the detail are asked again,
    // so the buttons the server now offers replace the ones it offered before.
    await waitFor(() =>
      expect(
        callsOf(stub).filter((call) => call.url.includes("/available-actions")).length,
      ).toBeGreaterThan(before),
    );
    await waitFor(() =>
      expect(
        callsOf(stub).filter((call) => call.url.includes("/review-context")).length,
      ).toBeGreaterThan(1),
    );
  });

  it("25a. a refusal is shown in the server's words, in the dialog", async () => {
    stubFetch(
      routes(BOTH, [
        {
          match: "/transition",
          method: "POST",
          status: 409,
          body: {
            error: {
              code: "pr_invalid_transition",
              message: "Không thể gửi duyệt Trưởng nhóm ở trạng thái hiện tại.",
              details: { current: "AI_REVIEW", target: "TEAM_LEAD_REVIEW", allowed: [] },
            },
          },
        },
      ]),
    );
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    await userEvent.click(within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" }));
    await confirm();
    await waitFor(() =>
      expect(dialog().getByText(/Không thể gửi duyệt Trưởng nhóm/)).toBeInTheDocument(),
    );
  });
});

describe("26. after the list refreshes, the item sits under the Team Lead column", () => {
  it("is a server fact the board reads back, not a move the browser makes", () => {
    // The board renders lanes from the server's `lane` queries and never
    // relocates a card itself - so "moved to Chờ duyệt Trưởng nhóm" is what the
    // refetch returns, and the page holds no table that could put it elsewhere.
    const board = read("app/pr/content/page.tsx");
    for (const forbidden of ["CONTENT_TRANSITIONS", "nextStage", "canTransition", 'AI_REVIEW"]']) {
      expect(board, forbidden).not.toContain(forbidden);
    }
    // And the detail page invalidates the board after any move.
    const detail = read("app/pr/content/[id]/page.tsx");
    expect(detail).toContain('queryKey: ["content-board"]');
  });
});

describe("27. no fake AI review appears anywhere", () => {
  it("the AI panel says there is no review, and the history says the AI was skipped", async () => {
    const atLead = { ...CONTENT, workflow_stage: "TEAM_LEAD_REVIEW" };
    stubFetch(
      routes(
        [],
        [
          {
            match: "/history",
            body: [
              {
                id: "e1",
                from_stage: "IDEA",
                to_stage: "BRIEFING",
                trigger: "MANUAL",
                actor_user_id: SESSION.user_id,
                approval_event_id: null,
                production_submission_id: null,
                reverses_event_id: null,
                reversed_by_event_id: null,
                note: null,
                created_at: "2026-09-21T01:00:00+00:00",
              },
              {
                id: "e2",
                from_stage: "BRIEFING",
                to_stage: "SCRIPTING",
                trigger: "MANUAL",
                actor_user_id: SESSION.user_id,
                approval_event_id: null,
                production_submission_id: null,
                reverses_event_id: null,
                reversed_by_event_id: null,
                note: null,
                created_at: "2026-09-21T01:10:00+00:00",
              },
              {
                id: "e3",
                from_stage: "SCRIPTING",
                to_stage: "TEAM_LEAD_REVIEW",
                trigger: "MANUAL",
                actor_user_id: SESSION.user_id,
                approval_event_id: null,
                production_submission_id: null,
                reverses_event_id: null,
                reversed_by_event_id: null,
                note: null,
                created_at: "2026-09-21T01:20:00+00:00",
              },
            ],
          },
        ],
        atLead,
      ),
    );
    renderWithQuery(<ContentDetailPage />);
    await screen.findByText("Việc cần làm tiếp");

    await userEvent.click(screen.getByRole("tab", { name: "Duyệt" }));
    await waitFor(() =>
      expect(screen.getByText("Chưa có AI review cho phiên bản này.")).toBeInTheDocument(),
    );
    for (const fake of ["Đạt", "PASS", "AI đã duyệt", "Đã kiểm tra chính sách"]) {
      expect(screen.queryByText(fake)).not.toBeInTheDocument();
    }

    await userEvent.click(screen.getByRole("tab", { name: "Lịch sử" }));
    const line = await screen.findByText(/Bỏ qua AI review, gửi duyệt Trưởng nhóm/);
    expect(line).toHaveTextContent("Trần Minh Anh · Bỏ qua AI review, gửi duyệt Trưởng nhóm");
    // Ordinary moves keep their arrow; nothing in the list claims a verdict.
    expect(screen.getByText(/→ Brief/)).toBeInTheDocument();
    expect(screen.queryByText(/AI review xong/)).not.toBeInTheDocument();
  });

  it("words each submission by its edge, so the two paths never read the same", () => {
    const row = (from_stage: string, to_stage: string, trigger: string) => ({
      from_stage,
      to_stage,
      trigger,
    });
    expect(transitionHistoryLabel(row("SCRIPTING", "AI_REVIEW", "MANUAL"))).toBe("Gửi AI review");
    expect(transitionHistoryLabel(row("AI_REVIEW", "TEAM_LEAD_REVIEW", "AI_REVIEW"))).toBe(
      "AI review xong, gửi duyệt Trưởng nhóm",
    );
    expect(transitionHistoryLabel(row("AI_REVIEW", "SCRIPTING", "AI_REVIEW"))).toBe(
      "AI review yêu cầu chỉnh sửa",
    );
    expect(transitionHistoryLabel(row("SCRIPTING", "TEAM_LEAD_REVIEW", "MANUAL"))).toBe(
      "Bỏ qua AI review, gửi duyệt Trưởng nhóm",
    );
    // An undo of a revision request takes the same edge and is not a bypass.
    expect(transitionHistoryLabel(row("SCRIPTING", "TEAM_LEAD_REVIEW", "UNDO"))).toBe(
      "Hoàn tác về Chờ duyệt Trưởng nhóm",
    );
    expect(transitionHistoryLabel(row("TEAM_LEAD_REVIEW", "HEAD_REVIEW", "HUMAN_APPROVAL"))).toBe(
      "→ Chờ duyệt Trưởng phòng",
    );
  });

  it("names the business step on the button, not the mechanism", () => {
    expect(transitionLabel("TEAM_LEAD_REVIEW")).toBe("Gửi duyệt Trưởng nhóm");
    expect(transitionLabel("AI_REVIEW")).toBe("Gửi đi AI review");
    expect(stageGuidance("SCRIPTING")).toBe("Viết xong kịch bản thì gửi duyệt.");
    const spec = transitionConfirmation("TEAM_LEAD_REVIEW");
    expect(spec.variant).toBe("primary");
    expect(spec.confirmLabel).toBe("Gửi duyệt");
    for (const wrong of ["Skip", 'Bỏ qua AI"', "flag"]) {
      expect(transitionLabel("TEAM_LEAD_REVIEW")).not.toContain(wrong);
    }
  });
});

describe("28. the action row wraps on a narrow screen", () => {
  it("lays the buttons out in a wrapping flex row with no fixed width", async () => {
    stubFetch(routes(BOTH));
    renderWithQuery(<ContentDetailPage />);
    const section = await panel();
    const lead = within(section).getByRole("button", { name: "Gửi duyệt Trưởng nhóm" });
    const row = lead.parentElement!;
    expect(row.className).toContain("flex");
    expect(row.className).toContain("flex-wrap");
    expect(row.className).toContain("gap-2");
    // Neither button pins a width the row could not shrink below, so at 320px
    // the second button drops to the next line rather than overflowing.
    for (const button of within(row).getAllByRole("button")) {
      expect(button.className).not.toMatch(/\bw-\[/);
      expect(button.className).not.toMatch(/min-w-\[/);
      expect(button.className).not.toContain("whitespace-nowrap");
    }
    // The sticky phone bar carries only the primary move; the secondary one is
    // reached in the row above, which is what keeps the bar one tap wide.
    const bars = section.querySelectorAll(".sm\\:hidden");
    expect(bars.length).toBe(1);
    expect(
      within(bars[0] as HTMLElement).getByRole("button", { name: "Gửi đi AI review" }),
    ).toBeInTheDocument();
    expect(
      within(bars[0] as HTMLElement).queryByRole("button", { name: "Gửi duyệt Trưởng nhóm" }),
    ).not.toBeInTheDocument();
  });
});
