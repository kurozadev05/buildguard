import json
from collections.abc import AsyncGenerator
from typing import Any

from .base import GenRequest, GenResult, Msg, ProviderError, StreamEvent, ToolCall, Usage
from .http import HttpProvider


def _parts(content: Any) -> Any:
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p["text"]})
        elif t in ("image", "document"):
            out.append({"type": t, "source": {"type": "base64", "media_type": p["media_type"], "data": p["data"]}})
    return out


def _messages(msgs: list[Msg]) -> list[dict]:
    out: list[dict] = []
    for m in msgs:
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content if isinstance(m.content, str) else json.dumps(m.content)}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)               # parallel tool results go in one user turn
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant":
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks or ""})
        else:
            out.append({"role": "user", "content": _parts(m.content)})
    return out


class AnthropicProvider(HttpProvider):
    name = "anthropic"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _payload(self, req: GenRequest, stream: bool) -> dict:
        p: dict[str, Any] = {"model": self.model, "max_tokens": req.max_tokens, "temperature": req.temperature,
                             "system": req.system, "messages": _messages(req.messages)}
        if req.tools:
            p["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in req.tools]
        if stream:
            p["stream"] = True
        return p

    async def generate(self, req: GenRequest) -> GenResult:
        d = await self.post_json("/v1/messages", self._payload(req, False))
        try:
            text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
            calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in d["content"] if b.get("type") == "tool_use"]
            u = d.get("usage") or {}
            return GenResult(text, calls, Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))), d.get("model", self.model), self.name, d.get("stop_reason", ""))
        except (KeyError, TypeError, ValueError):
            raise ProviderError("invalid_response", "unexpected response shape") from None

    async def stream(self, req: GenRequest) -> AsyncGenerator[StreamEvent, None]:
        text, tools, usage, stop, model = [], {}, Usage(), "", self.model
        async for line in self.stream_lines("/v1/messages", self._payload(req, True)):
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            t = ev.get("type")
            if t == "message_start":
                usage.input_tokens = int((ev.get("message", {}).get("usage") or {}).get("input_tokens", 0))
                model = ev.get("message", {}).get("model", model)
            elif t == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                cb = ev["content_block"]
                tools[ev["index"]] = {"id": cb["id"], "name": cb["name"], "json": ""}
            elif t == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta":
                    text.append(d.get("text", ""))
                    yield StreamEvent("delta", d.get("text", ""))
                elif d.get("type") == "input_json_delta" and ev.get("index") in tools:
                    tools[ev["index"]]["json"] += d.get("partial_json", "")
            elif t == "message_delta":
                usage.output_tokens = int((ev.get("usage") or {}).get("output_tokens", usage.output_tokens))
                stop = (ev.get("delta") or {}).get("stop_reason", stop)
            elif t == "error":
                et = (ev.get("error") or {}).get("type", "")
                raise ProviderError("unavailable" if "overload" in et or "api_error" in et else "bad_request", "provider stream error", retryable="overload" in et or "api_error" in et)
        calls = []
        for i in sorted(tools):
            try:
                args = json.loads(tools[i]["json"] or "{}")
            except ValueError:
                raise ProviderError("invalid_response", "malformed tool arguments") from None
            calls.append(ToolCall(tools[i]["id"], tools[i]["name"], args if isinstance(args, dict) else {}))
        yield StreamEvent("done", result=GenResult("".join(text), calls, usage, model, self.name, stop))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError("bad_request", "Anthropic has no embeddings API; set AI_EMBEDDING_PROVIDER")
