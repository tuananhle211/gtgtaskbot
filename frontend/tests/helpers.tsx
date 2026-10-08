import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  within,
  type RenderResult,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore, type ReactElement } from "react";
import { expect, vi } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * Render a component with a query client that does not retry.
 *
 * Retries would make a test asserting an error state wait for a second attempt
 * that will fail identically, so every failure assertion would be slow and some
 * would be flaky.
 */
export function renderWithQuery(element: ReactElement): RenderResult {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}

/**
 * The address bar, for a `next/navigation` mock that actually navigates.
 *
 * Next's own `useSearchParams` re-renders the page when the router moves; a mock
 * that returns a plain object does not, so a control writing the URL would leave
 * the screen exactly as it was and a test of "the URL is the state" would pass
 * by rendering the same thing twice. That was harmless while the query string
 * only carried filters a test could set up front - and stopped being harmless in
 * Step 1F.2.3c1, when the lifecycle group moved into the URL and clicking a tab
 * became a new request.
 *
 * So the mock subscribes properly. `router.replace` moves `search.value` and
 * every mounted `useSearchParams` follows it, which is also what makes Back,
 * Forward and a reload testable at all: they are the same call.
 *
 * @param search The mutable box the test seeds before rendering. Kept as the
 *   caller's own object so `SEARCH.value = new URLSearchParams(...)` in a
 *   `beforeEach` still means "arrive at this URL".
 */
export function urlStore(search: { value: URLSearchParams }) {
  const listeners = new Set<() => void>();
  return {
    /** `useSearchParams`, subscribed so a navigation re-renders the page. */
    useSearchParams: () =>
      useSyncExternalStore(
        (listener: () => void) => {
          listeners.add(listener);
          return () => {
            listeners.delete(listener);
          };
        },
        () => search.value,
      ),
    /**
     * Arrive at a URL: what `router.replace` does, and what Back does.
     *
     * Call it inside `act` when a test drives it directly - a click already is
     * inside one.
     */
    navigate: (url: string) => {
      search.value = new URLSearchParams(url.split("?")[1] ?? "");
      for (const listener of [...listeners]) listener();
    },
  };
}

/**
 * The `next/navigation` mock `/pr/channels` needs, and a way to reset it.
 *
 * Step 1F.2.9 moved channel selection into `?channel=` so that the OAuth
 * callback - which has always redirected to
 * `/pr/channels?connection=connected&channel=…` - lands back on the channel
 * somebody left from. That made the page a reader of `useSearchParams` and a
 * caller of `router.replace`, which every test rendering it now has to provide.
 *
 * Provided here rather than copied into six files, because the six would drift:
 * one of them would mock `replace` as a spy that does not navigate, and a test
 * of "clicking a card opens that channel" would pass while the screen never
 * changed. `urlStore` makes the mock actually move, which is also what makes
 * arriving from a callback testable at all - it is the same call.
 *
 * @example
 *   const NAV = channelsNavigation();
 *   vi.mock("next/navigation", () => NAV.module);
 *   beforeEach(() => NAV.reset());
 */
export function channelsNavigation(pathname = "/pr/channels") {
  const search = { value: new URLSearchParams() };
  const bar = urlStore(search);
  return {
    /** What `vi.mock("next/navigation", …)` should return. */
    module: {
      usePathname: () => pathname,
      useSearchParams: () => bar.useSearchParams(),
      useRouter: () => ({
        replace: (url: string) => bar.navigate(url),
        push: (url: string) => bar.navigate(url),
        refresh: vi.fn(),
        back: vi.fn(),
      }),
      useParams: () => ({}),
    },
    /** Back to a bare `/pr/channels`. Call in `beforeEach`. */
    reset: () => {
      search.value = new URLSearchParams();
    },
    /** Arrive at a URL - what returning from a provider's consent screen does. */
    arriveAt: (url: string) => bar.navigate(url),
    /** The address bar as it stands, for asserting where a control moved to. */
    current: () => search.value.toString(),
  };
}

/**
 * Wait until every board lane has answered its own request.
 *
 * Step 1F.2.3c2 made the board *n + 1* requests: one for the figures and one
 * per visible lane, all in flight together. Waiting for the board region only
 * waits for the first of those, so an assertion about cards would read the page
 * while three of its four columns were still empty.
 *
 * The condition is the lane's own placeholder, which is user-visible text rather
 * than a test hook: a column that has not answered says "Đang tải…", and a
 * column that has says either its cards or "Chưa có nội dung ở bước này.".
 */
export async function settleLanes(): Promise<void> {
  await waitFor(() =>
    expect(screen.queryAllByText("Đang tải…")).toHaveLength(0),
  );
}

