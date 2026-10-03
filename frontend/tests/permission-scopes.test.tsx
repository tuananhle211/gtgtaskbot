/**
 * Step 1F.2.7: the permissions screen grants a **scope**, and shows one.
 *
 * Two halves, and they fail differently, so they are tested separately:
 *
 * * **the form** has to send what somebody picked - several classifications,
 *   several channels, "Tất cả" as a mode rather than as every id, and an expiry
 *   from a preset. A form that sent a subset of the selection would hand out
 *   less authority than the operator thinks; one that expanded "Tất cả" into
 *   today's channel ids would hand out a grant that stops covering the next one;
 * * **the list** has to show the whole scope. A row that said *Hảo · Duyệt
 *   Trưởng nhóm* and nothing else describes a grant this product no longer
 *   issues, and the person reading it would have no way to tell what it reaches.
 *
 * No authorization is asserted here, because none happens in the browser. What
 * is asserted is that the panel sends the operator's choice unaltered and
 * renders the server's answer unaltered.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { channelsNavigation, confirm, dialog, renderWithQuery, stubFetch } from "./helpers";

// Thành viên & Phân quyền made the grant surface the third tab of the page.
// Every test here still renders the *page* and arrives at `?tab=grants`, the
// way a link from an approval refusal does, so the tab wiring is under test
// along with the form.
const NAV = channelsNavigation("/pr/permissions");
vi.mock("next/navigation", () => NAV.module);
const { default: PermissionsPage } = await import("@/app/pr/permissions/page");
beforeEach(() => {
  NAV.reset();
  NAV.arriveAt("/pr/permissions?tab=grants");
});

const HAO = "11111111-1111-4111-8111-111111111111";
const TIKTOK = "22222222-2222-4222-8222-222222222222";
const FACEBOOK = "33333333-3333-4333-8333-333333333333";
const YOUTUBE = "44444444-4444-4444-8444-444444444444";

const PEOPLE = [{ user_id: HAO, full_name: "Hảo", role: "EMPLOYEE" }];

const channel = (id: string, code: string, name: string) => ({
  id,
  code,
  name,
  category: "SCALE",
  status: "ACTIVE",
  brand_id: null,
  platform_id: "55555555-5555-4555-8555-555555555555",
  tier: null,
  url: null,
  platform_code: null,
  policy_grounded_platform: false,
  platform: null,
});

const CHANNELS = [
  channel(TIKTOK, "CH-0001", "TikTok BS Tiến"),
  channel(FACEBOOK, "CH-0004", "Facebook BS Tiến"),
  channel(YOUTUBE, "CH-0009", "YouTube Apexmed"),
];

const ROSTER = {
  members: [],
  counts: { total: 1, active: 1, suspended: 0, revoked: 0, pending: 0 },
  may_add: true,
  may_change_status: true,
  may_change_role: true,
  assignable_roles: [],
};

const routes = (grants: unknown[] = []) => [
  { match: "/api/pr/members", body: ROSTER },
  { match: "/api/pr/people", body: PEOPLE },
  { match: "/api/pr/channels", body: CHANNELS },
  { match: "/api/pr/capabilities", method: "GET", body: grants },
];

/** The worked example from the requirement, as the server would return it. */
const GRANT = {
  id: "66666666-6666-4666-8666-666666666666",
  user_id: HAO,
  capability: "PR_TEAM_LEAD_REVIEW",
  scope: {
    content_type_scope: "SELECTED",
    content_types: ["SHORT_VIDEO_SCRIPT", "FACEBOOK_POST"],
    include_unclassified_content: false,
    channel_scope: "SELECTED",
    channel_ids: [TIKTOK, FACEBOOK],
    include_unassigned_channel: false,
  },
  effective_from: null,
  effective_to: "2026-09-01",
  requires_role_baseline: false,
  granted_by_user_id: null,
};

