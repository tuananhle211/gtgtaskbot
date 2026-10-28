"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, type AdsPlatform } from "@/lib/api";
import { PLATFORMS } from "@/components/ads";
import { StaffDashboard } from "@/components/ads-staff";
import { TeamDashboard } from "@/components/ads-team";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Phòng Ads › Dashboard Ads" - one dashboard for everybody (2026-10-27, the
 * old "Dashboard tổng" merged in). A Trưởng phòng ADS / the OWNER / an ADMIN
 * (`ads.view_all`) lands on "Tổng team" and opens one marketer with "Xem chi
 * tiết theo" (`?user_id=`, "← Về khối tổng" goes back); a member sees their
 * own numbers. Both end with "Báo cáo chi tiết" (tháng, ngày). "Nền tảng"
 * (`?platform=`, Facebook by default) is kept when going from one to the
 * other.
 */
export default function AdsDashboardPage() {
  const router = useRouter();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const [userId, setUserId] = useState("");
  const [platform, setPlatform] = useState<AdsPlatform>("FACEBOOK");
  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    const picked = search.get("user_id");
    if (picked) setUserId(picked);
    const chosen = PLATFORMS.find(
      (item) => item.value === search.get("platform"),
    );
    if (chosen) setPlatform(chosen.value);
  }, []);
  const go = (id: string, on: AdsPlatform) => {
    const search = new URLSearchParams();
    if (id) search.set("user_id", id);
    if (on !== "FACEBOOK") search.set("platform", on);
    const text = search.toString();
    router.replace(text ? `/ads?${text}` : "/ads");
  };
  const pick = (id: string) => {
    setUserId(id);
    go(id, platform);
  };
  const choose = (on: AdsPlatform) => {
    setPlatform(on);
    go(userId, on);
  };
  if (me.isPending) return <Loading label="Đang tải Dashboard Ads…" />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  const viewAll = Boolean(me.data.ads?.view_all);
  if (viewAll && !userId)
    return (
      <TeamDashboard onPick={pick} platform={platform} onPlatform={choose} />
    );
  return viewAll ? (
    <StaffDashboard
      userId={userId}
      onPick={pick}
      platform={platform}
      onPlatform={choose}
    />
  ) : (
    <StaffDashboard platform={platform} onPlatform={choose} />
  );
}