/** A `fetch` stub keyed by URL substring. Anything unmatched is a loud failure. */
export function stubFetch(
  routes: Array<{
    match: string;
    status?: number;
    body?: unknown;
    method?: string;
  }>,
): ReturnType<typeof vi.fn> {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const route = routes.find(
        (candidate) =>
          url.includes(candidate.match) &&
          (candidate.method ?? method) === method,
      );
      if (!route) {
        throw new Error(`No stub for ${method} ${url}`);
      }
      const status = route.status ?? 200;
      // A 204 may not carry a body - the Response constructor throws if it does -
      // and the API uses one for permanent deletion, so the stub has to be able to
      // produce a real one rather than an approximation with an empty string.
      const body =
        status === 204 || route.body === undefined
          ? null
          : JSON.stringify(route.body);
      return new Response(body, {
        status,
        headers: { "Content-Type": "application/json" },
      });
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  // Exposed so a test can assert what was sent, not only what was rendered.
  (fetchMock as unknown as { calls: typeof calls }).calls = calls;
  return fetchMock as unknown as ReturnType<typeof vi.fn>;
}

/**
 * The `analytics` half of a metrics response, derived from one snapshot.
 *
 * Step 1F.2.4d. The server computes this from a channel's stored readings and
 * every test fixture needs one, so it is built here from the same snapshot the
 * fixture already declares - which is what stops a fixture from asserting a
 * follower count on the cards that its own history does not contain.
 *
 * Pass `over` for the derived half: growth, rates, the best post. Those are the
 * parts a test is usually *about*, and they have no default because there is no
 * honest default - a channel measured once has none of them.
 *
 * Step 1F.2.5 added `page_views_*` and `capabilities`. The capability defaults
 * are all-`false` with an empty `unavailable`, which is what a reading whose
 * connector recorded no metadata produces - the pre-milestone snapshot every
 * channel already has a history of. A test about the CH-0004 shape passes its
 * own; see `capabilities()` in `facebook-analytics-panel.test.tsx`.
 */
export function analyticsFrom(
  snapshot: Record<string, unknown>,
  over: Record<string, unknown> = {},
): Record<string, unknown> {
  const value = (name: string) => (snapshot[name] ?? null) as number | null;
  return {
    captured_at: snapshot.captured_at,
    followers: value("followers"),
    fans: value("fans"),
    following: value("following"),
    posts_count: value("posts_count"),
    engagements_7d: value("engagements_7d"),
    engagements_30d: value("engagements_30d"),
    posts_count_7d: value("posts_count_7d"),
    posts_count_30d: value("posts_count_30d"),
    reactions_30d: value("reactions_30d"),
    likes_30d: value("likes_30d"),
    comments_30d: value("comments_30d"),
    shares_30d: value("shares_30d"),
    video_views_7d: value("video_views_7d"),
    video_views_30d: value("video_views_30d"),
    views_7d: value("views_7d"),
    views_30d: value("views_30d"),
    reach_7d: value("reach_7d"),
    reach_30d: value("reach_30d"),
    impressions_7d: value("impressions_7d"),
    impressions_30d: value("impressions_30d"),
    page_views_7d: value("page_views_7d"),
    page_views_30d: value("page_views_30d"),
    follower_growth_7d: null,
    follower_growth_30d: null,
    engagement_change_7d: null,
    engagement_change_30d: null,
    follower_growth_rate_7d: null,
    follower_growth_rate_30d: null,
    engagement_per_follower_30d: null,
    average_engagement_per_post_30d: null,
    top_post_30d: null,
    limitation_note: null,
    capabilities: {
      reach_available: false,
      impressions_available: false,
      reactions_available: false,
      comments_available: false,
      shares_available: false,
      video_views_available: false,
      page_views_available: false,
      top_post_rankable: false,
      post_fields: {},
      insight_metrics_available: [],
      window_30d_end: null,
      unavailable: [],
    },
    ...over,
  };
}

export const SESSION = {
  user_id: "11111111-1111-1111-1111-111111111111",
  full_name: "Le Trưởng Nhóm",
  role: "TEAM_LEAD",
  active: true,
  capabilities: ["PR_TEAM_LEAD_REVIEW"],
};

export const CONTENT = {
  id: "22222222-2222-2222-2222-222222222222",
  code: "CNT-2026-000001",
  title: "Chăm sóc sau nâng mũi",
  workflow_stage: "TEAM_LEAD_REVIEW",
  priority: "NORMAL",
  content_type: "SHORT_VIDEO_SCRIPT",
  brand_id: "33333333-3333-3333-3333-333333333333",
  owner_user_id: SESSION.user_id,
  planned_publish_at: null,
  updated_at: "2026-08-01T03:00:00+00:00",
};

