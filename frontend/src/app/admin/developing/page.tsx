"use client";

/**
 * "Quản trị › Đang phát triển" (13/11/2026), the OWNER's alone: a module still
 * being built is hidden from everybody else - no menu, no page, its API
 * refuses, the permission screens leave it out (ADMINs included). Grants
 * already given stay and come back when the module is released.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AdminTabs } from "@/components/admin-tabs";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { developingApi } from "@/lib/finance";

export default function DevelopingModulesPage() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ["developing-modules"], queryFn: developingApi.list, retry: false });
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const save = useMutation({
    mutationFn: (modules: string[]) => developingApi.save(modules),
    onSuccess: () => {
      setDraft({});
      void queryClient.invalidateQueries({ queryKey: ["developing-modules"] });
      void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
    },
  });
  const rows = list.data?.modules ?? [];
  const value = (module: string, stored: boolean) => draft[module] ?? stored;
  const changed = rows.filter((row) => value(row.module, row.developing) !== row.developing);
  const chosen = rows.filter((row) => value(row.module, row.developing)).map((row) => row.module);
  return (
    <div className="space-y-4">
      <AdminTabs active="developing" />
      <PageHeader
        title="Tính năng đang phát triển"
        subtitle="Module đánh dấu ở đây chỉ chủ sở hữu thấy: người khác (kể cả quản trị viên) không thấy menu, trang, quyền của module. Quyền đã cấp vẫn giữ, có lại khi mở module."
      />
      {list.isPending ? <Loading /> : null}
      {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      {rows.length ? (
        <div className="panel divide-y divide-[var(--border)]">
          {rows.map((row) => {
            const on = value(row.module, row.developing);
            return (
              <label
                key={row.module}
                className="flex min-h-14 items-center justify-between gap-3 px-4 py-3"
                data-testid="developing-row"
              >
                <span>
                  <span className="font-medium">{row.label}</span>{" "}
                  {row.developing ? <Pill tone="warn">Đang ẩn</Pill> : <Pill tone="good">Đã mở</Pill>}
                </span>
                <span className="flex items-center gap-2 text-sm">
                  Đang phát triển
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={(event) => setDraft({ ...draft, [row.module]: event.target.checked })}
                    className="h-5 w-5"
                  />
                </span>
              </label>
            );
          })}
        </div>
      ) : null}
      {rows.length ? (
        <ConfirmButton
          spec={{
            title: "Lưu tính năng đang phát triển?",
            description: changed
              .map((row) =>
                value(row.module, row.developing)
                  ? `${row.label}: ẩn với mọi người trừ chủ sở hữu.`
                  : `${row.label}: mở cho người được cấp quyền.`,
              )
              .join(" "),
            confirmLabel: "Lưu",
          }}
          disabled={!changed.length}
          onConfirm={() => save.mutate(chosen)}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu
        </ConfirmButton>
      ) : null}
    </div>
  );
}
