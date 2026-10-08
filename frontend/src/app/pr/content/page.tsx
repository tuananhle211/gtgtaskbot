"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  api,
  type BulkArchiveResult,
  type ContentFilters,
  type ContentSummary,
  type Uuid,
} from "@/lib/api";
import { bulkArchiveConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import { CreateContentForm } from "@/components/pr-create-content";
import {
  CONTENT_SCOPES,
  DATE_FIELD_ORDER,
  COMPLETED_GROUP,
  DATE_PRESETS,
  CONTENT_TYPE_ORDER,
  PRIORITY_BY_URGENCY,
  PRIORITY_ORDER,
  UNCLASSIFIED_CONTENT_TYPE,
  SELECTABLE_DISTRIBUTION_MODES,
  contentTypeLabel,
  dateFieldLabel,
  datePresetRange,
  monthLabel,
  distributionModeLabel,
  previousMonth,
  priorityLabel,
  periodOptionsFor,
  stageLabel,
} from "@/lib/labels";
import {
  ARCHIVE_VIEW,
  LANE_PAGE_SIZE,
  OPERATIONAL_GROUPS,
  type BoardColumn,
  boardCounts,
  columnCount,
  columnGridClass,
  columnHolds,
  columnLabel,
  groupByKey,
  groupCount,
  groupOfStage,
  laneApprovalGate,
} from "@/lib/board";
import { Empty, ErrorBox, Loading } from "@/components/states";
import {
  BoardLane,
  type LaneSelection,
  PageHeader,
  PrimaryButton,
  SecondaryButton,
  Select,
  TabStrip,
} from "@/components/pr";
import {
  BulkApprovalBar,
  type BulkSelection,
  useBulkSelection,
} from "@/components/bulk-approval";
import {
  ResourceDraftSection,
  type ResourceDraft,
  resourceDraftPayload,
  resourceDraftProblems,
  serverResourceProblem,
} from "@/components/resource-drafts";

/**
 * The content workspace. The screen people live in.
 *
 * ## Why it is a work queue and not a list of everything
 *
 * At the volume this is built for - around fifty new items a day - "everything,
 * newest first" is not a workspace. A reviewer with three things waiting on them
 * arrived at a page where those three were somewhere among several hundred rows,
 * and the only tools for finding them were a stage dropdown and their own
 * scrolling. So the page opens on a **scope**: the server decides which one from
 * the capabilities the session holds, and it is never "Tất cả".
 *
 * Four things follow from that, and they are the whole of this step's frontend:
 *
 * 1. **Every filter is server-side.** There is no `.filter()` over a fetched
 *    array anywhere below. Fetching a few months of history to narrow it here
 *    would make the counts describe what happened to be downloaded.
 * 2. **The counts come from the same filter as the cards.** `/contents/board`
 *    answers both, so a lane cannot be labelled 120 while showing 7. Before this
 *    they were two endpoints with no shared filter.
 * 3. **The filters live in the URL.** Reload, back, forward and a pasted link
 *    all work, because the query string *is* the state - there is no
 *    `useState` copy of it to fall out of step. Step 1F.2.3c1 moved the last
 *    holdout, the lifecycle group, in with them.
 * 4. **The default scope is the server's answer.** The tab strip highlights
 *    `board.scope`, which is what the server applied. The browser does not
 *    decide who is a reviewer.
 *
 * ## Why there is no thirteen-lane board any more
 *
 * There was one, and it was 3400px wide. Every visit began by dragging sideways
 * to find the three stages that had anything in them, and on a phone it made the
 * whole page scroll horizontally - so `Tổng quan`, `Task` and every other screen
 * inherited a horizontal scrollbar they never needed.
 *
 * The stages are shown four-at-most at a time, grouped by `OPERATIONAL_GROUPS`
 * in `lib/board.ts`, which is the single place the grouping is decided - the
 * tabs, the columns, the counts and which card lands where all read from it.
 *
 * The open group is a **filter**, not a view of what already arrived: it goes
 * into the URL and into the request, and the server narrows by it before it
 * cuts the page. Step 1F.2.3c1 - grouping sixty fetched rows in the browser put
 * a five-item "Chuẩn bị" one card on page 1 and four on page 3. What stays local
 * is the reading order and the words: no service knows about "Chuẩn bị", moving
 * a stage between groups changes nothing about which transitions are legal, and
 * adjacency inside a group is not an edge.
 *
 * The same markup is a four-column grid on a desktop and a stack of sections on
 * a phone. There is no separate mobile board, and no drag-to-move - a dragged
 * card implies the move is legal, and only the Python matrix knows whether it is.
 *
 * ## Why there is no pager under the board any more
 *
 * Step 1F.2.3c2, and the same bug as 1F.2.3c1 one level deeper. A group is not
 * one queue: "Sản xuất" is four of them, and in production it held 155 items
 * waiting for a producer, none ready, three being cut and twelve in internal
 * review. One pager over the group meant the 155 took every slot on page 1, so a
 * lane header reading *Đang sản xuất 3* sat above an empty column and the three
 * cards were on page three - of a pager that belonged to a different column.
 *
 * A lane count that says 3 while all three cards are hidden is not a pagination
 * detail; it is the board lying about the work. So **each lane is its own
 * query**: `?lane=` narrows to one column before `LIMIT`, twenty rows at a time,
 * with its own "Xem thêm" that touches nothing else. The board-wide `?page=` is
 * gone, because there is no longer one thing for it to page.
 *
 * That makes this page issue *n + 1* requests for an n-column group: one that
 * asks for the figures and no rows at all (`limit=0`), and one per lane. They go
 * out together rather than in a chain - every lane mounts in the same render, so
 * the board is one round trip wide and never four deep. The alternative, one
 * fat request the browser then divided, is exactly what this step removed: the
 * page received sixty cards and drew three of them.
 *
 * ## Four regions, and no fifth
 *
 * Step 1F.2.3c. Top to bottom: **scope**, **filters**, **lifecycle groups**,
 * **board**. Each is its own landmark, so it is obvious where one ends and the
 * next begins, and there is nothing else on the page.
 *
 * In particular there are no summary tiles any more. Four of them used to sit
 * between the scope strip and the filters - "Đang xử lý", "Chờ duyệt", "Sẵn sàng
 * đăng", "Đã đăng" - and they were a report on a screen for processing work:
 * they pushed the filters and the first row of cards below the fold, and none of
 * them was something anybody clicked. Those figures belong on `Tổng quan`, where
 * they still are.
 */
export default function ContentBoardPage() {
  // `useSearchParams` suspends during prerender, and Next refuses to build a
  // page that reads it outside a boundary. The fallback is what a reader sees
  // for the length of one render, so it says what is happening rather than
  // flashing an empty board.
  return (
    <Suspense fallback={<Loading label="Đang tải nội dung…" />}>
      <ContentWorkspace />
    </Suspense>
  );
}

function ContentWorkspace() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  // `?create=1` opens the form at once - the shared "Tạo order" screen sends a
  // person here with it already open.
  const [creating, setCreating] = useState(params.get("create") === "1");

  // Everything the filter bar knows is read straight out of the URL. Reading it
  // into `useState` and syncing the two is where this kind of page usually goes
  // wrong - the back button then moves the URL and not the screen.
  const read = (key: string) => params.get(key) ?? "";
  const scope = read("scope");
  const search = read("q");
  const responsible = read("responsible");
  const platform = read("platform");
  const channel = read("channel");
  const stage = read("stage");
  const priority = read("priority");
  const contentType = read("content_type");
  const datePreset = read("date") || "ALL";
  const dateField = read("date_field") || DATE_FIELD_ORDER[0];
  const customFrom = read("from");
  const customTo = read("to");

  /**
   * Which group's work is on screen - in the URL, like every other filter, and
   * sent to the server with the rest of them.
   *
   * It was local state until Step 1F.2.3c1, on the reasoning that a tab only
   * regroups what has already arrived. That reasoning was the bug: with 146
   * items matching the filters and five of them in "Chuẩn bị", the browser was
   * handed page 1 of *everything*, kept the preparation cards in it, and drew
   * one card - with the other four on page 3, under a tab that said 5. A group
   * has to narrow the query before `LIMIT`, so it is a query parameter, and
   * being one it is bookmarkable, shareable and survives Back.
   *
   * Falls back to the tab `?stage=…` has a column in, so a shared stage link
   * still opens where its cards are rather than on an empty "Chuẩn bị".
   */
  const group =
    read("group") || groupOfStage(stage) || OPERATIONAL_GROUPS[0].key;
  /**
   * Step 1F.2.3f.6c. `?view=archive` opens **the archive**: archived content
   * alone, fetched only now. The default board never asks for it - not its
   * cards, not its counts - and the server's default board never returns it,
   * so nothing here is a column hidden over a query that still ran.
   */
  const archive = read("view") === "archive";
  /**
   * **Kỳ báo cáo**, `YYYY-MM`, from the URL - or nothing. Step 1F.2.3f.6d.
   *
   * An optional filter, off by default: the board opens on **every active
   * row**, and a month narrows it only when somebody chooses one. Nothing is
   * sent when the URL names no month, and the server reads a missing month as
   * no month - it never substitutes the current one. Which month *is* current
   * still comes from the server (`current_period`), for the selector's option
   * list and the previous-period archive; it is never quietly selected.
   *
   * It is a dimension of the whole board, not one group's: every request on
   * this page carries whatever is chosen, and switching tabs leaves it alone.
   */
  const selectedPeriod = read("period") || undefined;

  /**
   * Write a set of query params, dropping the empties.
   *
   * `replace` rather than `push`: a person adjusting four dropdowns should not
   * have to press Back four times to leave the page.
   *
   * `page` is stripped from every write, and this is not a reset - it is a
   * removal. Step 1F.2.3c2 took the board's global pager out, so a `?page=2`
   * left over from a bookmark or from browser history describes a pagination
   * this screen no longer has. Carrying it forward would do nothing visible and
   * put a meaningless parameter in every link somebody shares; letting it reach
   * a request would be worse, because it once hid whole lanes.
   */
  const setParams = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    next.delete("page");
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, {
      scroll: false,
    });
  };

  const custom = datePreset === "CUSTOM";
  const range = custom
    ? { from: customFrom, to: customTo }
    : (datePresetRange(datePreset) ?? { from: "", to: "" });

  const filters: ContentFilters = {
    // Omitted rather than defaulted: the server picks, and says which it picked.
    // The archive has no queue and no "mine": it is read as the whole archive.
    scope: archive ? "ALL" : scope || undefined,
    // Always sent, including on the first visit where it is the fallback above.
    // The group narrows before the page is cut, so leaving it off would page
    // through every group and show a fraction of this one - the bug. The
    // archive is not a group: it is its own view, and the server's stage
    // tables put ARCHIVED in no group at all.
    group: archive ? undefined : group,
    view: archive ? "ARCHIVE" : undefined,
    stage: stage || undefined,
    priority: priority || undefined,
    content_type: contentType || undefined,
    responsible_user_id: responsible || undefined,
    platform_id: platform || undefined,
    channel_id: channel || undefined,
    date_field: range.from || range.to ? dateField : undefined,
    date_from: range.from || undefined,
    date_to: range.to || undefined,
    /**
     * **Kỳ báo cáo, on every request.** Step 1F.2.3f.6.
     *
     * The month is a dimension of the dataset the way the scope is, so it goes
     * with every group and every lane - the tab counts, the lane headers and
     * the cards are all read against it, and against nothing the browser
     * derived. Which fact each lane is read by is the server's table.
     *
     * Deliberately not one of the date filters above: those narrow whatever is
     * on screen on whichever dimension somebody picked, and this decides which
     * month's board is on screen. They compose.
     */
    period: selectedPeriod,
    search: search || undefined,
  };

  /**
   * The board's figures, and deliberately none of its cards.
   *
   * `limit: 0` asks the server for the counts alone. Before Step 1F.2.3c2 this
   * request also carried sixty rows which the page divided into four columns,
   * and most of them were thrown away the moment a group had one busy lane -
   * which is what put the three "Đang sản xuất" cards on page three. The cards
   * come from the lane queries below, one per column; this one exists for the
   * tab counts, the lane headers and the scope the server resolved.
   */
  const board = useQuery({
    // The filter object is the key, so every distinct view is cached separately
    // and going back to one is instant rather than a refetch.
    queryKey: ["content-board", filters],
    queryFn: () => api.contentBoard({ ...filters, limit: 0 }),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const brands = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const platforms = useQuery({
    queryKey: ["platforms"],
    queryFn: () => api.listPlatforms(),
  });
  const channels = useQuery({
    queryKey: ["channels", "ACTIVE"],
    queryFn: () => api.listChannels({ status: "ACTIVE" }),
  });

  const names = new Map(
    (people.data ?? []).map((person) => [person.user_id, person.full_name]),
  );
  // One request for the whole board, not one per card. A brand the list does
  // not carry - a retired one - renders as no chip rather than as a UUID.
  const brandNames = new Map(
    (brands.data ?? []).map((brand) => [brand.id, brand.name]),
  );

  // Every figure on this page comes from the response, so they are all about the
  // same filtered set. `activeScope` is the server's, never a local default.
  const counts = boardCounts(board.data);
  const activeScope = board.data?.scope ?? scope;
  /**
   * The month on screen: the server's echo once it has answered, the URL's
   * value before that. `undefined` is *Tất cả kỳ* - no month, the default.
   */
  const period = board.data ? (board.data.period ?? undefined) : selectedPeriod;
  const currentPeriod = board.data?.current_period ?? undefined;
  /**
   * Step 1F.2.3f.6b. **The option list is anchored on the current business
   * month, never on the selected one.** The server says which month is
   * current; the list runs from there backwards - at least twelve months, and
   * further when a deep link names an older month - so September stays one
   * click away after August is chosen, and `?period=2026-03` still offers
   * every month up to today's. Until the first response lands only the URL's
   * month (if any) is offered rather than a guess from the browser's clock.
   * *Tất cả kỳ* is drawn above the list, not in it: it is the absence of a
   * month, and `periodOptionsFor` deals in months.
   */
  const periodOptions = periodOptionsFor(currentPeriod, period);
  /**
   * Step 1F.2.3f.6d. **Which closed month the archive control offers.** An
   * explicitly selected closed month is the month on screen and the one to
   * put away; otherwise - *Tất cả kỳ*, or the current month itself - it is the
   * month before the current business month, from the server's
   * `current_period` and never from a clock. Both read as *"Lưu trữ nội dung
   * kỳ MM/YYYY"*, so the label always says which.
   */
  const archiveTarget =
    period && currentPeriod && period < currentPeriod
      ? period
      : currentPeriod
        ? previousMonth(currentPeriod)
        : undefined;
  /**
   * Step 1F.2.3f.6a. Whether the month narrowed what is on screen - the
   * server's word, echoed with the figures. `false` both when no month is
   * chosen and under *Cần tôi xử lý*, where a chosen month is kept but not
   * read. The two are told apart by the resolved scope, also the server's:
   * only the queue disables the selector.
   */
  const periodApplied = board.data?.period_applied ?? Boolean(selectedPeriod);
  const periodIgnoredByScope = activeScope === "MY_ACTIONS";

  const openGroup = archive ? ARCHIVE_VIEW : groupByKey(group);

  /**
   * Step 1F.2.8. Everything that defines *which items are on screen*, as one
   * string. A bulk selection is stored against it and dropped the moment it
   * changes, which is how every filter clears the selection with one rule
   * rather than one `useEffect` per control - see `components/bulk-approval`.
   *
   * The **resolved** scope, not the URL's: arriving with no `?scope=` and being
   * given `MY_ACTIONS` by the server is the same context as arriving with it,
   * and treating the two as different would clear a selection on the first
   * response of every visit. The lane is absent on purpose - a selection spans
   * the board's review lanes, and paging one of them adds cards rather than
   * changing which set is being chosen from.
   */
  const selectionSignature = JSON.stringify([
    activeScope,
    period,
    group,
    stage,
    priority,
    contentType,
    responsible,
    platform,
    channel,
    search,
    range.from,
    range.to,
    range.from || range.to ? dateField : "",
  ]);
  const bulk = useBulkSelection(selectionSignature);

  /**
   * Titles of whatever the lanes have loaded, for the confirmation's sample.
   *
   * Collected from the lanes rather than fetched: the dialog shows five of them
   * and counts the rest, so there is nothing to be gained by asking the server
   * for eighty titles nobody will read. A select-all whose ids are mostly not
   * on screen simply contributes fewer names, and the count carries the weight.
   */
  const [titles, setTitles] = useState<ReadonlyMap<string, string>>(new Map());
  const noteTitles = useCallback((items: ContentSummary[]) => {
    setTitles((previous) => {
      let changed = false;
      const next = new Map(previous);
      for (const item of items) {
        if (next.get(item.id) !== item.title) {
          next.set(item.id, item.title);
          changed = true;
        }
      }
      // Same map back when nothing is new, so this cannot loop: the lanes call
      // it on every render and only a genuinely new title re-renders the page.
      return changed ? next : previous;
    });
  }, []);

  /**
   * What to say after a batch. Success only: a refusal is rendered by
   * `ErrorBox` from the mutation's own error, which is where the server's
   * specific sentence and its `details` already are.
   */
  const [bulkResult, setBulkResult] = useState<string | null>(null);

  const approve = useMutation({
    mutationFn: (selection: BulkSelection) =>
      api.bulkApprove({
        gate: selection.gate,
        content_ids: [...selection.ids],
      }),
    onMutate: () => setBulkResult(null),
    onSuccess: (result) => {
      setBulkResult(`Duyệt thành công ${result.approved_count} nội dung.`);
      bulk.clear();
      void queryClient.invalidateQueries({ queryKey: ["content-board"] });
      void queryClient.invalidateQueries({ queryKey: ["content-lane"] });
      void queryClient.invalidateQueries({ queryKey: ["approvable"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const filtered = Boolean(
    scope ||
    selectedPeriod ||
    stage ||
    priority ||
    contentType ||
    responsible ||
    platform ||
    channel ||
    search ||
    datePreset !== "ALL",
  );
  // How much work the open group holds, read from the same counts the tab above
  // it shows - so the board and its own tab cannot disagree about whether there
  // is anything here. `board.total` is the same number by construction; taking
  // it from the counts means one figure rather than two that have to match.
  const rows = board.data ? groupCount(openGroup, counts) : 0;
  // What the *other* tabs hold, from the counts - which are over the filters
  // without the group. It is the difference between "this filter matches
  // nothing" and "nothing in this tab", and they need different sentences.
  const elsewhere = archive
    ? 0
    : OPERATIONAL_GROUPS.reduce(
        (sum, entry) => sum + groupCount(entry, counts),
        0,
      );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Nội dung"
        subtitle="Theo dõi và xử lý nội dung PR theo từng giai đoạn."
        action={
          <PrimaryButton onClick={() => setCreating((isOpen) => !isOpen)}>
            {creating ? "Đóng" : "+ Tạo nội dung"}
          </PrimaryButton>
        }
      />

      {/*
        Step 1F.2.3f.6. **Kỳ báo cáo - the month the whole board is about.**

        Above the scope strip and the filters, because it is the widest
        decision on the page: every tab count, every lane header and every card
        below is read against this month, each lane by its own business fact.
        It is not a filter and does not live among them - a person clearing the
        filters keeps their month - and there is exactly one of it: the
        completed-only "Kỳ công việc" selector is gone, because two month
        selectors is two opinions about which month is on screen.
      */}
      <section
        aria-label="Kỳ báo cáo"
        className="flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 sm:px-4"
      >
        <label className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Kỳ báo cáo
          </span>
          <Select
            value={period ?? ""}
            aria-label="Kỳ báo cáo"
            disabled={periodIgnoredByScope}
            onChange={(event) => setParams({ period: event.target.value })}
          >
            {/* The first option is the absence of a month - the default, and
                what "Xóa bộ lọc" returns to. Choosing it removes `period`
                from the URL rather than writing a magic value into it. */}
            <option value="">Tất cả kỳ</option>
            {periodOptions.map((code) => (
              <option key={code} value={code}>
                {monthLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        {periodIgnoredByScope ? (
          // The month is kept, not cleared: switching back to Tất cả re-applies
          // it. What changes is only whether it is in force, and that is said.
          <span role="note" className="text-xs text-[var(--text-muted)]">
            Không áp dụng cho “Cần tôi xử lý”: hàng đợi hiển thị mọi nội dung
            đang chờ bạn xử lý, kể cả tồn từ kỳ trước.
          </span>
        ) : archive ? (
          <span className="text-xs text-[var(--text-muted)]">
            {periodApplied ? "Tính theo ngày lưu trữ." : "Mọi nội dung đã lưu trữ."}
          </span>
        ) : periodApplied ? (
          <span className="text-xs text-[var(--text-muted)]">
            Mỗi bước tính theo mốc nghiệp vụ của bước đó: vào bước, bắt đầu sản
            xuất, ngày đăng thực tế.
          </span>
        ) : (
          <span className="text-xs text-[var(--text-muted)]">
            Mọi nội dung đang hoạt động. Chọn một tháng để xem theo kỳ.
          </span>
        )}
        {/* Step 1F.2.3f.6c. The one way into the archive, and the way back.
            A link that changes the URL, so the archive is a bookmarkable place
            rather than a mode - and so nothing about it is fetched until it
            is the place somebody is standing. */}
        <SecondaryButton
          className="ml-auto"
          onClick={() =>
            setParams(archive ? { view: "" } : { view: "archive", stage: "" })
          }
        >
          {archive ? "Quay lại bảng nội dung" : "Xem nội dung lưu trữ"}
        </SecondaryButton>
      </section>

      {/* REGION A - scope. The first thing on the page because it is the first
          decision: which slice of the work am I looking at. The archive has no
          slices - it is read whole - so the strip is not offered there. */}
      {archive ? (
        <h2 className="text-base font-semibold">Nội dung lưu trữ</h2>
      ) : (
        <TabStrip
          label="Phạm vi nội dung"
          active={activeScope}
          onSelect={(key) => setParams({ scope: key })}
          tabs={CONTENT_SCOPES.map((entry) => ({
            key: entry.key,
            label: entry.label,
          }))}
        />
      )}

      {creating ? (
        <CreateContentForm
          onCreated={() => {
            setCreating(false);
            void queryClient.invalidateQueries({ queryKey: ["content-board"] });
            void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          }}
        />
      ) : null}

      {/* REGION B - filters, in a panel of their own. They used to float
          directly above the cards, which made the first row of a busy board look
          like part of the filter bar; the border and the heading are what make
          "where I narrow the list" and "the list" two different places. */}
      <section
        aria-label="Bộ lọc"
        className="space-y-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 sm:p-4"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          Bộ lọc
        </h2>
        <label className="block">
          <span className="sr-only">Tìm nội dung</span>
          <input
            type="search"
            value={search}
            onChange={(event) => setParams({ q: event.target.value })}
            placeholder="Tìm theo tiêu đề hoặc mã…"
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 sm:max-w-sm"
          />
        </label>
        {/* Every control below narrows the same server-side query, so they
            compose: the board shows the intersection, and the counts above it
            are about that intersection. */}
        <div className="flex flex-wrap gap-2">
          <label className="min-w-0">
            <span className="sr-only">Lọc theo ngày</span>
            <Select
              value={datePreset}
              onChange={(event) => setParams({ date: event.target.value })}
            >
              {DATE_PRESETS.map((preset) => (
                <option key={preset.key} value={preset.key}>
                  {preset.label}
                </option>
              ))}
            </Select>
          </label>
          {datePreset === "ALL" ? null : (
            <label className="min-w-0">
              <span className="sr-only">Mốc ngày</span>
              <Select
                value={dateField}
                onChange={(event) =>
                  setParams({ date_field: event.target.value })
                }
              >
                {DATE_FIELD_ORDER.map((code) => (
                  <option key={code} value={code}>
                    {dateFieldLabel(code)}
                  </option>
                ))}
              </Select>
            </label>
          )}
          {custom ? (
            <>
              <label className="min-w-0 text-xs text-[var(--text-muted)]">
                <span className="sr-only">Từ ngày</span>
                <input
                  type="date"
                  value={customFrom}
                  onChange={(event) => setParams({ from: event.target.value })}
                  aria-label="Từ ngày"
                  className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3"
                />
              </label>
              <label className="min-w-0 text-xs text-[var(--text-muted)]">
                <span className="sr-only">Đến ngày</span>
                <input
                  type="date"
                  value={customTo}
                  onChange={(event) => setParams({ to: event.target.value })}
                  aria-label="Đến ngày"
                  className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3"
                />
              </label>
            </>
          ) : null}
          <label className="min-w-0">
            <span className="sr-only">Lọc theo nền tảng</span>
            <Select
              value={platform}
              onChange={(event) => setParams({ platform: event.target.value })}
            >
              <option value="">Mọi nền tảng</option>
              {(platforms.data ?? []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </Select>
          </label>
          <label className="min-w-0">
            <span className="sr-only">Lọc theo kênh</span>
            <Select
              value={channel}
              onChange={(event) => setParams({ channel: event.target.value })}
            >
              <option value="">Mọi kênh</option>
              {(channels.data ?? []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </Select>
          </label>
          <label className="min-w-0">
            <span className="sr-only">Lọc theo người phụ trách</span>
            <Select
              value={responsible}
              onChange={(event) =>
                setParams({ responsible: event.target.value })
              }
            >
              <option value="">Mọi người phụ trách</option>
              {(people.data ?? []).map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.full_name}
                </option>
              ))}
            </Select>
          </label>
          <label className="min-w-0">
            <span className="sr-only">Lọc theo bước</span>
            <Select
              value={stage}
              onChange={(event) => setParams({ stage: event.target.value })}
            >
              <option value="">Mọi bước</option>
              {openGroup.stages.map((code) => (
                <option key={code} value={code}>
                  {stageLabel(code)}
                </option>
              ))}
            </Select>
          </label>
          <label className="min-w-0">
            {/* Step 1F.2.3e. Beside the priority filter, in the same panel. The
                last option is "Chưa phân loại" - the historical rows that
                predate the field, which is a real slice somebody works through
                rather than an absence to hide. */}
            <span className="sr-only">Lọc theo loại nội dung</span>
            <Select
              value={contentType}
              onChange={(event) =>
                setParams({ content_type: event.target.value })
              }
            >
              <option value="">Mọi loại nội dung</option>
              {CONTENT_TYPE_ORDER.map((code) => (
                <option key={code} value={code}>
                  {contentTypeLabel(code)}
                </option>
              ))}
              <option value={UNCLASSIFIED_CONTENT_TYPE}>
                {contentTypeLabel(null)}
              </option>
            </Select>
          </label>
          <label className="min-w-0">
            {/* Step 1F.2.3d. In the same panel as every other filter, and sent
                to the server like every other one - the ordering already puts
                urgent work first, and this is for the times somebody wants only
                that. Most urgent first, because "Rất gấp" is what they came for. */}
            <span className="sr-only">Lọc theo mức ưu tiên</span>
            <Select
              value={priority}
              onChange={(event) => setParams({ priority: event.target.value })}
            >
              <option value="">Mọi mức ưu tiên</option>
              {PRIORITY_BY_URGENCY.map((code) => (
                <option key={code} value={code}>
                  {priorityLabel(code)}
                </option>
              ))}
            </Select>
          </label>
          {filtered ? (
            <SecondaryButton
              // Clears the filters and leaves the scope to the server again, so
              // "Xóa bộ lọc" returns to the page as it opens rather than to an
              // unfiltered list of everything. Step 1F.2.3f.6d: the month is an
              // optional filter now, so it is cleared with the rest - the page
              // as it opens is *Tất cả kỳ*. The open group is navigation and
              // stays.
              onClick={() => {
                const kept = new URLSearchParams();
                if (read("group")) kept.set("group", group);
                if (read("view")) kept.set("view", read("view"));
                const rendered = kept.toString();
                router.replace(rendered ? `${pathname}?${rendered}` : pathname, {
                  scroll: false,
                });
              }}
            >
              Xóa bộ lọc
            </SecondaryButton>
          ) : (
            // Kept in place and inert rather than removed, so the row does not
            // reflow the moment a filter is chosen or cleared.
            <SecondaryButton disabled className="text-[var(--text-muted)]">
              Xóa bộ lọc
            </SecondaryButton>
          )}
        </div>
      </section>

      {/* REGION C - where in the pipeline. Navigation, not metrics: each tab
          asks the server for that slice, and the number on it is how much work
          the slice holds under the filters above - which is why the counts are
          about the filtered set *before* the group narrows it. Counted after,
          the four tabs you are not on would all read 0. */}
      {archive ? null : (
        <TabStrip
          label="Nhóm quy trình"
          active={openGroup.key}
          // Three things move together, and each of them would be a bug alone:
          // the group goes into the URL and so into the next request; the stage
          // filter is cleared, because its options are the open group's stages and
          // a selection from the previous tab would filter to a stage with no
          // column; and `page` is dropped by `setParams`, because page 2 of a
          // 146-item view is past the end of a 5-item one.
          onSelect={(key) => setParams({ group: key, stage: "" })}
          tabs={OPERATIONAL_GROUPS.map((entry) => ({
            key: entry.key,
            label: entry.label,
            count: board.data ? groupCount(entry, counts) : undefined,
          }))}
        />
      )}

      {/*
        Step 1F.2.3f.6. **The end-of-period archive**, offered where the work
        it acts on is visible. *Lưu trữ* holds what was actually archived now,
        so last month's published output does not file itself away - a person
        with the transition right puts it away, deliberately, after seeing how
        many pieces that means. Nothing runs on the first of the month.
      */}
      {!archive && group === COMPLETED_GROUP && archiveTarget ? (
        <PeriodArchiveControl
          period={archiveTarget}
          onArchived={(result) => {
            setBulkResult(
              `Đã lưu trữ ${result.archived_count} nội dung đã đăng trong ${monthLabel(result.period)}.`,
            );
            void queryClient.invalidateQueries({ queryKey: ["content-board"] });
            void queryClient.invalidateQueries({ queryKey: ["content-lane"] });
            void queryClient.invalidateQueries({
              queryKey: ["archive-candidates"],
            });
            void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          }}
        />
      ) : null}

      {board.isPending ? <Loading label="Đang tải nội dung…" /> : null}
      {board.isError ? (
        <ErrorBox error={board.error} onRetry={() => board.refetch()} />
      ) : null}

      {/* REGION D - the board. Separated from the strip above by a rule rather
          than by spacing alone, so "which phase" and "the work in it" read as two
          things at a glance. */}
      {board.data ? (
        <section
          aria-label="Bảng nội dung"
          className="border-t border-[var(--border)] pt-4"
        >
          {rows === 0 ? (
            // Three different silences, and telling them apart is the whole
            // value of the message. The group's own sentence comes first when
            // the filters match work in *other* tabs - "Xóa bộ lọc" would be
            // wrong advice there, because the filter is fine and the tab is
            // empty.
            <Empty
              message={
                elsewhere > 0
                  ? openGroup.empty
                  : search
                    ? "Không tìm thấy nội dung nào khớp với từ khóa này."
                    : filtered
                      ? "Không có nội dung nào khớp bộ lọc hiện tại. Bấm “Xóa bộ lọc” để mở rộng."
                      : "Chưa có nội dung nào. Bấm “Tạo nội dung” để bắt đầu."
              }
            />
          ) : (
            // At most four columns, so this never needs a horizontal scroller:
            // one column on a phone, two on a tablet, four on a desktop. Each
            // one fetches its own rows - see `ContentLane`.
            <div className={columnGridClass(openGroup.columns)}>
              {openGroup.columns.map((column) => (
                <ContentLane
                  // Keyed by group *and* lane, so changing tab unmounts every
                  // lane of the old group rather than reusing a component that
                  // would carry its "Xem thêm" depth into the new one.
                  key={`${openGroup.key}:${column.key}`}
                  column={column}
                  filters={filters}
                  // The server's count for this column under this filter, which
                  // is the whole queue and not the cards loaded into it.
                  count={columnCount(column, counts)}
                  assignees={names}
                  brands={brandNames}
                  // Step 1F.2.8. Only the three review lanes get any of this,
                  // and even there only the cards the server marked approvable
                  // are selectable - see `laneApprovalGate` and
                  // `approvable_by_me`.
                  selected={bulk.selection?.ids ?? []}
                  selectionGate={bulk.selection?.gate}
                  onToggle={bulk.toggle}
                  onTogglePage={bulk.togglePage}
                  onSelectAll={bulk.selectAll}
                  onItems={noteTitles}
                />
              ))}
            </div>
          )}
        </section>
      ) : null}

      {/* Step 1F.2.8. The result of a batch, in words, above the bar that
          started it. Success is a sentence with the count in it; a refusal is
          the server's own sentence, which already says that nothing at all was
          approved. */}
      {bulkResult ? (
        <p
          role="status"
          className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-sm"
        >
          {bulkResult}
        </p>
      ) : null}
      {approve.isError ? <ErrorBox error={approve.error} /> : null}

      {bulk.selection ? (
        <BulkApprovalBar
          selection={bulk.selection}
          titles={titles}
          pending={approve.isPending}
          error={approve.error}
          onApprove={() => bulk.selection && approve.mutate(bulk.selection)}
          onClear={bulk.clear}
        />
      ) : null}
    </div>
  );
}

/**
 * Step 1F.2.3f.6. *"Lưu trữ nội dung kỳ 08/2026"* - the end-of-period archive.
 *
 * Reads the server's candidate set for the month before the one on screen and
 * offers it as one confirmed action. Three things are the server's and not
 * this component's: **whether** the session may archive (`may_archive`), **how
 * many** pieces the month holds (`total`, the same query as the *Đã đăng*
 * header for that month), and **which** ids the batch is - frozen at the read,
 * so nothing published or re-dated since can join it. The confirmation names
 * the count the server gave, and a `truncated` month says so rather than
 * rounding.
 *
 * Renders nothing when there is nothing to do or nobody entitled to do it: an
 * empty month is not a warning, and a button somebody may not press is a
 * refusal waiting to happen.
 */
function PeriodArchiveControl({
  period,
  onArchived,
}: {
  /** The closed month, `YYYY-MM`. */
  period: string;
  onArchived: (result: BulkArchiveResult) => void;
}) {
  const candidates = useQuery({
    queryKey: ["archive-candidates", period],
    queryFn: () => api.archiveCandidates(period),
  });
  const archive = useMutation({
    mutationFn: (ids: Uuid[]) => api.archiveBatch({ period, content_ids: ids }),
    onSuccess: (result) => {
      onArchived(result);
      void candidates.refetch();
    },
  });
  const found = candidates.data;
  if (!found || !found.may_archive || found.total === 0) return null;
  const count = found.content_ids.length;
  return (
    <section
      aria-label="Lưu trữ nội dung kỳ trước"
      className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 sm:px-4"
    >
      <p className="text-sm">
        <span className="font-medium">{found.total}</span> nội dung đã đăng
        trong {monthLabel(found.period)} vẫn đang ở “Đã đăng”.
        {found.truncated ? ` Mỗi lần lưu trữ tối đa ${found.limit}.` : ""}
      </p>
      <ConfirmButton
        spec={bulkArchiveConfirmation({
          count,
          period: found.period,
          eligibleTotal: found.truncated ? found.total : undefined,
        })}
        onConfirm={() => archive.mutate(found.content_ids)}
        pending={archive.isPending}
        error={archive.error}
        tone="secondary"
      >
        Lưu trữ nội dung kỳ {monthLabel(found.period).replace("Tháng ", "")}
      </ConfirmButton>
    </section>
  );
}

/**
 * One column of the board, fetching and paging its own rows.
 *
 * Step 1F.2.3c2, and the whole of the fix. Each lane is an independent work
 * queue, so each one is an independent query: `?lane=` reaches
 * `content_conditions` and narrows before `LIMIT`, which is what lets *Đang sản
 * xuất* show all three of its cards on the first render while *Chờ nhận sản
 * xuất* shows the first twenty of its 155 beside it.
 *
 * Three properties this shape gets for free, and each was a bug before it:
 *
 * * **"Xem thêm" touches one lane.** It appends a page to this query and no
 *   other component re-renders its cards, because no other component shares the
 *   data. Nothing is refetched either - `useInfiniteQuery` keeps the pages it
 *   has and asks only for the next.
 * * **Any filter change resets every lane.** The filters are in the query key,
 *   so a new filter set is a different query that starts at page one. There is
 *   no per-lane offset in state or in the URL to go stale, and a response for
 *   the previous filters can never be appended to the new board - it belongs to
 *   a key nothing is observing any more.
 * * **Changing group discards the depth.** The component is keyed by group and
 *   lane and `gcTime: 0` drops the data with the last observer, so coming back
 *   to a tab loads its first page rather than restoring somebody's scroll from
 *   ten minutes ago.
 *
 * Step 1F.2.8 added bulk selection to the three lanes that are review queues.
 * The selection itself lives on the workspace, not here - one selection spans
 * the board and belongs to one step - so this component only reports which
 * cards it is drawing and passes the workspace's handlers down. What it *does*
 * own is the eligible-count query for its own step, because that number is
 * about this column and nothing else.
 */
function ContentLane({
  column,
  filters,
  count,
  assignees,
  brands,
  selected,
  selectionGate,
  onToggle,
  onTogglePage,
  onSelectAll,
  onItems,
}: {
  column: BoardColumn;
  /** Every server-side filter *except* the lane, which this component adds. */
  filters: ContentFilters;
  /** The whole lane under those filters, from the board's count request. */
  count: number;
  assignees: Map<string, string>;
  brands: Map<string, string>;
  /** Every id selected anywhere on the board. Step 1F.2.8. */
  selected: readonly string[];
  /** The step the current selection belongs to, if any. */
  selectionGate?: string;
  onToggle: (gate: string, stage: string, item: ContentSummary) => void;
  onTogglePage: (gate: string, stage: string, items: ContentSummary[]) => void;
  onSelectAll: (
    gate: string,
    stage: string,
    ids: readonly string[],
    eligibleTotal: number,
  ) => void;
  /** Report loaded titles up, for the confirmation dialog's sample. */
  onItems: (items: ContentSummary[]) => void;
}) {
  const lane = column.key;
  const rows = useInfiniteQuery({
    queryKey: ["content-lane", lane, filters],
    queryFn: ({ pageParam }) =>
      api.contentBoard({ ...filters, lane, limit: LANE_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    // The next offset is how many rows are already loaded, and the stop
    // condition is the **lane's** own total - which the server computes over
    // this lane's predicate, so it is the queue and not the page. A short page
    // stops it too: without that, a total that disagreed with the rows for any
    // reason would ask for the same empty page forever.
    getNextPageParam: (last, pages) => {
      if (last.items.length === 0) return undefined;
      const loaded = pages.reduce((sum, entry) => sum + entry.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    // See the docstring: leaving a group discards its lanes' depth.
    gcTime: 0,
  });

  const items = (rows.data?.pages ?? [])
    .flatMap((entry) => entry.items)
    // Defensive only, and it must stay that way: the server returned this lane,
    // so every card belongs here. It is a guard against a build whose column
    // table has drifted from the server's lane table, never the thing that
    // decides which cards a lane holds - that decision is `?lane=`, in SQL,
    // before the page is cut. A lane that filtered its way to the right cards
    // would be the old bug wearing a smaller hat.
    .filter((item) => columnHolds(column, item));

  useEffect(() => {
    onItems(items);
  });

  /**
   * Which approval step this column is a queue for, or `null` for the twelve
   * that are not. Three lanes get checkboxes; the rest are untouched.
   */
  const gate = laneApprovalGate(column);

  /**
   * How many items at this step this session may actually approve, and - when
   * asked for - which ones.
   *
   * Fetched with `enabled: false` and run by the "chọn tất cả ở bước này"
   * control, then read for its `total` afterwards. Two properties matter and
   * both come from the server rather than from the cards on screen:
   *
   * * the **count** is the eligible queue, which is not the lane's `count`: a
   *   lane at `INTERNAL_REVIEW` holds everything at that step, and a reviewer
   *   scoped to two channels may decide only some of it;
   * * the **ids** are frozen at the moment they arrive. From then on the batch
   *   is exactly that list, so nothing that reaches the step while somebody
   *   reads the confirmation dialog can join it.
   */
  const selectable = items.filter((item) => item.approvable_by_me);

  const eligible = useQuery({
    queryKey: ["approvable", gate?.gate, filters],
    queryFn: () => api.approvableSelection(gate?.gate ?? "", filters),
    // Asked as soon as this lane has something ticked-able in it, because the
    // control has to *say the number*: "Chọn tất cả 79 nội dung ở bước Duyệt
    // nội bộ" is checkable and "Chọn tất cả" is a promise nobody can audit. One
    // small request - ids and a count, never content - and only for somebody who
    // can already approve something here.
    enabled: Boolean(gate) && selectable.length > 0,
    gcTime: 0,
  });

  const laneSelection: LaneSelection | undefined = gate
    ? {
        // Ids from another step are not this lane's, so they are not drawn as
        // ticked here - which is what makes "one selection, one step" visible
        // rather than merely enforced.
        selected: new Set(selectionGate === gate.gate ? selected : []),
        onToggle: (item) => onToggle(gate.gate, gate.stage, item),
        onSelectPage: () => onTogglePage(gate.gate, gate.stage, selectable),
        onSelectAllAtStep: () => {
          void eligible.refetch().then((result) => {
            const data = result.data;
            if (data)
              onSelectAll(gate.gate, gate.stage, data.content_ids, data.total);
          });
        },
        eligibleTotal: eligible.data?.total,
        stepLabel: stageLabel(gate.stage),
        loadingAll: eligible.isFetching,
      }
    : undefined;

  return (
    <BoardLane
      title={columnLabel(column)}
      items={items}
      assignees={assignees}
      brands={brands}
      count={count}
      loading={rows.isPending}
      hasMore={rows.hasNextPage}
      loadingMore={rows.isFetchingNextPage}
      onLoadMore={() => void rows.fetchNextPage()}
      selection={laneSelection}
    />
  );
}
