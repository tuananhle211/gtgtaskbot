"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/states";

/**
 * The old "Thành viên luồng" address. Streams are no longer something people
 * are tagged into (2026-10-17): accounts are managed flat, by department, on
 * "Quản lý tài khoản".
 */
export default function UnitsAdminPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin/accounts");
  }, [router]);
  return <Loading />;
}
