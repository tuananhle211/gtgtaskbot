"use client";

import Link from "next/link";

import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  api,
  type AssignWorkBatch,
  type BulkValidationPreflight,
  type PeriodContainer,
  type RecurringTemplate,
  type RecurringTemplateInput,
  type ReportingPeriod,
  type WorkContribution,
  type WorkItem,
  type WorkItemDetail,
  type WorkResult,
  // Aliased: the default export of this module is already called `WorkPage`,
  // and the wire type and the screen are two different things.
  type WorkPage as WorkPageResult,
  type WorkSummary,
  type WorkType,
} from "@/lib/api";
import {
  WORK_ASSIGNMENT_MODES,
  WORK_MODES,
  WORK_PRESETS,
  WORK_SOURCE_FILTERS,
  WORK_STATUS_FILTERS,
  WORK_SUMMARY_TILES,
  contentWorkOutcomeMessage,
  formatMinutes,
  formatQuantity,
  formatRate,
  formatWhen,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { Select } from "@/components/pr";
import { ConfirmButton, ConfirmDialog } from "@/components/confirm";
import { Linkified } from "@/components/linkified";
import { KpiWorkspace } from "./kpi";
import {
  AccumulateSwitch,
  RecurringManagement,
  RecurringPreview,
  RecurringScheduleFields,
  useRecurringSchedule,
} from "./recurring";
import { PerformanceWorkspace } from "./performance";
import { WorkTypeWorkspace } from "./types";
import {
  acceptWorkConfirmation,
  adminRemoveResultConfirmation,
  bulkValidateWorkConfirmation,
  approveWorkConfirmation,
  cancelWorkConfirmation,
  completeWorkConfirmation,
  deleteTerminalWorkItemConfirmation,
  deleteLegacyWorkItemConfirmation,
  reconsiderResultConfirmation,
  rejectResultConfirmation,
  rejectWorkConfirmation,
  reopenWorkConfirmation,
  startWorkConfirmation,
} from "@/lib/confirmations";

/**
 * Công việc - the Work Ledger. M1.
 *
 * ## What this screen is for, and what it deliberately does not show
 *
 * It answers *"what valid work does this person have, and what did they get
 * credited for"* - and it shows **no score of any kind**, because none exists.
 * `COUNTED` means a piece of work was independently validated; whether it earns
 * points is M2's question and M6's arithmetic.
 *
 * ## The two ideas the layout is built around
 *
 * **Five facts, not one count.** Created, accepted, completed, approved and
 * counted are five different things about one job, and the summary strip shows
 * them as five figures rather than a single "task count" that would have to
 * pick one and hide the rest.
 *
 * **A period filter never hides outstanding work.** `Nợ việc` and `Đang làm`
 * are computed by the server without the period bounds, so choosing "Tháng
 * này" answers a question about achievement without suppressing June's unpaid
 * debt. The tiles say which is which - see `WORK_SUMMARY_TILES.live`.
 *
 * ## Where the rules live
 *
 * Not here. Every label is a `*_label` from the server, every control is drawn
 * from a `can_*` flag the server computed from the same checks the writes make,
 * and the self-validation rule is `can_validate` - a contributor never sees the
 * button, and calling the route anyway gets the same refusal. Hiding a control
 * is a courtesy; the refusal is the rule.
 *
 * ## M2 added a second view, not a second page
 *
 * `?view=kpi` is *Kế hoạch KPI* - see `./kpi.tsx`. It is a view of this screen
 * rather than a route of its own because it answers the next question about the
 * same rows: the ledger says what work was validated, and the KPI view says how
 * much of it sits inside an approved quota. Two pages would make somebody
 * navigate between two halves of one sentence.
 *
 * **Still no score.** `COUNTED` is M1's word and `ELIGIBLE` is M2's; neither is
 * a point total, and there is nowhere on either view to put one.
 */
export default function WorkPage() {
  // `useSearchParams` suspends during prerender and Next refuses to build a
  // page that reads it outside a boundary.
  return (
    <Suspense fallback={<Loading label="Đang tải công việc…" />}>
      <WorkWorkspace />
    </Suspense>
  );
}

function WorkWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();

  // Filters live in the URL, like the content board's: the back button then
  // moves the screen, and a view is shareable.
  /**
   * Post-M4. **`ALL` - "Tất cả tháng" - is the management default.**
   *
   * It used to be `TODAY`, which made the screen answer "what is happening
   * right now" while its outer scope is a reporting month. A manager opening
   * September wants September; "Hôm nay" is one click away and narrows it.
   */
  const preset = params.get("preset") || "ALL";
  /**
   * Period-container patch. **No scope in the URL means the widest scope this
   * person may see - "Toàn bộ".** The empty string is sent as *no parameter*,
   * and the server resolves it (`PrWorkQueryService.default_scope`), so a first
   * load and a bookmark without `scope` show the same list. The selector
   * displays the resolved value; choosing another scope writes it to the URL
   * and the existing behaviour takes over.
   */
  const scope = params.get("scope") || "";
  const selected = params.get("item");
  // M2. In the URL like every other filter on this screen, so the back button
  // moves between the ledger and the KPI plan and a view is shareable.
  //
  // M2.5 added `config`. An unknown value still falls back to the ledger rather
  // than rendering nothing, so a stale bookmark lands somewhere useful.
  const requested = params.get("view");
  const view: "ledger" | "kpi" | "performance" | "config" =
    requested === "kpi"
      ? "kpi"
      : requested === "performance"
        ? "performance"
        : requested === "config"
          ? "config"
          : "ledger";
  /**
   * Post-M4 consolidation. **Panels open over the ledger; they are not views.**
   *
   * `?panel=recurring` is *Quản lý việc định kỳ* - the routines that already
   * exist, with their lifecycle buttons. It is deliberately a panel and not a
   * `view`: the tabs are the module's peers - the ledger, the KPI plan, the
   * performance table, the taxonomy - and a routine is none of those. It is a
   * way of assigning work, so it opens inside the screen that assigns work.
   */
  const panel = params.get("panel") === "recurring" ? "recurring" : null;
  /**
   * M4A. Where the work came from, in the URL like every other filter here.
   *
   * The three sources answer to different people - the content workflow
   * produces one, a recurring template the second, and only the third is
   * something a person typed - so a validator sweeping a routine queue and a
   * manager auditing the projector are two jobs, not one list.
   */
  const source = params.get("source") || "";
  /**
   * Post-M4. **The outer boundary**, and the first thing the server applies.
   *
   * Defaults to the newest month the department has opened - resolved from the
   * period list rather than from this browser's clock, because which month is
   * "current" is a fact about the department's reporting calendar and not about
   * the machine the screen is open on.
   */
  const requestedPeriod = params.get("period");
  const person = params.get("person") || "";
  const workStatus = params.get("status") || "";
  const [creating, setCreating] = useState<
    "propose" | "assign" | "report" | null
  >(null);

  const move = (next: Record<string, string | null>) => {
    const search = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === null) search.delete(key);
      else search.set(key, value);
    }
    const rendered = search.toString();
    // `scroll: false`: every control on this screen writes to the URL, and the
    // App Router's default on a URL change is to scroll to the top. Opening a
    // card must leave the card where the finger is.
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, {
      scroll: false,
    });
  };

  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
  });
  const periods = useQuery({
    queryKey: ["work-periods"],
    queryFn: () => api.workPeriods(),
  });
  // The newest opened month, unless the URL names one. `workPeriods` returns
  // them newest first, so the head of the list is the department's current
  // reporting month.
  const periodId = requestedPeriod || periods.data?.[0]?.id || "";
  const period = periods.data?.find((one) => one.id === periodId);
  const capabilities = dashboard.data?.my_capabilities ?? [];
  const mayManage = capabilities.includes("PR_WORK_MANAGE");
  const mayValidate = capabilities.includes("PR_WORK_VALIDATE");
  /**
   * Seeing the whole department is its **own** capability, and deliberately not
   * implied by managing work: a Trưởng nhóm who assigned three jobs follows
   * those three, and reading every colleague's record is a different act.
   *
   * The server refuses a scope this list does not offer, so hiding the option
   * is a courtesy - see `_require_scope`. What matters is that the two agree,
   * which is why each entry below names the capability the server checks.
   */
  const mayViewAll = capabilities.includes("PR_WORK_VIEW_ALL");
  /**
   * M2. Configuring a KPI quota is `PR_WORK_CONFIGURE` - ADMIN and OWNER - and
   * **deliberately not** `PR_WORK_MANAGE`: a Trưởng nhóm who assigns work does
   * not thereby decide an arbitrary colleague's targets, and TasksBot models no
   * team that would make a narrower middle ground honest. The server refuses
   * either way; not drawing the controls is a courtesy.
   */
  const mayConfigure = capabilities.includes("PR_WORK_CONFIGURE");
  /**
   * M6. Judging somebody's month is its own capability - ADMIN and OWNER - and
   * **deliberately not** `PR_WORK_MANAGE`: assigning work to a person is not
   * the same act as deciding what their quality, timeliness and contribution
   * were worth, because the second is a judgement about a person. The server refuses either
   * way; not drawing the table is a courtesy.
   */
  const mayReview = capabilities.includes("PR_PERFORMANCE_REVIEW");
  const scopes = [
    { key: "MINE", label: "Công việc của tôi", allowed: true },
    { key: "ASSIGNED_BY_ME", label: "Tôi giao", allowed: mayManage },
    {
      key: "NEEDS_MY_DECISION",
      label: "Chờ tôi xử lý",
      // Either kind of decider: accepting a proposal and validating finished
      // work are two capabilities, and somebody may hold one without the other.
      allowed: mayManage || mayValidate,
    },
    { key: "ALL", label: "Toàn bộ", allowed: mayViewAll },
  ].filter((one) => one.allowed);
  // What the selector shows while the URL names no scope. The server makes the
  // same choice from the same capability, so the two cannot disagree.
  const effectiveScope = scope || (mayViewAll ? "ALL" : "MINE");

  const work = useQuery({
    queryKey: ["work", scope, preset, source, periodId, person, workStatus],
    queryFn: () =>
      api.listWork({
        // Omitted when the URL names none: the server picks "Toàn bộ" for
        // anybody who may see it.
        scope: scope || undefined,
        preset,
        // Post-M4. Every filter on this screen narrows **one** unified monthly
        // list: the source is metadata, not a second ledger, so content,
        // manual and recurring work arrive together and stay together.
        source_type: source || undefined,
        period_id: periodId || undefined,
        user_id: person || undefined,
        status: workStatus || undefined,
        // The day slices ask about when work *happened*, not when it is due -
        // which is what makes "Hôm nay" mean the same thing for a script
        // approved this morning and a routine that fired at 09:00.
        date_field: "EXECUTION_AT",
      }),
    enabled: Boolean(periodId) || periods.isSuccess,
  });
  // The tiles narrow with the list they sit above: "12 chờ xác nhận" over a
  // list showing three is worse than no figure at all.
  const summary = useQuery({
    queryKey: ["work-summary", scope, source, periodId, person],
    queryFn: () =>
      api.workSummary({
        scope: scope || undefined,
        // **No preset.** The tiles describe the whole reporting month; the day
        // slice below them narrows the list and deliberately not the figures,
        // and the strip says so in words. Two kinds of number in one row with
        // nothing distinguishing them is the mismatch this avoids.
        source_type: source || undefined,
        period_id: periodId || undefined,
        user_id: person || undefined,
      }),
    enabled: Boolean(periodId) || periods.isSuccess,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["work"] });
    void queryClient.invalidateQueries({ queryKey: ["work-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-item"] });
  };

  const views = [
    { key: "ledger", label: "Công việc", allowed: true },
    { key: "kpi", label: "Kế hoạch KPI", allowed: true },
    // M2.5. Owning the taxonomy is `PR_WORK_CONFIGURE` - ADMIN and OWNER - and
    // deliberately not `PR_WORK_MANAGE`: a Trưởng nhóm who assigns work picks
    // from the list, and does not decide what the list contains. The server
    // refuses either way; not drawing the tab is a courtesy.
    // M6. Everybody reaches *Hiệu suất*: an employee sees their own month there,
    // and a reviewer sees the department. Which of the two is rendered is the
    // capability's decision, not the tab's.
    { key: "performance", label: "Hiệu suất", allowed: true },
    // Post-M4 consolidation. **There is no "Định kỳ" tab.** A recurring
    // assignment *is* an assignment, so it is set up in *Giao công việc* by
    // answering *Hình thức: Định kỳ*, and the routines that exist are managed
    // from a panel on the same screen - see `?panel=recurring`. A manager
    // deciding what somebody should do this month should never first have to
    // decide which module owns the answer.
    //
    // "Định kỳ" survives in three places, all of them still here: the source
    // filter over the ledger, the badge on a generated row, and the mode inside
    // the assignment form. What it is no longer is a peer module.
    { key: "config", label: "Cấu hình", allowed: mayConfigure },
  ].filter((one) => one.allowed);

  /**
   * Part V. **The old bookmark still lands somewhere useful.**
   *
   * `?view=recurring` was the tab until this patch, and people have it saved.
   * Rewritten in place to `?panel=recurring`, which is the same content in its
   * new home, rather than left to fall through to the ledger - a bookmark that
   * silently shows a different screen is the failure this avoids, and a blank
   * page is worse. `router.replace`, so Back does not bounce between the two.
   */
  useEffect(() => {
    if (requested !== "recurring") return;
    move({ view: null, panel: "recurring" });
    // Keyed on `requested` alone. `move` is rebuilt every render and reads the
    // current params when it is called, so listing it would only make this run
    // on every render to reach the same no-op.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requested]);

  return (
    <div className="space-y-4">
      <nav aria-label="Chế độ xem" className="flex flex-wrap gap-1.5">
        {views.map((one) => (
          <button
            key={one.key}
            type="button"
            aria-current={view === one.key ? "page" : undefined}
            onClick={() =>
              move({ view: one.key === "ledger" ? null : one.key, item: null })
            }
            className={`min-h-11 rounded px-3 text-sm ${
              view === one.key
                ? "bg-[var(--text)] text-[var(--surface)]"
                : "border border-[var(--border)]"
            }`}
          >
            {one.label}
          </button>
        ))}
      </nav>

      {view === "config" && mayConfigure ? <WorkTypeWorkspace /> : null}

      {view === "performance" ? (
        <PerformanceWorkspace
          mayReview={mayReview}
          mayConfigure={mayConfigure}
        />
      ) : null}

      {view === "kpi" ? (
        <KpiWorkspace mayConfigure={mayConfigure} mayViewAll={mayViewAll} />
      ) : view === "config" || view === "performance" ? null : (
        <LedgerWorkspace
          scope={effectiveScope}
          scopes={scopes}
          preset={preset}
          source={source}
          periodId={periodId}
          periods={periods.data ?? []}
          period={period}
          person={person}
          workStatus={workStatus}
          mayViewAll={mayViewAll}
          mayValidate={mayValidate}
          selected={selected}
          mayManage={mayManage}
          mayConfigure={mayConfigure}
          panel={panel}
          creating={creating}
          setCreating={setCreating}
          move={move}
          work={work}
          summary={summary}
          refresh={refresh}
        />
      )}
    </div>
  );
}

/**
 * The unified monthly work ledger: the list, the summary strip and the detail
 * panel.
 *
 * **One list, three sources.** Content, manual and recurring work appear
 * together, ordered by the day they happened, and the source is a badge and a
 * filter rather than a separate ledger. A department's September is one thing;
 * splitting it by where each row came from would make "what did this person do
 * last month" a question nobody could answer on one screen.
 *
 * The outer scope is a **reporting month**, and every other control narrows it.
 */
function LedgerWorkspace({
  scope,
  scopes,
  preset,
  source,
  periodId,
  periods,
  period,
  person,
  workStatus,
  mayViewAll,
  mayValidate,
  selected,
  mayManage,
  mayConfigure,
  panel,
  creating,
  setCreating,
  move,
  work,
  summary,
  refresh,
}: {
  scope: string;
  scopes: Array<{ key: string; label: string }>;
  preset: string;
  /** M4A. `""` is every source. See `WORK_SOURCE_FILTERS`. */
  source: string;
  /** Post-M4. The reporting month the whole screen is scoped to. */
  periodId: string;
  periods: ReportingPeriod[];
  period: ReportingPeriod | undefined;
  /** Post-M4. `""` is everybody the caller's scope already allows. */
  person: string;
  /** Post-M4. `""` is every lifecycle status. Independent of source and date. */
  workStatus: string;
  /** Whether the person filter may offer colleagues at all. */
  mayViewAll: boolean;
  /** M4A. Decides whether the bulk-validation panel is offered at all. */
  mayValidate: boolean;
  selected: string | null;
  mayManage: boolean;
  /** M2.5. Decides which empty-taxonomy message the create form shows. */
  mayConfigure: boolean;
  /** Post-M4 consolidation. `"recurring"` opens *Quản lý việc định kỳ*. */
  panel: "recurring" | null;
  creating: "propose" | "assign" | "report" | null;
  setCreating: (next: "propose" | "assign" | "report" | null) => void;
  move: (next: Record<string, string | null>) => void;
  work: UseQueryResult<WorkPageResult>;
  summary: UseQueryResult<WorkSummary>;
  refresh: () => void;
}) {
  const [validating, setValidating] = useState(false);
  const queryClient = useQueryClient();
  /**
   * Work maintenance. The one sentence the list says after a legacy content
   * work item is deleted. The row's detail is gone with the row, so the
   * message lives here, above the list, rather than inside a panel that no
   * longer exists.
   */
  const [notice, setNotice] = useState<string | null>(null);
  const onDeleted = (deletedId: string, message: string) => {
    // The deleted row's own cache entry is dropped rather than refetched - a
    // refetch would 404 - and the list, the tiles and every other detail are
    // invalidated as after any other change. No Content sync, no rebuild and
    // no projection is asked for: recording the content again is a separate
    // act the administrator takes on the maintenance screen. The sentence is
    // the caller's, because a legacy row and a cancelled row went for
    // different reasons and the list should say which.
    queryClient.removeQueries({ queryKey: ["work-item", deletedId] });
    refresh();
    setNotice(message);
    if (selected === deletedId) move({ item: null });
  };
  // Only fetched when the person filter is actually offered - an employee who
  // cannot see colleagues' work has no use for a staff list.
  const people = useQuery({
    queryKey: ["people"],
    queryFn: api.people,
    enabled: mayViewAll,
  });
  // What a validator could actually act on: finished work waiting for somebody.
  // Computed here rather than in the panel so the toolbar can say how many
  // there are before anybody opens it.
  const waiting = (work.data?.items ?? []).filter(
    (one) => one.status === "COMPLETED",
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {/*
          Post-M4. The reporting month leads the toolbar because it is the outer
          boundary of every query underneath it - the screen is "Công việc tháng
          09/2026", and the controls after it are all narrowings of that.
        */}
        <Select
          value={periodId}
          onChange={(event) => move({ period: event.target.value, item: null })}
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          aria-label="Kỳ báo cáo"
        >
          {periods.map((one) => (
            <option key={one.id} value={one.id}>
              {one.code}
            </option>
          ))}
        </Select>
        {scopes.length > 1 ? (
          <Select
            value={scope}
            onChange={(event) =>
              move({ scope: event.target.value, item: null })
            }
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            aria-label="Phạm vi"
          >
            {scopes.map((one) => (
              <option key={one.key} value={one.key}>
                {one.label}
              </option>
            ))}
          </Select>
        ) : null}
        {/*
          Period-container patch. **The generic report form.** Everybody who may
          file work may report a result into their own monthly stream - "+3
          khách hàng", "+520 bình luận" - and a manager into anybody's. No KPI
          is asked about before the button works: the target is compared
          afterwards, never consulted first.
        */}
        <button
          type="button"
          onClick={() => setCreating(creating === "report" ? null : "report")}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)]"
        >
          {creating === "report" ? "Đóng" : "Báo cáo kết quả"}
        </button>
        <button
          type="button"
          onClick={() => setCreating(creating === "propose" ? null : "propose")}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          {creating === "propose" ? "Đóng" : "Đề xuất công việc"}
        </button>
        {mayManage ? (
          <button
            type="button"
            onClick={() => setCreating(creating === "assign" ? null : "assign")}
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            {creating === "assign" ? "Đóng" : "Giao công việc"}
          </button>
        ) : null}
        {/*
          Post-M4 consolidation, Part K. The routines that already exist, beside
          the button that creates them - because they are the same subject.
          `PR_WORK_MANAGE`, the same capability as assigning one job by hand and
          deliberately not `PR_WORK_CONFIGURE`: a recurring assignment *is* an
          assignment, repeated, while owning the taxonomy is the different act
          that lives under Cấu hình. In the URL, so the panel is shareable and
          so the old `?view=recurring` bookmark has somewhere to land.
        */}
        {mayManage ? (
          <button
            type="button"
            aria-current={panel === "recurring" ? "true" : undefined}
            onClick={() =>
              move({ panel: panel === "recurring" ? null : "recurring" })
            }
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            {panel === "recurring" ? "Đóng" : "Quản lý việc định kỳ"}
          </button>
        ) : null}
        {/*
          M4A. Opt-in, like the two create forms beside it. A checkbox column
          drawn permanently over the ledger would invite exactly the
          unconsidered sweep this feature is shaped to keep deliberate - and it
          would repeat every waiting title above the list that already shows
          them.
        */}
        {mayValidate && waiting.length > 0 ? (
          <button
            type="button"
            onClick={() => setValidating((current) => !current)}
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            {validating ? "Đóng" : `Xác nhận hàng loạt (${waiting.length})`}
          </button>
        ) : null}
      </div>

      {creating === "report" ? (
        <ReportResultForm
          mayManage={mayManage}
          periodId={periodId}
          onDone={(workItemId) => {
            setCreating(null);
            refresh();
            move({ item: workItemId });
          }}
        />
      ) : null}
      {creating && creating !== "report" ? (
        <WorkForm
          mode={creating}
          mayConfigure={mayConfigure}
          mayManage={mayManage}
          onDone={(created) => {
            setCreating(null);
            refresh();
            // Part Y. A new routine is a **draft** that generates nothing until
            // somebody activates it, so the form hands off to the panel that
            // carries *Bật chạy* rather than closing onto a ledger where
            // nothing has appeared and nothing is going to.
            if (created === "RECURRING") move({ panel: "recurring" });
          }}
        />
      ) : null}

      {/*
        Guarded on the capability as well as on the URL, so a bookmarked
        `?panel=recurring` from somebody who has since lost `PR_WORK_MANAGE`
        shows the ledger rather than a panel whose every call will 403.
      */}
      {panel === "recurring" && mayManage ? <RecurringManagement /> : null}

      {summary.data ? (
        <SummaryStrip summary={summary.data} periodCode={period?.code} />
      ) : null}

      {/*
        M4A. Where the work came from. A filter over what this scope already
        allows - it reveals nothing new, which is why it sits beside the period
        presets rather than beside the scope selector.
      */}
      <nav aria-label="Nguồn công việc" className="flex flex-wrap gap-1.5">
        {WORK_SOURCE_FILTERS.map((one) => (
          <button
            key={one.key || "ALL"}
            type="button"
            aria-current={source === one.key ? "page" : undefined}
            onClick={() => move({ source: one.key || null, item: null })}
            className={`min-h-11 rounded px-3 text-sm ${
              source === one.key
                ? "bg-[var(--text)] text-[var(--surface)]"
                : "border border-[var(--border)]"
            }`}
          >
            {one.label}
          </button>
        ))}
      </nav>

      {/*
        Post-M4. Two filters that are deliberately **not** the source strip and
        not the day strip: who did it, and what state it is in. Three separate
        questions, three separate controls - one combined control would make
        each of them unaskable on its own.
      */}
      <div className="flex flex-wrap items-center gap-2">
        {mayViewAll ? (
          <Select
            value={person}
            onChange={(event) =>
              move({ person: event.target.value || null, item: null })
            }
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            aria-label="Người thực hiện"
          >
            <option value="">Tất cả nhân sự</option>
            {people.data?.map((one) => (
              <option key={one.user_id} value={one.user_id}>
                {one.full_name}
              </option>
            ))}
          </Select>
        ) : null}
        <Select
          value={workStatus}
          onChange={(event) =>
            move({ status: event.target.value || null, item: null })
          }
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          aria-label="Trạng thái"
        >
          {WORK_STATUS_FILTERS.map((one) => (
            <option key={one.key} value={one.key}>
              {one.label}
            </option>
          ))}
        </Select>
      </div>

      <nav aria-label="Khoảng thời gian" className="flex flex-wrap gap-1.5">
        {WORK_PRESETS.filter((one) => one.key !== "CUSTOM").map((one) => (
          <button
            key={one.key}
            type="button"
            aria-current={preset === one.key ? "page" : undefined}
            onClick={() => move({ preset: one.key, item: null })}
            className={`min-h-11 rounded px-3 text-sm ${
              preset === one.key
                ? "bg-[var(--text)] text-[var(--surface)]"
                : "border border-[var(--border)]"
            }`}
          >
            {one.label}
          </button>
        ))}
      </nav>

      {validating && mayValidate && waiting.length > 0 ? (
        <BulkValidationPanel
          items={waiting}
          onDone={() => {
            setValidating(false);
            refresh();
          }}
        />
      ) : null}

      {/*
        Inline UX patch. **The detail opens under the card that was tapped.**

        This used to be a two-column grid - list on the left, one detail panel
        on the right - which below the `lg` breakpoint stacked into "the whole
        list, then the detail": on a phone the selected card and its detail
        were several screens apart, and every tap was followed by a scroll to
        the bottom to find out what had opened. Now each row owns its own
        detail: one open card at a time, the URL's `item` still says which, and
        nothing on this page moves the viewport.
      */}
      <div className="max-w-3xl">
        <div className="space-y-2">
          {notice ? (
            <p
              role="status"
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-emerald-500/40 bg-emerald-500/10 p-2 text-xs"
            >
              <span>{notice}</span>
              <button
                type="button"
                onClick={() => setNotice(null)}
                className="min-h-8 rounded px-2 text-[11px] text-[var(--text-muted)]"
              >
                Đóng
              </button>
            </p>
          ) : null}
          {work.isPending ? <Loading label="Đang tải công việc…" /> : null}
          {work.isError ? (
            <ErrorBox error={work.error} onRetry={() => work.refetch()} />
          ) : null}
          {/*
            Cancelled-work patch. **A deep link still opens its row.** The
            default list leaves cancelled work out server-side, so
            `?item=<cancelled-id>` - a link somebody was sent, or a bookmark
            from before the row was cancelled - names a row no card on this
            page owns. Rather than a blank page, the row is drawn on its own
            above the list, marked as outside the current filter, with the way
            to the view that does list it. Nothing about the filter is changed
            behind the person's back.
          */}
          {selected &&
          work.isSuccess &&
          !work.data.items.some((one) => one.id === selected) ? (
            <SelectedOutsideFilter
              workItemId={selected}
              workStatus={workStatus}
              move={move}
              onChanged={refresh}
              onDeleted={onDeleted}
              mayConfigure={mayConfigure}
            />
          ) : null}
          {work.data?.items.length === 0 ? (
            <Empty
              message={
                preset === "OVERDUE"
                  ? "Không có việc nào quá hạn."
                  : workStatus === "CANCELLED"
                    ? "Không có công việc đã hủy nào ở mục này."
                    : "Chưa có công việc nào ở mục này."
              }
            />
          ) : null}
          {/*
            Post-M4. **Two sections, and the second is never hidden.**

            The server orders execution-dated work first and leaves the rest as
            the tail, so the split is a heading over a boundary the query
            already produced rather than a re-sort here. Undated work - a
            manually assigned job with a deadline and no execution date - is
            real work somebody still has to do; dropping it, or giving it a
            `created_at` to make it sortable, is the invention this whole change
            exists to remove.
          */}
          <ul className="space-y-2">
            {(work.data?.items ?? [])
              .filter((one) => one.execution_at)
              .map((item) => (
                <WorkRow
                  key={item.id}
                  item={item}
                  expanded={selected === item.id}
                  onToggle={() =>
                    move({ item: selected === item.id ? null : item.id })
                  }
                  onChanged={refresh}
                  onDeleted={onDeleted}
                  mayConfigure={mayConfigure}
                />
              ))}
          </ul>
          {(work.data?.items ?? []).some((one) => !one.execution_at) ? (
            <p className="pt-2 text-xs font-semibold text-[var(--text-muted)]">
              Chưa có ngày thực hiện
            </p>
          ) : null}
          <ul className="space-y-2">
            {(work.data?.items ?? [])
              .filter((one) => !one.execution_at)
              .map((item) => (
                <WorkRow
                  key={item.id}
                  item={item}
                  expanded={selected === item.id}
                  onToggle={() =>
                    move({ item: selected === item.id ? null : item.id })
                  }
                  onChanged={refresh}
                  onDeleted={onDeleted}
                  mayConfigure={mayConfigure}
                />
              ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/**
 * Validating a queue of finished work in one act. **M4A.**
 *
 * ## Why this screen has a preflight
 *
 * The batch is all-or-nothing, which is the right promise and a hostile one to
 * debug: a validator who ticks forty rows and is told "4 công việc không hợp
 * lệ" has no way to find out which four except by bisection. So the panel asks
 * the server what the batch *would* do before opening the dialog, and lists the
 * blocked rows with the server's own sentence for each.
 *
 * ## What this component does not decide
 *
 * **Nothing.** It does not know the self-validation rule, does not compare the
 * actor to a contributor list, and does not filter its own selection. Every
 * reason shown is `reason_label` as the server wrote it, and pressing the
 * button anyway gets the same refusal - hiding a row is a courtesy, the refusal
 * is the rule.
 */
function BulkValidationPanel({
  items,
  onDone,
}: {
  items: WorkItem[];
  onDone: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [preflight, setPreflight] = useState<BulkValidationPreflight | null>(
    null,
  );

  const check = useMutation({
    mutationFn: () => api.bulkValidationPreflight(picked),
    onSuccess: setPreflight,
  });
  const confirm = useMutation({
    mutationFn: () => api.bulkValidate(picked),
    onSuccess: () => {
      setPicked([]);
      setPreflight(null);
      onDone();
    },
  });

  const toggle = (id: string) =>
    setPicked((current) => {
      // Any change invalidates the advice: a preflight describes a moment, and
      // showing yesterday's answer over today's selection is worse than none.
      setPreflight(null);
      return current.includes(id)
        ? current.filter((one) => one !== id)
        : [...current, id];
    });

  return (
    <section
      aria-label="Xác nhận hàng loạt"
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3"
    >
      <h3 className="text-sm font-semibold">Xác nhận hàng loạt</h3>
      <p className="text-xs text-[var(--text-muted)]">
        Chọn các công việc đã hoàn thành để xác nhận cùng lúc. Bạn không thể xác
        nhận công việc mình có tham gia.
      </p>

      <ul className="max-h-48 space-y-1 overflow-y-auto">
        {items.map((one) => (
          <li key={one.id}>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={picked.includes(one.id)}
                onChange={() => toggle(one.id)}
                aria-label={`Chọn ${one.code}`}
              />
              <code className="text-[10px] text-[var(--text-muted)]">
                {one.code}
              </code>
              <span>{one.title}</span>
            </label>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={picked.length === 0 || check.isPending}
          onClick={() => check.mutate()}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm disabled:opacity-50"
        >
          {check.isPending
            ? "Đang kiểm tra…"
            : `Kiểm tra ${picked.length} công việc`}
        </button>
        {preflight ? (
          <ConfirmButton
            spec={bulkValidateWorkConfirmation(preflight.validatable_count)}
            disabled={
              preflight.validatable_count === 0 || preflight.blocked_count > 0
            }
            pending={confirm.isPending}
            error={confirm.error}
            onConfirm={() => confirm.mutate()}
            className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
          >
            {confirm.isPending
              ? "Đang xác nhận…"
              : `Xác nhận ${preflight.validatable_count} công việc`}
          </ConfirmButton>
        ) : null}
      </div>

      {preflight && preflight.blocked_count > 0 ? (
        <div className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2">
          <p className="text-xs font-medium">
            {preflight.blocked_count} công việc không thể xác nhận. Hãy bỏ chọn
            trước khi tiếp tục.
          </p>
          <ul className="mt-1 space-y-0.5">
            {preflight.candidates
              .filter((one) => one.reason !== null)
              .map((one) => (
                <li
                  key={one.work_item_id}
                  className="text-[11px] text-[var(--text-muted)]"
                >
                  <code>{one.code}</code> · {one.reason_label}
                </li>
              ))}
          </ul>
        </div>
      ) : null}

      {check.isError ? <ErrorBox error={check.error} /> : null}
    </section>
  );
}

/**
 * The five figures, with the period ones and the live ones marked apart.
 *
 * `live` is the whole reason this strip is worth reading: two of these describe
 * a chosen period and three describe right now, and a person who could not tell
 * them apart would read "Nợ việc: 4" as "four this month" - which is exactly
 * the misreading that makes carried-over work invisible.
 *
 * **No score tile, and there is nowhere to put one.**
 */
function SummaryStrip({
  summary,
  periodCode,
}: {
  summary: WorkSummary;
  periodCode: string | undefined;
}) {
  return (
    <section aria-label="Tổng hợp công việc" className="space-y-2">
      {/*
        Post-M4. **The tiles say which scope they are.**

        They count the whole reporting month and do not move when the day strip
        below narrows the list - that is deliberate, because a month figure is
        the one a KPI conversation is about. What is not acceptable is leaving a
        reader to discover the mismatch, so the scope is written above them.
      */}
      <p className="text-xs text-[var(--text-muted)]">
        {periodCode ? `Tính cho cả kỳ ${periodCode}` : "Tính cho cả kỳ báo cáo"}{" "}
        · không đổi theo bộ lọc ngày bên dưới
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {WORK_SUMMARY_TILES.map((tile) => (
          <div
            key={tile.key}
            title={tile.hint}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3"
          >
            <p className="text-xs text-[var(--text-muted)]">
              {tile.label}
              {tile.live ? (
                <span
                  className="ml-1 text-[10px]"
                  title="Không phụ thuộc kỳ đã chọn"
                >
                  · hiện tại
                </span>
              ) : null}
            </p>
            <p className="text-lg font-semibold">{summary[tile.key]}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

/** How loudly to draw a status. Green only for work that was actually validated. */
function statusTone(status: string): "good" | "warn" | "neutral" {
  if (status === "APPROVED") return "good";
  if (status === "PROPOSED" || status === "COMPLETED") return "warn";
  return "neutral";
}

/**
 * **Who answers for this job**, as the card names them. One name, own row.
 *
 * Read from what the ledger already records and nothing invented on top:
 *
 * * a **period container** belongs to exactly one person - its subject, who is
 *   also its single `PRIMARY` contribution;
 * * a **one-off job** - manual, content-derived or recurring - has a `PRIMARY`
 *   contributor (the projector and the assigner both write exactly one) and
 *   may have `CONTRIBUTOR` and `SUPPORT` rows beside it. The primary is the
 *   responsible person; the rest are counted, not listed, on the card. The
 *   expanded detail still lists everybody with their own credit status.
 *
 * `null` means the server resolved nobody - a proposal nobody has taken on,
 * or a row whose contributor could not be named - and the card says so in
 * words rather than printing a dash.
 */
function responsibleOf(item: WorkItem): {
  name: string | null;
  others: number;
} {
  const named = (row: WorkContribution | undefined) =>
    row?.user_name?.trim() || null;
  if (item.is_period_container) {
    const subject = item.contributors.find(
      (row) => row.user_id === item.subject_user_id,
    );
    return {
      name:
        named(subject) ?? item.period_container?.subject_name?.trim() ?? null,
      others: Math.max(0, item.contributors.length - (subject ? 1 : 0)),
    };
  }
  const primary =
    item.contributors.find((row) => row.contribution_role === "PRIMARY") ??
    item.contributors[0];
  return {
    name: named(primary),
    others: primary ? item.contributors.length - 1 : 0,
  };
}

/** Where the job came from, by name: the piece or the routine. `null` for manual work. */
function sourceNameOf(item: WorkItem): string | null {
  if (item.is_source_derived && item.content_code) return item.content_code;
  if (item.recurring_template_name) return item.recurring_template_name;
  return null;
}

/**
 * One job, as a row.
 *
 * Shows the title, the type, the status, the deadline, the quantity, the
 * contributors, the source and the priority - and the overdue flag comes from
 * the server (`is_overdue`) rather than from comparing a date here, so the list
 * and the summary can never disagree about what is late.
 */
/**
 * One list row: the card, and - when this is the open one - its detail
 * directly beneath it.
 *
 * The card is a `<button>` and the detail is its sibling, not its child, so
 * the buttons, links and inputs inside the detail are never nested inside the
 * toggle and cannot bubble a click into it. `aria-controls` names the detail
 * region, which exists in every state - loading, failed, loaded - so the
 * reference never dangles.
 */
/**
 * The row a `?item=` names when no card on the current page owns it.
 *
 * Reads the detail the same way an expanded card does - same query key, so an
 * action taken here and the cached detail agree - and draws the ordinary
 * card above the ordinary detail, so nothing about the row looks different
 * for having been reached by link. The caption is the only addition: the
 * row is outside the filter, and a cancelled one has a view that lists it.
 * A row that no longer exists says so and offers only to close.
 */
function SelectedOutsideFilter({
  workItemId,
  workStatus,
  move,
  onChanged,
  onDeleted,
  mayConfigure,
}: {
  workItemId: string;
  workStatus: string;
  move: (next: Record<string, string | null>) => void;
  onChanged: () => void;
  onDeleted: (deletedId: string, message: string) => void;
  mayConfigure: boolean;
}) {
  const detail = useQuery({
    queryKey: ["work-item", workItemId],
    queryFn: () => api.workItem(workItemId),
  });
  const close = (
    <button
      type="button"
      onClick={() => move({ item: null })}
      className="min-h-8 rounded border border-[var(--border)] px-2 text-[11px]"
    >
      Đóng
    </button>
  );
  if (detail.isPending) return <Loading label="Đang mở công việc…" />;
  if (detail.isError) {
    return (
      <section aria-label="Công việc đang mở ngoài bộ lọc" className="space-y-1">
        <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />
        <p className="flex justify-end">{close}</p>
      </section>
    );
  }
  const item = detail.data.item;
  return (
    <section aria-label="Công việc đang mở ngoài bộ lọc" className="space-y-1">
      <p className="flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs">
        <span>
          Công việc <code>{item.code}</code> không nằm trong bộ lọc hiện tại
          {item.status === "CANCELLED" ? " vì đã hủy." : "."}
        </span>
        <span className="flex flex-wrap gap-1">
          {item.status === "CANCELLED" && workStatus !== "CANCELLED" ? (
            <button
              type="button"
              onClick={() => move({ status: "CANCELLED" })}
              className="min-h-8 rounded border border-[var(--border)] px-2 text-[11px]"
            >
              Mở mục Đã hủy
            </button>
          ) : null}
          {close}
        </span>
      </p>
      <ul className="space-y-2">
        <WorkRow
          item={item}
          expanded
          onToggle={() => move({ item: null })}
          onChanged={onChanged}
          onDeleted={onDeleted}
          mayConfigure={mayConfigure}
        />
      </ul>
    </section>
  );
}

function WorkRow({
  item,
  expanded,
  onToggle,
  onChanged,
  onDeleted,
  mayConfigure,
}: {
  item: WorkItem;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
  /** Work maintenance. The row was deleted; the list owns what happens next. */
  onDeleted: (deletedId: string, message: string) => void;
  /** Work maintenance. Draws the administrative removal on results. Server-enforced. */
  mayConfigure: boolean;
}) {
  return (
    <li>
      <WorkCard
        item={item}
        expanded={expanded}
        onToggle={onToggle}
        mayConfigure={mayConfigure}
      />
      {expanded ? (
        <div id={`work-detail-${item.id}`} className="mt-1">
          <WorkDetail
            workItemId={item.id}
            onChanged={onChanged}
            onDeleted={onDeleted}
            mayConfigure={mayConfigure}
          />
        </div>
      ) : null}
    </li>
  );
}

function WorkCard({
  item,
  expanded,
  onToggle,
  mayConfigure = false,
}: {
  item: WorkItem;
  expanded: boolean;
  onToggle: () => void;
  /**
   * Work maintenance. Draws the quiet *"Dữ liệu cũ từ Nội dung"* marker on a
   * legacy content row for the people who could act on it. The flag itself is
   * the server's; this only decides whether to mention it on a collapsed card.
   */
  mayConfigure?: boolean;
}) {
  const quantity = formatQuantity(item.quantity, item.unit_label);
  const responsible = responsibleOf(item);
  const sourceName = sourceNameOf(item);
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={`work-detail-${item.id}`}
      className={`w-full rounded-lg border p-3 text-left ${
        expanded
          ? "border-[var(--text-muted)] bg-[var(--surface)]"
          : "border-[var(--border)] bg-[var(--surface)]"
      }`}
    >
      <code className="text-[10px] text-[var(--text-muted)]">{item.code}</code>
      <p className="text-sm font-medium break-words">{item.title}</p>
      {/*
        **The responsible person, on a row of their own, right under the title.**

        Owner-visibility patch. The name used to sit three items into the muted
        metadata line - "Không có hạn · Trần Minh Anh · Nguồn: CNT-…" - which on
        a phone is one long grey string and on a long list is the one thing a
        manager is scanning for. It is the second thing a card says now, at
        body size and weight, and the metadata line no longer repeats it.

        Who the person *is* comes from the ledger - see `responsibleOf` - and
        the card names one. Everybody else is a count; the detail lists them.
      */}
      <p
        className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-sm"
        data-testid="work-owner"
      >
        {responsible.name ? (
          <span className="font-semibold break-words">{responsible.name}</span>
        ) : (
          <span className="text-[var(--text-muted)]">
            Chưa có người phụ trách
          </span>
        )}
        {responsible.others > 0 ? (
          <span className="text-xs text-[var(--text-muted)]">
            +{responsible.others} người tham gia
          </span>
        ) : null}
      </p>
      <p className="mt-1.5 flex flex-wrap items-center gap-1">
        <Pill tone={statusTone(item.status)}>{item.status_label}</Pill>
        {/*
          Post-M4. The source badge, in the shared Vietnamese wording - "Nội
          dung", "Thủ công", "Định kỳ". A raw `CONTENT` was never shown, and now
          the badge is on every row rather than only in the trailing prose,
          because a unified list is exactly where somebody needs to tell three
          sources apart at a glance.
        */}
        <Pill>{item.source_label}</Pill>
        {item.work_type_name ? <Pill>{item.work_type_name}</Pill> : null}
        {item.priority !== "NORMAL" ? (
          <Pill tone="warn">{item.priority_label}</Pill>
        ) : null}
        {item.is_overdue ? <Pill tone="warn">Quá hạn</Pill> : null}
        {item.is_period_container ? (
          <Pill tone="good">
            Theo kỳ {item.period_container?.period_code ?? ""}
          </Pill>
        ) : quantity ? (
          <Pill>{quantity}</Pill>
        ) : null}
        {/*
          Work maintenance. A legacy content row - the pre-period-container
          shape - is marked for the people who may clean it up, and no louder
          than the status beside it: the same neutral pill, muted text. The
          delete lives in the expanded detail, never on a collapsed card.
        */}
        {mayConfigure && item.is_legacy_content_work ? (
          <Pill>Dữ liệu cũ từ Nội dung</Pill>
        ) : null}
      </p>
      {item.period_container ? (
        <ContainerFigures container={item.period_container} />
      ) : null}
      {/*
        **When this job sits in the person's day, given its own line.** It is
        what somebody plans around, and it is what the monthly list is ordered
        by - a deadline three lines into a paragraph is not.

        Post-M4 consolidation, Part Q: **the heading follows the source,
        because the two sources mean different things by "when".**

        *Thực hiện* is `execution_at` - the content milestone, or the recurring
        occurrence's `scheduled_for`. It is a statement about when the work is
        performed.

        *Bắt đầu* is `accepted_at` - the instant a manager pressed *Giao công
        việc*, which is when the job entered this person's workload. Manual work
        has no execution date and never will, so labelling that instant *Thực
        hiện* would claim the work was done the moment it was handed over.
        Falling back to `due_at` for either would put a deadline under a heading
        that says the work happened.
      */}
      {item.is_period_container ? null : item.execution_at ? (
        <p className="mt-1 text-xs font-medium">
          Thực hiện {formatWhen(item.execution_at)}
        </p>
      ) : item.accepted_at ? (
        <p className="mt-1 text-xs font-medium">
          Bắt đầu {formatWhen(item.accepted_at)}
        </p>
      ) : null}
      {/*
        The trailing metadata: the deadline and, for source-derived work, the
        piece or routine by name - "Nguồn: CNT-2026-000042" is a job somebody
        remembers doing, and "Từ quy trình nội dung" alone is not. The kind of
        source is the badge above, so it is not repeated here; the responsible
        person has their own row above, so they are not repeated here either.
      */}
      {item.is_period_container && !sourceName ? null : (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {item.is_period_container
            ? null
            : item.due_at
              ? `Hạn ${formatWhen(item.due_at)}`
              : "Không có hạn"}
          {!item.is_period_container && sourceName ? " · " : null}
          {sourceName ? `Nguồn: ${sourceName}` : null}
        </p>
      )}
      {/* The affordance. Compact, and it says which way the card will go. */}
      <p
        className="mt-1 text-right text-[11px] text-[var(--text-muted)]"
        aria-hidden="true"
      >
        {expanded ? "Thu gọn ▴" : "Xem chi tiết ▾"}
      </p>
    </button>
  );
}

/**
 * Period-container patch. **Actual against target, as the server said it.**
 *
 * "27 / 20 khách hàng · 135% · +7 vượt chỉ tiêu · 10.260 điểm". Every number
 * here is a response field; the bar is the one thing drawn as a length, and it
 * is the server's capped `progress_percent`, so the text can read 135% while
 * the bar stops at full. Without a KPI the line reads "3 buổi · KPI —".
 */
function ContainerFigures({ container }: { container: PeriodContainer }) {
  const points =
    container.standard_minutes !== null
      ? `${formatMinutes(container.standard_minutes)} điểm`
      : container.scoring_status_label;
  return (
    <div className="mt-1 space-y-1">
      <p className="text-xs">
        <span className="font-medium">Thực tế {container.actual_label}</span>
        {container.has_target ? (
          <>
            {" · "}
            <span className="font-medium">
              {container.completion_percent
                ? `${formatRate(container.completion_percent)}%`
                : ""}
            </span>
            {container.over_target_quantity !== "0" &&
            container.over_target_quantity !== "0.00"
              ? ` · +${formatRate(container.over_target_quantity)} vượt chỉ tiêu`
              : ""}
          </>
        ) : (
          " · KPI —"
        )}
        {" · "}
        {points}
      </p>
      {container.has_target ? (
        <div
          className="h-1.5 w-full overflow-hidden rounded bg-[var(--border)]"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Number(container.progress_percent)}
          aria-label="Tiến độ so với KPI"
        >
          <div
            className={`h-full ${container.is_target_met ? "bg-emerald-500" : "bg-[var(--text)]"}`}
            style={{ width: `${container.progress_percent}%` }}
          />
        </div>
      ) : null}
      {container.pending_count > 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          {formatRate(container.pending_quantity)} {container.unit_label} chờ
          xác nhận
        </p>
      ) : null}
    </div>
  );
}

/**
 * Period-container patch. **The generic report form: quantity, label, link.**
 *
 * Names a work type - and, for a manager, a person - and reports into the
 * month's stream for the pair, which the server opens on first use. There is
 * no target on this form and no check against one: reporting the twenty-first
 * customer is the same request as reporting the first, and reporting work with
 * no KPI at all is allowed.
 */
function ReportResultForm({
  mayManage,
  periodId,
  onDone,
}: {
  mayManage: boolean;
  periodId: string;
  onDone: (workItemId: string) => void;
}) {
  const types = useQuery({
    queryKey: ["work-types"],
    queryFn: () => api.workTypes(),
  });
  const people = useQuery({
    queryKey: ["people"],
    queryFn: api.people,
    enabled: mayManage,
  });
  const [workTypeId, setWorkTypeId] = useState("");
  const [subject, setSubject] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [label, setLabel] = useState("");
  const [link, setLink] = useState("");
  const chosen: WorkType | undefined = types.data?.find(
    (one) => one.id === workTypeId,
  );

  const submit = useMutation({
    mutationFn: () =>
      api.reportWorkResult({
        work_type_id: workTypeId,
        subject_user_id: subject || null,
        period_id: periodId || null,
        quantity: quantity.trim() || null,
        label: label.trim() || null,
        link: link.trim() || null,
      }),
    onSuccess: (detail) => onDone(detail.item.id),
  });
  const ready = Boolean(workTypeId) && Number(quantity) > 0;

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <h3 className="text-sm font-semibold">Báo cáo kết quả</h3>
      <p className="text-xs text-[var(--text-muted)]">
        Kết quả được cộng dồn vào công việc định kỳ của tháng. KPI chỉ để so
        sánh: báo cáo vượt chỉ tiêu hay chưa có KPI đều được ghi nhận. Kết quả
        sẽ được tính sau khi một người khác xác nhận.
      </p>
      <Select
        value={workTypeId}
        onChange={(event) => setWorkTypeId(event.target.value)}
        aria-label="Loại công việc"
        className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
      >
        <option value="">Chọn loại công việc</option>
        {types.data?.map((one) => (
          <option key={one.id} value={one.id}>
            {one.category_label} · {one.name}
          </option>
        ))}
      </Select>
      {mayManage ? (
        <Select
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          aria-label="Người thực hiện"
          className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
        >
          <option value="">Tôi</option>
          {people.data?.map((person) => (
            <option key={person.user_id} value={person.user_id}>
              {person.full_name}
            </option>
          ))}
        </Select>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="text-xs">
          Số lượng {chosen ? `(${chosen.default_unit_label})` : ""}
          <input
            type="number"
            min="0"
            step="0.5"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            aria-label="Số lượng"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs">
          Nhãn
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Ví dụ: khách A, bài tuần 2"
            aria-label="Nhãn"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs">
          Link
          <input
            value={link}
            onChange={(event) => setLink(event.target.value)}
            placeholder="https://…"
            aria-label="Link"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
      </div>
      <button
        type="submit"
        disabled={!ready || submit.isPending}
        className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
      >
        {submit.isPending ? "Đang lưu…" : "Lưu kết quả"}
      </button>
      {submit.isError ? <ErrorBox error={submit.error} /> : null}
    </form>
  );
}

/**
 * Period-container patch. The results on one stream and the controls over them.
 *
 * Reporting is the subject's or a manager's act; counting is a validator's who
 * is not the subject - both are server flags. A counted result is never
 * deleted here: a validator excludes it with a reason, and the reporter may
 * only withdraw their own pending one.
 */
function ResultsPanel({
  detail,
  onChanged,
  onSynced,
  mayConfigure = false,
}: {
  detail: WorkItemDetail;
  onChanged: (next: WorkItemDetail) => void;
  /** After a per-content sync: the detail is re-read, because the projector wrote elsewhere too. */
  onSynced?: () => void;
  /**
   * Work maintenance. When the person holds `PR_WORK_CONFIGURE`, every result
   * that is not already excluded gets *Xóa kết quả công việc* - the
   * administrative exclusion, distinct from a validator's *Loại bỏ* in who
   * may do it and in what the audit says. The server refuses it for anybody
   * else, whatever this flag drew.
   */
  mayConfigure?: boolean;
}) {
  const item = detail.item;
  const container = item.period_container;
  const [quantity, setQuantity] = useState("1");
  const [label, setLabel] = useState("");
  const [link, setLink] = useState("");
  /**
   * *Từ chối / Không ghi nhận*. One dialog for the row being rejected; the
   * reason is typed inside it and is required - the confirm stays disabled
   * until there is one, so the server's "reason required" is never what the
   * person meets first. `0041`: the decision is persisted as
   * `VALIDATOR_REJECTED`, and no sync brings the result back.
   */
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  /**
   * *Đồng bộ lại từ Nội dung*, for one content result. The same projector the
   * worker runs, called now for that piece; the outcome sentence is the
   * projector's own vocabulary. A separate control from the administrative
   * removal beside it, on purpose - one takes accounting out, the other asks
   * the source what accounting there should be - and neither calls the other.
   */
  const [synced, setSynced] = useState<string | null>(null);
  const resync = useMutation({
    mutationFn: (contentId: string) => api.projectContentWork(contentId),
    onSuccess: (report) => {
      setSynced(contentWorkOutcomeMessage(report.outcome));
      onSynced?.();
    },
  });

  const report = useMutation({
    mutationFn: () =>
      api.reportWorkResultInto(item.id, {
        quantity: quantity.trim() || null,
        label: label.trim() || null,
        link: link.trim() || null,
      }),
    onSuccess: (next) => {
      setQuantity("1");
      setLabel("");
      setLink("");
      onChanged(next);
    },
  });
  const validate = useMutation({
    mutationFn: () => api.validateWorkResults(item.id),
    onSuccess: onChanged,
  });
  const validateOne = useMutation({
    mutationFn: (resultId: string) =>
      api.validateWorkResults(item.id, { result_ids: [resultId] }),
    onSuccess: onChanged,
  });
  const exclude = useMutation({
    mutationFn: (resultId: string) =>
      api.excludeWorkResult(resultId, reason.trim()),
    onSuccess: (next) => {
      setRejecting(null);
      setReason("");
      onChanged(next);
    },
  });
  /** *Xem xét lại*: the one release of a rejection. Back to pending; nothing counted. */
  const reconsider = useMutation({
    mutationFn: (resultId: string) => api.reconsiderWorkResult(resultId),
    onSuccess: onChanged,
  });
  const withdraw = useMutation({
    mutationFn: (resultId: string) => api.withdrawWorkResult(resultId),
    onSuccess: onChanged,
  });
  const adminRemove = useMutation({
    mutationFn: (resultId: string) => api.adminRemoveWorkResult(resultId),
    onSuccess: onChanged,
  });
  const failure =
    report.error ??
    validate.error ??
    validateOne.error ??
    reconsider.error ??
    withdraw.error ??
    adminRemove.error ??
    resync.error ??
    null;

  if (!container) return null;
  const rejectingRow = rejecting
    ? (detail.results.find((row) => row.id === rejecting) ?? null)
    : null;
  const amountOf = (row: WorkResult) =>
    `+${formatRate(row.quantity)} ${container.unit_label}`;
  return (
    <section className="space-y-2 rounded border border-[var(--border)] p-2">
      <h4 className="text-xs font-semibold">
        Kết quả trong kỳ {container.period_code}
      </h4>
      {/* The actual-against-target line and its bar are on the card directly
          above this panel - see `ContainerFigures` there - so the panel opens
          on what the card does not say: the declared, pending and excluded
          split, then the results themselves. */}
      <p className="text-xs text-[var(--text-muted)]">
        Đã báo cáo {formatRate(container.declared_quantity)}{" "}
        {container.unit_label} · đã ghi nhận{" "}
        {formatRate(container.actual_quantity)} · chờ xác nhận{" "}
        {formatRate(container.pending_quantity)}
        {container.excluded_quantity !== "0" &&
        container.excluded_quantity !== "0.00"
          ? ` · loại bỏ ${formatRate(container.excluded_quantity)}`
          : ""}
        {container.has_target
          ? ` · KPI ${formatRate(container.target_quantity)} ${container.unit_label}`
          : " · chưa có KPI cho loại công việc này"}
      </p>

      {detail.can_validate_results && container.pending_count > 0 ? (
        <button
          type="button"
          onClick={() => validate.mutate()}
          disabled={validate.isPending}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {validate.isPending
            ? "Đang xác nhận…"
            : `Xác nhận ${container.pending_count} kết quả chờ`}
        </button>
      ) : null}

      {detail.results.length > 0 ? (
        <ul className="space-y-1 text-xs">
          {detail.results.map((row) => (
            <li
              key={row.id}
              className="flex flex-wrap items-center gap-2 rounded border border-[var(--border)] p-2"
            >
              <span className="font-medium">
                +{formatRate(row.quantity)} {container.unit_label}
              </span>
              <Pill
                tone={
                  row.status === "COUNTED"
                    ? "good"
                    : row.status === "PENDING"
                      ? "warn"
                      : "neutral"
                }
              >
                {row.status_label}
              </Pill>
              <Pill>{row.source_label}</Pill>
              {/*
                The source withdrew what this pending row came from and the
                projector has not swept it yet. Said here, in the canonical
                words, rather than letting the row look like one a validator
                should act on - the server already withholds the controls.
              */}
              {row.status === "PENDING" && row.source_eligible === false ? (
                <Pill tone="neutral">Không còn đủ điều kiện</Pill>
              ) : null}
              {row.label ? <span>{row.label}</span> : null}
              {row.link ? (
                <a
                  className="underline"
                  href={row.link}
                  target="_blank"
                  rel="noreferrer"
                >
                  Link
                </a>
              ) : null}
              <span className="text-[var(--text-muted)]">
                {formatWhen(row.reported_at)}
                {row.reported_by_name ? ` · ${row.reported_by_name}` : ""}
                {row.counted_by_name
                  ? ` · xác nhận bởi ${row.counted_by_name}`
                  : ""}
              </span>
              {/*
                Why an excluded row is out, in full: the reason, who decided
                and when. "Đã từ chối" and "Đã xóa khỏi ghi nhận" are the
                pill's words (the server's `status_label`); this line is the
                decision trail a validator or an administrator reads before
                touching it again.
              */}
              {row.status === "EXCLUDED" ? (
                <span
                  className="basis-full text-[var(--text-muted)]"
                  data-testid={`result-exclusion-${row.id}`}
                >
                  {row.excluded_reason ? `Lý do: ${row.excluded_reason}` : ""}
                  {row.excluded_by_name
                    ? `${row.excluded_reason ? " · " : ""}Người xử lý: ${row.excluded_by_name}`
                    : ""}
                  {row.excluded_at
                    ? ` · Thời gian: ${formatWhen(row.excluded_at)}`
                    : ""}
                </span>
              ) : null}
              {row.can_validate ? (
                <button
                  type="button"
                  disabled={validateOne.isPending}
                  onClick={() => validateOne.mutate(row.id)}
                  aria-label={`Xác nhận kết quả ${amountOf(row)}`}
                  className="rounded border border-[var(--border)] px-2 py-1 text-xs disabled:opacity-50"
                >
                  Xác nhận
                </button>
              ) : null}
              {row.can_reject ? (
                <button
                  type="button"
                  onClick={() => {
                    setReason("");
                    setRejecting(row.id);
                  }}
                  aria-label={`${row.status === "PENDING" ? "Từ chối" : "Loại bỏ"} kết quả ${amountOf(row)}`}
                  className="rounded border border-[var(--border)] px-2 py-1 text-xs"
                >
                  {row.status === "PENDING" ? "Từ chối / Không ghi nhận" : "Loại bỏ"}
                </button>
              ) : null}
              {row.can_reconsider ? (
                <ConfirmButton
                  spec={reconsiderResultConfirmation(amountOf(row))}
                  pending={reconsider.isPending}
                  error={reconsider.error}
                  onConfirm={() => reconsider.mutate(row.id)}
                  ariaLabel={`Xem xét lại kết quả ${amountOf(row)}`}
                  className="!min-h-8 !px-2 !text-xs"
                >
                  Xem xét lại
                </ConfirmButton>
              ) : null}
              {row.can_withdraw ? (
                <button
                  type="button"
                  disabled={withdraw.isPending}
                  onClick={() => withdraw.mutate(row.id)}
                  className="rounded border border-[var(--border)] px-2 py-1 text-xs"
                >
                  Rút lại
                </button>
              ) : null}
              {mayConfigure && row.status !== "EXCLUDED" ? (
                <ConfirmButton
                  spec={adminRemoveResultConfirmation(
                    row.source_type === "CONTENT",
                  )}
                  tone="danger"
                  pending={adminRemove.isPending}
                  error={adminRemove.error}
                  onConfirm={() => adminRemove.mutate(row.id)}
                  ariaLabel={`Xóa kết quả ${formatRate(row.quantity)} ${container.unit_label}`}
                  className="!min-h-8 !px-2 !text-xs"
                >
                  Xóa kết quả công việc
                </ConfirmButton>
              ) : null}
              {/*
                Secondary, beside the destructive one and never merged with
                it. Offered on a content result that is counted, pending or
                administratively removed, because the question it asks the
                source is the same in each state and the answer is the
                projector's. Not on a result a validator rejected: the
                projector would only report that it is holding it, and a
                button that looks like a way back but is not one misleads.
                *Xem xét lại* is that row's control.
              */}
              {mayConfigure &&
              row.source_type === "CONTENT" &&
              row.content_id &&
              !row.held_by_validator ? (
                <button
                  type="button"
                  disabled={resync.isPending}
                  onClick={() => resync.mutate(row.content_id as string)}
                  aria-label={`Đồng bộ lại từ Nội dung ${row.label ?? row.content_id}`}
                  className="min-h-8 rounded border border-[var(--border)] px-2 text-xs disabled:opacity-50"
                >
                  {resync.isPending ? "Đang đồng bộ…" : "Đồng bộ lại từ Nội dung"}
                </button>
              ) : null}
              {row.held_by_validator && row.source_type === "CONTENT" ? (
                <span className="basis-full text-[var(--text-muted)]">
                  Đồng bộ từ Nội dung sẽ không ghi nhận lại kết quả này. Dùng{" "}
                  <em>Xem xét lại</em> nếu cần đánh giá lại.
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--text-muted)]">
          Chưa có kết quả nào trong kỳ này.
        </p>
      )}
      {rejectingRow ? (
        <ConfirmDialog
          open
          spec={rejectResultConfirmation({
            quantity: amountOf(rejectingRow),
            counted: rejectingRow.status === "COUNTED",
            details: (
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Lý do</span>
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  aria-label="Lý do từ chối"
                  rows={3}
                  placeholder="Ví dụ: Không đủ minh chứng"
                  className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-sm"
                />
              </label>
            ),
          })}
          pending={exclude.isPending}
          error={exclude.error}
          confirmDisabled={!reason.trim()}
          onConfirm={() => exclude.mutate(rejectingRow.id)}
          onCancel={() => {
            setRejecting(null);
            setReason("");
          }}
        />
      ) : null}
      {synced ? (
        <p
          role="status"
          className="flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--border)] p-2 text-xs"
        >
          <span>{synced}</span>
          <button
            type="button"
            onClick={() => setSynced(null)}
            className="min-h-8 rounded px-2 text-[11px] text-[var(--text-muted)]"
          >
            Đóng
          </button>
        </p>
      ) : null}

      {detail.can_report_result ? (
        <form
          className="grid grid-cols-2 gap-2 sm:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            report.mutate();
          }}
        >
          <label className="col-span-2 text-xs sm:col-span-1">
            Số lượng ({container.unit_label})
            <input
              type="number"
              min="0"
              step="0.5"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              aria-label="Số lượng kết quả"
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-xs">
            Nhãn
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              aria-label="Nhãn kết quả"
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-xs">
            Link
            <input
              value={link}
              onChange={(event) => setLink(event.target.value)}
              aria-label="Link kết quả"
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
          <div className="col-span-2 flex items-end sm:col-span-1">
            <button
              type="submit"
              disabled={!(Number(quantity) > 0) || report.isPending}
              className="min-h-11 w-full rounded border border-[var(--border)] px-3 text-sm disabled:opacity-50"
            >
              {report.isPending ? "Đang lưu…" : "Thêm kết quả"}
            </button>
          </div>
        </form>
      ) : null}
      {failure ? <ErrorBox error={failure} /> : null}
    </section>
  );
}

/**
 * One job in full, with the controls this actor may actually use.
 *
 * Every control is drawn from a server flag. `can_validate` in particular is
 * false for a contributor whatever capability they hold - so a person who did
 * the work never sees "Xác nhận hoàn thành", and the API refuses them anyway.
 */
/**
 * What a cancelled row still holds, in the words an administrator reads.
 * Keyed on the server's count names; a kind this file does not know is
 * still shown, by its key, rather than dropped.
 */
const BLOCKING_LABELS: Record<string, string> = {
  results: "kết quả công việc",
  counted_contributions: "ghi nhận hiệu suất",
  quota_allocations: "phân bổ KPI/M2",
  score_allocations: "phân bổ điểm M6",
};

function AdminDeleteBlock({
  detail,
  responsible,
  pending,
  error,
  onConfirm,
}: {
  detail: WorkItemDetail;
  responsible: string | null;
  pending: boolean;
  error: unknown;
  onConfirm: () => void;
}) {
  const eligibility = detail.admin_delete;
  if (!eligibility) return null;
  const item = detail.item;
  const blocking = Object.entries(eligibility.blocking).filter(([, count]) => count > 0);
  if (!detail.can_admin_delete) {
    return (
      <div
        className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs"
        data-testid="admin-delete-blocked"
      >
        <p className="font-semibold">Không thể xóa công việc này.</p>
        <p className="mt-1 text-[var(--text-muted)]">
          {eligibility.message ??
            "Công việc này vẫn còn dữ liệu liên quan nên chưa xóa được."}
        </p>
        {blocking.length > 0 ? (
          <>
            <p className="mt-1">Đang còn:</p>
            <ul className="list-inside list-disc">
              {blocking.map(([kind, count]) => (
                <li key={kind}>
                  {count} {BLOCKING_LABELS[kind] ?? kind}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    );
  }
  return (
    <div
      className="rounded border border-[var(--border)] p-2 text-xs"
      data-testid="admin-delete"
    >
      <p className="text-[var(--text-muted)]">
        Công việc này đã kết thúc ({item.status_label}) và không còn dữ liệu kết
        quả hay ghi nhận hiệu suất nào. Chủ sở hữu hoặc Quản trị viên có thể xóa
        hẳn dòng này; các công việc khác không bị ảnh hưởng.
      </p>
      <div className="mt-2">
        <ConfirmButton
          spec={deleteTerminalWorkItemConfirmation({
            code: item.code,
            title: item.title,
            responsible,
            statusLabel: item.status_label,
          })}
          tone="danger"
          pending={pending}
          error={error}
          onConfirm={onConfirm}
        >
          Xóa công việc
        </ConfirmButton>
      </div>
    </div>
  );
}

function WorkDetail({
  workItemId,
  onChanged,
  onDeleted,
  mayConfigure = false,
}: {
  workItemId: string;
  onChanged: () => void;
  /** Work maintenance. The row is gone; the list clears the selection and says so. */
  onDeleted?: (deletedId: string, message: string) => void;
  mayConfigure?: boolean;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["work-item", workItemId],
    queryFn: () => api.workItem(workItemId),
  });
  const settle = (next: WorkItemDetail) => {
    queryClient.setQueryData(["work-item", workItemId], next);
    onChanged();
  };
  const action = (run: () => Promise<WorkItemDetail>) => ({
    mutationFn: run,
    onSuccess: settle,
  });

  const accept = useMutation(action(() => api.acceptWork(workItemId)));
  const reject = useMutation(action(() => api.rejectWork(workItemId)));
  const start = useMutation(action(() => api.startWork(workItemId)));
  const complete = useMutation(action(() => api.completeWork(workItemId)));
  const approve = useMutation(action(() => api.approveWork(workItemId)));
  const reopen = useMutation(action(() => api.reopenWork(workItemId)));
  const cancel = useMutation(action(() => api.cancelWork(workItemId)));
  /**
   * Work maintenance. Deleting a **legacy** content work item - the
   * pre-period-container shape. Offered only when the server said
   * `can_delete_legacy` (legacy row *and* `PR_WORK_CONFIGURE`), and the
   * route refuses anybody else regardless. On success nothing is written back
   * into the cache for this row - it does not exist - and nothing else is
   * called: no sync, no rebuild, no projection. The list is told, and it
   * clears the selection.
   */
  const deleteLegacy = useMutation({
    mutationFn: () => api.deleteLegacyWorkItem(workItemId),
    onSuccess: () => onDeleted?.(workItemId, "Đã xóa công việc cũ."),
  });
  /**
   * Terminal-work delete. Deleting a **cancelled or rejected** work item - the
   * second eligibility rule, beside the legacy one and never merged with it.
   * Offered only when the server said `admin_delete.rule === "terminal"` and
   * `can_admin_delete` (terminal row, holder of `PR_WORK_CONFIGURE`, and
   * nothing blocking right now); the route re-derives all of it under a
   * lock. A refusal re-reads the detail so the panel shows what the row
   * holds, from the server's counts, instead of a button that will keep
   * failing. Nothing else is called on success. Not a transition: a rejected
   * proposal is never cancelled on the way out.
   */
  const deleteTerminal = useMutation({
    mutationFn: () => api.deleteTerminalWorkItem(workItemId),
    onSuccess: () => onDeleted?.(workItemId, "Đã xóa công việc."),
    onError: () => void detail.refetch(),
  });
  /**
   * *Đồng bộ lại từ Nội dung* for the piece this row came from. The canonical
   * projector, run now, for one content item; a separate control from the
   * delete above it, and neither calls the other. What it concludes is said
   * in the projector's own words beneath the button.
   */
  const [synced, setSynced] = useState<string | null>(null);
  const resync = useMutation({
    mutationFn: (contentId: string) => api.projectContentWork(contentId),
    onSuccess: (report) => {
      setSynced(contentWorkOutcomeMessage(report.outcome));
      void detail.refetch();
      onChanged();
    },
  });

  // The first refusal among the actions, whichever it was. One area rather
  // than seven, because only one action can be in flight at a time.
  const failure =
    accept.error ??
    reject.error ??
    start.error ??
    complete.error ??
    approve.error ??
    reopen.error ??
    cancel.error ??
    deleteLegacy.error ??
    deleteTerminal.error ??
    resync.error ??
    null;

  if (detail.isPending) return <Loading label="Đang tải chi tiết…" />;
  if (detail.isError)
    return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;

  const data = detail.data;
  const item = data.item;
  const quantity = formatQuantity(item.quantity, item.unit_label);
  const people = item.contributors.length;
  const responsible = responsibleOf(item);

  // Inline UX patch. The card above this panel already shows the code, the
  // title, the status, the type and the deadline, so none of that leads here.
  // A period container opens on its results - the thing somebody tapped the
  // card to see or to add to - and the metadata that used to head the panel
  // sits under *Thông tin thêm* at the end.
  const metadata = (
    <details className="rounded border border-[var(--border)] p-2">
      <summary className="cursor-pointer text-xs font-semibold">
        Thông tin thêm
      </summary>
      <p className="mt-2 flex flex-wrap gap-1">
        <Pill>{data.work_type.name}</Pill>
        <Pill>{data.work_type.category_label}</Pill>
      </p>
      <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-[var(--text-muted)]">Hạn</dt>
          <dd>
            {item.due_at ? formatWhen(item.due_at) : "Không có hạn"}
            {item.is_overdue ? " · quá hạn" : ""}
          </dd>
        </div>
        {quantity && !item.is_period_container ? (
          <div>
            <dt className="text-[var(--text-muted)]">Số lượng</dt>
            <dd>{quantity}</dd>
          </div>
        ) : null}
        {/*
          Post-M4, refined by the consolidation patch. Beside the deadline and
          never instead of it, and named for what the instant actually is - see
          the same decision on the card. Manual work shows *Bắt đầu* from
          `accepted_at`; inventing an execution date from `due_at` is the thing
          this field exists to stop.
        */}
        {item.is_period_container ? (
          <div>
            <dt className="text-[var(--text-muted)]">Kỳ</dt>
            <dd>{item.period_container?.period_code ?? "—"}</dd>
          </div>
        ) : item.execution_at ? (
          <div>
            <dt className="text-[var(--text-muted)]">Thực hiện</dt>
            <dd>{formatWhen(item.execution_at)}</dd>
          </div>
        ) : item.accepted_at ? (
          <div>
            <dt className="text-[var(--text-muted)]">Bắt đầu</dt>
            <dd>{formatWhen(item.accepted_at)}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-[var(--text-muted)]">Nguồn</dt>
          <dd>
            {item.source_label}
            {/* The overview at the top of a one-off job already links the
                piece; a container has no overview, so its link lives here. */}
            {item.is_period_container &&
            item.is_source_derived &&
            data.content_code ? (
              <>
                {" · "}
                <a
                  className="underline"
                  href={`/pr/content/${item.content_id}`}
                >
                  {data.content_code}
                </a>
              </>
            ) : null}
            {/*
              M4B, surfaced post-M4. The routine this job came from, by name and
              by link. Reached through the occurrence the server resolved - this
              file decodes nothing out of a source key.
            */}
            {item.is_period_container && item.recurring_template_id ? (
              <>
                {" · "}
                <a className="underline" href={`/pr/work?panel=recurring`}>
                  {item.recurring_template_name}
                </a>
              </>
            ) : null}
          </dd>
        </div>
        <div>
          {/* Three times, said as three things. "Báo hoàn thành" is a claim and
              "Đã xác nhận" is the validation - collapsing them into one
              "updated at" would hide the boundary the module exists for. */}
          <dt className="text-[var(--text-muted)]">Mốc thời gian</dt>
          <dd>
            {item.accepted_at
              ? `Giao ${formatWhen(item.accepted_at)}`
              : "Chưa nhận"}
            {item.completed_at
              ? ` · Báo xong ${formatWhen(item.completed_at)}`
              : ""}
            {item.approved_at
              ? ` · Xác nhận ${formatWhen(item.approved_at)}`
              : ""}
          </dd>
        </div>
      </dl>
    </details>
  );

  return (
    <section
      className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 sm:p-4"
      aria-label={`Chi tiết ${item.title}`}
    >
      {/*
        Owner-visibility patch. **The overview: who, from where, what kind.**

        The first thing the panel says, in the order a reader asks it. The
        responsible person leads, at the same weight the card gives them;
        the source names the piece or the routine and links to it; the kind
        of work is the type by name. A legacy content row - the shape the
        projector wrote before results were kept by month - says so under
        *Dữ liệu*, and for somebody who holds `PR_WORK_CONFIGURE` that line
        carries the one control that can remove it. A period container keeps
        its results panel first; its subject is the card's own row already.
      */}
      {item.is_period_container ? null : (
        <section aria-label="Tổng quan công việc">
          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-[var(--text-muted)]">Phụ trách</dt>
              <dd className="text-sm font-semibold break-words">
                {responsible.name ?? (
                  <span className="font-normal text-[var(--text-muted)]">
                    Chưa có người phụ trách
                  </span>
                )}
                {responsible.others > 0 ? (
                  <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                    +{responsible.others} người tham gia
                  </span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--text-muted)]">Nguồn</dt>
              <dd>
                {item.is_source_derived && item.content_id
                  ? "Từ quy trình nội dung"
                  : item.recurring_template_id
                    ? "Từ công việc định kỳ"
                    : item.source_label}
                {item.is_source_derived && data.content_code ? (
                  <>
                    <br />
                    <a
                      className="underline"
                      href={`/pr/content/${item.content_id}`}
                    >
                      {data.content_code}
                    </a>
                  </>
                ) : null}
                {item.recurring_template_id ? (
                  <>
                    <br />
                    <a className="underline" href={`/pr/work?panel=recurring`}>
                      {item.recurring_template_name}
                    </a>
                  </>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--text-muted)]">Loại công việc</dt>
              <dd>{data.work_type.name}</dd>
            </div>
            {item.is_legacy_content_work ? (
              <div>
                <dt className="text-[var(--text-muted)]">Dữ liệu</dt>
                <dd>
                  <Pill>Dữ liệu cũ từ Nội dung</Pill>
                </dd>
              </div>
            ) : null}
          </dl>
        </section>
      )}

      {/*
        Work maintenance. **The delete, and only here.** Inside the expanded
        detail, never on a collapsed card, and only when the server said this
        person may: `can_delete_legacy` is false for every row that is not
        the old item-grain shape and for everybody below ADMIN. The dialog
        says what the delete does not do; the route re-checks everything.
      */}
      {data.can_delete_legacy ? (
        <div className="rounded border border-[var(--border)] p-2 text-xs">
          <p className="text-[var(--text-muted)]">
            Đây là dữ liệu công việc được tạo từ cơ chế Nội dung cũ. Xóa dòng
            này không đụng tới Nội dung nguồn và không tự đồng bộ lại; nếu cần
            ghi nhận lại, chạy <em>Đồng bộ dữ liệu công việc</em> ở mục Cấu hình
            sau.
          </p>
          <div className="mt-2">
            <ConfirmButton
              spec={deleteLegacyWorkItemConfirmation({
                code: item.code,
                title: item.title,
                responsible: responsible.name,
                contentCode: data.content_code,
              })}
              tone="danger"
              pending={deleteLegacy.isPending}
              error={deleteLegacy.error}
              onConfirm={() => deleteLegacy.mutate()}
            >
              Xóa công việc
            </ConfirmButton>
            {item.content_id ? (
              <button
                type="button"
                disabled={resync.isPending}
                onClick={() => resync.mutate(item.content_id as string)}
                className="ml-2 min-h-11 rounded border border-[var(--border)] px-3 text-sm disabled:opacity-50"
              >
                {resync.isPending ? "Đang đồng bộ…" : "Đồng bộ lại từ Nội dung"}
              </button>
            ) : null}
          </div>
          {synced ? (
            <p role="status" className="mt-2 rounded border border-[var(--border)] p-2">
              {synced}
            </p>
          ) : null}
        </div>
      ) : null}

      {/*
        **The other delete, and also only here.** Drawn from `admin_delete`,
        which the server sends only to somebody who holds `PR_WORK_CONFIGURE`
        reading a row one of the two delete rules covers - so TEAM_LEAD and
        EMPLOYEE get neither the button nor the reasoning. The server also
        says which rule: `legacy` is the block above, `terminal` (a cancelled
        or rejected ordinary row) is this one, never both. When the row may
        not go, the panel says why in the server's words and lists what it
        still holds, because "409" is not an explanation.
      */}
      {data.admin_delete?.rule === "terminal" ? (
        <AdminDeleteBlock
          detail={data}
          responsible={responsible.name}
          pending={deleteTerminal.isPending}
          error={deleteTerminal.error}
          onConfirm={() => deleteTerminal.mutate()}
        />
      ) : null}

      {item.is_period_container ? (
        <ResultsPanel
          detail={data}
          onChanged={settle}
          onSynced={() => {
            void detail.refetch();
            onChanged();
          }}
          mayConfigure={mayConfigure}
        />
      ) : null}

      {item.description ? (
        <p className="text-xs whitespace-pre-line break-words">
          {item.description}
        </p>
      ) : null}

      <Contributors contributions={item.contributors} />

      {/* The one sentence that keeps the boundary visible where it matters. */}
      {item.status === "COMPLETED" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Đã báo hoàn thành. Công việc <strong>chưa được ghi nhận</strong> cho
          tới khi một người không tham gia công việc này xác nhận.
        </p>
      ) : null}
      {item.status === "PROPOSED" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Đây là đề xuất. Chưa tính là công việc được giao và chưa ghi nhận cho
          ai.
        </p>
      ) : null}

      {/*
        M3. The two sentences source-derived work needs, and they say different
        things on purpose.

        The first is the reassurance: the employee does **not** have to file
        this again, which is the whole point of the milestone.

        The second is the self-approved case, and it must read as a step that is
        pending rather than as an error. The content workflow accepted the
        piece; what is missing is somebody other than the writer confirming the
        *work*, and that is a person to ask rather than a problem to fix.
      */}
      {item.is_source_derived ? (
        <p className="rounded border border-[var(--border)] p-2 text-xs text-[var(--text-muted)]">
          <strong>Nguồn: Nội dung.</strong> Công việc này được ghi nhận tự động
          từ quy trình nội dung. Bạn không cần nhập lại, và cũng không sửa trực
          tiếp ở đây được — muốn thay đổi thì điều chỉnh ở nội dung gốc.
          {item.content_id ? (
            <>
              {" "}
              <Link
                href={`/pr/content/${item.content_id}`}
                className="underline underline-offset-2"
              >
                Mở nội dung {data.content_code ?? ""}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
      {/*
        M3.1 widened this copy, because `COMPLETED` now has two causes and the
        old sentence asserted the wrong one half the time. A script's approver
        being its writer is one; an editor's cut simply not having been reviewed
        yet is the other, and telling that editor "người duyệt cũng là người
        thực hiện" would be the screen making something up.
      */}
      {item.is_source_derived && item.status === "COMPLETED" ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          <strong>Chờ xác nhận độc lập.</strong> Khối lượng đã được ghi nhận,
          nhưng chưa có người độc lập xác nhận nên chưa tính vào KPI. Cần một
          người khác — không phải người thực hiện — xác nhận công việc này.
        </p>
      ) : null}

      {/*
        **Every lifecycle button is drawn from the server's action contract,
        and from nothing else.** `can_accept` … `can_cancel` are resolved on
        the server from its transition table and the same guards the writes
        apply (`resolve_work_actions`). This panel used to infer each button
        from `status`, `is_source_derived` and the coarse `can_manage` /
        `can_validate` / `can_execute` flags - a second copy of the state
        machine, and one that drifted: a REJECTED row was offered *Hủy* and
        then refused. There is no status list here any more; if a button is
        wrong, the resolver is wrong, and the matrix test says so.
      */}
      <div className="flex flex-wrap gap-2">
        {data.can_accept ? (
          <ConfirmButton
            spec={acceptWorkConfirmation(item.title)}
            pending={accept.isPending}
            error={accept.error}
            onConfirm={() => accept.mutate()}
          >
            Chấp nhận
          </ConfirmButton>
        ) : null}
        {data.can_reject ? (
          <ConfirmButton
            spec={rejectWorkConfirmation(item.title)}
            tone="secondary"
            pending={reject.isPending}
            error={reject.error}
            onConfirm={() => reject.mutate()}
          >
            Từ chối
          </ConfirmButton>
        ) : null}

        {/* Asks first, like every other state change on this panel. Starting
            work is a workflow transition a manager reading the board can see -
            it is not destructive, and it is not free of consequence either. */}
        {data.can_start ? (
          <ConfirmButton
            spec={startWorkConfirmation(item.title)}
            tone="secondary"
            pending={start.isPending}
            error={start.error}
            onConfirm={() => start.mutate()}
          >
            Bắt đầu
          </ConfirmButton>
        ) : null}

        {data.can_complete ? (
          <ConfirmButton
            spec={completeWorkConfirmation(item.title)}
            pending={complete.isPending}
            error={complete.error}
            onConfirm={() => complete.mutate()}
          >
            Hoàn thành
          </ConfirmButton>
        ) : null}

        {/* `can_approve` carries the self-validation rule: a contributor never
            gets it, and the API refuses them if they call it anyway. */}
        {data.can_approve ? (
          <ConfirmButton
            spec={approveWorkConfirmation(item.title, people)}
            pending={approve.isPending}
            error={approve.error}
            onConfirm={() => approve.mutate()}
          >
            Xác nhận hoàn thành
          </ConfirmButton>
        ) : null}
        {data.can_reopen ? (
          <ConfirmButton
            spec={reopenWorkConfirmation(item.title)}
            tone="secondary"
            pending={reopen.isPending}
            error={reopen.error}
            onConfirm={() => reopen.mutate()}
          >
            Trả lại
          </ConfirmButton>
        ) : null}

        {/* Manual work only, never approved, never REJECTED or CANCELLED -
            all decided by the server. Cancelling is a lifecycle fact the
            content workflow or the routine owns for everything else. */}
        {data.can_cancel ? (
          <ConfirmButton
            spec={cancelWorkConfirmation(item.title)}
            tone="secondary"
            pending={cancel.isPending}
            error={cancel.error}
            onConfirm={() => cancel.mutate()}
          >
            Hủy
          </ConfirmButton>
        ) : null}
      </div>

      {/* A refused action, shown on the panel and not only inside the dialog
          that raised it. The dialog is where a failure is *first* seen; this is
          what makes sure a refusal is still readable afterwards - and the one
          that matters most is the self-validation refusal, which somebody will
          hit precisely when their screen was drawn a minute ago. */}
      {failure ? <ErrorBox error={failure} /> : null}

      {item.status === "APPROVED" ? (
        <p className="text-xs text-[var(--text-muted)]">
          Đã xác nhận nên không sửa lại được. Nếu cần điều chỉnh, hãy ghi nhận
          bằng một công việc mới.
        </p>
      ) : null}

      <Evidence detail={data} onAdded={settle} />

      {metadata}

      <WorkTimeline workItemId={workItemId} />
    </section>
  );
}

/**
 * Who did this job, and whether each person's share was counted.
 *
 * One row per person, never a divided total: a shoot with three people shows
 * three names each with a full unit of credit, because the department did one
 * shoot and each of them did a day's work.
 */
function Contributors({
  contributions,
}: {
  contributions: WorkContribution[];
}) {
  if (contributions.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold">Người thực hiện</h4>
      <ul className="space-y-1">
        {contributions.map((one) => (
          <li
            key={one.id}
            className="flex flex-wrap items-center justify-between gap-2 text-xs"
          >
            <span>
              {one.user_name ?? "—"}
              <span className="text-[var(--text-muted)]">
                {" "}
                · {one.contribution_role_label}
              </span>
            </span>
            <span className="flex items-center gap-1">
              <Pill tone={one.count_status === "COUNTED" ? "good" : "neutral"}>
                {one.count_status_label}
              </Pill>
              {one.counted_at ? (
                <span className="text-[10px] text-[var(--text-muted)]">
                  {formatWhen(one.counted_at)}
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Evidence, as **one text**. A form whose submit button is its own confirmation.
 *
 * A member no longer splits what they have into a "Nhãn" and a "Liên kết".
 * They type one thing - a description, a Drive link, a Facebook link, several
 * lines, any mix - and the server keeps it whole and makes the links inside it
 * clickable here. No URL is required, and nothing on this screen checks for
 * one.
 *
 * Rows written before this patch carry a label and a link and no text; they
 * render as they always did, the label linking to the location. Rows written
 * as text render the text. Both can sit in one list.
 *
 * With nothing attached yet the textarea is simply there for anybody who may
 * add evidence - one field, nothing to open first. Once something exists, the
 * list leads and *Thêm minh chứng* reveals the field, so a job with three
 * pieces of evidence is not headed by an empty form.
 */
function Evidence({
  detail,
  onAdded,
}: {
  detail: WorkItemDetail;
  onAdded: (next: WorkItemDetail) => void;
}) {
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const mayAdd =
    detail.can_execute &&
    !detail.item.is_source_derived &&
    detail.item.status !== "APPROVED";
  const open = mayAdd && (detail.evidence.length === 0 || adding);
  const add = useMutation({
    mutationFn: () =>
      api.addWorkEvidence(detail.item.id, { text: text.trim() }),
    onSuccess: (next) => {
      setText("");
      setAdding(false);
      onAdded(next);
    },
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold">
          Minh chứng
          {detail.work_type.requires_evidence ? (
            <span className="ml-1 text-[10px] text-[var(--text-muted)]">
              · bắt buộc
            </span>
          ) : null}
        </h4>
        {mayAdd && detail.evidence.length > 0 ? (
          <button
            type="button"
            onClick={() => setAdding((was) => !was)}
            className="min-h-11 rounded border border-[var(--border)] px-2 text-xs"
          >
            {adding ? "Đóng" : "Thêm minh chứng"}
          </button>
        ) : null}
      </div>
      {detail.evidence.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">Chưa có minh chứng.</p>
      ) : (
        <ul className="space-y-1">
          {detail.evidence.map((one) => (
            <li key={one.id} className="text-xs">
              {one.text ? (
                <Linkified text={one.text} />
              ) : one.location ? (
                <a
                  href={one.location}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="break-all underline"
                >
                  {one.label}
                </a>
              ) : (
                <span className="break-words">{one.label}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {open ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Nhập mô tả, liên kết hoặc thông tin liên quan…"
            aria-label="Minh chứng"
            rows={3}
            className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
          <button
            type="submit"
            disabled={add.isPending || !text.trim()}
            className="min-h-11 rounded bg-[var(--text)] px-3 text-xs text-[var(--surface)] disabled:opacity-50"
          >
            {add.isPending ? "Đang lưu…" : "Lưu minh chứng"}
          </button>
          {add.isError ? <ErrorBox error={add.error} /> : null}
        </form>
      ) : null}
    </div>
  );
}

/** The user-facing story. Not the audit trail - see the API module. */
function WorkTimeline({ workItemId }: { workItemId: string }) {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["work-history", workItemId],
    queryFn: () => api.workHistory(workItemId),
    enabled: open,
  });
  return (
    <details
      className="rounded border border-[var(--border)] p-2"
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-xs font-semibold">
        Lịch sử
      </summary>
      {history.isPending && open ? <Loading label="Đang tải lịch sử…" /> : null}
      <ul className="mt-2 space-y-1">
        {history.data?.map((one) => (
          <li key={one.id} className="text-[11px]">
            <span className="text-[var(--text-muted)]">
              {formatWhen(one.created_at)}
            </span>{" "}
            {one.event_label}
            {one.actor_name ? ` · ${one.actor_name}` : ""}
            {one.note ? ` · ${one.note}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * **Proposing or assigning work - and, for an assignment, deciding whether it
 * happens once or every week.** M4A built this form; the post-M4 UX
 * consolidation made it the module's single assignment entry point.
 *
 * ## The question this form now asks first
 *
 * *Hình thức: Một lần / Định kỳ.* Before this, a manager had to answer it by
 * choosing a **tab** - *Công việc* or *Định kỳ* - which is a question about the
 * software rather than about the work. The intention is the same either way
 * ("Hạnh Quyên seeds a hundred comments"); only its shape differs, and the
 * shape is one radio.
 *
 * **One form, three endpoints, and the split is deliberate.**
 *
 * * *Đề xuất* → `POST /work/propose`. Lands at `PROPOSED` and needs a
 *   *different* manager to accept it. That is the whole anti-gaming design:
 *   assigning lands at `ACCEPTED` because the assignment **is** the
 *   authorization, and proposing is not an authorization at all.
 * * *Giao · Một lần* → `POST /work/assign`. One manual work item per the
 *   assignment mode, accepted at the moment the manager confirms.
 * * *Giao · Định kỳ* → `POST /work/recurring`. A routine at `Nháp`, which
 *   generates **nothing** until somebody activates it, and then generates
 *   ordinary M1 work items.
 *
 * The frontend picks; the server does not guess. A single endpoint that
 * persisted either a work item or a template depending on a flag in the body
 * would be one route owning two unrelated lifecycles, and every validation on
 * it would have to start by asking which one it was looking at.
 *
 * ## What stays on screen when the mode changes, and what does not
 *
 * The kind of work, the name, the description, the people, how it is handed
 * out and the quantity are facts about the *assignment* and survive the switch
 * untouched. What is discarded is what the other shape cannot mean: a *Một lần*
 * assignment has a deadline instant and no schedule; a *Định kỳ* one has a
 * schedule and a deadline **rule** - "hạn sau N giờ" from each firing - because
 * a routine with a single due date would be a routine that is late forever.
 *
 * ## What M4A added, and why each piece is still here
 *
 * **Several assignees, and an explicit choice about what that means.** One
 * instruction handed to three people is either one shared job or three separate
 * obligations, and the two are different facts rather than different spellings.
 * There is no default the server will accept silently: it refuses a
 * multi-assignee body that does not say which, because guessing `SHARED_WORK`
 * would let one person complete three people's work and one validation count
 * all three. A routine is asked even for a single assignee - the mode is stored
 * and outlives the moment, so a second person added next month would otherwise
 * inherit whatever the field happened to default to.
 *
 * **A quantity that is required when the work type is measured by one.** The
 * field is not merely encouraged: a `QUANTITY` type filed without a number
 * reports as zero comments against a plan expressed in comments. One hundred
 * comments is **one** work item with `quantity = 100` - one-off or once per
 * firing - and never a hundred rows.
 *
 * **The unit is read-only**, always, because it comes from the work type. That
 * is what stops one real job being split into whichever shape counts best.
 *
 * **A diagnostic, never a gate.** `WorkReadiness` says whether counted work of
 * this kind will reach a KPI quota and an M6 rate. Both can be false and the
 * form still submits: "Kháng page David" is real work whether or not anybody
 * has written a scoring rule for page recovery, and a form that refused it
 * would teach people to file real work under whichever type was configured.
 *
 * ## Content is not here, and that is structural
 *
 * No body this form can build reaches `source_type`. Content-derived work is
 * projected from the M3.1 milestone and appears in the ledger on its own; the
 * sentence saying so is guidance, and the absence of the field is the guard.
 *
 * A parameter form, so its submit button **is** the confirmation step - see
 * `ACTION_INVENTORY`. Stacking a dialog on top would be two confirmations for
 * one decision. Activating a routine is the act that needs confirming, and it
 * has its own, in *Quản lý việc định kỳ*.
 */
function WorkForm({
  mode,
  mayConfigure,
  mayManage,
  onDone,
}: {
  mode: "propose" | "assign";
  mayConfigure: boolean;
  /**
   * Part W. Whether *Định kỳ* is offered at all. Setting up a routine is
   * `PR_WORK_MANAGE` - M4B's rule, unchanged by merging the forms. Merging two
   * screens must not widen what either could do.
   */
  mayManage: boolean;
  onDone: (created: "ONE_TIME" | "RECURRING") => void;
}) {
  const queryClient = useQueryClient();
  const types = useQuery({
    queryKey: ["work-types"],
    queryFn: () => api.workTypes(),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const [workMode, setWorkMode] = useState<"ONE_TIME" | "RECURRING">(
    "ONE_TIME",
  );
  const [workTypeId, setWorkTypeId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [quantity, setQuantity] = useState("");
  const [assignees, setAssignees] = useState<string[]>([]);
  const [assignmentMode, setAssignmentMode] = useState("SEPARATE_PER_ASSIGNEE");
  // Period-container patch. Whether the routine is a stream rather than a job
  // per firing. See `AccumulateSwitch`.
  const [accumulate, setAccumulate] = useState(false);
  // The recurrence half, in the same hook the edit form uses - so a routine is
  // described by one set of controls whether it is being created or corrected.
  const {
    schedule,
    patch,
    ready: scheduleReady,
    input: scheduleInput,
  } = useRecurringSchedule();

  // A proposal is one person's suggestion about their own work; there is no
  // standing authorization to propose, so *Định kỳ* is an assignment-only
  // shape. Guarded on the capability too, not only on the mode.
  const mayRecur = mode === "assign" && mayManage;
  const recurring = mayRecur && workMode === "RECURRING";

  const chosen: WorkType | undefined = types.data?.find(
    (one) => one.id === workTypeId,
  );
  const several = assignees.length > 1;
  const accumulating = recurring && accumulate;
  // Measured by quantity means the number is part of the record, not a note.
  // A stream carries no quantity: what it should reach is a KPI target.
  const needsQuantity =
    !accumulating && chosen?.default_quota_basis === "QUANTITY";

  /**
   * The diagnostic. Asked for the type and whoever is currently named, and
   * re-asked as that list changes - a quota is per person and per month, so
   * adding a fourth assignee can change the answer.
   */
  const readiness = useQuery({
    queryKey: ["work-readiness", workTypeId, assignees],
    queryFn: () => api.workReadiness(workTypeId, assignees),
    enabled: Boolean(workTypeId),
  });

  /** The routine, assembled once so the preview and the submit agree exactly. */
  const templateBody: RecurringTemplateInput = {
    name: title.trim(),
    work_type_id: workTypeId,
    assignment_mode: accumulating ? "SEPARATE_PER_ASSIGNEE" : assignmentMode,
    contributor_user_ids: assignees,
    quantity: accumulating ? null : quantity.trim() || null,
    accumulate_by_period: accumulating,
    description: description.trim() || null,
    ...scheduleInput,
  };

  // Three endpoints returning three shapes - one item, the list a batch
  // created, or a template - and this form reads none of them: it closes and
  // refreshes. Typed as the union rather than narrowed, so a future caller that
  // *does* read the result has to say which it expected.
  const submit = useMutation<
    WorkItemDetail | AssignWorkBatch | RecurringTemplate
  >({
    mutationFn: () => {
      if (recurring) return api.createRecurringTemplate(templateBody);
      const body = {
        work_type_id: workTypeId,
        title: title.trim(),
        description: description.trim() || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        quantity: quantity.trim() || null,
      };
      if (mode === "propose") return api.proposeWork(body);
      return api.assignWorkBatch({
        ...body,
        contributor_user_ids: assignees,
        // Sent only when it means something. With one assignee the two modes
        // produce identical work and the server does not ask.
        assignment_mode: several ? assignmentMode : null,
      });
    },
    onSuccess: () => {
      if (recurring) {
        void queryClient.invalidateQueries({
          queryKey: ["recurring-templates"],
        });
      }
      onDone(recurring ? "RECURRING" : "ONE_TIME");
    },
  });

  // Only once the list has actually arrived - an empty array while the query is
  // still pending would flash the guidance at somebody whose department has a
  // perfectly good taxonomy.
  const noTypes = types.isSuccess && (types.data?.length ?? 0) === 0;
  const ready =
    workTypeId &&
    title.trim() &&
    (mode === "propose" || assignees.length > 0) &&
    (!recurring || scheduleReady) &&
    (!needsQuantity || Number(quantity) > 0);

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <h3 className="text-sm font-semibold">
        {mode === "assign" ? "Giao công việc" : "Đề xuất công việc"}
      </h3>
      {mode === "propose" ? (
        <p className="text-xs text-[var(--text-muted)]">
          Đề xuất chưa tính là công việc được giao. Một người quản lý khác cần
          chấp nhận trước khi được ghi nhận.
        </p>
      ) : null}

      {/*
        Post-M4 consolidation, Parts C and Y. **The first question, and the
        only one that changes what this form creates.** Default *Một lần*: most
        assignments are one job, and a form that opened on the rarer shape would
        make the common case the one people have to undo.
      */}
      {mayRecur ? (
        <fieldset className="space-y-1 rounded border border-[var(--border)] p-2">
          <legend className="text-xs font-semibold">Hình thức công việc</legend>
          {WORK_MODES.map((one) => (
            <label key={one.key} className="flex items-start gap-2 text-xs">
              <input
                type="radio"
                name="work_mode"
                value={one.key}
                checked={workMode === one.key}
                onChange={() => setWorkMode(one.key)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">{one.label}</span>
                <span className="block text-[var(--text-muted)]">
                  {one.hint}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}

      {/*
        M4A, Part H. Manual work must not become a second way to create
        content-derived workload, and the honest way to prevent that is to say
        so where somebody would otherwise do it. The *rule* is structural - no
        request body reaches `source_type` at all - so this sentence is guidance
        rather than the guard. It applies to both shapes: a routine that mirrored
        a content schedule would double-count the same deliverable.
      */}
      <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
        Công việc phát sinh từ quy trình Nội dung sẽ được hệ thống tự ghi nhận.
        Không cần tạo thủ công lại.
      </p>

      {/*
        M2.5. An empty taxonomy gets a sentence, not a selector with nothing in
        it. This was the whole shape of the original bug: `pr_work_types` shipped
        empty, so the picker rendered its placeholder and no options, and the
        person in front of it had no way to tell "nothing configured" from
        "still loading" or "you are not allowed". The two messages differ
        because the two people can do different things about it.
      */}
      {noTypes ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          {mayConfigure
            ? "Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình."
            : "Hiện chưa có loại công việc khả dụng."}
        </p>
      ) : (
        <Select
          value={workTypeId}
          onChange={(event) => setWorkTypeId(event.target.value)}
          aria-label="Loại công việc"
          className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
        >
          <option value="">Chọn loại công việc</option>
          {types.data?.map((one) => (
            <option key={one.id} value={one.id}>
              {one.category_label} · {one.name}
            </option>
          ))}
        </Select>
      )}

      {chosen?.requires_evidence ? (
        <p className="text-xs text-[var(--text-muted)]">
          Loại công việc này bắt buộc có minh chứng khi báo hoàn thành.
        </p>
      ) : null}

      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Tên công việc"
        aria-label="Tên công việc"
        className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
      />

      <textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="Mô tả"
        aria-label="Mô tả"
        rows={2}
        className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
      />

      {mode === "assign" ? (
        <fieldset className="space-y-1">
          <legend className="text-xs">Người thực hiện</legend>
          <div className="flex flex-wrap gap-2">
            {people.data?.map((person) => (
              <label
                key={person.user_id}
                className="flex items-center gap-1 text-xs"
              >
                <input
                  type="checkbox"
                  checked={assignees.includes(person.user_id)}
                  onChange={(event) =>
                    setAssignees((current) =>
                      event.target.checked
                        ? [...current, person.user_id]
                        : current.filter((one) => one !== person.user_id),
                    )
                  }
                />
                {person.full_name}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      {/*
        M4A, Part F, and Part S of the consolidation.

        For a **one-off** assignment this is shown only when it decides
        something: with one assignee both modes produce identical work, and
        offering the choice anyway would be asking a question whose answer does
        not matter - which is how people learn to click past the one that does.

        For a **routine** it is always shown, because the mode is stored and
        outlives the moment. A second person added to the routine next month
        would otherwise inherit a default nobody chose.
      */}
      {recurring ? (
        <AccumulateSwitch checked={accumulate} onChange={setAccumulate} />
      ) : null}

      {mode === "assign" && (recurring || several) ? (
        <fieldset className="space-y-1 rounded border border-[var(--border)] p-2">
          <legend className="text-xs font-semibold">Cách giao</legend>
          {WORK_ASSIGNMENT_MODES.map((one) => (
            <label key={one.key} className="flex items-start gap-2 text-xs">
              <input
                type="radio"
                disabled={accumulating && one.key !== "SEPARATE_PER_ASSIGNEE"}
                name="assignment_mode"
                value={one.key}
                checked={assignmentMode === one.key}
                onChange={() => setAssignmentMode(one.key)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">{one.label}</span>
                <span className="block text-[var(--text-muted)]">
                  {one.hint}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-2">
        {/*
          Part D and Part E. **A one-off assignment states a deadline and
          nothing else about time.** There is no "ngày thực hiện" field, and
          there is deliberately not going to be one: the operational start of
          manually assigned work is `accepted_at` - the instant the manager
          presses this button - and asking somebody to type it would invite a
          date that disagrees with what the module recorded.

          A routine has no single deadline instant. Its deadline is the rule
          each firing inherits, and it lives in the schedule fields below.
        */}
        {recurring ? null : (
          <label className="text-xs">
            Hạn
            <input
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
        )}
        {accumulating ? null : (
          <label className="text-xs">
            {/* The unit comes from the work type, never from the person filing:
              that is what stops one real job being split into whichever shape
              counts best. Read-only, and rendered as text rather than as a
              disabled input so nobody looks for a way to change it. */}
            Số lượng {chosen ? `(${chosen.default_unit_label})` : ""}
            {needsQuantity ? <span aria-hidden="true"> *</span> : null}
            <input
              type="number"
              min="0"
              step="0.5"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              aria-label={`Số lượng${chosen ? ` (${chosen.default_unit_label})` : ""}`}
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
            />
          </label>
        )}
      </div>

      {needsQuantity ? (
        <p className="text-xs text-[var(--text-muted)]">
          Loại công việc này được tính theo số lượng nên bắt buộc nhập. Ví dụ:
          100 comment là <strong>một</strong> đầu việc số lượng 100
          {recurring ? " cho mỗi lần chạy" : ""}.
        </p>
      ) : null}

      {/*
        The recurrence half, revealed by the *Hình thức* radio and rendered by
        the same components the edit form uses. The preview underneath is the
        server's - `POST /preview`, computed by the same object the generator
        fires from - and nothing on this screen works out a date.
      */}
      {recurring ? (
        <>
          <RecurringScheduleFields schedule={schedule} onChange={patch} />
          <RecurringPreview
            body={templateBody}
            enabled={
              scheduleReady && Boolean(workTypeId) && assignees.length > 0
            }
          />
          <p className="text-xs text-[var(--text-muted)]">
            Việc định kỳ mới được lưu ở trạng thái <strong>Nháp</strong> và chưa
            sinh việc. Hãy xem lịch dự kiến rồi bấm <strong>Bật chạy</strong> ở
            Quản lý việc định kỳ.
          </p>
        </>
      ) : null}

      {/*
        M4A, Parts J and AP. Two facts, two different people who can act on
        them, so two sentences rather than one combined warning. Neither blocks
        the button.
      */}
      {readiness.data ? (
        <div className="space-y-1">
          {!readiness.data.has_scoring_rule ? (
            <p className="text-xs text-[var(--text-muted)]">
              Chưa có quy tắc workload cho loại công việc này. Công việc vẫn
              được ghi nhận vào Work Ledger.
            </p>
          ) : null}
          {readiness.data.assignees_without_quota > 0 ? (
            <p className="text-xs text-[var(--text-muted)]">
              Đầu việc này sẽ được ghi nhận vào Work Ledger. Hiện chưa có
              KPI/quota phù hợp cho kỳ này
              {readiness.data.assignees_without_quota > 1
                ? ` (${readiness.data.assignees_without_quota} người)`
                : ""}
              .
            </p>
          ) : null}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={!ready || submit.isPending}
        className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
      >
        {submit.isPending
          ? "Đang lưu…"
          : recurring
            ? "Tạo việc định kỳ"
            : mode === "assign"
              ? "Giao công việc"
              : "Gửi đề xuất"}
      </button>
      {submit.isError ? <ErrorBox error={submit.error} /> : null}
    </form>
  );
}
