"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type EffectivePermissionRow,
  type ModulePermissions,
  type PersonPermissions,
  type PermissionSource,
} from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { AdminTabs } from "@/components/admin-tabs";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { employmentTone, formatResourceDay } from "@/lib/resources";

/**
 * "Quyền" of one person (0066), as a checklist per module.
 *
 * Every permission is a row with a tick. A tick the person's role or
 * department seat already gives is on and locked ("mặc định"); the rest can
 * be ticked by whoever may manage this module for this person (the OWNER, an
 * ADMIN, or a head holding "Cấp / thu hồi quyền" over them), within what
 * they may grant themselves. "Lưu" sends the whole module's checklist: a
 * tick becomes a personal grant, an untick a revocation. The PR review gates
 * are scoped per person on the PR screen and only point there.
 */
export default function PersonPermissionsPage() {
  const params = useParams<{ id: string }>();
  const userId = params.id;
  const page = useQuery({
    queryKey: ["permissions", userId],
    queryFn: () => api.personPermissions(userId),
    retry: false,
  });
  if (page.isPending) return <Loading label="Đang tải quyền…" />;
  if (page.isError) return <ErrorBox error={page.error} onRetry={() => page.refetch()} />;
  const data = page.data;
  return (
    <div className="space-y-4">
      <AdminTabs active="departments" />
      <PageHeader
        title={`Quyền · ${data.full_name}`}
        subtitle="Quyền mặc định đến từ vai trò và chỗ ngồi ở phòng ban (đã tích sẵn, không bỏ được ở đây). Muốn thêm quyền thì tích vào rồi Lưu."
      />
      <section aria-label="Thông tin" className="panel p-4">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Pill tone="neutral">{data.role_label}</Pill>
          <Pill tone={employmentTone(data.employment_status)}>{data.employment_status_label}</Pill>
          {data.sees_all ? <Pill tone="good">Mọi quyền ở mọi module</Pill> : null}
        </p>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          {data.seats.length === 0
            ? "Chưa thuộc phòng ban nào."
            : data.seats.map((seat) => `${seat.position_label} ${seat.name}`).join(" · ")}
          {" · "}
          <Link href="/admin/departments" className="text-[var(--accent)] underline">
            Phòng ban
          </Link>
        </p>
      </section>
      {data.modules.map((module) => (
        <ModuleChecklist key={`${module.module}:${data.personal.length}`} person={data} module={module} />
      ))}
    </div>
  );
}

function sourceText(source: PermissionSource): string {
  if (source.source === "DEPARTMENT") {
    return `${source.position_label} ${source.department_name}`;
  }
  return `cấp riêng${source.effective_to ? ` đến ${formatResourceDay(source.effective_to)}` : ""}${source.note ? ` · ${source.note}` : ""}`;
}

/** What the row's tick means before any edit. */
type Tick = {
  /** On by the role or a seat: cannot be turned off here. */
  locked: boolean;
  /** The scope a personal grant gives (null = none). */
  personal: string | null;
  /** The scope the seats give (null = none). */
  inherited: string | null;
};

function tickOf(person: PersonPermissions, row: EffectivePermissionRow): Tick {
  if (person.sees_all) return { locked: true, personal: null, inherited: "ALL" };
  const inherited = row.sources
    .filter((source) => source.source === "DEPARTMENT")
    .map((source) => source.scope)
    .sort((a, b) => (a === "ALL" ? -1 : b === "ALL" ? 1 : 0))[0] ?? null;
  const personal = row.sources
    .filter((source) => source.source === "PERSONAL")
    .map((source) => source.scope)
    .sort((a, b) => (a === "ALL" ? -1 : b === "ALL" ? 1 : 0))[0] ?? null;
  return { locked: inherited !== null, personal, inherited };
}

