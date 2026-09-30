import { ApiError, newRequestId, toApiError } from "./api";
import type { ChatDone } from "./types";

export interface SseEvent { event: string; data: unknown }
/** Incremental Server-Sent-Events parser: feed decoded text chunks, get complete events. Handles events split across chunks. */
export class SseParser {
  private buf = "";
  push(chunk: string): SseEvent[] {
    this.buf += chunk.replace(/\r\n/g, "\n");
    const out: SseEvent[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, i); this.buf = this.buf.slice(i + 2);
      let event = "message"; const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;                         // comment / keep-alive
        if (line.startsWith("event:")) event = line.slice(6).trim(); else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (!data.length) continue;
      try { out.push({ event, data: JSON.parse(data.join("\n")) }); } catch { /* malformed frame: skip, never crash the UI */ }
    }
    return out;
  }
}

export interface StreamHandlers {
  onMeta?: (conversationId: string) => void; onDelta: (text: string) => void; onTool?: (name: string, ok: boolean) => void;
  onReplace?: (text: string) => void; onDone: (d: ChatDone) => void; onError: (e: { code: string; message: string }) => void;
}
/** POST /bff/ai/chat/stream. Pre-flight failures (auth, limits, ownership) throw ApiError; mid-stream failures go to onError. Abort via `signal`. */
export async function streamChat(body: { message: string; conversation_id?: string | null; project_id?: string | null }, h: StreamHandlers, signal: AbortSignal, lang = "en"): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/bff/ai/chat/stream", { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      headers: { "content-type": "application/json", "x-bg-csrf": "1", "x-request-id": newRequestId(), "accept-language": lang, accept: "text/event-stream" }, body: JSON.stringify(body) });
  } catch (e) {
    if (signal.aborted) throw e;
    throw new ApiError(0, navigator.onLine ? "NETWORK" : "OFFLINE", "Network error", navigator.onLine ? "network" : "offline");
  }
  if (!res.ok) { const err = await toApiError(res); if (err.kind === "auth") window.dispatchEvent(new CustomEvent("bg:session-expired")); throw err; }
  if (!res.body) throw new ApiError(0, "NO_STREAM", "Streaming is not supported", "unknown");
  const reader = res.body.getReader(), dec = new TextDecoder(), parser = new SseParser();
  let finished = false, drained = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) { drained = true; break; }
      for (const ev of parser.push(dec.decode(value, { stream: true }))) {
        const d = ev.data as Record<string, unknown>;
        if (ev.event === "meta") h.onMeta?.(String(d.conversation_id));
        else if (ev.event === "delta") h.onDelta(String(d.text ?? ""));
        else if (ev.event === "tool") h.onTool?.(String(d.name), d.ok !== false);
        else if (ev.event === "replace") h.onReplace?.(String(d.text ?? ""));
        else if (ev.event === "done") { finished = true; h.onDone(d as unknown as ChatDone); }
        else if (ev.event === "error") { finished = true; h.onError({ code: String(d.code ?? "ERROR"), message: String(d.message ?? "") }); }
      }
    }
    if (!finished && !signal.aborted) h.onError({ code: "AI_STREAM_INTERRUPTED", message: "The connection was interrupted." });
  } finally { if (!drained) reader.cancel().catch(() => undefined); }   // only cancel an unfinished stream (user pressed Stop / error); a drained one just ends
}
