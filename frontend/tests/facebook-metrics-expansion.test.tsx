/**
 * Step 1F.2.4d - the analytics panel a manager actually reads.
 *
 * The browser decides nothing here either, and the assertions are shaped to
 * prove it: every growth figure, every rate and every average arrives computed
 * from the server, and the one sentence about metrics Meta retired is the
 * server's words. A test that passed because the component divided engagements
 * by followers would be testing a second implementation of a rule that has to
 * be reproducible in a report - which a number computed in a browser is not.
 *
 * What is really being asserted
 * -----------------------------
 *
 * **Blank is not zero, on screen.** This is the rule the whole step turns on and
 * it is only observable here: the API can be as careful as it likes about
 * `null`, and if the panel renders `0` the care was wasted. So the tests below
 * check the rendered text, in both directions - a Page that got no shares shows
 * `0`, a Page whose share count could not be read shows `—`.
 *
 * **A card that can never fill is not drawn.** Twelve fixed cards, always
 * present, so two channels can be compared side by side. The platform-specific
 * ones - reach, views, video plays - appear only where the platform reports
 * them, because a permanently blank card in a fixed grid trains people to
 * ignore that position.
 *
 * **A comparison says what it compared.** "30 ngày" is what was asked for;
 * "so với 33 ngày trước" is what was found, and the second is what the card
 * prints.
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

const PLATFORM = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  code: "FACEBOOK",
  name: "Facebook",
  status: "ACTIVE",
  policy_grounded: true,
};

const BRAND = {
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  code: "APEXMED",
  name: "Apexmed",
};

const CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-0009",
  name: "Apex Media Facebook",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: BRAND.id,
  brand_name: BRAND.name,
  platform_id: PLATFORM.id,
  platform_code: PLATFORM.code,
  platform_name: PLATFORM.name,
  platform_label: "Facebook",
  category_label: "Kênh mở rộng",
  status_label: "Đang hoạt động",
  url: null,
  handle: "@apexmedia",
  notes: null,
  metrics_status: "CONNECTED_API",
  metrics_status_label: "Đã kết nối API",
  latest_captured_at: "2026-08-20T02:00:00Z",
  followers: 130000,
  days_since_capture: 0,
  latest_source: "API",
  connection_state: "CONNECTED",
  sync_status: "SUCCESS",
  last_sync_succeeded_at: "2026-08-20T02:00:00Z",
};

/** A Facebook reading with everything this step can fill in. */
const SNAPSHOT = {
  id: "33333333-3333-3333-3333-333333333333",
  channel_id: CHANNEL.id,
  captured_at: "2026-08-20T02:00:00Z",
  source: "API",
  source_label: "Tự động từ nền tảng",
  recorded_by_user_id: null,
  recorded_by_name: null,
  followers: 130000,
  fans: 134500,
  following: null,
  posts_count: null,
  views_7d: null,
  views_30d: null,
  reach_7d: null,
  reach_30d: null,
  impressions_7d: null,
  impressions_30d: null,
  engagements_7d: 900,
  engagements_30d: 3600,
  posts_count_7d: 4,
  posts_count_30d: 15,
  reactions_30d: 900,
  likes_30d: null,
  comments_30d: 120,
  shares_30d: 30,
  video_views_7d: 1200,
  video_views_30d: 5400,
  extra_metrics: { meta_provider: "FACEBOOK", facebook_page_fan_count: 134500 },
};

const GROWTH = {
  metric: "followers",
  window_days: 30,
  latest: 130000,
  baseline: 124000,
  delta: 6000,
  direction: "UP",
  delta_pct: 4.8,
  baseline_captured_at: "2026-07-18T02:00:00Z",
  baseline_age_days: 33,
};

