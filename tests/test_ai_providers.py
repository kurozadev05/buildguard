"""Provider layer over real HTTP: wire formats, streaming, tools, retries, timeouts, fallback, breaker, cancellation, secrecy."""
import asyncio

import pytest

from app.ai import factory
from app.ai.base import GenRequest, Msg, ProviderError, ToolSpec
from app.ai.metrics import metrics
from app.config import settings

REQ = GenRequest(system="sys", messages=[Msg("user", "hello")], max_tokens=100, temperature=0.1)
TOOL = ToolSpec("get_batch", "Look up a batch", {"type": "object", "properties": {"batch_code": {"type": "string"}}, "required": ["batch_code"]})


def use(monkeypatch, fake_server, kind):
    base = {"anthropic": "/anthropic", "openai": "/openai"}[kind]
    monkeypatch.setattr(settings, "ai_provider", kind)
    monkeypatch.setattr(settings, "ai_base_url", fake_server + base)
    factory._instance = None


async def collect(gen):
    deltas, final = [], None
    async for ev in gen:
        if ev.kind == "delta":
            deltas.append(ev.text)
        else:
            final = ev.result
    return deltas, final


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_generate_text_and_usage(fake, fake_server, monkeypatch, kind):
    use(monkeypatch, fake_server, kind)
    fake.push({"text": "hello there", "usage": (21, 5)})
    r = await factory.get_provider().generate(REQ)
    assert r.text == "hello there" and (r.usage.input_tokens, r.usage.output_tokens) == (21, 5)
    sent = fake.calls[0]
    assert sent["body"]["model"] == "fake-model" and sent["body"]["max_tokens"] == 100
    assert ("x-api-key" if kind == "anthropic" else "authorization") in sent["headers"]


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_tool_calls_and_tool_result_roundtrip(fake, fake_server, monkeypatch, kind):
    use(monkeypatch, fake_server, kind)
    fake.push({"tool_calls": [{"name": "get_batch", "args": {"batch_code": "CON-M25-001"}}]}, {"text": "done"})
    req = GenRequest(system="s", messages=[Msg("user", "status?")], max_tokens=50, temperature=0, tools=[TOOL])
    p = factory.get_provider()
    r1 = await p.generate(req)
    assert r1.tool_calls[0].name == "get_batch" and r1.tool_calls[0].arguments == {"batch_code": "CON-M25-001"}
    req.messages += [Msg("assistant", "", tool_calls=r1.tool_calls), Msg("tool", '{"status":"FLAGGED"}', tool_call_id=r1.tool_calls[0].id)]
    assert (await p.generate(req)).text == "done"
    second = fake.calls[1]["body"]["messages"]
    assert any("tool_result" in str(m) or m.get("role") == "tool" for m in second)          # result was sent back in the vendor's format
    assert "tools" in fake.calls[0]["body"]


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_streaming_deltas_arrive_incrementally_with_usage(fake, fake_server, monkeypatch, kind):
    use(monkeypatch, fake_server, kind)
    fake.push({"text": "one two three four", "chunk_delay": 0.05, "usage": (30, 9)})
    times = []
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    deltas, final = [], None
    async for ev in factory.get_provider().stream(REQ):
        if ev.kind == "delta":
            deltas.append(ev.text)
            times.append(loop.time() - t0)
        else:
            final = ev.result
    assert "".join(deltas) == "one two three four" and final.text == "one two three four"
    assert (final.usage.input_tokens, final.usage.output_tokens) == (30, 9)
    assert times[-1] - times[0] > 0.1                                       # really streamed, not buffered and dumped at the end


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_streaming_tool_call_assembly(fake, fake_server, monkeypatch, kind):
    use(monkeypatch, fake_server, kind)
    fake.push({"tool_calls": [{"name": "get_batch", "args": {"batch_code": "CON-M25-007"}}]})
    _, final = await collect(factory.get_provider().stream(GenRequest("s", [Msg("user", "x")], 50, 0, tools=[TOOL])))
    assert final.tool_calls[0].arguments == {"batch_code": "CON-M25-007"}


async def test_rate_limit_is_retried_honouring_retry_after(fake):
    fake.push({"status": 429, "retry_after": 2}, {"text": "finally"})
    r = await factory.get_provider().generate(REQ)
    assert r.text == "finally" and len(fake.calls) == 2 and fake.sleeps[0] >= 2 and metrics.snapshot()["counters"]["retries"] == 1


async def test_retries_are_bounded_with_exponential_backoff(fake):
    fake.push(*[{"status": 503}] * 10)
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == "unavailable" and len(fake.calls) == settings.ai_max_retries + 1
    assert fake.sleeps[1] > fake.sleeps[0]


