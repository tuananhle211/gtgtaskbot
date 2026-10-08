/**
 * The two public legal pages, /terms and /privacy.
 *
 * These are the only routes in this app a visitor without a session is meant to
 * reach, and the only ones a third party reads on our behalf: TikTok's app
 * review opens them directly. So the assertions here are of two kinds.
 *
 * The first is that the pages are *public* - not by trusting a comment, but by
 * checking the three places that could make them private: the segment does not
 * render `Shell` (the component that holds the session query and swaps in a
 * sign-in prompt), no page in the segment calls the API client, and the proxy
 * table in `next.config.mjs` does not sweep these paths off to FastAPI.
 *
 * The second is that the documents do not claim things that are untrue. A
 * privacy policy saying TasksBot can post to TikTok would be a compliance problem
 * rather than a rendering bug, and it is the kind of sentence that arrives later
 * in an innocent-looking edit. The forbidden-claims test is the guard.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TermsPage, { metadata as termsMetadata } from "@/app/(legal)/terms/page";
import PrivacyPage, { metadata as privacyMetadata } from "@/app/(legal)/privacy/page";

const SRC = path.resolve(__dirname, "../src");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

const LEGAL_SOURCES = [
  "app/(legal)/layout.tsx",
  "app/(legal)/terms/page.tsx",
  "app/(legal)/privacy/page.tsx",
  "components/legal.tsx",
];

/** Every heading on a rendered page, lower-cased, in document order. */
function headings(): string[] {
  return screen
    .getAllByRole("heading")
    .map((node) => (node.textContent ?? "").toLowerCase());
}

/** True when some heading mentions every word in `phrase`. */
const covers = (phrase: string) => {
  const words = phrase.toLowerCase().split(" ");
  return headings().some((heading) => words.every((word) => heading.includes(word)));
};

describe("the legal pages are reachable without a session", () => {
  it("does not wrap the segment in Shell, which is what gates /pr", () => {
    for (const relative of LEGAL_SOURCES) {
      const source = read(relative);
      expect(source, relative).not.toContain("components/shell");
      expect(source, relative).not.toContain("<Shell");
    }
  });

  it("never calls the API, so there is nothing to 401", () => {
    for (const relative of LEGAL_SOURCES) {
      const source = read(relative);
      expect(source, relative).not.toContain("@/lib/api");
      expect(source, relative).not.toContain("useQuery");
      // A server component that fetched would still be a request the visitor's
      // missing cookie could fail.
      expect(source, relative).not.toContain("fetch(");
    }
  });

  it("renders as static prose with no client boundary", () => {
    for (const relative of LEGAL_SOURCES) {
      expect(read(relative), relative).not.toContain('"use client"');
    }
  });

  it("stays per-request, so the nonce-based CSP admits Next's scripts", () => {
    expect(read("app/(legal)/layout.tsx")).toContain("await headers()");
  });

  it("is not swept into the API proxy by a rewrite", async () => {
    // @ts-expect-error - untyped .mjs import
    const untypedConfig = (await import("../next.config.mjs")).default;
    const config = untypedConfig as { rewrites: () => Promise<{ source: string }[]> };
    const sources = (await config.rewrites()).map((rule) => rule.source);
    for (const route of ["/terms", "/privacy"]) {
      expect(sources, route).not.toContain(route);
      // Nor by a wildcard: every rule in this table is rooted at /api or /auth.
      for (const source of sources) {
        expect(source.startsWith("/api") || source.startsWith("/auth"), source).toBe(true);
      }
    }
  });
});

describe("page metadata", () => {
  it("titles and describes both pages", () => {
    // Each page names only itself; the product comes from the root layout's
    // title template. Asserted as the composed result, because the tab title is
    // the thing that has to be right - see the next test for the composition.
    expect(termsMetadata.title).toBe("Terms of Service");
    expect(privacyMetadata.title).toBe("Privacy Policy");
    expect(String(termsMetadata.description).length).toBeGreaterThan(40);
    expect(String(privacyMetadata.description).length).toBeGreaterThan(40);
  });

  it("composes to the exact titles registered with TikTok", async () => {
    // The addresses given to TikTok's app review are checked by a human reading
    // the browser tab, so the composed string is the requirement - not either
    // half of it. If the root template is edited, this fails here rather than in
    // a review queue.
    const root = (await import("@/app/layout")).metadata;
    const template = (root.title as { template: string }).template;
    expect((root.title as { default: string }).default).toBe("TasksBot");

    const compose = (title: unknown) => template.replace("%s", String(title));
    expect(compose(termsMetadata.title)).toBe("Terms of Service | TasksBot");
    expect(compose(privacyMetadata.title)).toBe("Privacy Policy | TasksBot");
  });

  it("overrides the app-wide noindex, because these two are published", () => {
    // src/app/layout.tsx sends `noindex, nofollow` for the panel. A public legal
    // document that refuses to be found is only half published.
    for (const metadata of [termsMetadata, privacyMetadata]) {
      expect(metadata.robots).toEqual({ index: true, follow: true });
    }
    expect(read("app/layout.tsx")).toContain("robots");
  });
});

