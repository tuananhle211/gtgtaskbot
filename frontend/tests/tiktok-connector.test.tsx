/**
 * Step 1F.2.6 - TikTok in the connection panel.
 *
 * Numbered 53-62, following the requirement numbering the step was specified
 * with: 53-56 connecting, 57-59 the connected identity, 60-62 the controls and
 * the two refusal screens.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Which platform name appears is `provider_label` from the server; whether a
 * connect control appears at all is `supported`; whether anybody may press it
 * is `can_manage_connection`; every state word is a `*_label`. A test that
 * passed because the component matched on `"TIKTOK"` and rendered its own
 * Vietnamese would be testing a second implementation of rules that live on the
 * server.
 *
 * What is deliberately **not** tested here
 * -----------------------------------------
 *
 * Analytics cards. There are none, and there will be none until
 * `meobot-tiktok-probe` has been run against a real account - whether the
 * Display API serves per-video counters decides what a TikTok card can honestly
 * say, and that is not a question this milestone answers. What is tested is
 * that a channel can be connected, that the identity shown is the one TikTok
 * confirmed, and that the metrics panel degrades to what it already does for
 * any channel with only stock counts recorded.
 *
 * There is also no account-chooser test, and its absence is the assertion:
 * TikTok consent authorizes exactly one account, the provider does not
 * implement account selection, and a TikTok connection never reaches
 * `PENDING_SELECTION`. Test 62 is what pins that down.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, analyticsFrom, channelsNavigation, renderWithQuery, stubFetch } from "./helpers";

/* Step 1F.2.9. `/pr/channels` reads its selection out of `?channel=`, so every
   test that renders it needs a router that really navigates. See
   `channelsNavigation`. */
const NAV = channelsNavigation();
vi.mock("next/navigation", () => NAV.module);

const { default: ChannelsPage } = await import("@/app/pr/channels/page");

const DASHBOARD = {
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: ["PR_CHANNEL_MANAGE"],
  recent_content: [],
};

const TT_PLATFORM = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  code: "TIKTOK",
  name: "TikTok",
  status: "ACTIVE",
  policy_grounded: true,
};

const BRAND = {
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  code: "APEXMED",
  name: "Apexmed",
};

/** CH-0014, one of the real channels this milestone was specified against. */
const TT_CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-0014",
  name: "Tâm sự cùng bs Vũ Trọng Tiến",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: BRAND.id,
  platform_id: TT_PLATFORM.id,
  tier: null,
  url: null,
  platform_code: "TIKTOK",
  policy_grounded_platform: true,
  platform: "TIKTOK",
  platform_label: "TikTok",
  platform_name: "TikTok",
  handle: "@bsvutrongtien",
  external_id: null,
  metrics_status: "CONNECTED_API",
  metrics_status_label: "Đã kết nối API",
  latest_captured_at: "2026-08-22T02:00:00Z",
  followers: 128400,
  days_since_capture: 0,
  latest_source: "API",
};

/** TikTok's stable per-app identifier. Long, opaque, and not a username. */
const OPEN_ID = "_000AbCdEfGhIjKlMnOpQrStUvWxYz1234567890";

const TT_CONNECTION = {
  id: "11111111-1111-1111-1111-111111111111",
  channel_id: TT_CHANNEL.id,
  provider: "TIKTOK",
  state: "CONNECTED",
  state_label: "Đã kết nối",
  provider_account_id: OPEN_ID,
  provider_account_name: "Tâm sự cùng bs Vũ Trọng Tiến",
  provider_account_handle: "@bsvutrongtien",
  sync_status: "SUCCESS",
  sync_status_label: "Đồng bộ thành công",
  last_sync_succeeded_at: "2026-08-22T02:00:00Z",
  last_sync_failed_at: null,
  last_sync_error_code: null,
  last_sync_error_message: null,
  days_since_success: 0,
  auto_sync_enabled: true,
  connected_at: "2026-08-01T02:00:00Z",
};

const ttState = (over: Record<string, unknown> = {}) => ({
  channel_id: TT_CHANNEL.id,
  supported: true,
  configured: true,
  provider: "TIKTOK",
  provider_label: "TikTok",
  connection: TT_CONNECTION,
  can_manage_connection: true,
  auto_sync_label: "Hàng ngày",
  ...over,
});

