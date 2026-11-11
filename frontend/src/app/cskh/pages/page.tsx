"use client";

import { CskhGate } from "@/components/cskh-common";
import { CskhPages } from "@/components/cskh-pages";

/** "Kết nối page" (0102, CSKH MANAGE): Pancake, POS Cake, pages, settings. */
export default function CskhPagesPage() {
  return (
    <CskhGate allowed={(menu) => menu.manage}>
      <CskhPages />
    </CskhGate>
  );
}