const FULL_ANALYTICS = analyticsFrom(SNAPSHOT, {
  follower_growth_30d: GROWTH,
  follower_growth_7d: {
    ...GROWTH,
    window_days: 7,
    delta: 1000,
    baseline_age_days: 7,
  },
  engagement_change_30d: {
    ...GROWTH,
    metric: "engagements_30d",
    latest: 3600,
    baseline: 3000,
    delta: 600,
    delta_pct: 20,
    baseline_age_days: 30,
  },
  engagement_per_follower_30d: 0.0277,
  average_engagement_per_post_30d: 70,
  top_post_30d: {
    post_id: "111_1",
    engagements: 480,
    permalink_url: "https://www.facebook.com/111_1",
    created_at: "2026-08-14T02:15:00Z",
    excerpt: "Khai trương chi nhánh mới tại Quận 7",
    reactions: 400,
    comments: 60,
    shares: 20,
  },
  limitation_note:
    "Facebook không còn cung cấp reach/impressions ở cấp Trang (Graph v23), " +
    "nên MeoBot để trống thay vì ước lượng.",
});

const metricsBody = (analytics: unknown = FULL_ANALYTICS, over: Record<string, unknown> = {}) => ({
  channel_id: CHANNEL.id,
  status: "CONNECTED_API",
  status_label: "Đã kết nối API",
  latest: SNAPSHOT,
  previous: null,
  trend: null,
  history: [SNAPSHOT],
  total: 1,
  limit: 30,
  offset: 0,
  days_since_capture: 0,
  analytics,
  can_record_metrics: true,
  has_history: true,
  ...over,
});

const PEOPLE = [
  {
    user_id: SESSION.user_id,
    full_name: SESSION.full_name,
    role: SESSION.role,
  },
];

/** Order matters: `stubFetch` takes the first substring hit. */
const routes = (metrics: unknown = metricsBody()) => [
  { match: "/api/pr/dashboard", body: DASHBOARD },
  { match: "/api/pr/platforms", body: [PLATFORM] },
  { match: "/api/pr/brands", body: [BRAND] },
  {
    match: "/connections/accounts",
    body: { channel_id: CHANNEL.id, provider: "FACEBOOK", accounts: [] },
  },
  {
    match: "/connection",
    body: {
      channel_id: CHANNEL.id,
      provider: "FACEBOOK",
      provider_label: "Facebook",
      supported: true,
      configured: true,
      connection: null,
      can_manage_connection: true,
      auto_sync_label: "Hàng ngày",
    },
  },
  { match: "/metrics", body: metrics },
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
  { match: "/api/pr/people", body: PEOPLE },
];

async function openChannel(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(CHANNEL.name) }));
}

/**
 * The summary-card region, which the history table's headings are not part of.
 *
 * The table below repeats "Followers", "Engagements 30 ngày" and several other
 * headings, so an unscoped query finds two of each. The panel gives the cards
 * their own labelled group precisely so this distinction can be made.
 */
function cards(): HTMLElement {
  return screen.getByRole("group", { name: "Chỉ số tổng hợp" });
}

/** The card with this heading, so a number is asserted against its own label. */
function card(label: string): HTMLElement {
  const heading = within(cards()).getByText(label);
  const box = heading.parentElement;
  expect(box).not.toBeNull();
  return box as HTMLElement;
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 33-38: the six cards management reads first --------------------------

describe("33. the primary cards are the questions a manager asks", () => {
  it("draws them all, in Vietnamese, from the server's analytics", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText("Chỉ số gần nhất")).toBeInTheDocument();
    // Step 1F.2.5 renamed these into the Vietnamese a PR manager uses and
    // grouped them under the three questions they answer. The set is the same
    // set; what changed is that a reader can now tell which three matter.
    for (const label of [
      "Followers",
      "Lượt thích Trang",
      "Tăng Followers 30 ngày",
      "Tương tác 7 ngày",
      "Tương tác 30 ngày",
      "Tương tác / Followers",
    ]) {
      expect(within(cards()).getByText(label)).toBeInTheDocument();
    }
  });
});

describe("34. Lượt thích Trang is not the follower count", () => {
  it("shows both numbers, because Meta split them and reports quote both", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Followers")).getByText("130.000")).toBeInTheDocument();
    expect(within(card("Lượt thích Trang")).getByText("134.500")).toBeInTheDocument();
  });
});

