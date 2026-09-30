"""Shared HTTP plumbing: one pooled keep-alive client per provider, error classification, SSE line reader."""
import logging
from collections.abc import AsyncIterator

import httpx

from ..config import settings
from .base import ProviderError

log = logging.getLogger("buildguard.ai.http")


def classify(status: int, body: str, retry_after: str | None = None) -> ProviderError:
    ra = None
    if retry_after:
        try:
            ra = max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            ra = None
    low = body[:400].lower()
    if status in (401, 403):
        return ProviderError("auth", "provider rejected credentials", status=status)
    if status == 404:
        return ProviderError("model_unavailable", "model or endpoint not found", status=status)
    if status == 408:
        return ProviderError("timeout", "provider timed out", retryable=True, status=status)
    if status == 429:
        return ProviderError("rate_limit", "provider rate limit", retryable=True, status=status, retry_after=ra)
    if status == 413 or (status in (400, 422) and any(k in low for k in ("context length", "too long", "maximum context", "context_length", "too many tokens"))):
        return ProviderError("context_length", "request exceeds the model's context window", status=status)
    if status in (400, 422):
        return ProviderError("bad_request", "provider rejected the request", status=status)
    if status >= 500:
        return ProviderError("unavailable", f"provider error {status}", retryable=True, status=status, retry_after=ra)
    return ProviderError("bad_request", f"unexpected status {status}", status=status)


class HttpProvider:
    name = "http"
    model = ""

    def __init__(self, *, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, headers=self._headers(),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0),      # keep-alive reuse
                timeout=httpx.Timeout(connect=settings.ai_connect_timeout_ms / 1000, read=settings.ai_timeout_ms / 1000, write=10.0, pool=5.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def post_json(self, path: str, payload: dict) -> dict:
        try:
            r = await self.client.post(path, json=payload)
        except httpx.TimeoutException:
            raise ProviderError("timeout", "no response within the timeout", retryable=True) from None
        except httpx.TransportError as exc:
            raise ProviderError("network", type(exc).__name__, retryable=True) from None
        if r.status_code >= 400:
            raise classify(r.status_code, r.text, r.headers.get("retry-after"))
        try:
            data = r.json()
        except ValueError:
            raise ProviderError("invalid_response", "provider returned non-JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "provider returned an unexpected shape")
        return data

    async def stream_lines(self, path: str, payload: dict) -> AsyncIterator[str]:
        """Yields raw SSE lines. Cancelling the consumer closes the connection (client disconnect => upstream stops)."""
        got_bytes = False
        try:
            async with self.client.stream("POST", path, json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise classify(r.status_code, body, r.headers.get("retry-after"))
                async for line in r.aiter_lines():
                    got_bytes = True
                    yield line
        except httpx.TimeoutException:
            raise ProviderError("timeout" if not got_bytes else "stream_interrupted", "stream stalled", retryable=not got_bytes) from None
        except httpx.TransportError as exc:
            raise ProviderError("network" if not got_bytes else "stream_interrupted", type(exc).__name__, retryable=not got_bytes) from None
