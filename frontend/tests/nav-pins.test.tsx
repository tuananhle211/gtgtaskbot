/**
 * "Phòng Media" + "Ghim menu" (2026-11-12): Bảng order (+ Effort), Phòng Ads and
 * Phòng PR sit under one heading; a pinned branch goes first, out of it, open.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Shell, arrangeNav, isFolder, navFor, MEDIA_KEY, type DisplayEntry } from "@/components/shell";
import type { UnitsMe } from "@/lib/api";
import { renderWithQuery, SESSION, stubFetch } from "./helpers";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

const ME = {
  units: [{ code: "ADS" }, { code: "PR" }],
  default_unit: "ADS",
  can_view_all: true,
  can_admin: [],
  departments: [],
  ads: { view: true, edit_revenue: false, manage_connections: false },
  nav_pins: [],
} as unknown as UnitsMe;

const labels = (entries: DisplayEntry[]) => entries.map((entry) => entry.label);

describe("Phòng Media", () => {
  it("gathers the order boards, Phòng Ads and Phòng PR under one heading, in place", () => {
    const nav = navFor(ME, "OWNER");
    const { pinned, rest } = arrangeNav(nav);
    expect(pinned).toEqual([]);
    const media = rest.find((entry) => entry.label === "Phòng Media");
    expect(media && isFolder(media)).toBe(true);
    if (!media || !isFolder(media)) return;
    // Effort (the ORD token grid) belongs with the order boards.
    expect(media.entries.map((entry) => entry.label)).toEqual([
      "Bảng order",
      "Effort",
      "Phòng Ads",
      "Phòng PR",
    ]);
    // It sits where "Bảng order" was, and nothing else moved.
    expect(labels(rest)).not.toContain("Phòng PR");
    expect(labels(rest).indexOf("Phòng Media")).toBe(
      nav.findIndex((entry) => entry.label === "Bảng order"),
    );
  });

  it("leaves a single media branch on its own", () => {
    const ordersOnly = { ...ME, units: [{ code: "ADS" }], can_view_all: false, ads: null } as unknown as UnitsMe;
    const { rest } = arrangeNav(navFor(ordersOnly, "EMPLOYEE"));
    expect(labels(rest)).toContain("Bảng order");
    expect(labels(rest)).not.toContain("Phòng Media");
  });

  it("puts pinned branches first in pin order, out of Phòng Media, and ignores unknown pins", () => {
    const nav = navFor(ME, "OWNER");
    const { pinned, rest } = arrangeNav(nav, ["pr", "khong-co", "hr"]);
    expect(labels(pinned)).toEqual(["Phòng PR", "Nhân sự"]);
    expect(labels(rest)).not.toContain("Nhân sự");
    const media = rest.find((entry) => entry.label === "Phòng Media");
    expect(media && isFolder(media) && media.entries.map((entry) => entry.label)).toEqual([
      "Bảng order",
      "Effort",
      "Phòng Ads",
    ]);
    const whole = arrangeNav(nav, [MEDIA_KEY]);
    expect(labels(whole.pinned)).toEqual(["Phòng Media"]);
    expect(labels(whole.rest)).not.toContain("Phòng Media");
  });
});

describe("Ghim menu in the sidebar", () => {
  it("draws a pinned branch first and open, and saves a new pin", async () => {
    const stub = stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "OWNER" } },
      { match: "/api/units/me", body: { ...ME, nav_pins: ["pr"] } },
      { match: "/api/notifications", body: { items: [], unread_count: 0 } },
      { match: "/api/account/nav-pins", method: "PUT", body: { pins: ["pr", "ads"] } },
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
    const pinnedBlock = await screen.findByRole("group", { name: "Đã ghim" });
    const pr = within(pinnedBlock).getByRole("group", { name: "Phòng PR" });
    // Open, with no fold button: its screens are right there.
    expect(within(pr).queryByRole("button", { name: "Phòng PR" })).not.toBeInTheDocument();
    expect(within(pr).getByRole("link", { name: "Quản lý task" })).toBeInTheDocument();
    expect(within(pr).getByRole("button", { name: "Bỏ ghim Phòng PR" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // The rest: Phòng Media holds Bảng order and Phòng Ads, folded.
    const primary = screen.getByRole("navigation", { name: "Điều hướng chính" });
    const media = within(primary).getByRole("group", { name: "Phòng Media" });
    expect(within(media).getByRole("button", { name: "Phòng Media" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(within(media).queryByRole("group", { name: "Phòng PR" })).not.toBeInTheDocument();

    await userEvent.setup().click(within(media).getByRole("button", { name: "Ghim Phòng Ads" }));
    await waitFor(() => {
      const puts = (stub as unknown as { calls: { url: string; method: string; body: unknown }[] }).calls.filter(
        (call) => call.method === "PUT" && call.url.includes("/api/account/nav-pins"),
      );
      expect(puts.map((call) => call.body)).toEqual([{ pins: ["pr", "ads"] }]);
    });
    await waitFor(() =>
      expect(
        within(screen.getByRole("group", { name: "Đã ghim" })).getByRole("group", { name: "Phòng Ads" }),
      ).toBeInTheDocument(),
    );
  });
});