describe("the Terms of Service", () => {
  it("covers every required section", () => {
    render(<TermsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Terms of Service");
    for (const section of [
      "about tasksbot",
      "eligibility",
      "accounts",
      "acceptable use",
      "third-party services",
      "tiktok integration",
      "content",
      "availability",
      "warranties",
      "limitation of liability",
      "termination of access",
      "changes to these terms",
      "contact",
    ]) {
      expect(covers(section), section).toBe(true);
    }
    expect(screen.getAllByText(/effective date/i).length).toBeGreaterThan(0);
  });

  it("disclaims any TikTok affiliation rather than implying one", () => {
    const { container } = render(<TermsPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/not.{0,40}affiliated with/i);
    expect(text).toMatch(/endorsed by/i);
    expect(text).toMatch(/govern/i);
    // The authorization promise, which is the one users can act on.
    expect(text).toMatch(/only connect social media accounts you own or are authorized to manage/i);
  });
});

describe("the Privacy Policy", () => {
  it("covers every required section", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Privacy Policy");
    for (const section of [
      "overview",
      "information tasksbot may collect",
      "account authentication information",
      "connected social platform information",
      "tiktok data",
      "usage technical information",
      "how information is used",
      "data sharing",
      "third-party service providers",
      "data retention",
      "security",
      "disconnecting an account",
      "data deletion",
      "children's privacy",
      "changes to this privacy policy",
      "contact",
    ]) {
      expect(covers(section), section).toBe(true);
    }
    expect(screen.getAllByText(/effective date/i).length).toBeGreaterThan(0);
  });

  it("names the four TikTok scopes and what each exposes", () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? "";
    for (const scope of [
      "user.info.basic",
      "user.info.profile",
      "user.info.stats",
      "video.list",
    ]) {
      expect(text, scope).toContain(scope);
    }
    expect(text).toMatch(/display name/i);
    expect(text).toMatch(/follower count/i);
    expect(text).toMatch(/display api/i);
  });

  it("ties TikTok access to OAuth authorization and granted scopes only", () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/only.{0,60}authorize.{0,80}oauth/is);
    expect(text).toMatch(/within the scopes you grant/i);
    expect(text).toMatch(/access and refresh tokens/i);
    expect(text).toMatch(/stops future access/i);
    expect(text).toMatch(
      /used only to provide the connected social media management and analytics functionality/i,
    );
  });

  it("gives a deletion route that does not depend on an invented address", () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(
      /request deletion of account-related or connected-platform data by contacting the TasksBot administrator/i,
    );
    // No support mailbox exists in this repo, so neither page may imply one.
    expect(text).not.toMatch(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
  });
});

describe("neither document claims access TasksBot does not have", () => {
  /**
   * Claims that would be false, matched against *affirmative* sentences only.
   *
   * Scanning the whole page for "publish to TikTok" would flag the sentence that
   * says TasksBot does not - which is the sentence we want. So sentences carrying
   * a negation are dropped first, and what is left is what the document asserts.
   */
  const forbidden: [RegExp, string][] = [
    [/tiktok password|password[^.]{0,40}tiktok/i, "holding a TikTok password"],
    [/(post|publish|upload)[a-z]*\s+(to|on)\s+tiktok/i, "posting to TikTok"],
    [/(read|collect|access)[a-z]*[^.]{0,40}(direct|private) messages?/i, "reading messages"],
  ];

  /** Sentences that assert something, i.e. those without a negation in them. */
  function affirmativeSentences(text: string): string[] {
    return text
      .split(/(?<=[.;:])\s+|(?=\d+\.\s?[A-Z])/)
      .filter((sentence) => !/\b(not|never|no)\b/i.test(sentence));
  }

  it("makes none of them, and states each negative explicitly", () => {
    for (const [page, node] of [
      ["terms", <TermsPage key="t" />],
      ["privacy", <PrivacyPage key="p" />],
    ] as const) {
      const { container, unmount } = render(node);
      const text = container.textContent ?? "";

      for (const sentence of affirmativeSentences(text)) {
        for (const [pattern, label] of forbidden) {
          expect(sentence, `${page}: ${label}`).not.toMatch(pattern);
        }
      }

      // Not merely silent - each page says the negative out loud.
      expect(text, page).toMatch(/does\s+not\s+post/i);
      expect(text, page).toMatch(/does\s+not[^.]{0,40}(direct )?messages/i);
      expect(text, page).toMatch(/beyond the (permissions|scopes) you grant/i);
      unmount();
    }
  });
});

describe("the pages link to each other and home", () => {
  it("points Terms at Privacy and at the home page", () => {
    render(<TermsPage />);
    const nav = screen.getByRole("navigation", { name: /legal documents/i });
    expect(within(nav).getByRole("link", { name: "Privacy Policy" })).toHaveAttribute(
      "href",
      "/privacy",
    );
    expect(within(nav).getByRole("link", { name: /tasksbot home/i })).toHaveAttribute("href", "/");
  });

  it("points Privacy at Terms and at the home page", () => {
    render(<PrivacyPage />);
    const nav = screen.getByRole("navigation", { name: /legal documents/i });
    expect(within(nav).getByRole("link", { name: "Terms of Service" })).toHaveAttribute(
      "href",
      "/terms",
    );
    expect(within(nav).getByRole("link", { name: /tasksbot home/i })).toHaveAttribute("href", "/");
  });

  it("marks the English prose as English inside a Vietnamese document", () => {
    const { container } = render(<TermsPage />);
    expect(container.querySelector("article")).toHaveAttribute("lang", "en");
  });

  it("carries one effective date, shared by both pages", () => {
    const { container: terms, unmount } = render(<TermsPage />);
    const termsDate = terms.querySelector("time")?.getAttribute("datetime");
    unmount();
    const { container: privacy } = render(<PrivacyPage />);
    expect(privacy.querySelector("time")?.getAttribute("datetime")).toBe(termsDate);
    expect(termsDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