/**
 * What a TikTok sync actually records today: three stock counts and the
 * provider-specific totals beside them.
 *
 * Every windowed column is `null` - not `0` - and that is the fixture's whole
 * job. The Display API has no reporting window at all, so a month's views is
 * *not measured* rather than *measured as nothing*, and test 59 checks the
 * panel says so.
 */
const API_SNAPSHOT = {
  id: "33333333-3333-3333-3333-333333333333",
  channel_id: TT_CHANNEL.id,
  captured_at: "2026-08-22T02:00:00Z",
  source: "API",
  source_label: "Tự động từ nền tảng",
  recorded_by_user_id: null,
  recorded_by_name: null,
  followers: 128400,
  following: 312,
  posts_count: 382,
  fans: null,
  views_7d: null,
  views_30d: null,
  reach_7d: null,
  reach_30d: null,
  impressions_7d: null,
  impressions_30d: null,
  engagements_7d: null,
  engagements_30d: null,
  likes_30d: null,
  comments_30d: null,
  shares_30d: null,
  posts_count_7d: null,
  posts_count_30d: null,
  reactions_30d: null,
  video_views_7d: null,
  video_views_30d: null,
  extra_metrics: {
    tiktok_provider: "TIKTOK",
    tiktok_api_product: "DISPLAY_API",
    tiktok_total_likes: 4500000,
    tiktok_video_count: 382,
  },
};

const METRICS = {
  channel_id: TT_CHANNEL.id,
  status: "CONNECTED_API",
  status_label: "Đã kết nối API",
  latest: API_SNAPSHOT,
  previous: null,
  trend: null,
  history: [API_SNAPSHOT],
  total: 1,
  limit: 30,
  offset: 0,
  days_since_capture: 0,
  // Computed by the server from this snapshot, exactly as it is for every
  // other platform: the derivations in `channel_analytics` were never
  // TikTok-specific, so a TikTok reading gets a full analytics object whose
  // windowed halves are all `null`.
  analytics: analyticsFrom(API_SNAPSHOT),
  can_record_metrics: true,
  has_history: true,
};

/**
 * One live look at the connected TikTok account, as the server sends it.
 *
 * Step 1F.2.9. Deliberately **not** derived from `API_SNAPSHOT`: a snapshot is
 * a stored reading of four numbers and this is a live read of an account, and
 * conflating the two in a fixture would hide the fact that the panel shows
 * things - an avatar, a bio, a video's share link - that no snapshot has ever
 * contained.
 */
const OVERVIEW = {
  channel_id: TT_CHANNEL.id,
  provider: "TIKTOK",
  state: "CONNECTED",
  state_label: "Đã kết nối",
  account: {
    open_id: OPEN_ID,
    display_name: "Tâm sự cùng bs Vũ Trọng Tiến",
    username: "bsvutrongtien",
    handle: "@bsvutrongtien",
    avatar_url: "https://p16-sign.tiktokcdn.com/avatar.jpeg",
    profile_url: "https://www.tiktok.com/@bsvutrongtien",
    bio: "Bác sĩ thẩm mỹ",
    is_verified: false,
  },
  stats: {
    follower_count: 128400,
    following_count: 312,
    likes_count: 4500000,
    video_count: 382,
    availability: "available",
    availability_label: "Đã lấy được",
  },
  profile_availability: "available",
  profile_availability_label: "Đã lấy được",
  videos: [
    {
      video_id: "v1",
      title: "Tân trang cô bé có đau không?",
      description: "Giải đáp cùng bác sĩ",
      created_at: "2026-08-20T09:00:00Z",
      duration_seconds: 47,
      cover_image_url: "https://p16-sign.tiktokcdn.com/cover-1.jpeg",
      share_url: "https://www.tiktok.com/@bsvutrongtien/video/1",
      embed_link: "https://www.tiktok.com/embed/v2/1",
      view_count: 125400,
      like_count: 8200,
      comment_count: 320,
      share_count: 56,
    },
    {
      video_id: "v2",
      title: "Chăm sóc sau thủ thuật",
      description: null,
      created_at: "2026-08-18T09:00:00Z",
      duration_seconds: 31,
      cover_image_url: "https://p16-sign.tiktokcdn.com/cover-2.jpeg",
      share_url: "https://www.tiktok.com/@bsvutrongtien/video/2",
      embed_link: null,
      view_count: 44100,
      like_count: 2100,
      comment_count: 88,
      share_count: 12,
    },
  ],
  videos_availability: "available",
  videos_availability_label: "Đã lấy được",
  video_counters_availability: "available",
  video_counters_availability_label: "Đã lấy được",
  videos_cursor: 6,
  videos_has_more: false,
  max_video_pages: 5,
  granted_scopes: [
    "user.info.basic",
    "user.info.profile",
    "user.info.stats",
    "video.list",
  ],
  scopes: [
    {
      scope: "user.info.basic",
      label: "Thông tin cơ bản",
      description: "Ảnh đại diện, tên hiển thị và mã tài khoản TikTok.",
      granted: true,
    },
    {
      scope: "user.info.profile",
      label: "Hồ sơ TikTok",
      description:
        "Tên người dùng, liên kết hồ sơ, tiểu sử và trạng thái xác minh.",
      granted: true,
    },
    {
      scope: "user.info.stats",
      label: "Thống kê tài khoản",
      description:
        "Số người theo dõi, số đang theo dõi, tổng lượt thích và số video.",
      granted: true,
    },
    {
      scope: "video.list",
      label: "Danh sách video công khai",
      description: "Các video công khai gần đây và chỉ số của từng video.",
      granted: true,
    },
  ],
  fetched_at: "2026-08-22T02:05:00Z",
  last_sync_succeeded_at: "2026-08-22T02:00:00Z",
  sync_status: "SUCCESS",
  sync_status_label: "Đồng bộ thành công",
  sync_requested: false,
  can_manage_connection: true,
};

