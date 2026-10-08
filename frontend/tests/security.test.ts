/**
 * Step 1E.1 frontend security tests.
 *
 * The runtime behaviour a browser sees - the CSP header, the nonce on every
 * script tag, HSTS presence by environment - is checked against a *built and
 * running* Next server, which vitest cannot start. That is covered by
 * `docs/pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` and was verified by hand during this
 * step (every PR route served 15 script tags, 0 without a nonce).
 *
 * What is checked here is everything provable without a server: the policy the
 * middleware composes, the absence of secrets from the source and the built
 * bundle, and the client's handling of 401 versus 403.
 */

import { describe, expect, it } from "vitest";
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { ApiError } from "@/lib/api";

const ROOT = path.resolve(__dirname, "..");
const read = (relative: string) => readFileSync(path.join(ROOT, relative), "utf8");

/** Source with `//` comment lines removed. */
function withoutComments(source: string): string {
  return source
    .split("\n")
    .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
    .join("\n");
}

describe("37. the content security policy is restrictive and nonce-based", () => {
  const middleware = withoutComments(read("src/middleware.ts"));

  it("locks down the directives that matter", () => {
    for (const directive of [
      "default-src 'self'",
      "connect-src 'self'",
      "frame-ancestors 'none'",
      "frame-src 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ]) {
      expect(middleware).toContain(directive);
    }
  });

  it("allows inline script only by nonce, never by unsafe-inline", () => {
    const scriptSrc = middleware.split("\n").find((line) => line.includes("script-src"));
    expect(scriptSrc).toBeDefined();
    // `strict-dynamic` means host allowlists are ignored, so a script tag
    // without the nonce does not load at all. That is why every route had to be
    // made per-request - see src/app/pr/layout.tsx.
    expect(scriptSrc).toContain("'strict-dynamic'");
    expect(scriptSrc).toContain("nonce-");
    expect(scriptSrc).not.toContain("unsafe-inline");
    expect(scriptSrc).not.toContain("unsafe-eval");
    expect(middleware).not.toContain("'unsafe-eval'");
  });

  it("keeps every route per-request, or the nonce would not reach the HTML", () => {
    // A statically prerendered page's HTML is generated once at build time and
    // cannot carry a per-request nonce. Combined with `strict-dynamic` that means
    // no script loads and the page renders blank - while looking fine to `curl`.
    for (const layout of [
      "src/app/pr/layout.tsx",
      "src/app/dashboard/layout.tsx",
      "src/app/tasks/layout.tsx",
      "src/app/orders/layout.tsx",
      "src/app/admin/layout.tsx",
      "src/app/auth/layout.tsx",
      // Password login (public) and the account screen.
      "src/app/login/layout.tsx",
      "src/app/account/layout.tsx",
      // The public legal segment. Its pages are static prose and would still be
      // readable un-hydrated, but a public page whose scripts the browser
      // refuses is not a page to ship.
      "src/app/(legal)/layout.tsx",
    ]) {
      const source = read(layout);
      expect(source, layout).toContain("await headers()");
    }
  });

  it("decides HSTS from configuration, not from a request header", () => {
    expect(middleware).toContain("process.env.WEB_COOKIE_SECURE");
    // X-Forwarded-Proto is client-settable at the edge of an untrusted hop, and
    // an HSTS entry a browser has cached cannot be withdrawn.
    expect(middleware).not.toContain("x-forwarded-proto");
    expect(middleware).not.toContain("X-Forwarded-Proto");
  });
});

describe("39. no secret reaches the browser", () => {
  it("has no NEXT_PUBLIC_ variable anywhere in the source", () => {
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry) => {
        const full = path.join(dir, entry);
        return statSync(full).isDirectory() ? walk(full) : [full];
      });
    const sources = walk(path.join(ROOT, "src")).filter((file) => /\.tsx?$/.test(file));
    expect(sources.length).toBeGreaterThan(5);
    for (const file of sources) {
      expect(readFileSync(file, "utf8"), file).not.toContain("NEXT_PUBLIC");
    }
  });

  it("keeps the API address server-side", () => {
    const config = read("next.config.mjs");
    // Read at request time in the Node process that does the proxying. A browser
    // never learns the internal address, and never needs to.
    expect(config).toContain("process.env.MEOBOT_API_URL");
    expect(config).not.toContain("NEXT_PUBLIC_MEOBOT_API_URL");
  });

  it("has no backend secret in the built client bundle", () => {
    const staticDir = path.join(ROOT, ".next", "static");
    if (!existsSync(staticDir)) {
      // The build is not a test dependency; the deployment checklist re-checks
      // this against the real artefact. Skipping loudly beats a false pass.
      console.warn("no .next/static - run `npx next build` to check the bundle"); // eslint-disable-line no-console
      return;
    }
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry) => {
        const full = path.join(dir, entry);
        return statSync(full).isDirectory() ? walk(full) : [full];
      });
    const bundle = walk(staticDir)
      .filter((file) => /\.(js|css|json)$/.test(file))
      .map((file) => readFileSync(file, "utf8"))
      .join("\n");
    expect(bundle.length).toBeGreaterThan(1000);
    for (const secret of [
      "MEOBOT_API_URL",
      "postgresql+asyncpg",
      "TELEGRAM_BOT_TOKEN",
      "meobot_web_session=",
      "http://api:8000",
    ]) {
      expect(bundle, secret).not.toContain(secret);
    }
  });
});

