/**
 * Step 1F.2.8, the confirmation policy: what asks, what does not, and how.
 *
 * Numbered 167-173.
 *
 * Three layers, and they are separated because they fail for different reasons.
 *
 * **167-169 are the component**, rendered on its own. Focus, keyboard, the
 * double-submit latch and the two variants are properties of `ConfirmDialog`
 * and asserting them through a page would make them fail for fifteen unrelated
 * reasons.
 *
 * **170-172 are the policy in place**: an approval, a claim, an assignment, a
 * revocation and a disconnect each ask, in their own screen, with copy that
 * names what and whom. These are the tests that would catch a button being
 * wired straight to a mutation again.
 *
 * **173 is the regression strategy**, and it is deliberately not a sweep for
 * `<button>`. Such a test fails on every tab strip and passes on any mutation
 * hidden behind a `div onClick` - brittle in one direction and blind in the
 * other. What is asserted instead is: no page uses the browser's own
 * `confirm()`; every screen that mutates imports the shared component; and the
 * explicit action inventory in `lib/confirmations` is complete in shape - each
 * entry names a policy, and each unconfirmed one carries a reason. The
 * inventory is a short list a reviewer reads in a diff, which is the only kind
 * of coverage rule that survives a year.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { ConfirmDialog, type ConfirmSpec } from "@/components/confirm";
import {
  ACTION_INVENTORY,
  assignProducerConfirmation,
} from "@/lib/confirmations";
import {
  CONTENT,
  SESSION,
  VERSION,
  cancelDialog,
  channelsNavigation,
  confirm,
  dialog,
  renderWithQuery,
  stubFetch,
} from "./helpers";

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const read = (relative: string) =>
  readFileSync(path.join(SRC, relative), "utf8");

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });

/* Step 1F.2.9. `/pr/channels` keeps its selection in `?channel=`, so a router
   whose `replace` is a spy leaves the screen exactly as it was and the channel
   never opens. `channelsNavigation` provides one that really navigates; the
   `useParams` override is what the content detail page in this file needs. */
const NAV = channelsNavigation("/pr/content");
vi.mock("next/navigation", () => ({
  ...NAV.module,
  useParams: () => ({ id: CONTENT.id }),
}));

const { default: ContentDetailPage } =
  await import("@/app/pr/content/[id]/page");
const { default: PermissionsPage } = await import("@/app/pr/permissions/page");
const { default: ChannelsPage } = await import("@/app/pr/channels/page");

// ===========================================================================
// The component, on its own
// ===========================================================================

const SPEC: ConfirmSpec = {
  title: "Duyệt nội dung này?",
  description:
    "Nội dung sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.",
  confirmLabel: "Duyệt",
};

function Harness({
  spec = SPEC,
  pending = false,
  onConfirm = vi.fn(),
  onCancel = vi.fn(),
}: {
  spec?: ConfirmSpec;
  pending?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
}) {
  return (
    <>
      <button type="button">Nút mở</button>
      <ConfirmDialog
        open
        spec={spec}
        pending={pending}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    </>
  );
}

