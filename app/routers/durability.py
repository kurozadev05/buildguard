from datetime import datetime
from typing import Annotated

from pydantic import Field

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n
from ..errors import EnvelopeRoute
from ..database import get_db
from ..deps import WRITE_ROLES, current_user, ensure_access, lang_dep
from ..models import Element, Observation, RiskAssessment, User
from ..schemas import ObservationIn
from ..services import audit, core, risk
from ..utils import to_dict

router = APIRouter(tags=["durability intelligence"], route_class=EnvelopeRoute)


@router.post("/observations", status_code=201)
def add_observation(body: ObservationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Record an inspection/NDT/environment reading against an element."""
    o = core.add_observation(db, user, body)
    audit.commit(db)
    return to_dict(o)


@router.post("/observations/bulk", status_code=201)
def add_bulk(body: Annotated[list[ObservationIn], Field(max_length=200)], user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = [core.add_observation(db, user, o) for o in body]
    audit.commit(db)
    return {"created": len(out)}


@router.get("/elements/{element_id}/observations")
def observations(element_id: str, kind: str | None = None, limit: int = Query(500, ge=1, le=2000), user: User = Depends(current_user), db: Session = Depends(get_db)):
    el = core.get_or_404(db, Element, element_id, "Element")
    ensure_access(db, user, el.project_id)
    q = select(Observation).where(Observation.element_id == el.id)
    if kind:
        q = q.where(Observation.kind == kind)
    return [to_dict(o) for o in db.scalars(q.order_by(Observation.observed_at).limit(limit))]


@router.post("/durability/elements/{element_id}/assess")
def assess(element_id: str, as_of: datetime | None = None, persist: bool = True, user: User = Depends(current_user),
           db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Compute the durability/performance RISK for an element (transparent factors, anomalies, trend, recommendations)."""
    el = core.get_or_404(db, Element, element_id, "Element")
    ensure_access(db, user, el.project_id, WRITE_ROLES if persist else None)
    if as_of is not None and user.role != "admin":
        raise HTTPException(403, "Only admins may compute assessments as of a past date")
    result = risk.assess_element(db, user, el, as_of=as_of, persist=persist)
    if persist:
        audit.commit(db)
    result["level_label"] = i18n.risk_label(result["level"], lang)
    result["path"] = core.element_path(db, el)
    return result


@router.get("/durability/elements/{element_id}/history")
def history(element_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    el = core.get_or_404(db, Element, element_id, "Element")
    ensure_access(db, user, el.project_id)
    rows = db.scalars(select(RiskAssessment).where(RiskAssessment.element_id == el.id).order_by(RiskAssessment.computed_at)).all()
    return {"element": el.name, "path": core.element_path(db, el),
            "history": [{"computed_at": r.computed_at.isoformat() + "Z", "score": r.score, "level": r.level, "trend": r.trend} for r in rows]}


@router.get("/durability/projects/{project_id}/risk-map")
def risk_map(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_access(db, user, project_id)
    latest = {}
    for r in db.scalars(select(RiskAssessment).where(RiskAssessment.project_id == project_id).order_by(RiskAssessment.computed_at)):
        latest[r.element_id] = r
    out = []
    for eid, r in latest.items():
        el = core.get_or_404(db, Element, eid, "Element")
        out.append({"element_id": eid, "path": core.element_path(db, el), "score": r.score, "level": r.level, "trend": r.trend,
                    "top_factors": (r.factors or [])[:3], "recommendations": r.recommendations})
    return sorted(out, key=lambda x: -x["score"])
