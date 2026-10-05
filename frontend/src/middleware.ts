/**
 * Per-request security headers for the PR admin panel.
 *
 * The static headers (`nosniff`, `Referrer-Policy`, `X-Frame-Options`) live in
 * `next.config.mjs` because they never vary. The three here cannot be static:
 *
 * - **CSP** needs a fresh nonce per response;
 * - **HSTS** must not be sent over plain HTTP in development, where it would
 *   pin `localhost` to HTTPS in the developer's browser and be a nuisance to
 *   undo;
 * - **Permissions-Policy** is here only to sit beside them.
 *
 * ## Why a nonce, and not `unsafe-inline`
 *
 * Next.js App Router inlines a `<script>` carrying the flight payload for
 * hydration. The two ways to allow it are `'unsafe-inline'` - which permits
 * *every* inline script and so is barely a CSP at all - or a nonce.
 *
 * Next reads the `Content-Security-Policy` header this middleware sets, finds
 * the `'nonce-…'` token, and stamps that nonce onto its own script tags. An
 * injected `<script>` has no nonce and is refused. That is the documented
 * approach and it is the reason `script-src` here contains no `unsafe-` value.
 *
 * The one concession is `style-src 'unsafe-inline'`. Next injects inline
 * `<style>` for its own CSS handling, and there is no nonce path for it. An
 * inline-style injection can restyle a page; it cannot execute. Worth naming as a
 * real limitation rather than leaving it to be discovered.
 *
 * Nonce-based CSP opts a route out of static prerendering. Acceptable here: every
 * page in this panel is a client component that fetches at request time, so none
 * of them were usefully static.
 */

import { NextResponse, type NextRequest } from "next/server";

/**
 * True when this deployment is served over HTTPS and should assert HSTS.
 *
 * Read from configuration (`WEB_COOKIE_SECURE`, the same flag the API uses for
 * the session cookie) rather than sniffed from `X-Forwarded-Proto`. A header a
 * client can set must not decide a security behaviour, and a browser that has
 * cached HSTS for a hostname cannot be told to forget it.
 */
const HSTS_ENABLED = process.env.WEB_COOKIE_SECURE !== "false";

/**
 * TikTok's CDN hosts, allowed for `img-src` on the channel page only.
 *
 * Step 1F.2.9. The TikTok account panel renders an avatar and video cover
 * images that TikTok serves from its own CDN and expires within hours. There
 * are two ways to show them: widen `img-src`, or proxy every image through the
 * API. Proxying would mean a new endpoint that fetches an attacker-influencable
 * URL server-side, which is an SSRF surface this deployment does not currently
 * have and would have to defend with the same allowlist written below - so the
 * allowlist is applied where it costs nothing instead.
 *
 * Wildcards by registrable domain rather than exact hosts because TikTok picks
 * a regional shard per request (`p16-sign-va`, `p19-sign`, `p16-pu-sign-no`, …)
 * and pinning the ones seen today would break the panel silently the first time
 * a different edge answered.
 *
 * `img-src` is the narrowest directive that could carry this: an image host
 * cannot execute a script, cannot read the session cookie, and cannot receive
 * one - the cookie is `SameSite=Strict`. The exposure it does create is a
 * request to TikTok carrying the viewer's IP, which is unavoidable for anything
 * short of a full proxy and is what loading a TikTok image means.
 */
const TIKTOK_IMAGE_HOSTS = [
  "https://*.tiktokcdn.com",
  "https://*.tiktokcdn-us.com",
  "https://*.tiktokcdn-eu.com",
  "https://*.ttwstatic.com",
].join(" ");

/**
 * Where those hosts are allowed. Nowhere else.
 *
 * Scoped to one path rather than applied site-wide because the rest of the
 * panel has no business loading a third-party image, and a CSP that permits
 * something everywhere in order to permit it in one place is a weaker CSP for
 * no gain.
 */
const TIKTOK_IMAGE_PATH = "/pr/channels";

function contentSecurityPolicy(nonce: string, isSecure: boolean, { tiktokImages = false } = {}): string {
  const directives = [
    "default-src 'self'",
    // No 'unsafe-inline', no 'unsafe-eval'. 'strict-dynamic' lets a nonced
    // script load the chunks it needs without every chunk URL being listed.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    // The documented limitation - see the module comment.
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob:${tiktokImages ? ` ${TIKTOK_IMAGE_HOSTS}` : ""}`,
    "font-src 'self' data:",
    // The panel only ever talks to its own origin: Next proxies /api to the
    // FastAPI service server-side, so the browser makes no cross-origin request.
    // Anything trying to exfiltrate to another host is refused here.
    "connect-src 'self'",
    // Nothing is embedded and nothing may embed this. The modern equivalent of
    // X-Frame-Options: DENY, which next.config.mjs also sends for old browsers.
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ];
  if (HSTS_ENABLED && isSecure) {
    directives.push("upgrade-insecure-requests");
  }
  return directives.join("; ");
}

export function middleware(request: NextRequest): NextResponse {
  const isSecure = request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
  const nonce = crypto.randomUUID();
  const csp = contentSecurityPolicy(nonce, isSecure, {
    // `startsWith` rather than an exact match, so `/pr/channels?channel=…` -
    // which is where the OAuth callback lands - is covered too.
    tiktokImages: request.nextUrl.pathname.startsWith(TIKTOK_IMAGE_PATH),
  });

  // Next reads these off the *request* to stamp its own script tags.
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", csp);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set(
    "Permissions-Policy",
    // Nothing in a PR dashboard needs a camera, a microphone or a location.
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  );
  
  // Only assert HSTS and upgrade requests if the connection is actually secure
  const isSecure = request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
  if (HSTS_ENABLED && isSecure) {
    response.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  }
  return response;
}

export const config = {
  // Everything except Next's own static output and the favicon. Those are
  // immutable files with no inline script, and running middleware on each one
  // would add a nonce computation per asset for no benefit.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
