import type { AvailableAction } from "@/lib/api";

/** Whether the server offered `kind` among this content's available actions. */
export const has = (
  actions: AvailableAction[] | undefined,
  kind: AvailableAction["action"],
) => (actions ?? []).some((action) => action.action === kind);
