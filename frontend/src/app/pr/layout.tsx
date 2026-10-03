import { headers } from "next/headers";
import { Shell } from "@/components/shell";

/**
 * Rendered per request, not prerendered at build time.
 *
 * This is what makes the nonce-based CSP in `src/middleware.ts` work. A
 * statically prerendered page's HTML is generated once at build time, so it
 * cannot carry a per-request nonce - and with `strict-dynamic` in the policy, a
 * script tag without one does not load at all. The page would render blank in a
 * real browser while looking perfectly fine to `curl`.
 *
 * `export const dynamic = "force-dynamic"` was tried first and does **not**
 * propagate to child pages in Next 15.5 - the routes stayed `○ (Static)` and
 * served un-nonced HTML. Awaiting `headers()` does force it, because reading a
 * request header is by definition per-request. The nonce this reads is also the
 * one Next stamps onto its script tags, so the value is used rather than being a
 * trick to defeat the optimiser.
 *
 * Nothing is lost by giving up prerendering. Every page under /pr is a client
 * component that fetches behind a session cookie, so there was never any
 * cacheable HTML; the "static" classification was describing an empty shell.
 */
export default async function PrLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <Shell>{children}</Shell>;
}
