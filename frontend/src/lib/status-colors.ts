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
  // Legacy: an order still at the old link step (no new order reaches it).
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

/**
 * The states that mean "waiting for somebody", as the server sends them on a
 * step (`cell.status`) or a row (`row.state`, `task.state`): a decision
 * (`CHO_DUYET`), a Leader to hand it out (`CHO_PHAN_CONG`), the assignee to
 * press "Nhận việc" (`DA_GIAO`), or nobody to hand it out (`CHUA_GIAO`).
 * Every one of them is amber.
 */
export const WAITING_STATES: ReadonlySet<string> = new Set([
  "CHO_DUYET",
  "CHO_PHAN_CONG",
  "DA_GIAO",
  "CHUA_GIAO",
]);

/** One step's standing inside a task (Ads node statuses, PR phase cells). */
const STEP_COLORS: Record<string, StatusColor> = {
  CHUA_TOI: "slate",
  BO_QUA: "slate",
  CHUA_GIAO: "amber",
  CHO_PHAN_CONG: "amber",
  DA_GIAO: "amber",
  DANG_LAM: "blue",
  AI_DANG_REVIEW: "fuchsia",
  CHO_DUYET: "amber",
  DANG_SUA: "red",
  HOAN_THANH: "green",
  PENDING: "slate",
  DONE: "green",
};

/**
 * A label that reads as waiting: "Chờ Hùng duyệt", "Dựng · Chờ Nam phân
 * công", "Chờ giao", "Thiết kế · Đã giao Vy". The safety net for a code the
 * table above does not know yet.
 */
export function isWaitingLabel(label: string | null | undefined): boolean {
  if (!label) return false;
  return label
    .split("·")
    .map((part) => part.trim())
    .some((part) => part.startsWith("Chờ") || part.startsWith("Đã giao"));
}

export function stageColor(code: string): StatusColor {
  return STAGE_COLORS[code] ?? "slate";
}

/**
 * A step's colour. A PR phase cell that is current takes the colour of the
 * stage it is at; any waiting state - by code, or by a label that starts
 * with "Chờ" - is amber.
 */
export function stepColor(code: string, currentStage?: string, label?: string): StatusColor {
  if (WAITING_STATES.has(code) || isWaitingLabel(label)) return "amber";
  if (code === "CURRENT" && currentStage) return stageColor(currentStage);
  return STEP_COLORS[code] ?? "slate";
}

/**
 * A row's status colour (the "Trạng thái" column, the task header): amber
 * while it waits for somebody, else the colour of the stage it is at.
 */
export function rowStatusColor(
  stage: string,
  state?: string | null,
  label?: string | null,
): StatusColor {
  if ((state && WAITING_STATES.has(state)) || isWaitingLabel(label)) return "amber";
  return stageColor(stage);
}

/** The step statuses, in reading order, for the legend under the table. */
export const STEP_LEGEND: Array<[string, string]> = [
  ["CHUA_TOI", "Chưa tới"],
  ["CHO_DUYET", "Chờ duyệt / phân công / nhận · Đã giao"],
  ["DANG_LAM", "Đang làm"],
  ["DANG_SUA", "Đang sửa"],
  ["HOAN_THANH", "Hoàn thành"],
];
