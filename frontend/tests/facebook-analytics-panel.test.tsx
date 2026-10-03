/**
 * Step 1F.2.5 - the Facebook analytics panel, against the real CH-0004 shape.
 *
 * Every number in this file is a figure production actually reported for
 * CH-0004 on the day this milestone was built: 7.880 followers, 553 engagements
 * over the month, 14 posts, 2.479 video views, 11.168 page views, and - the
 * reason the milestone exists - `reactions_30d` and `comments_30d` both `NULL`
 * because the current Facebook grant does not cover the post interaction
 * summaries.
 *
 * Using the real shape rather than a tidy one is the point. A fixture where
 * every field is populated proves the panel can render a happy path nobody has;
 * this one proves it renders the path everybody has.
 *
 * What is really being asserted
 * -----------------------------
 *
 * **A blank card is never a bare blank.** The panel has three ways to be empty
 * and they are three different sentences: "—" for a number nobody has, "Chưa đủ
 * dữ liệu" for a comparison that could not be made, and a named reason for a
 * metric the platform or the grant refuses. A dashboard that renders all three
 * as an em dash is a dashboard that gets reported as broken every month.
 *
 * **Reach is not a card.** Requirement 6, and the support ticket behind it: a
 * "Reach —" card beside six live numbers reads as a failed sync. It is replaced
 * by a sentence naming Meta as the reason, and there is a test that the card
 * does not come back.
 *
 * **The best post is withheld, not relabelled.** When reactions and comments
 * could not be read, "bài tốt nhất" is the most-shared post wearing a ranking
 * it did not earn. The server declines to send it; this file checks the panel
 * never invents it back.
 *
 * **The browser still computes nothing.** 553 / 7.880 = 7,02% is arithmetic the
 * server did. So is -0,1% over 29 days. A test that passed because a component
 * divided two fields would be testing a second implementation of a number that
 * has to be reproducible in a report.
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
  code: "CH-0004",
  name: "Apexmed Facebook",
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
  handle: "@apexmed",
  notes: null,
  metrics_status: "CONNECTED_API",
  metrics_status_label: "Đã kết nối API",
  latest_captured_at: "2026-08-22T14:42:00Z",
  followers: 7880,
  days_since_capture: 0,
  latest_source: "API",
  connection_state: "CONNECTED",
  sync_status: "SUCCESS",
  last_sync_succeeded_at: "2026-08-22T14:42:00Z",
};

/** CH-0004's canonical columns, exactly as production reports them. */
const SNAPSHOT = {
  id: "33333333-3333-3333-3333-333333333333",
  channel_id: CHANNEL.id,
  captured_at: "2026-08-22T14:42:00Z",
  source: "API",
  source_label: "Tự động từ nền tảng",
  recorded_by_user_id: null,
  recorded_by_name: null,
  followers: 7880,
  fans: 7880,
  following: null,
  posts_count: null,
  views_7d: null,
  views_30d: null,
  reach_7d: null,
  reach_30d: null,
  impressions_7d: null,
  impressions_30d: null,
  engagements_7d: 153,
  engagements_30d: 553,
  posts_count_7d: 5,
  posts_count_30d: 14,
  // The regression this milestone is downstream of: not zero, unknown.
  reactions_30d: null,
  likes_30d: null,
  comments_30d: null,
  // Read successfully, and nobody shared anything. A measurement.
  shares_30d: 0,
  video_views_7d: 415,
  video_views_30d: 2479,
  page_views_7d: 2604,
  page_views_30d: 11168,
  extra_metrics: { meta_provider: "FACEBOOK" },
};

const NOT_PERMITTED_NOTE =
  "Quyền hiện tại của kết nối Facebook không đọc được chỉ số này. " +
  "Cần cấp lại quyền cho ứng dụng mới có số liệu.";
const UNSUPPORTED_NOTE =
  "Meta hiện không còn cung cấp chỉ số này qua API đang dùng, " +
  "nên MeoBot để trống thay vì ước lượng.";

const unavailable = (metric: string, label: string, permitted: boolean) => ({
  metric,
  label,
  availability: permitted ? "NOT_PERMITTED" : "UNSUPPORTED",
  availability_label: permitted ? "Chưa có quyền đọc" : "Không còn được Meta cung cấp",
  note: permitted ? NOT_PERMITTED_NOTE : UNSUPPORTED_NOTE,
});