/** The same panel with one part of it refused, for the degradation tests. */
const overview = (over: Record<string, unknown> = {}) => ({
  ...OVERVIEW,
  ...over,
});

const PEOPLE = [
  {
    user_id: SESSION.user_id,
    full_name: SESSION.full_name,
    role: SESSION.role,
  },
];

/** When a "Đồng bộ lại" in a test came back. Later than `fetched_at`. */
const REFRESHED_AT = "2026-08-22T03:30:00Z";

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  connection: unknown,
  capabilities: string[] = ["PR_CHANNEL_MANAGE"],
  channel: Record<string, unknown> = TT_CHANNEL,
) => [
  {
    match: "/api/pr/dashboard",
    body: { ...DASHBOARD, my_capabilities: capabilities },
  },
  { match: "/api/pr/platforms", body: [TT_PLATFORM] },
  { match: "/api/pr/brands", body: [BRAND] },
  {
    match: "/connections/accounts",
    body: { channel_id: channel.id, provider: "TIKTOK", accounts: [] },
  },
  // Before `/connection`, which is a substring of this path: `stubFetch` takes
  // the first hit, and a panel handed the connection state where it expected an
  // account overview crashes on the first field it reads.
  {
    match: "/connections/tiktok/refresh",
    method: "POST",
    body: { ...OVERVIEW, sync_requested: true, fetched_at: REFRESHED_AT },
  },
  { match: "/connections/tiktok/overview", body: OVERVIEW },
  { match: "/connection", body: connection },
  { match: "/metrics", body: METRICS },
  {
    match: `/api/pr/channels/${channel.id}`,
    body: {
      channel,
      assignments: [],
      can_edit_channel: capabilities.includes("PR_CHANNEL_MANAGE"),
      can_record_metrics: capabilities.includes("PR_CHANNEL_MANAGE"),
      can_manage_assignments: capabilities.includes("PR_CHANNEL_MANAGE"),
    },
  },
  { match: "/api/pr/channels", body: [channel] },
  { match: "/api/pr/people", body: PEOPLE },
];

/**
 * The account panel's stats grid, once it has arrived.
 *
 * By its heading rather than by a test id, and scoped because three places on
 * this screen legitimately show 128.400 - the channel card, the stored-metrics
 * panel and this - and an unscoped `getByText` would pass on whichever it found
 * first, including the one that is not being tested.
 */
async function statsBlock(): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", {
    name: "Thống kê tài khoản",
  });
  return heading.parentElement as HTMLElement;
}

