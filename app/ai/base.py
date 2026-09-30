"""Provider-neutral types. Nothing outside app/ai/providers* knows which vendor is in use."""
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]                      # JSON Schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Msg:
    role: str                                       # user | assistant | tool
    content: Any = ""                               # str, or list of parts: {"type":"text"|"image"|"document", ...}
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class GenRequest:
    system: str
    messages: list[Msg]
    max_tokens: int
    temperature: float
    tools: list[ToolSpec] = field(default_factory=list)
    json_mode: bool = False


@dataclass
class GenResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    finish_reason: str = ""


@dataclass
class StreamEvent:
    kind: str                                       # "delta" | "done"
    text: str = ""
    result: GenResult | None = None


# kinds that justify trying the fallback provider (the request itself was fine, the provider was not)
FALLBACK_KINDS = {"timeout", "network", "rate_limit", "unavailable", "model_unavailable", "auth"}


class ProviderError(Exception):
    """Normalised provider failure. `message` is safe to log; it never contains keys or prompts."""

    def __init__(self, kind: str, message: str = "", *, retryable: bool = False, status: int | None = None, retry_after: float | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message, self.retryable, self.status, self.retry_after = kind, message, retryable, status, retry_after


class Provider(Protocol):
    name: str
    model: str

    async def generate(self, req: GenRequest) -> GenResult: ...
    def stream(self, req: GenRequest) -> AsyncGenerator[StreamEvent, None]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def aclose(self) -> None: ...