describe("35. the growth card says what it actually compared", () => {
  it("prints the real gap, not the window that was asked for", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Tăng Followers 30 ngày")).getByText("+6.000")).toBeInTheDocument();
    // 33, from `baseline_age_days` - not 30, which is only what was requested.
    // A card claiming "30 ngày" over a 33-day-old baseline is a small lie
    // nobody could detect from the screen.
    expect(screen.getAllByText(/so với 33 ngày trước/).length).toBeGreaterThan(0);
  });
});

describe("36. the engagement rate is rendered, never computed", () => {
  it("shows the server's ratio as a percentage and divides nothing itself", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Tương tác / Followers")).getByText("2,77%")).toBeInTheDocument();
  });
});

describe("37. the secondary cards cover the month's content", () => {
  it("shows posts, the average per post, and the three interaction counts", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Bài đăng 30 ngày")).getByText("15")).toBeInTheDocument();
    // Step 1F.2.5 moved the average and the three interaction counts into the
    // "Chi tiết dữ liệu" disclosure. Still on this screen, still in the labelled
    // group, one click from the front - which is where a number somebody looks
    // up only when they already have a question belongs.
    expect(within(card("Trung bình tương tác / bài")).getByText("70,0")).toBeInTheDocument();
    expect(within(card("Reactions 30 ngày")).getByText("900")).toBeInTheDocument();
    expect(within(card("Comments 30 ngày")).getByText("120")).toBeInTheDocument();
    expect(within(card("Shares 30 ngày")).getByText("30")).toBeInTheDocument();
  });
});

describe("38. video plays are a primary card, with the week beneath the month", () => {
  it("draws the card when there is a figure", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Lượt xem video 30 ngày")).getByText("5.400")).toBeInTheDocument();
    expect(within(card("Lượt xem video 30 ngày")).getByText(/7 ngày: 1.200/)).toBeInTheDocument();
  });

  it("draws it as a blank rather than dropping it when nobody could read it", async () => {
    stubFetch(
      routes(
        metricsBody(
          analyticsFrom({ ...SNAPSHOT, video_views_7d: null, video_views_30d: null }, {}),
        ),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // Step 1F.2.5 promoted this into the fixed grid, so it stays and reads "—".
    // The old behaviour - drop the card - was right while it sat in a loose row
    // of platform-specific extras and is wrong in a row of three a manager
    // compares across channels: a missing card there reads as "this channel is
    // different" when the truth is "nobody has this number".
    expect(within(card("Lượt xem video 30 ngày")).getByText("—")).toBeInTheDocument();
    expect(within(cards()).getByText("Reactions 30 ngày")).toBeInTheDocument();
  });
});

// --- 39-42: blank is not zero ---------------------------------------------

describe("39. an unavailable metric renders as an em dash and never as zero", () => {
  it("shows — for every fixed card the reading could not fill", async () => {
    const bare = {
      ...SNAPSHOT,
      fans: null,
      engagements_7d: null,
      engagements_30d: null,
      posts_count_7d: null,
      posts_count_30d: null,
      reactions_30d: null,
      comments_30d: null,
      shares_30d: null,
      video_views_7d: null,
      video_views_30d: null,
    };
    stubFetch(routes(metricsBody(analyticsFrom(bare))));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    for (const label of [
      "Lượt thích Trang",
      "Tương tác 7 ngày",
      "Tương tác 30 ngày",
      "Bài đăng 30 ngày",
      "Reactions 30 ngày",
      "Comments 30 ngày",
      "Shares 30 ngày",
      "Trung bình tương tác / bài",
      "Tương tác / Followers",
    ]) {
      expect(within(card(label)).getByText("—")).toBeInTheDocument();
    }
    // The one number that *was* read is still a number.
    expect(within(card("Followers")).getByText("130.000")).toBeInTheDocument();
  });
});

describe("40. a measured zero is shown as zero", () => {
  it("keeps 0 for a month that really had no shares", async () => {
    const quiet = {
      ...SNAPSHOT,
      posts_count_30d: 0,
      reactions_30d: 0,
      comments_30d: 0,
      shares_30d: 0,
    };
    stubFetch(routes(metricsBody(analyticsFrom(quiet))));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Shares 30 ngày")).getByText("0")).toBeInTheDocument();
    expect(within(card("Reactions 30 ngày")).getByText("0")).toBeInTheDocument();
    expect(within(card("Bài đăng 30 ngày")).getByText("0")).toBeInTheDocument();
    // And the average of nothing is still unknown, not zero: nothing was
    // divided, because nothing was posted.
    expect(within(card("Trung bình tương tác / bài")).getByText("—")).toBeInTheDocument();
  });
});

describe("41. a channel with no history shows blanks, not a fabricated trend", () => {
  it("says so under the growth card instead of inventing a comparison", async () => {
    stubFetch(routes(metricsBody(analyticsFrom(SNAPSHOT))));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Tăng Followers 30 ngày")).getByText("—")).toBeInTheDocument();
    // "Chưa đủ dữ liệu" wherever a comparison was asked for and none could be
    // made - never "0%", which would be a measurement nobody took.
    expect(within(card("Tăng Followers 30 ngày")).getByText("Chưa đủ dữ liệu")).toBeInTheDocument();
    expect(within(card("Tương tác 30 ngày")).getByText("Chưa đủ dữ liệu")).toBeInTheDocument();
  });
});

describe("42. a channel nobody has measured shows no cards at all", () => {
  it("renders the empty state rather than twelve em dashes", async () => {
    stubFetch(
      routes(
        metricsBody(null, {
          latest: null,
          history: [],
          total: 0,
          has_history: false,
        }),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText(/Chưa có dữ liệu chỉ số/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Chỉ số tổng hợp" })).not.toBeInTheDocument();
    expect(screen.queryByText("Lượt thích Trang")).not.toBeInTheDocument();
  });
});

// --- 43-45: the rest of the panel -----------------------------------------

describe("43. the month's best post is offered as a link", () => {
  it("shows its excerpt, its counts and where to open it", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    const link = screen.getByRole("link", {
      name: /Khai trương chi nhánh mới/,
    });
    expect(link).toHaveAttribute("href", "https://www.facebook.com/111_1");
    // Opened in a new tab, and never with an opener into this session.
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByText(/480 tương tác/)).toBeInTheDocument();
  });
});

describe("44. the panel explains a blank it cannot fill", () => {
  it("renders the server's sentence about metrics Meta retired", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText(/không còn cung cấp reach\/impressions/)).toBeInTheDocument();
    // Reach and impressions get no card at all - see test 38's reasoning.
    expect(within(cards()).queryByText("Reach 30 ngày")).not.toBeInTheDocument();
    expect(within(cards()).queryByText("Impressions 30 ngày")).not.toBeInTheDocument();
  });

  it("says nothing when the server sent no note", async () => {
    stubFetch(routes(metricsBody(analyticsFrom(SNAPSHOT))));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.queryByText(/không còn cung cấp/)).not.toBeInTheDocument();
  });
});

