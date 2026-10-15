"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ResourceOption, type ResourceOptionKind, type ResourceOptions } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

/**
 * "Cài đặt tài nguyên" (the Phòng Tài nguyên's head, SETTINGS): the
 * pick-lists - add, rename, reorder, retire an entry (never deleted; a
 * status also says which group it belongs to, so the reports stay right) -
 * and the "quá N ngày chưa rà soát" threshold.
 */
const FIELD =
  "min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

export default function ResourceSettingsPage() {
  const options = useQuery({
    queryKey: ["resources", "options", "all"],
    queryFn: () => api.resourceOptions(true),
  });
  if (options.isPending) return <Loading label="Đang tải cài đặt…" />;
  if (options.isError) return <ErrorBox error={options.error} onRetry={() => options.refetch()} />;
  const data = options.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Cài đặt tài nguyên"
        subtitle="Danh sách lựa chọn (loại, quyền, trạng thái, nguồn gốc, bảo mật 2 lớp, bàn giao) và ngưỡng rà soát. Phòng Tài nguyên tự thêm, không cần nhờ IT."
      />
      {data.can_manage ? null : (
        <p className="rounded-lg bg-[var(--surface-muted)] px-3 py-2 text-sm text-[var(--text-muted)]">
          Chỉ Trưởng phòng Tài nguyên chỉnh cài đặt.
        </p>
      )}
      <ReviewThreshold days={data.review_days} canManage={data.can_manage} />
      <div className="grid gap-4 lg:grid-cols-2">
        {data.kinds.map((kind) => (
          <OptionList key={kind.kind} kind={kind} options={data} canManage={data.can_manage} />
        ))}
      </div>
    </div>
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ["resources"] });
}

function ReviewThreshold({ days, canManage }: { days: number; canManage: boolean }) {
  const refresh = useRefresh();
  const id = useId();
  const [value, setValue] = useState(String(days));
  const save = useMutation({
    mutationFn: () => api.updateResourceSettings({ review_days: Number(value) }),
    onSuccess: refresh,
  });
  const parsed = Number(value);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 3650;
  return (
    <section aria-label="Ngưỡng rà soát" className="panel p-4">
      <h2 className="text-sm font-semibold">Ngưỡng &quot;quá N ngày chưa rà soát&quot;</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Tài nguyên không được rà soát hoặc đổi trạng thái quá số ngày này sẽ vào danh sách cảnh báo.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label htmlFor={id} className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Số ngày
          <input
            id={id}
            type="number"
            min={1}
            max={3650}
            value={value}
            disabled={!canManage}
            onChange={(event) => setValue(event.target.value)}
            className={`${FIELD} w-28`}
          />
        </label>
        {canManage ? (
          <ConfirmButton
            spec={{
              title: `Đặt ngưỡng rà soát ${parsed} ngày?`,
              description: "Áp dụng ngay cho danh sách cảnh báo và báo cáo.",
              confirmLabel: "Lưu ngưỡng",
            }}
            onConfirm={() => save.mutate()}
            pending={save.isPending}
            error={save.error}
            disabled={!valid || parsed === days}
            tone="primary"
          >
            Lưu ngưỡng
          </ConfirmButton>
        ) : null}
      </div>
      {save.isError ? (
        <div className="mt-2">
          <NoticeBox error={save.error} onDismiss={() => save.reset()} />
        </div>
      ) : null}
    </section>
  );
}

