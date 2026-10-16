"use client";

import { DesignCodesManager } from "@/components/design-codes";

/**
 * "Mã task design": the Design ban's catalogue, for whoever may keep it
 * (`design.manage_codes` on `/api/units/me`). The table itself - and what
 * somebody without that right reads instead - is `DesignCodesManager`.
 */
export default function DesignCodesPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Mã task design</h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Luồng Order (ORD) · danh mục mã task design của ban Design
        </p>
      </div>
      <DesignCodesManager />
    </div>
  );
}
