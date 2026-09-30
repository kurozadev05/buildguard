import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/register", "/offline"];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const hasSession = req.cookies.has("bg_rt") || req.cookies.has("bg_at");      // presence only: the backend decides validity
  const isPublic = PUBLIC.some((p) => pathname === p);
  if (!isPublic && !hasSession) {
    const u = req.nextUrl.clone();
    u.pathname = "/login";
    u.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(u);
  }
  if (hasSession && (pathname === "/login" || pathname === "/register")) {
    const u = req.nextUrl.clone(); u.pathname = "/"; u.search = "";
    return NextResponse.redirect(u);
  }
  const nonce = btoa(crypto.randomUUID());
  const dev = process.env.NODE_ENV !== "production";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",           // React inline style attributes; no remote styles
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",                         // the browser may only talk to this origin: never to the backend or any AI provider
    "worker-src 'self'", "manifest-src 'self'", "media-src 'self' blob:",
    "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
  ].join("; ");
  const headers = new Headers(req.headers);
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", csp);
  const res = NextResponse.next({ request: { headers } });
  res.headers.set("Content-Security-Policy", csp);
  return res;
}

export const config = {
  matcher: [{ source: "/((?!bff|auth|healthz|p/|_next/static|_next/image|icons|favicon.ico|sw.js|manifest.webmanifest).*)", missing: [{ type: "header", key: "next-router-prefetch" }, { type: "header", key: "purpose", value: "prefetch" }] }],
};