/** The fieldset a legend belongs to, so two multi-selects cannot be confused. */
const scopeBox = (legend: string) =>
  screen.getByText(legend).closest("fieldset") as HTMLFieldSetElement;

const tick = (legend: string, label: string | RegExp) =>
  fireEvent.click(within(scopeBox(legend)).getByLabelText(label));

/** Render the page with its three queries stubbed, and hand back the fetch mock. */
async function openForm(grants: unknown[] = []) {
  const fetchMock = stubFetch(routes(grants));
  renderWithQuery(<PermissionsPage />);
  await waitFor(() => expect(screen.getByText("Cấp quyền duyệt")).toBeInTheDocument());
  await waitFor(() =>
    expect(within(scopeBox("Kênh")).getByLabelText("TikTok BS Tiến")).toBeInTheDocument(),
  );
  return fetchMock as unknown as { calls: Array<{ url: string; body: never }> };
}

/** The body of the last request to a route, as the page sent it. */
const lastBody = <T,>(fetchMock: { calls: Array<{ url: string; body: never }> }, path: string): T =>
  fetchMock.calls.filter((call) => call.url.includes(path)).at(-1)!.body as T;

describe("1. the grant form carries a scope", () => {
  it("sends several classifications and several channels in one grant", async () => {
    const fetchMock = await openForm();

    fireEvent.change(screen.getByLabelText(/^Người/), { target: { value: HAO } });
    tick("Phân loại nội dung", "Kịch bản video ngắn");
    tick("Phân loại nội dung", "Bài đăng Facebook");
    tick("Kênh", "TikTok BS Tiến");
    tick("Kênh", "Facebook BS Tiến");
    // Step 1F.2.8. This form *is* the confirmation - it exists to collect the
    // person, the gate, the scope and the expiry - so there is no second dialog
    // on top of it. What the step did add is the submit naming the action:
    // "Cấp quyền Duyệt Trưởng nhóm cho Hảo", not "Lưu".
    expect(
      screen.getByRole("button", { name: "Cấp quyền Duyệt Trưởng nhóm cho Hảo" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Cấp quyền/ }));

    await waitFor(() =>
      expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/grant"))).toBe(true),
    );
    const sent = lastBody<{
      user_id: string;
      capability: string;
      scope: Record<string, unknown>;
      effective_to: string | null;
    }>(fetchMock, "/capabilities/grant");

    expect(sent.user_id).toBe(HAO);
    expect(sent.capability).toBe("PR_TEAM_LEAD_REVIEW");
    expect(sent.scope.content_type_scope).toBe("SELECTED");
    expect(sent.scope.content_types).toEqual(["SHORT_VIDEO_SCRIPT", "FACEBOOK_POST"]);
    expect(sent.scope.channel_scope).toBe("SELECTED");
    expect(sent.scope.channel_ids).toEqual([TIKTOK, FACEBOOK]);
    // Neither missing-case is opted into unless somebody ticks it.
    expect(sent.scope.include_unclassified_content).toBe(false);
    expect(sent.scope.include_unassigned_channel).toBe(false);
    // "Không hết hạn" is the default, and it is a null rather than a far date.
    expect(sent.effective_to).toBeNull();
  });

  it('sends "Tất cả" as a mode, never as today\'s channel ids', async () => {
    const fetchMock = await openForm();

    fireEvent.change(screen.getByLabelText(/^Người/), { target: { value: HAO } });
    tick("Phân loại nội dung", "Chọn tất cả");
    tick("Kênh", "Chọn tất cả");
    fireEvent.click(screen.getByRole("button", { name: /^Cấp quyền/ }));

    await waitFor(() =>
      expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/grant"))).toBe(true),
    );
    const sent = lastBody<{ scope: Record<string, unknown> }>(fetchMock, "/capabilities/grant");

    expect(sent.scope.content_type_scope).toBe("ALL");
    expect(sent.scope.channel_scope).toBe("ALL");
    // The point of the mode: no list of the three channels that exist today.
    expect(sent.scope.content_types).toEqual([]);
    expect(sent.scope.channel_ids).toEqual([]);
  });

  it("offers the unclassified and unassigned states as explicit choices", async () => {
    const fetchMock = await openForm();

    fireEvent.change(screen.getByLabelText(/^Người/), { target: { value: HAO } });
    tick("Phân loại nội dung", "Kịch bản video ngắn");
    tick("Phân loại nội dung", "Chưa phân loại");
    tick("Kênh", "TikTok BS Tiến");
    tick("Kênh", "Chưa gán kênh");
    fireEvent.click(screen.getByRole("button", { name: /^Cấp quyền/ }));

    await waitFor(() =>
      expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/grant"))).toBe(true),
    );
    const sent = lastBody<{ scope: Record<string, unknown> }>(fetchMock, "/capabilities/grant");

    // The sentinels are form values and never reach the wire as content types.
    expect(sent.scope.content_types).toEqual(["SHORT_VIDEO_SCRIPT"]);
    expect(sent.scope.include_unclassified_content).toBe(true);
    expect(sent.scope.channel_ids).toEqual([TIKTOK]);
    expect(sent.scope.include_unassigned_channel).toBe(true);
  });

  it("will not submit a scope that covers nothing", async () => {
    await openForm();
    fireEvent.change(screen.getByLabelText(/^Người/), { target: { value: HAO } });
    expect(screen.getByRole("button", { name: /^Cấp quyền/ })).toBeDisabled();

    tick("Phân loại nội dung", "Kịch bản video ngắn");
    // One axis chosen is still nothing: a grant needs both.
    expect(screen.getByRole("button", { name: /^Cấp quyền/ })).toBeDisabled();
    tick("Kênh", "TikTok BS Tiến");
    expect(screen.getByRole("button", { name: /^Cấp quyền/ })).toBeEnabled();
  });

  it("turns an expiry preset into a day, and a custom one into the picked day", async () => {
    const fetchMock = await openForm();
    fireEvent.change(screen.getByLabelText(/^Người/), { target: { value: HAO } });
    tick("Phân loại nội dung", "Kịch bản video ngắn");
    tick("Kênh", "TikTok BS Tiến");

    fireEvent.change(screen.getByLabelText(/^Thời hạn/), { target: { value: "P7D" } });
    fireEvent.click(screen.getByRole("button", { name: /^Cấp quyền/ }));
    await waitFor(() =>
      expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/grant"))).toBe(true),
    );
    const week = lastBody<{ effective_to: string }>(fetchMock, "/capabilities/grant");
    // Seven days counted inclusive of today: `effective_to` is a closed bound.
    const expected = new Date();
    expected.setDate(expected.getDate() + 6);
    expect(week.effective_to).toBe(
      `${expected.getFullYear()}-${`${expected.getMonth() + 1}`.padStart(2, "0")}-${`${expected.getDate()}`.padStart(2, "0")}`,
    );
  });

  it("asks for a date when the expiry is Tùy chọn", async () => {
    await openForm();
    expect(screen.queryByLabelText(/Ngày hết hạn/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Thời hạn/), { target: { value: "CUSTOM" } });
    expect(screen.getByLabelText(/Ngày hết hạn/)).toBeInTheDocument();
  });

  it("offers only the three grantable capabilities", async () => {
    await openForm();
    const options = within(screen.getByLabelText(/^Quyền duyệt/))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toEqual(["Duyệt Trưởng nhóm", "Duyệt Trưởng phòng", "Duyệt nội bộ"]);
  });
});

