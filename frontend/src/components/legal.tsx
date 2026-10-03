import Link from "next/link";

/**
 * The two public legal pages, /terms and /privacy, share one shell.
 *
 * They are the only pages in this app a visitor may see without a session, and
 * the only ones written in English: they exist so a platform reviewer - TikTok's,
 * today - can read what MeoChat does with the data an account owner authorizes.
 * The rest of the panel speaks Vietnamese to the team that uses it, so the
 * `<article>` here carries its own `lang` rather than the document's.
 *
 * Everything is server-rendered. There is no state, no query and no `"use
 * client"`, because a legal page that needs JavaScript to say what it says is a
 * legal page that can fail to say it.
 *
 * ## Why the body styling is arbitrary variants
 *
 * There is no typography plugin in this project, and the two pages together hold
 * something like a hundred paragraphs and list items. Putting the same three
 * classNames on each of them is how the two documents drift apart. One
 * descendant-selector block on the wrapper keeps the rules in one place without
 * adding a dependency or a global stylesheet rule that only two routes use.
 */

/**
 * The date both documents took effect, in one place.
 *
 * Two pages that disagree about when the terms they describe started applying is
 * the one factual error a legal page cannot afford, and it is exactly the kind
 * that survives a copy-edit of only one file.
 */
export const EFFECTIVE_DATE = { iso: "2026-08-24", label: "August 24, 2026" } as const;

/** Paragraph, list and heading rhythm for a body of prose. */
const PROSE = [
  "text-[15px] leading-7",
  "[&_h2]:mt-10 [&_h2]:scroll-mt-6 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:tracking-tight [&_h2]:text-[var(--text)]",
  "[&_h3]:mt-6 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-[var(--text)]",
  "[&_p]:mt-3 [&_p]:text-[var(--text-muted)]",
  "[&_ul]:mt-3 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5 [&_ul]:text-[var(--text-muted)]",
  "[&_li>strong]:font-medium [&_li>strong]:text-[var(--text)]",
  "[&_a]:text-[var(--accent)] [&_a]:underline [&_a]:underline-offset-2",
].join(" ");

/** One numbered-in-spirit section: an anchorable heading and its prose. */
export function LegalSection({
  id,
  heading,
  children,
}: {
  id: string;
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id}>
      <h2 id={id}>{heading}</h2>
      {children}
    </section>
  );
}

/**
 * The frame both documents sit in: title, effective date, body, cross-links.
 *
 * `sibling` is the *other* legal page. Each document links to the one it is not,
 * so somebody who arrived at a bare URL from a review form can reach both.
 */
export function LegalDocument({
  title,
  lead,
  sibling,
  children,
}: {
  title: string;
  lead: string;
  sibling: { href: string; label: string };
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <header className="mb-6">
        <Link
          href="/"
          className="inline-flex min-h-11 items-center text-sm font-semibold tracking-tight text-[var(--text)] hover:text-[var(--accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
        >
          MeoChat
        </Link>
      </header>

      <article
        lang="en"
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 sm:p-10"
      >
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          Effective date:{" "}
          <time dateTime={EFFECTIVE_DATE.iso}>{EFFECTIVE_DATE.label}</time>
        </p>
        <p className="mt-4 text-sm text-[var(--text-muted)]">{lead}</p>

        <div className={`mt-2 ${PROSE}`}>{children}</div>
      </article>

      <nav
        aria-label="Legal documents"
        className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm"
      >
        <Link
          href={sibling.href}
          className="inline-flex min-h-11 items-center text-[var(--accent)] underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
        >
          {sibling.label}
        </Link>
        <Link
          href="/"
          className="inline-flex min-h-11 items-center text-[var(--text-muted)] underline underline-offset-2 hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
        >
          MeoChat home
        </Link>
      </nav>
    </div>
  );
}
