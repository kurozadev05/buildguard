import json
import re
from datetime import date, datetime, timedelta, UTC
from typing import Annotated, Any, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

Role = Literal["admin", "site_engineer", "qa", "lab", "client", "auditor"]
Exposure = Literal["mild", "moderate", "severe", "very_severe", "extreme"]
ElementType = Literal["slab", "beam", "column", "footing", "wall", "staircase", "raft", "other"]
TestType = Literal["slump", "fresh_temperature", "cube_compressive_strength", "core_strength", "upv", "rebound_hammer"]
ObsKind = Literal["crack_width_mm", "moisture_pct", "upv_km_s", "rebound_number", "carbonation_depth_mm",
                  "half_cell_mv", "curing_days", "temperature_c", "humidity_pct"]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _utc_naive(v: datetime | None) -> datetime | None:
    """Store everything as naive UTC and refuse readings dated in the future (clock skew allowance: 1 day)."""
    if v is None:
        return v
    if v.tzinfo is not None:
        v = v.astimezone(UTC).replace(tzinfo=None)
    if v > datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1):
        raise ValueError("timestamp is in the future")
    return v


def _small_json(v: dict) -> dict:
    if len(json.dumps(v, default=str)) > 8000:
        raise ValueError("values object too large (max 8 KB)")
    return v


ClientId = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F-]{36}$")]     # client-generated UUIDs for offline creates
Text = Annotated[str, StringConstraints(max_length=2000)]
Name = Annotated[str, StringConstraints(max_length=200)]
UtcTime = Annotated[datetime, AfterValidator(_utc_naive)]
SmallDict = Annotated[dict[str, Any], AfterValidator(_small_json)]


class RegisterIn(BaseModel):
    email: str
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=128)
    language: Literal["en", "hi"] = "en"

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("invalid email")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class LogoutIn(BaseModel):
    refresh_token: Optional[str] = Field(default=None, max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class UserPatch(BaseModel):
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    language: Optional[Literal["en", "hi"]] = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    location: Optional[Name] = None
    client_name: Optional[Name] = None


class MemberIn(BaseModel):
    user_id: str


class BuildingIn(BaseModel):
    id: Optional[ClientId] = None
    name: str = Field(min_length=1, max_length=120)


class FloorIn(BaseModel):
    id: Optional[ClientId] = None
    name: str = Field(min_length=1, max_length=120)
    level: int = 0


class ElementIn(BaseModel):
    id: Optional[ClientId] = None
    name: str = Field(min_length=1, max_length=120)
    element_type: ElementType = "slab"
    exposure: Optional[Exposure] = None


class BatchIn(BaseModel):
    id: Optional[ClientId] = None
    project_id: str
    grade: str = Field(pattern=r"^[Mm]\d{2}$")
    construction_type: Literal["rcc", "pcc"] = "rcc"
    exposure: Exposure = "moderate"
    supplier: Optional[Name] = None
    supplier_ref: Optional[Name] = None
    delivery_date: Optional[date] = None
    quantity_m3: float = Field(gt=0, le=10000)
    cement_type: Literal["opc", "ppc", "psc", "other"] = "opc"
    cement_content_kg_m3: Optional[float] = Field(default=None, gt=0, le=800)
    wc_ratio: Optional[float] = Field(default=None, gt=0, le=1.0)
    admixture: Optional[Text] = None
    mix_notes: Optional[Text] = None


class SampleIn(BaseModel):
    id: Optional[ClientId] = None
    batch_id: str
    cast_date: date
    cubes_cast: int = Field(default=6, ge=3, le=30)
    curing_method: Optional[Text] = None
    notes: Optional[Text] = None


class TestIn(BaseModel):
    id: Optional[ClientId] = None
    batch_id: str
    sample_id: Optional[str] = None
    test_type: TestType
    age_days: Optional[int] = Field(default=None, ge=1, le=365)
    values: SmallDict
    lab_name: Optional[Name] = None
    notes: Optional[Text] = None
    tested_at: Optional[UtcTime] = None
    investigation_action_id: Optional[ClientId] = None


class AmendIn(BaseModel):
    values: SmallDict
    age_days: Optional[int] = Field(default=None, ge=1, le=365)
    reason: str = Field(min_length=5, max_length=1000)


class ReviewIn(BaseModel):
    decision: Literal["accepted", "rejected"]
    comment: str = Field(min_length=3, max_length=1000)


class UsageIn(BaseModel):
    id: Optional[ClientId] = None
    batch_id: str
    element_id: str
    volume_m3: Optional[float] = Field(default=None, gt=0)
    poured_at: Optional[UtcTime] = None
    notes: Optional[Text] = None


class ObservationIn(BaseModel):
    id: Optional[ClientId] = None
    element_id: str
    kind: ObsKind
    value: float = Field(ge=-100000, le=100000)
    unit: Optional[Annotated[str, StringConstraints(max_length=20)]] = None
    observed_at: Optional[UtcTime] = None
    notes: Optional[Text] = None


class ChecklistIn(BaseModel):
    construction_type: Literal["rcc", "pcc"] = "rcc"
    element_type: ElementType = "slab"
    grade: str = Field(pattern=r"^[Mm]\d{2}$")
    exposure: Exposure = "moderate"
    quantity_m3: float = Field(default=10, gt=0, le=10000)
    reinforcement: Literal["light", "heavy"] = "light"
    placing_method: Literal["normal", "pumped", "tremie"] = "normal"
    weather: Literal["normal", "hot", "cold"] = "normal"
    cement_type: Literal["opc", "ppc", "psc", "other"] = "opc"
    explain: bool = False


class ActionPatch(BaseModel):
    status: Literal["pending", "done", "waived"]
    note: Optional[Text] = None
    result_test_id: Optional[str] = None


class CloseIn(BaseModel):
    closure_note: str = Field(min_length=5, max_length=1000)


class InvestigationIn(BaseModel):
    batch_id: str
    reason: str = Field(min_length=5, max_length=1000)


class OcrConfirmIn(BaseModel):
    batch_id: Optional[str] = None
    sample_id: Optional[str] = None
    test_type: TestType = "cube_compressive_strength"
    age_days: Optional[int] = Field(default=None, ge=1, le=365)
    values: SmallDict
    lab_name: Optional[Name] = None
    tested_at: Optional[UtcTime] = None


class OcrTextIn(BaseModel):
    text: str = Field(min_length=3, max_length=20000)


class SyncOpIn(BaseModel):
    op_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    type: Literal["batch.create", "sample.create", "test.create", "usage.create", "observation.create"]
    client_ts: Optional[UtcTime] = None
    data: SmallDict


class SyncPushIn(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=64)
    ops: list[SyncOpIn] = Field(max_length=100)
