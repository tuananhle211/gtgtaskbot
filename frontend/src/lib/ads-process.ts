/**
 * The Ads "Quy trình": which production nodes an order visits.
 *
 * Order (the head's approval), Gắn link and the final review are always on;
 * the person who orders ticks any non-empty set of Biên kịch / Design / Dựng.
 * The server stores the choice as a process code written with B, T, D in
 * pipeline order ("B", "T", "D", "BT", "BD", "TD", "BTD") and normalises a
 * node list the same way, so this file only mirrors it for the form's preview
 * and the task table's filter.
 */

export const PROCESS_NODES = [
  { node: "BIEN_TAP", letter: "B", label: "Biên kịch" },
  { node: "THIET_KE", letter: "T", label: "Design" },
  { node: "DUNG", letter: "D", label: "Dựng" },
] as const;

export type ProcessNode = (typeof PROCESS_NODES)[number]["node"];

/** The ticked nodes, in pipeline order. */
export function orderedProcess(nodes: Iterable<string>): ProcessNode[] {
  const chosen = new Set(nodes);
  return PROCESS_NODES.filter((item) => chosen.has(item.node)).map(
    (item) => item.node,
  );
}

/** "BD" for Biên kịch + Dựng. Empty when nothing is ticked. */
export function processCode(nodes: Iterable<string>): string {
  const chosen = new Set(nodes);
  return PROCESS_NODES.filter((item) => chosen.has(item.node))
    .map((item) => item.letter)
    .join("");
}

/** "Biên kịch › Dựng" for "BD". */
export function processCodeLabel(code: string): string {
  return PROCESS_NODES.filter((item) => code.includes(item.letter))
    .map((item) => item.label)
    .join(" › ");
}

/** Every allowed process code, for the task table's "Quy trình" filter. */
export const PROCESS_CODES = ["B", "T", "D", "BT", "BD", "TD", "BTD"] as const;

/** A video kind's points, Vietnamese style: 1, 1,5, 0,25. */
export function formatPoints(points: number | string | null | undefined): string {
  const value = Number(points ?? 0);
  return Number.isFinite(value) ? value.toLocaleString("vi-VN") : String(points);
}
