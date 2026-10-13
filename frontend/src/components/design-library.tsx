"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  api,
  type DesignLibraryLink,
  type DesignLibraryLinkBody,
} from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import {
  LIBRARY_ICON_MAX,
  LIBRARY_MAX_LINKS,
  LIBRARY_NAME_MAX,
  LibraryIcon,
  isLibraryUrl,
} from "@/components/library-icon";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

/**
 * "Quản lý thư viện": the Design team's "Thư viện" menu, edited as one list.
 *
 * Each entry is a name, a link out of the app and an icon (an emoji or a
 * letter or two; left empty, the name's initials are drawn). The list is saved
 * whole and in the order on screen (`PUT /api/units/ADS/library`): an entry
 * keeps its `id`, a new one has none, and one removed here is deleted there.
 * So nothing is sent until "Lưu" - which asks first, like the catalogues next
 * to it - and a save that goes through refreshes `/api/units/me`, so the
 * sidebar shows the new menu at once.
 *
 * The checks here mirror the server's (a name, an `http(s)` link, at most 30
 * entries) only to say early what is missing. The server's own answer is what
 * is shown when it refuses: its message, on the row and field it names
 * (`details.index`, `details.field`).
 *
 * Kept by the Trưởng phòng Design (and OWNER / ADMIN): `design.manage_library`.
 */

const field =
  "min-h-10 w-full rounded-lg border bg-[var(--surface)] px-2.5 text-sm text-[var(--text)] placeholder:text-[var(--text-muted)]";

const LIBRARY_KEY = ["units", "ADS", "library"];

interface Row {
  /** A local handle for React: the entry's id, or one made up for a new row. */
  key: string;
  id?: string;
  icon: string;
  name: string;
  url: string;
}

const rowOf = (link: DesignLibraryLink): Row => ({
  key: link.id,
  id: link.id,
  icon: link.icon ?? "",
  name: link.name,
  url: link.url,
});

/** The list as the server takes it: trimmed, an empty icon as null. */
function libraryBody(rows: Row[]): DesignLibraryLinkBody[] {
  return rows.map((row) => ({
    ...(row.id ? { id: row.id } : {}),
    name: row.name.trim(),
    url: row.url.trim(),
    icon: row.icon.trim() || null,
  }));
}

type FieldName = "name" | "url";

/** What is wrong with a row before it is sent, per field. */
function problemsOf(row: Row): Partial<Record<FieldName, string>> {
  const problems: Partial<Record<FieldName, string>> = {};
  if (row.name.trim() === "") problems.name = "Nhập tên.";
  if (row.url.trim() === "") problems.url = "Nhập link.";
  else if (!isLibraryUrl(row.url))
    problems.url = "Link phải bắt đầu bằng http:// hoặc https://.";
  return problems;
}

export function DesignLibraryManager() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const canManage = Boolean(me.data?.design?.manage_library);
  const library = useQuery({
    queryKey: LIBRARY_KEY,
    queryFn: api.designLibrary,
    enabled: canManage,
    // Read once: the editor below holds the list from then on, and a refetch
    // behind an edit in progress would have nothing to do with it.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (!canManage || library.data?.can_manage === false) {
    return (
      <p role="status" className="panel p-4 text-sm text-[var(--text-muted)]">
        Thư viện do Trưởng phòng Design quản lý. Bạn không có quyền chỉnh sửa
        danh sách này.
      </p>
    );
  }
  if (library.isPending) return <Loading label="Đang tải thư viện…" />;
  if (library.isError)
    return <ErrorBox error={library.error} onRetry={() => library.refetch()} />;
  return <LibraryEditor initial={library.data.links} />;
}

