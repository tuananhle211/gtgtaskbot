/**
 * Next.js configuration for the PR admin panel.
 *
 * The one decision that matters here is the rewrite.
 *
 * ## Why the API is proxied instead of called cross-origin
 *
 * The session is an `HttpOnly; Secure; SameSite=Strict` cookie. `Strict` means
 * the browser attaches it only to requests the *same site* initiated - which is
 * precisely what makes it a CSRF defence, and precisely what would stop a page
 * on `pr.example.com` from authenticating a call to `api.example.com`.
 *
 * Loosening the cookie to `SameSite=Lax` or `None` to allow that would give up
 * the defence in order to work around it. So instead the browser only ever talks
 * to one origin, and Next forwards `/api/*` and `/auth/*` to FastAPI server-side.
 * The cookie is same-site by construction, no CORS preflight happens, and no
 * CSRF token is needed because there is no cross-site credentialed request for
 * one to protect.
 *
 * `MEOBOT_API_URL` is read at **run** time on the server. It is not
 * `NEXT_PUBLIC_`, so it never reaches the browser bundle - the internal API
 * address is not something a page needs to know.
 */

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Traced, self-contained server output for the Docker runner stage. Harmless
  // for `next dev`, which ignores it.
  output: "standalone",
  // No `output: "export"`. A static export cannot proxy, and without the proxy
  // the cookie story above falls apart.
  async rewrites() {
    const target = process.env.MEOBOT_API_URL ?? "http://api:8000";
    return [
      { source: "/api/pr/:path*", destination: `${target}/api/pr/:path*` },
      // The notification centre is deliberately *not* under /api/pr - a
      // notification points at any target kind, not only a PR one - so it needs
      // its own proxy entry. Without it the browser's /api/notifications calls
      // are served by Next itself, which has no such route, and the whole panel
      // 404s. The collection route has no sub-path, hence the bare source
      // alongside the wildcard one.
      {
        source: "/api/notifications",
        destination: `${target}/api/notifications`,
      },
      {
        source: "/api/notifications/:path*",
        destination: `${target}/api/notifications/:path*`,
      },
      { source: "/api/auth/:path*", destination: `${target}/api/auth/:path*` },
      // The signed-in person's own account (profile, password, figures) and
      // the member roster. Bare source beside the wildcard, as for /api/orders.
      { source: "/api/account", destination: `${target}/api/account` },
      {
        source: "/api/account/:path*",
        destination: `${target}/api/account/:path*`,
      },
      // Units, the Ads order engine and the shared board. `/api/orders` has a
      // bare collection route (create), hence the bare source beside the
      // wildcard one, as for notifications.
      {
        source: "/api/units/:path*",
        destination: `${target}/api/units/:path*`,
      },
      { source: "/api/orders", destination: `${target}/api/orders` },
      {
        source: "/api/orders/:path*",
        destination: `${target}/api/orders/:path*`,
      },
      // The unified task (PR content + Ads order). The bare source is not
      // called today but mirrors /api/orders, so a collection route added
      // later does not silently 404 through Next.
      { source: "/api/tasks", destination: `${target}/api/tasks` },
      {
        source: "/api/tasks/:path*",
        destination: `${target}/api/tasks/:path*`,
      },
      {
        source: "/api/board/:path*",
        destination: `${target}/api/board/:path*`,
      },
      // The magic-link landing route. Handled by FastAPI, which sets the cookie
      // and redirects to /pr - a page this app serves.
      { source: "/auth/login", destination: `${target}/auth/login` },
    ];
  },
  // The old PR landing page and kanban now live on the shared screens. The
  // FastAPI login still redirects to /pr, so /pr itself forwards.
  async redirects() {
    return [
      { source: "/pr", destination: "/dashboard", permanent: false },
      // The PR create form moved to the shared "Tạo order" screen; the board
      // at /pr/content itself stays (rows and the task table link into it).
      {
        source: "/pr/content",
        has: [{ type: "query", key: "create", value: "1" }],
        destination: "/orders/new?unit=PR",
        permanent: false,
      },
      // One detail screen for both units. Old links (notifications already
      // sent, bookmarks, Telegram messages) land on it: the API accepts an
      // order code / id and a PR content id as the task ref. `/orders/new`
      // (the create form) and the `/pr/content` board itself are not touched -
      // the first is excluded by the pattern, the second has no segment.
      {
        source: "/orders/:ref((?!new$)[^/]+)",
        destination: "/tasks/:ref",
        permanent: false,
      },
      {
        source: "/pr/content/:id([^/]+)",
        destination: "/tasks/:id",
        permanent: false,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Defence in depth for the HttpOnly cookie: an injected script cannot
          // read it, and these make injecting one harder.
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
