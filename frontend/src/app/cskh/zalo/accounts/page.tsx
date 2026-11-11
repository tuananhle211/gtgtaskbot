"use client";

import { CskhGate } from "@/components/cskh-common";
import { CskhZaloAccounts } from "@/components/cskh-zalo";

/** "Kết nối Zalo" (0102): the company nicks, their telesale, the QR login. */
export default function CskhZaloAccountsPage() {
  return (
    <CskhGate allowed={(menu) => menu.zalo || menu.manage}>
      <CskhZaloAccounts />
    </CskhGate>
  );
}
