/**
 * Step 1F.2.4b - the YouTube connection panel, in the browser.
 *
 * Numbered 57-70, following the requirement numbering the step was specified
 * with.
 *
 * The browser decides nothing here, and the assertions are shaped to prove it.
 * Whether a connect button appears is `can_manage_connection` from the server;
 * whether it appears *at all* is `supported`; every state word is a `*_label`
 * field. A test that passed because the component matched on `"YOUTUBE"` or on
 * `state === "CONNECTED"` and rendered its own Vietnamese would be testing a
 * second implementation of rules that live on the server.
 *
 * The other half is what the panel must never say. A connector whose credential
 * has been revoked must not wear a healthy badge over data that stopped moving
 * three weeks ago (test 66), a failed sync must not leak Google's own words
 * onto a Vietnamese screen (test 67), and a channel with a connector must still
 * tell the truth about a manual reading that happens to be newer (test 70).
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

const PLATFORM = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  code: "YOUTUBE",
  name: "YouTube",
  status: "ACTIVE",
  policy_grounded: false,
};

const BRAND = { id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", code: "APEXMED", name: "Apexmed" };

const CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-0007",
  name: "Apex Media",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: BRAND.id,
  platform_id: PLATFORM.id,
  tier: null,
  url: "https://www.youtube.com/@apexmedia",
  platform_code: "YOUTUBE",
  policy_grounded_platform: false,
  platform: "YOUTUBE",
  platform_label: "YouTube",
  platform_name: "YouTube",
  handle: "@apexmedia",
  external_id: "UCabcdefghijklmnopqrstuv",
  metrics_status: "CONNECTED_API",
  metrics_status_label: "Đã kết nối API",
  latest_captured_at: "2026-08-20T02:00:00Z",
  followers: 124812,
  days_since_capture: 0,
  latest_source: "API",
};

/**
 * A Website channel, for the "no connector exists" screen.
 *
 * This was a TikTok channel until Step 1F.2.6 gave TikTok a connector. The
 * fixture moved rather than the test being deleted: the screen it exercises is
 * still reachable and still matters - Website and Other have no connector and
 * are told so in words rather than by a button that always fails.
 */
const UNCONNECTABLE_CHANNEL = {
  ...CHANNEL,
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  code: "CH-0004",
  name: "Apexmed Website",
  platform: "WEBSITE",
  platform_label: "Website",
  platform_name: "Website",
  platform_code: "WEBSITE",
  metrics_status: "MANUAL",
  metrics_status_label: "Dữ liệu thủ công",
  latest_source: "MANUAL",
};

