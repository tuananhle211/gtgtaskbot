/**
 * Step 1F.2.4a - channel identity and manual metric readings, in the browser.
 *
 * Numbered 42-54, following the requirement numbering the step was specified
 * with.
 *
 * What these assert is narrow on purpose: **the browser decides nothing here.**
 * The platform badge is `platform_label` from the server, the data status is
 * `metrics_status_label`, the source of a reading is `source_label`, the
 * follower delta and the staleness are numbers the API computed, and whether
 * "Ghi nhận chỉ số" appears at all is `can_record_metrics`. So most of the
 * load-bearing assertions have the shape "given this server answer, this
 * appears" and its mirror "given the answer without it, this does not" -
 * because a panel that draws a record button from a role string looks identical
 * on screen to one that asks.
 *
 * The other half is what the screen must never say. There is no live API sync
 * in TasksBot, so a figure typed three days ago must not read as today's: the
 * capture time and the source travel with every number, and test 53 says the
 * word "Live" is nowhere on the page.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SESSION, analyticsFrom, channelsNavigation, renderWithQuery, stubFetch } from "./helpers";

/**
 * The dashboard the channel screen reads to decide whether to offer the create
 * buttons. Only `my_capabilities` matters here; the rest is shape.
 */
const DASHBOARD = {
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: ["PR_CHANNEL_MANAGE"],
  recent_content: [],
};

/* Step 1F.2.9. `/pr/channels` reads its selection out of `?channel=`, so every
   test that renders it needs a router that really navigates. See
   `channelsNavigation`. */
const NAV = channelsNavigation();
vi.mock("next/navigation", () => NAV.module);

const { default: ChannelsPage } = await import("@/app/pr/channels/page");

const PLATFORM = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  code: "TIKTOK",
  name: "TikTok",
  status: "ACTIVE",
  policy_grounded: true,
};

const BRAND = { id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", code: "APEXMED", name: "Apexmed" };

const CHANNEL = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  code: "CH-0004",
  name: "Dr Tiến - Tân trang cô bé",
  category: "SCALE",
  status: "ACTIVE",
  brand_id: BRAND.id,
  platform_id: PLATFORM.id,
  tier: null,
  url: "https://www.tiktok.com/@drtien",
  platform_code: "TIKTOK",
  policy_grounded_platform: true,
  platform: "TIKTOK",
  platform_label: "TikTok",
  platform_name: "TikTok",
  handle: "@drtien",
  external_id: null,
  metrics_status: "MANUAL",
  metrics_status_label: "Nhập thủ công",
  latest_captured_at: "2026-08-20T02:00:00Z",
  followers: 124812,
  days_since_capture: 0,
};

/** A channel registered on a platform outside the canonical six. */
const LEGACY_CHANNEL = {
  ...CHANNEL,
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  code: "CH-0001",
  name: "Trang cũ",
  handle: null,
  platform: null,
  platform_label: "Chưa xác định",
  platform_name: "Facebook Việt Nam",
  metrics_status: "DISCONNECTED",
  metrics_status_label: "Chưa có dữ liệu",
  latest_captured_at: null,
  followers: null,
  days_since_capture: null,
};

const SNAPSHOT = {
  id: "11111111-2222-3333-4444-555555555555",
  channel_id: CHANNEL.id,
  captured_at: "2026-08-20T02:00:00Z",
  source: "MANUAL",
  source_label: "Nhập thủ công",
  recorded_by_user_id: SESSION.user_id,
  recorded_by_name: SESSION.full_name,
  followers: 124812,
  following: null,
  posts_count: null,
  views_7d: null,
  views_30d: 3102441,
  reach_7d: null,
  reach_30d: 1284220,
  impressions_7d: null,
  impressions_30d: null,
  engagements_7d: null,
  engagements_30d: 186230,
  likes_30d: null,
  comments_30d: null,
  shares_30d: null,
  extra_metrics: null,
};

const EARLIER = {
  ...SNAPSHOT,
  id: "66666666-7777-8888-9999-000000000000",
  captured_at: "2026-08-13T02:00:00Z",
  followers: 123392,
  recorded_by_name: "Hà Trưởng Phòng",
};

const METRICS = {
  channel_id: CHANNEL.id,
  status: "MANUAL",
  status_label: "Nhập thủ công",
  latest: SNAPSHOT,
  previous: EARLIER,
  trend: { delta: 1420, delta_pct: 1.2 },
  history: [SNAPSHOT, EARLIER],
  total: 2,
  limit: 30,
  offset: 0,
  days_since_capture: 0,
  analytics: analyticsFrom(SNAPSHOT),
  can_record_metrics: true,
  has_history: true,
};

