"""In-process AI metrics (no extra infrastructure). Exposed to admins at /api/ai/metrics; per-request data also goes to ai_usage."""
import threading
from collections import Counter, deque


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.c: Counter = Counter()
        self.latency: deque = deque(maxlen=500)
        self.ttft: deque = deque(maxlen=500)
        self.retrieval: deque = deque(maxlen=500)
        self.provider_latency: deque = deque(maxlen=500)

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.c[name] += n

    def observe(self, series: str, ms: float) -> None:
        with self._lock:
            getattr(self, series).append(ms)

    def reset(self) -> None:
        with self._lock:
            self.c.clear()
            for d in (self.latency, self.ttft, self.retrieval, self.provider_latency):
                d.clear()

    def snapshot(self) -> dict:
        with self._lock:
            c = dict(self.c)
            req = c.get("requests", 0)
            return {"counters": c,
                    "error_rate": round(c.get("errors", 0) / req, 4) if req else 0.0,
                    "timeout_rate": round(c.get("provider_errors.timeout", 0) / req, 4) if req else 0.0,
                    "cache_hit_rate": round(c.get("cache_hit", 0) / max(1, c.get("cache_hit", 0) + c.get("cache_miss", 0)), 4),
                    "latency_ms": {"p50": _pct(list(self.latency), 0.5), "p95": _pct(list(self.latency), 0.95)},
                    "provider_latency_ms": {"p50": _pct(list(self.provider_latency), 0.5), "p95": _pct(list(self.provider_latency), 0.95)},
                    "time_to_first_token_ms": {"p50": _pct(list(self.ttft), 0.5), "p95": _pct(list(self.ttft), 0.95)},
                    "rag_retrieval_ms": {"p50": _pct(list(self.retrieval), 0.5), "p95": _pct(list(self.retrieval), 0.95)}}


metrics = Metrics()
