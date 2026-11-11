"use client";

import { Suspense } from "react";
import { CskhGate } from "@/components/cskh-common";
import { CskhInbox } from "@/components/cskh-inbox";
import { Loading } from "@/components/states";

/** "Hộp chat page" (0102): the Pancake pages' conversations in one inbox. */
export default function CskhPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải…" />}>
      <CskhGate allowed={(menu) => menu.inbox}>
        <CskhInbox />
      </CskhGate>
    </Suspense>
  );
}