function OptionList({
  kind,
  options,
  canManage,
}: {
  kind: ResourceOptionKind;
  options: ResourceOptions;
  canManage: boolean;
}) {
  const refresh = useRefresh();
  const nameId = useId();
  const [name, setName] = useState("");
  const [group, setGroup] = useState("NORMAL");
  const isStatus = kind.kind === "STATUS";
  const add = useMutation({
    mutationFn: () =>
      api.createResourceOption({
        kind: kind.kind,
        name: name.trim(),
        status_group: isStatus ? group : null,
      }),
    onSuccess: () => {
      setName("");
      refresh();
    },
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.reorderResourceOptions(kind.kind, ids),
    onSuccess: refresh,
  });
  const move = (index: number, direction: -1 | 1) => {
    const ids = kind.options.map((option) => option.id);
    const target = index + direction;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorder.mutate(ids);
  };
  return (
    <section aria-label={kind.label} className="panel">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold">{kind.label}</h2>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {kind.options.map((option, index) => (
          <OptionRow
            key={option.id}
            option={option}
            options={options}
            canManage={canManage}
            onMove={(direction) => move(index, direction)}
            first={index === 0}
            last={index === kind.options.length - 1}
          />
        ))}
      </ul>
      {canManage ? (
        <form
          aria-label={`Thêm ${kind.label.toLowerCase()}`}
          className="flex flex-wrap items-end gap-2 border-t border-[var(--border)] px-4 py-3"
          onSubmit={(event) => event.preventDefault()}
        >
          <label htmlFor={nameId} className="flex min-w-[10rem] flex-1 flex-col gap-1 text-xs text-[var(--text-muted)]">
            Thêm lựa chọn
            <input
              id={nameId}
              type="text"
              value={name}
              maxLength={120}
              onChange={(event) => setName(event.target.value)}
              placeholder="Tên hiển thị"
              className={FIELD}
            />
          </label>
          {isStatus ? (
            <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
              Nhóm
              <Select value={group} onChange={(event) => setGroup(event.target.value)} aria-label="Nhóm trạng thái" className="text-sm">
                {options.status_groups.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          <ConfirmButton
            spec={{
              title: `Thêm "${name.trim()}" vào ${kind.label.toLowerCase()}?`,
              description: "Lựa chọn mới dùng được ngay trên form và khi nhập Excel.",
              confirmLabel: "Thêm lựa chọn",
            }}
            onConfirm={() => add.mutate()}
            pending={add.isPending}
            error={add.error}
            disabled={!name.trim()}
            tone="primary"
            onOpenChange={(open) => {
              if (open) add.reset();
            }}
          >
            Thêm
          </ConfirmButton>
          {add.isError ? (
            <div className="basis-full">
              <NoticeBox error={add.error} onDismiss={() => add.reset()} />
            </div>
          ) : null}
        </form>
      ) : null}
      {reorder.isError ? (
        <div className="px-4 pb-3">
          <NoticeBox error={reorder.error} onDismiss={() => reorder.reset()} />
        </div>
      ) : null}
    </section>
  );
}

function OptionRow({
  option,
  options,
  canManage,
  onMove,
  first,
  last,
}: {
  option: ResourceOption;
  options: ResourceOptions;
  canManage: boolean;
  onMove: (direction: -1 | 1) => void;
  first: boolean;
  last: boolean;
}) {
  const refresh = useRefresh();
  const [name, setName] = useState(option.name);
  const [group, setGroup] = useState(option.status_group ?? "NORMAL");
  const update = useMutation({
    mutationFn: (body: { name?: string; active?: boolean; status_group?: string }) =>
      api.updateResourceOption(option.id, body),
    onSuccess: refresh,
  });
  const dirty =
    name.trim() !== option.name ||
    (option.kind === "STATUS" && group !== (option.status_group ?? "NORMAL"));
  const arrow = "min-h-9 min-w-9 rounded-md border border-[var(--border)] text-xs disabled:opacity-30";
  return (
    <li className={`flex flex-wrap items-center gap-2 px-4 py-2 ${option.active ? "" : "opacity-60"}`}>
      {canManage ? (
        <>
          <input
            aria-label={`Tên của ${option.name}`}
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            className={`${FIELD} min-h-9 min-w-[10rem] flex-1`}
          />
          {option.kind === "STATUS" ? (
            <Select
              aria-label={`Nhóm của ${option.name}`}
              value={group}
              onChange={(event) => setGroup(event.target.value)}
              className="min-h-9 text-xs"
            >
              {options.status_groups.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          ) : null}
        </>
      ) : (
        <span className="min-w-[10rem] flex-1 text-sm">
          {option.name}
          {option.status_group_label ? (
            <span className="ml-2 text-xs text-[var(--text-muted)]">{option.status_group_label}</span>
          ) : null}
        </span>
      )}
      <span className="font-mono text-[11px] text-[var(--text-muted)]">{option.code}</span>
      {option.active ? null : <Pill tone="bad">Ngừng dùng</Pill>}
      {canManage ? (
        <>
          <button type="button" aria-label={`Đưa ${option.name} lên`} className={arrow} disabled={first} onClick={() => onMove(-1)}>
            ↑
          </button>
          <button type="button" aria-label={`Đưa ${option.name} xuống`} className={arrow} disabled={last} onClick={() => onMove(1)}>
            ↓
          </button>
          <ConfirmButton
            spec={{
              title: `Lưu "${name.trim()}"?`,
              description: "Tên mới hiện ngay ở mọi nơi dùng lựa chọn này; dữ liệu cũ vẫn trỏ về cùng lựa chọn.",
              confirmLabel: "Lưu",
            }}
            onConfirm={() =>
              update.mutate({
                name: name.trim(),
                ...(option.kind === "STATUS" ? { status_group: group } : {}),
              })
            }
            pending={update.isPending}
            error={update.error}
            disabled={!dirty || !name.trim()}
            tone="secondary"
            ariaLabel={`Lưu ${option.name}`}
            className="min-h-9 px-3 text-xs"
          >
            Lưu
          </ConfirmButton>
          <ConfirmButton
            spec={{
              title: option.active ? `Ngừng dùng "${option.name}"?` : `Dùng lại "${option.name}"?`,
              description: option.active
                ? "Không còn chọn được trên form và khi nhập Excel; tài nguyên đang dùng lựa chọn này giữ nguyên."
                : "Chọn lại được trên form và khi nhập Excel.",
              confirmLabel: option.active ? "Ngừng dùng" : "Dùng lại",
              variant: option.active ? "destructive" : undefined,
            }}
            onConfirm={() => update.mutate({ active: !option.active })}
            pending={update.isPending}
            error={update.error}
            tone={option.active ? "danger" : "secondary"}
            ariaLabel={option.active ? `Ngừng dùng ${option.name}` : `Dùng lại ${option.name}`}
            className="min-h-9 px-3 text-xs"
          >
            {option.active ? "Ngừng" : "Dùng lại"}
          </ConfirmButton>
        </>
      ) : null}
      {update.isError ? (
        <div className="basis-full">
          <NoticeBox error={update.error} onDismiss={() => update.reset()} />
        </div>
      ) : null}
    </li>
  );
}