const CONNECTION = {
  id: "11111111-1111-1111-1111-111111111111",
  channel_id: CHANNEL.id,
  provider: "YOUTUBE",
  state: "CONNECTED",
  state_label: "Đã kết nối",
  provider_account_id: "UCabcdefghijklmnopqrstuv",
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

const CONNECTED_STATE = {
  channel_id: CHANNEL.id,
  supported: true,
  configured: true,
  provider: "YOUTUBE",
  // Step 1F.2.4c: the panel renders the platform's name from the server rather
  // than from a table of its own, so every connector gets the right word.
  provider_label: "YouTube",
  connection: CONNECTION,
  can_manage_connection: true,
  auto_sync_label: "Hàng ngày",
};

const DISCONNECTED_STATE = { ...CONNECTED_STATE, connection: null };

const UNSUPPORTED_STATE = {
  channel_id: UNCONNECTABLE_CHANNEL.id,
  supported: false,
  configured: true,
  provider: null,
  provider_label: null,
  connection: null,
  can_manage_connection: true,
  auto_sync_label: null,
};

const API_SNAPSHOT = {
  id: "22222222-2222-2222-2222-222222222222",
  channel_id: CHANNEL.id,
  captured_at: "2026-08-20T02:00:00Z",
  source: "API",
  source_label: "Tự động từ nền tảng",
  recorded_by_user_id: null,
  recorded_by_name: null,
  followers: 124812,
  following: null,
  posts_count: 412,
  views_7d: 5000,
  views_30d: 21000,
  reach_7d: null,
  reach_30d: null,
  impressions_7d: null,
  impressions_30d: null,
  engagements_7d: null,
  engagements_30d: null,
  likes_30d: 300,
  comments_30d: 42,
  shares_30d: 11,
  extra_metrics: {
    youtube_total_view_count: 98765432,
    youtube_analytics_start_30d: "2026-07-20",
    youtube_analytics_end_30d: "2026-08-18",
  },
};

const MANUAL_SNAPSHOT = {
  ...API_SNAPSHOT,
  id: "33333333-3333-3333-3333-333333333333",
  captured_at: "2026-08-19T02:00:00Z",
  source: "MANUAL",
  source_label: "Nhập thủ công",
  recorded_by_user_id: SESSION.user_id,
  recorded_by_name: SESSION.full_name,
  followers: 124000,
  extra_metrics: null,
};

const METRICS = {
  channel_id: CHANNEL.id,
  status: "CONNECTED_API",
  status_label: "Đã kết nối API",
  latest: API_SNAPSHOT,
  previous: MANUAL_SNAPSHOT,
  trend: { delta: 812, delta_pct: 0.7 },
  history: [API_SNAPSHOT, MANUAL_SNAPSHOT],
  total: 2,
  limit: 30,
  offset: 0,
  days_since_capture: 0,
  analytics: analyticsFrom(API_SNAPSHOT),
  can_record_metrics: true,
  has_history: true,
};

const PEOPLE = [{ user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role }];

/**
 * The channel screen's routes.
 *
 * `/connection` and `/metrics` come before the bare channel routes: `stubFetch`
 * matches on the first substring hit and every one of these URLs also contains
 * "/api/pr/channels".
 */
const routes = (
  connection: unknown = CONNECTED_STATE,
  channel: Record<string, unknown> = CHANNEL,
  metrics: unknown = METRICS,
  capabilities: string[] = ["PR_CHANNEL_MANAGE"],
) => [
  { match: "/api/pr/dashboard", body: { ...DASHBOARD, my_capabilities: capabilities } },
  { match: "/api/pr/platforms", body: [PLATFORM] },
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/connection", body: connection },
  { match: "/metrics", body: metrics },
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

async function openChannel(name: string): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 57-59: which channels get a connector at all -------------------------

describe("57. a YouTube channel is offered a connection", () => {
  it("shows Kết nối YouTube when nothing is connected yet", async () => {
    stubFetch(routes(DISCONNECTED_STATE));
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByRole("button", { name: "Kết nối YouTube" })).toBeInTheDocument();
    expect(screen.getByText(/Kết nối để MeoChat tự lấy số liệu/)).toBeInTheDocument();
  });
});

describe("58. a channel with no connector is told so in words", () => {
  it("shows no connect control for a Website channel, and explains why", async () => {
    stubFetch(routes(UNSUPPORTED_STATE, UNCONNECTABLE_CHANNEL));
    renderWithQuery(<ChannelsPage />);
    await openChannel(UNCONNECTABLE_CHANNEL.name);

    expect(
      await screen.findByText(/Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này/),
    ).toBeInTheDocument();
    // No dead button. One that always failed would teach people the panel lies.
    expect(screen.queryByRole("button", { name: /Kết nối/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Đồng bộ ngay" })).not.toBeInTheDocument();
    // And manual entry is untouched - which is the whole point of saying this
    // rather than hiding the section.
    expect(screen.getByText(/ghi nhận thủ công như bình thường/)).toBeInTheDocument();
  });
});

describe("58a. a deployment without OAuth configured says so", () => {
  it("explains that the server is not set up rather than that the feature is missing", async () => {
    stubFetch(routes({ ...DISCONNECTED_STATE, configured: false }));
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    // The platform's name comes from the server, so this sentence reads
    // "Cấu hình YouTube…" here and "Cấu hình Facebook…" on a Page.
    expect(await screen.findByText(/Cấu hình YouTube chưa sẵn sàng/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kết nối YouTube" })).toBeDisabled();
  });
});

// --- 59-61: what a connected channel shows --------------------------------

describe("59. the bound account is shown, not assumed", () => {
  it("renders the YouTube channel title and its channel ID", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    // "Apex Media" is both the PR channel's name and the bound YouTube
    // account's, which is the ordinary case and exactly why the assertion is
    // scoped: what matters is that the *connection* names the account it
    // resolved, not that the string appears somewhere on the page.
    const panel = (await screen.findByText("Kênh")).closest("dl");
    expect(panel).not.toBeNull();
    expect(within(panel as HTMLElement).getByText("Apex Media")).toBeInTheDocument();
    // The id is what a manager checks when they suspect they authorized the
    // wrong Google account, so it is shown rather than hidden as "technical".
    expect(within(panel as HTMLElement).getByText("UCabcdefghijklmnopqrstuv")).toBeInTheDocument();
  });
});

describe("60. connection state and sync state are shown separately", () => {
  it("renders both labels, from the server", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByText("Đã kết nối")).toBeInTheDocument();
    expect(screen.getByText("Hàng ngày")).toBeInTheDocument();
  });

  it("says a connection is healthy even when its last run failed", async () => {
    // The case one combined "status" string could not express: the credential
    // works, the last attempt did not, and the two need different responses.
    stubFetch(
      routes({
        ...CONNECTED_STATE,
        connection: {
          ...CONNECTION,
          sync_status: "FAILED",
          sync_status_label: "Đồng bộ thất bại",
          last_sync_error_code: "RATE_LIMITED",
          last_sync_error_message: "YouTube đang giới hạn truy vấn. Hệ thống sẽ thử lại sau.",
        },
      }),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByText("Đã kết nối")).toBeInTheDocument();
    expect(screen.getByText(/Đồng bộ thất bại/)).toBeInTheDocument();
    // Not told to reauthorize: nothing is wrong with the credential.
    expect(screen.queryByText(/cần xác thực lại/i)).not.toBeInTheDocument();
  });
});

describe("61. the last successful sync is shown, with staleness", () => {
  it("says how long it has been when a channel has gone quiet", async () => {
    stubFetch(
      routes({
        ...CONNECTED_STATE,
        connection: { ...CONNECTION, days_since_success: 4 },
      }),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByText(/Đã 4 ngày chưa đồng bộ thành công/)).toBeInTheDocument();
  });

  it("says so plainly when nothing has been synced yet", async () => {
    stubFetch(
      routes({
        ...CONNECTED_STATE,
        connection: {
          ...CONNECTION,
          sync_status: "NEVER_SYNCED",
          sync_status_label: "Chưa đồng bộ lần nào",
          last_sync_succeeded_at: null,
          days_since_success: null,
        },
      }),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByText(/Chưa đồng bộ lần nào/)).toBeInTheDocument();
  });
});

// --- 62-65: the controls, and who gets them -------------------------------

describe("62. Đồng bộ ngay is offered to a manager", () => {
  it("posts to the sync endpoint and refetches", async () => {
    const fetchMock = stubFetch([
      { match: "/metrics/sync", method: "POST", status: 202, body: CONNECTION },
      ...routes(),
    ]);
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    await userEvent.click(await screen.findByRole("button", { name: "Đồng bộ ngay" }));
    await waitFor(() => {
      const posted = (
        fetchMock as unknown as { calls: Array<{ url: string; method: string }> }
      ).calls.find((call) => call.method === "POST" && call.url.includes("/metrics/sync"));
      expect(posted).toBeTruthy();
    });
  });
});

describe("63. somebody who may not manage the connection gets no controls", () => {
  it("hides every control on the server's flag, not on a role string", async () => {
    stubFetch(
      routes(
        { ...CONNECTED_STATE, can_manage_connection: false },
        CHANNEL,
        { ...METRICS, can_record_metrics: false },
        ["PR_TEAM_LEAD_REVIEW"],
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    // The state is still visible - reading is open, managing is not.
    expect(await screen.findByText("Đã kết nối")).toBeInTheDocument();
    expect(screen.getAllByText("Apex Media").length).toBeGreaterThan(0);
    for (const control of ["Đồng bộ ngay", "Kết nối lại", "Ngắt kết nối"]) {
      expect(screen.queryByRole("button", { name: control })).not.toBeInTheDocument();
    }
  });
});

describe("64-65. reconnect and disconnect are offered on a connected channel", () => {
  it("shows both, and says disconnecting keeps the history", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(await screen.findByRole("button", { name: "Kết nối lại" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ngắt kết nối" })).toBeInTheDocument();
    // The sentence that stops somebody hesitating over the button for the wrong
    // reason: no metric history is lost by disconnecting.
    expect(screen.getByText(/Ngắt kết nối không xoá số liệu đã ghi nhận/)).toBeInTheDocument();
  });
});

// --- 66-67: the two states the panel must not soften ----------------------

describe("66. a revoked credential is stated clearly", () => {
  it("says reauthorize, keeps the history, and offers no sync", async () => {
    stubFetch(
      routes(
        {
          ...CONNECTED_STATE,
          connection: {
            ...CONNECTION,
            state: "ACTION_REQUIRED",
            state_label: "Cần xác thực lại",
            sync_status: "FAILED",
            sync_status_label: "Đồng bộ thất bại",
            last_sync_error_code: "AUTH_REQUIRED",
            last_sync_error_message: "Kết nối YouTube cần xác thực lại.",
          },
        },
        { ...CHANNEL, metrics_status: "ACTION_REQUIRED", metrics_status_label: "Cần xác thực lại" },
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    // Said twice on purpose - once as the connection's own error and once as
    // the banner explaining what it means for the data - so the assertion is on
    // "at least once" rather than on exactly one.
    await waitFor(() =>
      expect(screen.getAllByText(/Kết nối YouTube cần xác thực lại\./).length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/Số liệu đã lấy trước đó vẫn còn nguyên/)).toBeInTheDocument();
    // Syncing is pointless until somebody reauthorizes, so it is not offered.
    expect(screen.getByRole("button", { name: "Đồng bộ ngay" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Kết nối lại" })).toBeEnabled();
  });
});

describe("67. a sync failure never shows the provider's own words", () => {
  it("renders MeoBot's sentence and no raw payload", async () => {
    stubFetch(
      routes({
        ...CONNECTED_STATE,
        connection: {
          ...CONNECTION,
          sync_status: "FAILED",
          sync_status_label: "Đồng bộ thất bại",
          last_sync_error_code: "PROVIDER_UNAVAILABLE",
          last_sync_error_message: "Không kết nối được tới YouTube. Hệ thống sẽ thử lại sau.",
        },
      }),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    expect(
      await screen.findByText(/Không kết nối được tới YouTube\. Hệ thống sẽ thử lại sau\./),
    ).toBeInTheDocument();
    const body = document.body.textContent ?? "";
    for (const leak of ["quotaExceeded", "Traceback", "googleapis.com", "403", "invalid_grant"]) {
      expect(body).not.toContain(leak);
    }
  });
});

// --- 68-70: the timeline, with both kinds of reading ----------------------

describe("68-69. each reading says where it came from", () => {
  it("labels the API reading and the manual one differently", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Tự động từ nền tảng")).toBeInTheDocument();
    expect(within(table).getByText("Nhập thủ công")).toBeInTheDocument();
    // An API reading has no human author, and the table says so rather than
    // naming whoever pressed the button.
    expect(within(table).getByText(SESSION.full_name)).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("shows the Analytics window the numbers actually cover", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    // "Ghi nhận" is when MeoBot fetched; this is what the figures span. The two
    // are different facts and the panel must not merge them into "cập nhật".
    expect(await screen.findByText(/dữ liệu YouTube Analytics từ/)).toBeInTheDocument();
    expect(screen.getByText(/20\/07\/2026 đến 18\/08\/2026/)).toBeInTheDocument();
  });
});

describe("70. a manual reading newer than an API one is not dressed up as API", () => {
  it("says Nhập thủ công for the current figure while the channel stays connected", async () => {
    const manualLatest = {
      ...METRICS,
      latest: { ...MANUAL_SNAPSHOT, captured_at: "2026-08-21T02:00:00Z" },
      previous: API_SNAPSHOT,
      history: [{ ...MANUAL_SNAPSHOT, captured_at: "2026-08-21T02:00:00Z" }, API_SNAPSHOT],
    };
    stubFetch(
      routes(CONNECTED_STATE, { ...CHANNEL, latest_source: "MANUAL" }, manualLatest),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel(CHANNEL.name);

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.getByText(/Nguồn: Nhập thủ công/)).toBeInTheDocument();
    // The connection is still healthy - the badge describes the connector, the
    // source line describes the number, and they are allowed to differ.
    expect(screen.getByText("Đã kết nối")).toBeInTheDocument();
  });
});