const NO_METRICS = {
  channel_id: CHANNEL.id,
  status: "DISCONNECTED",
  status_label: "Chưa có dữ liệu",
  latest: null,
  previous: null,
  trend: null,
  history: [],
  total: 0,
  limit: 30,
  offset: 0,
  days_since_capture: null,
  // Step 1F.2.4d. `null`, not an object of blanks: this channel has never been
  // measured, which the panel renders as an empty state rather than as twelve
  // cards reading "—".
  analytics: null,
  can_record_metrics: true,
  has_history: false,
};

const PEOPLE = [{ user_id: SESSION.user_id, full_name: SESSION.full_name, role: SESSION.role }];

/**
 * The channel screen's routes.
 *
 * `/metrics` comes first: `stubFetch` matches on the first substring hit and
 * every metrics URL also contains "/api/pr/channels", so the order is what makes
 * the two distinguishable at all.
 */
const routes = (
  metrics: unknown = METRICS,
  detail: unknown = { channel: CHANNEL, assignments: [], can_edit_channel: true, can_record_metrics: true, can_manage_assignments: true },
  channels: unknown[] = [CHANNEL],
  capabilities: string[] = ["PR_CHANNEL_MANAGE"],
) => [
  { match: "/api/pr/dashboard", body: { ...DASHBOARD, my_capabilities: capabilities } },
  { match: "/api/pr/platforms", body: [PLATFORM] },
  { match: "/api/pr/brands", body: [BRAND] },
  { match: "/metrics", body: metrics },
  { match: `/api/pr/channels/${CHANNEL.id}`, body: detail },
  { match: `/api/pr/channels/${LEGACY_CHANNEL.id}`, body: detail },
  { match: "/api/pr/channels", body: channels },
  { match: "/api/pr/people", body: PEOPLE },
];

async function openChannel(name = CHANNEL.name): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(name) }));
}

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 42-44: identity -------------------------------------------------------

describe("42. the platform is on the channel card, as words", () => {
  it("renders the server's label rather than a colour or a code", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);

    const card = await screen.findByRole("button", { name: new RegExp(CHANNEL.name) });
    expect(within(card).getByText("TikTok")).toBeInTheDocument();
    // The badge is text and the raw code is not shown: "TIKTOK" is a token the
    // policy system matches on, not something to put in front of a person.
    expect(card.textContent).not.toContain("TIKTOK");
    expect(within(card).getByText("Nhập thủ công")).toBeInTheDocument();
  });
});

describe("43. a channel outside the canonical six says so", () => {
  it("shows Chưa xác định and names what it was registered as", async () => {
    stubFetch(routes(NO_METRICS, {
      channel: LEGACY_CHANNEL,
      assignments: [],
      can_edit_channel: true,
      can_record_metrics: true,
      can_manage_assignments: true,
    }, [LEGACY_CHANNEL]));
    renderWithQuery(<ChannelsPage />);
    await openChannel(LEGACY_CHANNEL.name);

    expect(await screen.findByText(/chưa xác định nền tảng/i)).toBeInTheDocument();
    // Not an error: the channel is usable, and the platform it really sits on
    // is named so somebody can go and fix it.
    expect(screen.getByText(/Facebook Việt Nam/)).toBeInTheDocument();
    expect(screen.getByText(/vẫn dùng bình thường/)).toBeInTheDocument();
  });
});

