"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Loading } from "@/components/states";
import { TaskCodesCatalogue } from "@/components/task-codes";

/**
 * "Danh mục điểm" (0074): every production step's task codes and their
 * points, and the forms ("hình thức"). `?phase=` opens a step's tab.
 */
export default function TaskCodesPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Danh mục điểm</h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Luồng Order (ORD) · mã task công việc và điểm chuẩn của từng pha
        </p>
      </div>
      <Suspense fallback={<Loading />}>
        <WithPhase />
      </Suspense>
    </div>
  );
}

function WithPhase() {
  const params = useSearchParams();
  return <TaskCodesCatalogue initial={params.get("phase")} />;
}
