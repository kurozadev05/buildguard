"""Function-calling tools. The model can only *request* these; the backend validates arguments (pydantic, extra=forbid),
authorizes as the calling user (same checks as the REST API), executes an allow-listed read-only query, and returns a size-capped,
sanitized result. There is no SQL/shell/filesystem/HTTP tool and nothing that writes."""
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import accessible_project_ids, ensure_access
from ..i18n import status_label
from ..models import Batch, RiskAssessment, TestRecord, User
from ..services import core, dashboard
from ..services.rules_engine import required_samples
from .base import ToolSpec
from .dbutil import in_db
from .grounding import one_line

log = logging.getLogger("buildguard.ai.tools")
MAX_RESULT_CHARS = 3500


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchCode(_Args):
    batch_code: str = Field(pattern=r"^CON-M\d{2}-\d{3}$", description="Batch code such as CON-M25-001")


class ListBatchesArgs(_Args):
    status: str | None = Field(default=None, pattern=r"^(PENDING|VERIFIED|REVIEW_REQUIRED|FLAGGED)$")
    limit: int = Field(default=10, ge=1, le=15)


class ProjectArg(_Args):
    project_id: str | None = Field(default=None, min_length=36, max_length=36)
    limit: int = Field(default=8, ge=1, le=10)


class KnowledgeArgs(_Args):
    query: str = Field(min_length=3, max_length=200)


def _batch_by_code(db: Session, user: User, code: str) -> Batch:
    b = db.scalar(select(Batch).where(Batch.batch_code == code))
    if not b:
        raise HTTPException(404, "batch not found")
    ensure_access(db, user, b.project_id)                         # same authorization as GET /api/batches/{id}
    return b


def get_batch(db: Session, user: User, a: BatchCode) -> dict:
    b = _batch_by_code(db, user, a.batch_code)
    tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == b.id, TestRecord.is_current == True)).all()          # noqa: E712
    return {"batch_code": b.batch_code, "grade": b.grade, "exposure": b.exposure, "supplier": one_line(b.supplier or "-"),
            "status": b.status, "status_label": status_label(b.status), "quantity_m3": b.quantity_m3, "required_samples": required_samples(b.quantity_m3),
            "tests": [{"type": t.test_type, "age_days": t.age_days, "status": core.effective_status(t)} for t in tests][:20],
            "locations_recorded": len(core.batch_locations(db, b.id))}


def batch_impact(db: Session, user: User, a: BatchCode) -> dict:
    b = _batch_by_code(db, user, a.batch_code)
    locs = core.batch_locations(db, b.id)
    return {"batch_code": b.batch_code, "status": b.status, "affected_count": len(locs),
            "affected_volume_m3": round(sum(loc["volume_m3"] or 0 for loc in locs), 2), "locations": [one_line(loc["path"], 120) for loc in locs[:15]]}


def list_batches(db: Session, user: User, a: ListBatchesArgs) -> dict:
    pids = accessible_project_ids(db, user)
    q = select(Batch).where(Batch.project_id.in_(pids))
    if a.status:
        q = q.where(Batch.status == a.status)
    rows = db.scalars(q.order_by(Batch.created_at.desc()).limit(a.limit)).all()
    return {"count": len(rows), "batches": [{"batch_code": b.batch_code, "grade": b.grade, "status": b.status, "supplier": one_line(b.supplier or "-")} for b in rows]}


def project_risk(db: Session, user: User, a: ProjectArg) -> dict:
    pids = [a.project_id] if a.project_id else accessible_project_ids(db, user)
    if a.project_id:
        ensure_access(db, user, a.project_id)
    latest: dict[str, RiskAssessment] = {}
    for r in db.scalars(select(RiskAssessment).where(RiskAssessment.project_id.in_(pids)).order_by(RiskAssessment.computed_at)):
        latest[r.element_id] = r
    top = sorted(latest.values(), key=lambda r: -r.score)[:a.limit]
    paths = core.element_paths(db, [r.element_id for r in top])
    return {"elements": [{"path": one_line(paths.get(r.element_id, {}).get("path", "?"), 120), "score": r.score, "level": r.level, "trend": r.trend,
                          "top_factors": [f["factor"] for f in (r.factors or [])[:3]]} for r in top]}