function ModuleChecklist({ person, module }: { person: PersonPermissions; module: ModulePermissions }) {
  const queryClient = useQueryClient();
  // The checklist as edited: permission -> scope | null (not ticked), for the
  // rows the viewer may change. Starts from the personal grants.
  const editable = module.rows.filter(
    (row) => !row.personal_only && module.can_manage && (module.grantable[row.permission]?.length ?? 0) > 0,
  );
  const [draft, setDraft] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(editable.map((row) => [row.permission, tickOf(person, row).personal])),
  );
  const save = useMutation({
    mutationFn: () => api.savePermissionChecklist(person.user_id, module.module, draft),
    onSuccess: (next) => {
      queryClient.setQueryData(["permissions", person.user_id], next);
      void queryClient.invalidateQueries({ queryKey: ["units"] });
    },
  });
  const original = Object.fromEntries(editable.map((row) => [row.permission, tickOf(person, row).personal]));
  const changed = editable.filter((row) => (draft[row.permission] ?? null) !== (original[row.permission] ?? null));
  const held = module.rows.filter((row) => row.scope !== "NONE").length;
  const scopeLabel = (value: string) => person.scope_labels[value] ?? value;
  return (
    <section aria-label={module.label} className="panel">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">{module.label}</h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            {held === 0 ? "Không có quyền nào." : `${held} quyền đang có.`}
            {module.can_manage
              ? editable.length > 0
                ? " Tích để cấp thêm, bỏ tích để thu hồi quyền cấp riêng."
                : " Bạn không có quyền nào ở module này để cấp thêm."
              : " Bạn không cấp được quyền ở module này."}
          </p>
        </div>
        {module.can_manage && editable.length > 0 ? (
          <div className="flex items-center gap-2">
            {changed.length > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setDraft(original);
                  save.reset();
                }}
                className="min-h-9 rounded-lg border border-[var(--border)] px-3 text-xs"
              >
                Bỏ thay đổi
              </button>
            ) : null}
            <ConfirmButton
              spec={{
                title: `Lưu quyền ${module.label.toLowerCase()} của ${person.full_name}?`,
                description: changed
                  .map((row) => {
                    const next = draft[row.permission] ?? null;
                    return next === null
                      ? `Thu hồi "${row.label}".`
                      : `Cấp "${row.label}" (${scopeLabel(next)}).`;
                  })
                  .join(" ") || "Không có thay đổi.",
                confirmLabel: "Lưu quyền",
                count: changed.length > 1 ? changed.length : undefined,
              }}
              onConfirm={() => save.mutate()}
              pending={save.isPending}
              error={save.error}
              disabled={changed.length === 0}
              tone="primary"
              className="min-h-9 px-3 text-xs"
              onOpenChange={(open) => {
                if (open) save.reset();
              }}
            >
              Lưu quyền
            </ConfirmButton>
          </div>
        ) : null}
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {module.rows.map((row) => {
          const tick = tickOf(person, row);
          const canEdit = editable.some((item) => item.permission === row.permission);
          const scopes = module.grantable[row.permission] ?? [];
          const current = canEdit ? (draft[row.permission] ?? null) : tick.personal;
          const checked = tick.locked || current !== null;
          const effective = tick.locked ? tick.inherited : current;
          return (
            <li
              key={row.permission}
              data-permission={row.permission}
              data-checked={checked ? "true" : "false"}
              data-locked={tick.locked ? "true" : undefined}
              className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm"
            >
              {row.personal_only ? (
                <span className="inline-block h-4 w-4" aria-hidden="true" />
              ) : (
                <input
                  type="checkbox"
                  aria-label={row.label}
                  checked={checked}
                  disabled={tick.locked || !canEdit}
                  onChange={(event) =>
                    setDraft((was) => ({
                      ...was,
                      [row.permission]: event.target.checked ? (scopes.includes("ALL") ? "ALL" : scopes[0]) : null,
                    }))
                  }
                  className="h-4 w-4"
                />
              )}
              <span className={checked || row.personal_only ? "font-medium" : "text-[var(--text-muted)]"}>
                {row.label}
              </span>
              {checked && effective && !row.personal_only ? (
                canEdit && !tick.locked && scopes.length > 1 ? (
                  <select
                    aria-label={`Phạm vi ${row.label}`}
                    value={current ?? scopes[0]}
                    onChange={(event) =>
                      setDraft((was) => ({ ...was, [row.permission]: event.target.value }))
                    }
                    className="min-h-8 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
                  >
                    {scopes.map((value) => (
                      <option key={value} value={value}>
                        {scopeLabel(value)}
                      </option>
                    ))}
                  </select>
                ) : canEdit && tick.locked && tick.inherited === "OWN" && scopes.includes("ALL") ? (
                  // The seat gives "Của mình"; a personal grant may widen it to "Toàn bộ".
                  <select
                    aria-label={`Phạm vi ${row.label}`}
                    value={current ?? "OWN"}
                    onChange={(event) =>
                      setDraft((was) => ({
                        ...was,
                        [row.permission]: event.target.value === "OWN" ? null : event.target.value,
                      }))
                    }
                    className="min-h-8 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
                  >
                    <option value="OWN">Mặc định: {scopeLabel("OWN")}</option>
                    <option value="ALL">{scopeLabel("ALL")}</option>
                  </select>
                ) : (
                  <Pill tone={(current ?? effective) === "ALL" ? "good" : "warn"}>
                    {scopeLabel(current ?? effective)}
                  </Pill>
                )
              ) : null}
              <span className="text-xs text-[var(--text-muted)]">
                {person.sees_all && !row.personal_only ? `mặc định · vai trò ${person.role_label}` : null}
                {!person.sees_all && tick.locked
                  ? `mặc định · ${row.sources.filter((s) => s.source === "DEPARTMENT").map(sourceText).join(", ")}`
                  : null}
                {!tick.locked && tick.personal && (draft[row.permission] ?? tick.personal) !== null
                  ? row.sources.filter((s) => s.source === "PERSONAL").map(sourceText).join(", ")
                  : null}
                {row.personal_only ? (
                  <>
                    {row.gate_grants.length > 0 ? `${row.gate_grants.length} quyền duyệt theo phạm vi · ` : ""}
                    <Link href="/pr/permissions?tab=grants" className="text-[var(--accent)] underline">
                      cấp theo kênh / loại nội dung ở màn PR
                    </Link>
                  </>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
      {save.isError ? (
        <div className="p-4">
          <NoticeBox error={save.error} onDismiss={() => save.reset()} />
        </div>
      ) : null}
    </section>
  );
}
