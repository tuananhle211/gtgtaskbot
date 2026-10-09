"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type UnitPlatform } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

const field =
  "min-h-9 w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

interface Draft {
  name: string;
  active: boolean;
  sort_order: string;
}

const draftOf = (item: UnitPlatform): Draft => ({
  name: item.name,
  active: item.active,
  sort_order: String(item.sort_order),
});

const validDraft = (draft: Draft) =>
  draft.name.trim() !== "" && Number.isInteger(Number(draft.sort_order || "0"));

export function PlatformManager({ code }: { code: string }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const canAdmin = me.data?.can_admin.includes(code) ?? false;
  const items = useQuery({
    queryKey: ["units", code, "platforms", "all"],
    queryFn: () => api.unitPlatforms(code, true),
    enabled: canAdmin,
  });
  if (!canAdmin) return null;
  return (
    <section
      aria-label="Nền tảng"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Nền tảng</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Người order chọn nền tảng khi lên order. Tắt &quot;Đang dùng&quot; để
        ngừng.
      </p>
      {items.isPending ? (
        <Loading label="Đang tải nền tảng…" />
      ) : items.isError ? (
        <ErrorBox error={items.error} onRetry={() => items.refetch()} />
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="table-dense w-full min-w-[28rem]">
            <thead>
              <tr>
                <th scope="col" className="text-left">
                  Tên nền tảng
                </th>
                <th scope="col">Đang dùng</th>
                <th scope="col">Thứ tự</th>
                <th scope="col">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...items.data.platforms]
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((item) => (
                  <PlatformRow key={item.id} code={code} item={item} />
                ))}
              <NewPlatformRow
                code={code}
                nextOrder={
                  items.data.platforms.reduce(
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

function PlatformRow({
  code,
  item,
}: {
  code: string;
  item: UnitPlatform;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(item));
  const saved = draftOf(item);
  const save = useMutation({
    mutationFn: () => {
      const body: Parameters<typeof api.updateUnitPlatform>[2] = {};
      if (draft.name.trim() !== saved.name) body.name = draft.name.trim();
      if (draft.active !== saved.active) body.active = draft.active;
      if (Number(draft.sort_order) !== Number(saved.sort_order))
        body.sort_order = Number(draft.sort_order || "0");
      return api.updateUnitPlatform(code, item.id, body);
    },
    onSuccess: (updated) => {
      setDraft(draftOf(updated));
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "platforms"],
      });
    },
  });
  const changed =
    draft.name.trim() !== saved.name ||
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
              title: `Lưu nền tảng "${draft.name.trim()}"?`,
              description: "Áp dụng cho order gửi từ giờ.",
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
          <td colSpan={4}>
            <NoticeBox error={save.error} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewPlatformRow({
  code,
  nextOrder,
}: {
  code: string;
  nextOrder: number;
}) {
  const queryClient = useQueryClient();
  const empty = (): Draft => ({ name: "", active: true, sort_order: "" });
  const [draft, setDraft] = useState<Draft>(empty);
  const create = useMutation({
    mutationFn: () =>
      api.createUnitPlatform(code, {
        name: draft.name.trim(),
        active: draft.active,
        sort_order: Number(draft.sort_order || nextOrder),
      }),
    onSuccess: () => {
      setDraft(empty());
      void queryClient.invalidateQueries({
        queryKey: ["units", code, "platforms"],
      });
    },
  });
  return (
    <>
      <tr className="border-t-2 border-[var(--border)]">
        <td>
          <input
            aria-label="Tên nền tảng mới"
            placeholder="Thêm nền tảng…"
            value={draft.name}
            onChange={(event) =>
              setDraft((all) => ({ ...all, name: event.target.value }))
            }
            className={field}
          />
        </td>
        <td className="text-center">
          <input
            aria-label="Đang dùng nền tảng mới"
            type="checkbox"
            checked={draft.active}
            onChange={(event) =>
              setDraft((all) => ({ ...all, active: event.target.checked }))
            }
          />
        </td>
        <td>
          <input
            aria-label="Thứ tự nền tảng mới"
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
              title: `Thêm nền tảng "${draft.name.trim()}"?`,
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
          <td colSpan={4}>
            <NoticeBox error={create.error} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
