"use client";

import Link from "next/link";
import type { ContentSummary } from "@/lib/api";
import {
  contentTypeLabel,
  formatShortDay,
  priorityLabel,
  productionStateLabel,
  stageLabel,
} from "@/lib/labels";
import { Pill } from "@/components/states";

/**
 * The pieces every PR screen is built from.
 *
 * Step 1E.2 pulled these out of the pages because the same card was being
 * written three slightly different ways - once on the board, once on the
 * dashboard's review queue, once in "nội dung mới nhất" - and the three had
 * drifted into showing different fields. One component, one answer to "what does
 * a piece of content look like".
 *
 * Nothing here decides anything. A `StageBadge` renders the stage it is given;
 * it does not know which stage may follow it, and `ContentCard` links to a
 * detail page rather than offering an action, because the actions a person may
 * take are the server's to list.
 */

/** Page title, optional subtitle, and the one call to action that matters. */
export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-[var(--text-muted)]">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** The filled button. One per screen - more than one is no primary at all. */
export function PrimaryButton({
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

/** The outlined button, for everything that is not the forward move. */
export function SecondaryButton({
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

/**
 * Every dropdown in the PR admin.
 *
 * There is one of these because there were ten hand-rolled `<select>` elements
 * with four slightly different class strings between them, and the option-text
 * problem had to be fixed in each of them separately - which is how it came to be
 * fixed in some and not others. The rule that actually makes the
 * option list readable is in `globals.css`, since a native popup is drawn by the
 * OS and Tailwind classes on the `<select>` do not reach into it; this component
 * is the other half, so the *field* looks the same everywhere.
 *
 * `data-select="pr"` is a marker, not a style hook: it lets a test assert that a
 * given picker goes through here rather than reintroducing its own, which is the
 * only way to keep this from drifting back apart.
 */
export function Select({
  children,
  className = "",
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      data-select="pr"
      className={`min-h-11 max-w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] disabled:opacity-50 ${className}`}
    >
      {children}
    </select>
  );
}

/** Destructive styling. Used only for actions the server marked `DANGER`. */
export function DangerButton({
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`min-h-11 rounded-lg border border-red-500/50 bg-red-500/10 px-4 text-sm font-medium text-red-700 transition-colors hover:bg-red-500/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500 disabled:opacity-50 dark:text-red-300 ${className}`}
    >
      {children}
    </button>
  );
}

/**
 * Which stages get which colour.
 *
 * Colour is a reading aid, not a rule: "waiting on somebody" is amber, "done"
 * is green, "abandoned" is red. Grouping stages for a palette says nothing
 * about which of them may follow which.
 */
function stageTone(stage: string): "neutral" | "warn" | "good" | "bad" {
  if (stage === "CANCELLED") return "bad";
  if (["TEAM_LEAD_REVIEW", "HEAD_REVIEW", "INTERNAL_REVIEW", "AI_REVIEW"].includes(stage)) {
    return "warn";
  }
  // ``MEASURED`` stays in this list although Step 1F.2.3f.5 retired the stage:
  // a transition-history row can still name it, and a badge with no tone would
  // be the one thing on the screen that looked broken.
  if (["APPROVED", "READY_TO_PUBLISH", "PUBLISHED", "MEASURED"].includes(stage)) return "good";
  return "neutral";
}

export function StageBadge({ stage }: { stage: string }) {
  return <Pill tone={stageTone(stage)}>{stageLabel(stage)}</Pill>;
}

/**
 * Priority, shown only when it is not the default.
 *
 * Every item being labelled "Bình thường" is a column of noise that trains
 * people to stop reading the badge, which is the one place "Gấp" has to be
 * noticed. The detail page shows it unconditionally - there, one field among
 * twenty is a fact rather than clutter, and "what is this set to" is a question
 * a blank answers badly.
 *
 * Three escalating treatments, and the **word is always rendered**: colour
 * carries the urgency for somebody scanning, and the label carries it for
 * everybody else. Step 1F.2.3d.
 */
export function PriorityBadge({ priority }: { priority: string }) {
  if (priority === "NORMAL") return null;
  const tone =
    priority === "CRITICAL"
      ? "critical"
      : priority === "URGENT"
        ? "bad"
        : priority === "HIGH"
          ? "warn"
          : "neutral";
  return <Pill tone={tone}>{priorityLabel(priority)}</Pill>;
}

/**
 * What kind of thing this is, for a card or a detail row. Step 1F.2.3e.
 *
 * Always rendered, including "Chưa phân loại": unlike priority, where the
 * default is most rows and a badge on each would be noise, a format is
 * genuinely informative on every card - it is the difference between a
 * three-line script and a TVC. Neutral tone throughout, because a format is a
 * classification and not a severity; priority is the badge that shouts.
 */
export function ContentTypeBadge({ contentType }: { contentType: string | null }) {
  return <Pill tone="neutral">{contentTypeLabel(contentType)}</Pill>;
}

/**
 * One piece of content, as a tappable card.
 *
 * The **title** is the primary line and the code is muted metadata underneath -
 * people talk about "bài chăm sóc sau sinh", not about `CNT-2026-000003`, and a
 * board that leads with the code asks everybody to memorise a counter.
 *
 * Fields are rendered only when the API actually carries them. `brand_id` and
 * the channel are deliberately absent: the summary response has an id with no
 * name endpoint behind it, and a UUID on a card is worse than nothing.
 */
export function ContentCard({
  item,
  assignee,
  brand,
  showStage = false,
  selectable,
}: {
  item: ContentSummary;
  assignee?: string;
  brand?: string;
  showStage?: boolean;
  /**
   * Step 1F.2.8. Offered only for a card the **server** said this session may
   * approve, and rendered *beside* the card rather than inside it: the card is a
   * link, and a checkbox inside a link is a control that navigates when you try
   * to tick it. Absent, the card is exactly what it was.
   */
  selectable?: { checked: boolean; onChange: () => void; label: string };
}) {
  const card = (
    <Link
      href={`/pr/content/${item.id}`}
      className="block rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 transition-colors hover:border-[var(--text-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
    >
      <p className="text-sm font-medium leading-snug">{item.title}</p>
      <p className="mt-1 font-mono text-[11px] text-[var(--text-muted)]">{item.code}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {showStage ? <StageBadge stage={item.workflow_stage} /> : null}
        {/* Step 1F.2.3b. Two cards at ``APPROVED`` are not the same card: one is
            waiting for somebody to pick it up and the other is somebody's next
            job. The server derives which; the lane cannot show it, because both
            are the same stage. */}
        {item.production_state ? (
          <Pill tone={item.production_state === "WAITING_FOR_PRODUCER" ? "warn" : "neutral"}>
            {productionStateLabel(item.production_state)}
          </Pill>
        ) : null}
        <PriorityBadge priority={item.priority} />
        {/* After the priority badge, deliberately: priority is the
            operational signal and stays first in the reading order. */}
        <ContentTypeBadge contentType={item.content_type} />
        {item.planned_publish_at ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            Hạn {formatShortDay(item.planned_publish_at)}
          </span>
        ) : null}
        {/*
          Step 1F.2.3f.6. One publication fact on the card, and no archive
          badge: which column a card is in already says whether it was archived,
          and there is no longer a second kind of "archived" to tell apart. The
          instant is the server's - the same `MIN` over active publications the
          month is read against - so the day here and the column it sits in can
          never disagree.
        */}
        {item.published_at ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            Thực tế đăng {formatShortDay(item.published_at)}
          </span>
        ) : null}
      </div>
      {brand || assignee ? (
        <p className="mt-1.5 truncate text-[11px] text-[var(--text-muted)]">
          {[brand, assignee].filter(Boolean).join(" · ")}
        </p>
      ) : null}
    </Link>
  );

  if (!selectable) return card;
  return (
    <div className="flex items-start gap-2">
      <input
        type="checkbox"
        checked={selectable.checked}
        onChange={selectable.onChange}
        aria-label={selectable.label}
        className="mt-3.5 size-4 shrink-0 accent-[var(--accent)]"
      />
      <div className="min-w-0 flex-1">{card}</div>
    </div>
  );
}

/**
 * A sticky action bar for phones.
 *
 * `pb-[env(safe-area-inset-bottom)]` is the load-bearing part: without it the
 * primary action sits under the iPhone home indicator, where a tap either does
 * nothing or dismisses the app. Hidden from `sm` upwards, where the same
 * buttons are already visible in the flow of the page.
 */
export function MobileActionBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 mt-4 border-t border-[var(--border)] bg-[var(--surface)]/95 px-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 backdrop-blur sm:hidden">
      {children}
    </div>
  );
}

