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
 * "Quyền" of one person (0066), as a checklist per module - the OWNER's and
 * an ADMIN's page (2026-11-10; an ADMIN never on the OWNER nor themselves).
 *
 * Every permission is a row with a tick. What the system role itself holds
 * is on and locked (`by_role`: the OWNER everything, an ADMIN all but the
 * OWNER-only ones). Everything else can be ticked or unticked, the defaults a
 * department seat gives included ("mặc định", `inherited`): the server keeps
 * one personal row per permission that differs from the default (0098,
 * `personal`; `NONE` = a default taken away). "Lưu" sends the changed rows.
 * The PR review gates are scoped per person on the PR screen and only point
 * there.
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
        subtitle="Quyền mặc định đến từ phòng ban được tích sẵn; tích hoặc bỏ tích bất kỳ ô nào rồi Lưu. Quyền có sẵn theo vai trò hệ thống (Chủ sở hữu / Quản trị viên) bị khoá - muốn bớt thì đổi vai trò."
      />
      <section aria-label="Thông tin" className="panel p-4">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Pill tone="neutral">{data.role_label}</Pill>
          <Pill tone={employmentTone(data.employment_status)}>{data.employment_status_label}</Pill>
          {data.sees_all ? (
            <Pill tone="good">
              {data.role === "OWNER" ? "Mọi quyền ở mọi module" : "Mọi quyền, trừ quyền chỉ chủ sở hữu cấp"}
            </Pill>
          ) : null}
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
        <ModuleChecklist
          key={`${module.module}:${data.personal.map((row) => `${row.id}:${row.scope}`).join(",")}`}
          person={data}
          module={module}
        />
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

/** The scope a row stands at (`null` = not held). */
function heldScope(row: EffectivePermissionRow): string | null {
  return row.scope === "NONE" ? null : row.scope;
}

/** The default a department seat gives (`null` = none). */
function defaultScope(row: EffectivePermissionRow): string | null {
  const inherited = row.inherited ?? "NONE";
  return inherited === "NONE" ? null : inherited;
}

function ModuleChecklist({ person, module }: { person: PersonPermissions; module: ModulePermissions }) {
  const queryClient = useQueryClient();
  // The checklist as edited: permission -> scope | null (not held), for the
  // rows the viewer may change. Starts from what the person holds now.
  const editable = module.rows.filter(
    (row) =>
      !row.personal_only &&
      !row.by_role &&
      module.can_manage &&
      (module.grantable[row.permission]?.length ?? 0) > 0,
  );
  const original = Object.fromEntries(editable.map((row) => [row.permission, heldScope(row)]));
  const [draft, setDraft] = useState<Record<string, string | null>>(original);
  const changed = editable.filter((row) => (draft[row.permission] ?? null) !== (original[row.permission] ?? null));
  const save = useMutation({
    mutationFn: () =>
      api.savePermissionChecklist(
        person.user_id,
        module.module,
        Object.fromEntries(changed.map((row) => [row.permission, draft[row.permission] ?? null])),
      ),
    onSuccess: (next) => {
      queryClient.setQueryData(["permissions", person.user_id], next);
      void queryClient.invalidateQueries({ queryKey: ["units"] });
    },
  });
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
                ? " Tích để cấp, bỏ tích để thu hồi - kể cả quyền mặc định của phòng ban."
                : " Mọi quyền ở module này đã có sẵn theo vai trò."
              : " Bạn không sửa được quyền ở module này."}
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
                    const fallback = defaultScope(row);
                    if (next === null) {
                      return fallback
                        ? `Bỏ "${row.label}" (quyền mặc định của phòng ban).`
                        : `Thu hồi "${row.label}".`;
                    }
                    if (next === fallback) return `Trả "${row.label}" về mặc định (${scopeLabel(next)}).`;
                    return `Cấp "${row.label}" (${scopeLabel(next)}).`;
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
          const canEdit = editable.some((item) => item.permission === row.permission);
          const scopes = module.grantable[row.permission] ?? [];
          const fallback = defaultScope(row);
          const current = row.by_role ? "ALL" : canEdit ? (draft[row.permission] ?? null) : heldScope(row);
          const checked = current !== null;
          const differs = !row.by_role && !row.personal_only && current !== fallback;
          const seatText = row.sources.filter((s) => s.source === "DEPARTMENT").map(sourceText).join(", ");
          const personalText = row.sources.filter((s) => s.source === "PERSONAL").map(sourceText).join(", ");
          return (
            <li
              key={row.permission}
              data-permission={row.permission}
              data-checked={checked ? "true" : "false"}
              data-locked={row.by_role ? "true" : undefined}
              data-default={fallback ?? undefined}
              className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm"
            >
              {row.personal_only ? (
                <span className="inline-block h-4 w-4" aria-hidden="true" />
              ) : (
                <input
                  type="checkbox"
                  aria-label={row.label}
                  checked={checked}
                  disabled={row.by_role || !canEdit}
                  onChange={(event) =>
                    setDraft((was) => ({
                      ...was,
                      [row.permission]: event.target.checked
                        ? fallback && scopes.includes(fallback)
                          ? fallback
                          : scopes.includes("ALL")
                            ? "ALL"
                            : scopes[0]
                        : null,
                    }))
                  }
                  className="h-4 w-4"
                />
              )}
              <span className={checked || row.personal_only ? "font-medium" : "text-[var(--text-muted)]"}>
                {row.label}
              </span>
              {checked && current && !row.personal_only ? (
                canEdit && scopes.length > 1 ? (
                  <select
                    aria-label={`Phạm vi ${row.label}`}
                    value={current}
                    onChange={(event) =>
                      setDraft((was) => ({ ...was, [row.permission]: event.target.value }))
                    }
                    className="min-h-8 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
                  >
                    {scopes.map((value) => (
                      <option key={value} value={value}>
                        {value === fallback ? `Mặc định: ${scopeLabel(value)}` : scopeLabel(value)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Pill tone={current === "ALL" ? "good" : "warn"}>{scopeLabel(current)}</Pill>
                )
              ) : null}
              {differs ? <Pill tone="warn">{fallback ? "khác mặc định" : "cấp riêng"}</Pill> : null}
              <span className="text-xs text-[var(--text-muted)]">
                {row.by_role && !row.personal_only ? `mặc định · vai trò ${person.role_label}` : null}
                {!row.by_role && fallback
                  ? `mặc định ${scopeLabel(fallback).toLowerCase()}${seatText ? ` · ${seatText}` : " · có sẵn theo chức vụ"}`
                  : null}
                {!row.by_role && personalText && row.personal && row.personal !== "NONE"
                  ? `${fallback ? " · " : ""}${personalText}`
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
