"""Structured output: model JSON -> pydantic validation -> application. One bounded repair attempt, then give up (returns None)."""
import json
import logging
import time
from pydantic import BaseModel, ValidationError

from . import factory
from .base import GenRequest, Msg, ProviderError, Usage
from .metrics import metrics

log = logging.getLogger("buildguard.ai.structured")


def _extract_json(text: str) -> dict:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object found")
    v = json.loads(text[i:j + 1])
    if not isinstance(v, dict):
        raise ValueError("JSON is not an object")
    return v


async def generate_structured[T: BaseModel](schema: type[T], system: str, content, *, max_tokens: int = 500) -> tuple[T | None, Usage, str]:
    """Returns (validated object or None, usage, status). status: ok | repaired | invalid | provider_error | disabled."""
    prov = factory.get_provider()
    usage = Usage()
    if prov is None:
        return None, usage, "disabled"
    req = GenRequest(system=system, messages=[Msg("user", content)], max_tokens=max_tokens, temperature=0.0, json_mode=True)
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            res = await prov.generate(req)
        except ProviderError as e:
            log.warning("structured generation failed", extra={"fields": {"kind": e.kind}})
            return None, usage, "provider_error"
        metrics.observe("provider_latency", (time.perf_counter() - t0) * 1000)
        usage.input_tokens += res.usage.input_tokens
        usage.output_tokens += res.usage.output_tokens
        try:
            return schema.model_validate(_extract_json(res.text)), usage, "ok" if attempt == 0 else "repaired"
        except (ValueError, ValidationError) as exc:
            metrics.inc("structured_invalid")
            if attempt == 0:
                req.messages += [Msg("assistant", res.text[:1500]), Msg("user", f"That was not valid ({str(exc)[:160]}). Reply with ONLY the corrected JSON object.")]
    return None, usage, "invalid"
