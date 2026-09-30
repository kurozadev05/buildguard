"use client";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, FileUp, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { upload } from "@/lib/api";
import { friendly } from "@/lib/errors";
import { fmtBytes } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type { Doc } from "@/lib/types";
import { Field, Select } from "./ui/form";
import { useToast } from "./ui/overlay";
import { Button, FormError, Progress } from "./ui/ui";

export const MAX_UPLOAD = 10 * 1024 * 1024;
const OK_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
/** First-line checks for a friendlier UX. The server re-validates content, size and type: never trust these. */
export function checkFile(f: File): string | null {
  if (!OK_TYPES.includes(f.type)) return "Choose a JPEG, PNG, WebP image or a PDF.";
  if (f.size > MAX_UPLOAD) return `That file is ${fmtBytes(f.size)}. The limit is ${fmtBytes(MAX_UPLOAD)}.`;
  if (f.size === 0) return "That file is empty.";
  return null;
}
export function UploadBox({ projectId, batchId, sampleId, testId, kind = "photo", kinds, onDone }: { projectId: string; batchId?: string; sampleId?: string; testId?: string; kind?: string; kinds?: string[]; onDone?: (d: Doc) => void }) {
  const { t } = useT(); const toast = useToast(); const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null); const [preview, setPreview] = useState<string | null>(null); const [problem, setProblem] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null); const [error, setError] = useState<unknown>(null); const [k, setK] = useState(kind); const ac = useRef<AbortController | null>(null);
  useEffect(() => { if (file && file.type.startsWith("image/")) { const u = URL.createObjectURL(file); setPreview(u); return () => URL.revokeObjectURL(u); } setPreview(null); }, [file]);
  function pick(f?: File) { setError(null); if (!f) return; const p = checkFile(f); setProblem(p); setFile(p ? null : f); }
  async function send() {
    if (!file) return; setError(null); setPct(0); ac.current = new AbortController();
    const fd = new FormData(); fd.append("file", file); fd.append("kind", k); fd.append("project_id", projectId);
    if (batchId) fd.append("batch_id", batchId); if (sampleId) fd.append("sample_id", sampleId); if (testId) fd.append("test_id", testId);
    try { const d = await upload<Doc>("documents", fd, { onProgress: setPct, signal: ac.current.signal }); toast({ kind: "success", title: "Uploaded", message: "The file was saved with a tamper-evident hash." }); setFile(null); onDone?.(d); void qc.invalidateQueries({ queryKey: ["documents"] }); }
    catch (e) { if (e instanceof DOMException && e.name === "AbortError") toast({ kind: "info", title: "Upload cancelled" }); else setError(e); }
    setPct(null);
  }
  return (
    <div className="stack-sm">
      {kinds && <Field label="Type"><Select value={k} onChange={(e) => setK(e.target.value)}>{kinds.map((x) => <option key={x} value={x}>{x.replaceAll("_", " ")}</option>)}</Select></Field>}
      <div className="row">
        <label className="btn"><Camera size={18} aria-hidden />Take photo<input type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => pick(e.target.files?.[0])} disabled={pct !== null} /></label>
        <label className="btn"><FileUp size={18} aria-hidden />Choose file<input type="file" accept={OK_TYPES.join(",")} className="sr-only" onChange={(e) => pick(e.target.files?.[0])} disabled={pct !== null} /></label>
      </div>
      {problem && <p className="err-text" role="alert">✕ {problem}</p>}
      {file && (
        <div className="row card" style={{ padding: ".6rem" }}>
          {preview && ( // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="Selected file preview" style={{ width: 72, height: 72, objectFit: "cover", borderRadius: 4 }} />)}
          <div className="grow"><div style={{ overflowWrap: "anywhere" }}>{file.name}</div><div className="hint">{fmtBytes(file.size)}</div>{pct !== null && <Progress value={pct} max={100} label="Upload progress" />}</div>
          {pct === null ? <><Button variant="primary" onClick={send}>Upload</Button><Button variant="ghost" icon onClick={() => setFile(null)} aria-label={t("common.cancel")}><X size={18} aria-hidden /></Button></> : <Button onClick={() => ac.current?.abort()}>{t("common.cancel")} ({pct}%)</Button>}
        </div>
      )}
      <FormError error={error} />
      {error ? <span className="sr-only">{friendly(error, t).message}</span> : null}
    </div>
  );
}
