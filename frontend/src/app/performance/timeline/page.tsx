"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader, Select } from "@/components/pr";
import { monthOf } from "@/components/performance";
import { Gantt, GanttLegend } from "@/components/performance-gantt";
import { foldText } from "@/components/searchable-select";
import { ErrorBox, Loading } from "@/components/states";
import { monthLabel } from "@/lib/labels";

/**
 * "Hiệu suất › Tiến độ (Gantt)": the steps each person took in the month,
 * as bars on the month's days, with what they are (order, step, task
 * codes) and their points. Everybody sees their own; a Trưởng phòng also
 * their direct reports; the company viewers pick a department, or see
 * everybody with a step in the month. Month and department on the URL.
 */
export default function TimelinePage() {
  return (
    <Suspense fallback={<Loading />}>
      <Timeline />
    </Suspense>
  );
}

function shift(month: string, by: number): string {
  const [year, number] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, number - 1 + by, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

function Timeline() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const month = params.get("month") || monthOf(new Date());
  const department = params.get("department") || undefined;
  const [search, setSearch] = useState("");
  const timeline = useQuery({
    queryKey: ["performance", "timeline", month, department ?? ""],
    queryFn: () => api.performanceTimeline(month, department),
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
  const groups = useMemo(() => {
    const all = timeline.data?.groups ?? [];
    const needle = foldText(search.trim());
    if (!needle) return all;
    return all
      .map((group) =>
        foldText(group.name).includes(needle)
          ? group
          : {
              ...group,
              bars: group.bars.filter((bar) =>
                foldText(`${bar.order_code} ${bar.order_title} ${bar.codes.join(" ")}`).includes(needle),
              ),
            },
      )
      .filter((group) => foldText(group.name).includes(needle) || group.bars.length > 0);
  }, [timeline.data, search]);

  if (timeline.isPending) return <Loading label="Đang tải tiến độ…" />;
  if (timeline.isError) return <ErrorBox error={timeline.error} onRetry={() => timeline.refetch()} />;
  const data = timeline.data;
  const nav = "inline-flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]";
  return (
    <div className="space-y-4">
      <PageHeader
        title="Tiến độ (Gantt)"
        subtitle="Các task từng người đã nhận trong tháng: từ lúc nhận tới lúc xong (hoặc hôm nay nếu đang làm), màu theo bước, kèm mã task và điểm."
      />
      <div className="panel flex flex-wrap items-center gap-3 p-3">
        <div className="inline-flex items-center gap-2">
          <button type="button" className={nav} aria-label="Tháng trước" onClick={() => go({ month: shift(month, -1) })}>
            ‹
          </button>
          <span className="min-w-32 text-center text-sm font-semibold">{monthLabel(month)}</span>
          <button type="button" className={nav} aria-label="Tháng sau" onClick={() => go({ month: shift(month, 1) })}>
            ›
          </button>
        </div>
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
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Tìm người, mã order, mã task…"
          aria-label="Tìm trong tiến độ"
          className="min-h-10 min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)] sm:max-w-xs"
        />
        {timeline.isFetching ? <span className="text-xs text-[var(--text-muted)]">Đang tải…</span> : null}
      </div>
      <GanttLegend />
      <Gantt month={data.month} groups={groups} />
    </div>
  );
}
