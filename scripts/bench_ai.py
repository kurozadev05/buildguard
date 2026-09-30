"""Measures the AI layer's own overhead against a simulated provider with known latency (no real provider needed).
Everything runs over real sockets: app (uvicorn) <- httpx clients, app -> simulated provider server.
Usage: python scripts/bench_ai.py     Prints measured numbers only."""
import asyncio
import os
import sys
import tempfile
import threading
import time

_tmp = tempfile.mkdtemp()
os.environ.update({"DATABASE_URL": f"sqlite:///{_tmp}/bench.db", "UPLOAD_DIR": f"{_tmp}/up", "JWT_SECRET": "bench", "PASSWORD_ITERATIONS": "2000", "ENV": "test",
                   "SEED_DEMO": "false", "RATE_LIMIT_DEFAULT_PER_MIN": "1000000", "RATE_LIMIT_AI_PER_MIN": "1000000", "RATE_LIMIT_LOGIN_PER_MIN": "1000000",
                   "RATE_LIMIT_REGISTER_PER_MIN": "1000000", "AI_MAX_REQUESTS_PER_MINUTE": "1000000", "AI_DAILY_TOKEN_BUDGET": "0"})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_provider import Server, fake  # noqa: E402

PROVIDER_LAT = 0.30          # simulated provider time-to-first-byte (seconds)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * q))]


def line(name, vals_ms):
    print(f"  {name:<46} p50 {pct(vals_ms, .5):7.1f} ms   p95 {pct(vals_ms, .95):7.1f} ms   (n={len(vals_ms)})")


