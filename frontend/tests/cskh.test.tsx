import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { channelsNavigation, confirm, renderWithQuery, stubFetch } from "./helpers";
import type { UnitsMe } from "@/lib/api";
import { parseTagIds } from "@/lib/cskh";

const NAV = channelsNavigation("/cskh");
vi.mock("next/navigation", () => NAV.module);

const { CskhWorkspace } = await import("@/components/cskh");
const { navFor, navLinks } = await import("@/components/shell");

const REPLY = {
  id: "r1",
  page_row_id: "p1",
  page_name: "Apexmed HN",
  conversation_row_id: "c1",
  status: "SUGGESTED",
  status_label: "Chờ duyệt",
  reason: null,
  reason_label: null,
  suggested_text: "Dạ, em chào chị ạ.",
  sent_text: null,
  handoff: true,
  handoff_reason: "khách muốn đặt lịch",
  violations: [{ rule: "Cam kết", quote: "chắc chắn", reason: "hứa kết quả" }],
  review_passed: false,
  review_note: null,
  customer_at: "2026-11-09T02:00:00Z",
  answered_at: "2026-11-09T02:00:30Z",
  sent_at: null,
  decided_by: null,
  auto_sent: false,
  paused: "handoff",
  paused_label: "Agent đã chuyển cho người",
  paused_until: "2026-11-09T14:00:00Z",
};

const PAGE = {
  id: "p1",
  page_id: "1122334455",
  name: "Apexmed HN",
  active: false,
  mode: "SUGGEST",
  mode_label: "Gợi ý - người duyệt rồi gửi",
  agent_slug: "chatpage",
  access_token_hint: "••••3456",
  has_token: true,
  webhook_url: "https://apm.example/api/cskh/pancake/webhook/KEY",
  handoff_tag_ids: ["12"],
  stop_when_assigned: false,
  last_event_at: null,
  last_checked_at: null,
  last_check_ok: null,
  last_check_message: null,
};

describe("Chăm sóc khách hàng", () => {
  beforeEach(() => NAV.reset());
  afterEach(() => vi.unstubAllGlobals());

  it("is a nav item of its own, with the waiting count", () => {
    const me = (cskh: UnitsMe["cskh"]) =>
      ({
        units: [],
        default_unit: null,
        can_view_all: false,
        can_admin: [],
        can_tag: [],
        can_edit_members: [],
        departments: [],
        effort_enabled: true,
        cskh,
      }) as unknown as UnitsMe;
    const item = (value: UnitsMe) =>
      navLinks(navFor(value, "EMPLOYEE")).find((link) => link.href === "/cskh");
    expect(item(me({ reply: true, manage: false, waiting: 3 }))?.badge).toBe(3);
    expect(item(me({ reply: false, manage: false, waiting: 0 }))).toBeUndefined();
    expect(item(me(null))).toBeUndefined();
  });

  it("sends an edited suggestion after confirming", async () => {
    const fetchMock = stubFetch([
      { match: "/api/cskh/access", body: { reply: true, manage: false, configured: true } },
      { match: "/api/cskh/replies?tab=waiting", body: { replies: [REPLY], counts: { SUGGESTED: 1 } } },
      { match: "/api/cskh/replies/r1/send", method: "POST", body: { ...REPLY, status: "SENT" } },
    ]);
    renderWithQuery(<CskhWorkspace />);
    const box = await screen.findByDisplayValue("Dạ, em chào chị ạ.");
    expect(screen.getByText(/Agent xin chuyển người: khách muốn đặt lịch/)).toBeInTheDocument();
    expect(screen.getByText("Phạm luật (1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Chờ duyệt \(1\)/ })).toBeInTheDocument();
    // No pages tab without CSKH MANAGE.
    expect(screen.queryByRole("button", { name: "Kết nối Pancake" })).not.toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, "Dạ chị để lại SĐT giúp em ạ.");
    await userEvent.click(screen.getByRole("button", { name: "Gửi cho khách" }));
    await confirm();
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: { url: string; body: unknown }[] }).calls;
      expect(calls.find((call) => call.url.includes("/send"))?.body).toEqual({
        text: "Dạ chị để lại SĐT giúp em ạ.",
      });
    });
  });

  it("shows the OWNER the pages with their webhook address, never the token", async () => {
    NAV.arriveAt("/cskh?tab=pages");
    stubFetch([
      { match: "/api/cskh/access", body: { reply: true, manage: true, configured: true } },
      { match: "/api/cskh/replies?tab=waiting", body: { replies: [], counts: {} } },
      {
        match: "/api/cskh/pages",
        body: {
          pages: [PAGE],
          agents: [{ slug: "chatpage", name: "Chat Page" }],
          modes: [
            { value: "SUGGEST", label: "Gợi ý - người duyệt rồi gửi" },
            { value: "AUTO", label: "Tự động gửi (câu hỏi đơn giản)" },
          ],
          secret_ready: true,
          configured: true,
        },
      },
    ]);
    renderWithQuery(<CskhWorkspace />);
    expect(await screen.findByText(PAGE.webhook_url)).toBeInTheDocument();
    expect(screen.getByText("••••3456")).toBeInTheDocument();
    expect(screen.getByText("Đang tắt")).toBeInTheDocument();
    expect(screen.getByText("Chat Page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Thêm page" })).toBeInTheDocument();
  });

  it("reads tag ids typed by hand", () => {
    expect(parseTagIds("12, 15 15;18")).toEqual(["12", "15", "18"]);
  });
});
