from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..deps import current_user, ensure_access
from ..models import Batch, Building, Element, Floor, Project
from ..models import User
from ..schemas import SyncPushIn
from ..services import sync as sync_svc
from ..services.rules_engine import RULES
from ..utils import to_dict

router = APIRouter(prefix="/sync", tags=["offline sync"], route_class=EnvelopeRoute)


@router.get("/bootstrap")
def bootstrap(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Download once while online: hierarchy, open batches, IS rules and a cursor. Lets the app work offline."""
    ensure_access(db, user, project_id)
    return {
        "project": to_dict(db.get(Project, project_id)),
        "buildings": [to_dict(x) for x in db.scalars(select(Building).where(Building.project_id == project_id))],
        "floors": [to_dict(x) for x in db.scalars(select(Floor).where(Floor.project_id == project_id))],
        "elements": [to_dict(x) for x in db.scalars(select(Element).where(Element.project_id == project_id))],
        "batches": [to_dict(x) for x in db.scalars(select(Batch).where(Batch.project_id == project_id))],
        "rules": {k: v for k, v in RULES.items() if k != "knowledge"},
        "cursor": sync_svc.current_cursor(db),
        "instructions": "Queue writes offline with op_id + client UUIDs, POST them to /api/sync/push, then GET /api/sync/pull?since=<cursor>.",
    }


@router.post("/push")
def push(body: SyncPushIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Apply queued offline operations (idempotent per op_id; each op succeeds or fails independently)."""
    return sync_svc.push(db, user, body.device_id, body.ops)


@router.get("/pull")
def pull(since: int = 0, project_id: str | None = None, limit: int = 500, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return sync_svc.pull(db, user, since, project_id, min(limit, 1000))
