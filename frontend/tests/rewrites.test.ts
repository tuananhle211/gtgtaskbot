/**
 * The proxy table in `next.config.mjs`.
 *
 * Every browser call in `src/lib/api.ts` goes to a same-origin `/api/...` path
 * and is forwarded to FastAPI by a rewrite (see the file header there for why
 * the cookie makes that mandatory). A path the table does not cover is not an
 * error at build time and not an error in `next dev` against a mocked client -
 * it is a 404 served by Next in production, which is exactly how the whole
 * notification centre broke.
 *
 * So the rewrites are asserted here by *resolving real request paths* through
 * the table, rather than by grepping the config for a substring: the thing that
 * matters is where `/api/notifications/unread-count` ends up, not whether some
 * line mentions notifications.
 */

import { beforeAll, afterAll, describe, expect, it } from "vitest";

// The config is plain JavaScript and the app build sets `allowJs: false`, so
// there is no declaration file for it. Its shape is pinned by the cast below.
// @ts-expect-error - untyped .mjs import
import untypedConfig from "../next.config.mjs";

type Rewrite = { source: string; destination: string };

const nextConfig = untypedConfig as { rewrites: () => Promise<Rewrite[]> };

/**
 * Resolve `pathname` against the rewrite table the way Next does: first source
 * that matches wins, and a `:name*` segment captures the rest of the path.
 * Returns the destination, or null when nothing matches (a production 404).
 */
function resolve(rewrites: Rewrite[], pathname: string): string | null {
  for (const rewrite of rewrites) {
    const keys: string[] = [];
    const pattern = rewrite.source
      .split("/")
      .filter(Boolean)
      .map((segment) => {
        if (segment.startsWith(":") && segment.endsWith("*")) {
          keys.push(segment.slice(1, -1));
          // Zero or more segments, so the separating slash is optional too.
          return "(?:/(.*))?";
        }
        if (segment.startsWith(":")) {
          keys.push(segment.slice(1));
          return "/([^/]+)";
        }
        return `/${segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`;
      })
      .join("");
    const match = new RegExp(`^${pattern}$`).exec(pathname);
    if (!match) continue;

    let destination = rewrite.destination;
    keys.forEach((key, index) => {
      const value = match[index + 1] ?? "";
      destination = destination.includes(`/:${key}*`)
        ? destination.replace(`/:${key}*`, value ? `/${value}` : "")
        : destination.replace(`:${key}`, value);
    });
    return destination;
  }
  return null;
}

const DEFAULT_TARGET = "http://api:8000";

describe("the api proxy table", () => {
  let rewrites: Rewrite[];

  beforeAll(async () => {
    delete process.env.MEOBOT_API_URL;
    rewrites = await nextConfig.rewrites();
  });

  it("still proxies the PR routes unchanged", () => {
    expect(resolve(rewrites, "/api/pr/content")).toBe(`${DEFAULT_TARGET}/api/pr/content`);
    expect(resolve(rewrites, "/api/pr/tasks/42/approve")).toBe(
      `${DEFAULT_TARGET}/api/pr/tasks/42/approve`,
    );
    expect(resolve(rewrites, "/api/auth/me")).toBe(`${DEFAULT_TARGET}/api/auth/me`);
    expect(resolve(rewrites, "/auth/login")).toBe(`${DEFAULT_TARGET}/auth/login`);
  });

  it("proxies the notification collection, which has no sub-path", () => {
    // `apiClient.notifications.list` calls `/api/notifications?...`; the query
    // string is not part of rewrite matching, the pathname is.
    expect(resolve(rewrites, "/api/notifications")).toBe(
      `${DEFAULT_TARGET}/api/notifications`,
    );
  });

  it("proxies the unread badge poll", () => {
    expect(resolve(rewrites, "/api/notifications/unread-count")).toBe(
      `${DEFAULT_TARGET}/api/notifications/unread-count`,
    );
  });

  it("proxies both mark-read routes", () => {
    expect(resolve(rewrites, "/api/notifications/42/read")).toBe(
      `${DEFAULT_TARGET}/api/notifications/42/read`,
    );
    expect(resolve(rewrites, "/api/notifications/read-all")).toBe(
      `${DEFAULT_TARGET}/api/notifications/read-all`,
    );
  });

  it("does not sweep unrelated paths into the API", () => {
    // A page route must keep being served by Next.
    expect(resolve(rewrites, "/pr/tasks")).toBeNull();
    expect(resolve(rewrites, "/api/notificationsomething")).toBeNull();
  });

  it("covers every /api path the client actually calls", async () => {
    const { readFileSync } = await import("node:fs");
    const path = await import("node:path");
    const client = readFileSync(
      path.resolve(__dirname, "../src/lib/api.ts"),
      "utf8",
    );
    // Literal prefixes of every `/api/...` string in the client, with template
    // holes and query suffixes cut back to the last static segment.
    const prefixes = new Set(
      [...client.matchAll(/["'`](\/api\/[^"'`$]*)/g)].map(([, found]) =>
        found.replace(/\/$/, ""),
      ),
    );
    expect(prefixes.size).toBeGreaterThan(5);
    for (const prefix of prefixes) {
      expect(resolve(rewrites, prefix), prefix).not.toBeNull();
    }
  });
});

describe("the proxy target", () => {
  const original = process.env.MEOBOT_API_URL;

  afterAll(() => {
    if (original === undefined) delete process.env.MEOBOT_API_URL;
    else process.env.MEOBOT_API_URL = original;
  });

  it("comes from MEOBOT_API_URL for notifications exactly as for PR", async () => {
    process.env.MEOBOT_API_URL = "http://meobot-api:8000";
    const rewrites = await nextConfig.rewrites();
    expect(resolve(rewrites, "/api/pr/content")).toBe(
      "http://meobot-api:8000/api/pr/content",
    );
    expect(resolve(rewrites, "/api/notifications")).toBe(
      "http://meobot-api:8000/api/notifications",
    );
    expect(resolve(rewrites, "/api/notifications/unread-count")).toBe(
      "http://meobot-api:8000/api/notifications/unread-count",
    );
  });
});
