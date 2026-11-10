"use client";

/**
 * "Sơ đồ tổ chức" (0096): Ban giám đốc -> khối -> phòng -> bộ phận, each with
 * its Trưởng phòng and headcount - built from the departments page's tree.
 */

import { useQuery } from "@tanstack/react-query";
import { hrApi, type HrOrgDepartment } from "@/lib/hr";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

function Node({ item, children, depth }: { item: HrOrgDepartment; children: HrOrgDepartment[]; depth: number }) {
  return (
    <li>
      <div className={`rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 ${depth ? "" : "shadow-sm"}`}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="font-medium">
            {item.name} <span className="font-mono text-xs text-[var(--text-muted)]">{item.code}</span>
          </p>
          <span className="text-xs tabular-nums text-[var(--text-muted)]">{item.people} người</span>
        </div>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          {item.heads.length
            ? item.heads.map((head) => `★ ${head.full_name}${head.job_title ? ` (${head.job_title})` : ""}`).join(" · ")
            : "Chưa có trưởng phòng"}
        </p>
      </div>
      {children.length ? (
        <ul className="ml-4 mt-2 space-y-2 border-l border-[var(--border)] pl-4">
          {children.map((child) => (
            <Node key={child.id} item={child} children={[]} depth={depth + 1} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export default function HrOrgPage() {
  const org = useQuery({ queryKey: ["hr", "org"], queryFn: hrApi.org });
  if (org.isPending) return <Loading label="Đang tải sơ đồ…" />;
  if (org.isError) return <ErrorBox error={org.error} onRetry={() => org.refetch()} />;
  const all = org.data.departments;
  const childrenOf = new Map<string, HrOrgDepartment[]>();
  for (const item of all) {
    if (item.parent_id) childrenOf.set(item.parent_id, [...(childrenOf.get(item.parent_id) ?? []), item]);
  }
  const blocks: { block: string; items: HrOrgDepartment[] }[] = [];
  for (const item of all.filter((department) => !department.parent_id)) {
    const name = item.block ?? "Khác";
    const group = blocks.find((entry) => entry.block === name);
    if (group) group.items.push(item);
    else blocks.push({ block: name, items: [item] });
  }
  const total = all.reduce((sum, item) => sum + item.people, 0);
  return (
    <div className="space-y-4">
      <PageHeader title="Sơ đồ tổ chức" subtitle={`${all.length} phòng ban · ${total} chỗ ngồi đang hoạt động`} />
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {blocks.map((group) => (
          <section key={group.block} className="panel p-4">
            <p className="brand-eyebrow">{group.block}</p>
            <ul className="mt-3 space-y-2">
              {group.items.map((item) => (
                <Node key={item.id} item={item} children={childrenOf.get(item.id) ?? []} depth={0} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
