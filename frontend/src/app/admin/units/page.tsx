"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { AdminTabs } from "@/components/admin-tabs";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { UnitPanel } from "@/components/unit-panel";
import { taggableUnits, unitName, byStreamOrder } from "@/lib/units";

/**
 * "Quản trị › Thành viên luồng": each stream's roster. Who may administer or
 * tag in which stream is the server's decision: this page offers the streams
 * `/api/units/me` lists under `can_admin` or `can_tag`, and every write is
 * refused there if it should be. Seating people and permissions are "Phòng
 * ban"; the ORD settings are "Thiết lập ORD".
 */
export default function UnitsAdminPage() {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const [chosen, setChosen] = useState<string | null>(null);

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  const units = [
    ...me.data.can_admin,
    ...taggableUnits(me.data).filter((code) => !me.data.can_admin.includes(code)),
  ];
  if (units.length === 0) {
    return (
      <div className="space-y-4">
        <AdminTabs active="units" />
        <PageHeader title="Thành viên luồng" />
        <p className="text-sm text-[var(--text-muted)]">
          Bạn không quản trị luồng nào.
        </p>
      </div>
    );
  }
  const code = chosen && units.includes(chosen) ? chosen : units[0];
  return (
    <div className="space-y-4">
      <AdminTabs active="units" />
      <PageHeader
        title="Thành viên luồng"
        subtitle="Ai đang trong luồng PR / luồng Order (ORD), mã thành viên, kho cá nhân, token/ngày và trạng thái tài khoản. Phòng ban và chức vụ quyết định họ thuộc luồng nào."
      />
      {units.length > 1 ? (
        <div
          role="tablist"
          aria-label="Luồng"
          className="flex gap-2 border-b border-[var(--border)]"
        >
          {byStreamOrder(units).map((unit) => (
            <button
              key={unit}
              type="button"
              role="tab"
              aria-selected={unit === code}
              onClick={() => setChosen(unit)}
              className={`min-h-11 px-3 text-sm ${unit === code ? "border-b-2 border-[var(--text)] font-medium" : "text-[var(--text-muted)]"}`}
            >
              {unitName(unit, me.data.units.find((item) => item.code === unit)?.label)}
            </button>
          ))}
        </div>
      ) : null}
      <UnitPanel code={code} />
    </div>
  );
}
