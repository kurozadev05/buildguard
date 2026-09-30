from sqlalchemy.orm import Session

from ..models import ChangeLog
from ..utils import sanitize, to_dict


def add(db: Session, project_id: str, entity: str, entity_id: str, op: str, obj=None, extra: dict | None = None):
    payload = to_dict(obj) if obj is not None else {}
    if extra:
        payload.update(extra)
    db.add(ChangeLog(project_id=project_id, entity=entity, entity_id=entity_id, op=op,
                     payload=sanitize(payload)))
