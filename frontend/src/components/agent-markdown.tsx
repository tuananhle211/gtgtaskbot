import React from "react";

/**
 * The agents answer in Markdown (Đội agent, 0091). This draws the part they
 * use - headings, lists, tables, code, quotes, bold / italic, links - as React
 * elements only: no HTML from the answer ever reaches the page, and a link is
 * drawn only for http(s) / mailto.
 */

const INLINE =
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\((?:https?:\/\/|mailto:)[^\s)]+\))|(https?:\/\/[^\s<]*[^\s<.,;:!?)\]'"”])|((?:^|(?<=\s))[*_][^*_\s][^*_\n]*[*_](?=\s|$|[.,;:!?]))/g;

function safeHref(url: string): string | null {
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : null;
}

function link(href: string, label: React.ReactNode, key: string) {
  return (
    <a
      key={key}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="break-words text-[var(--accent)] underline underline-offset-2"
    >
      {label}
    </a>
  );
}

export function renderInline(text: string, keyBase = "i"): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    const [whole, code, bold, mdLink, bare, italic] = match;
    const key = `${keyBase}-${index++}`;
    if (code) {
      out.push(
        <code key={key} className="rounded bg-[var(--surface-muted)] px-1 py-0.5 font-mono text-[0.85em]">
          {code.slice(1, -1)}
        </code>,
      );
    } else if (bold) {
      out.push(<strong key={key}>{renderInline(bold.slice(2, -2), key)}</strong>);
    } else if (mdLink) {
      const label = mdLink.slice(1, mdLink.indexOf("]("));
      const href = safeHref(mdLink.slice(mdLink.indexOf("](") + 2, -1));
      out.push(href ? link(href, label, key) : label);
    } else if (bare) {
      out.push(link(bare, bare, key));
    } else if (italic) {
      out.push(<em key={key}>{italic.slice(1, -1)}</em>);
    } else {
      out.push(whole);
    }
    last = start + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

export function AgentMarkdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  const next = () => `b${key++}`;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // ``` fenced code
    if (line.trim().startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(
        <pre
          key={next()}
          className="overflow-x-auto rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 font-mono text-xs leading-relaxed"
        >
          {body.join("\n")}
        </pre>,
      );
      continue;
    }
    // headings: drawn small, it is a chat bubble
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <p key={next()} className="font-semibold text-[var(--text)]">
          {renderInline(heading[2], `h${key}`)}
        </p>,
      );
      i++;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={next()} className="border-[var(--border)]" />);
      i++;
      continue;
    }
    // table: a header row followed by |---|
    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={next()} className="overflow-x-auto rounded-lg border border-[var(--border)]">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="bg-[var(--surface-muted)]">
              <tr>
                {head.map((cell, c) => (
                  <th key={c} className="border-b border-[var(--border)] px-2 py-1.5 font-semibold">
                    {renderInline(cell, `th${key}-${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r} className="border-t border-[var(--border)]">
                  {head.map((_, c) => (
                    <td key={c} className="px-2 py-1.5 align-top">
                      {renderInline(row[c] ?? "", `td${key}-${r}-${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    // lists
    const bullet = /^\s*[-*+]\s+(.*)$/;
    const numbered = /^\s*\d+[.)]\s+(.*)$/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line) && !bullet.test(line);
      const pattern = ordered ? numbered : bullet;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push((pattern.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={next()} className={`space-y-0.5 pl-5 ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((item, n) => (
            <li key={n}>{renderInline(item, `li${key}-${n}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(
        <blockquote key={next()} className="border-l-2 border-[var(--border)] pl-3 text-[var(--text-muted)]">
          {renderInline(quote.join(" "), `q${key}`)}
        </blockquote>,
      );
      continue;
    }
    // a paragraph: lines until a blank one or another block
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("```") &&
      !/^(#{1,4})\s/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !lines[i].startsWith(">")
    ) {
      para.push(lines[i++]);
    }
    if (para.length === 0) {
      // A line no rule took (e.g. a lone "|"): keep it as text.
      para.push(lines[i++]);
    }
    blocks.push(
      <p key={next()} className="whitespace-pre-wrap break-words">
        {para.map((row, n) => (
          <React.Fragment key={n}>
            {n > 0 ? <br /> : null}
            {renderInline(row, `p${key}-${n}`)}
          </React.Fragment>
        ))}
      </p>,
    );
  }
  return <div className="space-y-2 text-sm leading-relaxed">{blocks}</div>;
}
