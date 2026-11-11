/**
 * Quản trị › Hiệu năng (0103, plan docs/ops/SCALING_PLAN.md D).
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PerformancePage from "@/app/admin/performance/page";
import { formatMs } from "@/lib/perf";
import { adminTabsFor } from "@/components/admin-tabs";
import type { PerfReport, UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/admin/performance",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

const LINE = { method: "", count: 0, average_ms: null, p95_ms: null, max_ms: 0, slow: 0, failed: 0, dropped: 0 };

const REPORT: PerfReport = {
  days: 7,
  since: "2026-11-04T05:00:00+00:00",
  api: {
    ...LINE,
    name: "",
    count: 12000,
    average_ms: 64,
    p95_ms: 200,
    max_ms: 4100,
    slow: 7,
    failed: 1,
    peak_p95_ms: 200,
    peak_count: 9000,
    limit_ms: 300,
    verdict: { level: "good", message: "Giờ cao điểm, 95% yêu cầu xong trong 200 ms - dưới ngưỡng 300 ms: chưa cần đợt 2." },
  },
  routes: [
    { ...LINE, method: "GET", name: "/api/hr/timesheet/export.xlsx", count: 9, average_ms: 1300, p95_ms: 2000, max_ms: 4100, slow: 5 },
  ],
  queue: [{ ...LINE, name: "cskh.poll_pancake", count: 30000, average_ms: 300, p95_ms: 1000, max_ms: 95000, slow: 2, dropped: 4 }],
  queue_verdict: { level: "bad", message: "4 lượt tác vụ bị bỏ vì chờ quá lâu - worker đang quá tải." },
};

describe("Quản trị › Hiệu năng", () => {
  it("is a tab for the OWNER and an ADMIN only", () => {
    const me = { can_admin: [], departments: [] } as unknown as UnitsMe;
    expect(adminTabsFor(me, "OWNER").map((tab) => tab.href)).toContain("/admin/performance");
    expect(adminTabsFor(me, "ADMIN").map((tab) => tab.href)).toContain("/admin/performance");
    expect(adminTabsFor(me, "EMPLOYEE").map((tab) => tab.href)).not.toContain("/admin/performance");
  });

  it("formats milliseconds the Vietnamese way", () => {
    expect(formatMs(null)).toBe("—");
    expect(formatMs(120)).toBe("120 ms");
    expect(formatMs(1250)).toBe("1,3 s");
    expect(formatMs(150_000)).toBe("2,5 phút");
  });

  it("shows the verdicts, the figures and the slowest routes, and changes the period", async () => {
    const stub = stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: { units: [], can_admin: [], departments: [] } },
      { match: "/api/cron/performance", body: REPORT },
    ]);
    renderWithQuery(<PerformancePage />);
    expect(await screen.findByText(/dưới ngưỡng 300 ms/)).toBeInTheDocument();
    expect(screen.getByText(/worker đang quá tải/).closest("[data-level]")).toHaveAttribute("data-level", "bad");
    const tiles = screen.getByRole("list", { name: "Chỉ số tổng" });
    expect(within(tiles).getByText("12.000")).toBeInTheDocument();
    expect(within(tiles).getByText("200 ms")).toHaveClass("text-[var(--good)]");
    expect(screen.getByText("/api/hr/timesheet/export.xlsx")).toBeInTheDocument();
    expect(screen.getByText("cskh.poll_pancake")).toBeInTheDocument();
    expect(screen.getByText("1,6 phút")).toBeInTheDocument(); // the longest wait

    await userEvent.setup().click(screen.getByRole("button", { name: "30 ngày" }));
    await waitFor(() =>
      expect(
        (stub as unknown as { calls: { url: string }[] }).calls.some((call) => call.url.includes("days=30")),
      ).toBe(true),
    );
  });

  it("does not colour the peak figure before there is enough to judge", async () => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: { units: [], can_admin: [], departments: [] } },
      {
        match: "/api/cron/performance",
        body: {
          ...REPORT,
          api: {
            ...REPORT.api,
            peak_p95_ms: 500,
            verdict: { level: "none", message: "Chưa đủ số liệu giờ cao điểm (cần vài ngày làm việc)." },
          },
        },
      },
    ]);
    renderWithQuery(<PerformancePage />);
    const tiles = await screen.findByRole("list", { name: "Chỉ số tổng" });
    const peak = within(tiles).getByText("500 ms");
    expect(peak).not.toHaveClass("text-[var(--bad)]");
    expect(peak).not.toHaveClass("text-[var(--good)]");
  });
});
