"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { AdminTabs } from "@/components/admin-tabs";
import { DurationManager } from "@/components/durations";
import { PageHeader } from "@/components/pr";
import { PlatformManager } from "@/components/platforms";
import { ErrorBox, Loading } from "@/components/states";
import { SettingsForm } from "@/components/unit-panel";
import { VideoKindsManager } from "@/components/video-kinds";

/**
 * "Quản trị › Thiết lập ORD": the order flow's settings (review steps, effort,
 * AI script review...) and its catalogues (video kinds, platforms, durations).
 * For whoever administers ORD (OWNER / ADMIN); the server refuses every write
 * from anybody else. No permission column here since 0066: an ADMIN holds
 * everything, and what a department grants is edited on "Phòng ban".
 */
export default function OrdSettingsPage() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["units"] });

  return (
    <div className="space-y-4">
      <AdminTabs active="settings" />
      <PageHeader
        title="Thiết lập ORD"
        subtitle="Cách luồng order chạy: bước duyệt, effort, AI duyệt kịch bản, loại video, nền tảng, thời lượng."
      />
      {me.isPending ? <Loading /> : null}
      {me.isError ? <ErrorBox error={me.error} onRetry={() => me.refetch()} /> : null}
      {me.data && !me.data.can_admin.includes("ADS") ? (
        <p className="text-sm text-[var(--text-muted)]">Bạn không quản trị luồng ORD.</p>
      ) : null}
      {me.data?.can_admin.includes("ADS") ? (
        <>
          <SettingsForm code="ADS" onDone={refresh} />
          <VideoKindsManager code="ADS" />
          <PlatformManager code="ADS" />
          <DurationManager code="ADS" />
        </>
      ) : null}
    </div>
  );
}
