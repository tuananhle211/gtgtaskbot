/**
 * "Giao sản xuất" on the PR board (2026-10-20): a card at a hand-off stage
 * (APPROVED / PRODUCTION) gets a button under it for whoever may assign
 * production; it opens a picker and sends the piece's own producer write.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { STAGE_ORDER } from "@/lib/labels";
import type { ContentSummary } from "@/lib/api";
import { CONTENT, renderWithQuery, SESSION, settleLanes, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams("group=PRODUCTION") };
const URL_BAR = urlStore(SEARCH);

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/pr/content",
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({ replace: (url: string) => URL_BAR.navigate(url), push: vi.fn() }),
}));

const { default: ContentBoardPage } = await import("@/app/pr/content/page");

const PRODUCER = "22222222-2222-2222-2222-222222222222";

function item(id: string, title: string, workflow_stage: string): ContentSummary {
  return {
    ...(CONTENT as unknown as ContentSummary),
    id,
    code: `CNT-2026-${id.slice(0, 6)}`,
    title,
    workflow_stage,
    producer_user_id: null,
    production_state: workflow_stage === "APPROVED" ? "WAITING_FOR_PRODUCER" : null,
    approvable_by_me: false,
  };
}

const WAITING = item("aaaaaaaa-0000-0000-0000-000000000001", "Chờ người dựng", "APPROVED");

function board(items: ContentSummary[]) {
  return {
    items,
    total: items.length,
    scope: "ALL",
    stage_counts: STAGE_ORDER.map((stage) => ({
      stage,
      count: items.filter((row) => row.workflow_stage === stage).length,
    })),
    production_state_counts: [],
    limit: 20,
    offset: 0,
  };
}

function open(mayAssign: boolean) {
  const stub = stubFetch([
    { match: "/api/auth/session", body: SESSION },
    {
      match: "/effective-permissions",
      body: {
        user_id: SESSION.user_id,
        capabilities: [
          { capability: "PR_PRODUCTION_ASSIGN", label: "Phân công sản xuất", allowed: mayAssign },
        ],
      },
    },
    { match: "/producer", method: "POST", body: { ...CONTENT, producer_user_id: PRODUCER } },
    { match: "/api/pr/brands", body: [] },
    { match: "/api/pr/platforms", body: [] },
    { match: "/api/pr/channels", body: [] },
    {
      match: "/api/pr/people",
      body: [
        { user_id: SESSION.user_id, full_name: "Lê Trưởng Nhóm", role: "TEAM_LEAD" },
        { user_id: PRODUCER, full_name: "Minh Dựng", role: "EMPLOYEE" },
      ],
    },
    { match: "/api/pr/contents/board", body: board([WAITING]) },
  ]);
  renderWithQuery(<ContentBoardPage />);
  return stub;
}

describe("Giao sản xuất on the PR board", () => {
  it("assigns a producer from the card", async () => {
    const stub = open(true);
    await settleLanes();
    const button = await screen.findByRole("button", { name: `Giao sản xuất · ${WAITING.title}` });
    await userEvent.click(button);
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.selectOptions(dialog.getByRole("combobox"), PRODUCER);
    await userEvent.click(dialog.getByRole("button", { name: "Giao" }));
    // The confirmation opens over the picker: press its accept button.
    const accept = document.querySelector<HTMLElement>("[data-confirm-accept]");
    expect(accept).not.toBeNull();
    await userEvent.click(accept as HTMLElement);
    await waitFor(() => {
      const sent = (stub as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls.find(
        (call) => call.method === "POST" && call.url.includes(`/api/pr/contents/${WAITING.id}/producer`),
      );
      expect(sent?.body).toEqual({ producer_user_id: PRODUCER });
    });
  });

  it("is not offered to somebody who may not assign production", async () => {
    open(false);
    await settleLanes();
    expect(await screen.findByText(WAITING.title)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Giao sản xuất ·/ })).not.toBeInTheDocument();
  });
});
