"""Rate-limit / cache store: in-memory semantics, real Redis semantics, cross-worker sharing, and Redis outage behaviour."""
import asyncio
import shutil
import socket
import subprocess
import time

import pytest

from app.ai import kv
from app.config import settings

pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server not installed")


@pytest.fixture(scope="module")
def redis_url(tmp_path_factory):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(tmp_path_factory.mktemp("redis"))],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"redis://127.0.0.1:{port}/0"
    p.terminate()
    p.wait(5)


async def test_memory_kv_ttl_and_incr_window():
    m = kv.MemoryKV()
    assert await m.incr("c", 1) == 1 and await m.incr("c", 1) == 2
    await m.set("k", "v", 1)
    assert await m.get("k") == "v"
    await asyncio.sleep(1.1)
    assert await m.get("k") is None and await m.get("c") is None                 # expired; a new window starts at 1
    assert await m.incr("c", 1) == 1


async def test_redis_kv_semantics_and_fixed_window(redis_url):
    r = kv.RedisKV(redis_url)
    await r.r.flushdb()
    assert await r.ping() is True
    assert [await r.incr("w", 5) for _ in range(3)] == [1, 2, 3]
    ttl1 = await r.r.ttl(kv.NS + "w")
    await asyncio.sleep(1.1)
    await r.incr("w", 5)
    assert await r.r.ttl(kv.NS + "w") < ttl1                                       # TTL is set once (NX): the window does not slide forever
    await r.set("k", "v", 1)
    assert await r.get("k") == "v"
    await asyncio.sleep(1.2)
    assert await r.get("k") is None
    assert await r.incr("tok", 60, by=42) == 42


async def test_two_workers_share_counters_and_cache(redis_url):
    w1, w2 = kv.RedisKV(redis_url), kv.RedisKV(redis_url)                          # independent clients == independent server processes
    await w1.r.flushdb()
    assert await w1.incr("rl:user", 60) == 1
    assert await w2.incr("rl:user", 60) == 2
    await w1.set("ask:x", "answer", 60)
    assert await w2.get("ask:x") == "answer"


async def test_redis_outage_degrades_instead_of_failing():
    dead = kv.RedisKV("redis://127.0.0.1:1/0")                                     # nothing listens here
    assert await dead.ping() is False
    assert await dead.incr("a", 60) == 1 and await dead.incr("a", 60) == 2         # falls back to in-memory for the call
    await dead.set("k", "v", 60)
    assert await dead.get("k") == "v"


def test_api_limits_and_cache_are_shared_across_workers_via_redis(client, fake, redis_url, monkeypatch):
    pw = "Passw0rd!xx"
    tok = client.post("/api/auth/register", json={"email": "kv.user@x.com", "full_name": "KV", "password": pw}).json().get("access_token") \
        or client.post("/api/auth/login", json={"email": "kv.user@x.com", "password": pw}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "ai_max_requests_per_minute", 3)
    kv._kv = None
    import redis
    redis.Redis.from_url(redis_url).flushdb()
    fake.default = {"text": "Cores are acceptable at 85 percent of fck [K8]."}
    q = {"question": "core test acceptance?"}
    assert client.post("/api/ai/ask", json=q, headers=h).json()["cached"] is False
    kv._kv = None                                                                  # "another worker process": brand-new client, same Redis
    r = client.post("/api/ai/ask", json=q, headers=h).json()
    assert r["cached"] is True and len(fake.calls) == 1
    kv._kv = None
    client.post("/api/ai/ask", json=q, headers=h)
    kv._kv = None
    assert client.post("/api/ai/ask", json=q, headers=h).status_code == 429       # 4th request in the minute, counted across all three "workers"
    kv._kv = None


def test_api_keeps_working_when_redis_is_down(client, fake, monkeypatch):
    pw = "Passw0rd!xx"
    tok = client.post("/api/auth/login", json={"email": "kv.user@x.com", "password": pw}).json()["access_token"]
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(settings, "ai_max_requests_per_minute", 100)
    kv._kv = None
    fake.default = {"text": "Slump ranges depend on the placing condition [K6]."}
    r = client.post("/api/ai/ask", json={"question": "slump range?"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["generated_by"] == "llm"
    kv._kv = None