@pytest.mark.parametrize("status,kind", [(401, "auth"), (400, "bad_request"), (404, "model_unavailable")])
async def test_non_retryable_errors_are_not_retried(fake, status, kind):
    fake.push({"status": status}, {"text": "should never be reached"})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == kind and len(fake.calls) == 1


async def test_context_length_error_is_classified(fake):
    fake.push({"status": 400, "message": "prompt is too long: maximum context length exceeded"})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == "context_length" and len(fake.calls) == 1


async def test_hung_provider_times_out_and_retries(fake, monkeypatch):
    monkeypatch.setattr(settings, "ai_timeout_ms", 300)
    monkeypatch.setattr(settings, "ai_max_retries", 1)
    factory._instance = None
    fake.push({"delay": 2}, {"delay": 2})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == "timeout" and len(fake.calls) == 2 and metrics.snapshot()["counters"]["provider_errors.timeout"] == 2


async def test_malformed_provider_json_is_rejected_cleanly(fake):
    fake.push({"raw": "{not json"})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == "invalid_response"


async def test_fallback_provider_used_on_outage_but_not_on_bad_request(fake, fake_server, monkeypatch):
    monkeypatch.setattr(settings, "ai_fallback_provider", "openai")
    monkeypatch.setattr(settings, "ai_fallback_base_url", fake_server + "/openai")
    monkeypatch.setattr(settings, "ai_fallback_model", "fb-model")
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    factory._instance = None
    fake.push({"status": 503}, {"text": "from fallback"})
    r = await factory.get_provider().generate(REQ)
    assert r.text == "from fallback" and r.provider == "openai" and metrics.snapshot()["counters"]["fallback_used"] == 1
    fake.calls.clear()
    fake.push({"status": 400}, {"text": "must not be used"})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert e.value.kind == "bad_request" and len(fake.calls) == 1


async def test_circuit_breaker_stops_hammering_a_dead_provider(fake, monkeypatch):
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    factory._instance = None
    fake.push(*[{"status": 503}] * 20)
    p = factory.get_provider()
    for _ in range(3):
        with pytest.raises(ProviderError):
            await p.generate(REQ)
    n = len(fake.calls)
    with pytest.raises(ProviderError) as e:
        await p.generate(REQ)
    assert len(fake.calls) == n and "circuit" in e.value.message and metrics.snapshot()["counters"]["breaker_opened"] == 1


async def test_stream_retries_before_first_token_but_never_after(fake):
    fake.push({"status": 503}, {"text": "a b c"})
    deltas, final = await collect(factory.get_provider().stream(REQ))
    assert "".join(deltas) == "a b c" and len(fake.calls) == 2
    fake.calls.clear()
    fake.push({"text": "a b c d e f", "disconnect_after": 8}, {"text": "restarted from scratch"})
    got = []
    with pytest.raises(ProviderError) as e:
        async for ev in factory.get_provider().stream(REQ):
            if ev.kind == "delta":
                got.append(ev.text)
    assert e.value.kind == "stream_interrupted" and got and len(fake.calls) == 1               # partial text was shown => no silent restart


async def test_client_cancellation_closes_the_upstream_stream(fake):
    fake.push({"text": " ".join(["word"] * 200), "chunk_delay": 0.02})
    gen = factory.get_provider().stream(REQ)
    async for ev in gen:
        if ev.kind == "delta":
            break
    assert fake.active_streams == 1
    await gen.aclose()
    for _ in range(50):
        if fake.active_streams == 0:
            break
        await asyncio.sleep(0.05)
    assert fake.active_streams == 0


async def test_embeddings_keep_input_order(fake, fake_server, monkeypatch):
    monkeypatch.setattr(settings, "ai_embedding_provider", "openai")
    monkeypatch.setattr(settings, "ai_embedding_base_url", fake_server + "/openai")
    monkeypatch.setattr(settings, "ai_embedding_model", "emb-x")
    factory._instance = None
    vecs = await factory.get_provider().embed(["core test", "slump test", "curing days"])
    assert [v.index(1.0) for v in vecs] == [0, 1, 2]


async def test_secrets_never_appear_in_errors_or_logs(fake, caplog):
    fake.push({"status": 401, "message": "invalid key sk-test-SECRET-KEY-123"})
    with pytest.raises(ProviderError) as e:
        await factory.get_provider().generate(REQ)
    assert "SECRET" not in str(e.value) and "SECRET" not in caplog.text
