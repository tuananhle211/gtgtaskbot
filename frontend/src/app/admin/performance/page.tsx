"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type PerfLine, type PerfReport, type PerfVerdict } from "@/lib/api";
import { AdminTabs } from "@/components/admin-tabs";
import { PageHeader } from "@/components/pr";
import { formatMs } from "@/lib/perf";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Quản trị › Hiệu năng" (0103, plan docs/ops/SCALING_PLAN.md D), OWNER / ADMIN.
 *
 * How fast the API answers (per route template, never a real path) and how
 * long background tasks wait in their queue, over the last 1 / 7 / 30 days.
 * The two sentences at the top are the server's verdict: whether the peak-hour
 * p95 is over the đợt 2 threshold, whether tasks are being dropped.
 */
const PERIODS = [
  { days: 1, label: "24 giờ" },
  { days: 7, label: "7 ngày" },
  { days: 30, label: "30 ngày" },
] as const;

export default function PerformancePage() {
  const [days, setDays] = useState<number>(7);
  const report = useQuery({
    queryKey: ["perf-report", days],
    queryFn: () => api.perfReport(days),
    retry: false,
  });
  return (
    <div className="space-y-4">
      <AdminTabs active="performance" />
      <PageHeader
        title="Hiệu năng hệ thống"
        subtitle="Web trả lời nhanh hay chậm, trang nào chậm, và tác vụ nền (đồng bộ, thông báo, hộp chat) có phải chờ lâu không."
      />
      <div role="group" aria-label="Khoảng thời gian" className="flex flex-wrap gap-1.5">
        {PERIODS.map((period) => {
          const selected = period.days === days;
          return (
            <button
              key={period.days}
              type="button"
              aria-pressed={selected}
              onClick={() => setDays(period.days)}
              className={`min-h-11 rounded-lg px-3.5 text-sm transition-colors ${
                selected
                  ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                  : "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
              }`}
            >
              {period.label}
            </button>
          );
        })}
      </div>
      {report.isPending ? <Loading label="Đang tải số liệu…" /> : null}
      {report.isError ? <ErrorBox error={report.error} onRetry={() => report.refetch()} /> : null}
      {report.data ? <Report data={report.data} /> : null}
    </div>
  );
}

const COUNT = (value: number) => value.toLocaleString("vi-VN");

const VERDICT_STYLE: Record<PerfVerdict["level"], string> = {
  good: "border-[var(--good)]/30 bg-[var(--good-soft)]",
  warn: "border-[var(--warn)]/30 bg-[var(--warn-soft)]",
  bad: "border-[var(--bad)]/30 bg-[var(--bad-soft)]",
  none: "border-[var(--border)] bg-[var(--surface-muted)]",
};

function Verdict({ title, verdict }: { title: string; verdict: PerfVerdict }) {
  return (
    <div data-level={verdict.level} className={`rounded-xl border p-4 text-sm ${VERDICT_STYLE[verdict.level]}`}>
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-[var(--text)]">{verdict.message}</p>
    </div>
  );
}

function Tile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "bad" | "good" }) {
  const color = tone === "bad" ? "text-[var(--bad)]" : tone === "good" ? "text-[var(--good)]" : "";
  return (
    <li className="panel p-4">
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-[var(--text-muted)]">{hint}</p> : null}
    </li>
  );
}

function Report({ data }: { data: PerfReport }) {
  const peakOver = data.api.peak_p95_ms !== null && data.api.peak_p95_ms > data.api.limit_ms;
  // A colour only once the server has enough peak-hour requests to judge.
  const judged = data.api.verdict.level !== "none";
  return (
    <>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-2">
        <Verdict title="Web (API)" verdict={data.api.verdict} />
        <Verdict title="Tác vụ nền" verdict={data.queue_verdict} />
      </div>

      <ul aria-label="Chỉ số tổng" className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Số yêu cầu" value={COUNT(data.api.count)} />
        <Tile label="Trung bình" value={formatMs(data.api.average_ms)} />
        <Tile
          label="95% xong trong (giờ cao điểm)"
          value={formatMs(data.api.peak_p95_ms)}
          hint={`8:00–18:00 · ngưỡng ${data.api.limit_ms} ms`}
          tone={judged ? (peakOver ? "bad" : "good") : undefined}
        />
        <Tile label="Chậm trên 1 giây" value={COUNT(data.api.slow)} tone={data.api.slow ? "bad" : undefined} />
        <Tile label="Lỗi máy chủ" value={COUNT(data.api.failed)} tone={data.api.failed ? "bad" : undefined} />
      </ul>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Trang / thao tác chậm nhất</h2>
        <PerfTable
          lines={data.routes}
          empty="Chưa có số liệu."
          first="Đường dẫn"
          slowLabel="Trên 1 s"
          lastLabel="Lỗi"
          last={(line) => line.failed}
          name={(line) => (
            <>
              <span className="mr-1.5 rounded bg-[var(--surface-muted)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--text-muted)]">
                {line.method}
              </span>
              <span className="break-all">{line.name}</span>
            </>
          )}
        />
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Tác vụ nền: thời gian chờ trước khi chạy</h2>
        <PerfTable
          lines={data.queue}
          empty="Chưa có số liệu (worker ghi mỗi phút)."
          first="Tác vụ"
          slowLabel="Chờ trên 1 phút"
          lastLabel="Bị bỏ vì chờ lâu"
          last={(line) => line.dropped}
          name={(line) => <span className="break-all">{line.name}</span>}
        />
      </section>

      <p className="text-xs text-[var(--text-muted)]">
        “95% xong trong” = 95 trên 100 lượt xong trong khoảng thời gian đó (làm tròn lên mốc gần nhất). Số liệu
        gom theo giờ, giữ 30 ngày, không ghi ai gọi hay nội dung gửi.
      </p>
    </>
  );
}

function PerfTable({
  lines,
  empty,
  first,
  slowLabel,
  lastLabel,
  last,
  name,
}: {
  lines: PerfLine[];
  empty: string;
  first: string;
  slowLabel: string;
  lastLabel: string;
  last: (line: PerfLine) => number;
  name: (line: PerfLine) => React.ReactNode;
}) {
  if (lines.length === 0) {
    return <p className="panel p-4 text-sm text-[var(--text-muted)]">{empty}</p>;
  }
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense w-full min-w-[44rem]">
        <thead>
          <tr>
            <th className="text-left">{first}</th>
            <th className="text-right">Số lần</th>
            <th className="text-right">Trung bình</th>
            <th className="text-right">95% trong</th>
            <th className="text-right">Lâu nhất</th>
            <th className="text-right">{slowLabel}</th>
            <th className="text-right">{lastLabel}</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={`${line.method} ${line.name}`}>
              <td className="min-w-[15rem] max-w-[22rem] text-sm">{name(line)}</td>
              <td className="text-right tabular-nums">{COUNT(line.count)}</td>
              <td className="text-right tabular-nums">{formatMs(line.average_ms)}</td>
              <td className="text-right tabular-nums">{formatMs(line.p95_ms)}</td>
              <td className="text-right tabular-nums">{line.count ? formatMs(line.max_ms) : "—"}</td>
              <td className={`text-right tabular-nums ${line.slow ? "font-semibold text-[var(--warn)]" : ""}`}>
                {COUNT(line.slow)}
              </td>
              <td className={`text-right tabular-nums ${last(line) ? "font-semibold text-[var(--bad)]" : ""}`}>
                {COUNT(last(line))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
