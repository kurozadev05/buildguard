"""Reliability wrapper around any Provider: bounded retries with exponential backoff + jitter, a circuit breaker,
provider fallback, and an overall deadline. Retries only happen when nothing has been shown to the user yet,
and only for read-only generation (no tool side effects exist in this system), so a retry can never duplicate an action."""
import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing

from ..config import settings
from .base import FALLBACK_KINDS, GenRequest, GenResult, Provider, ProviderError, StreamEvent
from .metrics import metrics

log = logging.getLogger("buildguard.ai.provider")
_sleep = asyncio.sleep                                     # patched in tests


class Breaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0):
        self.threshold, self.cooldown, self.fails, self.open_until = threshold, cooldown_s, 0, 0.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until           # after the cooldown one trial request is let through (half-open)

    def ok(self) -> None:
        self.fails, self.open_until = 0, 0.0

    def fail(self) -> None:
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until, self.fails = time.monotonic() + self.cooldown, 0
            metrics.inc("breaker_opened")


def _delay(attempt: int, err: ProviderError) -> float:
    base = min(4.0, 0.4 * (2 ** attempt))
    return max(err.retry_after or 0.0, base) + random.random() * 0.25          # noqa: S311  (jitter, not security)


class ResilientProvider:
    def __init__(self, primary: Provider, fallback: Provider | None = None):
        self.chain = [p for p in (primary, fallback) if p is not None]
        self.breakers = {id(p): Breaker() for p in self.chain}
        self.embedder: Provider | None = None

    @property
    def name(self) -> str:
        return self.chain[0].name

    @property
    def model(self) -> str:
        return self.chain[0].model

    async def aclose(self) -> None:
        for p in self.chain + ([self.embedder] if self.embedder else []):
            await p.aclose()

    async def _run(self, prov: Provider, fn, *, deadline: float):
        """One provider, bounded retries. `fn(prov)` performs the call."""
        br = self.breakers[id(prov)]
        last: ProviderError | None = None
        for attempt in range(settings.ai_max_retries + 1):
            if not br.allow():
                raise ProviderError("unavailable", "circuit open", retryable=False)
            if time.monotonic() >= deadline:
                raise ProviderError("timeout", "overall deadline exceeded")
            try:
                out = await fn(prov)
                br.ok()
                return out
            except ProviderError as e:
                last = e
                metrics.inc(f"provider_errors.{e.kind}")
                if e.kind in FALLBACK_KINDS or e.kind == "invalid_response":
                    br.fail()
                if not e.retryable or attempt >= settings.ai_max_retries:
                    raise
                metrics.inc("retries")
                await _sleep(min(_delay(attempt, e), max(0.0, deadline - time.monotonic())))
        raise last or ProviderError("unavailable", "no attempt made")

    async def generate(self, req: GenRequest) -> GenResult:
        deadline = time.monotonic() + settings.ai_total_timeout_ms / 1000
        last: ProviderError | None = None
        for i, prov in enumerate(self.chain):
            try:
                t0 = time.perf_counter()
                res = await self._run(prov, lambda p: p.generate(req), deadline=deadline)
                metrics.observe("provider_latency", (time.perf_counter() - t0) * 1000)
                if i:
                    metrics.inc("fallback_used")
                return res
            except ProviderError as e:
                last = e
                if e.kind not in FALLBACK_KINDS and not (e.kind == "unavailable"):
                    raise
        assert last is not None
        raise last

    async def stream(self, req: GenRequest) -> AsyncGenerator[StreamEvent, None]:
        deadline = time.monotonic() + settings.ai_total_timeout_ms / 1000
        last: ProviderError | None = None
        for i, prov in enumerate(self.chain):
            br = self.breakers[id(prov)]
            for attempt in range(settings.ai_max_retries + 1):
                if not br.allow():
                    last = ProviderError("unavailable", "circuit open")
                    break
                started = False
                try:
                    async with aclosing(prov.stream(req)) as gen:
                        async for ev in gen:
                            started = started or ev.kind == "delta"
                            if time.monotonic() > deadline:
                                raise ProviderError("timeout", "overall deadline exceeded")
                            yield ev
                    br.ok()
                    if i:
                        metrics.inc("fallback_used")
                    return
                except ProviderError as e:
                    last = e
                    metrics.inc(f"provider_errors.{e.kind}")
                    if e.kind in FALLBACK_KINDS:
                        br.fail()
                    if started:                                   # user already saw partial text: never restart silently
                        raise ProviderError("stream_interrupted", e.message) from None
                    if not e.retryable or attempt >= settings.ai_max_retries:
                        break
                    metrics.inc("retries")
                    await _sleep(min(_delay(attempt, e), max(0.0, deadline - time.monotonic())))
            if last is not None and last.kind not in FALLBACK_KINDS and last.kind != "unavailable":
                raise last
        assert last is not None
        raise last

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.embedder is None:
            raise ProviderError("bad_request", "embeddings are not configured")
        deadline = time.monotonic() + settings.ai_total_timeout_ms / 1000
        self.breakers.setdefault(id(self.embedder), Breaker())
        return await self._run(self.embedder, lambda p: p.embed(texts), deadline=deadline)