async function open(name: string): Promise<void> {
  await userEvent.click(
    await screen.findByRole("button", { name: new RegExp(name) }),
  );
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 53-56: CONNECTING ----------------------------------------------------

describe("53. a TikTok channel is offered a TikTok connection", () => {
  it("names the platform from the server, not from a table in the browser", async () => {
    stubFetch(routes(ttState({ connection: null })));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(
      await screen.findByRole("button", { name: "Kết nối TikTok" }),
    ).toBeInTheDocument();
    // Not YouTube's or Facebook's label - it follows the channel's own platform.
    expect(
      screen.queryByRole("button", { name: "Kết nối YouTube" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Kết nối Facebook" }),
    ).not.toBeInTheDocument();
  });
});

describe("54. pressing connect navigates to TikTok's own consent screen", () => {
  it("sends the browser to the authorization URL the server minted", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    stubFetch([
      {
        match: "/connections/tiktok/authorize",
        method: "POST",
        body: {
          authorization_url:
            "https://www.tiktok.com/v2/auth/authorize/?client_key=k&state=s",
          expires_at: "2026-08-22T03:00:00Z",
        },
      },
      ...routes(ttState({ connection: null })),
    ]);
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    await userEvent.click(
      await screen.findByRole("button", { name: "Kết nối TikTok" }),
    );
    // A full navigation, not a fetch: the person has to arrive at TikTok
    // themselves so they can see the account they are authorizing and the
    // scopes they are granting.
    expect(assign).toHaveBeenCalledWith(
      "https://www.tiktok.com/v2/auth/authorize/?client_key=k&state=s",
    );
  });
});

describe("55. an unconfigured deployment says so rather than failing on press", () => {
  it("disables the control and points at the server, not at the feature", async () => {
    stubFetch(routes(ttState({ connection: null, configured: false })));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(
      await screen.findByText(/Cấu hình TikTok chưa sẵn sàng/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Kết nối TikTok" }),
    ).toBeDisabled();
  });
});

describe("56. somebody who may not manage channels is offered no control", () => {
  it("hides every write control rather than disabling it", async () => {
    stubFetch(routes(ttState({ can_manage_connection: false }), []));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(await screen.findByText("Đã kết nối")).toBeInTheDocument();
    for (const control of [
      "Kết nối TikTok",
      "Đồng bộ ngay",
      "Kết nối lại",
      "Ngắt kết nối",
    ]) {
      expect(
        screen.queryByRole("button", { name: control }),
      ).not.toBeInTheDocument();
    }
  });
});

// --- 57-59: THE CONNECTED IDENTITY ----------------------------------------

describe("57. the bound account is shown by the id TikTok calls it", () => {
  it("labels the identifier Open ID rather than a generic Account ID", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    // "Open ID" is the word on `meobot-tiktok-probe` output and in TikTok's own
    // console. A panel calling it "Account ID" makes that comparison one guess
    // longer for whoever is diagnosing a wrong binding.
    const row = (await screen.findByText("Open ID")).closest("div");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText(OPEN_ID)).toBeInTheDocument();
  });
});

describe("58. the display name and handle come from the server", () => {
  it("shows what TikTok confirmed, not what somebody typed on the channel", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    const panel = (await screen.findByText("Tài khoản")).closest("dl");
    expect(panel).not.toBeNull();
    expect(
      within(panel as HTMLElement).getByText("Tâm sự cùng bs Vũ Trọng Tiến"),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByText("@bsvutrongtien"),
    ).toBeInTheDocument();
    expect(screen.getByText("Đã kết nối")).toBeInTheDocument();
  });
});

describe("59. a windowed metric TikTok never measured is blank, never zero", () => {
  it("renders the stock counts and leaves the windowed columns empty", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    // The counts the Display API actually serves.
    const audience = (await screen.findByText("Khán giả")).closest("div");
    expect(audience).not.toBeNull();
    expect(within(audience as HTMLElement).getByText("128.400")).toBeInTheDocument();

    // And the month's engagement, which TikTok never measured, is an em dash.
    // A `0` here would be a measurement nobody took - the mistake every rule in
    // this milestone is arranged to prevent, and the one that would be quoted
    // to a client as "engagement collapsed".
    const engagement = screen.getByText("Tương tác 30 ngày").closest("div");
    expect(engagement).not.toBeNull();
    expect(within(engagement as HTMLElement).getByText("—")).toBeInTheDocument();
    expect(within(engagement as HTMLElement).queryByText("0")).not.toBeInTheDocument();
  });
});

// --- 60-62: CONTROLS AND REFUSALS -----------------------------------------

