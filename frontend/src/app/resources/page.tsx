"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type ResourceFilters } from "@/lib/api";
import { PageHeader, PrimaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { Pager, pageSizeOf } from "@/components/task-table";
import { ResourceTable } from "@/components/resource-table";
import { ResourceFormDialog } from "@/components/resource-form";
import { cleanFilters, optionsOf } from "@/lib/resources";

/**
 * "Danh sách tài nguyên": every resource this person may see, with the
 * requirement's filters (department, type, holder, status, handover, the
 * holder's employment status) and a search by code, id or name. Every filter
 * is on the URL. A row with a warning is painted; a click opens the profile.
 * "Xuất Excel" downloads the same rows (the server keeps ids as text);
 * "Thêm tài nguyên" is the Phòng Tài nguyên's.
 */
export default function ResourcesPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải tài nguyên…" />}>
      <ResourceList />
    </Suspense>
  );
}

const field =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

const SORTS = [
  ["code", "Mã"],
  ["name", "Tên"],
  ["type", "Loại"],
  ["department", "Ban"],
  ["holder", "Người giữ"],
  ["status", "Trạng thái"],
  ["reviewed_at", "Ngày rà soát"],
] as const;

function ResourceList() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const read = (key: string) => params.get(key) ?? "";
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const options = useQuery({ queryKey: ["resources", "options"], queryFn: () => api.resourceOptions() });
  const people = useQuery({ queryKey: ["resources", "people"], queryFn: () => api.resourcePeople() });
  const page = Number(read("page") || "1");
  const size = pageSizeOf(params.get("size"));
  const filters: ResourceFilters = cleanFilters({
    department_id: read("department"),
    type_id: read("type"),
    holder: read("holder"),
    status_id: read("status"),
    handover_id: read("handover"),
    employment_status: read("employee"),
    q: read("q"),
    alerts: read("alerts") === "true",
    sort: read("sort") || "code",
    desc: read("desc") === "true",
    limit: size,
    offset: (page - 1) * size,
  });
  const list = useQuery({
    queryKey: ["resources", "list", filters],
    queryFn: () => api.resources(filters),
    enabled: me.isSuccess && Boolean(me.data.resources?.view),
  });
  const [search, setSearch] = useState(read("q"));
  const [adding, setAdding] = useState(false);

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
  const access = me.data.resources;
  if (!access?.view) {
    return (
      <div className="space-y-4">
        <PageHeader title="Tài nguyên" />
        <p className="text-sm text-[var(--text-muted)]">
          Bạn chưa được xem mục Tài nguyên. Quyền này do phòng ban cấp (Quản trị › Phòng ban).
        </p>
      </div>
    );
  }
  const selectFilter = (label: string, key: string, kind: string) => (
    <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
      {label}
      <Select
        value={read(key)}
        onChange={(event) => setParams({ [key]: event.target.value })}
        className="text-sm"
        aria-label={label}
      >
        <option value="">Tất cả</option>
        {optionsOf(options.data, kind).map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </Select>
    </label>
  );
  const activeCount = ["department", "type", "holder", "status", "handover", "employee", "q", "alerts"].filter(
    (key) => read(key),
  ).length;
  const exportHref = api.resourceExportUrl({ ...filters, limit: undefined, offset: undefined });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Danh sách tài nguyên"
        subtitle={
          access.view_all
            ? "Toàn bộ tài nguyên của công ty. Dòng đỏ: người giữ đã nghỉ việc, chuyển ban, hoặc tài nguyên đã mất."
            : "Tài nguyên của phòng bạn và những gì bạn đang giữ."
        }
        action={
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={exportHref}
              className="inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
            >
              Xuất Excel
            </a>
            {(access.create ?? access.edit) ? (
              <PrimaryButton type="button" onClick={() => setAdding(true)}>
                + Thêm tài nguyên
              </PrimaryButton>
            ) : null}
          </div>
        }
      />

      <div className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-pressed={read("alerts") === "true"}
            onClick={() => setParams({ alerts: read("alerts") === "true" ? "" : "true" })}
            className={`min-h-9 rounded-full border px-3.5 text-xs font-medium ${
              read("alerts") === "true"
                ? "border-[var(--text)] bg-[var(--text)] text-[var(--surface)]"
                : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            Có cảnh báo
          </button>
          {activeCount > 0 ? (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                router.replace(pathname, { scroll: false });
              }}
              className="min-h-9 rounded-full border border-[var(--border)] px-3.5 text-xs text-[var(--text-muted)]"
            >
              Bỏ lọc ({activeCount})
            </button>
          ) : null}
          <form
            className="ml-auto flex min-w-[16rem] flex-1 items-center gap-2 sm:max-w-sm"
            onSubmit={(event) => {
              event.preventDefault();
              setParams({ q: search });
            }}
          >
            <input
              type="search"
              aria-label="Tìm theo mã, ID hoặc tên"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tìm mã, ID, tên…"
              className={`${field} w-full`}
            />
            <button type="submit" className={`${field} shrink-0`}>
              Tìm
            </button>
          </form>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Ban
            <Select
              value={read("department")}
              onChange={(event) => setParams({ department: event.target.value })}
              className="text-sm"
              aria-label="Ban"
            >
              <option value="">Tất cả</option>
              {(options.data?.departments ?? []).map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </Select>
          </label>
          {selectFilter("Loại", "type", "TYPE")}
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Người được bàn giao
            <Select
              value={read("holder")}
              onChange={(event) => setParams({ holder: event.target.value })}
              className="text-sm"
              aria-label="Người được bàn giao"
            >
              <option value="">Tất cả</option>
              <option value="none">Phòng Tài nguyên giữ</option>
              {(people.data?.people ?? []).map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.full_name}
                </option>
              ))}
            </Select>
          </label>
          {selectFilter("Trạng thái", "status", "STATUS")}
          {selectFilter("Bàn giao", "handover", "HANDOVER")}
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Trạng thái nhân viên
            <Select
              value={read("employee")}
              onChange={(event) => setParams({ employee: event.target.value })}
              className="text-sm"
              aria-label="Trạng thái nhân viên"
            >
              <option value="">Tất cả</option>
              {(options.data?.employment_statuses ?? []).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Sắp xếp
            <div className="flex gap-1">
              <Select
                value={read("sort") || "code"}
                onChange={(event) => setParams({ sort: event.target.value })}
                className="min-w-0 flex-1 text-sm"
                aria-label="Sắp xếp theo"
              >
                {SORTS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
              <button
                type="button"
                aria-label={read("desc") === "true" ? "Đang giảm dần" : "Đang tăng dần"}
                onClick={() => setParams({ desc: read("desc") === "true" ? "" : "true" })}
                className="min-h-11 rounded-lg border border-[var(--border)] px-3 text-sm"
              >
                {read("desc") === "true" ? "↓" : "↑"}
              </button>
            </div>
          </label>
        </div>
      </div>

      {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      {list.isPending ? <Loading label="Đang tải tài nguyên…" /> : null}
      {list.data ? (
        <div className="panel">
          <ResourceTable rows={list.data.items} />
          <Pager
            page={page}
            total={list.data.total}
            shown={list.data.items.length}
            onPage={(next) => setParams({ page: next > 1 ? String(next) : "" })}
            noun="tài nguyên"
            pageSize={size}
            onPageSize={(next) => setParams({ size: next === 10 ? "" : String(next) })}
          />
        </div>
      ) : null}

      {adding ? (
        <ResourceFormDialog
          open
          onClose={() => setAdding(false)}
          options={options.data}
          onSaved={(detail) => {
            setAdding(false);
            router.push(detail.resource.detail_path);
          }}
        />
      ) : null}
    </div>
  );
}
