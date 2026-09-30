from ..config import settings
from .anthropic import AnthropicProvider
from .openai_compat import OpenAICompatProvider
from .resilient import ResilientProvider

# Defaults are conveniences only: model names change, so set AI_MODEL explicitly for anything that matters.
DEFAULTS = {
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-5-5"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),      # Gemini's OpenAI-compatible endpoint
    "local": ("http://localhost:11434/v1", "llama3.1"),                                            # Ollama / vLLM / LM Studio
}
EMBED_DEFAULTS = {"openai": "text-embedding-3-small", "gemini": "text-embedding-004", "local": "nomic-embed-text"}


def _build(kind: str, model: str, key: str, base: str, emb_model: str = "", emb_dims: int = 0):
    dbase, dmodel = DEFAULTS[kind]
    kw = dict(base_url=base or dbase, api_key=key, model=model or dmodel)
    if kind == "anthropic":
        return AnthropicProvider(**kw)
    return OpenAICompatProvider(name=kind, embedding_model=emb_model, embedding_dims=emb_dims, **kw)


_instance: ResilientProvider | None = None
_built_for: tuple | None = None


def _signature() -> tuple:
    s = settings
    return (s.ai_provider, s.ai_model, s.ai_base_url, s.ai_api_key, s.ai_fallback_provider, s.ai_fallback_model,
            s.ai_embedding_provider, s.ai_embedding_model, s.ai_embedding_dimensions)


def get_provider() -> ResilientProvider | None:
    """Process-wide provider (pooled connections). Rebuilt automatically if AI_* settings change (used by tests)."""
    global _instance, _built_for
    s = settings
    if s.ai_provider == "none":
        return None
    sig = _signature()
    if _instance is None or _built_for != sig:
        primary = _build(s.ai_provider, s.ai_model, s.ai_api_key, s.ai_base_url)
        fb = None
        if s.ai_fallback_provider != "none":
            fb = _build(s.ai_fallback_provider, s.ai_fallback_model, s.ai_fallback_api_key or s.ai_api_key, s.ai_fallback_base_url)
        _instance, _built_for = ResilientProvider(primary, fb), sig
        if s.ai_embedding_provider != "none":
            ek = s.ai_embedding_api_key or s.ai_api_key
            _instance.embedder = _build(s.ai_embedding_provider, "", ek, s.ai_embedding_base_url or (s.ai_base_url if s.ai_embedding_provider == s.ai_provider else ""),
                                        s.ai_embedding_model or EMBED_DEFAULTS[s.ai_embedding_provider], s.ai_embedding_dimensions)
    return _instance


def embeddings_enabled() -> bool:
    p = get_provider()
    return p is not None and p.embedder is not None


def embedding_model_id() -> str:
    return f"{settings.ai_embedding_provider}:{settings.ai_embedding_model or EMBED_DEFAULTS.get(settings.ai_embedding_provider, '')}:{settings.ai_embedding_dimensions}"


async def shutdown() -> None:
    global _instance, _built_for
    if _instance is not None:
        await _instance.aclose()
    _instance, _built_for = None, None
