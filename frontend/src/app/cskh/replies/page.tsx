"use client";

import { CskhGate } from "@/components/cskh-common";
import { CskhReplies } from "@/components/cskh-replies";

/** "Nhãn & câu mẫu" (0102, CSKH MANAGE). */
export default function CskhRepliesPage() {
  return (
    <CskhGate allowed={(menu) => menu.manage}>
      <CskhReplies />
    </CskhGate>
  );
}
