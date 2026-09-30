"""Tiny key-value store for rate limits, token budgets, response cache and query-embedding cache.
In-memory by default (correct for one process); Redis when REDIS_URL is set (correct across workers/instances).
Redis outages degrade to the in-memory store for that call so AI stays available; the event is logged."""
import logging
import time

from ..config import settings

log = logging.getLogger("buildguard.ai.kv")
NS = "bg:ai:"


class MemoryKV:
    def __init__(self):
        self.d: dict[str, tuple[str, float]] = {}
        self._n = 0

    def _gc(self):
        self._n += 1
        if self._n % 200 == 0 or len(self.d) > 50000:
            now = time.monotonic()
            for k in [k for k, (_, e) in self.d.items() if e <= now]:
                del self.d[k]
            if len(self.d) > 50000:
                self.d.clear()

    async def get(self, key: str) -> str | None:
        v = self.d.get(key)
        if not v or v[1] <= time.monotonic():
            self.d.pop(key, None)
            return None
        return v[0]

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._gc()
        self.d[key] = (value, time.monotonic() + ttl)

    async def incr(self, key: str, ttl: int, by: int = 1) -> int:
        self._gc()
        cur = await self.get(key)
        exp = self.d[key][1] if cur is not None else time.monotonic() + ttl
        n = int(cur or 0) + by
        self.d[key] = (str(n), exp)
        return n

    async def delete(self, key: str) -> None:
        self.d.pop(key, None)

    async def ping(self) -> bool:
        return True


class RedisKV:
    def __init__(self, url: str):
        import redis.asyncio as aioredis
        self.r = aioredis.from_url(url, decode_responses=True, socket_timeout=1.0, socket_connect_timeout=1.0)
        self.fallback = MemoryKV()

    async def _try(self, name: str, coro_fn, fb_fn):
        try:
            return await coro_fn()
        except Exception as exc:                                  # connection refused, timeout, etc.
            log.warning("redis unavailable (%s); using in-memory for this call", type(exc).__name__, extra={"fields": {"op": name}})
            return await fb_fn()

    async def get(self, key: str) -> str | None:
        return await self._try("get", lambda: self.r.get(NS + key), lambda: self.fallback.get(key))

    async def set(self, key: str, value: str, ttl: int) -> None:
        await self._try("set", lambda: self.r.set(NS + key, value, ex=ttl), lambda: self.fallback.set(key, value, ttl))

    async def incr(self, key: str, ttl: int, by: int = 1) -> int:
        async def go():
            p = self.r.pipeline()
            p.set(NS + key, 0, ex=ttl, nx=True)                # creates the window once; works on any Redis version (no EXPIRE NX needed)
            p.incrby(NS + key, by)
            return int((await p.execute())[1])
        return await self._try("incr", go, lambda: self.fallback.incr(key, ttl, by))

    async def delete(self, key: str) -> None:
        await self._try("del", lambda: self.r.delete(NS + key), lambda: self.fallback.delete(key))

    async def ping(self) -> bool:
        try:
            return bool(await self.r.ping())
        except Exception:
            return False


_kv: MemoryKV | RedisKV | None = None
_kv_url: str | None = None


def get_kv() -> MemoryKV | RedisKV:
    global _kv, _kv_url
    if _kv is None or _kv_url != settings.redis_url:
        _kv, _kv_url = (RedisKV(settings.redis_url) if settings.redis_url else MemoryKV()), settings.redis_url
    return _kv