/** The capability block the server sends for CH-0004 today. */
const CAPABILITIES = {
  reach_available: false,
  impressions_available: false,
  reactions_available: false,
  comments_available: false,
  shares_available: true,
  video_views_available: true,
  page_views_available: true,
  top_post_rankable: false,
  post_fields: {
    shares: "empty",
    comments: "not_permitted",
    reactions: "not_permitted",
  },
  insight_metrics_available: ["page_post_engagements", "page_video_views", "page_views_total"],
  window_30d_end: "2026-08-20",
  unavailable: [
    unavailable("reach_30d", "Reach 30 ngày", false),
    unavailable("impressions_30d", "Impressions 30 ngày", false),
    unavailable("reactions_30d", "Reactions 30 ngày", true),
    unavailable("comments_30d", "Comments 30 ngày", true),
    unavailable("top_post_30d", "Bài tốt nhất 30 ngày", true),
  ],
};

/** -8 followers over a 29-day gap: production's own, and a fall. */
const GROWTH_30D = {
  metric: "followers",
  window_days: 30,
  latest: 7880,
  baseline: 7888,
  delta: -8,
  direction: "DOWN",
  delta_pct: -0.1,
  baseline_captured_at: "2026-07-24T14:42:00Z",
  baseline_age_days: 29,
};

const ANALYTICS = analyticsFrom(SNAPSHOT, {
  follower_growth_30d: GROWTH_30D,
  follower_growth_rate_30d: -0.1,
  engagement_per_follower_30d: 0.0702,
  limitation_note: null,
  capabilities: CAPABILITIES,
});

const metricsBody = (analytics: unknown = ANALYTICS, over: Record<string, unknown> = {}) => ({
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

function cards(): HTMLElement {
  return screen.getByRole("group", { name: "Chỉ số tổng hợp" });
}

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

// --- 1-4: the nine numbers, in Vietnamese ---------------------------------

describe("1. the panel answers the questions a PR manager opens it with", () => {
  it("groups the nine primary cards under the three questions", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    for (const group of ["Khán giả", "Tương tác", "Nội dung & lượt xem"]) {
      expect(within(cards()).getByText(group)).toBeInTheDocument();
    }
    for (const label of [
      "Followers",
      "Lượt thích Trang",
      "Tăng Followers 30 ngày",
      "Tương tác 7 ngày",
      "Tương tác 30 ngày",
      "Tương tác / Followers",
      "Bài đăng 30 ngày",
      "Lượt xem video 30 ngày",
      "Lượt xem Trang 30 ngày",
    ]) {
      expect(within(cards()).getByText(label)).toBeInTheDocument();
    }
  });
});

describe("2. the numbers are CH-0004's own, formatted the way Vietnam reads them", () => {
  it("uses a full stop for thousands and never abbreviates", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Followers")).getByText("7.880")).toBeInTheDocument();
    expect(within(card("Lượt thích Trang")).getByText("7.880")).toBeInTheDocument();
    expect(within(card("Tương tác 7 ngày")).getByText("153")).toBeInTheDocument();
    expect(within(card("Tương tác 30 ngày")).getByText("553")).toBeInTheDocument();
    expect(within(card("Bài đăng 30 ngày")).getByText("14")).toBeInTheDocument();
    expect(within(card("Lượt xem video 30 ngày")).getByText("2.479")).toBeInTheDocument();
    // 11.168 and not "11,2K". The exact number is what somebody reconciles
    // against Meta's own screen, and an abbreviation cannot be checked.
    expect(within(card("Lượt xem Trang 30 ngày")).getByText("11.168")).toBeInTheDocument();
  });
});

describe("3. page views arrive typed, not dug out of extra_metrics", () => {
  it("draws them under their own name and never as Views", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Lượt xem Trang 30 ngày")).getByText(/7 ngày: 2.604/)).toBeInTheDocument();
    // "Views" means watch-style views on every other platform in the channel
    // list. A profile view filed there would make one column mean two things.
    expect(within(cards()).queryByText("Views 30 ngày")).not.toBeInTheDocument();
  });

  it("holds no connector JSON key in the browser at all", () => {
    const source = read("app/pr/channels/page.tsx");
    // The server projects these into `page_views_*`. A screen that knew the
    // string would break silently the day a connector renamed it.
    expect(source).not.toContain("facebook_page_views");
    expect(source).not.toContain("facebook_post_fields");
  });
});

