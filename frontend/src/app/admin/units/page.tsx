"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { UnitPanel } from "@/components/unit-panel";

/**
 * Tags, roles and settings per unit. Who may administer which unit is the
 * server's decision: this page offers the units `/api/units/me` lists under
 * `can_admin`, and every write is refused there if it should be.
 */
export default function UnitsAdminPage() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const [chosen, setChosen] = useState<string | null>(null);

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  const units = me.data.can_admin;
  if (units.length === 0) {
    return (
      <div className="space-y-4">
        <PageHeader title="Quản trị đơn vị" />
        <p className="text-sm text-[var(--text-muted)]">
          Bạn không quản trị đơn vị nào.
        </p>
      </div>
    );
  }
  const code = chosen && units.includes(chosen) ? chosen : units[0];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Quản trị đơn vị"
        subtitle="Gắn tag ban cho thành viên quyết định họ thấy task nào và có mặt trong chuỗi sản xuất của ban nào."
      />
      {units.length > 1 ? (
        <div
          role="tablist"
          aria-label="Đơn vị"
          className="flex gap-2 border-b border-[var(--border)]"
        >
          {units.map((unit) => (
            <button
              key={unit}
              type="button"
              role="tab"
              aria-selected={unit === code}
              onClick={() => setChosen(unit)}
              className={`min-h-11 px-3 text-sm ${unit === code ? "border-b-2 border-[var(--text)] font-medium" : "text-[var(--text-muted)]"}`}
            >
              {me.data.units.find((item) => item.code === unit)?.label ?? unit}
            </button>
          ))}
        </div>
      ) : null}
      <UnitPanel code={code} />
    </div>
  );
}