function LibraryEditor({ initial }: { initial: DesignLibraryLink[] }) {
  const queryClient = useQueryClient();
  // What the server holds, as last heard: the list this editor started from,
  // then whatever a save answered with.
  const [saved, setSaved] = useState<DesignLibraryLink[]>(initial);
  const [rows, setRows] = useState<Row[]>(() => initial.map(rowOf));
  const nextKey = useRef(0);
  const save = useMutation({
    mutationFn: () => api.saveDesignLibrary(libraryBody(rows)),
    onSuccess: async (stored) => {
      // What is on screen is now exactly what is stored (new rows have ids).
      setSaved(stored.links);
      setRows(stored.links.map(rowOf));
      queryClient.setQueryData(LIBRARY_KEY, stored);
      // The sidebar's menu is part of /api/units/me.
      await queryClient.invalidateQueries({ queryKey: ["units", "me"] });
    },
  });
  // Any edit answers the last refusal: it is about a list that is gone.
  const change = (next: (rows: Row[]) => Row[]) => {
    if (save.isError) save.reset();
    setRows(next);
  };
  const edit = (key: string, patch: Partial<Row>) =>
    change((all) => all.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  const remove = (key: string) => change((all) => all.filter((row) => row.key !== key));
  const move = (index: number, by: -1 | 1) =>
    change((all) => {
      const target = index + by;
      if (target < 0 || target >= all.length) return all;
      const next = [...all];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  const add = () =>
    change((all) => {
      nextKey.current += 1;
      return [...all, { key: `new-${nextKey.current}`, icon: "", name: "", url: "" }];
    });

  const problems = rows.map(problemsOf);
  const invalidRows = problems.filter((problem) => Object.keys(problem).length > 0).length;
  const full = rows.length >= LIBRARY_MAX_LINKS;
  const tooMany = rows.length > LIBRARY_MAX_LINKS;
  const changed =
    JSON.stringify(libraryBody(rows)) !== JSON.stringify(libraryBody(saved.map(rowOf)));

  // Where the server's refusal belongs: the row and field it names.
  const refusal = save.error instanceof ApiError ? save.error : null;
  const refusedIndex =
    refusal && typeof refusal.details?.index === "number" ? refusal.details.index : null;
  const refusedField =
    refusal && typeof refusal.details?.field === "string" ? refusal.details.field : null;
  const onRow = refusedIndex !== null && refusedIndex >= 0 && refusedIndex < rows.length;
  const serverProblem = (index: number, name: FieldName) =>
    refusal && onRow && refusedIndex === index && refusedField === name
      ? refusal.message
      : null;

  return (
    <section
      aria-label="Quản lý thư viện"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <p className="text-xs text-[var(--text-muted)]">
        Mỗi dòng là một mục trong menu Thư viện của ban Design: icon (một emoji
        hoặc 1–2 chữ cái; để trống thì lấy chữ cái đầu của tên), tên và link.
        Thứ tự ở đây là thứ tự trong menu. Thay đổi chỉ được lưu khi bấm Lưu.
      </p>

      {rows.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed border-[var(--border)] p-4 text-sm text-[var(--text-muted)]">
          Thư viện chưa có mục nào.
        </p>
      ) : (
        <ol className="mt-3 space-y-2">
          {rows.map((row, index) => {
            const n = index + 1;
            const blank = !row.name.trim() && !row.url.trim() && !row.icon.trim();
            const nameProblem = serverProblem(index, "name") ?? (blank ? null : problems[index].name);
            const urlProblem = serverProblem(index, "url") ?? (blank ? null : problems[index].url);
            return (
              <li
                key={row.key}
                data-testid="library-row"
                className="rounded-lg border border-[var(--border)] p-3"
              >
                <div className="flex flex-wrap items-start gap-2">
                  <span className="flex min-h-10 items-center">
                    <LibraryIcon name={row.name} icon={row.icon} size={32} />
                  </span>
                  <label className="flex w-24 flex-col gap-1 text-xs text-[var(--text-muted)]">
                    Icon
                    <input
                      aria-label={`Icon · dòng ${n}`}
                      value={row.icon}
                      maxLength={LIBRARY_ICON_MAX}
                      placeholder="N hoặc 🎨"
                      onChange={(event) => edit(row.key, { icon: event.target.value })}
                      className={`${field} border-[var(--border)]`}
                    />
                  </label>
                  <label className="flex min-w-[10rem] flex-1 flex-col gap-1 text-xs text-[var(--text-muted)]">
                    Tên
                    <input
                      aria-label={`Tên · dòng ${n}`}
                      value={row.name}
                      maxLength={LIBRARY_NAME_MAX}
                      aria-invalid={nameProblem ? true : undefined}
                      onChange={(event) => edit(row.key, { name: event.target.value })}
                      className={`${field} ${nameProblem ? "border-[var(--bad)]" : "border-[var(--border)]"}`}
                    />
                    {nameProblem ? (
                      <span role="alert" className="font-medium text-[var(--bad)]">
                        {nameProblem}
                      </span>
                    ) : null}
                  </label>
                  <label className="flex min-w-[14rem] flex-[2] flex-col gap-1 text-xs text-[var(--text-muted)]">
                    Link
                    <input
                      aria-label={`Link · dòng ${n}`}
                      type="url"
                      inputMode="url"
                      value={row.url}
                      placeholder="https://…"
                      aria-invalid={urlProblem ? true : undefined}
                      onChange={(event) => edit(row.key, { url: event.target.value })}
                      className={`${field} ${urlProblem ? "border-[var(--bad)]" : "border-[var(--border)]"}`}
                    />
                    {urlProblem ? (
                      <span role="alert" className="font-medium text-[var(--bad)]">
                        {urlProblem}
                      </span>
                    ) : null}
                  </label>
                  <div className="flex items-center gap-1 self-end">
                    <RowButton
                      label={`Chuyển dòng ${n} lên`}
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      ↑
                    </RowButton>
                    <RowButton
                      label={`Chuyển dòng ${n} xuống`}
                      disabled={index === rows.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      ↓
                    </RowButton>
                    <RowButton label={`Xoá dòng ${n}`} onClick={() => remove(row.key)}>
                      Xoá
                    </RowButton>
                  </div>
                </div>
                {blank ? (
                  <p className="mt-1.5 text-xs text-[var(--text-muted)]">
                    Nhập tên và link cho dòng này, hoặc xoá dòng.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={add}
            disabled={full}
            className="min-h-10 rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)] disabled:opacity-50"
          >
            + Thêm mục
          </button>
          <span className="text-xs text-[var(--text-muted)]">
            {rows.length} / {LIBRARY_MAX_LINKS} mục
          </span>
          {invalidRows > 0 ? (
            <span role="status" className="text-xs font-medium text-[var(--warn)]">
              Còn {invalidRows} dòng chưa đủ tên hoặc link hợp lệ.
            </span>
          ) : null}
        </div>
        <ConfirmButton
          spec={{
            title: `Lưu thư viện (${rows.length} mục)?`,
            description:
              "Menu Thư viện của ban Design sẽ hiện đúng danh sách và thứ tự này. Mục đã xoá khỏi danh sách sẽ biến mất khỏi menu.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          onOpenChange={(open) => {
            if (open) save.reset();
          }}
          pending={save.isPending}
          error={save.error}
          disabled={!changed || invalidRows > 0 || tooMany}
          tone="primary"
        >
          Lưu
        </ConfirmButton>
      </div>
      {/* A refusal that names no row (too many entries, no right) is said here;
          one that names a row is on that row. */}
      {save.isError && !(onRow && (refusedField === "name" || refusedField === "url")) ? (
        <div className="mt-3">
          <NoticeBox error={save.error} onDismiss={() => save.reset()} />
        </div>
      ) : null}
    </section>
  );
}

function RowButton({
  label,
  disabled = false,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-lg border border-[var(--border)] px-2.5 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] disabled:opacity-40"
    >
      {children}
    </button>
  );
}
