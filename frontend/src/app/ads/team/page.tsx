"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/**
 * The old "Dashboard tổng" address: merged into Dashboard Ads (2026-10-27),
 * whose "Tổng team" view it was.
 */
export default function AdsTeamPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/ads");
  }, [router]);
  return <Loading />;
}
