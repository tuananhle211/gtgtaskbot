"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { adminTabsFor } from "@/components/admin-tabs";
import { PageHeader } from "@/components/pr";
import { Loading } from "@/components/states";

/** "Quản trị" from the menu: straight on to the first screen this person may open. */
export default function AdminPage() {
  const router = useRouter();
  const session = useQuery({ queryKey: ["session"], queryFn: api.session, retry: false });
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const ready = !session.isPending && !me.isPending;
  const first = ready ? adminTabsFor(me.data, session.data?.role)[0]?.href : undefined;

  useEffect(() => {
    if (first) router.replace(first);
  }, [first, router]);

  if (!ready || first) return <Loading />;
  return (
    <div className="space-y-4">
      <PageHeader title="Quản trị" />
      <p className="text-sm text-[var(--text-muted)]">Bạn không có quyền quản trị nào.</p>
    </div>
  );
}
