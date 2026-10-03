import { headers } from "next/headers";

/**
 * The public legal segment: `/terms` and `/privacy`.
 *
 * `(legal)` is a route group, so it contributes nothing to the URL - the two
 * pages stay at the top level, which is where the addresses registered with
 * TikTok point:
 *
 *     https://app.meochat.online/terms
 *     https://app.meochat.online/privacy
 *
 * ## Anonymous by construction
 *
 * Neither page reads a session and neither is wrapped in `Shell`, which is the
 * component that holds the session query and renders a sign-in prompt instead of
 * its children. Only `/pr` uses it. `src/middleware.ts` sets security headers and
 * makes no authorization decision, and `next.config.mjs` has no rewrite matching
 * these paths, so nothing between the browser and the page can redirect a visitor
 * without a cookie. That was the requirement, and it needed no allowlist because
 * there was no denylist to be on.
 *
 * ## Why this awaits headers()
 *
 * The same reason `src/app/pr/layout.tsx` and `src/app/auth/layout.tsx` do. The
 * CSP in `src/middleware.ts` is nonce-based with `strict-dynamic`; HTML generated
 * once at build time cannot carry a per-request nonce, so Next's own script tags
 * would be refused by the browser. Reading a request header is per-request by
 * definition, which is what forces these routes out of prerendering.
 *
 * The pages themselves are static prose and would survive without hydration, but
 * "renders anyway with a console full of CSP violations" is not a state to ship a
 * public page in.
 */
export default async function LegalLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <>{children}</>;
}
