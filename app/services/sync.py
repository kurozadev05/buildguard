"""Offline-first sync.

Push: the phone queues operations while offline (each with a client-generated op_id and,
for creates, a client-generated UUID `id`). On reconnect it POSTs them in order. The server
applies each inside a SAVEPOINT, is idempotent per op_id (retries are safe), and returns a
per-op result. Pull: clients fetch everything changed since their cursor.
Conflict policy: creates are idempotent by id; corrections are new append-only versions, so
there is no last-writer-wins overwrite of test evidence. Server always assigns batch/sample codes.
"""
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import accessible_project_ids
from ..models import ChangeLog, SyncOp, User
from ..schemas import BatchIn, ObservationIn, SampleIn, SyncOpIn, TestIn, UsageIn
from . import audit, core

_HANDLERS: dict[str, tuple[Any, Any, Any]] = {
    "batch.create": (BatchIn, core.create_batch, lambda o: {"batch_code": o.batch_code, "entity_status": o.status}),
    "sample.create": (SampleIn, core.create_sample, lambda o: {"sample_code": o.sample_code}),
    "test.create": (TestIn, core.create_test, lambda o: {"entity_status": o.status, "record_hash": o.record_hash}),
    "usage.create": (UsageIn, core.add_usage, lambda o: {}),
    "observation.create": (ObservationIn, core.add_observation, lambda o: {}),
}


def apply_op(db: Session, user: User, device_id: str | None, op: SyncOpIn) -> dict:
    prior = db.get(SyncOp, (op.op_id, user.id))
    if prior:
        return {**prior.result, "op_id": op.op_id, "status": "duplicate"}
    schema, fn, summarise = _HANDLERS[op.type]
    mark = audit.marker(db)
    try:
        data = schema.model_validate(op.data)
        with db.begin_nested():
            obj = fn(db, user, data)
            audit.stage(db, user, "sync.apply", op.type, obj.id, getattr(obj, "project_id", None),
                        {"op_id": op.op_id, "device_id": device_id,
                         "client_ts": op.client_ts.isoformat() if op.client_ts else None})
            result = {"op_id": op.op_id, "status": "applied", "entity_id": obj.id, **summarise(obj)}
            db.add(SyncOp(op_id=op.op_id, user_id=user.id, device_id=device_id, op_type=op.type, result=result))
        return result
    except ValidationError as exc:
        audit.truncate(db, mark)
        return {"op_id": op.op_id, "status": "rejected", "error": "validation", "detail": exc.errors(include_url=False, include_context=False)}
    except HTTPException as exc:
        audit.truncate(db, mark)
        status = "conflict" if exc.status_code == 409 else "rejected"
        return {"op_id": op.op_id, "status": status, "error": exc.detail, "http_status": exc.status_code}
    except IntegrityError as exc:
        audit.truncate(db, mark)
        return {"op_id": op.op_id, "status": "rejected", "error": "integrity", "detail": str(exc.orig)[:200]}


def push(db: Session, user: User, device_id: str | None, ops: list[SyncOpIn]) -> dict:
    results = [apply_op(db, user, device_id, op) for op in ops]
    audit.commit(db)
    return {"results": results, "applied": sum(r["status"] == "applied" for r in results),
            "duplicates": sum(r["status"] == "duplicate" for r in results),
            "rejected": sum(r["status"] in ("rejected", "conflict") for r in results),
            "server_cursor": current_cursor(db)}


def current_cursor(db: Session) -> int:
    return db.scalar(select(func.coalesce(func.max(ChangeLog.seq), 0))) or 0


def pull(db: Session, user: User, since: int, project_id: str | None, limit: int = 500) -> dict:
    pids = accessible_project_ids(db, user)
    if project_id:
        if project_id not in pids:
            raise HTTPException(403, "Not a member of this project")
        pids = [project_id]
    if not pids:
        return {"changes": [], "next_cursor": since, "has_more": False}
    rows = db.scalars(select(ChangeLog).where(ChangeLog.seq > since, ChangeLog.project_id.in_(pids))
                      .order_by(ChangeLog.seq).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "changes": [{"seq": r.seq, "entity": r.entity, "entity_id": r.entity_id, "op": r.op,
                     "project_id": r.project_id, "ts": r.ts.isoformat() + "Z", "data": r.payload} for r in rows],
        "next_cursor": rows[-1].seq if rows else since,
        "has_more": more,
    }
