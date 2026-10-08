import { headers } from "next/headers";

/**
 * The public sign-in page. Not wrapped in `Shell` - that component holds the
 * session query and would answer an anonymous visit with the sign-in prompt
 * instead of this form.
 *
 * Per-request for the same reason as `src/app/auth/layout.tsx`: the CSP in
 * `src/middleware.ts` is nonce-based with `strict-dynamic`, and a prerendered
 * page's scripts carry no nonce, so the form would never hydrate.
 */
export default async function LoginLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <>{children}</>;
}
