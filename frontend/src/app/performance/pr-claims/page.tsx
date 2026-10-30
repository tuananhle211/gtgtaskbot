"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, RETURN_REASONS, type PrClaim } from "@/lib/api";
import { phaseStyle } from "@/components/design-codes-picker";
import { formatScore } from "@/components/performance";
import { Decision } from "@/components/pr-points-panel";
import { PageHeader } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDateTime } from "@/lib/datetime";

/**
 * "Hiệu suất › Duyệt điểm PR" (0085): the PR task codes waiting for a
 * Trưởng phòng PR (or the OWNER / an ADMIN), newest first, each decided on
 * the spot - approve, or refuse with one of the three reasons. Tabs for what
 * was already decided. Everybody else sees their own claims.
 */
type Tab = "PENDING" | "APPROVED" | "REJECTED";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "PENDING", label: "Chờ duyệt" },
  { key: "APPROVED", label: "Đã duyệt" },
  { key: "REJECTED", label: "Không duyệt" },
];

export default function PrClaimsPage() {
  const [tab, setTab] = useState<Tab>("PENDING");
  const claims = useQuery({
    queryKey: ["pr-claims", tab],
    queryFn: () => api.prClaims(tab),
    retry: false,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Duyệt điểm PR"
        subtitle="Mã task nhân viên PR tự nhập cho phần Nội dung / Sản xuất của từng bài. Duyệt xong điểm mới được tính."
      />
      <div role="tablist" aria-label="Trạng thái" className="flex gap-1">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`min-h-9 rounded-full border px-3 text-sm font-medium ${
              tab === item.key
                ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {claims.isPending ? (
        <Loading label="Đang tải…" />
      ) : claims.isError ? (
        claims.error instanceof ApiError && claims.error.status === 403 ? (
          <section role="alert" className="panel p-6 text-sm text-[var(--text-muted)]">
            {claims.error.message}
          </section>
        ) : (
          <ErrorBox error={claims.error} onRetry={() => claims.refetch()} />
        )
      ) : claims.data.length === 0 ? (
        <div className="panel p-4">
          <Empty message={tab === "PENDING" ? "Không có mã nào chờ duyệt." : "Chưa có gì ở đây."} />
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Phiếu điểm PR">
          {claims.data.map((claim) => (
            <ClaimCard key={claim.id} claim={claim} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ClaimCard({ claim }: { claim: PrClaim }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe, retry: false });
  const reviews = Boolean(me.data?.performance?.review_pr);
  const lines = Object.entries(claim.items).flatMap(([phase, items]) =>
    items.map((item) => ({ phase, item })),
  );
  return (
    <li className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm">
            <span className="font-semibold">{claim.name}</span>
            <span className="text-[var(--text-muted)]"> · {claim.stage_label}</span>
          </p>
          {claim.content_code ? (
            <p className="truncate text-xs text-[var(--text-muted)]">
              <Link href={`/tasks/${claim.content_code}`} className="font-mono font-semibold text-[var(--accent)] hover:underline">
                {claim.content_code}
              </Link>{" "}
              {claim.content_title}
            </p>
          ) : null}
          <p className="text-xs text-[var(--text-muted)]">Gửi {formatDateTime(claim.submitted_at)}</p>
        </div>
        <span className="text-right">
          <span className="block text-xl font-semibold tabular-nums">{formatScore(claim.points)}</span>
          <span className="text-xs text-[var(--text-muted)]">điểm</span>
        </span>
      </div>
      <ul className="mt-3 flex flex-wrap gap-1.5">
        {lines.map(({ phase, item }, index) => (
          <li
            key={`${item.code_id}-${index}`}
            className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${phaseStyle(phase).chip}`}
            title={item.name}
          >
            {item.free ? `Việc khác: ${item.note ?? ""}` : item.code}
            {item.quantity > 1 ? ` × ${item.quantity}` : ""}
            {item.form && item.form !== "NEW" && !item.free ? ` · ${item.form_label}` : ""}
            <span className="ml-1 font-normal text-[var(--text-muted)]">({formatScore(item.points ?? 0)})</span>
          </li>
        ))}
      </ul>
      {claim.note ? <p className="mt-2 text-sm text-[var(--text-muted)]">{claim.note}</p> : null}
      {claim.status === "REJECTED" ? (
        <p className="mt-2 text-xs text-[var(--bad)]">
          <Pill tone="bad">Không duyệt</Pill>{" "}
          {RETURN_REASONS.find((item) => item.value === claim.return_reason)?.label}
          {claim.decision_note ? ` - ${claim.decision_note}` : ""}
        </p>
      ) : null}
      {claim.status === "APPROVED" ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          <Pill tone="good">Đã duyệt</Pill> {claim.decided_by}
        </p>
      ) : null}
      {claim.status === "PENDING" && reviews ? (
        <Decision claim={claim} contentId={claim.content_id} />
      ) : null}
    </li>
  );
}
