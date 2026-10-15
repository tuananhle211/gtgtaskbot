"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { ResourceTable } from "@/components/resource-table";

/** "Tài nguyên của tôi": what the signed-in person is holding right now. */
export default function MyResourcesPage() {
  const list = useQuery({
    queryKey: ["resources", "list", { mine: true }],
    queryFn: () => api.resources({ mine: true, limit: 500 }),
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Tài nguyên của tôi"
        subtitle="Những tài nguyên đang được bàn giao cho bạn. Có sai sót thì báo Phòng Tài nguyên."
      />
      {list.isPending ? <Loading label="Đang tải…" /> : null}
      {list.isError ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      {list.data ? (
        <div className="panel">
          <ResourceTable rows={list.data.items} emptyMessage="Bạn chưa giữ tài nguyên nào." />
        </div>
      ) : null}
    </div>
  );
}