describe("167. the dialog is reachable and escapable from the keyboard", () => {
  it("announces itself as a modal, labelled and described by its own copy", () => {
    renderWithQuery(<Harness />);
    const box = screen.getByRole("dialog");

    expect(box).toHaveAttribute("aria-modal", "true");
    // Not a hard-coded id: the point is that the two attributes *point at* the
    // title and the body, which is what a screen reader reads on open.
    const labelled = document.getElementById(
      box.getAttribute("aria-labelledby") ?? "",
    );
    const described = document.getElementById(
      box.getAttribute("aria-describedby") ?? "",
    );
    expect(labelled).toHaveTextContent(SPEC.title);
    expect(described).toHaveTextContent(SPEC.description);
  });

  it("moves focus into the dialog and traps Tab inside it", async () => {
    renderWithQuery(<Harness />);
    const box = screen.getByRole("dialog");
    // An ordinary action focuses its confirm button: the person pressed a
    // button meaning to do the thing, and the dialog is telling them what it is.
    expect(box.querySelector("[data-confirm-accept]")).toHaveFocus();

    await userEvent.tab();
    expect(box.contains(document.activeElement)).toBe(true);
    await userEvent.tab();
    expect(box.contains(document.activeElement)).toBe(true);
    // The button behind the dialog is never reached by tabbing.
    expect(screen.getByRole("button", { name: "Nút mở" })).not.toHaveFocus();
  });

  it("focuses the cancel button on a destructive dialog", () => {
    renderWithQuery(
      <Harness
        spec={{ ...SPEC, variant: "destructive", confirmLabel: "Từ chối" }}
      />,
    );
    // The one opinion this component holds: a stray Enter on a destructive
    // dialog must cancel, not confirm.
    expect(
      screen.getByRole("dialog").querySelector("[data-confirm-cancel]"),
    ).toHaveFocus();
  });

  it("cancels on Escape, and does not while a request is in flight", async () => {
    const onCancel = vi.fn();
    const { rerender } = renderWithQuery(<Harness onCancel={onCancel} />);

    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);

    rerender(<Harness onCancel={onCancel} pending />);
    await userEvent.keyboard("{Escape}");
    // Still one. Dismissing mid-flight would leave somebody with no idea
    // whether the thing happened.
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("returns focus to whatever opened it", async () => {
    function Toggle() {
      const [open, setOpen] = (
        globalThis as unknown as { __useState: typeof import("react").useState }
      ).__useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Mở hộp thoại
          </button>
          <ConfirmDialog
            open={open}
            spec={SPEC}
            onConfirm={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        </>
      );
    }
    const react = await import("react");
    (
      globalThis as unknown as { __useState: typeof react.useState }
    ).__useState = react.useState;

    renderWithQuery(<Toggle />);
    const opener = screen.getByRole("button", { name: "Mở hộp thoại" });
    await userEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await cancelDialog();
    await waitFor(() => expect(opener).toHaveFocus());
  });
});

describe("167b. the dialog is a viewport modal, not a child of whatever opened it", () => {
  it("mounts on document.body rather than inside its trigger's subtree", () => {
    const { container } = renderWithQuery(<Harness />);
    const box = screen.getByRole("dialog");

    // The bug this pins down: both sticky action bars in this app carry
    // `backdrop-blur`, and `backdrop-filter` makes an element a **containing
    // block for its `position: fixed` descendants**. The bulk bar contains a
    // `ConfirmButton`, so `fixed inset-0` was resolving against a sixty-pixel
    // strip at the bottom of the window instead of against the viewport - the
    // dialog appeared jammed into the bar, and no `z-index` could lift it out.
    //
    // Asserted as "not in the trigger's tree, and on `body`" rather than by
    // checking a class, because that is the property that actually fixes it and
    // the one a future caller could break by rendering a dialog somewhere new.
    expect(container.contains(box)).toBe(false);
    expect(box.closest("body")).toBe(document.body);
    expect(box.parentElement?.parentElement).toBe(document.body);
  });

  it("escapes a blurred sticky ancestor - the exact shape that broke it", () => {
    function InsideABlurredBar() {
      return (
        <div
          className="sticky bottom-0 z-20 backdrop-blur"
          data-testid="sticky-bar"
        >
          <ConfirmDialog
            open
            spec={SPEC}
            onConfirm={vi.fn()}
            onCancel={vi.fn()}
          />
        </div>
      );
    }
    renderWithQuery(<InsideABlurredBar />);

    expect(
      screen.getByTestId("sticky-bar").contains(screen.getByRole("dialog")),
    ).toBe(false);
  });

  it("carries the two-layout contract on the overlay and the panel", () => {
    // jsdom has no layout engine and no media queries, so the class contract is
    // the only checkable artefact here - the pixel behaviour is verified in a
    // real browser at four viewports, see the milestone notes. Kept to the few
    // tokens that *encode the requirement* rather than pinning a whole string.
    renderWithQuery(<Harness />);
    const box = screen.getByRole("dialog");
    const overlay = box.parentElement!;

    // Full-screen scrim, above every sticky bar (they sit at z-10 / z-20).
    expect(overlay.className).toContain("fixed");
    expect(overlay.className).toContain("inset-0");
    expect(overlay.className).toContain("z-[100]");
    // Bottom sheet on a phone, centred card from `md` up - centred by the *md*
    // breakpoint specifically, which is what "desktop/laptop" asked for.
    expect(overlay.className).toContain("items-end");
    expect(overlay.className).toContain("md:items-center");

    // A 512px card with 16px corners on desktop; a full-width sheet with
    // rounded top corners on a phone.
    expect(box.className).toContain("md:max-w-lg");
    expect(box.className).toContain("md:rounded-2xl");
    expect(box.className).toContain("rounded-t-2xl");
    expect(box.className).toContain("w-full");
  });

  it("keeps the footer out of the scrolling body", () => {
    renderWithQuery(
      <Harness
        spec={{ ...SPEC, count: 16, details: "A · B · C và 13 nội dung khác" }}
      />,
    );
    const box = screen.getByRole("dialog");
    const footer = box.querySelector("footer")!;

    // A dialog listing titles and an error still has its buttons on screen: the
    // body scrolls, the footer does not move.
    expect(footer).toBeInTheDocument();
    expect(footer.contains(box.querySelector("[data-confirm-accept]"))).toBe(
      true,
    );
    expect(box.querySelector(".overflow-y-auto")!.contains(footer)).toBe(false);
    // Right-aligned from `sm` up; stacked and full width below it.
    expect(footer.className).toContain("sm:justify-end");
    expect(footer.className).toContain("flex-col-reverse");
    // The home indicator on a phone.
    expect(footer.className).toContain("env(safe-area-inset-bottom)");
  });

  it("renders the bulk shape compactly: title, body, count, sample, footer", () => {
    renderWithQuery(
      <Harness
        spec={{
          title: "Duyệt 16 nội dung?",
          description:
            "16 nội dung ở bước Chờ duyệt nội bộ sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.",
          confirmLabel: "Duyệt 16 nội dung",
          count: 16,
          details: "A · B · C · D · E và 11 nội dung khác",
        }}
      />,
    );
    const box = screen.getByRole("dialog");

    expect(
      within(box).getByRole("heading", { name: "Duyệt 16 nội dung?" }),
    ).toBeInTheDocument();
    expect(
      within(box).getByText(/Chờ duyệt nội bộ sẽ được ghi nhận/),
    ).toBeInTheDocument();
    expect(within(box).getByText("Số lượng: 16 nội dung")).toBeInTheDocument();
    expect(within(box).getByText(/và 11 nội dung khác/)).toBeInTheDocument();
    // Cancel first in the DOM, confirm second - so tab order and the reading
    // order left-to-right on desktop both put the action last.
    const buttons = within(box.querySelector("footer")!).getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveAttribute("data-confirm-cancel");
    expect(buttons[1]).toHaveAttribute("data-confirm-accept");
  });
});

describe("168. the dialog cannot be submitted twice", () => {
  it("fires once however many times the button is pressed", async () => {
    const onConfirm = vi.fn();
    renderWithQuery(<Harness onConfirm={onConfirm} />);
    const accept = screen
      .getByRole("dialog")
      .querySelector<HTMLElement>("[data-confirm-accept]")!;

    await userEvent.click(accept);
    await userEvent.click(accept);
    await userEvent.click(accept);

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("locks both buttons and says so while pending", () => {
    renderWithQuery(<Harness pending />);
    const box = screen.getByRole("dialog");

    expect(box.querySelector("[data-confirm-accept]")).toBeDisabled();
    expect(box.querySelector("[data-confirm-cancel]")).toBeDisabled();
    expect(within(box).getByText("Đang xử lý…")).toBeInTheDocument();
  });
});

describe("169. the two variants, and the optional parts", () => {
  it("renders a count and details when the action has them", () => {
    renderWithQuery(
      <Harness
        spec={{
          ...SPEC,
          title: "Duyệt 24 nội dung?",
          count: 24,
          details: "A · B · C và 21 nội dung khác",
        }}
      />,
    );
    expect(screen.getByText("Số lượng: 24 nội dung")).toBeInTheDocument();
    expect(
      screen.getByText("A · B · C và 21 nội dung khác"),
    ).toBeInTheDocument();
  });

  it("styles a destructive confirm differently from an ordinary one", () => {
    const { unmount } = renderWithQuery(<Harness />);
    const ordinary = screen
      .getByRole("dialog")
      .querySelector("[data-confirm-accept]")!.className;
    unmount();

    renderWithQuery(<Harness spec={{ ...SPEC, variant: "destructive" }} />);
    const destructive = screen
      .getByRole("dialog")
      .querySelector("[data-confirm-accept]")!.className;

    // Asserted as "different, and the destructive one is the red component"
    // rather than by pinning a class string: the palette may move, the
    // distinction may not.
    expect(destructive).not.toEqual(ordinary);
    expect(destructive).toContain("red");
  });
});

// ===========================================================================
// 170-172. The policy, in the screens
// ===========================================================================

const BRAND = { id: CONTENT.brand_id, code: "BR", name: "Apexmed" };
const PRODUCER = {
  user_id: "99999999-9999-9999-9999-999999999999",
  full_name: "Hà Chi",
  role: "EMPLOYEE",
};

type Route = {
  match: string;
  status?: number;
  body?: unknown;
  method?: string;
};

const detailRoutes = (
  actions: Array<Record<string, unknown>>,
  content: Record<string, unknown> = CONTENT,
  extra: Route[] = [],
): Route[] => [
  ...extra,
  {
    match: `/api/pr/contents/${CONTENT.id}/available-actions`,
    body: {
      content_id: CONTENT.id,
      workflow_stage: content.workflow_stage,
      available_actions: actions,
    },
  },
  { match: `/api/pr/contents/${CONTENT.id}/resources`, body: [] },
  { match: `/api/pr/contents/${CONTENT.id}/approvals`, body: [] },
  {
    match: `/api/pr/contents/${CONTENT.id}/review-context`,
    body: { content, current_version: VERSION, approvals: [], tasks: [] },
  },
  { match: `/api/pr/contents/${CONTENT.id}/versions`, body: [VERSION] },
  {
    match: `/api/pr/contents/${CONTENT.id}/history`,
    body: { transitions: [] },
  },
  {
    match: `/api/pr/contents/${CONTENT.id}/production`,
    body: {
      content_id: CONTENT.id,
      workflow_stage: content.workflow_stage,
      producer_user_id: content.producer_user_id ?? null,
      production_state: content.production_state ?? null,
      submissions: [],
    },
  },
  {
    match: `/api/pr/contents/${CONTENT.id}/ai-review`,
    body: { active: false, runs: [] },
  },
  {
    match: `/api/pr/contents/${CONTENT.id}`,
    body: { content, current_version: VERSION, targets: [], brand: BRAND },
  },
  {
    match: "/api/pr/people",
    body: [
      { user_id: SESSION.user_id, full_name: SESSION.full_name },
      PRODUCER,
    ],
  },
];

const posts = (stub: ReturnType<typeof stubFetch>) =>
  (
    stub as unknown as {
      calls: Array<{ url: string; method: string; body: unknown }>;
    }
  ).calls.filter((call) => call.method !== "GET");

describe("170. approving asks, and cancelling sends nothing", () => {
  it("asks before an approval and states what the approval does", async () => {
    const stub = stubFetch([
      {
        match: "/reviews",
        method: "POST",
        status: 201,
        body: { content: CONTENT, current_version: VERSION, targets: [] },
      },
      ...detailRoutes([
        {
          action: "APPROVAL",
          decision: "APPROVED",
          target_stage: null,
          emphasis: "PRIMARY",
        },
      ]),
    ]);
    renderWithQuery(<ContentDetailPage />);

    const approve = await screen.findAllByRole("button", { name: "Duyệt" });
    await userEvent.click(approve[0]);

    expect(dialog().getByText("Duyệt nội dung này?")).toBeInTheDocument();
    expect(
      dialog().getByText(
        "Nội dung sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.",
      ),
    ).toBeInTheDocument();
    expect(posts(stub)).toHaveLength(0);

    await cancelDialog();
    // Cancelling is the case worth pinning: a confirmation that still sent the
    // request would be worse than none, because it would look safe.
    expect(posts(stub)).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uses destructive styling for a rejection and ordinary for an approval", async () => {
    stubFetch(
      detailRoutes([
        {
          action: "APPROVAL",
          decision: "APPROVED",
          target_stage: null,
          emphasis: "PRIMARY",
        },
        {
          action: "APPROVAL",
          decision: "REJECTED",
          target_stage: null,
          emphasis: "DANGER",
        },
      ]),
    );
    renderWithQuery(<ContentDetailPage />);

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "Duyệt" }))[0],
    );
    expect(
      screen.getByRole("dialog").querySelector("[data-confirm-accept]")!
        .className,
    ).not.toContain("red");
    await cancelDialog();

    await userEvent.click(
      screen.getByRole("button", { name: /Thao tác khác/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Từ chối" }));
    expect(dialog().getByText("Từ chối nội dung này?")).toBeInTheDocument();
    expect(
      screen.getByRole("dialog").querySelector("[data-confirm-accept]")!
        .className,
    ).toContain("red");
  });
});

describe("171. claiming and assigning name what changes hands", () => {
  it("says who becomes responsible when claiming", async () => {
    stubFetch(
      detailRoutes(
        [
          {
            action: "CLAIM_PRODUCTION",
            decision: null,
            target_stage: null,
            emphasis: "PRIMARY",
          },
        ],
        {
          ...CONTENT,
          workflow_stage: "APPROVED",
          production_state: "WAITING_FOR_PRODUCER",
        },
      ),
    );
    renderWithQuery(<ContentDetailPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Nhận sản xuất" }),
    );
    expect(
      dialog().getByText("Nhận sản xuất nội dung này?"),
    ).toBeInTheDocument();
    expect(
      dialog().getByText(
        "Bạn sẽ trở thành người phụ trách sản xuất và nội dung sẽ chuyển sang bước Đang sản xuất.",
      ),
    ).toBeInTheDocument();
  });

  it("names the person on an assignment, and names both on a reassignment", () => {
    // A pure copy assertion - the three outcomes of one control, which is where
    // "Chuyển người sản xuất" wrongly describing a first assignment would live.
    const fresh = assignProducerConfirmation({ name: "Hà Chi" });
    expect(fresh.title).toBe("Phân công Hà Chi sản xuất?");
    expect(fresh.description).toBe(
      "Hà Chi sẽ trở thành người phụ trách sản xuất của nội dung này.",
    );
    expect(fresh.confirmLabel).toBe("Phân công Hà Chi");
    expect(fresh.variant).toBe("primary");

    const moved = assignProducerConfirmation({
      name: "Hà Chi",
      current: "Minh",
    });
    expect(moved.title).toBe("Chuyển người sản xuất sang Hà Chi?");
    expect(moved.description).toBe(
      "Người phụ trách sản xuất hiện tại sẽ được thay thế bằng Hà Chi.",
    );

    // Unassigning interrupts work in progress, so it is the destructive one.
    const cleared = assignProducerConfirmation({ name: null, current: "Minh" });
    expect(cleared.title).toBe("Bỏ phân công Minh?");
    expect(cleared.variant).toBe("destructive");
  });

  it("does not send an assignment merely because the picker changed", async () => {
    const stub = stubFetch(
      detailRoutes(
        [
          {
            action: "ASSIGN_PRODUCER",
            decision: null,
            target_stage: null,
            emphasis: "SECONDARY",
          },
        ],
        {
          ...CONTENT,
          workflow_stage: "APPROVED",
          production_state: "WAITING_FOR_PRODUCER",
        },
      ),
    );
    renderWithQuery(<ContentDetailPage />);

    const picker = await screen.findByRole("combobox", {
      name: "Người sản xuất",
    });
    await userEvent.selectOptions(picker, PRODUCER.user_id);

    // Before Step 1F.2.8 this `change` *was* the assignment, so a mis-scroll on
    // a phone moved somebody else's work to somebody else.
    expect(posts(stub)).toHaveLength(0);
    await userEvent.click(
      screen.getByRole("button", { name: "Phân công Hà Chi" }),
    );
    expect(
      dialog().getByText("Phân công Hà Chi sản xuất?"),
    ).toBeInTheDocument();
    expect(posts(stub)).toHaveLength(0);
  });
});

describe("172. permissions and integrations", () => {
  const GRANT = {
    id: "12121212-1212-4121-8121-121212121212",
    user_id: PRODUCER.user_id,
    capability: "PR_TEAM_LEAD_REVIEW",
    scope: {
      content_type_scope: "ALL",
      content_types: [],
      include_unclassified_content: false,
      channel_scope: "ALL",
      channel_ids: [],
      include_unassigned_channel: false,
    },
    effective_from: null,
    effective_to: null,
    requires_role_baseline: false,
    granted_by_user_id: null,
  };

  it("names the gate and the person when revoking", async () => {
    const stub = stubFetch([
      { match: "/capabilities/revoke", method: "POST", body: GRANT },
      { match: "/api/pr/capabilities", body: [GRANT] },
      { match: "/api/pr/channels", body: [] },
      { match: "/api/pr/people", body: [PRODUCER] },
      {
        match: "/api/pr/members",
        body: {
          members: [],
          counts: { total: 1, active: 1, suspended: 0, revoked: 0, pending: 0 },
          may_add: true,
          may_change_status: true,
          may_change_role: true,
          assignable_roles: [],
        },
      },
    ]);
    // The grant surface is the third tab of Thành viên & Phân quyền.
    NAV.arriveAt("/pr/permissions?tab=grants");
    renderWithQuery(<PermissionsPage />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Thu hồi" }),
    );
    expect(
      dialog().getByText("Thu hồi quyền Duyệt Trưởng nhóm của Hà Chi?"),
    ).toBeInTheDocument();
    expect(
      dialog().getByText("Quyền này sẽ ngừng có hiệu lực ngay lập tức."),
    ).toBeInTheDocument();
    expect(posts(stub)).toHaveLength(0);

    await confirm();
    await waitFor(() => expect(posts(stub)).toHaveLength(1));
  });

  it("does not stack a second dialog on the grant form", async () => {
    const stub = stubFetch([
      { match: "/capabilities/grant", method: "POST", body: GRANT },
      { match: "/api/pr/capabilities", body: [] },
      { match: "/api/pr/channels", body: [] },
      { match: "/api/pr/people", body: [PRODUCER] },
      {
        match: "/api/pr/members",
        body: {
          members: [],
          counts: { total: 1, active: 1, suspended: 0, revoked: 0, pending: 0 },
          may_add: true,
          may_change_status: true,
          may_change_role: true,
          assignable_roles: [],
        },
      },
    ]);
    NAV.arriveAt("/pr/permissions?tab=grants");
    renderWithQuery(<PermissionsPage />);

    // Wait for the people list, not merely for the picker: the picker exists
    // with one placeholder option from the first render.
    await screen.findByRole("option", { name: /Hà Chi/ });
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /^Người/ }),
      PRODUCER.user_id,
    );
    // Both scope axes to "all", which is what makes the grant submittable.
    for (const box of screen.getAllByRole("checkbox", {
      name: "Chọn tất cả",
    })) {
      await userEvent.click(box);
    }

    // The submit names the action rather than saying "Lưu", which is the whole
    // of requirement 12: the form collected the parameters, so the form's own
    // submit is the confirmation.
    const submit = screen.getByRole("button", {
      name: "Cấp quyền Duyệt Trưởng nhóm cho Hà Chi",
    });
    await userEvent.click(submit);

    await waitFor(() => expect(posts(stub)).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("172b. disconnecting a channel asks; connecting one does not", () => {
  const PLATFORM = {
    id: "d1",
    code: "TIKTOK",
    name: "TikTok",
    status: "ACTIVE",
  };
  const CHANNEL = {
    id: "c1",
    code: "CH-1",
    name: "Apexmed TikTok",
    category: "SCALE",
    status: "ACTIVE",
    brand_id: BRAND.id,
    platform_id: PLATFORM.id,
    platform_code: "TIKTOK",
    platform_label: "TikTok",
    metrics_status: "CONNECTED_API",
    metrics_status_label: "Đã kết nối API",
  };
  const state = (connection: unknown) => ({
    channel_id: CHANNEL.id,
    supported: true,
    configured: true,
    provider: "TIKTOK",
    provider_label: "TikTok",
    connection,
    can_manage_connection: true,
    auto_sync_label: "Hàng ngày",
  });
  const CONNECTED = {
    id: "x1",
    channel_id: CHANNEL.id,
    provider: "TIKTOK",
    state: "CONNECTED",
    state_label: "Đã kết nối",
    provider_account_id: "open-id",
    provider_account_name: "Apexmed",
    provider_account_handle: "@apexmed",
    sync_status: "SUCCESS",
    sync_status_label: "Đồng bộ thành công",
    last_sync_succeeded_at: null,
    last_sync_failed_at: null,
    last_sync_error_code: null,
    last_sync_error_message: null,
    days_since_success: 0,
    auto_sync_enabled: true,
    connected_at: null,
  };
  const channelRoutes = (connection: unknown): Route[] => [
    {
      match: "/api/pr/dashboard",
      body: {
        stage_counts: [],
        awaiting_my_review: [],
        overdue_tasks: [],
        my_capabilities: ["PR_CHANNEL_MANAGE"],
        recent_content: [],
      },
    },
    { match: "/api/pr/platforms", body: [PLATFORM] },
    { match: "/api/pr/brands", body: [BRAND] },
    {
      match: "/connections/accounts",
      body: { channel_id: CHANNEL.id, provider: "TIKTOK", accounts: [] },
    },
    {
      match: "/connections/tiktok/disconnect",
      method: "POST",
      body: state(null),
    },
    // Before `/connection`, which is a substring of it. This file is about the
    // confirmation dialogs, so the panel only has to render - see
    // `tiktok-connector.test.tsx` for what it renders.
    {
      match: "/connections/tiktok/overview",
      body: {
        channel_id: CHANNEL.id,
        provider: "TIKTOK",
        state: "CONNECTED",
        state_label: "Đã kết nối",
        account: {
          open_id: "open-id",
          display_name: "Apexmed",
          username: "apexmed",
          handle: "@apexmed",
          avatar_url: null,
          profile_url: null,
          bio: null,
          is_verified: null,
        },
        stats: {
          follower_count: null,
          following_count: null,
          likes_count: null,
          video_count: null,
          availability: "not_read",
          availability_label: "Chưa đọc trong lần này",
        },
        profile_availability: "available",
        profile_availability_label: "Đã lấy được",
        videos: [],
        videos_availability: "empty",
        videos_availability_label: "TikTok trả về rỗng",
        video_counters_availability: "empty",
        video_counters_availability_label: "TikTok trả về rỗng",
        videos_cursor: null,
        videos_has_more: false,
        max_video_pages: 5,
        granted_scopes: [],
        scopes: [],
        fetched_at: "2026-08-22T02:05:00Z",
        last_sync_succeeded_at: null,
        sync_status: "SUCCESS",
        sync_status_label: "Đồng bộ thành công",
        sync_requested: false,
        can_manage_connection: true,
      },
    },
    { match: "/connection", body: state(connection) },
    {
      match: "/metrics",
      body: {
        channel_id: CHANNEL.id,
        status: "CONNECTED_API",
        status_label: "Đã kết nối API",
        latest: null,
        previous: null,
        trend: null,
        history: [],
        total: 0,
        limit: 30,
        offset: 0,
        days_since_capture: null,
        analytics: null,
        can_record_metrics: true,
        has_history: false,
      },
    },
    {
      match: `/api/pr/channels/${CHANNEL.id}`,
      body: {
        channel: CHANNEL,
        assignments: [],
        can_edit_channel: true,
        can_record_metrics: true,
        can_manage_assignments: true,
      },
    },
    { match: "/api/pr/channels", body: [CHANNEL] },
    { match: "/api/pr/people", body: [PRODUCER] },
  ];

  it("asks before disconnecting, and says what stops happening", async () => {
    const stub = stubFetch(channelRoutes(CONNECTED));
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: new RegExp(CHANNEL.name) }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Ngắt kết nối" }),
    );
    expect(dialog().getByText("Ngắt kết nối TikTok này?")).toBeInTheDocument();
    expect(
      dialog().getByText(/ngừng đồng bộ dữ liệu từ tài khoản này/),
    ).toBeInTheDocument();
    expect(posts(stub)).toHaveLength(0);
    // Disconnecting takes an integration away, so it is destructive-styled.
    expect(
      screen.getByRole("dialog").querySelector("[data-confirm-accept]")!
        .className,
    ).toContain("red");
  });

  it("opens the provider's consent screen with no dialog in front of it", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    stubFetch([
      {
        match: "/connections/tiktok/authorize",
        method: "POST",
        body: {
          authorization_url: "https://www.tiktok.com/v2/auth/authorize/?x=1",
        },
      },
      ...channelRoutes(null),
    ]);
    renderWithQuery(<ChannelsPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: new RegExp(CHANNEL.name) }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Kết nối TikTok" }),
    );

    // Requirement 22: TikTok's own consent screen *is* the authorization step.
    // A TasksBot dialog in front of it would be a click that authorises nothing.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(assign).toHaveBeenCalled());
  });
});