/** A tab strip that scrolls itself rather than the page. */
export function TabStrip({
  tabs,
  active,
  onSelect,
  label,
}: {
  tabs: ReadonlyArray<{ key: string; label: string; count?: number }>;
  active: string;
  onSelect: (key: string) => void;
  label: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      // The strip may scroll; the page must not. `-mx-1 px-1` keeps the focus
      // ring of the first tab from being clipped by the overflow container.
      className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none]"
    >
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onSelect(tab.key)}
            className={`min-h-11 shrink-0 rounded-lg px-3.5 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              selected
                ? "bg-[var(--text)] font-medium text-[var(--surface)]"
                : "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {tab.label}
            {tab.count === undefined ? null : (
              <span className={selected ? "ml-1.5 opacity-70" : "ml-1.5 opacity-60"}>
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * One column of the board.
 *
 * A section, not a Kanban column: it is a `grid` cell on a wide screen and a
 * full-width block on a phone, so the same markup stacks vertically at 390px
 * without a horizontal scroller anywhere.
 *
 * Titled rather than staged. Step 1F.2.3c: the production half of the board is
 * columns of *derived production states* and the rest is columns of stages, and
 * a component that took a stage code could not draw the first four. Which
 * columns exist, in which order, and what each is called is `lib/board.ts`.
 *
 * ## Its own pager, because it is its own queue
 *
 * Step 1F.2.3c2. The board used to have one pager underneath it, and a lane was
 * whatever of the group's page happened to belong here - so *Đang sản xuất 3*
 * could draw nothing at all, because 155 items waiting for a producer had taken
 * every slot. A lane loads its own rows and offers its own "Xem thêm", and the
 * `count` in its header is the server's figure for the whole queue rather than
 * for the cards below it.
 */
/**
 * How a review lane offers bulk selection. Step 1F.2.8.
 *
 * Passed only to the three lanes that are review queues - see
 * `laneApprovalGate` - and every field of it is about *this* lane. Which cards
 * within it may be ticked is not here: that is `item.approvable_by_me`, from the
 * server, per card.
 */
export interface LaneSelection {
  /** Every id currently selected. This lane renders its own as checked. */
  selected: ReadonlySet<string>;
  onToggle: (item: ContentSummary) => void;
  /** Tick every eligible card **currently loaded here**. */
  onSelectPage: () => void;
  /**
   * Tick every eligible item at this step under the current filters, whether
   * loaded or not - resolved by the server into a frozen list of ids.
   */
  onSelectAllAtStep: () => void;
  /**
   * How many the server says are eligible at this step. Shown in the control's
   * own label, because "chọn tất cả" with no number is a promise nobody can
   * check - and it is deliberately **not** the lane's `count`, which includes
   * the items at this step that this person may not approve.
   */
  eligibleTotal?: number;
  /** The step's name, for the label: "…ở bước Duyệt nội bộ". */
  stepLabel: string;
  /** The select-all request is in flight. */
  loadingAll?: boolean;
}

export function BoardLane({
  title,
  items,
  assignees,
  brands,
  count,
  loading = false,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  selection,
}: {
  title: string;
  items: ContentSummary[];
  assignees?: Map<string, string>;
  brands?: Map<string, string>;
  /**
   * How many items this column holds **under the current filter**, from the
   * server. Falls back to the number of cards drawn, which is only the same
   * thing when the whole lane is loaded - so a lane showing 20 of 155 says 155
   * rather than 20. Step 1F.2.2: the counts and the cards come from one filter,
   * and this is where that arrives on screen.
   */
  count?: number;
  /**
   * This lane's first page is still in flight.
   *
   * Drawn as such rather than as the empty state, because "Chưa có nội dung ở
   * bước này" is a claim about the queue and a lane that has not answered yet
   * has not made it. Each lane loads on its own since Step 1F.2.3c2, so this is
   * per column and not per board.
   */
  loading?: boolean;
  /**
   * Whether the server has more rows for **this** lane. Decided by the caller
   * from the lane's own total, never from a board-wide pager: a lane with three
   * items has all three and needs no control, whatever the lane beside it holds.
   */
  hasMore?: boolean;
  /** A load in flight, so the control says so instead of looking inert. */
  loadingMore?: boolean;
  /** Append this lane's next page. Touches no other lane. */
  onLoadMore?: () => void;
  /** Bulk selection, for the three lanes that are review queues. Step 1F.2.8. */
  selection?: LaneSelection;
}) {
  const remaining = count === undefined ? 0 : Math.max(0, count - items.length);
  // Only cards the server marked approvable are selectable, and the two
  // select-all controls only appear when there is at least one of them here.
  const eligible = selection ? items.filter((item) => item.approvable_by_me) : [];
  const pageSelected =
    eligible.length > 0 && eligible.every((item) => selection?.selected.has(item.id));
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-2 flex items-center justify-between gap-2 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        <span className="truncate">{title}</span>
        <span className="rounded-full bg-[var(--surface-muted)] px-1.5 py-0.5 tabular-nums">
          {count ?? items.length}
        </span>
      </h3>
      {selection && eligible.length > 0 ? (
        // Two controls, and the difference between them is stated rather than
        // implied. "Trên trang" is what is loaded here; the other is the whole
        // step under the current filters, which is usually a bigger number and
        // must never be reachable by accident.
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={pageSelected}
              onChange={selection.onSelectPage}
              className="size-4 accent-[var(--accent)]"
            />
            Chọn tất cả trên trang ({eligible.length})
          </label>
          {selection.eligibleTotal !== undefined &&
          selection.eligibleTotal > eligible.length ? (
            <button
              type="button"
              disabled={selection.loadingAll}
              onClick={selection.onSelectAllAtStep}
              className="underline underline-offset-2 hover:no-underline disabled:opacity-60"
            >
              {selection.loadingAll
                ? "Đang chọn…"
                : `Chọn tất cả ${selection.eligibleTotal} nội dung ở bước ${selection.stepLabel}`}
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="space-y-2">
        {items.map((item) => (
          <ContentCard
            key={item.id}
            item={item}
            assignee={assignees?.get(item.owner_user_id)}
            brand={brands?.get(item.brand_id)}
            selectable={
              selection && item.approvable_by_me
                ? {
                    checked: selection.selected.has(item.id),
                    onChange: () => selection.onToggle(item),
                    label: `Chọn ${item.title}`,
                  }
                : undefined
            }
          />
        ))}
        {items.length === 0 ? (
          <p className="rounded-xl border border-dashed border-[var(--border)] p-3 text-xs text-[var(--text-muted)]">
            {loading ? "Đang tải…" : "Chưa có nội dung ở bước này."}
          </p>
        ) : null}
        {hasMore && onLoadMore ? (
          // Named with the lane, because there is one of these per column and
          // "Xem thêm" on its own would be four identical buttons to a screen
          // reader. The remaining figure is the server's count minus what is
          // loaded, so it is about the queue and not about the request.
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            aria-label={`Xem thêm ${title}`}
            className="min-h-11 w-full rounded-xl border border-dashed border-[var(--border)] px-3 text-xs font-medium text-[var(--text-muted)] hover:bg-[var(--surface-muted)] disabled:opacity-60"
          >
            {loadingMore ? "Đang tải thêm…" : remaining > 0 ? `Xem thêm (còn ${remaining})` : "Xem thêm"}
          </button>
        ) : null}
      </div>
    </section>
  );
}

/** A summary figure, over counts the server produced. */
export function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3">
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

/** One choice in a {@link ScopeMultiSelect}. `value` is a canonical id or code. */
export interface ScopeOption {
  value: string;
  label: string;
  /** Optional muted suffix - a channel code beside its name. */
  hint?: string;
}

/**
 * Pick many canonical values, or all of them.
 *
 * Built for the two axes of an approval grant - content classifications and
 * channels - and shaped by what that decision actually needs:
 *
 * * **"Tất cả" is a mode, not a select-everything button.** Ticking it does not
 *   fill the list with today's ids; it tells the server `ALL`, which keeps
 *   covering a channel created next month. That distinction is the whole reason
 *   this is not a plain multi-`<select>`, and pressing it hides the list rather
 *   than checking every row, so nobody can be looking at eleven ticks while the
 *   grant means "every channel there will ever be".
 * * **The summary is always visible.** The header carries either the "Tất cả"
 *   label or the count, so a long list scrolled halfway still says how much has
 *   been selected - which is the number somebody handing out approval rights is
 *   actually deciding.
 * * **Chips, capped.** The first few selections are named and the rest are a
 *   "+N" pill: naming eleven channels in a form field is clutter, naming none of
 *   them is a mystery.
 *
 * Checkboxes rather than a `<select multiple>`: ctrl-clicking to keep a previous
 * selection is a desktop idiom that does not exist on a phone, and losing four
 * choices to one stray tap is not a thing to risk on a permissions screen.
 */
export function ScopeMultiSelect({
  legend,
  options,
  selected,
  onSelectedChange,
  all,
  onAllChange,
  allLabel,
  emptyHint,
}: {
  legend: string;
  options: ScopeOption[];
  selected: string[];
  onSelectedChange: (next: string[]) => void;
  all: boolean;
  onAllChange: (next: boolean) => void;
  allLabel: string;
  emptyHint?: string;
}) {
  const chosen = new Set(selected);
  const toggle = (value: string) =>
    onSelectedChange(
      chosen.has(value) ? selected.filter((entry) => entry !== value) : [...selected, value],
    );
  const named = options.filter((option) => chosen.has(option.value)).slice(0, 3);
  const overflow = selected.length - named.length;

  return (
    <fieldset className="rounded-lg border border-[var(--border)] p-3">
      <legend className="px-1 text-sm font-medium">{legend}</legend>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={all}
            onChange={(event) => onAllChange(event.target.checked)}
            className="h-4 w-4 accent-[var(--text)]"
          />
          Chọn tất cả
        </label>
        <span className="text-xs text-[var(--text-muted)]" data-testid={`scope-count-${legend}`}>
          {all ? allLabel : `Đã chọn ${selected.length}`}
        </span>
      </div>

      {all ? null : (
        <>
          <div className="mt-2 max-h-44 space-y-1 overflow-y-auto pr-1">
            {options.length === 0 ? (
              <p className="text-xs text-[var(--text-muted)]">{emptyHint ?? "Chưa có lựa chọn."}</p>
            ) : null}
            {options.map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={chosen.has(option.value)}
                  onChange={() => toggle(option.value)}
                  // The hint sits inside the label, so without this the
                  // accessible name would be "TikTok BS TiếnCH-0001" - a name
                  // nobody, screen reader or test, would think to ask for.
                  aria-label={option.label}
                  className="h-4 w-4 accent-[var(--text)]"
                />
                <span>{option.label}</span>
                {option.hint ? (
                  <span className="text-xs text-[var(--text-muted)]">{option.hint}</span>
                ) : null}
              </label>
            ))}
          </div>
          {selected.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {named.map((option) => (
                <Pill key={option.value} tone="neutral">
                  {option.label}
                </Pill>
              ))}
              {overflow > 0 ? <Pill tone="neutral">{`+${overflow}`}</Pill> : null}
            </div>
          ) : null}
        </>
      )}
    </fieldset>
  );
}