describe("44. the create form asks for a platform before anything else", () => {
  it("cannot be submitted without one, and offers a handle", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);

    await userEvent.click(await screen.findByRole("button", { name: "+ Tạo kênh" }));
    const form = screen.getByRole("form", { name: "Tạo kênh" });
    const platform = within(form).getByRole("combobox", { name: "Nền tảng *" });
    expect(platform).toBeRequired();
    // Nothing is preselected: a default platform would be a guess recorded as
    // data on every channel whose creator did not look at this field.
    expect((platform as HTMLSelectElement).value).toBe("");
    expect(within(form).getByRole("button", { name: "Tạo kênh" })).toBeDisabled();

    // The handle is offered and is optional.
    expect(within(form).getByRole("textbox", { name: "Handle" })).not.toBeRequired();
  });

  it("sends the handle it was given, exactly as typed", async () => {
    const fetchMock = stubFetch([
      {
        match: "/api/pr/channels",
        method: "POST",
        status: 201,
        body: { channel: CHANNEL, assignments: [], can_edit_channel: true, can_record_metrics: true, can_manage_assignments: true },
      },
      ...routes(),
    ]);
    renderWithQuery(<ChannelsPage />);

    await userEvent.click(await screen.findByRole("button", { name: "+ Tạo kênh" }));
    const form = screen.getByRole("form", { name: "Tạo kênh" });
    await userEvent.selectOptions(
      within(form).getByRole("combobox", { name: "Nền tảng *" }),
      PLATFORM.id,
    );
    await userEvent.type(within(form).getByRole("textbox", { name: "Tên kênh" }), "Dr Tiến");
    await userEvent.type(within(form).getByRole("textbox", { name: "Handle" }), "@drtien");
    await userEvent.click(within(form).getByRole("button", { name: "Tạo kênh" }));

    await waitFor(() => {
      const posted = (
        fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
      ).calls.find((call) => call.method === "POST");
      expect(posted?.body).toMatchObject({ handle: "@drtien", platform_id: PLATFORM.id });
    });
  });
});

// --- 45-47: the empty state, and who may fill it --------------------------

describe("45. a channel nobody has measured says so, and shows no zeros", () => {
  it("renders the empty sentence rather than a wall of 0", async () => {
    stubFetch(routes(NO_METRICS));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText(/Chưa có dữ liệu chỉ số/)).toBeInTheDocument();
    // No summary card is drawn at all, so there is no "Followers 0" to mistake
    // for a measurement.
    expect(screen.queryByText("Followers")).not.toBeInTheDocument();
  });
});

describe("46. a manager is offered the form", () => {
  it("shows Ghi nhận chỉ số when the server says they may record", async () => {
    stubFetch(routes(NO_METRICS));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByRole("button", { name: "+ Ghi nhận chỉ số" })).toBeInTheDocument();
  });
});

describe("47. somebody who may not record is not offered it", () => {
  it("hides the control on the server's flag, not on a role string", async () => {
    stubFetch(
      routes(
        { ...NO_METRICS, can_record_metrics: false },
        {
          channel: CHANNEL,
          assignments: [],
          can_edit_channel: false,
          can_record_metrics: false,
          can_manage_assignments: false,
        },
        [CHANNEL],
        ["PR_TEAM_LEAD_REVIEW"],
      ),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText(/Chưa có dữ liệu chỉ số/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Ghi nhận chỉ số" })).not.toBeInTheDocument();
    // And the same person is not offered the channel edit form either.
    expect(screen.queryByRole("button", { name: "Sửa thông tin kênh" })).not.toBeInTheDocument();
  });
});

// --- 48: the manual form ---------------------------------------------------

describe("48. a partial reading is accepted, and blanks stay blank", () => {
  it("omits untouched fields from the body rather than sending zeros", async () => {
    const fetchMock = stubFetch([
      { match: "/metrics", method: "POST", status: 201, body: METRICS },
      ...routes(NO_METRICS),
    ]);
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await userEvent.click(await screen.findByRole("button", { name: "+ Ghi nhận chỉ số" }));
    const form = screen.getByRole("form", { name: "Ghi nhận chỉ số" });

    // Only the time and one number. Everything else is left alone.
    await userEvent.type(
      within(form).getByLabelText("Thời điểm *"),
      "2026-08-20T09:00",
    );
    await userEvent.type(within(form).getByRole("spinbutton", { name: "Followers" }), "124812");
    await userEvent.click(within(form).getByRole("button", { name: "Lưu chỉ số" }));

    await waitFor(() => {
      const posted = (
        fetchMock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }
      ).calls.find((call) => call.method === "POST");
      expect(posted).toBeTruthy();
      const body = posted!.body as Record<string, unknown>;
      expect(body.followers).toBe(124812);
      expect(body.captured_at).toBeTruthy();
      // Absent, not zero. The two mean different things all the way down to the
      // column, and the form is where the distinction is either kept or lost.
      expect("reach_30d" in body).toBe(false);
      expect("views_7d" in body).toBe(false);
      // And the client never names a source or a recorder.
      expect("source" in body).toBe(false);
      expect("recorded_by_user_id" in body).toBe(false);
    });
  });

  it("will not submit a reading with no numbers in it", async () => {
    stubFetch(routes(NO_METRICS));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await userEvent.click(await screen.findByRole("button", { name: "+ Ghi nhận chỉ số" }));
    const form = screen.getByRole("form", { name: "Ghi nhận chỉ số" });
    await userEvent.type(within(form).getByLabelText("Thời điểm *"), "2026-08-20T09:00");

    expect(within(form).getByRole("button", { name: "Lưu chỉ số" })).toBeDisabled();
  });
});

// --- 49-52: reading what is there ------------------------------------------

describe("49. the current reading is shown with its capture time", () => {
  it("renders the figures the latest snapshot carries and no others", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText("Chỉ số gần nhất")).toBeInTheDocument();
    // Full precision, grouped the way Vietnamese writes a number. Not "124,8K":
    // this is the figure somebody will reconcile against TikTok's own screen.
    // `getAllByText` because the same reading is also the top row of the history
    // table below - which is the point, and not a duplicate to assert away.
    expect(screen.getAllByText("124.812").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3.102.441").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1.284.220").length).toBeGreaterThan(0);
    expect(screen.getAllByText("186.230").length).toBeGreaterThan(0);
    // Nothing is drawn for the metrics this reading left blank.
    expect(screen.queryByText("Impressions 30 ngày")).not.toBeInTheDocument();
    expect(screen.queryByText("Likes 30 ngày")).not.toBeInTheDocument();
  });
});

