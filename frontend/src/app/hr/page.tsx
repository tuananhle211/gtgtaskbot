"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { hrApi, money, type HrBrief } from "@/lib/hr";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/datetime";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { ContractPill } from "@/components/hr-profile";

function Tile({ label, value, foot }: { label: string; value: React.ReactNode; foot?: string }) {
  return (
    <div className="panel p-4">
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {foot ? <p className="mt-1 text-xs text-[var(--text-muted)]">{foot}</p> : null}
    </div>
  );
}

function People({ title, rows, extra }: { title: string; rows: HrBrief[]; extra: (row: HrBrief) => React.ReactNode }) {
  return (
    <section className="panel p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--text-muted)]">Không có.</p>
      ) : (
        <ul className="mt-2 divide-y divide-[var(--border)]">
          {rows.map((row) => (
            <li key={row.user_id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0">
                <Link href={`/hr/profiles/${row.user_id}`} className="font-medium hover:underline">
                  {row.full_name}
                </Link>
                <span className="block truncate text-xs text-[var(--text-muted)]">
                  {row.member_code} · {row.department ?? "—"}
                </span>
              </span>
              <span className="shrink-0">{extra(row)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** "Tổng quan nhân sự" (0096): HCNS's dashboard; anybody else goes to their own profile. */
export default function HrOverviewPage() {
  const router = useRouter();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const allowed = Boolean(me.data?.hr?.overview);
  const overview = useQuery({ queryKey: ["hr", "overview"], queryFn: hrApi.overview, enabled: allowed });
  useEffect(() => {
    if (me.data && !allowed) router.replace("/hr/me");
  }, [me.data, allowed, router]);
  if (me.isPending || (allowed && overview.isPending)) return <Loading label="Đang tải tổng quan…" />;
  if (!allowed) return <Loading label="Đang mở hồ sơ của bạn…" />;
  if (overview.isError) return <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />;
  const o = overview.data!;
  const top = Math.max(1, ...o.by_department.map((item) => item.people));
  return (
    <div className="space-y-4">
      <PageHeader title="Tổng quan nhân sự" subtitle="Số liệu từ hồ sơ nhân sự, cập nhật theo thời gian thực." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Tổng nhân sự" value={o.total} foot={`${o.active} đang làm · ${o.left} đã nghỉ`} />
        <Tile label="Tỷ lệ nữ" value={o.female_pct === null ? "—" : `${o.female_pct}%`} />
        {o.sensitive ? <Tile label="Tuổi trung bình" value={o.average_age ?? "—"} /> : null}
        {o.sensitive ? <Tile label="Quỹ lương / tháng" value={money(o.payroll)} foot="Lương cơ bản + phụ cấp" /> : null}
        <Tile label="HĐ sắp hết hạn (60 ngày)" value={o.expiring_count} foot={`${o.expired_count} đã hết hạn · ${o.no_contract} chưa có HĐ`} />
        <Tile label="Yêu cầu chờ duyệt" value={o.pending_requests} />
        <Tile label="Hồ sơ thiếu tài liệu" value={o.missing_documents} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel p-4">
          <h2 className="text-base font-semibold">Nhân sự theo phòng ban</h2>
          <ul className="mt-3 space-y-2">
            {o.by_department.map((item) => (
              <li key={item.code} className="text-sm">
                <div className="flex justify-between gap-2">
                  <span className="truncate">{item.name}</span>
                  <span className="tabular-nums">{item.people}</span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-[var(--surface-muted)]">
                  <div className="h-2 rounded-full" style={{ width: `${(item.people / top) * 100}%`, background: "var(--brand-blue)" }} />
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel p-4">
          <h2 className="text-base font-semibold">Cơ cấu theo trạng thái</h2>
          <ul className="mt-3 divide-y divide-[var(--border)]">
            {o.by_status.map((item) => (
              <li key={item.label} className="flex justify-between py-2 text-sm">
                <span>{item.label}</span>
                <span className="tabular-nums font-medium">{item.people}</span>
              </li>
            ))}
          </ul>
        </section>
        <People title="Hợp đồng sắp hết hạn" rows={o.expiring} extra={(row) => <ContractPill contract={row.contract} />} />
        {o.sensitive ? (
          <People
            title="Sinh nhật trong tháng"
            rows={o.birthdays}
            extra={(row) => <span className="text-xs">{formatDate(row.birth_date ?? null).slice(0, 5)}</span>}
          />
        ) : null}
        <People title="Mới vào làm" rows={o.newest} extra={(row) => <span className="text-xs">{formatDate(row.joined_on)}</span>} />
      </div>
    </div>
  );
}
