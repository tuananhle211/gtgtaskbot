"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/** The old address of "Tài khoản CRM": it lives under Quản trị since 13/11. */
export default function AdsCrmRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin/crm");
  }, [router]);
  return <Loading />;
}
