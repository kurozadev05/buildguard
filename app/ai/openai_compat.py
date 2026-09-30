"""OpenAI-compatible chat + embeddings. Also serves Gemini (its OpenAI-compatible endpoint), Azure/OpenRouter, Ollama, vLLM, LM Studio."""
import json
from collections.abc import AsyncGenerator
from typing import Any

from .base import GenRequest, GenResult, Msg, ProviderError, StreamEvent, ToolCall, Usage
from .http import HttpProvider


def _content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p["text"]})
        elif t == "image":
            out.append({"type": "image_url", "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}})
        else:
            raise ProviderError("bad_request", "this input type is not supported by the configured provider")
    return out


def _messages(system: str, msgs: list[Msg]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in msgs:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content if isinstance(m.content, str) else json.dumps(m.content)})
        elif m.role == "assistant":
            d: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                d["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in m.tool_calls]
            out.append(d)
        else:
            out.append({"role": "user", "content": _content(m.content)})
    return out


def _loads(s: str) -> dict:
    try:
        v = json.loads(s or "{}")
    except ValueError:
        raise ProviderError("invalid_response", "malformed tool arguments") from None
    return v if isinstance(v, dict) else {}


class OpenAICompatProvider(HttpProvider):
    def __init__(self, *, name: str = "openai", embedding_model: str = "", embedding_dims: int = 0, **kw):
        super().__init__(**kw)
        self.name, self.embedding_model, self.embedding_dims = name, embedding_model, embedding_dims

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(self, req: GenRequest, stream: bool) -> dict:
        p: dict[str, Any] = {"model": self.model, "messages": _messages(req.system, req.messages), "max_tokens": req.max_tokens, "temperature": req.temperature}
        if req.tools:
            p["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in req.tools]
        if req.json_mode:
            p["response_format"] = {"type": "json_object"}
        if stream:
            p["stream"], p["stream_options"] = True, {"include_usage": True}
        return p

    async def generate(self, req: GenRequest) -> GenResult:
        d = await self.post_json("/chat/completions", self._payload(req, False))
        try:
            ch = d["choices"][0]
            msg = ch["message"]
            calls = [ToolCall(c["id"], c["function"]["name"], _loads(c["function"].get("arguments", ""))) for c in (msg.get("tool_calls") or [])]
            u = d.get("usage") or {}
            return GenResult(msg.get("content") or "", calls, Usage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))),
                             d.get("model", self.model), self.name, ch.get("finish_reason") or "")
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("invalid_response", "unexpected response shape") from None

    async def stream(self, req: GenRequest) -> AsyncGenerator[StreamEvent, None]:
        text: list[str] = []
        tools: dict[int, dict] = {}
        usage, finish, model = Usage(), "", self.model
        async for line in self.stream_lines("/chat/completions", self._payload(req, True)):
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                ev = json.loads(body)
            except ValueError:
                continue
            model = ev.get("model", model)
            if ev.get("usage"):
                usage = Usage(int(ev["usage"].get("prompt_tokens", 0)), int(ev["usage"].get("completion_tokens", 0)))
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    text.append(d["content"])
                    yield StreamEvent("delta", d["content"])
                for tc in d.get("tool_calls") or []:
                    slot = tools.setdefault(tc.get("index", 0), {"id": "", "name": "", "json": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["json"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
        calls = [ToolCall(tools[i]["id"] or f"call_{i}", tools[i]["name"], _loads(tools[i]["json"])) for i in sorted(tools)]
        yield StreamEvent("done", result=GenResult("".join(text), calls, usage, model, self.name, finish))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise ProviderError("bad_request", "AI_EMBEDDING_MODEL is not set")
        payload: dict[str, Any] = {"model": self.embedding_model, "input": texts}
        if self.embedding_dims:
            payload["dimensions"] = self.embedding_dims
        d = await self.post_json("/embeddings", payload)
        try:
            rows = sorted(d["data"], key=lambda r: r["index"])
            vecs = [[float(x) for x in r["embedding"]] for r in rows]
        except (KeyError, TypeError, ValueError):
            raise ProviderError("invalid_response", "unexpected embeddings shape") from None
        if len(vecs) != len(texts):
            raise ProviderError("invalid_response", "embedding count mismatch")
        return vecs