describe("60. the management controls are offered to a manager", () => {
  it("offers refresh, reconnect and disconnect", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    for (const control of ["Đồng bộ lại", "Kết nối lại", "Ngắt kết nối"]) {
      expect(
        await screen.findByRole("button", { name: control }),
      ).toBeInTheDocument();
    }
    /* Step 1F.2.9. "Đồng bộ ngay" is gone from a TikTok channel, and its
       absence is the assertion. "Đồng bộ lại" asks for the *same* snapshot
       through the same claim and additionally re-reads the account in the same
       press, so keeping both would offer a choice with a wrong answer. */
    expect(
      screen.queryByRole("button", { name: "Đồng bộ ngay" }),
    ).not.toBeInTheDocument();
    // Disconnecting is not deleting. The sentence matters because somebody
    // hesitating over that button is asking exactly this question.
    expect(
      screen.getByText(/Ngắt kết nối không xoá số liệu đã ghi nhận/),
    ).toBeInTheDocument();
  });
});

describe("61. an expired grant is explained, and the history is not disowned", () => {
  it("says reconnect, and says the numbers already collected are still real", async () => {
    stubFetch(
      routes(
        ttState({
          connection: {
            ...TT_CONNECTION,
            state: "ACTION_REQUIRED",
            state_label: "Cần kết nối lại",
            sync_status: "FAILED",
            sync_status_label: "Đồng bộ thất bại",
            last_sync_error_code: "AUTH_REQUIRED",
            last_sync_error_message: "Kết nối TikTok cần xác thực lại.",
          },
        }),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(
      await screen.findByText(/TikTok cần kết nối lại/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Số liệu đã lấy trước đó vẫn còn nguyên/),
    ).toBeInTheDocument();
    // The message is MeoBot's sentence, never TikTok's own prose or a log id.
    expect(
      screen.getByText(/Kết nối TikTok cần xác thực lại/),
    ).toBeInTheDocument();
    /* Syncing a dead connection is not offered at all; reconnecting is.
       Step 1F.2.9: the account panel is drawn only for a live connection, so an
       ACTION_REQUIRED channel gets no "Đồng bộ lại" either - asking TikTok with
       a revoked credential would put an error box under a panel that already
       says "kết nối lại". */
    expect(
      screen.queryByRole("button", { name: "Đồng bộ ngay" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Đồng bộ lại" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kết nối lại" })).toBeEnabled();
  });
});

describe("62. TikTok never shows an account chooser", () => {
  it("goes straight from connect to connected, because consent binds one account", async () => {
    stubFetch(routes(ttState({ connection: null })));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    await screen.findByRole("button", { name: "Kết nối TikTok" });
    // The Meta flow's chooser heading. Its absence is the assertion: TikTok
    // consent authorizes exactly one account, so offering a list of one would
    // be ceremony - and `PENDING_SELECTION` is a state a TikTok connection is
    // never put into.
    expect(screen.queryByText("Chọn tài khoản TikTok")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Chọn" }),
    ).not.toBeInTheDocument();
  });
});

// --- 63-74: THE ACCOUNT PANEL (Step 1F.2.9) --------------------------------
//
// Every block below is evidence for one TikTok scope, which is what this panel
// is for: app review asks an applicant to show that each requested permission
// is used for what they said, and before this step the only way to see any of
// it was a CLI. The tests are written the way the reviewer will read the
// screen - by looking for the number, the handle, the cover and the permission
// row, in the panel, with no developer tooling involved.
//
// The rule running through all of them: **an unavailable value is never a
// zero.** Tests 68, 69 and 70 are the ones that pin it down.

describe("63. the connected panel shows who TikTok says this account is", () => {
  it("renders the avatar, display name and handle from user.info.basic/profile", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    // The account panel is a second request after the connection state, so the
    // wait is on something only it renders.
    await screen.findByText("Video gần đây");
    expect(screen.getByText("Bác sĩ thẩm mỹ")).toBeInTheDocument();
    // Two nodes carry the handle - the connection details and the panel's
    // identity block - and both are the server's `@bsvutrongtien`.
    expect(screen.getAllByText("@bsvutrongtien").length).toBeGreaterThan(0);
    const avatar = document.querySelector('img[src*="tiktokcdn.com/avatar"]');
    expect(avatar).not.toBeNull();
    // A referrer would tell TikTok's CDN which MeoChat page loaded the image.
    expect(avatar).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(
      screen.getByRole("link", { name: "Mở hồ sơ trên TikTok" }),
    ).toHaveAttribute("href", "https://www.tiktok.com/@bsvutrongtien");
  });

  it("says the account is unverified only when TikTok said so", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    expect(await screen.findByText("Chưa xác minh")).toBeInTheDocument();
  });
});

