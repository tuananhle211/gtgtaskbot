"use client";

import { Suspense } from "react";
import { CskhWorkspace } from "@/components/cskh";
import { Loading } from "@/components/states";

/** "Chăm sóc khách hàng" (0094): Pancake pages answered by a Hermes agent. */
export default function CskhPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải…" />}>
      <CskhWorkspace />
    </Suspense>
  );
}
