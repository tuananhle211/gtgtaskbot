/**
 * One colour per status, so a glance down the task table tells the states
 * apart. Colours are names of `.st-*` classes in globals.css (each has a light
 * and a dark variant). Unknown codes fall back to slate.
 */
export type StatusColor =
  | "slate"
  | "amber"
  | "red"
  | "violet"
  | "pink"
  | "blue"
  | "cyan"
  | "indigo"
  | "orange"
  | "green"
  | "teal"
  | "lime"
  | "sky"
  | "fuchsia";

/** A task's own stage (Ads order stages and PR workflow stages). */
const STAGE_COLORS: Record<string, StatusColor> = {
  // Ads
  ORDER_PENDING: "amber",
  ORDER_RETURNED: "red",
  BIEN_TAP: "violet",
  THIET_KE: "pink",
  DUNG: "blue",
  GAN_LINK: "cyan",
  DUYET_VIDEO_BT: "indigo",
  FINAL_REVIEW: "orange",
  COMPLETED: "green",
  CANCELLED: "slate",
  // PR
  IDEA: "slate",
  BRIEFING: "sky",
  SCRIPTING: "violet",
  AI_REVIEW: "fuchsia",
  TEAM_LEAD_REVIEW: "amber",
  HEAD_REVIEW: "orange",
  APPROVED: "lime",
  PRODUCTION: "blue",
  INTERNAL_REVIEW: "indigo",
  READY_TO_PUBLISH: "cyan",
  PUBLISHED: "teal",
  MEASURED: "green",
  ARCHIVED: "slate",
};

/** One step's standing inside a task (Ads node statuses, PR phase cells). */
const STEP_COLORS: Record<string, StatusColor> = {
  CHUA_TOI: "slate",
  BO_QUA: "slate",
  CHUA_GIAO: "amber",
  DANG_LAM: "blue",
  AI_DANG_REVIEW: "fuchsia",
  CHO_DUYET: "orange",
  DANG_SUA: "red",
  HOAN_THANH: "green",
  PENDING: "slate",
  DONE: "green",
};

export function stageColor(code: string): StatusColor {
  return STAGE_COLORS[code] ?? "slate";
}

/** A PR phase cell that is current takes the colour of the stage it is at. */
export function stepColor(code: string, currentStage?: string): StatusColor {
  if (code === "CURRENT" && currentStage) return stageColor(currentStage);
  return STEP_COLORS[code] ?? "slate";
}

/** The step statuses, in reading order, for the legend under the table. */
export const STEP_LEGEND: Array<[string, string]> = [
  ["CHUA_TOI", "Chưa tới"],
  ["CHUA_GIAO", "Chờ giao / phân công"],
  ["DANG_LAM", "Đang làm"],
  ["CHO_DUYET", "Chờ duyệt"],
  ["DANG_SUA", "Đang sửa"],
  ["HOAN_THANH", "Hoàn thành"],
];