describe("4. the engagement ratio is the server's, and says whose it is", () => {
  it("renders 553 / 7.880 as 7,02% without dividing anything", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Tương tác / Followers")).getByText("7,02%")).toBeInTheDocument();
    // Not Facebook's engagement rate, and the card does not let anybody think
    // it is: Meta publishes no such figure for a Page.
    expect(
      within(card("Tương tác / Followers")).getByText(
        /tỷ lệ nội bộ, không phải chỉ số của Facebook/,
      ),
    ).toBeInTheDocument();
  });
});

// --- 5-7: null, zero and no-baseline stay three different things ----------

describe("5. a measured zero and an unknown are not the same card", () => {
  it("shows 0 for shares nobody made and — for a count nobody could read", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // `shares: "empty"` - Graph answered and there was nothing to count.
    expect(within(card("Shares 30 ngày")).getByText("0")).toBeInTheDocument();
    // `not_permitted` - Graph refused. Emphatically not zero.
    expect(within(card("Reactions 30 ngày")).getByText("—")).toBeInTheDocument();
    expect(within(card("Comments 30 ngày")).getByText("—")).toBeInTheDocument();
    expect(within(card("Reactions 30 ngày")).queryByText("0")).not.toBeInTheDocument();
    expect(within(card("Comments 30 ngày")).queryByText("0")).not.toBeInTheDocument();
  });

  it("names the reason under the blank rather than leaving it bare", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Reactions 30 ngày")).getByText("Chưa có quyền đọc")).toBeInTheDocument();
    expect(within(card("Comments 30 ngày")).getByText("Chưa có quyền đọc")).toBeInTheDocument();
  });

  it("keeps reactions and comments out of the primary rows while they are refused", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // They exist, in the disclosure. What they must not be is a permanently
    // blank card in a row a manager compares across channels.
    const detail = screen.getByText("Chi tiết dữ liệu").parentElement as HTMLElement;
    expect(within(detail).getByText("Reactions 30 ngày")).toBeInTheDocument();
    expect(within(detail).getByText("Comments 30 ngày")).toBeInTheDocument();
  });
});

describe("6. a comparison nobody could make says so, and never 0%", () => {
  it("prints the real gap when there is a baseline", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // -0,1% over the 29 days that actually separated the two readings, not the
    // 30 the panel asked for. And a fall is reported as a fall, with an arrow
    // and no judgement attached to it.
    expect(within(card("Followers")).getByText(/-0,1%/)).toBeInTheDocument();
    expect(within(card("Followers")).getByText(/so với 29 ngày trước/)).toBeInTheDocument();
    expect(within(card("Tăng Followers 30 ngày")).getByText("-8")).toBeInTheDocument();
  });

  it("says Chưa đủ dữ liệu when no baseline is near enough", async () => {
    stubFetch(routes(metricsBody(analyticsFrom(SNAPSHOT, { capabilities: CAPABILITIES }))));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Tăng Followers 30 ngày")).getByText("—")).toBeInTheDocument();
    expect(within(card("Tăng Followers 30 ngày")).getByText("Chưa đủ dữ liệu")).toBeInTheDocument();
    // A channel connected last week has not been flat for a month. Nobody
    // knows what it did, and 0% there is a measurement nobody took.
    expect(within(cards()).queryByText("0%")).not.toBeInTheDocument();
  });
});

describe("7. reach and impressions are a sentence, not two empty cards", () => {
  it("draws no card and names Meta as the reason", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // No card, and no blank where a card would be. The metric's name appears
    // only inside the explanation below - joined into a sentence with the other
    // metrics that share its reason, which is why an exact-text query for the
    // heading alone finds nothing.
    expect(within(cards()).queryByText("Reach 30 ngày")).not.toBeInTheDocument();
    expect(within(cards()).queryByText("Impressions 30 ngày")).not.toBeInTheDocument();
    const explanation = screen.getByRole("group", {
      name: "Chỉ số chưa hiển thị",
    });
    expect(within(explanation).getByText(/Reach 30 ngày/)).toBeInTheDocument();
    expect(within(explanation).getByText(/Không còn được Meta cung cấp/)).toBeInTheDocument();
    expect(within(explanation).getByText(UNSUPPORTED_NOTE)).toBeInTheDocument();
  });

  it("groups the two reasons so the long sentence is printed once", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    const explanation = screen.getByRole("group", {
      name: "Chỉ số chưa hiển thị",
    });
    // Five metrics, two reasons, two sentences.
    expect(within(explanation).getAllByText(UNSUPPORTED_NOTE)).toHaveLength(1);
    expect(within(explanation).getAllByText(NOT_PERMITTED_NOTE)).toHaveLength(1);
  });

  it("still draws them where a platform does report them", async () => {
    const instagram = {
      ...SNAPSHOT,
      reach_30d: 40000,
      impressions_30d: 90000,
      views_30d: 90000,
    };
    stubFetch(
      routes(
        metricsBody(
          analyticsFrom(instagram, {
            capabilities: {
              ...CAPABILITIES,
              reach_available: true,
              unavailable: [],
            },
          }),
        ),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // The group is conditional on the platform answering, not deleted. An
    // Instagram account reports all three and must keep seeing them.
    expect(within(cards()).getByText("Tiếp cận & hiển thị")).toBeInTheDocument();
    expect(within(card("Reach 30 ngày")).getByText("40.000")).toBeInTheDocument();
    expect(within(card("Impressions 30 ngày")).getByText("90.000")).toBeInTheDocument();
  });
});

