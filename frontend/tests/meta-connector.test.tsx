/**
 * Step 1F.2.4c - Facebook and Instagram in the connection panel.
 *
 * Numbered 79-99, following the requirement numbering the step was specified
 * with: 79-87 Facebook, 88-93 Instagram, 94-99 the mixed picture.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Which platform name appears is `provider_label` from the server; whether a
 * connect control appears at all is `supported`; whether anybody may press it
 * is `can_manage_connection`; every state word is a `*_label`. A test that
 * passed because the component matched on `"FACEBOOK"` and rendered its own
 * Vietnamese would be testing a second implementation of rules that live on the
 * server.
 *
 * The account chooser is the new shape
 * -------------------------------------
 *
 * It exists because Meta consent reaches every Page a person manages, and one
 * marketing manager routinely has a dozen. Tests 80 and 89 are about the
 * chooser showing enough to tell two similarly-named accounts apart - the id,
 * and for Instagram the Page it was reached through - because picking the wrong
 * one binds a channel to somebody else's brand and the numbers look plausible
 * either way.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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

const FB_PLATFORM = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  code: "FACEBOOK",
  name: "Facebook",
  status: "ACTIVE",
  policy_grounded: true,
};

const BRAND = { id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", code: "APEXMED", name: "Apexmed" };

const FB_CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-0009",
  name: "Apex Media Facebook",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: BRAND.id,
  platform_id: FB_PLATFORM.id,
  tier: null,
  url: null,
  platform_code: "FACEBOOK",
  policy_grounded_platform: true,
  platform: "FACEBOOK",
  platform_label: "Facebook",
  platform_name: "Facebook",
  handle: null,
  external_id: null,
  metrics_status: "CONNECTED_API",
  metrics_status_label: "Đã kết nối API",
  latest_captured_at: "2026-08-20T02:00:00Z",
  followers: 124812,
  days_since_capture: 0,
  latest_source: "API",
};

const IG_CHANNEL = {
  ...FB_CHANNEL,
  id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  code: "CH-0010",
  name: "Apex Media Instagram",
  platform: "INSTAGRAM",
  platform_label: "Instagram",
  platform_name: "Instagram",
  platform_code: "INSTAGRAM",
};

const TIKTOK_CHANNEL = {
  ...FB_CHANNEL,
  id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
  code: "CH-0011",
  name: "Dr Tiến TikTok",
  platform: "TIKTOK",
  platform_label: "TikTok",
  platform_name: "TikTok",
  platform_code: "TIKTOK",
  metrics_status: "MANUAL",
  metrics_status_label: "Dữ liệu thủ công",
  latest_source: "MANUAL",
};

const FB_CONNECTION = {
  id: "11111111-1111-1111-1111-111111111111",
  channel_id: FB_CHANNEL.id,
  provider: "FACEBOOK",
  state: "CONNECTED",
  state_label: "Đã kết nối",
  provider_account_id: "111111111111111",
  provider_account_name: "Apex Media",
  provider_account_handle: "@apexmedia",
  sync_status: "SUCCESS",
  sync_status_label: "Đồng bộ thành công",
  last_sync_succeeded_at: "2026-08-20T02:00:00Z",
  last_sync_failed_at: null,
  last_sync_error_code: null,
  last_sync_error_message: null,
  days_since_success: 0,
  auto_sync_enabled: true,
  connected_at: "2026-08-01T02:00:00Z",
};

const IG_CONNECTION = {
  ...FB_CONNECTION,
  id: "22222222-2222-2222-2222-222222222222",
  channel_id: IG_CHANNEL.id,
  provider: "INSTAGRAM",
  provider_account_id: "17841400000000001",
  provider_account_name: "apexmedia",
  provider_account_handle: "@apexmedia",
};

const fbState = (over: Record<string, unknown> = {}) => ({
  channel_id: FB_CHANNEL.id,
  supported: true,
  configured: true,
  provider: "FACEBOOK",
  provider_label: "Facebook",
  connection: FB_CONNECTION,
  can_manage_connection: true,
  auto_sync_label: "Hàng ngày",
  ...over,
});

const igState = (over: Record<string, unknown> = {}) => ({
  channel_id: IG_CHANNEL.id,
  supported: true,
  configured: true,
  provider: "INSTAGRAM",
  provider_label: "Instagram",
  connection: IG_CONNECTION,
  can_manage_connection: true,
  auto_sync_label: "Hàng ngày",
  ...over,
});

const API_SNAPSHOT = {
  id: "33333333-3333-3333-3333-333333333333",
  channel_id: FB_CHANNEL.id,
  captured_at: "2026-08-20T02:00:00Z",
  source: "API",
  source_label: "Tự động từ nền tảng",
  recorded_by_user_id: null,
  recorded_by_name: null,
  followers: 124812,
  following: null,
  posts_count: null,
  views_7d: null,
  views_30d: null,
  reach_7d: 5000,
  reach_30d: null,
  impressions_7d: 700,
  impressions_30d: 21000,
  engagements_7d: 70,
  engagements_30d: 2100,
  likes_30d: null,
  comments_30d: null,
  shares_30d: null,
  extra_metrics: { meta_provider: "FACEBOOK", facebook_page_fan_count: 130500 },
};

const METRICS = {
  channel_id: FB_CHANNEL.id,
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
  analytics: analyticsFrom(API_SNAPSHOT),
  can_record_metrics: true,
  has_history: true,
};

const ACCOUNTS = {
  channel_id: FB_CHANNEL.id,
  provider: "FACEBOOK",
  accounts: [
    { account_id: "111111111111111", name: "Apex Media", handle: null, via: null },
    { account_id: "222222222222222", name: "Apex Clinic", handle: null, via: null },
  ],
};

const IG_ACCOUNTS = {
  channel_id: IG_CHANNEL.id,
  provider: "INSTAGRAM",
  accounts: [
    {
      account_id: "17841400000000001",
      name: "apexmedia",
      handle: "@apexmedia",
      via: "Apex Media",
    },
  ],
};

const PEOPLE = [{ user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role }];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (
  channel: Record<string, unknown>,
  connection: unknown,
  accounts: unknown = ACCOUNTS,
  capabilities: string[] = ["PR_CHANNEL_MANAGE"],
) => [
  { match: "/api/pr/dashboard", body: { ...DASHBOARD, my_capabilities: capabilities } },
  { match: "/api/pr/platforms", body: [FB_PLATFORM] },
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/connections/accounts", body: accounts },
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

async function open(name: string): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 79-87: Facebook ------------------------------------------------------

describe("79. a Facebook channel is offered a Facebook connection", () => {
  it("names the platform from the server, not from a table in the browser", async () => {
    stubFetch(routes(FB_CHANNEL, fbState({ connection: null })));
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    expect(await screen.findByRole("button", { name: "Kết nối Facebook" })).toBeInTheDocument();
    // Not "Kết nối YouTube" - the label follows the channel's own platform.
    expect(screen.queryByRole("button", { name: "Kết nối YouTube" })).not.toBeInTheDocument();
  });
});

describe("80. the Page chooser shows enough to pick the right one", () => {
  it("lists every eligible Page with its id, and binds the chosen one", async () => {
    const fetchMock = stubFetch([
      { match: "/connections/select", method: "POST", body: { connection: FB_CONNECTION } },
      ...routes(
        FB_CHANNEL,
        fbState({
          connection: {
            ...FB_CONNECTION,
            state: "PENDING_SELECTION",
            state_label: "Chờ chọn tài khoản",
          },
        }),
      ),
    ]);
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    expect(await screen.findByText("Chọn Trang Facebook")).toBeInTheDocument();
    expect(screen.getByText("Apex Media")).toBeInTheDocument();
    expect(screen.getByText("Apex Clinic")).toBeInTheDocument();
    // The id is what tells two similarly-named Pages apart.
    expect(screen.getByText("111111111111111")).toBeInTheDocument();
    expect(screen.getByText("222222222222222")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Chọn" })[1]);
    await waitFor(() => {
      const posted = (
        fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
      ).calls.find((c) => c.method === "POST" && c.url.includes("/connections/select"));
      expect(posted?.body).toEqual({ account_id: "222222222222222" });
    });
  });
});

describe("81-82. the bound Page and its sync state are shown", () => {
  it("renders the Page name, the Page ID and the sync status label", async () => {
    stubFetch(routes(FB_CHANNEL, fbState()));
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    const panel = (await screen.findByText("Trang")).closest("dl");
    expect(panel).not.toBeNull();
    expect(within(panel as HTMLElement).getByText("Apex Media")).toBeInTheDocument();
    expect(within(panel as HTMLElement).getByText("111111111111111")).toBeInTheDocument();
    expect(screen.getByText("Đã kết nối")).toBeInTheDocument();
    expect(screen.getByText("Hàng ngày")).toBeInTheDocument();
  });
});

describe("83-85. the management controls", () => {
  it("offers sync, reconnect and disconnect to a manager", async () => {
    stubFetch(routes(FB_CHANNEL, fbState()));
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    for (const control of ["Đồng bộ ngay", "Kết nối lại", "Ngắt kết nối"]) {
      expect(await screen.findByRole("button", { name: control })).toBeInTheDocument();
    }
    expect(screen.getByText(/Ngắt kết nối không xoá số liệu đã ghi nhận/)).toBeInTheDocument();
  });

  it("offers none of them to somebody who may not manage the connection", async () => {
    stubFetch(
      routes(FB_CHANNEL, fbState({ can_manage_connection: false }), ACCOUNTS, [
        "PR_TEAM_LEAD_REVIEW",
      ]),
    );
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    // Reading stays open; managing does not.
    expect(await screen.findByText("Đã kết nối")).toBeInTheDocument();
    for (const control of ["Đồng bộ ngay", "Kết nối lại", "Ngắt kết nối"]) {
      expect(screen.queryByRole("button", { name: control })).not.toBeInTheDocument();
    }
  });
});

describe("86-87. a broken connection says so without leaking Graph", () => {
  it("names the platform in the warning and hides the provider's own words", async () => {
    stubFetch(
      routes(
        FB_CHANNEL,
        fbState({
          connection: {
            ...FB_CONNECTION,
            state: "ACTION_REQUIRED",
            state_label: "Cần xác thực lại",
            sync_status: "FAILED",
            sync_status_label: "Đồng bộ thất bại",
            last_sync_error_code: "AUTH_REQUIRED",
            last_sync_error_message: "Kết nối Facebook cần xác thực lại.",
          },
        }),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    expect(
      await screen.findByText(/Facebook cần kết nối lại để tiếp tục tự động đồng bộ/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Số liệu đã lấy trước đó vẫn còn nguyên/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đồng bộ ngay" })).toBeDisabled();

    const body = document.body.textContent ?? "";
    for (const leak of ["OAuthException", "error_subcode", "graph.facebook.com", "code 190"]) {
      expect(body).not.toContain(leak);
    }
  });
});

// --- 88-93: Instagram -----------------------------------------------------

describe("88. an Instagram channel is offered an Instagram connection", () => {
  it("uses Instagram's own words", async () => {
    stubFetch(routes(IG_CHANNEL, igState({ connection: null })));
    renderWithQuery(<ChannelsPage />);
    await open(IG_CHANNEL.name);

    expect(await screen.findByRole("button", { name: "Kết nối Instagram" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Kết nối Facebook" })).not.toBeInTheDocument();
  });
});

describe("89-91. the Instagram chooser names the Page it came through", () => {
  it("shows only accounts that exist, with the Page that reaches them", async () => {
    stubFetch(
      routes(
        IG_CHANNEL,
        igState({
          connection: {
            ...IG_CONNECTION,
            state: "PENDING_SELECTION",
            state_label: "Chờ chọn tài khoản",
          },
        }),
        IG_ACCOUNTS,
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await open(IG_CHANNEL.name);

    expect(await screen.findByText("Chọn tài khoản Instagram")).toBeInTheDocument();
    expect(screen.getByText("apexmedia")).toBeInTheDocument();
    // A Page with no linked professional account is not in the list at all -
    // the server excludes it, so the chooser shows what will work.
    expect(screen.queryByText("Apex Clinic")).not.toBeInTheDocument();
    // And the Page it was reached through is named, for telling two
    // similarly-named accounts apart.
    expect(screen.getByText(/qua Trang Apex Media/)).toBeInTheDocument();
  });
});

describe("92-93. Instagram gets the same state and controls", () => {
  it("shows the account, its id, and the management controls", async () => {
    stubFetch(routes(IG_CHANNEL, igState()));
    renderWithQuery(<ChannelsPage />);
    await open(IG_CHANNEL.name);

    const panel = (await screen.findByText("Tài khoản")).closest("dl");
    expect(within(panel as HTMLElement).getByText("apexmedia")).toBeInTheDocument();
    expect(within(panel as HTMLElement).getByText("17841400000000001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đồng bộ ngay" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ngắt kết nối" })).toBeInTheDocument();
  });
});

// --- 94-99: the mixed picture --------------------------------------------

describe("95. TikTok is still unsupported, in words", () => {
  it("offers no connect control and explains why", async () => {
    stubFetch(
      routes(TIKTOK_CHANNEL, {
        channel_id: TIKTOK_CHANNEL.id,
        supported: false,
        configured: true,
        provider: null,
        provider_label: null,
        connection: null,
        can_manage_connection: true,
        auto_sync_label: null,
      }),
    );
    renderWithQuery(<ChannelsPage />);
    await open(TIKTOK_CHANNEL.name);

    expect(
      await screen.findByText(/Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Kết nối/ })).not.toBeInTheDocument();
    // Manual entry is untouched, which is why the section says this rather than
    // disappearing.
    expect(screen.getByText(/ghi nhận thủ công như bình thường/)).toBeInTheDocument();
  });
});

describe("96-97. a Meta API reading is labelled as one", () => {
  it("says Tự động từ nền tảng and names no recorder", async () => {
    stubFetch(routes(FB_CHANNEL, fbState()));
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.getByText(/Nguồn: Tự động từ nền tảng/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    // An API reading has no human author.
    expect(within(table).queryByText(SESSION.full_name)).not.toBeInTheDocument();
  });

  it("draws no card for a metric Facebook does not report", async () => {
    stubFetch(routes(FB_CHANNEL, fbState()));
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    await screen.findByText("Chỉ số gần nhất");
    // reach_30d and views_* are empty for Facebook by design - Meta has no
    // 30-day unique reach and no comparable account-level views metric.
    //
    // Counted rather than queried, because "Views 30 ngày" and "Reach 30 ngày"
    // are also *column headers* in the history table below, which are there
    // whatever this reading contains. One occurrence means the header alone and
    // no summary card; two would mean a card was drawn for an empty metric.
    expect(screen.queryAllByText("Views 30 ngày")).toHaveLength(1);
    expect(screen.queryAllByText("Reach 30 ngày")).toHaveLength(1);
    // What Facebook does report gets a card - and is not a table column, so one
    // occurrence here means the card.
    expect(screen.queryAllByText("Impressions 30 ngày")).toHaveLength(1);
    expect(screen.getByText("21.000")).toBeInTheDocument();
  });
});

describe("98-99. a manual reading can be newer than the API one", () => {
  it("labels the reading MANUAL while the connection stays connected", async () => {
    const manual = {
      ...API_SNAPSHOT,
      id: "44444444-4444-4444-4444-444444444444",
      captured_at: "2026-08-21T02:00:00Z",
      source: "MANUAL",
      source_label: "Nhập thủ công",
      recorded_by_user_id: SESSION.user_id,
      recorded_by_name: SESSION.full_name,
      extra_metrics: null,
    };
    stubFetch([
      { match: "/connections/accounts", body: ACCOUNTS },
      { match: "/connection", body: fbState() },
      {
        match: "/metrics",
        body: { ...METRICS, latest: manual, history: [manual, API_SNAPSHOT], total: 2 },
      },
      ...routes(FB_CHANNEL, fbState()).slice(3),
    ]);
    renderWithQuery(<ChannelsPage />);
    await open(FB_CHANNEL.name);

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.getByText(/Nguồn: Nhập thủ công/)).toBeInTheDocument();
    // The badge describes the connector; the source line describes the number.
    // They are allowed to differ and must not be collapsed.
    expect(screen.getByText("Đã kết nối")).toBeInTheDocument();
  });
});
