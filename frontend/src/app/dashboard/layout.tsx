import { headers } from "next/headers";
import { Shell } from "@/components/shell";

/**
 * Per-request, like `src/app/pr/layout.tsx` and for the same reason: the
 * nonce-based CSP needs every page rendered per request. See that file.
 */
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <Shell>{children}</Shell>;
}
