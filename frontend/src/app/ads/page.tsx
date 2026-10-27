"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StaffDashboard } from "@/components/ads-staff";
import { TeamDashboard } from "@/components/ads-team";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Phòng Ads › Dashboard Ads" - one dashboard for everybody (2026-10-27, the
 * old "Dashboard tổng" merged in). A Trưởng phòng ADS / the OWNER / an ADMIN
 * (`ads.view_all`) lands on "Tổng team" and opens one marketer with "Xem chi
 * tiết theo" (`?user_id=`, "← Về khối tổng" goes back); a member sees their
 * own numbers. Both end with "Báo cáo chi tiết" (tháng, ngày).
 */
export default function AdsDashboardPage() {
  const router = useRouter();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const [userId, setUserId] = useState("");
  useEffect(() => {
    const picked = new URLSearchParams(window.location.search).get("user_id");
    if (picked) setUserId(picked);
  }, []);
  const pick = (id: string) => {
    setUserId(id);
    router.replace(id ? `/ads?user_id=${id}` : "/ads");
  };
  if (me.isPending) return <Loading label="Đang tải Dashboard Ads…" />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  const viewAll = Boolean(me.data.ads?.view_all);
  if (viewAll && !userId) return <TeamDashboard onPick={pick} />;
  return viewAll ? (
    <StaffDashboard userId={userId} onPick={pick} />
  ) : (
    <StaffDashboard />
  );
}
