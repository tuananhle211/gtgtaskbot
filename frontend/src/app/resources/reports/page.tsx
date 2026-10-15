"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { ResourceTable } from "@/components/resource-table";
import { monthOf, statusTone } from "@/lib/resources";

/**
 * "Báo cáo tổng hợp": department × type with the handover rate; the statuses
 * at the end of a chosen month (from the status history, so a past month
 * keeps its figures); and the warning list - at a leaver or a mover, past the
 * review threshold, Checkpoint or Die. The month is on the URL (`?month=`).
 */
export default function ReportsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Reports />
    </Suspense>
  );
}

function Reports() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const month = params.get("month") || monthOf(new Date());
  const summary = useQuery({
    queryKey: ["resources", "summary", month],
    queryFn: () => api.resourceSummary(month),
  });
  const warnings = useQuery({ queryKey: ["resources", "warnings"], queryFn: api.resourceWarnings });
  const percent = (rate: number) => `${Math.round(rate * 100)}%`;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Báo cáo tài nguyên"
        subtitle="Số tài nguyên theo ban × loại, tỷ lệ đã bàn giao, trạng thái theo tháng và danh sách cảnh báo."
      />
      <div className="panel p-3">
        <label className="flex max-w-xs flex-col gap-1 text-xs text-[var(--text-muted)]">
          Tháng (trạng thái tính đến cuối tháng)
          <input
            type="month"
            value={month}
            aria-label="Tháng"
            onChange={(event) =>
              router.replace(
                event.target.value ? `${pathname}?month=${event.target.value}` : pathname,
                { scroll: false },
              )
            }
            className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        </label>
      </div>

      {summary.isPending ? <Loading label="Đang tổng hợp…" /> : null}
      {summary.isError ? <ErrorBox error={summary.error} onRetry={() => summary.refetch()} /> : null}
      {summary.data ? (
        <>
          <section aria-label="Theo ban và loại" className="panel">
            <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-sm font-semibold">Theo ban × loại</h2>
              <p className="text-xs text-[var(--text-muted)]">
                Tổng {summary.data.total} · đã bàn giao {summary.data.handed_over} (
                {percent(summary.data.handover_rate)})
              </p>
            </div>
            <div className="table-scroll-x">
              <table className="table-dense min-w-[40rem]">
                <thead>
                  <tr>
                    <th scope="col">Ban</th>
                    {summary.data.types.map((type) => (
                      <th key={type.id} scope="col" className="text-right">
                        {type.name}
                      </th>
                    ))}
                    <th scope="col" className="text-right">
                      Tổng
                    </th>
                    <th scope="col" className="text-right">
                      Đã bàn giao
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {summary.data.departments.length === 0 ? (
                    <tr>
                      <td colSpan={summary.data.types.length + 3} className="text-[var(--text-muted)]">
                        Chưa có tài nguyên nào.
                      </td>
                    </tr>
                  ) : null}
                  {summary.data.departments.map((line) => (
                    <tr key={line.department.id}>
                      <td className="font-medium">{line.department.name}</td>
                      {summary.data.types.map((type) => (
                        <td key={type.id} className="text-right tabular-nums">
                          {line.by_type[type.id] ?? 0}
                        </td>
                      ))}
                      <td className="text-right font-semibold tabular-nums">{line.total}</td>
                      <td className="text-right tabular-nums">
                        {line.handed_over} ({percent(line.handover_rate)})
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-label="Theo trạng thái" className="panel p-4">
            <h2 className="text-sm font-semibold">Theo trạng thái · cuối tháng {month.slice(5)}/{month.slice(0, 4)}</h2>
            <ul className="mt-3 flex flex-wrap gap-3">
              {summary.data.by_status.map((item) => (
                <li
                  key={item.status.id}
                  className="min-w-[8rem] rounded-lg border border-[var(--border)] px-3 py-2"
                >
                  <Pill tone={statusTone(item.status.status_group)}>{item.status.name}</Pill>
                  <p className="mt-1 text-xl font-semibold tabular-nums">{item.count}</p>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-[var(--text-muted)]">
              {summary.data.by_group.map((group) => `${group.label}: ${group.count}`).join(" · ")}
            </p>
          </section>
        </>
      ) : null}

      <section aria-label="Danh sách cảnh báo" className="panel">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold">Danh sách cảnh báo</h2>
          {warnings.data ? (
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {warnings.data.counts
                .filter((item) => item.count > 0)
                .map((item) => `${item.label}: ${item.count}`)
                .join(" · ") || "Không có cảnh báo nào."}{" "}
              · Ngưỡng rà soát {warnings.data.review_days} ngày.
            </p>
          ) : null}
        </div>
        {warnings.isPending ? <Loading label="Đang tải cảnh báo…" /> : null}
        {warnings.isError ? <ErrorBox error={warnings.error} onRetry={() => warnings.refetch()} /> : null}
        {warnings.data ? (
          <ResourceTable rows={warnings.data.items} emptyMessage="Không có tài nguyên nào cần chú ý." />
        ) : null}
      </section>
    </div>
  );
}
