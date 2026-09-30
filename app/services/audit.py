"""Tamper-evident audit trail.

Every mutation stages an audit entry; `commit()` chains the staged entries
(entry_hash = SHA256(prev_hash + canonical(entry))) and commits them in the SAME
transaction as the change itself. Editing or deleting any past row breaks the chain,
which `verify_chain()` detects. Run a single API worker (see README) so the chain
cannot fork.
"""
import threading

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import AuditLog, User, utcnow
from ..utils import canonical, sanitize, sha256_hex

GENESIS = "0" * 64
_lock = threading.Lock()


def stage(db: Session, user: User | None, action: str, entity: str, entity_id: str | None = None,
          project_id: str | None = None, details: dict | None = None) -> None:
    db.info.setdefault("audit_pending", []).append({
        "ts": utcnow(),
        "actor_id": user.id if user else None,
        "actor_email": user.email if user else None,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "project_id": project_id,
        "details": sanitize(details or {}),
    })


def marker(db: Session) -> int:
    return len(db.info.get("audit_pending", []))


def truncate(db: Session, n: int) -> None:
    del db.info.get("audit_pending", [])[n:]


def _payload(e: dict) -> dict:
    return {
        "ts": e["ts"].isoformat(), "actor_id": e["actor_id"], "actor_email": e["actor_email"],
        "action": e["action"], "entity": e["entity"], "entity_id": e["entity_id"],
        "project_id": e["project_id"], "details": e["details"],
    }


def commit(db: Session) -> None:
    pending = db.info.pop("audit_pending", [])
    with _lock:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(727001)"))   # serialises the chain across workers/processes
        prev = db.scalar(select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1)) or GENESIS
        for e in pending:
            h = sha256_hex(prev + canonical(_payload(e)))
            db.add(AuditLog(ts=e["ts"], actor_id=e["actor_id"], actor_email=e["actor_email"],
                            action=e["action"], entity=e["entity"], entity_id=e["entity_id"],
                            project_id=e["project_id"], details=e["details"],
                            prev_hash=prev, entry_hash=h))
            prev = h
        db.commit()


def verify_chain(db: Session) -> dict:
    """Recompute the chain in one streaming pass (constant memory)."""
    prev, count = GENESIS, 0
    q = select(AuditLog.id, AuditLog.ts, AuditLog.actor_id, AuditLog.actor_email, AuditLog.action, AuditLog.entity, AuditLog.entity_id,
               AuditLog.project_id, AuditLog.details, AuditLog.prev_hash, AuditLog.entry_hash).order_by(AuditLog.id)
    for r in db.execute(q.execution_options(yield_per=2000)):
        payload = _payload({"ts": r.ts, "actor_id": r.actor_id, "actor_email": r.actor_email, "action": r.action, "entity": r.entity,
                            "entity_id": r.entity_id, "project_id": r.project_id, "details": r.details})
        if r.prev_hash != prev or r.entry_hash != sha256_hex(prev + canonical(payload)):
            return {"ok": False, "checked": count, "first_broken_id": r.id,
                    "message": "Audit chain broken: a past entry was modified, removed or reordered."}
        prev, count = r.entry_hash, count + 1
    return {"ok": True, "checked": count, "head_hash": prev, "message": "Audit chain intact."}