describe("45. the browser computes no metric of its own", () => {
  it("holds no arithmetic on followers, engagements or posts", () => {
    const source = read("app/pr/channels/page.tsx");
    // Growth, rates and averages all arrive computed. A browser that divided
    // engagements by followers would be a second implementation of a number
    // that has to be reproducible in a report.
    expect(source).not.toMatch(/engagements_30d\s*\/\s*/);
    expect(source).not.toMatch(/followers\s*-\s*/);
    expect(source).not.toMatch(/posts_count_30d\s*\)?\s*\*/);
    // And it holds no platform vocabulary it could act on: no branch on a
    // platform code, and no copy of the sentence the server composes.
    expect(source).not.toContain('=== "FACEBOOK"');
    expect(source).not.toContain("không còn cung cấp");
  });

  it("offers every new metric on the manual form, for the platforms with no connector", () => {
    const source = read("app/pr/channels/page.tsx");
    for (const field of [
      "fans",
      "posts_count_7d",
      "posts_count_30d",
      "reactions_30d",
      "video_views_7d",
      "video_views_30d",
    ]) {
      expect(source).toContain(`key: "${field}"`);
    }
  });
});

/** Reads a source file, the way `ux.test.tsx` does, for the guard tests above. */
function read(relative: string): string {
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
  const { join } = require("node:path") as typeof import("node:path");
  return readFileSync(join(process.cwd(), "src", relative), "utf-8");
}
