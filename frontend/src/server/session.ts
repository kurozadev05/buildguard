import "server-only";
import type { NextRequest } from "next/server";
import { backendUrl, cookieSecure } from "./env";

/** Tokens live ONLY in httpOnly, SameSite=Strict cookies. Browser JavaScript can never read them. */
export const AT = "bg_at";
export const RT = "bg_rt";
export interface Tokens { access: string; refresh: string; expiresAt: number }
export class UpstreamDown extends Error {}

export function jwtExpMs(token: string): number {
  try { return (JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString()).exp ?? 0) * 1000; } catch { return 0; }
}
export const readCookies = (req: NextRequest) => ({ access: req.cookies.get(AT)?.value, refresh: req.cookies.get(RT)?.value });

function cookie(name: string, value: string, maxAge: number): string {
  return `${name}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${Math.max(0, Math.floor(maxAge))}${cookieSecure() ? "; Secure" : ""}`;
}
export function setCookieHeaders(t: Tokens | null): string[] {
  if (!t) return [cookie(AT, "", 0), cookie(RT, "", 0)];
  return [cookie(AT, t.access, (t.expiresAt - Date.now()) / 1000), cookie(RT, t.refresh, 60 * 60 * 24 * 30)];
}

export function toTokens(d: { access_token: string; refresh_token: string; expires_at?: string }): Tokens {
  return { access: d.access_token, refresh: d.refresh_token, expiresAt: (d.expires_at ? Date.parse(d.expires_at) : 0) || jwtExpMs(d.access_token) };
}

// Refresh tokens rotate and the backend treats re-use as theft (it revokes the whole family).
// A page fires several requests at once, so: one refresh per token at a time, and remember the result briefly
// for requests that were already in flight carrying the old cookie.
const inflight = new Map<string, Promise<Tokens | null>>();
const recent = new Map<string, { t: Tokens | null; at: number }>();

async function doRefresh(rt: string): Promise<Tokens | null> {
  let r: Response;
  try {
    r = await fetch(`${backendUrl()}/api/auth/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: rt }), signal: AbortSignal.timeout(10_000), cache: "no-store" });
  } catch { throw new UpstreamDown(); }
  if (r.status >= 500) throw new UpstreamDown();
  if (!r.ok) return null; // invalid / expired / revoked: the session is over
  const j = await r.json();
  return toTokens(j.data ?? j);
}
export async function refreshTokens(rt: string): Promise<Tokens | null> {
  const hit = recent.get(rt);
  if (hit && Date.now() - hit.at < 60_000) return hit.t;
  let p = inflight.get(rt);
  if (!p) { p = doRefresh(rt).finally(() => inflight.delete(rt)); inflight.set(rt, p); }
  const t = await p;
  recent.set(rt, { t, at: Date.now() });
  if (recent.size > 500) for (const [k, v] of recent) if (Date.now() - v.at > 60_000) recent.delete(k);
  return t;
}
