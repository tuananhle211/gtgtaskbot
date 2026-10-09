"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type UnitDuration } from "@/lib/api";
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

const draftOf = (item: UnitDuration): Draft => ({
  name: item.name,
  points: String(Number(item.points)),
  active: item.active,
  sort_order: String(item.sort_order),
});

const validDraft = (draft: Draft) =>
  draft.name.trim() !== "" &&
  draft.points.trim() !== "" &&
  Number.isFinite(Number(draft.points)) &&
  Number(draft.points) >= 0 &&
  Number.isInteger(Number(draft.sort_order || "0"));

export function DurationManager({ code }: { code: string }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const canAdmin = me.data?.can_admin.includes(code) ?? false;
  const items = useQuery({
    queryKey: ["units", code, "durations", "all"],
    queryFn: () => api.unitDurations(code, true),
    enabled: canAdmin,
  });
  if (!canAdmin) return null;
  return (
    <section
      aria-label="Thời lượng & điểm"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Thời lượng & điểm</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Người order chọn thời lượng khi lên order; điểm được ghi lại tại thời
        điểm gửi.
      </p>
      {items.isPending ? (
        <Loading label="Đang tải thời lượng…" />
      ) : items.isError ? (
        <ErrorBox error={items.error} onRetry={() => items.refetch()} />
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="table-dense w-full min-w-[36rem]">
            <thead>
              <tr>
                <th scope="col" className="text-left">
                  Tên thời lượng
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
              {[...items.data.durations]
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((item) => (
                  <DurationRow key={item.id} code={code} item={item} />
                ))}
              <NewDurationRow
                code={code}
                nextOrder={
                  items.data.durations.reduce(
                    (max, item) => Math.max(max, item.sort_order),
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

function DurationRow({
  code,
  item,
}: {
  code: string;
  item: UnitDuration;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(item));
  const saved = draftOf(item);
  const save = useMutation({
    mutationFn: () => {
      const body: Parameters<typeof api.updateUnitDuration>[2] = {};
      if (draft.name.trim() !== saved.name) body.name = draft.name.trim();
      if (Number(draft.points) !== Number(saved.points))
        body.points = Number(draft.points);
      if (draft.active !== saved.active) body.active = draft.active;
      if (Number(draft.sort_order) !== Number(saved.sort_order))
        body.sort_order = Number(draft.sort_order || "0");
      return api.updateUnitDuration(code, item.id, body);
    },
    onSuccess: (updated) => {
      setDraft(draftOf(updated));
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "durations"],
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
      <tr className={item.active ? undefined : "opacity-60"}>
        <td>
          <input
            aria-label={`Tên · ${item.name}`}
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td>
          <input
            aria-label={`Điểm · ${item.name}`}
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
            aria-label={`Đang dùng · ${item.name}`}
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label={`Thứ tự · ${item.name}`}
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
              title: `Lưu thời lượng "${draft.name.trim()}"?`,
              description:
                "Áp dụng cho order gửi từ giờ. Order đã gửi giữ tên và điểm cũ.",
              confirmLabel: "Lưu",
            }}
            onConfirm={() => save.mutate()}
            pending={save.isPending}
            error={save.error}
            disabled={!changed || !validDraft(draft)}
            ariaLabel={`Lưu ${item.name}`}
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

function NewDurationRow({
  code,
  nextOrder,
}: {
  code: string;
  nextOrder: number;
}) {
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
      api.createUnitDuration(code, {
        name: draft.name.trim(),
        points: Number(draft.points),
        active: draft.active,
        sort_order: Number(draft.sort_order || nextOrder),
      }),
    onSuccess: () => {
      setDraft(empty());
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "durations"],
      });
    },
  });
  return (
    <>
      <tr className="border-t-2 border-[var(--border)]">
        <td>
          <input
            aria-label="Tên thời lượng mới"
            placeholder="Thêm thời lượng…"
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td>
          <input
            aria-label="Điểm thời lượng mới"
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
            aria-label="Đang dùng thời lượng mới"
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label="Thứ tự thời lượng mới"
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
              title: `Thêm thời lượng "${draft.name.trim()}"?`,
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