def supplier_scores(db: Session, user: User, a: ProjectArg) -> dict:
    pids = [a.project_id] if a.project_id else accessible_project_ids(db, user)
    if a.project_id:
        ensure_access(db, user, a.project_id)
    batches = db.scalars(select(Batch).where(Batch.project_id.in_(pids))).all()
    tests = db.scalars(select(TestRecord).where(TestRecord.project_id.in_(pids), TestRecord.is_current == True)).all()      # noqa: E712
    return {"suppliers": [{k: (one_line(v) if k == "supplier" else v) for k, v in s.items() if k != "note"} for s in dashboard.supplier_scorecards(batches, tests)[:a.limit]]}


@dataclass
class Tool:
    name: str
    description: str
    args: type[_Args]
    fn: Any | None                         # sync (db, user, args) -> dict, or None for the async knowledge search


def _schema(m: type[BaseModel]) -> dict:
    s = m.model_json_schema()
    s.pop("title", None)
    for p in s.get("properties", {}).values():
        p.pop("title", None)
    return s


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_batch", "Get status, grade, supplier, test results and sampling requirement for one batch by its code.", BatchCode, get_batch),
    Tool("batch_impact", "List where a batch was used (building/floor/element) and the affected volume. Use for flagged batches.", BatchCode, batch_impact),
    Tool("list_batches", "List recent batches the user can access, optionally filtered by status.", ListBatchesArgs, list_batches),
    Tool("project_risk", "Highest durability-risk elements (score, level, trend, main factors) for a project or all the user's projects.", ProjectArg, project_risk),
    Tool("supplier_scorecards", "Supplier reliability scorecards (batches, flagged rate, strength margin).", ProjectArg, supplier_scores),
    Tool("search_knowledge", "Search verified IS-code notes and the user's project documents.", KnowledgeArgs, None),
]}


def specs() -> list[ToolSpec]:
    return [ToolSpec(t.name, t.description, _schema(t.args)) for t in TOOLS.values()]


def _cap(payload: Any) -> str:
    s = json.dumps(payload, ensure_ascii=False, default=str)
    if len(s) <= MAX_RESULT_CHARS:
        return s
    return json.dumps({"truncated": True, "partial": s[:MAX_RESULT_CHARS - 60]})


async def run_tool(name: str, raw_args: dict, user: User) -> tuple[str, dict]:
    """Returns (json_text_for_the_model, audit_info). Never raises: failures become an error object the model can report."""
    tool = TOOLS.get(name)
    if tool is None:
        return _cap({"error": "unknown tool"}), {"tool": name, "ok": False, "reason": "unknown"}
    try:
        args = tool.args.model_validate(raw_args)
    except ValidationError:
        return _cap({"error": "invalid arguments"}), {"tool": name, "ok": False, "reason": "invalid_args"}
    try:
        if tool.fn is None:
            from . import rag
            pids = await in_db(accessible_project_ids, user)
            hits = await rag.search(args.query, pids, top_k=3, token_budget=900)              # type: ignore[attr-defined]
            result: dict = {"results": [{"label": h["label"], "reference": h["reference"], "title": h["title"], "text": h["text"][:500]} for h in hits]}
        else:
            fn = tool.fn
            result = await in_db(lambda db: fn(db, user, args))
        return _cap(result), {"tool": name, "ok": True}
    except HTTPException as e:
        return _cap({"error": "not found" if e.status_code == 404 else "access denied"}), {"tool": name, "ok": False, "reason": f"http_{e.status_code}"}
    except Exception:
        log.exception("tool failed", extra={"fields": {"tool": name}})
        return _cap({"error": "tool failed"}), {"tool": name, "ok": False, "reason": "error"}
