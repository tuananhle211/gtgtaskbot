import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { STEP_LEGEND, stageColor, stepColor } from "@/lib/status-colors";

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

  it("defines a CSS class for every colour it can return", () => {
    const css = readFileSync(path.join(__dirname, "../src/app/globals.css"), "utf8");
    const all = new Set([
      ...["ORDER_PENDING", "IDEA", "AI_REVIEW", "APPROVED", "PUBLISHED", "BRIEFING", "X"].map(stageColor),
      ...STEP_LEGEND.map(([code]) => stepColor(code)),
    ]);
    for (const colour of all) expect(css).toContain(`.st-${colour} {`);
  });
});
