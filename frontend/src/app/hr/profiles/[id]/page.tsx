"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { HrProfileView } from "@/components/hr-profile";

/** One person's HR profile (0096): HCNS, their Trưởng phòng, or themselves. */
export default function HrProfilePage() {
  const params = useParams<{ id: string }>();
  return (
    <div className="space-y-3">
      <Link href="/hr/profiles" className="text-sm text-[var(--accent)] hover:underline print:hidden">
        ← Quản lý hồ sơ
      </Link>
      <HrProfileView userId={params.id} />
    </div>
  );
}
