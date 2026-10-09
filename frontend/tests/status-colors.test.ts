import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  STEP_LEGEND,
  WAITING_STATES,
  isWaitingLabel,
  rowStatusColor,
  stageColor,
  stepColor,
} from "@/lib/status-colors";

describe("each status has its own colour", () => {
  it("gives every Ads stage a different colour", () => {
    const stages = [
      "ORDER_PENDING",
      "ORDER_RETURNED",
      "BIEN_TAP",
      "THIET_KE",
      "DUNG",
      "GAN_LINK",
      "DUYET_VIDEO_BT",
      "FINAL_REVIEW",
      "COMPLETED",
      "CANCELLED",
    ];
    expect(new Set(stages.map(stageColor)).size).toBe(stages.length);
  });

  it("gives every step status in the legend a different colour", () => {
    const colours = STEP_LEGEND.map(([code]) => stepColor(code));
    expect(new Set(colours).size).toBe(colours.length);
  });

  it("colours a current PR phase cell like the stage it is at", () => {
    expect(stepColor("CURRENT", "HEAD_REVIEW")).toBe(stageColor("HEAD_REVIEW"));
  });

  it("colours every waiting state amber, never green or blue", () => {
    for (const code of ["CHO_DUYET", "CHO_PHAN_CONG", "DA_GIAO", "CHUA_GIAO"]) {
      expect(WAITING_STATES.has(code)).toBe(true);
      expect(stepColor(code)).toBe("amber");
      // A row at a production stage that waits for somebody is amber too.
      expect(rowStatusColor("DUNG", code, "Dựng")).toBe("amber");
    }
    // Accepted and being worked on: blue, like the stage.
    expect(stepColor("DANG_LAM")).toBe("blue");
    expect(rowStatusColor("DUNG", "DANG_LAM", "Dựng · Đang làm")).toBe(stageColor("DUNG"));
    expect(stepColor("HOAN_THANH")).toBe("green");
    expect(stepColor("DANG_SUA")).toBe("red");
  });

  it("treats any label that starts with Chờ as waiting, as a safety net", () => {
    expect(isWaitingLabel("Chờ Hùng duyệt")).toBe(true);
    expect(isWaitingLabel("Dựng · Chờ Hoàng Nam phân công")).toBe(true);
    expect(isWaitingLabel("Dựng · Đã giao Quỳnh Như")).toBe(true);
    expect(isWaitingLabel("Chờ giao")).toBe(true);
    expect(isWaitingLabel("Dựng · Đang làm")).toBe(false);
    expect(isWaitingLabel("Đã duyệt")).toBe(false);
    expect(isWaitingLabel(null)).toBe(false);
    // An unknown code with a waiting label, and the PR stages that wait.
    expect(stepColor("SOMETHING_NEW", undefined, "Chờ Lan duyệt video")).toBe("amber");
    expect(stepColor("CURRENT", "HEAD_REVIEW", "Chờ Trang duyệt")).toBe("amber");
    expect(rowStatusColor("FINAL_REVIEW", null, "Chờ Tuấn duyệt final")).toBe("amber");
    expect(rowStatusColor("COMPLETED", "HOAN_THANH", "Hoàn thành")).toBe("green");
  });

  it("defines a CSS class for every colour it can return", () => {
    const css = readFileSync(path.join(__dirname, "../src/app/globals.css"), "utf8");
    const all = new Set([
      ...["ORDER_PENDING", "IDEA", "AI_REVIEW", "APPROVED", "PUBLISHED", "BRIEFING", "X"].map(stageColor),
      ...STEP_LEGEND.map(([code]) => stepColor(code)),
    ]);
    for (const colour of all) expect(css).toContain(`.st-${colour} {`);
  });
});