async def main():
    fake_url = Server().start()
    settings.ai_provider, settings.ai_api_key, settings.ai_base_url = "anthropic", "bench-key", fake_url + "/anthropic"
    settings.ai_model = "bench-model"
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
    limits = httpx.Limits(max_connections=300, keepalive_expiry=2.0)      # below uvicorn's 5 s keep-alive, so the client never reuses a connection the server already closed
    async with httpx.AsyncClient(base_url=base, timeout=60, limits=limits) as c:
        async def user(n):
            r = await c.post("/api/auth/register", json={"email": f"b{n}@bench.com", "full_name": "Bench User", "password": "BenchPass123!"})
            r = r.json()
            return {"Authorization": f"Bearer {(r.get('data') or r)['access_token']}"}
        h = await user(0)
        answer = "Take one sample for every 5 cubic metres of concrete [K1]."

        print(f"Simulated provider latency: {PROVIDER_LAT * 1000:.0f} ms (fixed), 1 worker, SQLite, no Redis\n")
        fake.default = {"text": answer, "delay": PROVIDER_LAT}

        print("1. Sequential requests (overhead = measured - simulated provider latency)")
        lat = []
        for i in range(40):
            t = time.perf_counter()
            r = await c.post("/api/ai/chat", json={"message": f"how many cube samples per m3, variant {i}"}, headers=h)
            assert r.status_code == 200, r.text
            lat.append((time.perf_counter() - t) * 1000)
        line("POST /ai/chat (retrieval+provider+persist)", lat)
        print(f"  => AI-layer overhead p50 {pct(lat, .5) - PROVIDER_LAT * 1000:.1f} ms, p95 {pct(lat, .95) - PROVIDER_LAT * 1000:.1f} ms")

        print("\n2. Streaming: time to first token seen by the client")
        ttft, total = [], []
        fake.default = {"text": "Take one sample for every five cubic metres of concrete placed on site [K1].", "delay": PROVIDER_LAT, "chunk_delay": 0.02}
        for i in range(25):
            t = time.perf_counter()
            first = None
            async with c.stream("POST", "/api/ai/chat/stream", json={"message": f"cube sampling frequency variant {i}"}, headers=h) as r:
                async for ln in r.aiter_lines():
                    if ln.startswith("event: delta") and first is None:
                        first = time.perf_counter() - t
            ttft.append(first * 1000)
            total.append((time.perf_counter() - t) * 1000)
        line("client time-to-first-token", ttft)
        print(f"  => relay overhead on first token p50 {pct(ttft, .5) - PROVIDER_LAT * 1000:.1f} ms")
        line("client total stream time", total)

        print("\n3. Cache: /ai/ask repeated public question")
        fake.default = {"text": answer, "delay": PROVIDER_LAT}
        t = time.perf_counter()
        r1 = await c.post("/api/ai/ask", json={"question": "cube sampling frequency per volume"}, headers=h)
        miss = (time.perf_counter() - t) * 1000
        hits = []
        for _ in range(30):
            t = time.perf_counter()
            r2 = await c.post("/api/ai/ask", json={"question": "cube sampling frequency per volume"}, headers=h)
            hits.append((time.perf_counter() - t) * 1000)
        assert r1.json()["data"]["cached"] is False and r2.json()["data"]["cached"] is True
        print(f"  miss {miss:.1f} ms;  hit p50 {pct(hits, .5):.1f} ms, p95 {pct(hits, .95):.1f} ms")

        for n in (20, 50, 100):
            print(f"\n4. {n} concurrent chat requests (distinct users), provider latency {PROVIDER_LAT * 1000:.0f} ms each")
            hs = [await user(1000 * n + i) for i in range(n)]
            fake.default = {"text": answer, "delay": PROVIDER_LAT}

            async def one(i):
                t = time.perf_counter()
                r = await c.post("/api/ai/chat", json={"message": f"cube sampling frequency user {i}"}, headers=hs[i])
                return r.status_code, (time.perf_counter() - t) * 1000
            t0 = time.perf_counter()
            res = await asyncio.gather(*[one(i) for i in range(n)])
            wall = time.perf_counter() - t0
            ok = [ms for s, ms in res if s == 200]
            print(f"  ok {len(ok)}/{n}, wall {wall:.2f} s (serial would be {n * PROVIDER_LAT:.0f} s), per-request p50 {pct(ok, .5):.0f} ms, p95 {pct(ok, .95):.0f} ms")

        n = 100
        print(f"\n5. {n} concurrent OPEN streams (each ~1.2 s), more than the 40-thread pool: proves streams don't hold threads")
        hs = [await user(9000 + i) for i in range(n)]
        fake.default = {"text": " ".join(["word"] * 12) + " [K1]", "delay": 0.05, "chunk_delay": 0.1}

        async def stream_one(i):
            t = time.perf_counter()
            done = False
            async with c.stream("POST", "/api/ai/chat/stream", json={"message": f"cube sampling stream {i}"}, headers=hs[i]) as r:
                async for ln in r.aiter_lines():
                    if ln.startswith("event: done"):
                        done = True
            return done, (time.perf_counter() - t)
        t0 = time.perf_counter()
        res = await asyncio.gather(*[stream_one(i) for i in range(n)])
        print(f"  completed {sum(d for d, _ in res)}/{n}, wall {time.perf_counter() - t0:.2f} s, slowest {max(t for _, t in res):.2f} s")

    from app.ai.metrics import metrics
    snap = metrics.snapshot()
    print("\n6. Server-side metrics (from /api/ai/metrics)")
    print("  retrieval ms:", snap["rag_retrieval_ms"], " request latency ms:", snap["latency_ms"], "\n  ttft ms:", snap["time_to_first_token_ms"],
          "\n  errors:", snap["counters"].get("errors", 0), "retries:", snap["counters"].get("retries", 0))

    print("\n7. Redis round-trip (local redis-server), if available")
    import shutil
    import subprocess
    if shutil.which("redis-server"):
        p = subprocess.Popen(["redis-server", "--port", "6391", "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(0.6)
        from app.ai.kv import RedisKV
        r = RedisKV("redis://127.0.0.1:6391/0")
        lat_i, lat_g = [], []
        for i in range(300):
            t = time.perf_counter(); await r.incr(f"k{i % 5}", 60); lat_i.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter(); await r.get(f"k{i % 5}"); lat_g.append((time.perf_counter() - t) * 1000)
        line("INCR (rate-limit counter)", lat_i)
        line("GET (cache lookup)", lat_g)
        p.terminate()
    os._exit(0)



if __name__ == "__main__":
    asyncio.run(main())
