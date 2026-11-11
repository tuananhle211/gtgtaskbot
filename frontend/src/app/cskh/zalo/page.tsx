"use client";

import { Suspense } from "react";
import { CskhGate } from "@/components/cskh-common";
import { CskhZaloInbox } from "@/components/cskh-zalo";
import { Loading } from "@/components/states";

/** "Hộp chat Zalo" (0102): the telesales' Zalo threads, matched to the page conversation. */
export default function CskhZaloPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải…" />}>
      <CskhGate allowed={(menu) => menu.zalo}>
        <CskhZaloInbox />
      </CskhGate>
    </Suspense>
  );
}
