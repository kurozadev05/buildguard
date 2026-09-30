import { Fragment, type ReactNode } from "react";
import type { AiSource } from "@/lib/types";

/** Safe renderer for AI text. It builds React elements from plain strings (React escapes everything), supports only
 *  paragraphs, "- " lists, **bold**, `code`, ``` fences and [K3]/[D1] source chips. No HTML, no links, no images: AI output is untrusted. */
function inline(text: string, sources: AiSource[], keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []; const re = /(\*\*[^*\n]{1,200}\*\*|`[^`\n]{1,200}`|\[[KD]\d{1,3}\])/g; let last = 0; let m: RegExpExecArray | null; let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${keyPrefix}${i++}`}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={`${keyPrefix}${i++}`}>{tok.slice(1, -1)}</code>);
    else { const label = tok.slice(1, -1); const s = sources.find((x) => x.label === label); out.push(<span key={`${keyPrefix}${i++}`} className="chip" title={s ? `${s.title}${s.reference ? ` (${s.reference})` : ""}` : "Source"}>{label}</span>); }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
export function RichText({ text, sources = [] }: { text: string; sources?: AiSource[] }) {
  const parts = text.split(/```/);
  return (
    <>
      {parts.map((part, pi) => {
        if (pi % 2 === 1) return <pre key={pi} tabIndex={0}><code>{part.replace(/^[a-z]*\n/, "")}</code></pre>;
        return <Fragment key={pi}>{part.split(/\n{2,}/).filter((b) => b.trim()).map((block, bi) => {
          const lines = block.split("\n");
          if (lines.every((l) => /^\s*[-*] /.test(l))) return <ul key={bi}>{lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*[-*] /, ""), sources, `${pi}${bi}${li}`)}</li>)}</ul>;
          return <p key={bi}>{lines.map((l, li) => <Fragment key={li}>{li > 0 && <br />}{inline(l, sources, `${pi}${bi}${li}`)}</Fragment>)}</p>;
        })}</Fragment>;
      })}
    </>
  );
}
