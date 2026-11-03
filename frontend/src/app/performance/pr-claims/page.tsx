"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type PrClaim } from "@/lib/api";
import { formatScore } from "@/components/performance";
import { ClaimLines } from "@/components/pr-points-panel";
import { PageHeader } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDateTime } from "@/lib/datetime";

/**
 * "Hiệu suất › Điểm PR" (0085; no approval since 2026-11-03): the PR task
 * codes for a Trưởng phòng PR (or the OWNER / an ADMIN) to review, newest
 * first - "Chưa cộng" while the piece is not done, "Đã cộng" once it is.
 * Editing happens on the task page (link). Everybody else sees their own.
 */
type Tab = "PENDING" | "APPROVED";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "PENDING", label: "Chưa cộng (task chưa xong)" },
  { key: "APPROVED", label: "Đã cộng" },
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
        title="Điểm PR"
        subtitle="Mã task nhân viên PR tự nhập cho phần Nội dung / Sản xuất của từng bài. Không cần duyệt: điểm được cộng khi task hoàn thành. Trưởng phòng PR sửa mã trên trang task đến khi task hoàn thành."
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
          <Empty message={tab === "PENDING" ? "Không có mã nào đang chờ task hoàn thành." : "Chưa có gì ở đây."} />
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
          <p className="text-xs text-[var(--text-muted)]">Nhập {formatDateTime(claim.submitted_at)}</p>
        </div>
        <span className="text-right">
          <span className="block text-xl font-semibold tabular-nums">{formatScore(claim.points)}</span>
          <span className="text-xs text-[var(--text-muted)]">điểm</span>
        </span>
      </div>
      <ClaimLines items={claim.items} />
      {claim.note ? <p className="mt-2 text-sm text-[var(--text-muted)]">{claim.note}</p> : null}
      {claim.status === "APPROVED" ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          <Pill tone="good">Đã cộng điểm</Pill> {claim.decided_at ? formatDateTime(claim.decided_at) : ""}
        </p>
      ) : (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          <Pill>Cộng khi hoàn thành</Pill>
        </p>
      )}
    </li>
  );
}
