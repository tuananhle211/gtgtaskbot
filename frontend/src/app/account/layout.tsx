import { headers } from "next/headers";
import { Shell } from "@/components/shell";

/** Per-request, like `src/app/tasks/layout.tsx`: the CSP nonce needs it. */
export default async function AccountLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <Shell>{children}</Shell>;
}