// ===========================================================================
// 173. Keeping the policy, without a brittle sweep
// ===========================================================================

describe("173. the confirmation policy has a maintainable regression strategy", () => {
  it("uses no browser confirm, alert or prompt anywhere in the panel", () => {
    for (const file of walk(SRC).filter((f) => /\.tsx?$/.test(f))) {
      const source = readFileSync(file, "utf8");
      for (const forbidden of [
        "window.confirm",
        "window.alert",
        "window.prompt",
      ]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("routes every mutating screen through the shared component", () => {
    // The check is *reuse*, not coverage: a page that writes and does not import
    // the shared confirmation is either bypassing the policy or has a reason
    // that belongs in the exemption list below, in writing.
    const exempt: Record<string, string> = {
      "components/notifications.tsx":
        "Đánh dấu đã đọc chỉ đổi trạng thái đọc của chính người đang đăng nhập, không đổi dữ liệu nghiệp vụ.",
      "components/shell.tsx":
        "Chỉ có đăng xuất; kết thúc phiên của chính mình, không đổi dữ liệu nghiệp vụ và đăng nhập lại được ngay.",
      "components/pr-create-content.tsx":
        "Form tạo nội dung PR, tách ra khỏi app/pr/content/page.tsx để dùng chung với màn Tạo order. Chỉ tạo bản ghi mới; nút gửi trong form chính là bước xác nhận (policy parameter-modal), như khi còn nằm trong trang bảng nội dung.",
      "components/pr-content-detail/review.tsx":
        'Tab Duyệt của trang nội dung PR, tách nguyên văn khỏi app/pr/content/[id]/page.tsx để dùng chung với màn task hợp nhất (/tasks/[ref]). Hai thao tác ghi: Lưu bản mới trong trình soạn thảo (policy parameter-modal, xem ACTION_INVENTORY "Sửa nội dung"; luôn tạo phiên bản mới, không ghi đè bản cũ) và Thử lại AI review đã lỗi (chỉ xếp lại một lần kiểm tra mang tính tư vấn, không đổi bước quy trình hay quyết định duyệt nào).',
      "app/login/page.tsx":
        "Form đăng nhập bằng ID Telegram và mật khẩu; nút Đăng nhập trong form chính là bước xác nhận (policy parameter-modal). Chỉ mở phiên cho chính người gõ mật khẩu, không đổi dữ liệu nghiệp vụ. Form Quên mật khẩu (một ô ID Telegram) cũng vậy: mật khẩu tạm chỉ gửi vào Telegram của chính tài khoản đó.",
      "app/account/password.tsx":
        "Form đổi mật khẩu của chính mình (hộp thoại trên trang Tài khoản, hoặc thẻ duy nhất khi bắt buộc đổi); nút Đổi mật khẩu trong form là bước xác nhận (policy parameter-modal, xem ACTION_INVENTORY). Phải nhập đúng mật khẩu hiện tại và chỉ đổi tài khoản của chính người đang đăng nhập.",
      "app/pr/work/mapping.tsx":
        "Form ba lựa chọn (cột mốc, loại nội dung, loại công việc); nút lưu trong form chính là bước xác nhận — xem ACTION_INVENTORY, policy parameter-modal. Lưu ánh xạ không viết lại lịch sử: công việc đã COUNTED trong kỳ đã đóng giữ nguyên, chỉ các lần chiếu sau dùng ánh xạ mới. M3.1 tách tệp này ra khỏi kpi.tsx, và trước đó nó lọt qua kiểm tra này chỉ vì ở chung tệp với các hộp thoại KPI.",
    };
    for (const file of walk(SRC).filter((f) => /\.tsx$/.test(f))) {
      const source = readFileSync(file, "utf8");
      if (!source.includes("useMutation")) continue;
      const relative = path.relative(SRC, file);
      if (relative in exempt) {
        expect(exempt[relative].length, relative).toBeGreaterThan(20);
        continue;
      }
      // Either the dialog itself, or a shared mutation component that owns one -
      // the board's bulk bar is the latter, and pointing it at the dialog
      // directly would be a second implementation of the same bar.
      expect(
        source.includes("@/components/confirm") ||
          source.includes("@/components/bulk-approval"),
        relative,
      ).toBe(true);
    }
  });

  it("keeps an explicit, complete action inventory", () => {
    expect(ACTION_INVENTORY.length).toBeGreaterThan(20);
    for (const entry of ACTION_INVENTORY) {
      expect(entry.action.length, entry.action).toBeGreaterThan(3);
      // Anything not behind a dialog has to say why, in a sentence somebody can
      // disagree with. That is what makes this list reviewable rather than a
      // tick-box.
      if (entry.policy !== "dialog") {
        expect(entry.reason ?? "", entry.action).not.toEqual("");
        expect((entry.reason ?? "").length, entry.action).toBeGreaterThan(20);
      }
    }
    // The state-changing families the audit has to cover, each named at least
    // once. Adding a family means adding a line here and a line there, which is
    // exactly the friction this is for.
    for (const family of [
      "Duyệt",
      "Từ chối",
      "Hoàn tác",
      "Nhận sản xuất",
      "Phân công",
      "Xóa vĩnh viễn",
      "Thu hồi quyền duyệt",
      "Ngắt kết nối",
      "task",
    ]) {
      expect(
        ACTION_INVENTORY.some((entry) => entry.action.includes(family)),
        family,
      ).toBe(true);
    }
  });

  it("never asks twice for a read-only interaction", () => {
    const readOnly = ACTION_INVENTORY.filter((entry) =>
      /Lọc|Mở chi tiết|thông báo đã đọc|màn hình cấp quyền|Đồng bộ số liệu/.test(
        entry.action,
      ),
    );
    expect(readOnly.length).toBeGreaterThan(3);
    for (const entry of readOnly) {
      expect(entry.policy, entry.action).toBe("none");
    }
  });
});

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});
