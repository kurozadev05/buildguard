"""Report digitisation: OCR/vision -> DRAFT fields. A human must confirm before a test record is created."""
import logging
import re
from typing import Any
from datetime import datetime

import base64

from ..ai import prompts
from ..ai.base import Usage
from ..ai.schemas import ReportFields
from ..ai.structured import generate_structured

log = logging.getLogger("buildguard.ocr")

_BATCH = re.compile(r"\bCON-M\d{2}-\d{3}\b", re.I)
_SAMPLE = re.compile(r"\bCON-M\d{2}-\d{3}-S\d+\b", re.I)
_GRADE = re.compile(r"\bM\s?(10|15|20|25|30|35|40|45|50)\b")
_AGE = re.compile(r"\b(\d{1,3})\s*[- ]?\s*(?:days?|d)\b", re.I)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})\b")
_STRENGTH = re.compile(r"\b(\d{1,3}(?:\.\d{1,2})?)\s*(?:N\s*/\s*mm\s*2|N/mm²|MPa|N/sq\.?\s*mm)", re.I)


def _to_iso(d: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_report_text(text: str) -> dict:
    """Regex fallback used when no vision model is configured, or on device-side OCR text."""
    fields: dict[str, Any] = {}
    conf: dict[str, float] = {}
    m = _SAMPLE.search(text)
    if m:
        fields["sample_code"] = m.group(0).upper()
        conf["sample_code"] = 0.9
    m = _BATCH.search(text)
    if m:
        fields["batch_code"] = m.group(0).upper()
        conf["batch_code"] = 0.9
    m = _GRADE.search(text)
    if m:
        fields["grade"] = "M" + m.group(1)
        conf["grade"] = 0.7
    m = _AGE.search(text)
    if m and 1 <= int(m.group(1)) <= 365:
        fields["age_days"] = int(m.group(1))
        conf["age_days"] = 0.7
    m = _DATE.search(text)
    if m and _to_iso(m.group(1)):
        fields["test_date"] = _to_iso(m.group(1))
        conf["test_date"] = 0.6
    strengths = [float(x) for x in _STRENGTH.findall(text)]
    strengths = [s for s in strengths if 3 <= s <= 120]
    if strengths:
        fields["specimens_mpa"] = strengths[:3] if len(strengths) >= 3 else strengths
        conf["specimens_mpa"] = 0.6 if len(strengths) >= 3 else 0.4
    return {"fields": fields, "confidence": conf}


async def extract(data: bytes, content_type: str) -> tuple[dict, Usage]:
    """Vision model (validated structured output) -> local OCR -> manual. Always a DRAFT for a human to confirm."""
    usage = Usage()
    if len(data) <= 5 * 1024 * 1024:
        kind = "document" if content_type == "application/pdf" else "image"
        parts = [{"type": kind, "media_type": content_type, "data": base64.b64encode(data).decode()}, {"type": "text", "text": "Extract the report fields."}]
        obj, usage, status = await generate_structured(ReportFields, prompts.EXTRACT_REPORT, parts)
        if obj is not None:
            return {"provider": "llm_vision", "fields": obj.model_dump(exclude_none=True, mode="json"), "confidence": {}, "needs_human_check": True}, usage
    if content_type.startswith("image/"):
        try:
            import io

            import pytesseract
            from PIL import Image
            from starlette.concurrency import run_in_threadpool
            text = await run_in_threadpool(lambda: pytesseract.image_to_string(Image.open(io.BytesIO(data))))
            return {"provider": "tesseract", **parse_report_text(text), "raw_text": text[:4000], "needs_human_check": True}, usage
        except Exception:
            log.info("tesseract unavailable; falling back to manual entry")
    return {"provider": "none", "fields": {}, "confidence": {}, "needs_human_check": True,
            "message": "No OCR engine configured. Enter values manually, or POST device-side OCR text to /api/ocr/parse-text."}, usage
