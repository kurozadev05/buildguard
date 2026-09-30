"""Read-only analytics that complement the rules engine: result-integrity screening and 7->28-day early warning."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AUDIT_ROLES, REVIEW_ROLES, current_user, ensure_access
from ..errors import EnvelopeRoute
from ..models import Batch, User
from ..services import core, integrity, predict

integrity_router = APIRouter(prefix="/integrity", tags=["Result integrity"], route_class=EnvelopeRoute)
predict_router = APIRouter(prefix="/predict", tags=["Early warning"], route_class=EnvelopeRoute)
SCREEN_ROLES = AUDIT_ROLES | REVIEW_ROLES


@integrity_router.get("/projects/{project_id}")
def project_scan(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Screen all cube results in a project for impossible timelines, copied values, reused evidence files and implausible scatter."""
    ensure_access(db, user, project_id, SCREEN_ROLES)
    return integrity.summarize(integrity.scan_project(db, project_id))


@integrity_router.get("/batches/{batch_id}")
def batch_scan(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = core.get_or_404(db, Batch, batch_id, "Batch")
    ensure_access(db, user, b.project_id, SCREEN_ROLES)
    return integrity.summarize([f for f in integrity.scan_project(db, b.project_id) if any(r["batch_code"] == b.batch_code for r in f["tests"])])


@predict_router.get("/projects/{project_id}")
def project_predictions(project_id: str, status: str | None = Query(None, pattern="^(ON_TRACK|WATCH|AT_RISK)$"),
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Samples with a 7-day result but no 28-day result yet, with an estimated 28-day strength and range."""
    ensure_access(db, user, project_id)
    rows = predict.predict_project(db, project_id)
    return {"predictions": [r for r in rows if not status or r["status"] == status], "estimate_only": True}


@predict_router.get("/batches/{batch_id}")
def batch_predictions(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = core.get_or_404(db, Batch, batch_id, "Batch")
    ensure_access(db, user, b.project_id)
    return {"predictions": predict.predict_project(db, b.project_id, b.id), "estimate_only": True}
