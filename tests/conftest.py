import io
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db",
    "UPLOAD_DIR": f"{_tmp}/uploads",
    "JWT_SECRET": "test-secret",
    "PASSWORD_ITERATIONS": "2000",
    "RATE_LIMIT_DEFAULT_PER_MIN": "100000", "RATE_LIMIT_LOGIN_PER_MIN": "100000", "RATE_LIMIT_REGISTER_PER_MIN": "100000",
    "RATE_LIMIT_UPLOAD_PER_MIN": "100000", "RATE_LIMIT_AI_PER_MIN": "100000", "RATE_LIMIT_PUBLIC_PER_MIN": "100000",
    "SEED_DEMO": "false",
    "ENV": "test",
    # Isolation: automated tests must never reach a developer's Redis, a real AI provider or a real API key, even if these are exported in the shell.
    "REDIS_URL": "", "AI_PROVIDER": "none", "AI_API_KEY": "", "AI_MODEL": "", "AI_BASE_URL": "",
    "AI_FALLBACK_PROVIDER": "none", "AI_FALLBACK_API_KEY": "", "AI_EMBEDDING_PROVIDER": "none", "AI_EMBEDDING_API_KEY": "",
    "ADMIN_EMAIL": "", "TRUST_PROXY": "false",
})

import pytest
from fastapi.testclient import TestClient

from app.main import app


class Client(TestClient):
    """Unwraps the {"success": true, "data": ...} envelope so most tests read naturally.
    `resp.envelope()` returns the raw body for tests that assert the contract itself."""

    def request(self, *a, **k):
        r = super().request(*a, **k)
        raw = r.json

        def unwrapped(**kw):
            b = raw(**kw)
            return b["data"] if isinstance(b, dict) and b.get("success") is True and "data" in b else b

        r.envelope = raw
        r.json = unwrapped
        return r


def make_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def client():
    with Client(app) as c:
        yield c


# ───────────── fake AI provider (real HTTP server) ─────────────
@pytest.fixture(scope="session")
def fake_server():
    from tests.fake_provider import Server
    s = Server()
    url = s.start()
    yield url
    s.stop()


@pytest.fixture
def fake(fake_server, monkeypatch):
    """Point the AI layer at the fake server (Anthropic wire format by default); scripts via fake.push(...)."""
    from app.ai import factory, kv
    from app.ai import resilient
    from app.ai.metrics import metrics
    from app.config import settings
    from tests.fake_provider import fake as f
    f.reset()
    metrics.reset()
    kv._kv = None
    from app.ai import rag
    rag._embed_backoff_until = 0.0
    from app.ai import service as ai_service
    ai_service._inflight.clear()
    for k, v in {"ai_provider": "anthropic", "ai_api_key": "sk-test-SECRET-KEY-123", "ai_base_url": fake_server + "/anthropic", "ai_model": "fake-model",
                 "ai_max_retries": 2, "ai_timeout_ms": 3000, "ai_total_timeout_ms": 20000, "ai_fallback_provider": "none", "redis_url": "",
                 "ai_embedding_provider": "none", "ai_max_requests_per_minute": 1000, "ai_daily_token_budget": 0}.items():
        monkeypatch.setattr(settings, k, v)
    sleeps: list[float] = []

    async def no_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(resilient, "_sleep", no_sleep)
    factory._instance = None
    f.sleeps = sleeps
    yield f
    factory._instance = None
