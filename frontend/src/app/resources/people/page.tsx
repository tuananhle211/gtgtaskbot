"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { ResourceTable } from "@/components/resource-table";
import { employmentTone } from "@/lib/resources";

/**
 * "Tra cứu theo nhân viên": pick a person; see their departments, employment
 * status, how many resources they hold (by type), the list they hold and the
 * list they held before. The person is on the URL (`?user=`).
 */
export default function PeoplePage() {
  return (
    <Suspense fallback={<Loading />}>
      <PeopleLookup />
    </Suspense>
  );
}

function PeopleLookup() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const userId = params.get("user") ?? "";
  const people = useQuery({ queryKey: ["resources", "people"], queryFn: () => api.resourcePeople() });
  const lookup = useQuery({
    queryKey: ["resources", "person", userId],
    queryFn: () => api.resourcePerson(userId),
    enabled: Boolean(userId),
  });
  const choose = (next: string) =>
    router.replace(next ? `${pathname}?user=${encodeURIComponent(next)}` : pathname, { scroll: false });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Tra cứu theo nhân viên"
        subtitle="Một người đang giữ gì, từng giữ gì - để bàn giao và thu hồi không sót."
      />
      <div className="panel p-3">
        <label className="flex max-w-md flex-col gap-1 text-xs text-[var(--text-muted)]">
          Nhân viên
          <Select value={userId} onChange={(event) => choose(event.target.value)} aria-label="Nhân viên" className="text-sm">
            <option value="">— chọn nhân viên —</option>
            {(people.data?.people ?? []).map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.full_name}
                {person.departments.length > 0 ? ` · ${person.departments.map((d) => d.name).join(", ")}` : ""}
                {person.employment_status !== "WORKING" ? ` (${person.employment_status_label})` : ""}
              </option>
            ))}
          </Select>
        </label>
        {people.isError ? <ErrorBox error={people.error} onRetry={() => people.refetch()} /> : null}
      </div>
      {userId && lookup.isPending ? <Loading label="Đang tra cứu…" /> : null}
      {lookup.isError ? <ErrorBox error={lookup.error} onRetry={() => lookup.refetch()} /> : null}
      {lookup.data ? (
        <>
          <section aria-label="Thông tin nhân viên" className="panel p-4">
            <h2 className="text-lg font-semibold">{lookup.data.person.full_name}</h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-[var(--text-muted)]">
                {lookup.data.person.departments.length > 0
                  ? lookup.data.person.departments.map((d) => d.name).join(" · ")
                  : "Chưa thuộc phòng ban nào"}
              </span>
              <Pill tone={employmentTone(lookup.data.person.employment_status)}>
                {lookup.data.person.employment_status_label}
              </Pill>
            </p>
            <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
              <div>
                <dt className="text-xs text-[var(--text-muted)]">Đang giữ</dt>
                <dd className="text-xl font-semibold tabular-nums">{lookup.data.total}</dd>
              </div>
              {lookup.data.by_type.map((item) => (
                <div key={item.type.id}>
                  <dt className="text-xs text-[var(--text-muted)]">{item.type.name}</dt>
                  <dd className="text-xl font-semibold tabular-nums">{item.count}</dd>
                </div>
              ))}
            </dl>
          </section>
          <section aria-label="Đang giữ" className="panel">
            <h2 className="border-b border-[var(--border)] px-4 py-3 text-sm font-semibold">Đang giữ</h2>
            <ResourceTable rows={lookup.data.holding} emptyMessage="Không giữ tài nguyên nào." />
          </section>
          <section aria-label="Từng giữ" className="panel">
            <h2 className="border-b border-[var(--border)] px-4 py-3 text-sm font-semibold">Từng giữ</h2>
            <ResourceTable rows={lookup.data.held_before} emptyMessage="Chưa từng giữ tài nguyên nào khác." />
          </section>
        </>
      ) : null}
    </div>
  );
}
