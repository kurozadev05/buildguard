import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .ai import factory, rag
from .config import settings
from .database import SessionLocal, engine
from .errors import data_error_handler, error_response, http_exception_handler, validation_handler
from .logging_setup import setup_logging
from .middleware import RequestContextMiddleware
from .models import User
from .routers import (advisor, ai, audit, insights, auth, dashboard, durability, evidence, investigations, materials,
                      projects, public, sync)

setup_logging(settings.log_level, settings.log_format, settings.env)
log = logging.getLogger("buildguard.app")
ROOT = Path(__file__).resolve().parent.parent


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


async def check_database() -> str:
    def probe() -> str:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return "ok"
    try:
        return await run_in_threadpool(probe)
    except Exception:
        log.error("database check failed", exc_info=True)
        return "unavailable"


async def check_redis() -> str:
    """'not_configured' (in-memory mode) | 'ok' | 'unavailable'. Never raises, never returns the URL."""
    if not settings.redis_url:
        return "not_configured"
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
        try:
            await r.ping()
        finally:
            await r.aclose()
        return "ok"
    except Exception as exc:
        log.warning("redis check failed: %s", type(exc).__name__)
        return "unavailable"


def check_ai() -> str:
    """'not_configured' (rules + verified retrieval only) | 'configured' | 'misconfigured' (provider chosen but no key)."""
    if settings.ai_provider == "none":
        return "not_configured"
    if settings.ai_provider != "local" and not settings.ai_api_key:
        return "misconfigured"
    return "configured"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_migrate:
        run_migrations()
    os.makedirs(settings.upload_dir, exist_ok=True)
    with SessionLocal() as db:
        rag.sync_knowledge(db)                          # mirror the verified IS notes into the retrieval index (DB only, no network)
    if settings.seed_demo:
        with SessionLocal() as db:
            if (db.scalar(select(func.count(User.id))) or 0) == 0:
                from .services.seed import seed_demo
                seed_demo(db)
    db_state, rd, ai_state = await check_database(), await check_redis(), check_ai()
    log.info("startup checks: database=%s redis=%s ai=%s", db_state, rd, ai_state, extra={"fields": {"env": settings.env, "database": db_state, "redis": rd, "ai": ai_state}})
    if db_state != "ok":
        log.error("DATABASE UNAVAILABLE: check DATABASE_URL and that PostgreSQL is running. The API will answer 503 until it is reachable.")
    if rd == "unavailable":
        log.warning("REDIS UNAVAILABLE at the configured REDIS_URL: AI rate limits/caches fall back to in-memory for this process. Start Redis (see README) or clear REDIS_URL.")
    if ai_state == "misconfigured":
        log.warning("AI_PROVIDER=%s but AI_API_KEY is empty: AI features run in rules-and-retrieval mode. Set AI_API_KEY in .env.local.", settings.ai_provider)
    log.info("startup complete", extra={"fields": {"env": settings.env}})
    yield
    # Graceful shutdown: uvicorn stops accepting connections and drains in-flight requests first; then we release the pool.
    await factory.shutdown()                           # close pooled provider connections
    engine.dispose()
    log.info("shutdown complete")


app = FastAPI(
    title=settings.app_name, version="1.1.0", lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None, redoc_url=None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    description=("AI-assisted construction material testing, quality traceability and durability-risk API. "
                 "Decision support for engineers: it does not replace laboratory testing, IS codes or engineering judgement."),
)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)          # type: ignore[arg-type]  # Starlette's typing is narrower than its runtime
app.add_exception_handler(RequestValidationError, validation_handler)             # type: ignore[arg-type]
app.add_exception_handler(DataError, data_error_handler)                           # type: ignore[arg-type]

# Order matters: the last added is outermost. CORS wraps everything so even 429/413/500 responses carry CORS headers.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "Accept-Language", "X-Request-ID"],
                   expose_headers=["X-Request-ID", "Retry-After"], max_age=600)

for r in (auth.router, auth.raw_router, projects.router, materials.router, advisor.router, ai.router, evidence.router, investigations.router,
          durability.router, dashboard.router, sync.router, audit.router, insights.integrity_router, insights.predict_router, public.api):
    app.include_router(r, prefix="/api")
app.include_router(ai.stream_router, prefix="/api")
app.include_router(public.pages)


@app.get("/health", tags=["system"])
def health():
    """Liveness: the process is up. No dependency checks, no infrastructure details."""
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
async def ready():
    """Readiness for developers and orchestrators. Words only: no URLs, credentials, versions or hostnames.
    503 only when the database is down (nothing works without it); a Redis outage or missing AI key reports 'degraded' with 200."""
    db, rd, ai_state = await check_database(), await check_redis(), check_ai()
    checks = {"database": db, "redis": rd, "ai": ai_state}
    if db != "ok":
        return error_response(503, "SERVICE_UNAVAILABLE", "Not ready", details=[{"field": k, "message": v, "type": "check"} for k, v in checks.items()])
    degraded = rd == "unavailable" or ai_state == "misconfigured"
    return {"status": "degraded" if degraded else "ready", "checks": checks}


@app.get("/", tags=["system"], include_in_schema=False)
def root():
    return {"service": settings.app_name, "docs": "/docs" if settings.docs_enabled else None}
