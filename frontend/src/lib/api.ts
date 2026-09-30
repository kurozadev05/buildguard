import { setNetDown } from "./net";
export type ErrorKind = "offline" | "network" | "timeout" | "auth" | "forbidden" | "notfound" | "validation" | "conflict" | "rate_limit" | "server" | "unknown";
export interface FieldError { field: string; message: string }

/** Server error details may contain debug entries in dev mode; keep only well-formed field errors. */
export const cleanDetails = (d: unknown): FieldError[] => (Array.isArray(d) ? d.filter((x): x is FieldError => !!x && typeof x.field === "string" && typeof x.message === "string") : []);

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public kind: ErrorKind, public details: FieldError[] = [], public requestId?: string, public retryAfter?: number) { super(message); this.name = "ApiError"; }
}
let lang = "en";
export const setApiLang = (l: string) => { lang = l; };

export type Query = Record<string, string | number | boolean | undefined | null>;
export interface ReqOpts { method?: "GET" | "POST" | "PATCH" | "DELETE"; body?: unknown; query?: Query; signal?: AbortSignal; timeoutMs?: number; retries?: number; silent401?: boolean }

function qs(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
export const newRequestId = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 16) : Math.random().toString(16).slice(2, 18).padEnd(16, "0"));

export function kindFor(status: number): ErrorKind {
  if (status === 401) return "auth"; if (status === 403) return "forbidden"; if (status === 404) return "notfound"; if (status === 409) return "conflict";
  if (status === 422 || status === 400) return "validation"; if (status === 429) return "rate_limit"; if (status === 504 || status === 408) return "timeout"; if (status >= 500) return "server";
  return "unknown";
}
/** Combines the caller's AbortSignal with a timeout without AbortSignal.any (Safari < 17.4). */
function withTimeout(signal: AbortSignal | undefined, ms: number): { signal: AbortSignal; done: () => void } {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(new DOMException("timeout", "TimeoutError")), ms);
  const onAbort = () => c.abort(signal?.reason);
  if (signal) { if (signal.aborted) onAbort(); else signal.addEventListener("abort", onAbort, { once: true }); }
  return { signal: c.signal, done: () => { clearTimeout(t); signal?.removeEventListener("abort", onAbort); } };
}
const sleep = (ms: number, signal?: AbortSignal) => new Promise<void>((res, rej) => { const t = setTimeout(res, ms); signal?.addEventListener("abort", () => { clearTimeout(t); rej(signal.reason); }, { once: true }); });

export async function toApiError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: FieldError[]; request_id?: string } } | null = null;
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  const e = body?.error;
  const ra = Number(res.headers.get("retry-after"));
  return new ApiError(res.status, e?.code ?? "HTTP_ERROR", e?.message ?? res.statusText, kindFor(res.status), cleanDetails(e?.details), e?.request_id ?? res.headers.get("x-request-id") ?? undefined, Number.isFinite(ra) && ra > 0 ? ra : undefined);
}
function notifyExpired() { if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("bg:session-expired")); }

async function once(path: string, o: ReqOpts, accept: "json" | "blob"): Promise<unknown> {
  const isForm = typeof FormData !== "undefined" && o.body instanceof FormData;
  const t = withTimeout(o.signal, o.timeoutMs ?? 30_000);
  const headers: Record<string, string> = { "x-bg-csrf": "1", "x-request-id": newRequestId(), "accept-language": lang };
  if (o.body !== undefined && !isForm) headers["content-type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/bff/${path}${qs(o.query)}`, { method: o.method ?? "GET", headers, credentials: "same-origin", cache: "no-store", signal: t.signal,
      body: o.body === undefined ? undefined : isForm ? (o.body as FormData) : JSON.stringify(o.body) });
  } catch (e) {
    t.done();
    if (o.signal?.aborted) throw e;                                                // caller cancelled: not an error to display
    if (t.signal.aborted) throw new ApiError(0, "TIMEOUT", "Request timed out", "timeout");
    const off = typeof navigator !== "undefined" && navigator.onLine === false;
    setNetDown(true);
    throw new ApiError(0, off ? "OFFLINE" : "NETWORK", off ? "You are offline" : "Network error", off ? "offline" : "network");
  }
  t.done(); setNetDown(false);
  if (!res.ok) {
    const err = await toApiError(res);
    if (err.kind === "auth" && !o.silent401) notifyExpired();
    throw err;
  }
  if (res.status === 204) return undefined;
  if (accept === "blob") return res.blob();
  const j = await res.json();
  return j && typeof j === "object" && "success" in j ? j.data : j;
}

/** JSON request through the same-origin BFF. GETs retry (network/timeout/5xx) with backoff; writes never retry automatically. */
export async function api<T>(path: string, o: ReqOpts = {}): Promise<T> {
  const retries = o.retries ?? ((o.method ?? "GET") === "GET" ? 2 : 0);
  for (let attempt = 0; ; attempt++) {
    try { return (await once(path, o, "json")) as T; } catch (e) {
      const retryable = e instanceof ApiError && ["network", "timeout", "server"].includes(e.kind) && e.status !== 500;
      if (!retryable || attempt >= retries) throw e;
      await sleep(400 * 3 ** attempt + Math.random() * 150, o.signal);
    }
  }
}
export const apiBlob = (path: string, o: ReqOpts = {}) => once(path, o, "blob") as Promise<Blob>;

/** Multipart upload with progress + cancel (fetch cannot report upload progress). */
export function upload<T>(path: string, form: FormData, o: { onProgress?: (pct: number) => void; signal?: AbortSignal; query?: Query } = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/bff/${path}${qs(o.query)}`);
    x.setRequestHeader("x-bg-csrf", "1"); x.setRequestHeader("accept-language", lang); x.setRequestHeader("x-request-id", newRequestId());
    x.upload.onprogress = (e) => { if (e.lengthComputable) o.onProgress?.(Math.round((e.loaded / e.total) * 100)); };
    x.onerror = () => { setNetDown(true); reject(new ApiError(0, "NETWORK", "Network error", navigator.onLine ? "network" : "offline")); };
    x.ontimeout = () => reject(new ApiError(0, "TIMEOUT", "Upload timed out", "timeout"));
    x.onabort = () => reject(new DOMException("aborted", "AbortError"));
    x.onload = async () => {
      let body: { success?: boolean; data?: T; error?: { code?: string; message?: string; details?: FieldError[]; request_id?: string } } | null = null;
      try { body = JSON.parse(x.responseText); } catch { /* ignore */ }
      if (x.status >= 200 && x.status < 300) return resolve((body?.data ?? body) as T);
      if (x.status === 401) notifyExpired();
      reject(new ApiError(x.status, body?.error?.code ?? "HTTP_ERROR", body?.error?.message ?? "Upload failed", kindFor(x.status), cleanDetails(body?.error?.details), body?.error?.request_id));
    };
    x.timeout = 120_000;
    o.signal?.addEventListener("abort", () => x.abort(), { once: true });
    x.send(form);
  });
}
