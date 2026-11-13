"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** The old address of "Tác vụ định kỳ": back in Quản trị since 13/11 (was Phòng Ads from 27/10). */
export default function AdsJobsRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin/cron");
  }, [router]);
  return <Loading />;
}
