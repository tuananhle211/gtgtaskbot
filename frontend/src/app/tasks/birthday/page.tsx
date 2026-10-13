"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type BoardFilters, type TaskRow } from "@/lib/api";
import { formatBirthday, genderLabel } from "@/lib/birthday";
import { isUntagged } from "@/lib/units";
import { BirthdayOrderDialog } from "@/components/birthday-order-form";
import { DesignerCell, WebLink } from "@/components/design-cells";
import { Empty, ErrorBox, Loading } from "@/components/states";
import {
  PAGE_SIZE,
  Pager,
  RowDeadlineCell,
  RowStatusCell,
} from "@/components/task-table";
import { UntaggedState } from "@/components/unit-switch";

export default function BirthdayOrdersPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải bảng order sinh nhật…" />}>
      <BirthdayBoard />
    </Suspense>
  );
}

const field =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/** An order that is over: nothing on it can be edited any more. */
const CLOSED_STATUSES = new Set(["COMPLETED", "CANCELLED"]);

/**
 * "Order sinh nhật": the Design ban's birthday artwork, nearest birthday first.
 *
 * The same rows as the task table (`/api/board/tasks`), asked for ORD's
 * `BIRTHDAY` category in `birthday` order, and drawn for this one job: who the
 * artwork is for, where it stands, what to work from and what was handed in.
 * A row's code opens the ordinary task page, which is where it is handed out,
 * accepted and handed in. Creating and editing happen here, in a modal.
 *
 * Who may open this is the server's word (`design.birthday` on
 * `/api/units/me`); the page asks for nothing until it says yes. The page and
 * the two filters ("Cần làm", the search) live on the URL.
 */
