"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** "Hiệu suất" goes on to the person's own month. */
export default function PerformanceIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/performance/me");
  }, [router]);
  return <Loading />;
}
