/**
 * How the content work queue is divided, and the only place that decides it.
 *
 * Step 1F.2.3c. Before this there were three different groupings of the same
 * thirteen stages on one screen - the tab strip's, the lanes', and a row of KPI
 * tiles with a fourth opinion - and they disagreed about the one thing that
 * mattered: `INTERNAL_REVIEW` was filed under "Chờ duyệt", beside the two script
 * gates. It is not one of those. It happens *after* a producer has been named,
 * has started, and has handed in a cut, so a lead opening "Chờ duyệt" was shown
 * finished videos among the scripts waiting for them, and the production tab
 * that should have ended with them did not.
 *
 * So: one table, `OPERATIONAL_GROUPS`, and everything on the board reads from
 * it - the tabs, the columns, the counts and which card lands where. Adding a
 * stage means adding it here once.
 *
 * ## The key is the server's, and the group is a query
 *
 * Step 1F.2.3c1. A `key` here is a `PrContentGroup` value and is sent as
 * `?group=` on every board request, because grouping *after* pagination is not
 * grouping: with 146 items matching the filters and five of them in "Chuẩn bị",
 * the page was handed sixty rows, kept the preparation ones, and drew a board
 * with one card on page 1 and four on page 3 - under a tab that correctly said
 * 5. The server narrows before `LIMIT` now, so a page of a group is a page of
 * that group, and the stage list below is the browser's copy of a mapping the
 * Python domain owns (`GROUP_STAGES` in `domain/pr/content_views.py`).
 *
 * ## Ordering and words, not rules
 *
 * What stays local is what a client should decide: the **order** of the tabs,
 * the **columns** inside each, the **labels** and the empty sentences. No
 * service knows the word "Sản xuất"; moving a stage between groups changes
 * nothing about which transitions are legal, and adjacency inside a group is
 * not an edge - the matrix that says what may happen next lives in the Python
 * domain layer, and the detail page asks the server for it rather than reading
 * this table. Nor is a group an access rule: who may act at a stage is the
 * grant for it, resolved server-side, whichever tab happens to draw the card.
 *
 * ## Why the production group is columns of *states* and not of stages
 *
 * Two cards at `APPROVED` are not the same card: one is waiting for somebody to
 * pick it up and the other is somebody's next job. That difference is
 * `production_state`, which **the server derives** from the stage and the
 * producer - the browser must not combine those two fields itself, because
 * which is which is a rule. So the four production columns are keyed on that
 * derived state, and their counts come from the server's
 * `production_state_counts` rather than from the cards that fit on this page.
 *
 * ## A column is a lane, and a lane is a query
 *
 * Step 1F.2.3c2. `key` is not only a label to sort arrived cards by: it is a
 * `PrContentLane` value, sent as `?lane=` so the server narrows to one column
 * before it cuts the page. Making the *group* the unit of pagination was still
 * one level too coarse - "Sản xuất" is four independent queues, and with 155
 * items waiting for a producer and three actually in production, a sixty-row
 * page of the group contained none of the three. The header said 3 and the
 * board showed nothing until page three of a pager that belonged to another
 * column.
 *
 * So each column fetches and pages on its own, and this table is what says
 * which columns a group has. The two column kinds keep their two key spaces
 * only because the server's lane vocabulary is spelled the same way: a stage
 * column's key is the stage's name and a production column's key is the handoff
 * state's name, and both are `PrContentLane` values - so nothing here has to
 * translate a column into a lane.
 */

import type { ContentBoard, ContentSummary } from "@/lib/api";
import { productionStateLabel, stageLabel } from "@/lib/labels";

/**
 * What a column is keyed on.
 *
 * `STAGE` for most of the board, `PRODUCTION_STATE` for the production half -
 * see the module docstring on why those four columns cannot be stages.
 */
export type BoardColumnKind = "STAGE" | "PRODUCTION_STATE";