export const VERSION = {
  id: "44444444-4444-4444-4444-444444444444",
  version_no: 3,
  title: CONTENT.title,
  topic: null,
  hook: null,
  brief: null,
  script_text: "Mở đầu bằng một câu hỏi.",
  change_note: null,
  created_by_user_id: SESSION.user_id,
  created_at: "2026-08-01T03:00:00+00:00",
};

/**
 * The open confirmation dialog, scoped for queries. Step 1F.2.8.
 *
 * Scoping matters because the dialog's confirm button deliberately **repeats the
 * trigger's words** - "Nhận sản xuất" opens a dialog whose accept button also
 * says "Nhận sản xuất", so that the last thing under the cursor is the action
 * rather than "Lưu". An unscoped `getByRole("button", …)` would find both.
 */
export function dialog() {
  return within(screen.getByRole("dialog"));
}

/**
 * Press the open dialog's confirm button.
 *
 * Found by `data-confirm-accept` rather than by its words: the words are the
 * thing under test in the copy assertions, and a helper that had to be told them
 * would make every call site repeat the string it is checking.
 */
export async function confirm(): Promise<void> {
  const accept = screen
    .getByRole("dialog")
    .querySelector<HTMLElement>("[data-confirm-accept]");
  if (!accept) throw new Error("the open dialog has no confirm button");
  await userEvent.click(accept);
}

/** Press the open dialog's cancel button. */
export async function cancelDialog(): Promise<void> {
  const cancel = screen
    .getByRole("dialog")
    .querySelector<HTMLElement>("[data-confirm-cancel]");
  if (!cancel) throw new Error("the open dialog has no cancel button");
  await userEvent.click(cancel);
}

/**
 * The Work action contract as a **fake server** would answer it, for detail
 * stubs. Mirrors `resolve_work_actions` on the server - the transition table
 * plus the coarse per-actor flags a stub already carries - so an existing
 * fixture keeps offering the buttons its status implied before the contract
 * existed. The page under test reads only the seven `can_*` flags; this helper
 * is where a test *simulates* the server, never where the page decides.
 * Explicit `can_*` in `flags` win, so a test can contradict the status on
 * purpose (a REJECTED row with `can_cancel: true`, or an ACCEPTED one without
 * `can_start`) and prove the page obeys the server rather than the status.
 */
export function serverWorkActions(
  item: Record<string, unknown>,
  flags: Record<string, unknown>,
): Record<string, boolean> {
  const status = String(item.status ?? "");
  const container = Boolean(item.is_period_container);
  const manual = item.source_type === "MANUAL";
  const manage = Boolean(flags.can_manage);
  const validate = Boolean(flags.can_validate);
  const execute = Boolean(flags.can_execute);
  const cancellable = [
    "PROPOSED",
    "ACCEPTED",
    "IN_PROGRESS",
    "COMPLETED",
  ].includes(status);
  const derived = {
    can_accept: manage && !container && status === "PROPOSED",
    can_reject: manage && !container && status === "PROPOSED",
    can_start: execute && !container && status === "ACCEPTED",
    can_complete:
      execute &&
      !container &&
      (status === "ACCEPTED" || status === "IN_PROGRESS"),
    can_approve: validate && !container && status === "COMPLETED",
    can_reopen: validate && !container && status === "COMPLETED",
    can_cancel: manage && manual && !container && cancellable,
  };
  const explicit = Object.fromEntries(
    Object.entries(flags)
      .filter(([key]) => key in derived)
      .map(([key, value]) => [key, Boolean(value)]),
  );
  return { ...derived, ...explicit };
}

/**
 * The PR content detail page's source files, relative to `src/`.
 *
 * Its tabs (content, review, resources, product, publish, history) were moved
 * verbatim into `components/pr-content-detail/` so the unified task screen
 * (`app/tasks/[ref]/page.tsx`) can reuse them. A test that scans "the detail
 * page" for a string scans all of these, not only the route file.
 */
export const PR_CONTENT_DETAIL_FILES: string[] = [
  "app/pr/content/[id]/page.tsx",
  ...readdirSync(path.join(__dirname, "../src/components/pr-content-detail"))
    .filter((file) => /\.tsx?$/.test(file))
    .sort()
    .map((file) => `components/pr-content-detail/${file}`),
];

/** Every file in {@link PR_CONTENT_DETAIL_FILES}, concatenated. */
export function readPrContentDetailSource(): string {
  return PR_CONTENT_DETAIL_FILES.map((file) =>
    readFileSync(path.join(__dirname, "../src", file), "utf8"),
  ).join("\n");
}