describe("64. the four lifetime counters are shown as lifetime counters", () => {
  it("renders followers, following, total likes and video count", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    const panel = await statsBlock();
    expect(within(panel).getByText("128.400")).toBeInTheDocument();
    expect(within(panel).getByText("312")).toBeInTheDocument();
    expect(within(panel).getByText("4.500.000")).toBeInTheDocument();
    expect(within(panel).getByText("382")).toBeInTheDocument();
  });

  it("never presents the lifetime likes total as a windowed figure", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    // The card says "Tổng lượt thích" and carries the qualifier. A panel that
    // called this "Likes 30 ngày" would be off by three orders of magnitude,
    // which is the exact mistake `tiktok_total_likes` exists to prevent.
    expect(await screen.findByText("Tổng lượt thích")).toBeInTheDocument();
    expect(
      screen.getByText("Tổng tích luỹ từ trước tới nay"),
    ).toBeInTheDocument();
  });
});

describe("65. recent public videos come from video.list", () => {
  it("renders a bounded list with covers, counters and a TikTok link", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(await screen.findByText("Video gần đây")).toBeInTheDocument();
    expect(
      screen.getByText("Tân trang cô bé có đau không?"),
    ).toBeInTheDocument();
    expect(screen.getByText("125.400 lượt xem")).toBeInTheDocument();
    expect(
      screen.getByText(/8\.200 thích · 320 bình luận · 56 chia sẻ/),
    ).toBeInTheDocument();
    expect(
      document.querySelector('img[src*="tiktokcdn.com/cover-1"]'),
    ).not.toBeNull();

    const links = screen.getAllByRole("link", { name: "Xem trên TikTok" });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute(
      "href",
      "https://www.tiktok.com/@bsvutrongtien/video/1",
    );
    // Opening somebody's TikTok must not hand that tab a handle on MeoChat.
    expect(links[0]).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("offers Xem thêm only while the server says more exist", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");
    // `videos_has_more` is false in the fixture, and that - not the number of
    // cards on screen - is what decides.
    expect(
      screen.queryByRole("button", { name: "Xem thêm" }),
    ).not.toBeInTheDocument();
  });
});

describe("66. Xem thêm loads the next page with TikTok's own cursor", () => {
  it("appends the next page and stops when the server says there is no more", async () => {
    const page2 = {
      ...OVERVIEW,
      videos: [
        {
          ...OVERVIEW.videos[0],
          video_id: "v3",
          title: "Trang hai",
          share_url: "https://www.tiktok.com/@bsvutrongtien/video/3",
        },
      ],
      videos_cursor: 12,
      videos_has_more: false,
    };
    // The second page is matched on the cursor the *first* page handed back,
    // which is the assertion hiding inside this stub: a panel that invented its
    // own offset, or dropped the cursor, would never reach this route.
    const fetchMock = stubFetch([
      { match: "cursor=6", body: page2 },
      ...routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? { ...route, body: { ...OVERVIEW, videos_has_more: true, videos_cursor: 6 } }
          : route,
      ),
    ]);

    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");

    await userEvent.click(screen.getByRole("button", { name: "Xem thêm" }));
    expect(await screen.findByText("Trang hai")).toBeInTheDocument();
    // The first page is still on screen: "Xem thêm" appends, it does not
    // replace, and a reviewer scrolling back must not lose what they saw.
    expect(
      screen.getByText("Tân trang cô bé có đau không?"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Xem thêm" }),
    ).not.toBeInTheDocument();

    const calls = (
      fetchMock as unknown as { calls: Array<{ url: string }> }
    ).calls;
    expect(
      calls.some((call) => call.url.includes("cursor=6")),
    ).toBe(true);
  });
});

describe("67. the granted permissions are shown, refusals included", () => {
  it("lists all four scopes with a human name and the raw scope string", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    await userEvent.click(await screen.findByText("Quyền truy cập"));
    for (const label of [
      "Thông tin cơ bản",
      "Hồ sơ TikTok",
      "Thống kê tài khoản",
      "Danh sách video công khai",
    ]) {
      expect(screen.getAllByText(new RegExp(label)).length).toBeGreaterThan(0);
    }
    for (const scope of [
      "user.info.basic",
      "user.info.profile",
      "user.info.stats",
      "video.list",
    ]) {
      expect(screen.getByText(scope)).toBeInTheDocument();
    }
  });

  it("marks a scope TikTok did not grant, rather than omitting it", async () => {
    stubFetch(
      routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? {
              ...route,
              body: overview({
                granted_scopes: ["user.info.basic"],
                scopes: OVERVIEW.scopes.map((scope) => ({
                  ...scope,
                  granted: scope.scope === "user.info.basic",
                })),
              }),
            }
          : route,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    await userEvent.click(await screen.findByText("Quyền truy cập"));
    // An app awaiting review must not look identical to one fully approved.
    expect(screen.getAllByText(/chưa được cấp/).length).toBe(3);
    expect(screen.getAllByText(/đã cấp/).length).toBe(1);
  });
});

