"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { stageLabel } from "@/lib/labels";
import { ErrorBox, Loading } from "@/components/states";

/**
 * Read-only, and honest about what it is not.
 *
 * There is no PR reporting service yet. The Step 1B schema has the tables -
 * `pr_report_runs`, `pr_report_artifacts`, `pr_reporting_periods`,
 * `pr_weekly_manual_inputs` - and **nothing writes them**, by deliberate design in
 * every step since. So this page shows the only figures that exist: the live stage
 * counts already on the dashboard.
 *
 * It would have been easy to render a chart of invented weekly numbers here and
 * call the page done. A dashboard that guesses is worse than one that says "not
 * built yet", because somebody will act on the guess.
 */
export default function ReportsPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });

  if (dashboard.isPending) return <Loading />;
  if (dashboard.isError) return <ErrorBox error={dashboard.error} onRetry={() => dashboard.refetch()} />;

  const total = dashboard.data.stage_counts.reduce((sum, row) => sum + row.count, 0);

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="text-sm font-semibold">Số lượng nội dung theo bước</h2>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Đếm trực tiếp từ cơ sở dữ liệu tại thời điểm mở trang. Không có số nào được suy đoán.
        </p>
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-[var(--text-muted)]">
              <th className="py-1">Bước</th>
              <th className="py-1 text-right">Số lượng</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.data.stage_counts.map((row) => (
              <tr key={row.stage} className="border-t border-[var(--border)]">
                <td className="py-1.5">{stageLabel(row.stage)}</td>
                <td className="py-1.5 text-right font-medium">{row.count}</td>
              </tr>
            ))}
            <tr className="border-t border-[var(--border)] font-semibold">
              <td className="py-1.5">Tổng</td>
              <td className="py-1.5 text-right">{total}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
        <h2 className="font-semibold">Báo cáo tuần / tháng — chưa xây</h2>
        <p className="mt-1 text-[var(--text-muted)]">
          Các bảng báo cáo (`pr_report_runs`, `pr_reporting_periods`,
          `pr_weekly_manual_inputs`) đã có trong schema từ Step 1B nhưng{" "}
          <strong>chưa có gì ghi vào chúng</strong> — chưa có service tổng hợp, chưa có job định kỳ,
          chưa có kết nối số liệu từ TikTok hay Facebook.
        </p>
        <p className="mt-2 text-[var(--text-muted)]">
          Trang này không vẽ biểu đồ từ số liệu không tồn tại. Khi có service báo cáo thật, nó sẽ hiện
          ở đây.
        </p>
      </section>
    </div>
  );
}
