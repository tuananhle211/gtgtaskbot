"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SUMMARY_BUCKETS, capabilityLabel, formatWhen } from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { ContentCard, PageHeader, StatTile } from "@/components/pr";

/**
 * The landing page.
 *
 * Every figure comes from `/api/pr/dashboard`, counted live. Nothing on this page
 * is computed in the browser from a partial list, because a count derived from a
 * paginated response is a number that looks authoritative and is not. Step 1E.2
 * grouped those counts into the same four buckets the content workspace uses -
 * grouping is presentation; the numbers are still the server's.
 *
 * "Chờ bạn duyệt" is empty for anybody without a review grant, including an
 * OWNER. That is the server's answer, and it is the honest one: without a grant
 * `PrApprovalService` would refuse every item in the queue. The queue leads with
 * the **title** of each piece of work, because a reviewer opens a queue to see
 * what they have to read, not to check codes off a list.
 */
export default function DashboardPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });

  if (dashboard.isPending) return <Loading label="Đang tải tổng quan…" />;
  if (dashboard.isError) {
    return <ErrorBox error={dashboard.error} onRetry={() => dashboard.refetch()} />;
  }

  const data = dashboard.data;
  const byStage = new Map(data.stage_counts.map((row) => [row.stage, row.count]));
  const names = new Map((people.data ?? []).map((person) => [person.user_id, person.full_name]));
  const total = (stages: string[]) =>
    stages.reduce((sum, code) => sum + (byStage.get(code) ?? 0), 0);

  return (
    <div className="space-y-6">
      <PageHeader title="Tổng quan" subtitle="Toàn bộ số liệu đếm trực tiếp lúc mở trang." />

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {SUMMARY_BUCKETS.map((bucket) => (
          <StatTile key={bucket.key} label={bucket.label} value={total(bucket.stages)} />
        ))}
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold">
          Chờ bạn duyệt
          {data.my_capabilities.length > 0 ? (
            <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
              {data.my_capabilities.map(capabilityLabel).join(", ")}
            </span>
          ) : null}
        </h2>
        {data.awaiting_my_review.length === 0 ? (
          <Empty
            message={
              data.my_capabilities.length === 0
                ? "Bạn hiện chưa được cấp quyền duyệt ở bước nào, nên không có gì chờ bạn."
                : "Không có nội dung nào đang chờ bạn duyệt."
            }
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.awaiting_my_review.map((item) => (
              <ContentCard
                key={item.id}
                item={item}
                showStage
                assignee={names.get(item.owner_user_id)}
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">Task quá hạn</h2>
        {data.overdue_tasks.length === 0 ? (
          <Empty message="Không có task nào quá hạn." />
        ) : (
          <ul className="space-y-2">
            {data.overdue_tasks.map((task) => (
              <li
                key={task.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 text-sm"
              >
                <span className="min-w-0">
                  <span className="block">{task.title}</span>
                  <code className="text-[11px] text-[var(--text-muted)]">{task.code}</code>
                </span>
                <Pill tone="bad">Hạn {formatWhen(task.deadline)}</Pill>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">Nội dung mới nhất</h2>
        {data.recent_content.length === 0 ? (
          <Empty message="Chưa có nội dung nào." />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.recent_content.map((item) => (
              <ContentCard
                key={item.id}
                item={item}
                showStage
                assignee={names.get(item.owner_user_id)}
              />
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Xem đầy đủ theo giai đoạn ở{" "}
          <Link className="underline" href="/pr/content">
            Nội dung
          </Link>
          .
        </p>
      </section>
    </div>
  );
}