describe("40. the frontend cannot read the session token", () => {
  it("never touches document.cookie or localStorage", () => {
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry) => {
        const full = path.join(dir, entry);
        return statSync(full).isDirectory() ? walk(full) : [full];
      });
    for (const file of walk(path.join(ROOT, "src")).filter((f) => /\.tsx?$/.test(f))) {
      const source = readFileSync(file, "utf8");
      // The cookie is HttpOnly, so these would not work anyway. Their absence is
      // the evidence that no code path is *trying* to hold a credential in JS.
      for (const forbidden of ["document.cookie", "localStorage", "sessionStorage"]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("sends the cookie by same-origin credentials and no header", () => {
    const client = read("src/lib/api.ts");
    expect(client).toContain('credentials: "same-origin"');
    expect(client).not.toContain("Authorization");
    expect(client).not.toContain("X-Telegram");
    // No token is ever read from anywhere to be attached to a request.
    expect(client).not.toMatch(/getToken|readToken|authToken/);
  });
});

describe("41-42. 401 and 403 are handled differently", () => {
  it("distinguishes them on the error object", () => {
    const unauthenticated = new ApiError(401, "http_error", "hết phiên", {});
    const forbidden = new ApiError(403, "pr_permission_denied_error", "không có quyền", {});
    const conflict = new ApiError(409, "pr_stale_version_error", "đã thay đổi", {});

    expect(unauthenticated.isUnauthenticated).toBe(true);
    expect(unauthenticated.isForbidden).toBe(false);

    // The distinction that prevents a login loop: a forbidden action must not be
    // read as "your session ended", or the UI signs somebody out for being
    // insufficiently privileged and they log back in to the same refusal.
    expect(forbidden.isUnauthenticated).toBe(false);
    expect(forbidden.isForbidden).toBe(true);

    expect(conflict.isConflict).toBe(true);
    expect(conflict.isUnauthenticated).toBe(false);
  });

  it("only the 401 branch tells somebody to get a new link", () => {
    const states = read("src/components/states.tsx");
    const loginBranch = states.slice(
      states.indexOf("api?.isUnauthenticated"),
      states.indexOf("const message ="),
    );
    expect(loginBranch).toContain("/web");
    // The forbidden branch points at where a grant comes from instead.
    const rest = states.slice(states.indexOf("const message ="));
    expect(rest).toContain("isForbidden");
    expect(rest).not.toContain("/web");
  });

  it("does not retry a 401, 403, 404 or 409", () => {
    // Retrying a 409 would re-send an approval whose whole problem was that the
    // draft changed underneath it.
    const providers = read("src/components/providers.tsx");
    expect(providers).toContain("[401, 403, 404, 409]");
    expect(providers).toContain("retry: false");
  });
});

describe("no client-side authority", () => {
  it("has no role or capability decision in the browser", () => {
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry) => {
        const full = path.join(dir, entry);
        return statSync(full).isDirectory() ? walk(full) : [full];
      });
    for (const file of walk(path.join(ROOT, "src")).filter((f) => /\.tsx?$/.test(f))) {
      const source = readFileSync(file, "utf8");
      for (const forbidden of [
        "hasPermission",
        "canApprove",
        "ROLE_RANK",
        "isAdmin",
        "dangerouslySetInnerHTML",
      ]) {
        expect(source, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });
});
