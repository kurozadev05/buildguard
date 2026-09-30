from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import EnvelopeRoute
from ..deps import REVIEW_ROLES, accessible_project_ids, current_user, ensure_access
from ..models import Batch, Investigation, InvestigationAction, User, utcnow
from ..schemas import ActionPatch, CloseIn, InvestigationIn
from ..services import audit, changes, core
from ..utils import to_dict

router = APIRouter(tags=["flagged-batch investigation"], route_class=EnvelopeRoute)


def _outs(db: Session, invs: list[Investigation]) -> list[dict]:
    """Serialise many investigations with a fixed number of queries (actions, batches, element paths)."""
    if not invs:
        return []
    ids = [i.id for i in invs]
    acts = db.scalars(select(InvestigationAction).where(InvestigationAction.investigation_id.in_(ids))
                      .order_by(InvestigationAction.stage, InvestigationAction.action_type)).all()
    by_inv: dict[str, list] = {}
    for a in acts:
        by_inv.setdefault(a.investigation_id, []).append(a)
    codes = dict(db.execute(select(Batch.id, Batch.batch_code).where(Batch.id.in_({i.batch_id for i in invs}))).all())
    paths = core.element_paths(db, [a.element_id for a in acts])
    out = []
    for inv in invs:
        al = by_inv.get(inv.id, [])
        out.append({**to_dict(inv), "batch_code": codes.get(inv.batch_id),
                    "actions": [{**to_dict(a), "path": paths.get(a.element_id, {}).get("path") if a.element_id else None} for a in al],
                    "progress": {"done": sum(a.status in ("done", "waived") for a in al), "total": len(al)}})
    return out


def _out(db: Session, inv: Investigation) -> dict:
    return _outs(db, [inv])[0]


@router.get("/investigations")
def list_investigations(status: str | None = None, batch_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Investigation).where(Investigation.project_id.in_(accessible_project_ids(db, user)))
    if status:
        q = q.where(Investigation.status == status)
    if batch_id:
        q = q.where(Investigation.batch_id == batch_id)
    return _outs(db, list(db.scalars(q.order_by(Investigation.opened_at.desc()).limit(limit).offset(offset))))


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = core.get_or_404(db, Investigation, inv_id, "Investigation")
    ensure_access(db, user, inv.project_id)
    return _out(db, inv)


@router.post("/investigations", status_code=201)
def open_manual(body: InvestigationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = core.get_or_404(db, Batch, body.batch_id, "Batch")
    ensure_access(db, user, b.project_id, REVIEW_ROLES)
    inv = core.open_investigation(db, user, b, None, body.reason)
    audit.commit(db)
    return _out(db, inv)


@router.patch("/investigations/actions/{action_id}")
def patch_action(action_id: str, body: ActionPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = core.get_or_404(db, InvestigationAction, action_id, "Action")
    inv = core.get_or_404(db, Investigation, a.investigation_id, "Investigation")
    ensure_access(db, user, inv.project_id, REVIEW_ROLES | {"lab"})
    if inv.status == "closed":
        raise HTTPException(409, "Investigation is closed")
    a.status, a.note, a.result_test_id = body.status, body.note, body.result_test_id or a.result_test_id
    a.completed_at = utcnow() if body.status != "pending" else None
    if inv.status == "open" and body.status != "pending":
        inv.status = "in_progress"
    changes.add(db, inv.project_id, "investigation", inv.id, "update", inv)
    audit.stage(db, user, "investigation.action", "investigation_action", a.id, inv.project_id, {"status": a.status, "type": a.action_type, "note": a.note})
    audit.commit(db)
    return _out(db, inv)


@router.post("/investigations/{inv_id}/close")
def close(inv_id: str, body: CloseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = core.get_or_404(db, Investigation, inv_id, "Investigation")
    core.close_investigation(db, user, inv, body.closure_note)
    audit.commit(db)
    return _out(db, inv)