export interface BoardColumn {
  /**
   * A `PrWorkflowStage` or a `PrProductionHandoff` code, per `kind` - and
   * either way a `PrContentLane` value, which is what it is sent as. See the
   * module docstring: a column is one server-side queue, not a bucket the
   * browser sorts arrived cards into.
   */
  readonly key: string;
  readonly kind: BoardColumnKind;
}

/**
 * How many cards a lane shows before it offers "Xem thêm".
 *
 * Twenty rather than the sixty the whole board used to fetch. A lane is one
 * queue and twenty of it is a screenful; the number that tells somebody how much
 * work is really there is the header count, which is the server's and describes
 * the whole lane however few cards are loaded.
 */
export const LANE_PAGE_SIZE = 20;

export interface OperationalGroup {
  /**
   * The server's `PrContentGroup` value. Sent as `?group=` and carried in the
   * page URL, so it is a contract rather than a local name - see the module
   * docstring on why the group has to reach the query.
   */
  readonly key: string;
  readonly label: string;
  /**
   * Which workflow stages this group covers. The group's tab count is the sum
   * over these, and the "Bước" filter offers exactly these while the group is
   * open. The authority is the server's `GROUP_STAGES`; this is the same table
   * for the two things only the browser needs it for - what to count into a tab
   * and which stages to offer in the filter.
   */
  readonly stages: readonly string[];
  /** The columns to draw, in the order the work actually happens. */
  readonly columns: readonly BoardColumn[];
  /** What to say when the group has nothing in it. Specific to the group. */
  readonly empty: string;
}

const stageColumn = (key: string): BoardColumn => ({ key, kind: "STAGE" });
const stateColumn = (key: string): BoardColumn => ({ key, kind: "PRODUCTION_STATE" });

/**
 * The five groups of the work queue, in operational order.
 *
 * `CANCELLED` is one of them rather than a button off to the side: abandoned
 * work is somewhere people genuinely go looking, and a group whose count is
 * usually small costs a tab and stops it being a special case in the page.
 */
export const OPERATIONAL_GROUPS: readonly OperationalGroup[] = [
  {
    key: "PREPARATION",
    label: "Chuẩn bị",
    stages: ["IDEA", "BRIEFING", "SCRIPTING", "AI_REVIEW"],
    columns: [
      stageColumn("IDEA"),
      stageColumn("BRIEFING"),
      stageColumn("SCRIPTING"),
      stageColumn("AI_REVIEW"),
    ],
    empty: "Không có nội dung nào đang ở giai đoạn chuẩn bị.",
  },
  {
    key: "EDITORIAL_REVIEW",
    label: "Chờ duyệt",
    // The two *script* gates, and only those. `INTERNAL_REVIEW` is a review of a
    // finished cut and belongs to the group below - putting it here was the bug
    // Step 1F.2.3c fixed.
    stages: ["TEAM_LEAD_REVIEW", "HEAD_REVIEW"],
    columns: [stageColumn("TEAM_LEAD_REVIEW"), stageColumn("HEAD_REVIEW")],
    empty: "Không có nội dung nào đang chờ duyệt.",
  },
  {
    key: "PRODUCTION",
    label: "Sản xuất",
    stages: ["APPROVED", "PRODUCTION", "INTERNAL_REVIEW"],
    // Four columns over three stages, in the order the work moves: nobody has
    // taken it, somebody has, they are cutting it, it is with the internal
    // reviewer.
    columns: [
      stateColumn("WAITING_FOR_PRODUCER"),
      stateColumn("READY_FOR_PRODUCTION"),
      stateColumn("IN_PRODUCTION"),
      stateColumn("IN_INTERNAL_REVIEW"),
    ],
    empty: "Không có nội dung nào trong quy trình sản xuất.",
  },
  {
    key: "COMPLETED",
    label: "Hoàn tất",
    // Step 1F.2.3f.5 retired *Đã đo hiệu quả*; Step 1F.2.3f.6c moved *Lưu trữ*
    // to the archive view. Two columns, and the stage is out of `stages` as
    // well as out of `columns` - a lane removed from the display while its
    // stage still counted would leave a figure over the group that nothing on
    // screen adds up to. The server's own group table agrees, and its default
    // board neither returns nor counts archived rows.
    stages: ["READY_TO_PUBLISH", "PUBLISHED"],
    columns: [stageColumn("READY_TO_PUBLISH"), stageColumn("PUBLISHED")],
    empty: "Không có nội dung nào ở nhóm hoàn tất.",
  },
  {
    key: "CANCELLED",
    label: "Đã hủy",
    stages: ["CANCELLED"],
    columns: [stageColumn("CANCELLED")],
    empty: "Không có nội dung nào đã hủy.",
  },
];

