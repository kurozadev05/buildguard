"""One error/response contract for the whole API.
success: {"success": true, "data": ..., "meta": {...}}
error:   {"success": false, "error": {"code", "message", "details", "request_id"}}"""
import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings

log = logging.getLogger("buildguard.errors")

_CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
          409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR",
          429: "RATE_LIMITED", 503: "SERVICE_UNAVAILABLE"}


def error_response(status: int, code: str, message: str, details=None, request_id: str | None = None, headers: dict | None = None) -> JSONResponse:
    body = {"success": False, "error": {"code": code, "message": message, "details": details or [], "request_id": request_id}}
    return JSONResponse(body, status_code=status, headers=headers)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code = getattr(exc, "code", None) or _CODES.get(exc.status_code, "HTTP_ERROR")
    request.state.error_code = code
    msg = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(exc.status_code, code, msg, request_id=_rid(request), headers=getattr(exc, "headers", None))


async def validation_handler(request: Request, exc: RequestValidationError):
    request.state.error_code = "VALIDATION_ERROR"
    # Drop pydantic's echoed `input` so submitted secrets (e.g. passwords) never come back in an error body.
    details = [{"field": ".".join(str(p) for p in e.get("loc", []) if p != "body"), "message": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return error_response(422, "VALIDATION_ERROR", "Invalid request", details, _rid(request))


async def data_error_handler(request: Request, exc: DataError):
    """The database refused a value the schema layer accepted (e.g. a NUL byte, which PostgreSQL cannot store in text). That is bad input, not a server fault."""
    request.state.error_code = "VALIDATION_ERROR"
    log.warning("database rejected input", extra={"fields": {"route": request.url.path, "method": request.method, "driver_error": type(getattr(exc, "orig", exc)).__name__}})
    return error_response(422, "VALIDATION_ERROR", "Some of the text contains characters that cannot be stored.",
                          [{"field": "", "message": "contains characters that cannot be stored (for example a NUL byte)", "type": "invalid_text"}], _rid(request))


def internal_error_response(request: Request, exc: Exception) -> JSONResponse:
    request.state.error_code = "INTERNAL_ERROR"
    log.error("unhandled exception", exc_info=exc, extra={"fields": {"route": request.url.path, "method": request.method}})
    details = [{"debug": f"{type(exc).__name__}: {exc}"}] if settings.env == "dev" else []
    return error_response(500, "INTERNAL_ERROR", "Internal server error", details, _rid(request))


class EnvelopeRoute(APIRoute):
    """Wraps successful JSON responses in the standard envelope (binary/HTML responses pass through untouched)."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            response = await original(request)
            if type(response) is JSONResponse and response.status_code < 400:
                meta = {}
                for k in ("limit", "offset"):
                    v = request.query_params.get(k)
                    if v and v.isdigit():
                        meta[k] = int(v)
                body = b'{"success":true,"data":' + response.body + b',"meta":' + json.dumps(meta).encode() + b"}"
                new = JSONResponse(None, status_code=response.status_code, background=response.background)
                new.body = body
                for k, v in response.headers.items():
                    if k.lower() not in ("content-length", "content-type"):
                        new.headers[k] = v
                new.headers["content-length"] = str(len(body))
                return new
            return response

        return handler
