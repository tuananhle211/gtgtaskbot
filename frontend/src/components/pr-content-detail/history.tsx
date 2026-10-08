"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type ContentVersion } from "@/lib/api";
import {
  decisionPastLabel,
  formatWhen,
  stageLabel,
  transitionHistoryLabel,
} from "@/lib/labels";
import { Empty, Loading, Pill } from "@/components/states";

export function HistoryTab({
  contentId,
  approvals,
  versions,
  names,
  loading,
}: {
  contentId: string;
  approvals: Awaited<ReturnType<typeof api.reviewContext>>["approvals"];
  versions: ContentVersion[];
  names: Map<string, string>;
  loading: boolean;
}) {
  return (
    <div className="space-y-4">
      <TransitionHistory contentId={contentId} names={names} />
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Lịch sử duyệt</h2>
        {approvals.length === 0 ? (
          <Empty message="Chưa có ai duyệt nội dung này." />
        ) : (
          <ul className="space-y-1.5 text-sm">
            {approvals.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center gap-2">
                <Pill tone={event.decision === "APPROVED" ? "good" : "bad"}>
                  {decisionPastLabel(event.decision)}
                </Pill>
                <span className="text-xs text-[var(--text-muted)]">
                  {stageLabel(event.approval_stage)} · v{event.version_reviewed}{" "}
                  · {formatWhen(event.decided_at)}
                </span>
                {event.comment ? (
                  <span className="text-xs">— {event.comment}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Các bản nháp</h2>
        {loading ? <Loading label="Đang tải bản nháp…" /> : null}
        {!loading && versions.length === 0 ? (
          <Empty message="Chưa có bản nháp nào." />
        ) : (
          <ul className="space-y-1.5 text-sm">
            {versions.map((row) => (
              <li key={row.id} className="text-[var(--text-muted)]">
                <span className="font-medium text-[var(--text)]">
                  v{row.version_no}
                </span>{" "}
                · {formatWhen(row.created_at)}
                {row.change_note ? ` · ${row.change_note}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Every stage change, and the ones that were taken back.
 *
 * Step 1F.2.3b. An undo appends rather than erases, so both halves of a
 * reversal pair are here: the original move, labelled "Đã hoàn tác", and the
 * undo underneath it. The label comes from `reversed_by_event_id` on the row -
 * the server pairs them - rather than from comparing timestamps in the browser.
 */
function TransitionHistory({
  contentId,
  names,
}: {
  contentId: string;
  names: Map<string, string>;
}) {
  const history = useQuery({
    queryKey: ["history", contentId],
    queryFn: () => api.contentHistory(contentId),
  });
  const rows = history.data ?? [];

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="mb-2 text-sm font-semibold">Lịch sử thao tác</h2>
      {history.isPending ? <Loading label="Đang tải lịch sử…" /> : null}
      {!history.isPending && rows.length === 0 ? (
        <Empty message="Chưa có bước nào được ghi nhận." />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-[var(--text-muted)]">
                {formatWhen(row.created_at)}
              </span>
              <span>
                {row.actor_user_id
                  ? `${names.get(row.actor_user_id) ?? "—"} · `
                  : ""}
                {/* Worded by the edge, from the central table: the direct
                    submission and the AI handoff land on the same stage, and
                    the row has to say which one this was - see
                    `transitionHistoryLabel`. */}
                {transitionHistoryLabel(row)}
              </span>
              {row.reversed_by_event_id ? (
                <Pill tone="warn">Đã hoàn tác</Pill>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