/**
 * The archive, as the one "group" of its own view. Step 1F.2.3f.6c.
 *
 * Not in `OPERATIONAL_GROUPS` and never a tab: it is reached through
 * `?view=archive`, fetched only then, and drawn with the same lane component
 * as everything else so a card in the archive looks like a card. The server
 * reads its month against `archived_at`.
 */
export const ARCHIVE_VIEW: OperationalGroup = {
  key: "ARCHIVE",
  label: "Nội dung lưu trữ",
  stages: ["ARCHIVED"],
  columns: [stageColumn("ARCHIVED")],
  empty: "Không có nội dung nào được lưu trữ trong kỳ này.",
};

/** A column's heading. Derived, so a label is never written twice. */
export function columnLabel(column: BoardColumn): string {
  return column.kind === "PRODUCTION_STATE"
    ? productionStateLabel(column.key)
    : stageLabel(column.key);
}

/**
 * Whether this card belongs in this column.
 *
 * Note what the production branch does **not** do: it compares the server's
 * derived `production_state`, and never the stage together with a null test on
 * the producer column. The second is the same answer recomputed in the browser,
 * which is the rule leaking out of the server - so no field of that name is
 * read anywhere in this file.
 */
export function columnHolds(column: BoardColumn, item: ContentSummary): boolean {
  return column.kind === "PRODUCTION_STATE"
    ? item.production_state === column.key
    : item.workflow_stage === column.key;
}

/**
 * The approval gate a board column is a queue for, or `null` for the rest.
 *
 * Step 1F.2.8, and the only mapping in the browser between a column and a
 * review step. Three of the fifteen columns are review queues, and one of the
 * three is a *production-state* column rather than a stage one: `INTERNAL_REVIEW`
 * items are drawn under "Chờ duyệt nội bộ" in the production group, so keying
 * this on `column.key` alone would miss it or, worse, match a stage that only
 * looks like a gate.
 *
 * The `stage` it returns is the workflow stage the batch is standing at, which
 * is what the confirmation dialog names; the `gate` is the `PrApprovalStage`
 * the server takes. They are different vocabularies with, at these three,
 * identical spellings - written out rather than assumed equal.
 *
 * This decides **which lane offers checkboxes**, never who may approve. That is
 * `approvable_by_me`, per card, from the server.
 */
export function laneApprovalGate(
  column: BoardColumn,
): { readonly gate: string; readonly stage: string } | null {
  if (column.kind === "STAGE") {
    if (column.key === "TEAM_LEAD_REVIEW") {
      return { gate: "TEAM_LEAD_REVIEW", stage: "TEAM_LEAD_REVIEW" };
    }
    if (column.key === "HEAD_REVIEW") return { gate: "HEAD_REVIEW", stage: "HEAD_REVIEW" };
    return null;
  }
  return column.key === "IN_INTERNAL_REVIEW"
    ? { gate: "INTERNAL_REVIEW", stage: "INTERNAL_REVIEW" }
    : null;
}

/** Which group a stage is shown under, or `undefined` for a stage this app has never heard of. */
export function groupOfStage(stage: string): string | undefined {
  return OPERATIONAL_GROUPS.find((group) => group.stages.includes(stage))?.key;
}

