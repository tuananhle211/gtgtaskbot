"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type UnitVideoKind } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

const field =
  "min-h-9 w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

interface Draft {
  name: string;
  points: string;
  active: boolean;
  sort_order: string;
}

const draftOf = (kind: UnitVideoKind): Draft => ({
  name: kind.name,
  points: String(Number(kind.points)),
  active: kind.active,
  sort_order: String(kind.sort_order),
});

const validDraft = (draft: Draft) =>
  draft.name.trim() !== "" &&
  draft.points.trim() !== "" &&
  Number.isFinite(Number(draft.points)) &&
  Number(draft.points) >= 0 &&
  Number.isInteger(Number(draft.sort_order || "0"));

/**
 * "Loại video & điểm hiệu suất": the unit's catalogue of video kinds, the
 * one an orderer picks from on the create form. Nothing is ever deleted -
 * a kind is retired by switching "Đang dùng" off, and an order keeps the
 * name and points it was sent with. Only a unit admin sees this (the
 * server refuses `include_inactive` and every write to anybody else).
 */
export function VideoKindsManager({ code }: { code: string }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const canAdmin = me.data?.can_admin.includes(code) ?? false;
  const kinds = useQuery({
    queryKey: ["units", code, "video-kinds", "all"],
    queryFn: () => api.unitVideoKinds(code, true),
    enabled: canAdmin,
  });
  if (!canAdmin) return null;
  return (
    <section
      aria-label="Loại video & điểm hiệu suất"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Loại video & điểm hiệu suất</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Người order chọn một loại khi lên order; điểm được ghi lại tại thời
        điểm gửi. Tắt &quot;Đang dùng&quot; để ngừng một loại - order cũ giữ
        nguyên.
      </p>
      {kinds.isPending ? (
        <Loading label="Đang tải loại video…" />
      ) : kinds.isError ? (
        <ErrorBox error={kinds.error} onRetry={() => kinds.refetch()} />
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="table-dense w-full min-w-[36rem]">
            <thead>
              <tr>
                <th scope="col" className="text-left">
                  Tên loại video
                </th>
                <th scope="col">Điểm</th>
                <th scope="col">Đang dùng</th>
                <th scope="col">Thứ tự</th>
                <th scope="col">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...kinds.data.kinds]
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((kind) => (
                  <KindRow key={kind.id} code={code} kind={kind} />
                ))}
              <NewKindRow
                code={code}
                nextOrder={
                  kinds.data.kinds.reduce(
                    (max, kind) => Math.max(max, kind.sort_order),
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

function KindRow({ code, kind }: { code: string; kind: UnitVideoKind }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(kind));
  const saved = draftOf(kind);
  const save = useMutation({
    mutationFn: () => {
      const body: Parameters<typeof api.updateUnitVideoKind>[2] = {};
      if (draft.name.trim() !== saved.name) body.name = draft.name.trim();
      if (Number(draft.points) !== Number(saved.points))
        body.points = Number(draft.points);
      if (draft.active !== saved.active) body.active = draft.active;
      if (Number(draft.sort_order) !== Number(saved.sort_order))
        body.sort_order = Number(draft.sort_order || "0");
      return api.updateUnitVideoKind(code, kind.id, body);
    },
    onSuccess: (updated) => {
      setDraft(draftOf(updated));
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "video-kinds"],
      });
    },
  });
  const changed =
    draft.name.trim() !== saved.name ||
    Number(draft.points) !== Number(saved.points) ||
    draft.active !== saved.active ||
    Number(draft.sort_order) !== Number(saved.sort_order);
  return (
    <>
      <tr className={kind.active ? undefined : "opacity-60"}>
        <td>
          <input
            aria-label={`Tên · ${kind.name}`}
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td>
          <input
            aria-label={`Điểm · ${kind.name}`}
            type="number"
            min={0}
            step={0.25}
            value={draft.points}
            onChange={(event) =>
              setDraft((all) => ({ ...all, points: event.target.value }))
            }
            className={`${field} w-20`}
          />
        </td>
        <td className="text-center">
          <input
            aria-label={`Đang dùng · ${kind.name}`}
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label={`Thứ tự · ${kind.name}`}
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
              title: `Lưu loại video "${draft.name.trim()}"?`,
              description:
                "Áp dụng cho order gửi từ giờ. Order đã gửi giữ tên và điểm cũ.",
              confirmLabel: "Lưu",
            }}
            onConfirm={() => save.mutate()}
            pending={save.isPending}
            error={save.error}
            disabled={!changed || !validDraft(draft)}
            ariaLabel={`Lưu ${kind.name}`}
            tone="secondary"
          >
            Lưu
          </ConfirmButton>
        </td>
      </tr>
      {save.isError ? (
        <tr>
          <td colSpan={5}>
            <NoticeBox error={save.error} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewKindRow({ code, nextOrder }: { code: string; nextOrder: number }) {
  const queryClient = useQueryClient();
  const empty = (): Draft => ({
    name: "",
    points: "1",
    active: true,
    sort_order: "",
  });
  const [draft, setDraft] = useState<Draft>(empty);
  const create = useMutation({
    mutationFn: () =>
      api.createUnitVideoKind(code, {
        name: draft.name.trim(),
        points: Number(draft.points),
        active: draft.active,
        sort_order: Number(draft.sort_order || nextOrder),
      }),
    onSuccess: () => {
      setDraft(empty());
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "video-kinds"],
      });
    },
  });
  return (
    <>
      <tr className="border-t-2 border-[var(--border)]">
        <td>
          <input
            aria-label="Tên loại video mới"
            placeholder="Thêm loại video…"
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td>
          <input
            aria-label="Điểm loại video mới"
            type="number"
            min={0}
            step={0.25}
            value={draft.points}
            onChange={(event) =>
              setDraft((all) => ({ ...all, points: event.target.value }))
            }
            className={`${field} w-20`}
          />
        </td>
        <td className="text-center">
          <input
            aria-label="Đang dùng loại video mới"
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label="Thứ tự loại video mới"
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
              title: `Thêm loại video "${draft.name.trim()}"?`,
              description: "Người order chọn được ngay trên form tạo order.",
              confirmLabel: "Thêm",
            }}
            onConfirm={() => create.mutate()}
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
            <NoticeBox error={create.error} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
