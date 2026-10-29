"use client";

import { TokenBadge } from "@/components/token-badge";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type TaskCell, type TaskRow } from "@/lib/api";
import { UnitSwitch, UntaggedState } from "@/components/unit-switch";
import { effortEnabled } from "@/lib/effort";
import { formatDateTime, formatShortDateTime } from "@/lib/datetime";
import { formatAgo } from "@/lib/labels";
import {
  currentUnit,
  isPlainStaff,
  isUntagged,
  unitEntry,
  unitName,
  unitShortLabel,
  unitTagClass,
} from "@/lib/units";
import { ConfirmButton } from "@/components/confirm";
import { DeadlineBadge } from "@/components/deadline-badge";
import { formatTokens } from "@/lib/deadline";
import { Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import {
  DesignCodesCell,
  DesignContentCell,
  DesignDeadlineCell,
  DesignStatusCell,
  DesignStatusSelect,
  DesignerCell,
  designDeadline,
  designStatusLabel,
  designTeam,
} from "@/components/design-cells";
import { RowActions } from "@/components/task-actions";
import { TaskBulkBar } from "@/components/task-bulk-bar";
import { PersonLink } from "@/components/person-link";
import {
  NO_ROW_NAV,
  Pager,
  RowDeadlineCell,
  RowStatusCell,
  SortHeader,
  type SortValueOf,
  StatusBadge,
  nextSort,
  pageSizeOf,
  parseSort,
  sortApplies,
  sortRows,
  sortTime,
  useRowNavigation,
} from "@/components/task-table";
import { STEP_LEGEND, stepColor } from "@/lib/status-colors";
import { DateInput } from "@/components/date-input";

/** The ORD "Pha" filter, until the server's own list arrives. */
const ADS_STEPS = [
  { value: "ORDER", label: "Order" },
  { value: "BIEN_TAP", label: "Biên tập" },
  { value: "THIET_KE", label: "Thiết kế" },
  { value: "DUNG", label: "Dựng" },
];

export default function TasksPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải bảng task…" />}>
      <TaskBoard />
    </Suspense>
  );
}

/** ORD's "Trạng thái task" (2026-10-19), until the server's own list arrives. */
const TASK_STATES = [
  { value: "CHO_DUYET", label: "Chờ duyệt" },
  { value: "ORDER_RETURNED", label: "Order bị trả lại" },
  { value: "CHO_PHAN_CONG", label: "Chờ phân công" },
  { value: "DA_GIAO", label: "Đã giao, chưa nhận" },
  { value: "DANG_LAM", label: "Đang làm" },
  { value: "DANG_SUA", label: "Đang sửa" },
  { value: "HOAN_THANH", label: "Hoàn thành" },
  { value: "DA_HUY", label: "Đã huỷ" },
];

/** The PR stages that are review gates: "Duyệt task" applies to rows there. */
const PR_GATES = new Set(["TEAM_LEAD_REVIEW", "HEAD_REVIEW", "INTERNAL_REVIEW"]);

const QUICK = [
  ["mine", "Task của tôi"],
  ["awaiting_me", "Chờ tôi xử lý"],
  ["priority", "Ưu tiên"],
  ["urgent", "Gấp"],
  // The rows whose current step - or, at the orderer's gates, the wished
  // deadline - is past due. ORD only: PR has no deadlines.
  ["overdue", "Trễ hạn"],
] as const;


const field =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/**
 * The task table: one unit, many filters, every filter on the URL.
 *
 * Rows come from `/api/board/tasks` already shaped for the screen - phase,
 * cells, labels, who holds it and since when - so this file draws and never
 * interprets. The two inline decisions a head makes on an ORD row (approve or
 * return the order) go through the same `/api/orders` actions as the detail.
 *
 * A plain staff member of the stream (base role EMPLOYEE, neither its head nor
 * a function's lead) opens on "Task của tôi" with the rows waiting on them
 * first (`order=todo_first`), unless the URL says otherwise: an explicit
 * `mine=false` keeps it off. Everyone else opens on everything.
 *
 * The Design ban (`design.view` on `/api/units/me`) reads ORD's table in a
 * simpler shape: the same first four columns (Order, Tên · Loại, Trạng thái,
 * Deadline), then "Nội dung", "Tên task" and "Thiết kế" from the row's
 * `design` block in place of the per-step cells and "Kết quả". Which rows they
 * get is the server's scope, not this file's.
 * Their rows also carry the step's own buttons ("Nhận việc", "Nộp bài",
 * "Duyệt": `RowActions`), which open the task page's dialog in place.
 * The status and the deadline they read are the Design step's own, and so are
 * their filters: no "Pha", "Quy trình", "Loại video" or "Marketing", but the
 * Design step's "Trạng thái" (`design_status`) and a "Người làm" that lists
 * the Design ban only. A removed filter still on the URL (an old link) is not
 * sent for them: nothing may hide rows behind a control that is not there.
 *
 * On every variant: a click on a row opens its task, the page size is a
 * choice (`?size=`), and a double click on a column header sorts the rows on
 * screen by that column (`?sort=`) - this page only, the server keeps paging
 * in its own order.
 */
