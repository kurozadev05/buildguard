"""AI service: the only place that turns a user request into provider calls.
Routers -> this module -> provider abstraction. Business code never touches a vendor SDK or wire format."""
import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime

import anyio
from fastapi import HTTPException

from ..config import settings
from ..deps import accessible_project_ids, ensure_access
from ..i18n import status_label
from ..models import Batch, TestRecord, User
from ..services import core
from . import factory, grounding, kv, prompts, rag, store, tools
from .base import GenRequest, GenResult, Msg, ProviderError, StreamEvent, Usage
from .dbutil import in_db, in_db_write
from .metrics import metrics
from .tokens import estimate_tokens, fit_history

log = logging.getLogger("buildguard.ai")
MAX_TOOL_CALLS = 6


class AIError(HTTPException):
    def __init__(self, code: str, message: str, status: int = 503, retry_after: int | None = None):
        super().__init__(status, message, headers={"Retry-After": str(retry_after)} if retry_after else None)
        self.code = code


@dataclass
class Caller:
    user: User
    request_id: str
    lang: str = "en"


def map_error(e: ProviderError) -> AIError:
    """Provider failures become stable, non-revealing API errors (no vendor, key or endpoint details)."""
    table = {"timeout": ("AI_TIMEOUT", 504, "The AI service took too long to respond."),
             "network": ("AI_UNAVAILABLE", 503, "The AI service is temporarily unavailable."),
             "unavailable": ("AI_UNAVAILABLE", 503, "The AI service is temporarily unavailable."),
             "rate_limit": ("AI_BUSY", 503, "The AI service is busy. Try again shortly."),
             "auth": ("AI_MISCONFIGURED", 503, "The AI service is not configured correctly. Contact the administrator."),
             "model_unavailable": ("AI_MISCONFIGURED", 503, "The AI service is not configured correctly. Contact the administrator."),
             "context_length": ("AI_INPUT_TOO_LARGE", 413, "The request is too large for the AI model."),
             "bad_request": ("AI_BAD_REQUEST", 502, "The AI service rejected the request."),
             "invalid_response": ("AI_BAD_RESPONSE", 502, "The AI service returned an unusable response."),
             "stream_interrupted": ("AI_STREAM_INTERRUPTED", 502, "The AI response was interrupted.")}
    code, status, msg = table.get(e.kind, ("AI_UNAVAILABLE", 503, "The AI service failed."))
    return AIError(code, msg, status, retry_after=5 if e.kind in ("rate_limit", "unavailable", "network") else None)


