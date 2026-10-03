import type { ReactNode } from "react";

/**
 * Free text with its `http://` and `https://` links made clickable.
 *
 * The whole renderer: split the text on URLs, wrap each URL in an anchor, and
 * keep everything else as the text node it already is. No HTML is parsed and
 * no markup is interpreted - a member typing `<b>` sees `<b>` - which is why
 * this is a handful of lines and not a Markdown engine. Line breaks survive
 * through `whitespace-pre-wrap` on the wrapper, and long links wrap rather than
 * widening a phone screen.
 *
 * Trailing sentence punctuation is left outside the link, so "xem
 * https://a.b/c." opens `https://a.b/c` - the same rule the server applies when
 * it picks a text's first link as the row's location.
 */
const URL_PATTERN = /https?:\/\/[^\s<>"']+/g;
const TRAILING = /[.,;:!?)\]}'"]+$/;

export function linkifySegments(text: string): Array<{ kind: "text" | "link"; value: string }> {
  const out: Array<{ kind: "text" | "link"; value: string }> = [];
  let cursor = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index ?? 0;
    let url = match[0];
    const trailing = url.match(TRAILING)?.[0] ?? "";
    url = url.slice(0, url.length - trailing.length);
    if (!url) continue;
    if (start > cursor) out.push({ kind: "text", value: text.slice(cursor, start) });
    out.push({ kind: "link", value: url });
    cursor = start + url.length;
  }
  if (cursor < text.length) out.push({ kind: "text", value: text.slice(cursor) });
  return out;
}

export function Linkified({ text, className }: { text: string; className?: string }) {
  const segments = linkifySegments(text);
  const nodes: ReactNode[] = segments.map((segment, index) =>
    segment.kind === "link" ? (
      <a
        key={index}
        href={segment.value}
        target="_blank"
        rel="noreferrer noopener"
        className="break-all underline underline-offset-2"
      >
        {segment.value}
      </a>
    ) : (
      <span key={index}>{segment.value}</span>
    ),
  );
  return <span className={`whitespace-pre-wrap break-words ${className ?? ""}`}>{nodes}</span>;
}
