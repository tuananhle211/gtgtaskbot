"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** The old address of "Tác vụ định kỳ": it lives in the Phòng Ads menu since 27/10. */
export default function CronRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/ads/jobs");
  }, [router]);
  return <Loading />;
}
