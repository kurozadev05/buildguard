from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..deps import AUDIT_ROLES, accessible_project_ids, ensure_access, require_roles
from ..models import AuditLog, User
from ..services import audit

router = APIRouter(prefix="/audit", tags=["audit trail"], route_class=EnvelopeRoute)


@router.get("")
def list_audit(project_id: str | None = None, entity: str | None = None, entity_id: str | None = None, action: str | None = None,
               limit: int = 100, offset: int = 0, user: User = Depends(require_roles(*AUDIT_ROLES)), db: Session = Depends(get_db)):
    pids = [project_id] if project_id else accessible_project_ids(db, user)
    if project_id:
        ensure_access(db, user, project_id)
    q = select(AuditLog).where(AuditLog.project_id.in_(pids))
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if action:
        q = q.where(AuditLog.action == action)
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(min(limit, 500)).offset(offset)).all()
    return [{"id": r.id, "ts": r.ts.isoformat() + "Z", "actor": r.actor_email, "action": r.action, "entity": r.entity,
             "entity_id": r.entity_id, "project_id": r.project_id, "details": r.details,
             "prev_hash": r.prev_hash, "entry_hash": r.entry_hash} for r in rows]


@router.get("/verify")
def verify(user: User = Depends(require_roles(*AUDIT_ROLES)), db: Session = Depends(get_db)):
    """Re-computes the whole hash chain. Any edited/deleted past entry is detected and located."""
    return audit.verify_chain(db)