# ───────────── cost / rate guards ─────────────
async def enforce_limits(user_id: str) -> None:
    store_ = kv.get_kv()
    minute = int(time.time() // 60)
    n = await store_.incr(f"rl:{user_id}:{minute}", 70)
    if n > settings.ai_max_requests_per_minute:
        metrics.inc("rejected_rate")
        raise AIError("AI_RATE_LIMITED", "Too many AI requests. Please slow down.", 429, retry_after=60 - int(time.time() % 60))
    if settings.ai_daily_token_budget:
        used = int(await store_.get(f"tok:{user_id}:{datetime.now(UTC).date()}") or 0)
        if used >= settings.ai_daily_token_budget:
            metrics.inc("rejected_budget")
            raise AIError("AI_BUDGET_EXCEEDED", "Daily AI usage limit reached. It resets at 00:00 UTC.", 429, retry_after=3600)


async def _charge(user_id: str, tokens: int) -> None:
    if tokens:
        await kv.get_kv().incr(f"tok:{user_id}:{datetime.now(UTC).date()}", 90000, by=tokens)


def _cost(u: Usage) -> float:
    return round(u.input_tokens / 1000 * settings.ai_price_in_per_1k + u.output_tokens / 1000 * settings.ai_price_out_per_1k, 6)


async def _record(caller: Caller, endpoint: str, usage: Usage, latency_ms: float, ttft_ms: float | None, status: str, error_code: str | None = None, cached: bool = False) -> None:
    prov = factory.get_provider()
    with anyio.CancelScope(shield=True):                       # must complete even if the client just disconnected
        await in_db_write(store.record_usage, request_id=caller.request_id, user_id=caller.user.id, endpoint=endpoint,
                    provider=prov.name if prov else "none", model=prov.model if prov else "none", prompt_version=prompts.PROMPT_VERSION,
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, latency_ms=int(latency_ms),
                    ttft_ms=int(ttft_ms) if ttft_ms is not None else None, cached=cached, status=status, error_code=error_code, estimated_cost=_cost(usage))
        await _charge(caller.user.id, usage.input_tokens + usage.output_tokens)
    metrics.observe("latency", latency_ms)
    if ttft_ms is not None:
        metrics.observe("ttft", ttft_ms)
    metrics.inc("tokens_in", usage.input_tokens)
    metrics.inc("tokens_out", usage.output_tokens)
    if status == "error":
        metrics.inc("errors")
    log.info("ai request", extra={"fields": {"endpoint": endpoint, "status": status, "errorCode": error_code, "inputTokens": usage.input_tokens,
                                             "outputTokens": usage.output_tokens, "latencyMs": int(latency_ms), "ttftMs": int(ttft_ms) if ttft_ms is not None else None,
                                             "cached": cached}})


async def _turn(prov, req: GenRequest, want_stream: bool) -> AsyncGenerator[StreamEvent, None]:
    if want_stream:
        async with aclosing(prov.stream(req)) as g:
            async for ev in g:
                yield ev
    else:
        res = await prov.generate(req)                 # same event shape as streaming: one delta with the full text, then done
        if res.text:
            yield StreamEvent("delta", res.text)
        yield StreamEvent("done", result=res)


def _public_sources(sources: list[dict]) -> list[dict]:
    return [{"label": s["label"], "title": s["title"], "reference": s["reference"], "source": s["source"]} for s in sources]


# ───────────── conversational chat (streaming or not) ─────────────
async def chat_events(caller: Caller, message: str, conversation_id: str | None, project_id: str | None, *, stream: bool) -> AsyncGenerator[tuple[str, dict], None]:
    """Yields (event, data): meta, delta, tool, replace, done. Raises AIError/HTTPException for hard failures."""
    t0 = time.perf_counter()
    metrics.inc("requests")
    user, lang = caller.user, caller.lang
    message = grounding.clean_text(message, settings.ai_max_input_chars)
    await enforce_limits(user.id)
    pids = await in_db(accessible_project_ids, user)
    if project_id and project_id not in pids:
        raise AIError("FORBIDDEN", "You are not a member of this project.", 403)
    conv_id, history = await in_db_write(store.start_turn, user.id, conversation_id, project_id, message)
    yield "meta", {"conversation_id": conv_id}

    sources = await rag.search(message, pids, project_id=project_id, top_k=4, token_budget=1200)
    prov = factory.get_provider()
    fallback = grounding.fallback_answer(sources, lang)
    system = prompts.system_prompt(lang)
    user_msg = prompts.user_turn(message, sources)
    budget = settings.ai_max_context_tokens - estimate_tokens(system) - estimate_tokens(user_msg) - settings.ai_max_tokens
    hist = fit_history(history, max(0, budget))
    msgs = [Msg(h["role"], h["content"]) for h in hist] + [Msg("user", user_msg)]

    parts: list[str] = []
    usage = Usage()
    tool_texts: list[str] = []
    tools_used: list[dict] = []
    status, error_code, ttft, generated_by = "ok", None, None, "llm"
    persisted, interrupted, degraded, validated = False, None, False, True
    pending_error: AIError | None = None
    msg_id = None
    try:
        if prov is None:
            generated_by, degraded = "retrieval", True
            parts.append(fallback)
            yield "delta", {"text": fallback}
        else:
            rounds = calls_total = 0
            try:
                while True:
                    req = GenRequest(system=system, messages=msgs, max_tokens=settings.ai_max_tokens, temperature=settings.ai_temperature,
                                     tools=tools.specs() if rounds < settings.ai_max_tool_rounds else [])
                    final: GenResult | None = None
                    async for ev in _turn(prov, req, stream):
                        if ev.kind == "delta":
                            if ttft is None:
                                ttft = (time.perf_counter() - t0) * 1000
                            parts.append(ev.text)
                            yield "delta", {"text": ev.text}
                        else:
                            final = ev.result
                    assert final is not None
                    usage.input_tokens += final.usage.input_tokens
                    usage.output_tokens += final.usage.output_tokens
                    if not final.tool_calls or rounds >= settings.ai_max_tool_rounds or calls_total + len(final.tool_calls) > MAX_TOOL_CALLS:
                        break          # bounded: a provider that keeps asking for tools is cut off and the answer is validated as-is
                    rounds += 1
                    msgs.append(Msg("assistant", final.text, tool_calls=final.tool_calls))
                    for c in final.tool_calls:
                        calls_total += 1
                        payload, info = await tools.run_tool(c.name, c.arguments, user)
                        tools_used.append(info)
                        tool_texts.append(payload)
                        msgs.append(Msg("tool", prompts.tool_result_block(c.name, payload), tool_call_id=c.id))
                        yield "tool", {"name": c.name, "ok": info["ok"]}
            except ProviderError as e:
                error_code = map_error(e).code
                if parts and e.kind == "stream_interrupted":
                    interrupted, status = "provider_stream_interrupted", "error"
                    pending_error = map_error(e)
                elif sources:
                    degraded, status, generated_by = True, "fallback", "retrieval"
                    parts.clear()
                    parts.append(fallback)
                    yield "replace", {"text": fallback}
                else:
                    status = "error"
                    pending_error = map_error(e)

            answer = "".join(parts)
            if pending_error is None and generated_by == "llm":
                src_text = prompts.fence_sources(sources) + " ".join(tool_texts) + " " + message + " " + " ".join(h["content"] for h in hist)
                ok = grounding.validate_answer(answer, {s["label"] for s in sources}, src_text, require_citation=bool(sources) and not tools_used)
                if ok is None:
                    validated, generated_by, status = False, "retrieval", "fallback"
                    metrics.inc("validation_rejected")
                    answer = fallback
                    parts[:] = [fallback]
                    yield "replace", {"text": fallback}
        answer = "".join(parts)
        meta = {"sources": _public_sources(sources), "tools": [t["tool"] for t in tools_used], "validated": validated, "generated_by": generated_by,
                "degraded": degraded, "prompt_version": prompts.PROMPT_VERSION, **({"interrupted": interrupted} if interrupted else {})}
        with anyio.CancelScope(shield=True):
            msg_id = await in_db_write(store.add_assistant, conv_id, answer, meta)
        persisted = True
        await _record(caller, "chat", usage, (time.perf_counter() - t0) * 1000, ttft, status, error_code)
        if pending_error:
            raise pending_error
        yield "done", {"message_id": msg_id, "conversation_id": conv_id, "generated_by": generated_by, "validated": validated, "degraded": degraded,
                       "sources": _public_sources(sources), "tools": [t["tool"] for t in tools_used], "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}}
    finally:
        if not persisted:                                   # client disconnected / crash: keep what the user saw, and account for it
            with anyio.CancelScope(shield=True):
                await in_db_write(store.add_assistant, conv_id, "".join(parts), {"interrupted": "client_disconnect", "validated": False, "sources": [], "tools": []})
                await _record(caller, "chat", usage, (time.perf_counter() - t0) * 1000, ttft, "error", "CLIENT_DISCONNECT")


async def chat(caller: Caller, message: str, conversation_id: str | None, project_id: str | None) -> dict:
    out: dict = {"text": ""}
    async for kind, data in chat_events(caller, message, conversation_id, project_id, stream=False):
        if kind == "delta":
            out["text"] += data["text"]
        elif kind == "replace":
            out["text"] = data["text"]
        elif kind in ("meta", "done"):
            out.update(data)
    return out


# ───────────── one-shot grounded Q&A (cached, single-flight) ─────────────
_inflight: dict[str, asyncio.Future] = {}


def _ctx_for_batch(db, user, batch_id: str) -> str:
    b = core.get_or_404(db, Batch, batch_id, "Batch")
    ensure_access(db, user, b.project_id)
    bad = [o["explanation"] for o in (b.registration_check or {}).get("outcomes", []) if o["status"] != "VERIFIED"]
    return grounding.one_line(f"Batch grade {b.grade}, exposure {b.exposure}, status {b.status}. " + " ".join(bad), 600)


async def ask(caller: Caller, question: str, batch_id: str | None = None, project_id: str | None = None) -> dict:
    t0 = time.perf_counter()
    metrics.inc("requests")
    user, lang = caller.user, caller.lang
    q = grounding.clean_text(question, settings.ai_max_input_chars)
    await enforce_limits(user.id)
    pids = await in_db(accessible_project_ids, user)
    if project_id and project_id not in pids:
        raise AIError("FORBIDDEN", "You are not a member of this project.", 403)
    context = await in_db(_ctx_for_batch, user, batch_id) if batch_id else ""
    sources = await rag.search(q, pids, project_id=project_id, top_k=4, token_budget=1200)
    prov = factory.get_provider()
    if not sources:
        return {"answer": grounding.fallback_answer([], lang), "sources": [], "generated_by": "retrieval", "grounded": True, "cached": False}
    fallback = grounding.fallback_answer(sources, lang)
    base = {"sources": _public_sources(sources), "grounded": True, "cached": False}
    if prov is None:
        return {**base, "answer": fallback, "generated_by": "retrieval"}

    shareable = not context and all(s["source"] == "knowledge" for s in sources)         # never share answers built from tenant data
    key = None
    if shareable:
        labels = ",".join(s["label"] for s in sources)
        key = f"ask:{prompts.PROMPT_VERSION}:{prov.model}:{lang}:{hashlib.sha256((q.lower() + labels).encode()).hexdigest()}"
        cached = await kv.get_kv().get(key)
        if cached:
            metrics.inc("cache_hit")
            await _record(caller, "ask", Usage(), (time.perf_counter() - t0) * 1000, None, "ok", cached=True)
            return {**base, **json.loads(cached), "cached": True}
        metrics.inc("cache_miss")

    async def compute() -> dict:
        req = GenRequest(system=prompts.system_prompt(lang), messages=[Msg("user", prompts.user_turn(q, sources, context))],
                         max_tokens=min(400, settings.ai_max_tokens), temperature=settings.ai_temperature)
        usage, status, err = Usage(), "ok", None
        out: dict = {}
        try:
            res = await prov.generate(req)
            usage = res.usage
            ok = grounding.validate_answer(res.text, {s["label"] for s in sources}, prompts.fence_sources(sources) + " " + context + " " + q, require_citation=True)
            if ok is None:
                metrics.inc("validation_rejected")
                status, out = "fallback", {"answer": fallback, "generated_by": "retrieval", "note": "AI summary failed validation; showing verified source text."}
            else:
                out = {"answer": ok, "generated_by": "llm"}
        except ProviderError as e:
            err, status = map_error(e).code, "fallback"
            out = {"answer": fallback, "generated_by": "retrieval", "degraded": True, "note": "AI temporarily unavailable; showing verified source text."}
        await _record(caller, "ask", usage, (time.perf_counter() - t0) * 1000, None, status, err)
        if key and out["generated_by"] == "llm":
            await kv.get_kv().set(key, json.dumps(out), settings.ai_cache_ttl_s)
        return out

    if key:                                                                                   # request de-duplication: identical concurrent questions share one LLM call
        if key in _inflight:
            metrics.inc("dedup_hit")
            return {**base, **(await asyncio.shield(_inflight[key]))}
        fut = asyncio.get_running_loop().create_future()
        _inflight[key] = fut
        try:
            out = await compute()
            fut.set_result(out)
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()                                                                   # mark retrieved so asyncio does not warn if nobody else awaited it
            raise
        finally:
            _inflight.pop(key, None)
        return {**base, **out}
    return {**base, **(await compute())}


# ───────────── explanations of stored results ─────────────
_NEXT = {
    "FLAGGED": ("Hold further use of this batch, tell the responsible engineer, and follow the investigation steps (NDT, then cores if doubtful).",
                "इस बैच का उपयोग रोकें, ज़िम्मेदार इंजीनियर को बताएँ और जाँच के चरण (NDT, फिर आवश्यकता होने पर कोर) अपनाएँ।"),
    "REVIEW_REQUIRED": ("An engineer should review this result before relying on it; retest or record the missing data.",
                        "इस परिणाम पर भरोसा करने से पहले इंजीनियर समीक्षा करें; दोबारा परीक्षण करें या छूटा डेटा दर्ज करें।"),
    "VERIFIED": ("No action needed for this result. Keep the evidence photo and report attached.",
                 "इस परिणाम के लिए कोई कार्रवाई आवश्यक नहीं। साक्ष्य फ़ोटो और रिपोर्ट संलग्न रखें।"),
}


def _load_test(db, user, test_id: str) -> dict:
    t = core.get_or_404(db, TestRecord, test_id, "Test")
    ensure_access(db, user, t.project_id)
    b = core.get_or_404(db, Batch, t.batch_id, "Batch")
    from ..i18n import test_label
    return {"status": core.effective_status(t), "label": test_label(t.test_type, "en") + (f" ({t.age_days}-day)" if t.age_days else ""),
            "outcomes": t.validation or [], "grade": b.grade}


async def explain_test(caller: Caller, test_id: str) -> dict:
    t0 = time.perf_counter()
    metrics.inc("requests")
    lang = caller.lang
    await enforce_limits(caller.user.id)
    d = await in_db(_load_test, caller.user, test_id)
    facts = "\n".join(f"- {o['title']} ({o['reference']}): required {o['requirement']}; observed {o['observed']}; {o['explanation']}" for o in d["outcomes"])
    src = f"Test: {d['label']}. Grade: {d['grade']}. Result: {d['status']}.\n{facts}"
    nxt = _NEXT.get(d["status"], _NEXT["REVIEW_REQUIRED"])[1 if lang == "hi" else 0]
    fallback = f"{status_label(d['status'], lang)}: " + " ".join(o["explanation"] for o in d["outcomes"]) + " " + nxt
    out: dict = {"status": d["status"], "explanation": fallback, "generated_by": "template"}
    prov = factory.get_provider()
    if prov is None:
        return out
    key = f"explain:{prompts.PROMPT_VERSION}:{prov.model}:{lang}:{hashlib.sha256(src.encode()).hexdigest()}"     # src has no tenant identifiers => safe to share
    hit = await kv.get_kv().get(key)
    if hit:
        metrics.inc("cache_hit")
        await _record(caller, "explain", Usage(), (time.perf_counter() - t0) * 1000, None, "ok", cached=True)
        return {**out, "explanation": hit, "generated_by": "llm", "cached": True}
    metrics.inc("cache_miss")
    usage, status, err = Usage(), "ok", None
    try:
        res = await prov.generate(GenRequest(system=prompts.system_prompt(lang) + "\nThe user wants an explanation of one already-computed result.",
                                             messages=[Msg("user", f"<sources>\n{src}\n</sources>\nExplain this result for a site engineer and say what to do next.")],
                                             max_tokens=min(300, settings.ai_max_tokens), temperature=settings.ai_temperature))
        usage = res.usage
        ok = grounding.validate_answer(res.text, set(), src + " " + nxt, require_citation=False)
        if ok:
            out.update(explanation=ok, generated_by="llm")
            await kv.get_kv().set(key, ok, settings.ai_cache_ttl_s)
        else:
            metrics.inc("validation_rejected")
            status = "fallback"
    except ProviderError as e:
        status, err = "fallback", map_error(e).code
    await _record(caller, "explain", usage, (time.perf_counter() - t0) * 1000, None, status, err)
    return out


async def explain_checklist(caller: Caller, checklist: dict) -> dict:
    """Plain-language summary of an already-verified checklist. The model may rephrase items; it cannot add tests or numbers."""
    items = "\n".join(f"- {i['name']} ({i['timing']}; ref {i['reference']}): {i['purpose']}" for i in checklist["items"])
    ctx = checklist["context"]
    fallback = (f"For {ctx['grade']} concrete in a {ctx['element_type']} ({ctx['exposure']} exposure, {ctx['quantity_m3']:g} m3), "
                f"take {checklist['sample_plan']['required_samples']} cube sample(s), run a slump test with each, "
                f"test cubes at 7 and 28 days, keep curing records and check cover before the pour.")
    prov = factory.get_provider()
    if prov is None:
        return {"summary": fallback, "generated_by": "template"}
    t0 = time.perf_counter()
    metrics.inc("requests")
    await enforce_limits(caller.user.id)
    src = f"Context: {ctx}\nVerified checklist:\n{items}"
    key = f"checklist:{prompts.PROMPT_VERSION}:{prov.model}:{caller.lang}:{hashlib.sha256(src.encode()).hexdigest()}"      # inputs are public rule data only
    hit = await kv.get_kv().get(key)
    if hit:
        metrics.inc("cache_hit")
        await _record(caller, "checklist", Usage(), (time.perf_counter() - t0) * 1000, None, "ok", cached=True)
        return {"summary": hit, "generated_by": "llm", "cached": True}
    metrics.inc("cache_miss")
    usage, status, err, out = Usage(), "ok", None, {"summary": fallback, "generated_by": "template"}
    try:
        res = await prov.generate(GenRequest(system=prompts.system_prompt(caller.lang) + "\nRewrite the verified checklist in simple language for a site engineer. Use ONLY the items given.",
                                             messages=[Msg("user", f"<sources>\n{src}\n</sources>")], max_tokens=min(300, settings.ai_max_tokens), temperature=settings.ai_temperature))
        usage = res.usage
        ok = grounding.validate_answer(res.text, set(), src, require_citation=False)
        if ok:
            out = {"summary": ok, "generated_by": "llm"}
            await kv.get_kv().set(key, ok, settings.ai_cache_ttl_s)
        else:
            metrics.inc("validation_rejected")
            status = "fallback"
    except ProviderError as e:
        status, err = "fallback", map_error(e).code
    await _record(caller, "checklist", usage, (time.perf_counter() - t0) * 1000, None, status, err)
    return out


record = _record          # public alias for routers that call generate_structured directly