describe("68. a refused stats scope is blank and explained, never zero", () => {
  it("shows dashes and names the missing permission", async () => {
    stubFetch(
      routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? {
              ...route,
              body: overview({
                stats: {
                  follower_count: null,
                  following_count: null,
                  likes_count: null,
                  video_count: null,
                  availability: "not_permitted",
                  availability_label: "Chưa được cấp quyền",
                },
              }),
            }
          : route,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    const panel = await statsBlock();
    // Four dashes, and no fabricated zeroes. Showing a TikTok reviewer "0
    // followers" for their own account is the worst thing this screen could do.
    expect(within(panel).getAllByText("—")).toHaveLength(4);
    expect(within(panel).queryByText("0")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Chưa hiển thị được thống kê tài khoản/),
    ).toBeInTheDocument();
    // And the identity above it is untouched.
    expect(screen.getAllByText("@bsvutrongtien").length).toBeGreaterThan(0);
  });
});

describe("69. a refused video.list is explained, not silently empty", () => {
  it("says which permission is missing and keeps the rest of the panel", async () => {
    stubFetch(
      routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? {
              ...route,
              body: overview({
                videos: [],
                videos_availability: "not_permitted",
                videos_availability_label: "Chưa được cấp quyền",
                videos_has_more: false,
              }),
            }
          : route,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(
      await screen.findByText(/Chưa hiển thị được danh sách video/),
    ).toBeInTheDocument();
    expect(within(await statsBlock()).getByText("128.400")).toBeInTheDocument();
  });

  it("tells a quiet account apart from a refused one", async () => {
    stubFetch(
      routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? {
              ...route,
              body: overview({
                videos: [],
                videos_availability: "empty",
                videos_availability_label: "TikTok trả về rỗng",
              }),
            }
          : route,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(
      await screen.findByText("Tài khoản này chưa có video công khai nào."),
    ).toBeInTheDocument();
  });
});

describe("70. per-video counters that TikTok withheld render as dashes", () => {
  it("keeps the cards and says the numbers are missing", async () => {
    stubFetch(
      routes(ttState()).map((route) =>
        route.match === "/connections/tiktok/overview"
          ? {
              ...route,
              body: overview({
                videos: OVERVIEW.videos.map((video) => ({
                  ...video,
                  view_count: null,
                  like_count: null,
                  comment_count: null,
                  share_count: null,
                })),
                video_counters_availability: "not_returned",
                video_counters_availability_label:
                  "TikTok không trả về trường này",
              }),
            }
          : route,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    expect(await screen.findByText("Video gần đây")).toBeInTheDocument();
    // The card survives; only its numbers are absent, and they are absent as
    // dashes rather than as an account that nobody watched.
    expect(
      screen.getByText("Tân trang cô bé có đau không?"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("— lượt xem")).toHaveLength(2);
    expect(screen.getByText(/Chỉ số từng video:/)).toBeInTheDocument();
  });
});

describe("71. Đồng bộ lại re-reads TikTok without a confirmation dialog", () => {
  it("posts to the refresh route and reports success", async () => {
    const fetchMock = stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");

    await userEvent.click(screen.getByRole("button", { name: "Đồng bộ lại" }));

    expect(await screen.findByText(/Đã cập nhật dữ liệu TikTok/)).toBeInTheDocument();
    const calls = (
      fetchMock as unknown as {
        calls: Array<{ url: string; method: string }>;
      }
    ).calls;
    const refreshed = calls.filter(
      (call) =>
        call.method === "POST" && call.url.includes("/connections/tiktok/refresh"),
    );
    expect(refreshed).toHaveLength(1);
    // No second sync system: the panel does not also poke /metrics/sync, because
    // the refresh route already asked for the snapshot through the same claim.
    expect(
      calls.filter((call) => call.url.includes("/metrics/sync")),
    ).toHaveLength(0);
  });

  it("asks nothing first, because re-reading numbers destroys nothing", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");

    await userEvent.click(screen.getByRole("button", { name: "Đồng bộ lại" }));
    // The confirmation dialog the destructive controls on this page do use.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await screen.findByText(/Đã cập nhật dữ liệu TikTok/);
  });

  it("shows a MeoChat sentence when TikTok refuses, never a raw payload", async () => {
    stubFetch([
      {
        match: "/connections/tiktok/refresh",
        method: "POST",
        status: 400,
        // The API's own envelope. The message is the sentence
        // `error_message(RATE_LIMITED, TIKTOK)` composes on the server; TikTok's
        // own error code and log id never travel in it.
        body: {
          error: {
            code: "pr.validation",
            message: "TikTok đang giới hạn truy vấn. Hệ thống sẽ thử lại sau.",
            details: { reason: "provider_error", error_code: "RATE_LIMITED" },
          },
        },
      },
      ...routes(ttState()),
    ]);
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");

    await userEvent.click(screen.getByRole("button", { name: "Đồng bộ lại" }));
    expect(
      await screen.findByText(/TikTok đang giới hạn truy vấn/),
    ).toBeInTheDocument();
    // TikTok's own vocabulary never reaches the screen.
    expect(screen.queryByText(/rate_limit_exceeded/)).not.toBeInTheDocument();
    expect(screen.queryByText(/log_id/)).not.toBeInTheDocument();
  });
});

describe("72. the panel renders no credential of any kind", () => {
  it("shows scope names and an Open ID, and nothing that could be replayed", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);
    await screen.findByText("Video gần đây");
    await userEvent.click(screen.getByText("Quyền truy cập"));

    const rendered = document.body.innerHTML;
    for (const forbidden of [
      "access_token",
      "refresh_token",
      "client_secret",
      "client_key",
      "encrypted_credential",
      "Bearer",
    ]) {
      expect(rendered, forbidden).not.toContain(forbidden);
    }
    // What is meant to be visible: the public account id and the scope names
    // somebody consented to.
    expect(rendered).toContain(OPEN_ID);
    expect(rendered).toContain("user.info.stats");
  });
});

describe("73. returning from TikTok's consent screen opens the same channel", () => {
  it("selects the channel named in the callback and says it worked", async () => {
    stubFetch(routes(ttState()));
    // Exactly the URL `_connection_redirect` builds after a successful TikTok
    // authorization. Before Step 1F.2.9 this page read neither parameter, so
    // consent succeeded and the reviewer came back to an empty channel list.
    NAV.arriveAt(
      `/pr/channels?connection=connected&channel=${TT_CHANNEL.id}`,
    );
    renderWithQuery(<ChannelsPage />);

    expect(
      await screen.findByText(/Đã kết nối TikTok thành công/),
    ).toBeInTheDocument();
    // The channel detail opened on its own - nobody clicked a card.
    await screen.findByText("Video gần đây");
    expect(screen.getAllByText("@bsvutrongtien").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Đã kết nối").length).toBeGreaterThan(0);
  });

  it("says what happened when consent was declined, and opens nothing", async () => {
    stubFetch(routes(ttState({ connection: null })));
    NAV.arriveAt("/pr/channels?connection=denied");
    renderWithQuery(<ChannelsPage />);

    expect(
      await screen.findByText(/Bạn đã huỷ cấp quyền/),
    ).toBeInTheDocument();
  });

  it("never echoes an unknown status token back onto the page", async () => {
    stubFetch(routes(ttState({ connection: null })));
    NAV.arriveAt("/pr/channels?connection=%3Cimg+src%3Dx%3E");
    renderWithQuery(<ChannelsPage />);

    await screen.findByText(TT_CHANNEL.name);
    // The set of status tokens is closed and the server chooses from it. A
    // query parameter is attacker-controlled text, and this banner is the one
    // place on the page that would otherwise print it.
    expect(document.body.innerHTML).not.toContain("img src=x");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("74. a connected TikTok channel does not lead with manual entry", () => {
  it("demotes the manual form and points at the live sync instead", async () => {
    stubFetch(routes(ttState()));
    renderWithQuery(<ChannelsPage />);
    await open(TT_CHANNEL.name);

    // Still available - a connected channel may still need a figure the
    // Display API does not serve - but no longer the headline action, because
    // making a TikTok reviewer type numbers the API already returns would be a
    // confusing thing to put in a demo video.
    expect(
      await screen.findByRole("button", { name: "+ Nhập tay (bổ sung)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "+ Ghi nhận chỉ số" }),
    ).not.toBeInTheDocument();
  });
});
