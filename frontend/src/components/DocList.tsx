"use client";
import { Download, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { fmtBytes, fmtDateTime } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks";
import { friendly } from "@/lib/errors";
import { useT } from "@/lib/i18n";
import type { Doc } from "@/lib/types";
import { useToast } from "./ui/overlay";
import { Button, EmptyState, ErrorState, SkeletonRows } from "./ui/ui";

export function DocList({ query, empty }: { query: Record<string, string | undefined>; empty?: string }) {
  const { t, lang } = useT(); const toast = useToast(); const [busy, setBusy] = useState<string | null>(null);
  const q = useApiQuery<Doc[]>(["documents"], "documents", { query: { ...query, limit: 50 } });
  async function verify(d: Doc) {
    setBusy(d.id);
    try { const r = await api<{ ok?: boolean; hash_matches?: boolean; message?: string }>(`documents/${d.id}/verify`); const good = r.ok ?? r.hash_matches ?? true; toast({ kind: good ? "success" : "error", title: good ? "✓ File is unchanged since upload" : "✕ File does not match its recorded hash", message: r.message }); }
    catch (e) { toast({ kind: "error", title: friendly(e, t).message }); } finally { setBusy(null); }
  }
  if (q.isLoading) return <SkeletonRows rows={3} />; if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} compact />;
  if (!q.data?.length) return <EmptyState title={empty ?? "No files yet"} hint="Photos and reports you upload appear here, each sealed with a SHA-256 hash." />;
  return (
    <ul className="list">{q.data.map((d) => (
      <li key={d.id} className="row between"><div className="grow"><div style={{ overflowWrap: "anywhere" }}><strong>{d.filename}</strong> <span className="badge neutral">{d.kind}</span></div><div className="hint">{fmtBytes(d.size_bytes)} · {fmtDateTime(d.uploaded_at, lang)} · <span className="mono">{d.sha256.slice(0, 10)}…</span></div></div>
        <div className="row"><Button size="sm" loading={busy === d.id} onClick={() => verify(d)}><ShieldCheck size={16} aria-hidden />Verify</Button><a className="btn sm" href={`/bff/documents/${d.id}/download`} download><Download size={16} aria-hidden />Download</a></div></li>
    ))}</ul>
  );
}
