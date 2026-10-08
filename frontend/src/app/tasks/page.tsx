"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type TaskCell, type TaskRow } from "@/lib/api";
import { UnitSwitch } from "@/components/unit-switch";
import { formatAgo, formatShortDay } from "@/lib/labels";
import { PROCESS_CODES, processCodeLabel } from "@/lib/ads-process";
import { currentUnit, unitEntry } from "@/lib/units";
import { ConfirmButton } from "@/components/confirm";
import { Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { STEP_LEGEND, type StatusColor, stageColor, stepColor } from "@/lib/status-colors";

const PAGE_SIZE = 10;

/** The Ads "Pha" filter, until the server's own list arrives. */
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

const QUICK = [
  ["mine", "Việc của tôi"],
  ["awaiting_me", "Chờ tôi xử lý"],
  ["priority", "Ưu tiên"],
  ["urgent", "Gấp"],
] as const;


const field =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/**
 * The task table: one unit, many filters, every filter on the URL.
 *
 * Rows come from `/api/board/tasks` already shaped for the screen - phase,
 * cells, labels, who holds it and since when - so this file draws and never
 * interprets. The two inline decisions a head makes on an Ads row (approve or
 * return the order) go through the same `/api/orders` actions as the detail.
 */
function TaskBoard() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const unit = currentUnit(params, me.data);
  const read = (key: string) => params.get(key) ?? "";
  const page = Number(read("page") || "1");
  const dense = read("view") === "compact";
  const filters = {
    unit,
    date_from: read("from") || undefined,
    date_to: read("to") || undefined,
    // Ads filters "Pha" by its four steps (Order, Biên tập, Thiết kế, Dựng);
    // PR and the merged view by the five shared phases.
    phase: unit === "ADS" ? undefined : read("phase") || undefined,
    step: unit === "ADS" ? read("step") || undefined : undefined,
    kind: read("kind") || undefined,
    video_kind_id: unit === "ADS" ? read("video_kind_id") || undefined : undefined,
    owner: read("owner") || undefined,
    assignee: read("assignee") || undefined,
    person: read("person") || undefined,
    mine: read("mine") === "true",
    awaiting_me: read("awaiting_me") === "true",
    priority: read("priority") === "true",
    urgent: read("urgent") === "true",
    q: read("q") || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };
  const board = useQuery({
    queryKey: ["board", "tasks", filters],
    queryFn: () => api.boardTasks(filters),
    enabled: me.isSuccess,
  });
  const members = useQuery({
    queryKey: ["units", unit, "members"],
    queryFn: () => api.unitMembers(unit),
    enabled: me.isSuccess && unit !== "ALL",
  });
  // The "Loại video" filter: the unit's active kinds, readable by any member.
  const videoKinds = useQuery({
    queryKey: ["units", "ADS", "video-kinds"],
    queryFn: () => api.unitVideoKinds("ADS"),
    enabled: me.isSuccess && unit === "ADS",
  });
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
  const toggle = (key: string) =>
    setParams({ [key]: read(key) === "true" ? "" : "true" });
  const activeFilters = [
    "from",
    "to",
    "phase",
    "step",
    "kind",
    "video_kind_id",
    "owner",
    "assignee",
    "person",
    "q",
  ].filter((key) => read(key)).length;

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;

  const entry = unitEntry(me.data, "ADS");
  const decides =
    unit === "ADS" && (me.data.can_view_all || entry?.role === "HEAD");
  const unitLabel =
    unit === "ALL"
      ? "Cả hai ban"
      : (me.data.units.find((item) => item.code === unit)?.label ?? unit);
  const first = board.data?.items[0];
  const cellHeads = first?.cells ?? [];
  const people = members.data?.members.filter((member) => member.active) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Quản lý task</h1>
            <UnitSwitch me={me.data} />
          </div>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {unitLabel} · Ưu tiên lên đầu · viền đậm là bước đang làm · bấm mã
            để mở chi tiết
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
            {QUICK.map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={read(key) === "true"}
                onClick={() => toggle(key)}
                className={`min-h-9 rounded-full border px-3.5 text-xs font-medium ${
                  read(key) === "true"
                    ? "border-[var(--text)] bg-[var(--text)] text-[var(--surface)]"
                    : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:text-[var(--text)]"
                }`}
              >
                {label}
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
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Từ ngày
            <input
              type="date"
              value={read("from")}
              onChange={(event) => setParams({ from: event.target.value })}
              className={field}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Đến ngày
            <input
              type="date"
              value={read("to")}
              onChange={(event) => setParams({ to: event.target.value })}
              className={field}
            />
          </label>
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
            <>
              <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                Quy trình
                <Select
                  value={read("kind")}
                  onChange={(event) => setParams({ kind: event.target.value })}
                  className="min-h-10"
                >
                  <option value="">Tất cả</option>
                  {PROCESS_CODES.map((code) => (
                    <option key={code} value={code}>
                      {`${code} · ${processCodeLabel(code)}`}
                    </option>
                  ))}
                </Select>
              </label>
              {(videoKinds.data?.kinds.length ?? 0) > 0 ? (
                <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                  Loại video
                  <Select
                    value={read("video_kind_id")}
                    onChange={(event) =>
                      setParams({ video_kind_id: event.target.value })
                    }
                    className="min-h-10"
                  >
                    <option value="">Tất cả</option>
                    {videoKinds.data!.kinds.map((kind) => (
                      <option key={kind.id} value={kind.id}>
                        {kind.name}
                      </option>
                    ))}
                  </Select>
                </label>
              ) : null}
            </>
          ) : null}
          {unit !== "ALL" ? (
            <>
              <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
                {unit === "ADS" ? "Marketing" : "Người phụ trách"}
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
                router.replace(`${pathname}?unit=${unit}`, { scroll: false });
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
                  <th scope="col">Order</th>
                  <th scope="col">Tên · Loại</th>
                  <th scope="col">Trạng thái · Đang giữ</th>
                  {cellHeads.map((cell) => (
                    <th key={cell.key} scope="col">
                      {cell.label}
                    </th>
                  ))}
                  <th scope="col">Kết quả</th>
                  <th scope="col">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {board.data.items.map((row) => (
                  <TaskTableRow
                    key={`${row.unit}:${row.id}`}
                    row={row}
                    showUnit={unit === "ALL"}
                    decides={decides}
                    dense={dense}
                    onChanged={() =>
                      void queryClient.invalidateQueries({
                        queryKey: ["board"],
                      })
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
            onPage={(next) => setParams({ page: next === 1 ? "" : String(next) })}
          />
        </section>
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

function Cell({ cell, dense, stage }: { cell: TaskCell; dense: boolean; stage: string }) {
  const skipped = cell.status === "BO_QUA";
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
          {cell.person_name ??
            (skipped ? "—" : cell.is_current ? "Chờ giao" : "–")}
        </span>
        {cell.status_label === "—" ? null : (
          <StatusBadge color={stepColor(cell.status, stage)}>
            {withoutNames(cell.status_label, cell.person_name)}
          </StatusBadge>
        )}
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
  decides,
  dense,
  onChanged,
}: {
  row: TaskRow;
  showUnit: boolean;
  decides: boolean;
  dense: boolean;
  onChanged: () => void;
}) {
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
  return (
    <tr className={row.is_priority ? "is-priority" : undefined}>
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          {showUnit ? (
            <span
              className={`unit-tag ${row.unit === "ADS" ? "unit-tag-ads" : "unit-tag-pr"}`}
            >
              {row.unit}
            </span>
          ) : null}
          <Link
            href={row.detail_path}
            className="font-mono text-xs font-semibold text-[var(--accent)]"
          >
            {row.code}
          </Link>
        </div>
        <span className="person block text-xs">{row.owner_name}</span>
        <span className="mt-1 flex flex-wrap items-center gap-1">
          <span className="date-chip" title="Ngày order">
            <CalendarIcon />
            {formatShortDay(row.created_at)}
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
          {!dense
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
      <td className="w-[15rem] min-w-[13rem] max-w-[17rem]">
        <StatusBadge color={stageColor(row.status)}>
          {boldNames(row.status_label, row.current_person_name)}
        </StatusBadge>
        {!dense && row.stage_since ? (
          <span className="block text-[11px] text-[var(--text-muted)]">
            ở bước này {formatAgo(row.stage_since)}
          </span>
        ) : null}
        {/* Skipped when the status already names the same people. */}
        {row.current_person_name && row.status_label.includes(row.current_person_name) ? null : (
          <span className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
            <span className="text-[var(--text-muted)]">Đang giữ:</span>
            {row.awaiting_assignment ? (
              <Pill tone="warn">Chờ giao</Pill>
            ) : (
              <span className="person">{row.current_person_name ?? "–"}</span>
            )}
          </span>
        )}
      </td>
      {row.cells.map((cell) => (
        <Cell key={cell.key} cell={cell} dense={dense} stage={row.status} />
      ))}
      <td className="whitespace-nowrap text-xs">
        {row.delivered_at ? (
          <span className="date-chip date-chip-done mb-1" title="Ngày giao link hoàn thành">
            <LinkIcon />
            Giao link {formatShortDay(row.delivered_at)}
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
          <span className="block text-[11px] text-[var(--text-muted)]">
            Hoàn thành {formatShortDay(row.returned_at)}
          </span>
        ) : null}
      </td>
      <td>
        <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
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
          <Link
            href={row.detail_path}
            className="inline-flex min-h-9 items-center rounded-lg border border-[var(--border)] px-2.5 text-xs"
          >
            Mở
          </Link>
        </div>
        {decide.isError ? <NoticeBox error={decide.error} /> : null}
      </td>
    </tr>
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

/**
 * Page numbers under the table: first, previous, a window of numbers around
 * the current page with ellipses, next, last. The range shown ("21–40 / 1223")
 * says where the visible rows sit in the whole list.
 */
function Pager({
  page,
  total,
  shown,
  onPage,
}: {
  page: number;
  total: number;
  shown: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = (page - 1) * PAGE_SIZE + shown;
  const numbers: Array<number | "gap"> = [];
  for (let n = 1; n <= pages; n += 1) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 2) numbers.push(n);
    else if (numbers[numbers.length - 1] !== "gap") numbers.push("gap");
  }
  const button =
    "inline-flex min-h-9 min-w-9 items-center justify-center rounded-lg border border-[var(--border)] px-2.5 text-xs disabled:opacity-40";
  return (
    <nav
      aria-label="Phân trang"
      className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--text-muted)]"
    >
      <span>
        {first}–{last} / {total} task · {PAGE_SIZE} dòng mỗi trang
      </span>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(1)} aria-label="Trang đầu">
          «
        </button>
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Trước
        </button>
        {numbers.map((n, index) =>
          n === "gap" ? (
            <span key={`gap-${index}`} className="px-1">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              aria-current={n === page ? "page" : undefined}
              onClick={() => onPage(n)}
              className={`${button} ${
                n === page
                  ? "border-[var(--accent)] bg-[var(--accent)] font-semibold text-[var(--accent-text)]"
                  : "bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
              }`}
            >
              {n}
            </button>
          ),
        )}
        <button type="button" className={button} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Sau
        </button>
        <button type="button" className={button} disabled={page >= pages} onClick={() => onPage(pages)} aria-label="Trang cuối">
          »
        </button>
      </div>
    </nav>
  );
}

function StatusBadge({ color, children }: { color: StatusColor; children: React.ReactNode }) {
  // One inner span: the badge is a flex box (dot + text), and without it each
  // text piece and <strong> would become its own flex column.
  return (
    <span className={`st st-${color}`}>
      <span>{children}</span>
    </span>
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

/** The status text with the people's names in bold and everything else plain. */
function boldNames(label: string, names: string | null): React.ReactNode {
  if (!names || !label.includes(names)) return label;
  const at = label.indexOf(names);
  return (
    <>
      {label.slice(0, at)}
      <strong className="font-bold">{names}</strong>
      {label.slice(at + names.length)}
    </>
  );
}
