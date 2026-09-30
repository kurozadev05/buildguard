"""Token budgeting without a tokenizer dependency: a conservative estimate (over-counts slightly, which is the safe direction)."""
import math


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return math.ceil(ascii_chars / 3.6 + (len(text) - ascii_chars) / 1.3)          # Devanagari/other scripts cost far more per char


def fit_history(messages: list[dict], budget: int) -> list[dict]:
    """Keep the newest messages that fit `budget` tokens (always keeps the last one). Older turns are dropped, oldest first."""
    kept: list[dict] = []
    used = 0
    for m in reversed(messages):
        t = estimate_tokens(m["content"]) + 4
        if kept and used + t > budget:
            break
        kept.append(m)
        used += t
    return list(reversed(kept))
