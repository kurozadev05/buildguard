"""A real HTTP server speaking the Anthropic and OpenAI(-compatible) wire formats, driven by a script.
Runs in a thread on an ephemeral port so provider code is tested over real sockets (streaming, timeouts, disconnects)."""
import asyncio
import json
import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

CONCEPTS = [("core", "कोर"), ("slump", "स्लम्प"), ("curing", "क्योरिंग"), ("cover", "कवर"), ("crack", "दरार"), ("sampling", "सैंपल", "sample"),
            ("exposure", "एक्सपोज़र"), ("spec", "specification", "विनिर्देश"), ("cube", "क्यूब"), ("upv", "ultrasonic")]


def embed_text(text: str, dims: int = 16) -> list[float]:
    t = text.lower()
    v = [0.0] * dims
    for i, group in enumerate(CONCEPTS):
        c = sum(t.count(w) for w in group)
        if c:
            v[i] = float(c)
    if not any(v):
        v[dims - 1] = 1.0
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


class Fake:
    def __init__(self):
        self.q: deque = deque()
        self.calls: list[dict] = []
        self.active_streams = 0
        self.default = {"text": "ok [K3]"}

    def push(self, *behaviors):
        self.q.extend(behaviors)

    def reset(self):
        self.q.clear()
        self.calls.clear()
        self.active_streams = 0
        self.default = {"text": "ok [K3]"}

    def next(self, path: str, body: dict, headers) -> dict:
        self.calls.append({"path": path, "body": body, "headers": dict(headers)})
        return self.q.popleft() if self.q else self.default


fake = Fake()
app = FastAPI()


def _err(b: dict):
    h = {"retry-after": str(b["retry_after"])} if b.get("retry_after") is not None else {}
    return JSONResponse({"error": {"message": b.get("message", "boom")}}, status_code=b["status"], headers=h)


async def _pre(b: dict):
    if b.get("delay"):
        await asyncio.sleep(b["delay"])


async def _sse(events, b: dict):
    fake.active_streams += 1
    try:
        for n, e in enumerate(events):
            if b.get("disconnect_after") is not None and n >= b["disconnect_after"]:
                raise RuntimeError("simulated connection drop")
            yield e
            await asyncio.sleep(b.get("chunk_delay", 0))
    finally:
        fake.active_streams -= 1


def _chunks(text: str):
    words = text.split(" ")
    return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]


@app.post("/anthropic/v1/messages")
async def anthropic(request: Request):
    body = await request.json()
    b = fake.next("anthropic", body, request.headers)
    await _pre(b)
    if b.get("status", 200) >= 400:
        return _err(b)
    if b.get("raw"):
        return JSONResponse(content=None, media_type="application/json") if False else StreamingResponse(iter([b["raw"]]), media_type="application/json")
    usage = b.get("usage", (11, 7))
    tcs = b.get("tool_calls", [])
    if body.get("stream"):
        ev = [f'event: message_start\ndata: {json.dumps({"type": "message_start", "message": {"model": "fake-a", "usage": {"input_tokens": usage[0]}}})}\n\n']
        idx = 0
        for w in _chunks(b.get("text", "")) if b.get("text") else []:
            ev.append(f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w}})}\n\n')
        for i, tc in enumerate(tcs):
            idx = i + 1
            ev.append(f'data: {json.dumps({"type": "content_block_start", "index": idx, "content_block": {"type": "tool_use", "id": f"tu_{i}", "name": tc["name"], "input": {}}})}\n\n')
            js = json.dumps(tc["args"])
            ev.append(f'data: {json.dumps({"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": js[:5]}})}\n\n')
            ev.append(f'data: {json.dumps({"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": js[5:]}})}\n\n')
        ev.append(f'data: {json.dumps({"type": "message_delta", "delta": {"stop_reason": "tool_use" if tcs else "end_turn"}, "usage": {"output_tokens": usage[1]}})}\n\n')
        ev.append('data: {"type": "message_stop"}\n\n')
        return StreamingResponse(_sse(ev, b), media_type="text/event-stream")
    content = ([{"type": "text", "text": b["text"]}] if b.get("text") else []) + [{"type": "tool_use", "id": f"tu_{i}", "name": tc["name"], "input": tc["args"]} for i, tc in enumerate(tcs)]
    return {"model": "fake-a", "content": content, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}, "stop_reason": "tool_use" if tcs else "end_turn"}


@app.post("/openai/chat/completions")
async def openai(request: Request):
    body = await request.json()
    b = fake.next("openai", body, request.headers)
    await _pre(b)
    if b.get("status", 200) >= 400:
        return _err(b)
    usage = b.get("usage", (11, 7))
    tcs = b.get("tool_calls", [])
    if body.get("stream"):
        ev = []
        for w in _chunks(b.get("text", "")) if b.get("text") else []:
            ev.append(f'data: {json.dumps({"model": "fake-o", "choices": [{"index": 0, "delta": {"content": w}}]})}\n\n')
        for i, tc in enumerate(tcs):
            js = json.dumps(tc["args"])
            ev.append(f'data: {json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": f"call_{i}", "function": {"name": tc["name"], "arguments": js[:4]}}]}}]})}\n\n')
            ev.append(f'data: {json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": js[4:]}}]}}]})}\n\n')
        ev.append(f'data: {json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tcs else "stop"}]})}\n\n')
        ev.append(f'data: {json.dumps({"choices": [], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})}\n\n')
        ev.append("data: [DONE]\n\n")
        return StreamingResponse(_sse(ev, b), media_type="text/event-stream")
    msg = {"role": "assistant", "content": b.get("text") or None}
    if tcs:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}} for i, tc in enumerate(tcs)]
    return {"model": "fake-o", "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tcs else "stop"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


@app.post("/openai/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    b = fake.next("embeddings", body, request.headers)
    if b.get("status", 200) >= 400:
        return _err(b)
    inputs = body["input"]
    return {"data": [{"index": i, "embedding": embed_text(t)} for i, t in reversed(list(enumerate(inputs)))]}      # returned out of order on purpose


class Server:
    def __init__(self):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> str:
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self):
        self.server.should_exit = True