// --- 8-10: the top post, freshness, and old data --------------------------

describe("8. the best post is withheld while its ranking cannot be trusted", () => {
  it("draws no performance card when the interaction summaries were refused", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // The server sends no `top_post_30d` at all in this state. The panel must
    // not reconstruct one from anything else on the response: a ranking on
    // shares alone is the most-shared post, not the best one, and a manager
    // quoting it to a client would have been misled by MeoBot.
    expect(screen.queryByText("Bài tốt nhất 30 ngày")).not.toBeInTheDocument();
    // It is accounted for rather than silently dropped.
    const explanation = screen.getByRole("group", {
      name: "Chỉ số chưa hiển thị",
    });
    expect(within(explanation).getByText(/Bài tốt nhất 30 ngày/)).toBeInTheDocument();
  });

  it("draws it again as soon as the ranking is sound", async () => {
    stubFetch(
      routes(
        metricsBody(
          analyticsFrom(
            { ...SNAPSHOT, reactions_30d: 400, comments_30d: 60 },
            {
              capabilities: {
                ...CAPABILITIES,
                reactions_available: true,
                comments_available: true,
                top_post_rankable: true,
                unavailable: [],
              },
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
            },
          ),
        ),
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.getByText("Bài tốt nhất 30 ngày")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Khai trương chi nhánh mới/ })).toBeInTheDocument();
  });
});

describe("9. the panel says what the monthly figures actually cover", () => {
  it("prints the settled window's last day, not the sync time", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    // Meta Insights settles up to about 48 hours behind, so the 30-day window
    // ends two days before the sync that fetched it. Calling both "cập nhật
    // lúc" would make a monthly total look like a snapshot of this morning.
    expect(screen.getByText(/Số liệu 30 ngày tính đến hết ngày 20\/08\/2026/)).toBeInTheDocument();
    expect(screen.getByText(/Ghi nhận:/)).toBeInTheDocument();
  });
});

describe("10. a response from before this milestone still renders", () => {
  it("draws every card it can and explains nothing it cannot justify", async () => {
    // The shape production had on 2026-08-21: followers and engagements, none
    // of the expansion columns, and no capability metadata whatsoever.
    const old = {
      ...SNAPSHOT,
      fans: null,
      posts_count_7d: null,
      posts_count_30d: null,
      video_views_7d: null,
      video_views_30d: null,
      page_views_7d: null,
      page_views_30d: null,
      shares_30d: null,
    };
    const analytics = analyticsFrom(old);
    // Not merely absent-and-empty: absent entirely, the way a cached response
    // or a tab held open across a deploy arrives.
    delete (analytics as Record<string, unknown>).capabilities;
    stubFetch(routes(metricsBody(analytics)));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(within(card("Followers")).getByText("7.880")).toBeInTheDocument();
    expect(within(card("Tương tác 30 ngày")).getByText("553")).toBeInTheDocument();
    for (const blank of [
      "Lượt thích Trang",
      "Bài đăng 30 ngày",
      "Lượt xem video 30 ngày",
      "Lượt xem Trang 30 ngày",
    ]) {
      expect(within(card(blank)).getByText("—")).toBeInTheDocument();
    }
    // No NaN, no undefined, no crash - and no explanation invented for a blank
    // whose cause this reading never recorded.
    expect(screen.queryByText(/NaN|undefined/)).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Chỉ số chưa hiển thị" })).not.toBeInTheDocument();
  });

  it("renders the empty state for a channel nobody has measured", async () => {
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
  });
});

/** Reads a source file, the way `ux.test.tsx` does, for the guard tests above. */
function read(relative: string): string {
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
  const { join } = require("node:path") as typeof import("node:path");
  return readFileSync(join(process.cwd(), "src", relative), "utf-8");
}
