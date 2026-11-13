"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** "Tài chính" opens on "Doanh thu". */
export default function FinanceIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/finance/revenue");
  }, [router]);
  return <Loading />;
}
