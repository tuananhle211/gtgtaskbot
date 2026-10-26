"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, ApiError, type PerformanceFigures } from "@/lib/api";
import { PageHeader, Select } from "@/components/pr";
import { PersonLink } from "@/components/person-link";
import {
  BAR_FILL,
  ColumnChart,
  ExportLink,
  MonthField,
  Tiles,
  figureTiles,
  formatScore,
  monthOf,
  trendColumns,
} from "@/components/performance";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { monthLabel } from "@/lib/labels";

/**
 * "Hiệu suất › Phòng của tôi": a Trưởng phòng's direct reports (their
 * "Trưởng quản lý" is this head), one row each, highest points first; the
 * company viewers pick any department, or everybody. Month and department
 * on the URL (`?month=`, `?department=`). A name opens the person's page.
 */
export default function TeamPerformancePage() {
  return (
    <Suspense fallback={<Loading />}>
      <TeamPerformance />
    </Suspense>
  );
}

function TeamPerformance() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const month = params.get("month") || monthOf(new Date());
  const department = params.get("department") || undefined;
  const team = useQuery({
    queryKey: ["performance", "team", month, department ?? ""],
    queryFn: () => api.performanceTeam(month, department),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const go = (next: { month?: string; department?: string | null }) => {
    const query = new URLSearchParams(params.toString());
    if (next.month) query.set("month", next.month);
    if (next.department !== undefined) {
      if (next.department) query.set("department", next.department);
      else query.delete("department");
    }
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
  };

  if (team.isPending) return <Loading label="Đang tải điểm của phòng…" />;
  if (team.isError) {
    if (team.error instanceof ApiError && team.error.status === 403) {
      return (
        <section role="alert" className="panel p-6">
          <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            {team.error.message} Trưởng phòng xem được điểm của nhân viên báo cáo trực tiếp cho mình.
          </p>
        </section>
      );
    }
    return <ErrorBox error={team.error} onRetry={() => team.refetch()} />;
  }
  const data = team.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title={data.view_all ? "Điểm hiệu suất theo phòng" : "Điểm hiệu suất phòng của tôi"}
        subtitle={`${monthLabel(month)} · ${
          data.view_all ? "chọn phòng để xem từng người." : "nhân viên báo cáo trực tiếp cho bạn."
        }`}
        action={data.export ? <ExportLink href={api.performanceExportUrl(month, department)} /> : undefined}
      />
      <div className="panel flex flex-wrap items-center gap-4 p-3">
        <MonthField value={month} onChange={(value) => go({ month: value })} />
        {data.departments.length > 0 ? (
          <label className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)]">
            Phòng
            <Select
              aria-label="Phòng"
              value={department ?? ""}
              onChange={(event) => go({ department: event.target.value || null })}
              className="min-w-48"
            >
              <option value="">{data.view_all ? "Tất cả nhân sự" : "Mọi phòng tôi quản lý"}</option>
              {data.departments.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
        {team.isFetching ? <span className="text-xs text-[var(--text-muted)]">Đang tải…</span> : null}
      </div>
      <Tiles label="Tổng của phòng" tiles={figureTiles(data.totals)} />
      <ColumnChart title="Tổng điểm 6 tháng gần nhất" data={trendColumns(data.trend)} />
      <TeamTable rows={data.rows} totals={data.totals} />
    </div>
  );
}

function TeamTable({
  rows,
  totals,
}: {
  rows: Array<{ user_id: string; name: string; departments: string[]; figures: PerformanceFigures }>;
  totals: PerformanceFigures;
}) {
  if (rows.length === 0) {
    return (
      <div className="panel p-4">
        <Empty message="Chưa có nhân viên nào báo cáo trực tiếp cho bạn ở đây." />
      </div>
    );
  }
  const max = Math.max(1, ...rows.map((row) => row.figures.points));
  return (
    <section className="panel overflow-hidden" aria-label="Điểm từng người">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[52rem] text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
              <th className="px-4 py-2 font-medium">Nhân sự</th>
              <th className="px-4 py-2 font-medium">Tổng điểm</th>
              <th className="px-4 py-2 text-right font-medium">Task xong</th>
              <th className="px-4 py-2 text-right font-medium">Dự kiến</th>
              <th className="px-4 py-2 text-right font-medium" title="Làm lỗi / Đổi yêu cầu / Mã chưa hợp lý">
                Bị trả
              </th>
              <th className="px-4 py-2 text-right font-medium">Trễ hạn</th>
              <th className="px-4 py-2 text-right font-medium">Tháng trước</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {rows.map((row) => {
              const figures = row.figures;
              return (
                <tr key={row.user_id}>
                  <td className="px-4 py-2.5">
                    <PersonLink userId={row.user_id} name={row.name} className="font-medium" />
                    <p className="text-xs text-[var(--text-muted)]">{row.departments.join(", ") || "Chưa có phòng"}</p>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="w-14 text-right font-semibold tabular-nums">{formatScore(figures.points)}</span>
                      <span aria-hidden="true" className="h-2 w-32 overflow-hidden rounded-r-[4px] bg-[var(--surface-muted)]">
                        <span
                          className="block h-full rounded-r-[4px]"
                          style={{ width: `${(figures.points / max) * 100}%`, background: BAR_FILL }}
                        />
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{figures.steps_done}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-[var(--text-muted)]">
                    {formatScore(figures.pending_points)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    <span className={figures.returns_fault ? "text-[var(--warn)]" : ""}>{figures.returns_fault}</span>
                    <span className="text-[var(--text-muted)]">
                      {" "}/ {figures.returns_other} / {figures.returns_codes}
                    </span>
                  </td>
                  <td className={`px-4 py-2.5 text-right tabular-nums ${figures.late_count ? "text-[var(--warn)]" : ""}`}>
                    {figures.late_count}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-[var(--text-muted)]">
                    {formatScore(figures.previous_points)}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-[var(--border)] font-semibold">
              <td className="px-4 py-2.5">Tổng · {rows.length} người</td>
              <td className="px-4 py-2.5">
                <span className="inline-block w-14 text-right tabular-nums">{formatScore(totals.points)}</span>
              </td>
              <td className="px-4 py-2.5 text-right tabular-nums">{totals.steps_done}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(totals.pending_points)}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">
                {totals.returns_fault} / {totals.returns_other} / {totals.returns_codes}
              </td>
              <td className="px-4 py-2.5 text-right tabular-nums">{totals.late_count}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(totals.previous_points)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
