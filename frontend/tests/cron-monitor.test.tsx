/**
 * "Quản trị › Tác vụ định kỳ" (0077): every scheduled job, how its last run
 * went, its history, "Chạy lại"; the OWNER customises a job (policy, failure
 * alerts on / off, who besides the OWNER gets them - 0080).
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CronPage from "@/app/ads/jobs/page";
import { adminTabsFor } from "@/components/admin-tabs";
import { navFor } from "@/components/shell";
import { notificationHref } from "@/components/notifications";
import type { AppNotification, UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/ads/jobs",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;
const sent = (stub: unknown, method: string, fragment: string) =>
  calls(stub).filter(
    (call) => call.method === method && call.url.includes(fragment),
  );

const job = (over: Record<string, unknown> = {}) => ({
  key: "notifications.drain_outbox",
  label: "Gửi tin trong hàng đợi",
  group: "Thông báo",
  description: "Gửi các tin Telegram đang chờ (outbox).",
  schedule_label: "mỗi 20 giây",
  state: "OK",
  state_label: "Ổn",
  enabled: true,
  retries: 0,
  retry_delay_minutes: 0,
  alert_after: 5,
  keep_successes: false,
  notify: false,
  recipients: [],
  last_started_at: "2026-10-27T01:00:00Z",
  last_finished_at: "2026-10-27T01:00:01Z",
  last_status: "SUCCESS",
  last_status_label: "Thành công",
  last_message: null,
  last_duration_ms: 400,
  last_success_at: "2026-10-27T01:00:01Z",
  consecutive_failures: 0,
  running_since: null,
  open_incident: null,
  ...over,
});

const FAILING = job({
  key: "ads.sync_spend",
  label: "Đồng bộ chi phí Meta",
  group: "Ads",
  description: "",
  schedule_label: "hằng ngày 08:00 · 23:00",
  state: "FAILING",
  state_label: "Đang lỗi",
  retries: 2,
  retry_delay_minutes: 30,
  alert_after: 1,
  keep_successes: true,
  notify: true,
  recipients: [{ user_id: "u-head", name: "Nguyễn Xuân Dũng" }],
  last_status: "FAILED",
  last_status_label: "Lỗi",
  last_message: "Token hết hạn",
  consecutive_failures: 3,
  open_incident: {
    id: "i-1",
    kind: "FAILED",
    opened_at: "2026-10-27T01:00:00Z",
    closed_at: null,
    failures: 3,
    last_error: "Token hết hạn",
    alerted_at: "2026-10-27T02:00:00Z",
    recovery_alerted_at: null,
    rerun_requested_at: null,
  },
});

const overview = (over: Record<string, unknown> = {}) => ({
  now: "2026-10-27T03:00:00Z",
  summary: { total: 2, ok: 1, failing: 1, late: 0, running: 0, off: 0 },
  jobs: [job(), FAILING],
  can_manage: true,
  people: [
    { user_id: "u-head", name: "Nguyễn Xuân Dũng", member_code: "M001DUNG" },
    { user_id: "u-huy", name: "Ngô Trọng Huy", member_code: "M011HUY" },
  ],
  ...over,
});

function open(
  data = overview(),
  role = "OWNER",
  extra: Array<{
    match: string;
    method?: string;
    status?: number;
    body?: unknown;
  }> = [],
) {
  const stub = stubFetch([
    ...extra,
    { match: "/api/auth/session", body: { ...SESSION, role } },
    {
      match: "/api/units/me",
      body: {
        units: [],
        can_admin: [],
        can_view_all: true,
        default_unit: null,
      },
    },
    { match: "/api/cron", method: "GET", body: data },
  ]);
  renderWithQuery(<CronPage />);
  return stub;
}

describe("the Tác vụ định kỳ tab", () => {
  it("sits in the Phòng Ads menu for the OWNER and an ADMIN, not in Quản trị", () => {
    const me = {
      units: [],
      can_admin: [],
      can_view_all: true,
      ads: {
        view: true,
        view_all: true,
        manage_accounts: true,
        manage_connections: false,
      },
    } as unknown as UnitsMe;
    const hrefs = (role: string) =>
      navFor(me, role).flatMap((entry) =>
        "children" in entry
          ? entry.children.map((child) => child.href)
          : [entry.href],
      );
    expect(hrefs("OWNER")).toContain("/ads/jobs");
    expect(hrefs("ADMIN")).toContain("/ads/jobs");
    expect(hrefs("EMPLOYEE")).not.toContain("/ads/jobs");
    expect(adminTabsFor(me, "OWNER").map((tab) => tab.href)).not.toContain(
      "/admin/cron",
    );
  });

  it("a job alert in the bell opens the screen", () => {
    const alert = {
      target_kind: "cron_job",
      target_id: "i-1",
    } as unknown as AppNotification;
    expect(notificationHref(alert)).toBe("/ads/jobs");
  });
});

describe("the jobs", () => {
  it("shows each job's state, schedule, last run and last error, grouped", async () => {
    open();
    const ok = await screen.findByTestId("cron-job-notifications.drain_outbox");
    expect(within(ok).getByText("Ổn")).toBeInTheDocument();
    expect(within(ok).getByText("mỗi 20 giây")).toBeInTheDocument();
    expect(within(ok).getByText("0,4 giây")).toBeInTheDocument();
    const failing = screen.getByTestId("cron-job-ads.sync_spend");
    expect(within(failing).getByText("Đang lỗi")).toBeInTheDocument();
    expect(within(failing).getByText("3 lần liên tiếp")).toBeInTheDocument();
    expect(within(failing).getByText("Token hết hạn")).toBeInTheDocument();
    expect(within(failing).getByText(/Đã báo lúc/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Ads" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("1 tác vụ cần xem");
  });

  it("filters down to the jobs in trouble", async () => {
    open();
    await screen.findByTestId("cron-job-ads.sync_spend");
    await userEvent.click(screen.getByRole("radio", { name: /Đang lỗi/ }));
    expect(
      screen.queryByTestId("cron-job-notifications.drain_outbox"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("cron-job-ads.sync_spend")).toBeInTheDocument();
  });

  it("Chạy lại asks first, then queues the job", async () => {
    const stub = open(overview(), "OWNER", [
      {
        match: "/api/cron/jobs/ads.sync_spend/run",
        method: "POST",
        status: 202,
        body: { task_id: "t-1", job: FAILING },
      },
    ]);
    const row = await screen.findByTestId("cron-job-ads.sync_spend");
    await userEvent.click(
      within(row).getByRole("button", {
        name: "Chạy lại Đồng bộ chi phí Meta",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Chạy lại" }),
    );
    await waitFor(() =>
      expect(
        sent(stub, "POST", "/api/cron/jobs/ads.sync_spend/run"),
      ).toHaveLength(1),
    );
  });

  it("an ADMIN re-runs but does not see the Tuỳ chỉnh button", async () => {
    open(overview({ can_manage: false }), "ADMIN");
    const row = await screen.findByTestId("cron-job-ads.sync_spend");
    expect(
      within(row).getByRole("button", {
        name: "Chạy lại Đồng bộ chi phí Meta",
      }),
    ).toBeInTheDocument();
    expect(
      within(row).queryByRole("button", { name: /Tuỳ chỉnh/ }),
    ).not.toBeInTheDocument();
  });

  it("each row says who is told of a failure", async () => {
    open(overview(), "OWNER");
    expect(
      await screen.findByTestId("cron-notify-ads.sync_spend"),
    ).toHaveTextContent("Báo lỗi: Chủ sở hữu + Nguyễn Xuân Dũng");
    expect(
      screen.getByTestId("cron-notify-notifications.drain_outbox"),
    ).toHaveTextContent("Không gửi thông báo");
  });

  it("the OWNER switches alerts and picks who gets them", async () => {
    const stub = open(overview(), "OWNER", [
      {
        match: "/api/cron/jobs/notifications.drain_outbox",
        method: "PATCH",
        body: job(),
      },
    ]);
    const row = await screen.findByTestId(
      "cron-job-notifications.drain_outbox",
    );
    await userEvent.click(
      within(row).getByRole("button", {
        name: "Tuỳ chỉnh Gửi tin trong hàng đợi",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).queryByLabelText("Thêm người nhận"),
    ).not.toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByLabelText(/Gửi thông báo khi tác vụ lỗi/),
    );
    expect(dialog).toHaveTextContent("Chủ sở hữu · luôn nhận");
    expect(dialog).toHaveTextContent("Không thêm ai thì chỉ chủ sở hữu nhận.");
    await userEvent.selectOptions(
      within(dialog).getByLabelText("Thêm người nhận"),
      "u-huy",
    );
    await userEvent.selectOptions(
      within(dialog).getByLabelText("Thêm người nhận"),
      "u-head",
    );
    // Picked people leave the list; × takes one off again.
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Bỏ Nguyễn Xuân Dũng" }),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lưu tuỳ chỉnh" }),
    );
    await waitFor(() =>
      expect(
        sent(stub, "PATCH", "/api/cron/jobs/notifications.drain_outbox"),
      ).toHaveLength(1),
    );
    expect(
      sent(stub, "PATCH", "/api/cron/jobs/notifications.drain_outbox")[0].body,
    ).toMatchObject({ notify: true, notify_user_ids: ["u-huy"] });
  });

  it("the OWNER edits a policy", async () => {
    const stub = open(overview(), "OWNER", [
      {
        match: "/api/cron/jobs/ads.sync_spend",
        method: "PATCH",
        body: FAILING,
      },
    ]);
    const row = await screen.findByTestId("cron-job-ads.sync_spend");
    await userEvent.click(
      within(row).getByRole("button", {
        name: "Tuỳ chỉnh Đồng bộ chi phí Meta",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    const retries = within(dialog).getByLabelText("Tự thử lại (lần)");
    await userEvent.clear(retries);
    await userEvent.type(retries, "1");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lưu tuỳ chỉnh" }),
    );
    await waitFor(() =>
      expect(sent(stub, "PATCH", "/api/cron/jobs/ads.sync_spend")).toHaveLength(
        1,
      ),
    );
    expect(
      sent(stub, "PATCH", "/api/cron/jobs/ads.sync_spend")[0].body,
    ).toEqual({
      enabled: true,
      retries: 1,
      retry_delay_minutes: 30,
      alert_after: 1,
      keep_successes: true,
      notify: true,
      notify_user_ids: ["u-head"],
    });
  });

  it("the history shows each run's error in full", async () => {
    open(overview(), "OWNER", [
      {
        match: "/api/cron/jobs/ads.sync_spend",
        method: "GET",
        body: {
          job: FAILING,
          runs: [
            {
              id: "r-1",
              trigger: "RETRY",
              trigger_label: "Tự thử lại",
              attempt: 3,
              started_at: "2026-10-27T02:00:00Z",
              finished_at: "2026-10-27T02:00:03Z",
              duration_ms: 3000,
              status: "FAILED",
              status_label: "Lỗi",
              message: "Token hết hạn",
              error: "IntegrationAuthError: Token hết hạn\n  at sync()",
            },
          ],
          incidents: [FAILING.open_incident],
        },
      },
    ]);
    const row = await screen.findByTestId("cron-job-ads.sync_spend");
    await userEvent.click(within(row).getByRole("button", { name: "Lịch sử" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(/Tự thử lại \(lần 3\)/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/IntegrationAuthError: Token hết hạn/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/chưa khắc phục/)).toBeInTheDocument();
  });
});
