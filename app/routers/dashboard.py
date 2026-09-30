from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..deps import REVIEW_ROLES, accessible_project_ids, current_user, ensure_access
from ..models import Alert, User, utcnow
from ..services import audit, core, dashboard
from ..utils import to_dict

router = APIRouter(tags=["dashboard & alerts"], route_class=EnvelopeRoute)


@router.get("/dashboard/summary")
def summary(project_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One-call dashboard: statuses, pending/overdue tests, sampling shortfall, missing evidence, alerts, risk, supplier scores."""
    if project_id:
        ensure_access(db, user, project_id)
        return dashboard.summary(db, [project_id])
    return dashboard.summary(db, accessible_project_ids(db, user))


@router.get("/dashboard/suppliers")
def suppliers(project_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import Batch, TestRecord
    pids = [project_id] if project_id else accessible_project_ids(db, user)
    if project_id:
        ensure_access(db, user, project_id)
    batches = db.scalars(select(Batch).where(Batch.project_id.in_(pids))).all()
    tests = db.scalars(select(TestRecord).where(TestRecord.project_id.in_(pids), TestRecord.is_current == True)).all()  # noqa: E712
    return dashboard.supplier_scorecards(batches, tests)


@router.get("/alerts")
def alerts(project_id: str | None = None, unacknowledged: bool = False, limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pids = [project_id] if project_id else accessible_project_ids(db, user)
    if project_id:
        ensure_access(db, user, project_id)
    q = select(Alert).where(Alert.project_id.in_(pids))
    if unacknowledged:
        q = q.where(Alert.acknowledged_at == None)  # noqa: E711
    return [to_dict(a) for a in db.scalars(q.order_by(Alert.created_at.desc()).limit(min(limit, 500)))]


@router.post("/alerts/{alert_id}/ack")
def ack(alert_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = core.get_or_404(db, Alert, alert_id, "Alert")
    ensure_access(db, user, a.project_id, REVIEW_ROLES)
    a.acknowledged_by, a.acknowledged_at = user.id, utcnow()
    audit.stage(db, user, "alert.ack", "alert", a.id, a.project_id, {})
    audit.commit(db)
    return to_dict(a)