function BirthdayBoard() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  // The Shell's own session query (same key): who "me" is, for "Chỉnh sửa".
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const read = (key: string) => params.get(key) ?? "";
  const allowed = Boolean(me.data?.design?.birthday);
  const page = Math.max(1, Number(read("page") || "1") || 1);
  const awaitingMe = read("awaiting_me") === "true";
  const filters: BoardFilters = {
    unit: "ADS",
    category: "BIRTHDAY",
    order: "birthday",
    awaiting_me: awaitingMe,
    q: read("q") || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };
  const board = useQuery({
    queryKey: ["board", "tasks", filters],
    queryFn: () => api.boardTasks(filters),
    enabled: allowed,
  });
  // The "(n)" on "Cần làm": the birthday rows waiting on the viewer, whatever
  // the search says. Refetched with every `["board"]` invalidation.
  const counts = useQuery({
    queryKey: ["board", "counts", "ADS", "BIRTHDAY"],
    queryFn: () => api.boardCounts("ADS", "BIRTHDAY"),
    enabled: allowed,
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
  });
  const [search, setSearch] = useState(read("q"));
  const [creating, setCreating] = useState(false);
  // The row being edited: held as it was when "Chỉnh sửa" was pressed, and
  // read fresh from the list while it is still on the page (its `version`).
  const [editing, setEditing] = useState<TaskRow | null>(null);

  const setParams = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in changes)) next.delete("page");
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname, { scroll: false });
  };

  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (isUntagged(me.data)) return <UntaggedState title="Order sinh nhật" />;
  if (!allowed) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Order sinh nhật</h1>
        <p role="status" className="panel p-4 text-sm text-[var(--text-muted)]">
          Bảng order sinh nhật dành cho ban Design và người lên order sinh nhật.
          Tài khoản của bạn chưa được mở bảng này.
        </p>
      </div>
    );
  }

  const viewerId = session.data?.user_id ?? null;
  const managesAll = Boolean(me.data.design?.manage_codes);
  const mayEdit = (row: TaskRow) =>
    !CLOSED_STATUSES.has(row.status) &&
    (managesAll || (Boolean(viewerId) && row.owner_user_id === viewerId));
  const awaiting = counts.data?.awaiting_me;
  const editingRow = editing
    ? (board.data?.items.find((item) => item.id === editing.id) ?? editing)
    : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Order sinh nhật</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Luồng Order (ORD) · sinh nhật gần nhất lên đầu · bấm mã để mở chi tiết
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex min-h-10 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
        >
          + Tạo order
        </button>
      </div>

      <div className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Lọc nhanh" className="flex flex-wrap gap-1.5">
            <button
              type="button"
              aria-pressed={awaitingMe}
              onClick={() => setParams({ awaiting_me: awaitingMe ? "" : "true" })}
              className={`min-h-9 rounded-full border px-3.5 text-xs font-medium ${
                awaitingMe
                  ? "border-[var(--text)] bg-[var(--text)] text-[var(--surface)]"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:text-[var(--text)]"
              }`}
            >
              Cần làm{typeof awaiting === "number" ? ` (${awaiting})` : ""}
            </button>
          </div>
          <form
            className="ml-auto flex min-w-[16rem] flex-1 items-center gap-2 sm:max-w-sm"
            onSubmit={(event) => {
              event.preventDefault();
              setParams({ q: search.trim() });
            }}
          >
            <input
              type="search"
              aria-label="Tìm theo mã hoặc họ tên"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tìm mã, họ tên…"
              className={`${field} w-full`}
            />
            <button type="submit" className={`${field} shrink-0`}>
              Tìm
            </button>
          </form>
        </div>
      </div>

      {board.isPending ? <Loading label="Đang tải danh sách…" /> : null}
      {board.isError ? (
        <ErrorBox error={board.error} onRetry={() => board.refetch()} />
      ) : null}
      {board.data && board.data.items.length === 0 ? (
        <Empty
          message={
            awaitingMe || read("q")
              ? "Không có order sinh nhật nào khớp bộ lọc."
              : "Chưa có order sinh nhật nào."
          }
        />
      ) : null}
      {board.data && board.data.items.length > 0 ? (
        <section className="panel">
          <div className="table-scroll-x">
            <table className="table-dense min-w-[72rem]">
              <thead>
                <tr>
                  <th scope="col">Mã</th>
                  <th scope="col">Chi nhánh</th>
                  <th scope="col">Họ và tên</th>
                  <th scope="col">Giới tính</th>
                  <th scope="col">Ngày sinh</th>
                  <th scope="col">Trạng thái · Đang giữ</th>
                  <th scope="col">Deadline</th>
                  <th scope="col">Nội dung</th>
                  <th scope="col">Thiết kế</th>
                  <th scope="col">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {board.data.items.map((row) => (
                  <BirthdayRow
                    key={row.id}
                    row={row}
                    viewerId={viewerId}
                    onEdit={mayEdit(row) ? () => setEditing(row) : undefined}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            page={page}
            total={board.data.total}
            shown={board.data.items.length}
            noun="order"
            onPage={(next) => setParams({ page: next === 1 ? "" : String(next) })}
          />
        </section>
      ) : null}

      <BirthdayOrderDialog open={creating} onClose={() => setCreating(false)} />
      <BirthdayOrderDialog
        // One form per row: opening another row starts from that row's values.
        key={editingRow?.id ?? "none"}
        open={Boolean(editingRow)}
        row={editingRow}
        onClose={() => setEditing(null)}
      />
    </div>
  );
}

function BirthdayRow({
  row,
  viewerId,
  onEdit,
}: {
  row: TaskRow;
  /** The signed-in person, for "Cần làm" when the API sends no `awaiting_me`. */
  viewerId: string | null;
  /** Set when the viewer may edit this order. */
  onEdit?: () => void;
}) {
  const person = row.birthday;
  const reference = row.design?.reference_link ?? null;
  const note = row.design?.note ?? null;
  // Waiting on the viewer: the server's flag, else "the holder is me".
  const todo =
    row.awaiting_me ?? (Boolean(viewerId) && row.current_person_user_id === viewerId);
  return (
    <tr className={row.is_priority ? "is-priority" : undefined}>
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
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
        <span className="person block text-xs">{row.owner_name}</span>
      </td>
      <td className="min-w-[8rem] max-w-[14rem] text-sm">{person?.branch || "–"}</td>
      <td className="min-w-[10rem] max-w-[18rem]">
        <Link href={row.detail_path} className="font-medium leading-snug">
          {person?.full_name || row.title}
        </Link>
      </td>
      <td className="whitespace-nowrap text-sm">{genderLabel(person?.gender)}</td>
      <td className="whitespace-nowrap text-sm font-medium tabular-nums">
        {formatBirthday(person?.birthday_on)}
      </td>
      <RowStatusCell row={row} />
      <RowDeadlineCell row={row} />
      <td className="min-w-[11rem] max-w-[18rem] text-xs" data-testid="birthday-content">
        {reference ? <WebLink href={reference} label="Ảnh minh hoạ" /> : null}
        {note ? (
          <span className="mt-0.5 block whitespace-pre-wrap leading-snug text-[var(--text-muted)]">
            {note}
          </span>
        ) : null}
        {!reference && !note ? <span className="text-[var(--text-muted)]">–</span> : null}
      </td>
      <DesignerCell row={row} />
      <td>
        <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
          {onEdit ? (
            <button
              type="button"
              onClick={onEdit}
              aria-label={`Chỉnh sửa ${row.code}`}
              className="inline-flex min-h-9 items-center rounded-lg border border-[var(--border)] px-2.5 text-xs hover:bg-[var(--surface-muted)]"
            >
              Chỉnh sửa
            </button>
          ) : null}
          <Link
            href={row.detail_path}
            className="inline-flex min-h-9 items-center rounded-lg border border-[var(--border)] px-2.5 text-xs"
          >
            Mở
          </Link>
        </div>
      </td>
    </tr>
  );
}
