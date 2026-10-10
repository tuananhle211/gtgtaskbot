/**
 * The dashboard's "Theo ban": a ban's member opens on their own ban, the
 * switch goes to "Tất cả"; tokens left go red once over.
 */

import { useState } from "react";
import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BanStatsPanel } from "@/components/ban-stats";
import { renderWithQuery, stubFetch } from "./helpers";

const ban = (role: string, label: string, used: number, members: unknown[] = []) => ({
  role,
  label,
  budget: 16,
  used,
  left: 16 - used,
  open_tokens: 2,
  open_tasks: 1,
  done: 3,
  in_progress: 1,
  overdue: 1,
  late: 0,
  members,
});

const STATS = {
  date_from: "2026-10-01",
  date_to: "2026-10-31",
  today: "2026-10-10",
  my_ban: "DUNG",
  bans: [
    ban("BIEN_TAP", "Biên kịch", 4),
    ban("THIET_KE", "Design", 0),
    ban("DUNG", "Dựng", 20, [
      {
        user_id: "u-1",
        full_name: "Quỳnh Như",
        is_lead: false,
        budget: 8,
        used: 11,
        left: -3,
        today_left: -3,
        open_tokens: 2,
        open_tasks: 1,
        done: 2,
      },
    ]),
  ],
};

describe("Theo ban", () => {
  it("opens on the viewer's ban and switches to all", async () => {
    stubFetch([{ match: "/api/units/ADS/ban-stats", body: STATS }]);
    function Host() {
      const [chosen, setChosen] = useState<string | null>(null);
      return (
        <BanStatsPanel dateFrom="2026-10-01" dateTo="2026-10-31" ban={chosen} onBan={setChosen} />
      );
    }
    renderWithQuery(<Host />);
    const mine = await screen.findByTestId("ban-DUNG");
    expect(screen.getByRole("tab", { name: "Dựng (ban của bạn)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Over budget: red, "vượt".
    expect(within(mine).getByText("vượt 3")).toHaveClass("text-[var(--bad)]");
    expect(within(mine).getByText("Còn lại").nextSibling).toHaveClass("text-[var(--bad)]");

    await userEvent.click(screen.getByRole("tab", { name: "Tất cả" }));
    const all = await screen.findByTestId("ban-all");
    expect(within(all).getByText("Biên kịch")).toBeInTheDocument();
    expect(within(all).getByText("Tổng")).toBeInTheDocument();
  });
});
