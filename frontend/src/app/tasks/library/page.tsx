"use client";

import { DesignLibraryManager } from "@/components/design-library";

/**
 * "Quản lý thư viện": the Design team's "Thư viện" menu, for whoever may keep
 * it (`design.manage_library` on `/api/units/me`). The editor itself - and
 * what somebody without that right reads instead - is `DesignLibraryManager`.
 */
export default function DesignLibraryPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Quản lý thư viện</h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Luồng Order (ORD) · menu Thư viện của ban Design
        </p>
      </div>
      <DesignLibraryManager />
    </div>
  );
}
