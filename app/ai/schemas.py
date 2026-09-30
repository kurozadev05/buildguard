"""Request schemas and validated structured-output schemas for the AI layer (pydantic = the runtime validation layer)."""
import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(min_length=36, max_length=36, pattern=r"^[0-9a-fA-F-]{36}$")]


class ChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    conversation_id: Id | None = None
    project_id: Id | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    batch_id: Id | None = None
    project_id: Id | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    project_id: Id | None = None
    top_k: int = Field(default=4, ge=1, le=8)


class DocumentIn(BaseModel):
    project_id: Id
    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=20, max_length=400_000)


class AttachIn(BaseModel):
    test_id: Id


# ───────────── structured model outputs ─────────────
_BATCH = re.compile(r"^CON-M\d{2}-\d{3}$")
_SAMPLE = re.compile(r"^CON-M\d{2}-\d{3}-S\d+$")
_GRADE = re.compile(r"^M\d{2}$")


class ReportFields(BaseModel):
    """Draft fields read from a lab report. Anything invalid is dropped (None), never passed on; a human confirms the rest."""
    model_config = ConfigDict(extra="ignore")
    batch_code: str | None = None
    sample_code: str | None = None
    grade: str | None = None
    test_date: date | None = None
    age_days: int | None = Field(default=None, ge=1, le=365)
    specimens_mpa: list[float] | None = None
    lab_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            raise ValueError("expected an object")
        out: dict[str, Any] = {}
        for k, rx in (("batch_code", _BATCH), ("sample_code", _SAMPLE), ("grade", _GRADE)):
            x = v.get(k)
            if isinstance(x, str) and rx.match(x.strip().upper()):
                out[k] = x.strip().upper()
        td = v.get("test_date")
        if isinstance(td, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", td):
            out["test_date"] = td
        age = v.get("age_days")
        if isinstance(age, int) and not isinstance(age, bool) and 1 <= age <= 365:
            out["age_days"] = age
        sp = v.get("specimens_mpa")
        if isinstance(sp, list) and 1 <= len(sp) <= 6 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 3 <= x <= 120 for x in sp):
            out["specimens_mpa"] = [float(x) for x in sp]
        lab = v.get("lab_name")
        if isinstance(lab, str) and lab.strip():
            out["lab_name"] = re.sub(r"[^\w .,&()/-]", "", lab)[:120]
        return out


class DisplayReading(BaseModel):
    """Transcription of a compression-machine display. The MODEL only transcribes; strength is computed by the server."""
    model_config = ConfigDict(extra="ignore")
    value: float | None = Field(default=None, gt=0, lt=100000)
    unit: Literal["kN", "N", "MPa", "N/mm2", "kgf", "tonnes"] | None = None
    specimen_label: str | None = Field(default=None, max_length=60)
    legible: bool = True


def to_strength_mpa(value: float, unit: str, cube_mm: int = 150) -> float | None:
    area = float(cube_mm * cube_mm)                                      # mm2 (150 mm cube = 22 500 mm2)
    n = {"kN": value * 1000, "N": value, "kgf": value * 9.80665, "tonnes": value * 9806.65}.get(unit)
    if unit in ("MPa", "N/mm2"):
        return value
    return None if n is None else n / area
