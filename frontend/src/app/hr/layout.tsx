import { headers } from "next/headers";
import { Shell } from "@/components/shell";

/** Per-request, like `src/app/cskh/layout.tsx`: the CSP nonce needs it. */
export default async function HrLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <Shell>{children}</Shell>;
}
