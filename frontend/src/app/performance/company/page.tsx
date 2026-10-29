"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { phaseStyle } from "@/components/design-codes-picker";
import { PageHeader } from "@/components/pr";
import { PersonLink } from "@/components/person-link";
import {
  BarList,
  QuotaBar,
  ColumnChart,
  ExportLink,
  MonthField,
  PHASE_NAMES,
  Tiles,
  formatScore,
  monthOf,
  trendColumns,
} from "@/components/performance";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { monthLabel } from "@/lib/labels";

/**
 * "Hiệu suất › Toàn công ty" (OWNER / ADMIN / a `PERFORMANCE VIEW ALL`
 * grant): the month's points by department (the department of each step's
 * phase), the last six months, the ten highest scorers and the codes that
 * made the most points. A department's name opens its people.
 */
export default function CompanyPerformancePage() {
  return (
    <Suspense fallback={<Loading />}>
      <CompanyPerformance />
    </Suspense>
  );
}

const percent = (value: number | null) => (value === null ? "–" : `${Math.round(value * 100)}%`);

function CompanyPerformance() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const month = params.get("month") || monthOf(new Date());
  const overview = useQuery({
    queryKey: ["performance", "overview", month],
    queryFn: () => api.performanceOverview(month),
    placeholderData: keepPreviousData,
    retry: false,
  });

  if (overview.isPending) return <Loading label="Đang tải báo cáo…" />;
  if (overview.isError) {
    if (overview.error instanceof ApiError && overview.error.status === 403) {
      return (
        <section role="alert" className="panel p-6">
          <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">{overview.error.message}</p>
        </section>
      );
    }
    return <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />;
  }
  const data = overview.data;
  const teamHref = (departmentId: string | null) =>
    `/performance/team?month=${month}${departmentId ? `&department=${departmentId}` : ""}`;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Điểm hiệu suất toàn công ty"
        subtitle={`${monthLabel(month)} · điểm tính cho phòng của bước (Dựng cho phòng Dựng, Design cho phòng Thiết kế…).`}
        action={data.export ? <ExportLink href={api.performanceExportUrl(month)} /> : undefined}
      />
      <div className="panel flex flex-wrap items-center gap-4 p-3">
        <MonthField
          value={month}
          onChange={(value) => router.replace(`${pathname}?month=${value}`, { scroll: false })}
        />
        {overview.isFetching ? <span className="text-xs text-[var(--text-muted)]">Đang tải…</span> : null}
      </div>
      <Tiles
        label="Tổng toàn công ty"
        tiles={[
          { label: "Tổng điểm", value: formatScore(data.points), accent: true },
          { label: "Task xong", value: String(data.steps_done) },
          { label: "Người có điểm", value: String(data.people) },
          {
            label: "Điểm trung bình",
            value: formatScore(data.people ? data.points / data.people : 0),
            hint: "Mỗi người có điểm trong tháng",
            extra: data.quota ? (
              <QuotaBar
                points={data.people ? data.points / data.people : 0}
                quota={data.quota.monthly}
                toDate={data.quota.to_date}
                label="Định mức / người"
              />
            ) : undefined,
          },
        ]}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <BarList
          title="Điểm theo phòng"
          rows={data.departments.map((row) => ({
            key: row.department_id ?? "none",
            name: row.name,
            label: (
              <Link href={teamHref(row.department_id)} className="hover:underline">
                {row.name}
              </Link>
            ),
            value: row.points,
            target: data.quota ? data.quota.monthly * row.people : null,
            detail: `${row.people} người · TB ${formatScore(row.average)}`,
          }))}
        />
        <ColumnChart title="Tổng điểm 6 tháng gần nhất" data={trendColumns(data.trend)} />
      </div>
      <section className="panel overflow-hidden" aria-labelledby="company-departments">
        <h2 id="company-departments" className="border-b border-[var(--border)] px-4 py-3 text-base font-semibold">
          Các phòng
        </h2>
        {data.departments.length === 0 ? (
          <div className="p-4">
            <Empty message="Chưa có điểm nào trong tháng." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                  <th className="px-4 py-2 font-medium">Phòng</th>
                  <th className="px-4 py-2 text-right font-medium">Người</th>
                  <th className="px-4 py-2 text-right font-medium">Tổng điểm</th>
                  <th className="px-4 py-2 text-right font-medium">TB / người</th>
                  <th className="px-4 py-2 font-medium">Định mức</th>
                  <th className="px-4 py-2 text-right font-medium">Task xong</th>
                  <th className="px-4 py-2 text-right font-medium">Đúng hạn</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {data.departments.map((row) => (
                  <tr key={row.department_id ?? "none"}>
                    <td className="px-4 py-2.5 font-medium">
                      <Link href={teamHref(row.department_id)} className="text-[var(--accent)] hover:underline">
                        {row.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{row.people}</td>
                    <td className="px-4 py-2.5 text-right font-semibold tabular-nums">{formatScore(row.points)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(row.average)}</td>
                    <td className="px-4 py-2.5">
                      {data.quota ? (
                        <QuotaBar points={row.points} quota={data.quota.monthly * row.people} compact />
                      ) : (
                        "–"
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{row.steps_done}</td>
                    <td className={`px-4 py-2.5 text-right tabular-nums ${row.late_count ? "text-[var(--warn)]" : ""}`}>
                      {percent(row.on_time_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="panel overflow-hidden" aria-labelledby="company-top">
          <h2 id="company-top" className="border-b border-[var(--border)] px-4 py-3 text-base font-semibold">
            10 người điểm cao nhất
          </h2>
          {data.top.length === 0 ? (
            <div className="p-4">
              <Empty message="Chưa có điểm nào trong tháng." />
            </div>
          ) : (
            <ol className="divide-y divide-[var(--border)]">
              {data.top.map((row, index) => (
                <li key={row.user_id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  <span className="w-5 text-right text-xs tabular-nums text-[var(--text-muted)]">{index + 1}</span>
                  <span className="min-w-0 flex-1">
                    <PersonLink userId={row.user_id} name={row.name} className="font-medium" />
                    <span className="block truncate text-xs text-[var(--text-muted)]">
                      {row.departments.join(", ") || "Chưa có phòng"}
                    </span>
                  </span>
                  <span className="text-right">
                    <span className="font-semibold tabular-nums">{formatScore(row.figures.points)}</span>
                    {data.quota ? (
                      <span className="block text-[11px] tabular-nums text-[var(--text-muted)]">
                        {Math.round((row.figures.points / data.quota.monthly) * 100)}% định mức
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
        <section className="panel overflow-hidden" aria-labelledby="company-codes">
          <h2 id="company-codes" className="border-b border-[var(--border)] px-4 py-3 text-base font-semibold">
            Mã task nhiều điểm nhất
          </h2>
          {data.codes.length === 0 ? (
            <div className="p-4">
              <Empty message="Chưa có mã nào được tính." />
            </div>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {data.codes.map((row) => (
                <li key={`${row.phase}-${row.code}-${row.form ?? ""}`} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  <span
                    className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${phaseStyle(row.phase).chip}`}
                    title={PHASE_NAMES[row.phase]}
                  >
                    {row.code}
                  </span>
                  {row.form_label ? <span className="shrink-0 text-xs">{row.form_label}</span> : null}
                  <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-muted)]" title={row.name}>
                    {row.name || PHASE_NAMES[row.phase]}
                  </span>
                  <span className="text-xs tabular-nums text-[var(--text-muted)]">× {row.quantity}</span>
                  <span className="w-14 text-right font-semibold tabular-nums">{formatScore(row.points)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