/**
 * The group by key, falling back to the first - which is where the board opens.
 *
 * The fallback carries a URL now: `?group=` is typed, shared and bookmarked, so
 * a stale or misspelt value has to land somewhere sensible rather than on an
 * undefined group and a blank board.
 */
export function groupByKey(key: string): OperationalGroup {
  return OPERATIONAL_GROUPS.find((group) => group.key === key) ?? OPERATIONAL_GROUPS[0];
}

/** Which group this card is filed under. From its stage, which is the only input. */
export function contentOperationalGroup(item: ContentSummary): OperationalGroup | undefined {
  return OPERATIONAL_GROUPS.find((group) => group.stages.includes(item.workflow_stage));
}

/**
 * The words for where this card actually stands - its column's heading.
 *
 * "Chờ nhận sản xuất" rather than "Đã duyệt" for an unclaimed approved piece,
 * because approved is what happened and chờ nhận sản xuất is what somebody has
 * to do about it. Empty string when the card is at a stage no group covers.
 */
export function contentOperationalState(item: ContentSummary): string {
  const group = contentOperationalGroup(item);
  const column = group?.columns.find((entry) => columnHolds(entry, item));
  return column ? columnLabel(column) : "";
}

/**
 * The server's counts, indexed.
 *
 * Both maps are about the **whole filtered set** and not about this page of it,
 * which is what lets a column say 47 while showing 20 cards. Deriving either
 * from `board.items` is the mistake the envelope exists to prevent.
 *
 * "The whole filtered set" is the set **before the group narrows it** - the
 * server counts these without the `group` clause on purpose, because they label
 * the tabs and a tab has to say how much work is in the group you are not
 * standing in. The number that describes the *selected* group is `board.total`,
 * which is what the pager reads. Two counts, two scopes; mixing them makes four
 * tabs read 0 as soon as somebody uses the fifth.
 */
export interface BoardCounts {
  readonly byStage: ReadonlyMap<string, number>;
  readonly byProductionState: ReadonlyMap<string, number>;
}

export function boardCounts(board: ContentBoard | undefined): BoardCounts {
  return {
    byStage: new Map((board?.stage_counts ?? []).map((row) => [row.stage, row.count])),
    byProductionState: new Map(
      (board?.production_state_counts ?? []).map((row) => [row.production_state, row.count]),
    ),
  };
}

/**
 * How many items this column holds under the current filter - the **whole**
 * lane, not the cards loaded into it.
 *
 * The single source for a lane header and for whether "Xem thêm" has anything
 * left to load. Both count tables are computed by the server without the group
 * and without the lane, so this figure is the same number the lane's own request
 * reports as its `total` - by construction, since the predicates are the same
 * ones. Nothing in the browser recombines a stage with a producer column to get
 * it; see `columnHolds`.
 */
export function columnCount(column: BoardColumn, counts: BoardCounts): number {
  const table = column.kind === "PRODUCTION_STATE" ? counts.byProductionState : counts.byStage;
  return table.get(column.key) ?? 0;
}

/**
 * How many items this group holds under the current filter.
 *
 * Summed over the group's **stages**, not over its columns: every item at one of
 * those stages is in the group whether or not it landed in a column, so the tab
 * cannot quietly under-count. It also makes "Sản xuất" include the
 * `INTERNAL_REVIEW` items and "Chờ duyệt" exclude them, which is the correction
 * this module is for.
 */
export function groupCount(group: OperationalGroup, counts: BoardCounts): number {
  return group.stages.reduce((sum, stage) => sum + (counts.byStage.get(stage) ?? 0), 0);
}

/** The grid class for a group's columns. Static strings, so Tailwind sees them. */
export function columnGridClass(columns: readonly BoardColumn[]): string {
  if (columns.length <= 1) return "grid gap-4";
  if (columns.length === 2) return "grid gap-4 sm:grid-cols-2";
  return "grid gap-4 sm:grid-cols-2 xl:grid-cols-4";
}
