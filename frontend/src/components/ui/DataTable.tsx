"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

export interface Col<T> { key: string; header: string; cell: (row: T) => ReactNode; className?: string }
/** Real <table> on tablets and up (sortable server-side via the query); stacked labelled cards on phones. First column links for keyboard/screen-reader users. */
export function DataTable<T>({ columns, rows, rowKey, href, caption }: { columns: Col<T>[]; rows: T[]; rowKey: (r: T) => string; href?: (r: T) => string; caption: string }) {
  const router = useRouter();
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="data">
        <caption className="sr-only">{caption}</caption>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col">{c.header}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} className={href ? "clickable" : undefined} onClick={href ? (e) => { if (!(e.target as HTMLElement).closest("a,button")) router.push(href(r)); } : undefined}>
              {columns.map((c, i) => <td key={c.key} data-label={c.header} className={c.className}>{i === 0 && href ? <Link href={href(r)}>{c.cell(r)}</Link> : c.cell(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
