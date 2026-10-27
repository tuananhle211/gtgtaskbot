"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** The old address of "Kết nối BM": it lives in the Phòng Ads menu since 27/10. */
export default function AdsConnectionsRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/ads/connections");
  }, [router]);
  return <Loading />;
}
