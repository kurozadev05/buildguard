"""Output safety checks. Model text is untrusted: it is only shown if it is grounded in the sources we supplied."""
import re

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CITE = re.compile(r"\[([KD]\d+)\]")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def clean_text(s: str, maxlen: int) -> str:
    return _CTRL.sub(" ", s).strip()[:maxlen]


def one_line(s: object, maxlen: int = 80) -> str:
    """For values echoed into prompts/tool results (names, suppliers): no newlines, no control chars, bounded."""
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(s))).strip()[:maxlen]


def numbers(text: str) -> set[str]:
    return set(_NUM.findall(text))


def validate_answer(answer: str, allowed_labels: set[str], source_text: str, *, require_citation: bool) -> str | None:
    """Return the answer if safely grounded, else None:
    * every [K3]/[D1] citation must be a source we actually provided,
    * (optionally) at least one citation,
    * every number must appear somewhere in the sources/tool results/user text (no invented thresholds)."""
    if not answer or not answer.strip():
        return None
    cited = set(_CITE.findall(answer))
    if cited - allowed_labels:
        return None
    if require_citation and not cited:
        return None
    if not numbers(answer) <= numbers(source_text):
        return None
    return answer.strip()


def fallback_answer(sources: list[dict], lang: str = "en") -> str:
    """Deterministic answer built straight from the verified sources (used when AI is off, down, or fails validation)."""
    if not sources:
        return ("मैं इसका उत्तर सत्यापित IS-कोड नोट्स से नहीं दे सकता।" if lang == "hi"
                else "I can't answer that from the verified IS-code notes or project documents.")
    return " ".join(f"{s['title']} ({s['reference'] or s['source']}): {s['text']} [{s['label']}]" for s in sources[:2])
