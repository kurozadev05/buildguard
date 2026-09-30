import "server-only";
import { randomUUID } from "node:crypto";
import type { NextRequest } from "next/server";
import { allowedOrigins, backendUrl } from "./env";
import { jwtExpMs, readCookies, refreshTokens, setCookieHeaders, type Tokens, UpstreamDown } from "./session";

const NO_STORE = { "cache-control": "no-store" };
/** Endpoints that return tokens in their body are never reachable through the proxy. */
const BLOCKED = new Set(["login", "register", "refresh", "token", "logout"]);
const SAFE_SEGMENT = /^[A-Za-z0-9._~:@-]+$/;
const PASS = ["content-type", "content-disposition", "retry-after", "x-request-id", "x-accel-buffering"];

export function errorResponse(status: number, code: string, message: string, cookies?: string[], requestId?: string): Response {
  const h = new Headers({ ...NO_STORE, "content-type": "application/json" });
  cookies?.forEach((c) => h.append("set-cookie", c));
  return new Response(JSON.stringify({ success: false, error: { code, message, details: [], request_id: requestId ?? "-" } }), { status, headers: h });
}

/** CSRF: SameSite=Strict cookies + a custom header a cross-site form cannot set + an Origin check. */
export function csrfProblem(req: NextRequest): string | null {
  if (["GET", "HEAD", "OPTIONS"].includes(req.method)) return null;
  if (req.headers.get("x-bg-csrf") !== "1") return "Missing CSRF header";
  const origin = req.headers.get("origin");
  if (origin) {
    let host = "";
    try { host = new URL(origin).host; } catch { return "Bad origin"; }
    const mine = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
    if (host !== mine && !allowedOrigins().includes(origin)) return "Cross-origin request refused";
  }
  return null;
}

export function clientIp(req: NextRequest): string {
  const xff = req.headers.get("x-forwarded-for");
  return xff ? xff.split(",")[0].trim() : req.headers.get("x-real-ip") ?? "127.0.0.1";
}

function upstreamHeaders(req: NextRequest, access: string | undefined, requestId: string): Headers {
  const h = new Headers({ "x-request-id": requestId, "x-forwarded-for": clientIp(req), "accept-encoding": "identity" });
  if (access) h.set("authorization", `Bearer ${access}`);
  for (const k of ["accept", "accept-language", "content-type"]) { const v = req.headers.get(k); if (v) h.set(k, v); }
  return h;
}

function clientRequestId(req: NextRequest): string {
  const v = req.headers.get("x-request-id");
  return v && /^[A-Za-z0-9-]{8,64}$/.test(v) ? v : randomUUID().replace(/-/g, "").slice(0, 16);
}

export function backendPath(segments: string[], search: string): string | null {
  if (!segments.length || segments.some((s) => s === "." || s === ".." || !SAFE_SEGMENT.test(s))) return null;
  if (segments[0] === "auth" && segments[1] && BLOCKED.has(segments[1])) return null;
  return `/api/${segments.map(encodeURIComponent).join("/")}${search}`;
}

export async function forward(req: NextRequest, path: string): Promise<Response> {
  const requestId = clientRequestId(req);
  const bad = csrfProblem(req);
  if (bad) return errorResponse(403, "CSRF_REFUSED", bad, undefined, requestId);

  const { access, refresh } = readCookies(req);
  let tokens: Tokens | null = null;      // set when we rotated during this request
  let cookieOut: string[] | undefined;
  let bearer = access;

  // Proactively refresh if the access cookie is gone or about to expire (also covers streaming uploads, which cannot be replayed).
  const expiring = !access || jwtExpMs(access) < Date.now() + 15_000;
  const rotate = async (): Promise<boolean> => {
    if (!refresh) return false;
    try { tokens = await refreshTokens(refresh); } catch (e) { if (e instanceof UpstreamDown) throw e; tokens = null; }
    if (!tokens) { cookieOut = setCookieHeaders(null); return false; }
    bearer = tokens.access; cookieOut = setCookieHeaders(tokens);
    return true;
  };
  try {
    if (expiring && !(await rotate())) return errorResponse(401, "UNAUTHENTICATED", "Your session has ended. Please sign in again.", cookieOut ?? setCookieHeaders(null), requestId);
  } catch { return errorResponse(502, "UPSTREAM_UNAVAILABLE", "The service is temporarily unavailable.", undefined, requestId); }

  const isStream = path.includes("/ai/chat/stream");
  const isUpload = (req.headers.get("content-type") ?? "").startsWith("multipart/");
  const hasBody = !["GET", "HEAD"].includes(req.method);
  const buffered = hasBody && !isUpload ? await req.arrayBuffer() : undefined;
  const timeout = isStream ? 5 * 60_000 : path.includes("/ai/") || path.includes("/ocr/") ? 90_000 : 30_000;

  const send = (tok: string | undefined) => fetch(`${backendUrl()}${path}`, {
    method: req.method, headers: upstreamHeaders(req, tok, requestId), cache: "no-store", redirect: "manual",
    body: hasBody ? (isUpload ? req.body : buffered) : undefined, // eslint-disable-line @typescript-eslint/no-explicit-any
    // @ts-expect-error Node's fetch needs duplex for streamed request bodies
    duplex: isUpload ? "half" : undefined,
    signal: AbortSignal.any([req.signal, AbortSignal.timeout(timeout)]),
  });

  let up: Response;
  try {
    up = await send(bearer);
    if (up.status === 401 && !tokens && refresh && !isUpload) { // access token was revoked/expired early: one transparent retry
      if (await rotate()) up = await send(bearer);
    }
  } catch (e) {
    if (req.signal.aborted) return new Response(null, { status: 499 });
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return errorResponse(timedOut ? 504 : 502, timedOut ? "UPSTREAM_TIMEOUT" : "UPSTREAM_UNAVAILABLE",
      timedOut ? "The request took too long." : "The service is temporarily unavailable.", cookieOut, requestId);
  }

  const h = new Headers(NO_STORE);
  for (const k of PASS) { const v = up.headers.get(k); if (v) h.set(k, v); }
  if (!h.has("x-request-id")) h.set("x-request-id", requestId);
  if ((h.get("content-type") ?? "").startsWith("text/event-stream")) h.set("cache-control", "no-store, no-transform");
  // A password change invalidates every token on the backend: end the browser session too.
  if (path.startsWith("/api/auth/change-password") && up.ok) cookieOut = setCookieHeaders(null);
  cookieOut?.forEach((c) => h.append("set-cookie", c));
  return new Response([204, 205, 304].includes(up.status) ? null : up.body, { status: up.status, headers: h });
}