describe("2. the active-grant list shows the whole scope", () => {
  it("names the person, the gate, both scopes and the expiry", async () => {
    await openForm([GRANT]);
    const row = screen.getByTestId("grant-row");

    expect(within(row).getByText("Hảo")).toBeInTheDocument();
    expect(within(row).getByText("Duyệt Trưởng nhóm")).toBeInTheDocument();
    expect(within(row).getByText(/Kịch bản video ngắn, Bài đăng Facebook/)).toBeInTheDocument();
    expect(within(row).getByText(/TikTok BS Tiến, Facebook BS Tiến/)).toBeInTheDocument();
    expect(within(row).getByText(/01\/09\/2026/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Thu hồi" })).toBeInTheDocument();
  });

  it('renders an "all" scope as a mode rather than as a list of names', async () => {
    await openForm([
      {
        ...GRANT,
        scope: {
          ...GRANT.scope,
          content_type_scope: "ALL",
          content_types: [],
          channel_scope: "ALL",
          channel_ids: [],
        },
        effective_to: null,
      },
    ]);
    const row = screen.getByTestId("grant-row");
    expect(within(row).getByText(/Tất cả phân loại/)).toBeInTheDocument();
    expect(within(row).getByText(/Tất cả kênh/)).toBeInTheDocument();
    expect(within(row).getByText(/Không hết hạn/)).toBeInTheDocument();
  });

  it("names the missing-case inclusions when a grant covers them", async () => {
    await openForm([
      {
        ...GRANT,
        scope: {
          ...GRANT.scope,
          include_unclassified_content: true,
          include_unassigned_channel: true,
        },
      },
    ]);
    const row = screen.getByTestId("grant-row");
    expect(within(row).getByText(/Chưa phân loại/)).toBeInTheDocument();
    expect(within(row).getByText(/Chưa gán kênh/)).toBeInTheDocument();
  });

  it("revokes by grant id, because a person may hold several of one gate", async () => {
    const fetchMock = await openForm([GRANT]);
    fireEvent.click(
      within(screen.getByTestId("grant-row")).getByRole("button", { name: "Thu hồi" }),
    );
    // Step 1F.2.8. Revoking takes authority away the moment it lands, so it
    // asks - and the question names both the gate and the person, because a
    // list of grants is exactly where a mis-click hits the wrong row.
    expect(dialog().getByText("Thu hồi quyền Duyệt Trưởng nhóm của Hảo?")).toBeInTheDocument();
    expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/revoke"))).toBe(false);
    await confirm();

    await waitFor(() =>
      expect(fetchMock.calls.some((call) => call.url.includes("/capabilities/revoke"))).toBe(true),
    );
    const sent = lastBody<Record<string, unknown>>(fetchMock, "/capabilities/revoke");
    expect(sent).toEqual({ grant_id: GRANT.id });
    expect(sent).not.toHaveProperty("capability");
  });

  it("draws two rows for two grants of the same gate", async () => {
    await openForm([
      GRANT,
      {
        ...GRANT,
        id: "77777777-7777-4777-8777-777777777777",
        scope: {
          ...GRANT.scope,
          content_types: ["PRESS_ARTICLE"],
          channel_ids: [YOUTUBE],
        },
      },
    ]);
    const rows = screen.getAllByTestId("grant-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText(/Báo chí/)).toBeInTheDocument();
    expect(within(rows[1]).getByText(/YouTube Apexmed/)).toBeInTheDocument();
  });

  it("says so when an old grant still depends on the holder's role", async () => {
    await openForm([{ ...GRANT, requires_role_baseline: true }]);
    expect(screen.getByText(/vai trò của người này đã có quyền tương ứng/)).toBeInTheDocument();
  });
});

describe("3. the page states what a grant does", () => {
  it("says a grant is additive and exact, not narrowing", async () => {
    await openForm();
    expect(screen.getByText(/không đổi vai trò/)).toBeInTheDocument();
    expect(screen.getByText(/Đúng phạm vi đã chọn/)).toBeInTheDocument();
    // The pre-1F.2.7 sentence said the opposite and must be gone.
    expect(screen.queryByText(/chỉ thu hẹp, không mở rộng/)).not.toBeInTheDocument();
  });
});