function TaskBoard() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  // The Shell's own session query (same key): the base role and who "me" is.
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const unit = currentUnit(params, me.data);
  const read = (key: string) => params.get(key) ?? "";
  const untagged = isUntagged(me.data);
  const staff = isPlainStaff(me.data, session.data?.role, unit);
  const mineParam = params.get("mine");
  const mine = mineParam === "true" || (mineParam === null && staff);
  const order: "todo_first" | undefined =
    (staff && mine) || read("order") === "todo_first" ? "todo_first" : undefined;
  const viewerId = session.data?.user_id ?? null;
  const page = Number(read("page") || "1");
  const size = pageSizeOf(params.get("size"));
  const sort = parseSort(params.get("sort"));
  const dense = read("view") === "compact";
  /**
   * The Trưởng phòng PR's ticked rows (2026-10-19), by content id. Held
   * against the URL: any change of unit, filter, page or sort is a different
   * set to choose from, so the selection goes with it - reconciled during
   * render like the PR board's bulk selection.
   */
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const selectionContext = params.toString();
  const [selectionFor, setSelectionFor] = useState(selectionContext);
  if (selectionFor !== selectionContext) {
    setSelectionFor(selectionContext);
    if (selected.size > 0) setSelected(new Set());
  }
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);
  const bulkApprove = useMutation({
    /**
     * "Duyệt task" for every ticked row that stands at a review gate: one
     * PR bulk-approve request per gate (the server refuses a batch that
     * mixes two), rows elsewhere skipped. The server still checks each
     * item against the viewer's own grants.
     */
    mutationFn: async (rows: TaskRow[]) => {
      const byGate = new Map<string, string[]>();
      for (const row of rows) {
        if (!PR_GATES.has(row.status)) continue;
        byGate.set(row.status, [...(byGate.get(row.status) ?? []), row.id]);
      }
      let approved = 0;
      for (const [gate, ids] of byGate) {
        const result = await api.bulkApprove({ gate, content_ids: ids });
        approved += result.approved_count;
      }
      return approved;
    },
    onMutate: () => setBulkNotice(null),
    onSuccess: (approved) => {
      setBulkNotice(`Đã duyệt ${approved} task.`);
      setSelected(new Set());
      void queryClient.invalidateQueries({ queryKey: ["board"] });
      void queryClient.invalidateQueries({ queryKey: ["counts"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  // The Design ban's own table, on ORD only (never PR or the merged view).
  const designView = unit === "ADS" && Boolean(me.data?.design?.view);
  // The filters the Design ban does not have: read as empty for them.
  const general = (key: string) => (designView ? undefined : read(key) || undefined);
  const filters = {
    unit,
    date_from: read("from") || undefined,
    date_to: read("to") || undefined,
    // Ads filters "Pha" by its four steps (Order, Biên tập, Thiết kế, Dựng);
    // PR and the merged view by the five shared phases.
    phase: unit === "ADS" ? undefined : read("phase") || undefined,
    step: unit === "ADS" ? general("step") : undefined,
    // 2026-10-19: ORD's "Quy trình", "Loại video" and "Marketing" filters
    // are gone; PR keeps "Người phụ trách" (the owner).
    kind: unit === "ADS" ? undefined : general("kind"),
    owner: unit === "ADS" ? undefined : general("owner"),
    // The Design ban's "Trạng thái": where the Design step stands.
    design_status: designView ? read("design_status") || undefined : undefined,
    // ORD's "Trạng thái task": where the whole order stands.
    state: unit === "ADS" ? general("state") : undefined,
    assignee: read("assignee") || undefined,
    person: read("person") || undefined,
    mine,
    awaiting_me: read("awaiting_me") === "true",
    order,
    priority: read("priority") === "true",
    urgent: read("urgent") === "true",
    overdue: unit !== "PR" && read("overdue") === "true",
    q: read("q") || undefined,
    limit: size,
    offset: (page - 1) * size,
  };
  const board = useQuery({
    queryKey: ["board", "tasks", filters],
    queryFn: () => api.boardTasks(filters),
    // Waits for the session too: a staff member's first request is already
    // "Task của tôi", not everything followed by a second request.
    enabled: me.isSuccess && !untagged && !session.isPending,
  });
  const members = useQuery({
    queryKey: ["units", unit, "members"],
    queryFn: () => api.unitMembers(unit),
    enabled: me.isSuccess && !untagged && unit !== "ALL",
  });
  // The "(n)" on each quick filter: how many rows it holds in the unit on
  // screen, whatever the other filters say (an inbox count). Live: polled, and
  // refetched with the list - every `["board"]` invalidation covers it too.
  const counts = useQuery({
    queryKey: ["board", "counts", unit],
    queryFn: () => api.boardCounts(unit),
    enabled: me.isSuccess && !untagged,
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
  });
  const listAt = board.dataUpdatedAt;
  const countsAt = counts.dataUpdatedAt;
  const countsBusy = counts.isFetching;
  useEffect(() => {
    // The list came back fresher than the counts (a new page, a filter, a
    // refetch): the badges follow it. Not while they are already on their way.
    if (!listAt || !countsAt || countsBusy || listAt - countsAt < 1000) return;
    void queryClient.invalidateQueries({ queryKey: ["board", "counts"] });
    // Only the list's arrival triggers this; the counts' own state is read as it is.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listAt]);
  const [search, setSearch] = useState(read("q"));

  const setParams = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in changes)) next.delete("page");
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, {
      scroll: false,
    });
  };
  const isOn = (key: string) => (key === "mine" ? mine : read(key) === "true");
  // "Task của tôi" is on by default for staff: turning it off has to be said
  // on the URL (`mine=false`), and turning it back on is the default again.
  const toggle = (key: string) =>
    key === "mine"
      ? setParams({ mine: mine ? (staff ? "false" : "") : staff ? "" : "true" })
      : setParams({ [key]: read(key) === "true" ? "" : "true" });
  // Only the filters this viewer has: a removed one left on the URL is not
  // applied, so it is not counted either.
  const activeFilters = (
    designView
      ? ["from", "to", "design_status", "assignee", "person", "q"]
      : unit === "ADS"
        ? ["from", "to", "step", "state", "dept", "assignee", "person", "q"]
        : ["from", "to", "phase", "kind", "owner", "assignee", "person", "q"]
  ).filter((key) => read(key)).length;

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (untagged) return <UntaggedState title="Quản lý task" />;

  const entry = unitEntry(me.data, "ADS");
  const decides =
    unit === "ADS" && (me.data.can_view_all || entry?.role === "HEAD");
  /**
   * The Trưởng phòng PR (a HEAD seat in a PR department) - or the OWNER /
   * ADMIN - gets a checkbox on every row of the PR table and the bulk bar
   * (2026-10-19). PR only: the merged view and ORD have no bulk actions yet.
   */
  const selecting =
    unit === "PR" &&
    (me.data.can_view_all ||
      (me.data.departments ?? []).some(
        (seat) => seat.function === "PR" && seat.position === "HEAD",
      ));
  const unitLabel =
    unit === "ALL"
      ? "Cả hai luồng"
      : unitName(unit, me.data.units.find((item) => item.code === unit)?.label);
  const first = board.data?.items[0];
  const cellHeads = first?.cells ?? [];
  // "Người làm" for the Design ban: its own people only.
  const everyone = designView
    ? designTeam(members.data?.members)
    : (members.data?.members.filter((member) => member.active) ?? []);
  // 2026-10-19: "Người làm" is picked in two steps - a department, then one
  // of its people. The departments on offer are the ones the listed people
  // sit in (`departments` on every member row); the person list narrows to
  // the chosen one. The department alone is kept on the URL (`dept`) so the
  // list stays narrowed; it is not sent to the server.
  const departments = (() => {
    const seen = new Map<string, string>();
    for (const member of everyone) {
      for (const seat of member.departments ?? []) seen.set(seat.department_id, seat.name);
    }
    return [...seen.entries()].map(([id, name]) => ({ id, name }));
  })();
  const dept = read("dept");
  const people = dept
    ? everyone.filter((member) => (member.departments ?? []).some((seat) => seat.department_id === dept))
    : everyone;
  // No "(n)" until the first counts arrive (or when they cannot be read).
  const countOf = (key: (typeof QUICK)[number][0]) => {
    const count = counts.data?.[key];
    return typeof count === "number" ? ` (${count})` : "";
  };
  const showTokens = effortEnabled(me.data);
  // What each column sorts by (a double click on its header). The step
  // columns are the row's own cells, so they sort by that step's status.
  const sortValues: Record<string, SortValueOf<TaskRow>> = {
    order: (row) => row.code,
    title: (row: TaskRow) => row.title,
    // The Design table draws the Design step's status and deadline: it sorts
    // by what is drawn.
    status: (row) => (designView ? designStatusLabel(row) : row.status_label),
    deadline: (row) => sortTime(designView ? designDeadline(row) : row.deadline_at),
    ...(designView
      ? {
          content: (row: TaskRow) => row.design?.script_by,
          codes: (row: TaskRow) => row.design?.codes?.[0]?.code,
          designer: (row: TaskRow) => [
            row.design?.designer,
            sortTime(row.design?.design_submitted_at),
          ],
        }
      : {
          ...Object.fromEntries(
            cellHeads.map((head) => [
              `step:${head.key}`,
              (row: TaskRow) => {
                const label = row.cells.find((cell) => cell.key === head.key)?.status_label;
                return label === "—" ? null : label;
              },
            ]),
          ),
          result: (row: TaskRow) => sortTime(row.delivered_at),
        }),
  };
  const rows = sortRows(board.data?.items ?? [], sort, sortValues);
  const selectedRows = rows.filter((row) => selected.has(row.id));
  const allOnPage = rows.length > 0 && rows.every((row) => selected.has(row.id));
  const toggleRow = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const togglePage = () =>
    setSelected(allOnPage ? new Set() : new Set(rows.map((row) => row.id)));
  // Keeps the page: sorting rearranges the rows on screen, nothing is refetched.
  const onSort = (key: string) =>
    setParams({ sort: nextSort(sort, key), page: read("page") });
  const refreshBoard = () =>
    void queryClient.invalidateQueries({ queryKey: ["board"] });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Quản lý task</h1>
            <UnitSwitch me={me.data} />
          </div>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {unitLabel} · {order === "todo_first" ? "Việc chờ bạn lên đầu" : "Ưu tiên lên đầu"} ·
            viền đậm là bước đang làm · bấm mã để mở chi tiết
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Độ dày"
            className="flex rounded-lg border border-[var(--border)] p-0.5"
          >
            {[
              ["", "Chi tiết"],
              ["compact", "Gọn"],
            ].map(([value, label]) => (
              <button
                key={label}
                type="button"
                aria-pressed={dense === (value === "compact")}
                onClick={() => setParams({ view: value })}
                className={`min-h-9 rounded-md px-3 text-xs ${
                  dense === (value === "compact")
                    ? "bg-[var(--text)] font-medium text-[var(--surface)]"
                    : "text-[var(--text-muted)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {unit !== "ADS" ? (
            <Link
              href="/pr/content"
              className="inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] px-3 text-xs hover:bg-[var(--surface-muted)]"
            >
              Bảng PR chi tiết
            </Link>
          ) : null}
          <Link
            href={unit === "ALL" ? "/orders/new" : `/orders/new?unit=${unit}`}
            className="inline-flex min-h-10 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            + Tạo order
          </Link>
        </div>
      </div>

      <div className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Lọc nhanh"
            className="flex flex-wrap gap-1.5"
          >
            {QUICK.filter(([key]) => key !== "overdue" || unit !== "PR").map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={isOn(key)}
                onClick={() => toggle(key)}
                className={`min-h-9 rounded-full border px-3.5 text-xs font-medium ${
                  isOn(key)
                    ? "border-[var(--text)] bg-[var(--text)] text-[var(--surface)]"
                    : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:text-[var(--text)]"
                }`}
              >
                {label}
                {countOf(key)}
              </button>
            ))}
          </div>
          <form
            className="ml-auto flex min-w-[16rem] flex-1 items-center gap-2 sm:max-w-sm"
            onSubmit={(event) => {
              event.preventDefault();
              setParams({ q: search });
            }}
          >
            <input
              type="search"
              aria-label="Tìm theo mã hoặc tên"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tìm mã, tên kịch bản…"
              className={`${field} w-full`}
            />
            <button type="submit" className={`${field} shrink-0`}>
              Tìm
            </button>
          </form>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-[repeat(2,minmax(9.5rem,1.2fr))_repeat(6,minmax(0,1fr))]">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Từ ngày
            <DateInput
              value={read("from")}
              onChange={(event) => setParams({ from: event.target.value })}
              className={field}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Đến ngày
            <DateInput
              value={read("to")}
              onChange={(event) => setParams({ to: event.target.value })}
              className={field}
            />
          </label>
          {designView ? (
            <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
              Trạng thái
              <DesignStatusSelect
                value={read("design_status")}
                states={board.data?.design_states}
                onChange={(value) => setParams({ design_status: value })}
                className="min-h-10"
              />
            </label>
          ) : (
            <>
              <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                Pha
                {unit === "ADS" ? (
                  <Select
                    value={read("step")}
                    onChange={(event) => setParams({ step: event.target.value })}
                    className="min-h-10"
                  >
                    <option value="">Tất cả</option>
                    {(board.data?.steps?.length ? board.data.steps : ADS_STEPS).map(
                      (step) => (
                        <option key={step.value} value={step.value}>
                          {step.label}
                        </option>
                      ),
                    )}
                  </Select>
                ) : (
                  <Select
                    value={read("phase")}
                    onChange={(event) => setParams({ phase: event.target.value })}
                    className="min-h-10"
                  >
                    <option value="">Tất cả</option>
                    {(board.data?.phases ?? []).map((phase) => (
                      <option key={phase.value} value={phase.value}>
                        {phase.label}
                      </option>
                    ))}
                  </Select>
                )}
              </label>
              {unit === "ADS" ? (
                <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                  Trạng thái task
                  <Select
                    value={read("state")}
                    onChange={(event) => setParams({ state: event.target.value })}
                    className="min-h-10"
                  >
                    <option value="">Tất cả</option>
                    {(board.data?.task_states?.length ? board.data.task_states : TASK_STATES).map(
                      (state) => (
                        <option key={state.value} value={state.value}>
                          {state.label}
                        </option>
                      ),
                    )}
                  </Select>
                </label>
              ) : null}
            </>
          )}
          {unit !== "ALL" ? (
            <>
              {designView || unit === "ADS" ? null : (
                <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                  Người phụ trách
                  <Select
                    value={read("owner")}
                    onChange={(event) => setParams({ owner: event.target.value })}
                    className="min-h-10"
                  >
                    <option value="">Tất cả</option>
                    {people.map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.full_name}
                      </option>
                    ))}
                  </Select>
                </label>
              )}
              {departments.length > 0 ? (
                <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                  Phòng ban
                  <Select
                    value={dept}
                    onChange={(event) => {
                      const next = event.target.value;
                      const chosen = people.find((member) => member.user_id === read("assignee"));
                      // A person outside the new department is no longer on offer.
                      const keep =
                        chosen &&
                        (!next ||
                          (chosen.departments ?? []).some((seat) => seat.department_id === next));
                      setParams({ dept: next, assignee: keep ? read("assignee") : "" });
                    }}
                    className="min-h-10"
                  >
                    <option value="">Tất cả phòng</option>
                    {departments.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </Select>
                </label>
              ) : null}
              <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                {unit === "ADS" ? "Người làm" : "Người dựng"}
                <Select
                  value={read("assignee")}
                  onChange={(event) =>
                    setParams({ assignee: event.target.value })
                  }
                  className="min-h-10"
                >
                  <option value="">Tất cả</option>
                  {people.map((member) => (
                    <option key={member.user_id} value={member.user_id}>
                      {member.full_name}
                    </option>
                  ))}
                </Select>
              </label>
            </>
          ) : null}
          <div className="flex items-end">
            <button
              type="button"
              onClick={() => {
                setSearch("");
                // The filters go; how the table is shown (page size, sort) stays.
                const kept = new URLSearchParams({ unit });
                for (const key of ["size", "sort"]) {
                  if (read(key)) kept.set(key, read(key));
                }
                router.replace(`${pathname}?${kept.toString()}`, { scroll: false });
              }}
              className={`${field} w-full text-[var(--text-muted)]`}
            >
              Xoá lọc{activeFilters ? ` (${activeFilters})` : ""}
            </button>
          </div>
        </div>
      </div>

      {board.isPending ? <Loading label="Đang tải danh sách…" /> : null}
      {board.isError ? (
        <ErrorBox error={board.error} onRetry={() => board.refetch()} />
      ) : null}
      {board.data && board.data.items.length === 0 ? (
        <Empty message="Không có order nào khớp bộ lọc." />
      ) : null}
      {board.data && board.data.items.length > 0 ? (
        <section className="panel">
          {/* Sideways only: the page keeps the one vertical scroll (no height
              limit here), and a table wider than the screen scrolls left-right
              inside the panel instead of being clipped by the page. */}
          <div className="table-scroll-x">
            <table className="table-dense min-w-[78rem]">
              <thead>
                <tr>
                  {selecting ? (
                    <th scope="col" className="w-8">
                      <input
                        type="checkbox"
                        aria-label="Chọn tất cả trên trang"
                        checked={allOnPage}
                        onChange={togglePage}
                        className="size-4 accent-[var(--accent)]"
                      />
                    </th>
                  ) : null}
                  <SortHeader sortKey="order" sort={sort} onSort={onSort}>
                    Order
                  </SortHeader>
                  <SortHeader sortKey="title" sort={sort} onSort={onSort}>
                    Tên · Loại
                  </SortHeader>
                  <SortHeader sortKey="status" sort={sort} onSort={onSort}>
                    Trạng thái · Đang giữ
                  </SortHeader>
                  {unit !== "PR" ? (
                    <SortHeader sortKey="deadline" sort={sort} onSort={onSort}>
                      Deadline
                    </SortHeader>
                  ) : null}
                  {designView ? (
                    <>
                      <SortHeader sortKey="content" sort={sort} onSort={onSort}>
                        Nội dung
                      </SortHeader>
                      <SortHeader sortKey="codes" sort={sort} onSort={onSort}>
                        Tên task
                      </SortHeader>
                      <SortHeader sortKey="designer" sort={sort} onSort={onSort}>
                        Thiết kế
                      </SortHeader>
                    </>
                  ) : (
                    <>
                      {cellHeads.map((cell) => (
                        <SortHeader
                          key={cell.key}
                          sortKey={`step:${cell.key}`}
                          sort={sort}
                          onSort={onSort}
                        >
                          {cell.label}
                        </SortHeader>
                      ))}
                      <SortHeader sortKey="result" sort={sort} onSort={onSort}>
                        Kết quả
                      </SortHeader>
                    </>
                  )}
                  <th scope="col">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <TaskTableRow
                    key={`${row.unit}:${row.id}`}
                    row={row}
                    showUnit={unit === "ALL"}
                    showDeadline={unit !== "PR"}
                    viewerId={viewerId}
                    decides={decides}
                    dense={dense}
                    showTokens={showTokens}
                    design={designView}
                    onChanged={refreshBoard}
                    selectable={
                      selecting
                        ? { checked: selected.has(row.id), onToggle: () => toggleRow(row.id) }
                        : undefined
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            page={page}
            total={board.data.total}
            shown={board.data.items.length}
            pageSize={size}
            // A new page size starts again from page 1 (`setParams` drops `page`).
            onPageSize={(next) => setParams({ size: next === 10 ? "" : String(next) })}
            sorted={sortApplies(sort, sortValues)}
            onPage={(next) => setParams({ page: next === 1 ? "" : String(next) })}
          />
        </section>
      ) : null}

      {bulkNotice ? (
        <p
          role="status"
          className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-sm"
        >
          {bulkNotice}
        </p>
      ) : null}
      {selecting && selectedRows.length > 0 ? (
        <TaskBulkBar
          count={selectedRows.length}
          approvable={selectedRows.filter((row) => PR_GATES.has(row.status)).length}
          pending={bulkApprove.isPending}
          error={bulkApprove.error}
          onApprove={() => bulkApprove.mutate(selectedRows)}
          onClear={() => setSelected(new Set())}
        />
      ) : null}

      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
        <span>Chú thích:</span>
        {STEP_LEGEND.map(([code, label]) => (
          <StatusBadge key={code} color={stepColor(code)}>
            {label}
          </StatusBadge>
        ))}
        <Pill tone="critical">Gấp</Pill>
      </p>
    </div>
  );
}

function Cell({
  cell,
  dense,
  stage,
  showTokens,
}: {
  cell: TaskCell;
  dense: boolean;
  stage: string;
  /** Off when ORD does not count effort: no token badge on a step. */
  showTokens: boolean;
}) {
  const skipped = cell.status === "BO_QUA";
  const tokens = showTokens ? cell.tokens : null;
  return (
    <td>
      <div
        className={`flex w-[9.5rem] flex-col items-start gap-1 rounded-lg px-1.5 py-1 ${
          cell.is_current
            ? "border-2 border-[var(--text)]"
            : "border-2 border-transparent"
        } ${skipped ? "opacity-50" : ""}`}
      >
        <span
          className={
            cell.person_name
              ? "person text-[13px] leading-snug"
              : cell.is_current && !skipped
                ? "text-xs font-semibold text-[var(--warn)]"
                : "text-xs text-[var(--text-muted)]"
          }
        >
          {cell.person_name ? (
            <PersonLink userId={cell.person_user_id} name={cell.person_name} />
          ) : skipped ? (
            "—"
          ) : cell.is_current ? (
            "Chờ giao"
          ) : (
            "–"
          )}
        </span>
        {cell.status_label === "—" ? null : (
          <StatusBadge color={stepColor(cell.status, stage, cell.status_label)}>
            {withoutNames(cell.status_label, cell.person_name)}
          </StatusBadge>
        )}
        {!dense && (tokens != null || cell.deadline_at) ? (
          <span className="flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-muted)]">
            <TokenBadge value={tokens} size="sm" />
            <DeadlineBadge at={cell.deadline_at} status={cell.deadline_status} short />
          </span>
        ) : null}
        {!dense && (cell.since || cell.revisions > 0) ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {cell.since ? formatAgo(cell.since) : ""}
            {cell.revisions > 0
              ? `${cell.since ? " · " : ""}sửa ${cell.revisions}`
              : ""}
          </span>
        ) : null}
      </div>
    </td>
  );
}

function TaskTableRow({
  row,
  showUnit,
  showDeadline,
  viewerId,
  decides,
  dense,
  showTokens,
  design = false,
  onChanged,
  selectable,
}: {
  row: TaskRow;
  showUnit: boolean;
  /** The "Deadline" column (not on the PR-only table: PR has no deadlines). */
  showDeadline: boolean;
  /** The signed-in person, for "Cần làm" when the API sends no `awaiting_me`. */
  viewerId: string | null;
  decides: boolean;
  dense: boolean;
  showTokens: boolean;
  /**
   * The Design ban's view: "Nội dung", "Tên task" and "Thiết kế" in place of
   * the per-step cells and "Kết quả" - and the step's own buttons ("Nhận
   * việc", "Nộp bài", "Duyệt") in the action cell. It keeps "Tên · Loại" (the
   * order's name and type), just without the row's extra fields.
   */
  design?: boolean;
  onChanged: () => void;
  /** The Trưởng phòng PR's checkbox on this row (2026-10-19). */
  selectable?: { checked: boolean; onToggle: () => void };
}) {
  const rowNavigation = useRowNavigation();
  const decide = useMutation({
    mutationFn: ({ path, note }: { path: string; note?: string }) =>
      api.orderAction(row.id, path, {
        version: row.version,
        ...(note ? { note } : {}),
      }),
    onSuccess: onChanged,
  });
  const pending =
    row.unit === "ADS" && row.status === "ORDER_PENDING" && decides;
  // Waiting on the viewer: the server's flag, else "the holder is me".
  const todo =
    row.awaiting_me ??
    (Boolean(viewerId) && row.current_person_user_id === viewerId);
  const opens = rowNavigation(row.detail_path);
  return (
    <tr
      onClick={opens.onClick}
      className={`${opens.className}${row.is_priority ? " is-priority" : ""}`}
    >
      {selectable ? (
        // A click here ticks the row and never opens the task.
        <td {...NO_ROW_NAV} className="w-8 align-top">
          <input
            type="checkbox"
            aria-label={`Chọn ${row.code}`}
            checked={selectable.checked}
            onChange={selectable.onToggle}
            className="mt-1 size-4 accent-[var(--accent)]"
          />
        </td>
      ) : null}
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          {showUnit ? (
            <span className={`unit-tag ${unitTagClass(row.unit)}`}>
              {unitShortLabel(row.unit, row.unit_short_label)}
            </span>
          ) : null}
          <Link
            href={row.detail_path}
            className="font-mono text-xs font-semibold text-[var(--accent)]"
          >
            {row.code}
          </Link>
          {todo ? (
            <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-semibold text-[var(--accent-strong)]">
              Cần làm
            </span>
          ) : null}
        </div>
        <span className="block text-xs">
          <PersonLink userId={row.owner_user_id} name={row.owner_name} className="person" />
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-1">
          <span className="date-chip" title={`Ngày order ${formatDateTime(row.created_at)}`}>
            <CalendarIcon />
            {formatShortDateTime(row.created_at)}
          </span>
          {!dense ? (
            <span className="text-[11px] text-[var(--text-muted)]">
              {formatAgo(row.created_at)}
            </span>
          ) : null}
        </span>
      </td>
      <td className="min-w-[12rem] max-w-[24rem]">
        <Link
          href={row.detail_path}
          className="line-clamp-2 font-medium leading-snug"
        >
          {row.title}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <span
            title={row.kind_label}
            className="rounded bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[11px] font-semibold"
          >
            {row.kind || "–"}
          </span>
          {!dense ? (
            <span className="text-[11px] text-[var(--text-muted)]">
              {row.kind_label}
            </span>
          ) : null}
          {row.is_priority ? <Pill tone="warn">Ưu tiên</Pill> : null}
          {row.urgent ? <Pill tone="critical">Gấp</Pill> : null}
          {/* The Design view keeps this column to just the name and type. */}
          {!dense && !design
            ? (row.extras ?? []).map((extra) => (
                <span
                  key={extra.label}
                  className="text-[11px] text-[var(--text-muted)]"
                >
                  {extra.label}: {extra.value}
                </span>
              ))
            : null}
        </div>
      </td>
      {design ? (
        <DesignStatusCell row={row} dense={dense} />
      ) : (
        <RowStatusCell row={row} dense={dense} />
      )}
      {showDeadline ? (
        design ? (
          <DesignDeadlineCell row={row} />
        ) : (
          <RowDeadlineCell row={row} />
        )
      ) : null}
      {design ? (
        <>
          <DesignContentCell row={row} />
          <DesignCodesCell row={row} />
          <DesignerCell row={row} />
        </>
      ) : (
        <ResultCells row={row} dense={dense} showTokens={showTokens} />
      )}
      {/* The action cell: a click in here never opens the task. */}
      <td {...NO_ROW_NAV}>
        <div className="flex flex-wrap items-center justify-end gap-1.5 whitespace-nowrap">
          {design ? <RowActions row={row} onChanged={onChanged} /> : null}
          {pending ? (
            <>
              <ConfirmButton
                spec={{
                  title: `Duyệt order ${row.code}?`,
                  description:
                    "Order sẽ chuyển sang công đoạn đầu tiên theo quy trình.",
                  confirmLabel: "Duyệt",
                }}
                onConfirm={() => decide.mutate({ path: "/approve" })}
                pending={decide.isPending}
                error={decide.error}
                tone="primary"
              >
                Duyệt
              </ConfirmButton>
              <ReturnButton
                code={row.code}
                pending={decide.isPending}
                error={decide.error}
                onConfirm={(note) => decide.mutate({ path: "/return", note })}
              />
            </>
          ) : null}
        </div>
        {decide.isError ? <NoticeBox error={decide.error} /> : null}
      </td>
    </tr>
  );
}

/** The per-step cells and "Kết quả": everything a row shows outside the Design view. */
function ResultCells({
  row,
  dense,
  showTokens,
}: {
  row: TaskRow;
  dense: boolean;
  showTokens: boolean;
}) {
  return (
    <>
      {row.cells.map((cell) => (
        <Cell
          key={cell.key}
          cell={cell}
          dense={dense}
          stage={row.status}
          showTokens={showTokens}
        />
      ))}
      <td className="whitespace-nowrap text-xs">
        {row.delivered_at ? (
          <span
            className="date-chip date-chip-done mb-1"
            title={`Ngày giao link hoàn thành ${formatDateTime(row.delivered_at)}`}
          >
            <LinkIcon />
            Giao link {formatShortDateTime(row.delivered_at)}
          </span>
        ) : null}
        {row.product_link ? (
          <a
            href={row.product_link}
            target="_blank"
            rel="noreferrer"
            className="block font-medium text-[var(--accent)]"
          >
            Sản phẩm ↗
          </a>
        ) : null}
        {row.latest_link ? (
          <a
            href={row.latest_link}
            target="_blank"
            rel="noreferrer"
            className="block text-[var(--accent)]"
          >
            Bản nộp mới nhất ↗
          </a>
        ) : null}
        <span className="mt-0.5 flex items-center gap-1 text-[var(--text-muted)]">
          Trả sửa{" "}
          {row.revisions > 0 ? (
            <Pill tone="bad">{row.revisions}</Pill>
          ) : (
            <span>0</span>
          )}
        </span>
        {row.returned_at ? (
          <span
            className="block text-[11px] text-[var(--text-muted)]"
            title={formatDateTime(row.returned_at)}
          >
            Hoàn thành {formatShortDateTime(row.returned_at)}
          </span>
        ) : null}
      </td>
    </>
  );
}

function ReturnButton({
  code,
  pending,
  error,
  onConfirm,
}: {
  code: string;
  pending: boolean;
  error: unknown;
  onConfirm: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <ConfirmButton
      spec={{
        title: `Không duyệt order ${code}?`,
        description:
          "Order quay về người order để sửa và gửi lại. Cần ghi lý do.",
        confirmLabel: "Không duyệt",
        variant: "destructive",
        details: (
          <label className="flex flex-col gap-1 text-xs">
            Lý do
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="min-h-20 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm text-[var(--text)]"
            />
          </label>
        ),
      }}
      onConfirm={() => onConfirm(note)}
      pending={pending}
      error={error}
      tone="danger"
    >
      Không duyệt
    </ConfirmButton>
  );
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" />
    </svg>
  );
}

/**
 * A step's badge sits under the step's person line, so a label that repeats
 * those names ("Chờ Phuong Nhung, Nguyen Nguyen duyệt") is shortened to
 * "Chờ duyệt": the names are right above it.
 */
function withoutNames(label: string, names: string | null): string {
  if (!names || !label.includes(names)) return label;
  return label.replace(` ${names} `, " ").replace(names, "").replace(/\s{2,}/g, " ").trim();
}