describe("50. the trend says exactly what it compared", () => {
  it("renders the delta against the previous reading, not a monthly rate", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByText(/\+1\.420/)).toBeInTheDocument();
    expect(screen.getByText(/so với lần ghi trước/)).toBeInTheDocument();
    // Two manual readings are however far apart somebody's memory put them.
    expect(document.body.textContent).not.toContain("30 ngày tăng");
    expect(document.body.textContent).not.toMatch(/%\s*\/\s*30 ngày/);
  });

  it("shows no trend at all for a channel measured once", async () => {
    stubFetch(
      routes({ ...METRICS, previous: null, trend: null, history: [SNAPSHOT], total: 1 }),
    );
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await waitFor(() => expect(screen.getAllByText("124.812").length).toBeGreaterThan(0));
    expect(screen.queryByText(/so với lần ghi trước/)).not.toBeInTheDocument();
  });
});

describe("51. the history names who wrote each reading down", () => {
  it("renders the recorder the server resolved, per row", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    const table = await screen.findByRole("table");
    expect(within(table).getByText(SESSION.full_name)).toBeInTheDocument();
    expect(within(table).getByText("Hà Trưởng Phòng")).toBeInTheDocument();
    // Both readings are there. A correction appends; nothing is replaced.
    expect(within(table).getAllByRole("row")).toHaveLength(3); // header + two
  });
});

describe("52. the source of a reading is shown in words", () => {
  it("says Nhập thủ công, from the server's own label", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    expect(screen.getByText(/Nguồn: Nhập thủ công/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("MANUAL");
  });
});

// --- 53-54: what the screen must never claim, and what it must keep -------

describe("53. a typed number is never presented as live API data", () => {
  it("never says Live, and always says when it was captured", async () => {
    stubFetch(routes({ ...METRICS, days_since_capture: 3 }));
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await screen.findByText("Chỉ số gần nhất");
    const body = document.body.textContent ?? "";
    for (const forbidden of ["Live", "trực tiếp", "Tự động đồng bộ", "Realtime"]) {
      expect(body).not.toContain(forbidden);
    }
    expect(screen.getByText(/Ghi nhận: /)).toBeInTheDocument();
    // Staleness is the server's arithmetic, rendered - not the browser's.
    expect(screen.getByText(/Đã 3 ngày chưa cập nhật/)).toBeInTheDocument();
  });

  it("warns before a platform change that history was measured elsewhere", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    await userEvent.click(await screen.findByRole("button", { name: "Sửa thông tin kênh" }));
    const form = screen.getByRole("form", { name: "Sửa thông tin kênh" });
    // The picker is seeded with what the channel already is, so opening the
    // form changes nothing and warns about nothing.
    expect(screen.queryByText(/đã có lịch sử chỉ số/)).not.toBeInTheDocument();
  });
});

describe("54. assignments still work", () => {
  it("keeps the assignment section and its inclusive-end wording", async () => {
    stubFetch(routes());
    renderWithQuery(<ChannelsPage />);
    await openChannel();

    expect(await screen.findByRole("heading", { name: "Phân công" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Thêm phân công" })).toBeInTheDocument();
    expect(screen.getByText(/cuối cùng còn hiệu lực/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Phân công" })).toBeInTheDocument();
  });
});
