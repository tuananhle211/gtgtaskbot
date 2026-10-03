import { headers } from "next/headers";

/**
 * Rendered per request, for the same reason as the /pr segment.
 *
 * `/auth/failed` is the page somebody lands on with a spent login link. Left
 * statically prerendered, its script tags carry no nonce, and the
 * `strict-dynamic` CSP in `src/middleware.ts` refuses to load them - so the one
 * page whose whole job is to explain what went wrong would itself render blank.
 */
export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  await headers();
  return <>{children}</>;
}
