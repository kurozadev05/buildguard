"""Request context: request-id, body-size cap, per-bucket rate limiting, security headers, JSON access log."""
import logging
import re
import time
import uuid
from collections import deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .errors import error_response, internal_error_response
from .logging_setup import request_id_var

access = logging.getLogger("buildguard.access")
_RID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def bucket_for(method: str, path: str) -> tuple[str | None, int]:
    """Different endpoints get different limits (per minute, per client IP)."""
    s = settings
    if path in ("/health", "/ready"):
        return None, 0
    if path.startswith("/api/auth/login") or path.startswith("/api/auth/token"):
        return "login", s.rate_limit_login_per_min
    if path.startswith("/api/auth/refresh"):
        return "refresh", s.rate_limit_default_per_min
    if path.startswith("/api/auth/register"):
        return "register", s.rate_limit_register_per_min
    if method == "POST" and (path.startswith("/api/documents") or path.startswith("/api/ocr")):
        return "upload", s.rate_limit_upload_per_min
    if path.startswith("/api/advisor") or path.startswith("/api/ai"):
        return "ai", s.rate_limit_ai_per_min
    if path.startswith("/p/") or path.startswith("/api/public"):
        return "public", s.rate_limit_public_per_min
    return "default", s.rate_limit_default_per_min


class RateLimiter:
    """Sliding window per (bucket, ip). In-memory: correct for one instance; use Redis if you scale out.
    Stale keys are pruned so memory stays bounded."""

    MAX_KEYS = 20000

    def __init__(self):
        self.hits: dict[str, deque] = {}
        self._n = 0

    def check(self, key: str, limit: int, now: float) -> int:
        """Return 0 if allowed, else seconds to wait."""
        self._n += 1
        if self._n % 500 == 0 or len(self.hits) > self.MAX_KEYS:
            self._prune(now)
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return max(1, int(60 - (now - q[0])) + 1)
        q.append(now)
        return 0

    def _prune(self, now: float):
        for k in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
            del self.hits[k]
        if len(self.hits) > self.MAX_KEYS:
            self.hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()      # rightmost = appended by our own proxy, not client-forgeable
    return request.client.host if request.client else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if _RID.match(rid) else uuid.uuid4().hex[:16]
        request.state.request_id = rid
        token = request_id_var.set(rid)
        start = time.perf_counter()
        method, path = request.method, request.url.path
        try:
            resp = self._guard(request, method, path, rid)
            if resp is None:
                resp = await call_next(request)
        except Exception as exc:
            resp = internal_error_response(request, exc)
        self._headers(request, resp, rid, path)
        route = getattr(request.scope.get("route"), "path", None) or ("(unmatched)" if resp.status_code == 404 else path)
        if path not in ("/health", "/ready"):
            access.info("request", extra={"fields": {
                "method": method, "route": route, "status": resp.status_code,
                "durationMs": round((time.perf_counter() - start) * 1000, 1),
                "errorCode": getattr(request.state, "error_code", None)}})
        request_id_var.reset(token)
        return resp

    def _guard(self, request: Request, method: str, path: str, rid: str):
        # body size cap (declared length; uploads get their own larger cap)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit():
            cap = (settings.max_upload_mb + 1) * 1024 * 1024 if method == "POST" and (path.startswith("/api/documents") or path.startswith("/api/ocr")) \
                else settings.max_json_body_kb * 1024
            if int(cl) > cap:
                request.state.error_code = "PAYLOAD_TOO_LARGE"
                return error_response(413, "PAYLOAD_TOO_LARGE", "Request body too large", request_id=rid)
        if method == "OPTIONS":
            return None
        bucket, limit = bucket_for(method, path)
        if bucket:
            wait = limiter.check(f"{bucket}:{client_ip(request)}", limit, time.monotonic())
            if wait:
                request.state.error_code = "RATE_LIMITED"
                return error_response(429, "RATE_LIMITED", "Too many requests", request_id=rid, headers={"Retry-After": str(wait)})
        return None

    @staticmethod
    def _headers(request: Request, resp, rid: str, path: str):
        h = resp.headers
        h["X-Request-ID"] = rid
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Frame-Options"] = "DENY"
        h["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
        h["Cross-Origin-Resource-Policy"] = "same-site"
        if settings.strict and (request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"):
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path.startswith("/api"):
            h["Cache-Control"] = "no-store"
            h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        elif path.startswith("/p/"):
            h["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        # /docs (Swagger) loads scripts from a CDN, so no CSP is forced on it.
