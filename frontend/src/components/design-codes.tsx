"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DesignCode } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

const field =
  "min-h-9 w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

interface Draft {
  code: string;
  name: string;
  active: boolean;
  sort_order: string;
}

const draftOf = (row: DesignCode): Draft => ({
  code: row.code,
  name: row.name,
  active: row.active,
  sort_order: String(row.sort_order),
});

const validDraft = (draft: Draft) =>
  draft.code.trim() !== "" &&
  draft.name.trim() !== "" &&
  Number.isInteger(Number(draft.sort_order || "0"));

/** Every cache that reads the catalogue: this table and the pickers. */
const CATALOGUE_KEY = ["units", "ADS", "design-codes"];

/**
 * "Mã task design": the Design ban's catalogue of codes - what the Biên kịch
 * step picks from when handing in to Design, each with a quantity. Kept by the
 * Trưởng phòng Design (and OWNER / ADMIN): `design.manage_codes`.
 *
 * Nothing is ever deleted. A code is retired by switching "Đang dùng" off: it
 * leaves the pickers, and an order that already carries it keeps the code and
 * name it was picked with. Modelled on `VideoKindsManager`.
 */
export function DesignCodesManager() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const canManage = Boolean(me.data?.design?.manage_codes);
  const codes = useQuery({
    queryKey: [...CATALOGUE_KEY, "all"],
    queryFn: () => api.designCodes(true),
    enabled: canManage,
  });
  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  if (!canManage) {
    return (
      <p role="status" className="panel p-4 text-sm text-[var(--text-muted)]">
        Mã task design do Trưởng phòng Design quản lý. Bạn không có quyền chỉnh
        sửa bảng này.
      </p>
    );
  }
  return (
    <section
      aria-label="Mã task design"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <p className="text-xs text-[var(--text-muted)]">
        Biên kịch chọn mã (kèm số lượng) khi nộp bài sang Design. Tắt
        &quot;Đang dùng&quot; để ngừng một mã - order đã chọn giữ nguyên.
      </p>
      {codes.isPending ? (
        <div className="mt-3">
          <Loading label="Đang tải bảng mã task design…" />
        </div>
      ) : codes.isError ? (
        <div className="mt-3">
          <ErrorBox error={codes.error} onRetry={() => codes.refetch()} />
        </div>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="table-dense w-full min-w-[40rem]">
            <thead>
              <tr>
                <th scope="col" className="text-left">
                  Mã
                </th>
                <th scope="col" className="text-left">
                  Tên
                </th>
                <th scope="col">Đang dùng</th>
                <th scope="col">Thứ tự</th>
                <th scope="col">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...codes.data.codes]
                .sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code))
                .map((row) => (
                  <CodeRow key={row.id} row={row} />
                ))}
              <NewCodeRow
                nextOrder={
                  codes.data.codes.reduce(
                    (max, row) => Math.max(max, row.sort_order),
                    0,
                  ) + 1
                }
              />
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function CodeRow({ row }: { row: DesignCode }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(row));
  const saved = draftOf(row);
  const save = useMutation({
    mutationFn: () => {
      const body: Parameters<typeof api.updateDesignCode>[1] = {};
      if (draft.code.trim() !== saved.code) body.code = draft.code.trim();
      if (draft.name.trim() !== saved.name) body.name = draft.name.trim();
      if (draft.active !== saved.active) body.active = draft.active;
      if (Number(draft.sort_order) !== Number(saved.sort_order))
        body.sort_order = Number(draft.sort_order || "0");
      return api.updateDesignCode(row.id, body);
    },
    onSuccess: (updated) => {
      setDraft(draftOf(updated));
      void queryClient.invalidateQueries({ queryKey: CATALOGUE_KEY });
    },
  });
  const changed =
    draft.code.trim() !== saved.code ||
    draft.name.trim() !== saved.name ||
    draft.active !== saved.active ||
    Number(draft.sort_order) !== Number(saved.sort_order);
  const retiring = saved.active && !draft.active;
  return (
    <>
      <tr className={row.active ? undefined : "opacity-60"}>
        <td>
          <input
            aria-label={`Mã · ${row.code}`}
            value={draft.code}
            onChange={(event) =>
              setDraft((all) => ({ ...all, code: event.target.value }))
            }
            className={`${field} w-32 font-mono`}
          />
        </td>
        <td>
          <input
            aria-label={`Tên · ${row.code}`}
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td className="text-center">
          <input
            aria-label={`Đang dùng · ${row.code}`}
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label={`Thứ tự · ${row.code}`}
            type="number"
            step={1}
            value={draft.sort_order}
            onChange={(event) =>
              setDraft((all) => ({ ...all, sort_order: event.target.value }))
            }
            className={`${field} w-16`}
          />
        </td>
        <td className="text-right">
          <ConfirmButton
            spec={{
              title: retiring
                ? `Ngừng dùng mã "${saved.code}"?`
                : `Lưu mã task design "${draft.code.trim()}"?`,
              description: retiring
                ? "Mã không còn trong danh sách chọn khi nộp bài. Order đã chọn mã này giữ nguyên."
                : "Áp dụng cho các lần chọn mã từ giờ. Order đã chọn giữ mã và tên cũ.",
              confirmLabel: retiring ? "Ngừng dùng" : "Lưu",
            }}
            onConfirm={() => save.mutate()}
            onOpenChange={(open) => {
              if (open) save.reset();
            }}
            pending={save.isPending}
            error={save.error}
            disabled={!changed || !validDraft(draft)}
            ariaLabel={`Lưu ${row.code}`}
            tone="secondary"
          >
            Lưu
          </ConfirmButton>
        </td>
      </tr>
      {save.isError ? (
        <tr>
          <td colSpan={5}>
            <NoticeBox error={save.error} onDismiss={() => save.reset()} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewCodeRow({ nextOrder }: { nextOrder: number }) {
  const queryClient = useQueryClient();
  const empty = (): Draft => ({ code: "", name: "", active: true, sort_order: "" });
  const [draft, setDraft] = useState<Draft>(empty);
  const create = useMutation({
    mutationFn: () =>
      api.createDesignCode({
        code: draft.code.trim(),
        name: draft.name.trim(),
        active: draft.active,
        sort_order: Number(draft.sort_order || nextOrder),
      }),
    onSuccess: () => {
      setDraft(empty());
      void queryClient.invalidateQueries({ queryKey: CATALOGUE_KEY });
    },
  });
  return (
    <>
      <tr className="border-t-2 border-[var(--border)]">
        <td>
          <input
            aria-label="Mã task design mới"
            placeholder="Mã, vd. TKBTS"
            value={draft.code}
            onChange={(event) =>
              setDraft((all) => ({ ...all, code: event.target.value }))
            }
            className={`${field} w-32 font-mono`}
          />
        </td>
        <td>
          <input
            aria-label="Tên mã task design mới"
            placeholder="Thêm mã task design…"
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td className="text-center">
          <input
            aria-label="Đang dùng mã task design mới"
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label="Thứ tự mã task design mới"
            type="number"
            step={1}
            placeholder={String(nextOrder)}
            value={draft.sort_order}
            onChange={(event) =>
              setDraft((all) => ({ ...all, sort_order: event.target.value }))
            }
            className={`${field} w-16`}
          />
        </td>
        <td className="text-right">
          <ConfirmButton
            spec={{
              title: `Thêm mã task design "${draft.code.trim()}"?`,
              description: "Biên kịch chọn được ngay khi nộp bài sang Design.",
              confirmLabel: "Thêm",
            }}
            onConfirm={() => create.mutate()}
            onOpenChange={(open) => {
              if (open) create.reset();
            }}
            pending={create.isPending}
            error={create.error}
            disabled={!validDraft(draft)}
            tone="primary"
          >
            Thêm
          </ConfirmButton>
        </td>
      </tr>
      {create.isError ? (
        <tr>
          <td colSpan={5}>
            <NoticeBox error={create.error} onDismiss={() => create.reset()} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
