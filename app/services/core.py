"""Domain logic shared by the REST routers, the offline-sync endpoint and the demo seeder.

Functions here mutate the session and stage audit entries but never commit; the caller
finishes with `audit.commit(db)` so the change and its audit record are one transaction.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import TEST_ROLES, WRITE_ROLES, REVIEW_ROLES, ensure_access
from ..models import (Alert, Batch, BatchUsage, Building, Element, Floor, Investigation,
                      InvestigationAction, Observation, Project, Sample, TestRecord, User, uid, utcnow)
from ..schemas import BatchIn, ObservationIn, SampleIn, TestIn, UsageIn
from ..utils import canonical, iso, sha256_hex
from . import audit, changes, rules_engine as re_


# ───────────────────────── helpers ─────────────────────────
def get_or_404[T](db: Session, model: type[T], id_: str, label: str | None = None) -> T:
    obj = db.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{label or model.__name__} not found")
    return obj


def effective_status(t: TestRecord) -> str:
    """A recorded engineer review overrides the automatic status."""
    if t.review:
        return "VERIFIED" if t.review.get("decision") == "accepted" else "FLAGGED"
    return t.status


def raise_alert(db: Session, project_id: str, kind: str, severity: str, message: str, *,
                batch_id=None, element_id=None, test_id=None) -> Alert:
    a = Alert(project_id=project_id, kind=kind, severity=severity, message=message,
              batch_id=batch_id, element_id=element_id, test_id=test_id)
    db.add(a)
    db.flush()
    changes.add(db, project_id, "alert", a.id, "create", a)
    return a


def test_record_hash(t: TestRecord) -> str:
    """Seal over the evidence fields (not over status/review, which may legitimately change)."""
    return sha256_hex(canonical({
        "id": t.id, "batch_id": t.batch_id, "sample_id": t.sample_id, "test_type": t.test_type,
        "age_days": t.age_days, "values": t.values, "tested_at": iso(t.tested_at), "lab_name": t.lab_name,
        "created_by": t.created_by, "created_at": iso(t.created_at), "version": t.version,
        "supersedes_id": t.supersedes_id,
    }))


# ───────────────────────── location paths ─────────────────────────
def element_paths(db: Session, element_ids) -> dict[str, dict]:
    """Element id -> {path, element, floor, building, element_type} in ONE query (no per-row lookups)."""
    ids = list({i for i in element_ids if i})
    if not ids:
        return {}
    rows = db.execute(
        select(Element.id, Element.name, Element.element_type, Floor.name, Building.name, Project.name)
        .join(Floor, Floor.id == Element.floor_id).join(Building, Building.id == Floor.building_id)
        .join(Project, Project.id == Element.project_id).where(Element.id.in_(ids))).all()
    return {r[0]: {"element": r[1], "element_type": r[2], "floor": r[3], "building": r[4],
                   "path": f"{r[5]} → {r[4]} → {r[3]} → {r[1]}"} for r in rows}


def element_path(db: Session, element: Element) -> str:
    return element_paths(db, [element.id]).get(element.id, {}).get("path", element.name)


def batch_locations(db: Session, batch_id: str) -> list[dict]:
    usages = db.scalars(select(BatchUsage).where(BatchUsage.batch_id == batch_id).order_by(BatchUsage.poured_at)).all()
    paths = element_paths(db, [u.element_id for u in usages])
    out = []
    for u in usages:
        p = paths.get(u.element_id)
        if not p:
            continue
        out.append({"usage_id": u.id, "element_id": u.element_id, "element": p["element"], "element_type": p["element_type"],
                    "floor": p["floor"], "building": p["building"], "path": p["path"],
                    "volume_m3": u.volume_m3, "poured_at": u.poured_at.isoformat() + "Z"})
    return out


# ───────────────────────── batches ─────────────────────────
def next_batch_code(db: Session, grade: str) -> str:
    prefix = f"CON-{grade.upper()}-"
    codes = db.scalars(select(Batch.batch_code).where(Batch.batch_code.like(prefix + "%"))).all()
    nums = [int(c.rsplit("-", 1)[1]) for c in codes if c.rsplit("-", 1)[1].isdigit()]
    return f"{prefix}{(max(nums) if nums else 0) + 1:03d}"


def recompute_batch_status(db: Session, batch: Batch) -> str:
    tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == batch.id, TestRecord.is_current == True)).all()  # noqa: E712
    reg = (batch.registration_check or {}).get("status", "VERIFIED")
    sts = [reg] + [effective_status(t) for t in tests]
    if "FLAGGED" in sts:
        new = "FLAGGED"
    elif "REVIEW_REQUIRED" in sts:
        new = "REVIEW_REQUIRED"
    elif any(t.test_type == "cube_compressive_strength" and t.age_days == 28 and effective_status(t) == "VERIFIED" for t in tests):
        new = "VERIFIED"
    else:
        new = "PENDING"
    batch.status = new
    return new


def create_batch(db: Session, user: User, data: BatchIn) -> Batch:
    ensure_access(db, user, data.project_id, WRITE_ROLES)
    if data.id and db.get(Batch, data.id):
        raise HTTPException(409, "Batch id already exists")
    grade = data.grade.upper()
    b = Batch(project_id=data.project_id, grade=grade, fck=re_.fck_of(grade), construction_type=data.construction_type,
              exposure=data.exposure, supplier=data.supplier, supplier_ref=data.supplier_ref,
              delivery_date=data.delivery_date, quantity_m3=data.quantity_m3, cement_type=data.cement_type,
              cement_content_kg_m3=data.cement_content_kg_m3, wc_ratio=data.wc_ratio, admixture=data.admixture,
              mix_notes=data.mix_notes, created_by=user.id, batch_code=next_batch_code(db, grade))
    if data.id:
        b.id = data.id
    status, outs = re_.registration_check(b)
    b.registration_check = {"status": status, "outcomes": outs}
    b.status = "PENDING" if status == "VERIFIED" else status
    db.add(b)
    db.flush()
    changes.add(db, b.project_id, "batch", b.id, "create", b)
    audit.stage(db, user, "batch.create", "batch", b.id, b.project_id, {"batch_code": b.batch_code, "grade": b.grade, "status": b.status})
    if status != "VERIFIED":
        bad = [o for o in outs if o["status"] != "VERIFIED"]
        sev = "high" if status == "FLAGGED" else "medium"
        raise_alert(db, b.project_id, "registration", sev,
                    f"Batch {b.batch_code}: " + "; ".join(o["explanation"] for o in bad), batch_id=b.id)
    return b


def create_sample(db: Session, user: User, data: SampleIn) -> Sample:
    batch = get_or_404(db, Batch, data.batch_id, "Batch")
    ensure_access(db, user, batch.project_id, TEST_ROLES)
    if data.id and db.get(Sample, data.id):
        raise HTTPException(409, "Sample id already exists")
    n = len(db.scalars(select(Sample.id).where(Sample.batch_id == batch.id)).all()) + 1
    code = f"{batch.batch_code}-S{n}"
    while db.scalar(select(Sample.id).where(Sample.sample_code == code)):
        n += 1
        code = f"{batch.batch_code}-S{n}"
    s = Sample(project_id=batch.project_id, batch_id=batch.id, sample_code=code, cast_date=data.cast_date,
               cubes_cast=data.cubes_cast, curing_method=data.curing_method, notes=data.notes, created_by=user.id)
    if data.id:
        s.id = data.id
    db.add(s)
    db.flush()
    changes.add(db, s.project_id, "sample", s.id, "create", s)
    audit.stage(db, user, "sample.create", "sample", s.id, s.project_id, {"sample_code": code, "batch": batch.batch_code})
    return s


# ───────────────────────── tests ─────────────────────────
def _prior_28d_means(db: Session, batch: Batch, before: datetime, exclude_id: str | None = None) -> list[float]:
    rows = db.scalars(
        select(TestRecord).join(Batch, Batch.id == TestRecord.batch_id)
        .where(Batch.project_id == batch.project_id, Batch.grade == batch.grade,
               TestRecord.test_type == "cube_compressive_strength", TestRecord.age_days == 28,
               TestRecord.is_current == True, TestRecord.created_at <= before)  # noqa: E712
        .order_by(TestRecord.created_at, TestRecord.id)
    ).all()
    out = []
    for r in rows:
        if exclude_id and r.id == exclude_id:
            continue
        m = re_.sample_mean(r.values)
        if m is not None:
            out.append(m)
    return out


def _after_status(db: Session, user: User, batch: Batch, t: TestRecord, prev_batch_status: str) -> None:
    """Alerts + automatic investigation when a result needs attention."""
    eff = effective_status(t)
    if eff == "FLAGGED":
        raise_alert(db, t.project_id, "flagged_batch", "high",
                    f"Batch {batch.batch_code}: {t.test_type} {'(' + str(t.age_days) + 'd) ' if t.age_days else ''}FLAGGED - "
                    + "; ".join(o["explanation"] for o in t.validation if o["status"] == "FLAGGED"),
                    batch_id=batch.id, test_id=t.id)
        open_investigation(db, user, batch, t)
    elif eff == "REVIEW_REQUIRED":
        raise_alert(db, t.project_id, "review_required", "medium",
                    f"Batch {batch.batch_code}: {t.test_type} needs engineer review - "
                    + "; ".join(o["explanation"] for o in t.validation if o["status"] == "REVIEW_REQUIRED"),
                    batch_id=batch.id, test_id=t.id)


def create_test(db: Session, user: User, data: TestIn) -> TestRecord:
    batch = get_or_404(db, Batch, data.batch_id, "Batch")
    ensure_access(db, user, batch.project_id, TEST_ROLES)
    if data.id and db.get(TestRecord, data.id):
        raise HTTPException(409, "Test id already exists")
    if data.sample_id:
        s = get_or_404(db, Sample, data.sample_id, "Sample")
        if s.batch_id != batch.id:
            raise HTTPException(422, "Sample does not belong to this batch")
    now = utcnow()
    prior = _prior_28d_means(db, batch, now) if data.test_type == "cube_compressive_strength" and data.age_days == 28 else None
    status, outs = re_.evaluate(data.test_type, data.values, data.age_days, batch, prior)
    t = TestRecord(project_id=batch.project_id, batch_id=batch.id, sample_id=data.sample_id, test_type=data.test_type,
                   age_days=data.age_days, values=data.values, lab_name=data.lab_name, notes=data.notes,
                   tested_at=data.tested_at or now, status=status, validation=outs, version=1, supersedes_id=None,
                   created_by=user.id, created_at=now)
    t.id = data.id or uid()
    t.record_hash = test_record_hash(t)
    prev = batch.status
    db.add(t)
    db.flush()
    changes.add(db, t.project_id, "test", t.id, "create", t)
    audit.stage(db, user, "test.create", "test", t.id, t.project_id,
                {"batch": batch.batch_code, "type": t.test_type, "age_days": t.age_days, "status": status, "record_hash": t.record_hash})
    recompute_batch_status(db, batch)
    changes.add(db, batch.project_id, "batch", batch.id, "update", batch)
    _after_status(db, user, batch, t, prev)
    if data.investigation_action_id:
        act = db.get(InvestigationAction, data.investigation_action_id)
        inv = db.get(Investigation, act.investigation_id) if act else None
        if act and inv and inv.project_id == t.project_id:
            act.status, act.result_test_id, act.completed_at = "done", t.id, utcnow()
            audit.stage(db, user, "investigation.action_done", "investigation_action", act.id, t.project_id, {"result_test": t.id})
    return t


def amend_test(db: Session, user: User, old: TestRecord, values: dict, age_days: int | None, reason: str) -> TestRecord:
    ensure_access(db, user, old.project_id, TEST_ROLES)
    if not old.is_current:
        raise HTTPException(409, "Only the current version of a test can be amended")
    batch = get_or_404(db, Batch, old.batch_id)
    age = age_days if age_days is not None else old.age_days
    now = utcnow()
    prior = _prior_28d_means(db, batch, now, exclude_id=old.id) if old.test_type == "cube_compressive_strength" and age == 28 else None
    status, outs = re_.evaluate(old.test_type, values, age, batch, prior)
    t = TestRecord(id=uid(), project_id=old.project_id, batch_id=old.batch_id,
                   sample_id=old.sample_id, test_type=old.test_type, age_days=age, values=values, lab_name=old.lab_name,
                   notes=f"Amendment: {reason}", tested_at=old.tested_at, status=status, validation=outs,
                   version=old.version + 1, supersedes_id=old.id, created_by=user.id, created_at=now)
    t.record_hash = test_record_hash(t)
    old.is_current = False
    db.add(t)
    db.flush()
    changes.add(db, t.project_id, "test", old.id, "update", old)
    changes.add(db, t.project_id, "test", t.id, "create", t)
    audit.stage(db, user, "test.amend", "test", t.id, t.project_id,
                {"supersedes": old.id, "reason": reason, "old_status": old.status, "new_status": status, "record_hash": t.record_hash})
    recompute_batch_status(db, batch)
    changes.add(db, batch.project_id, "batch", batch.id, "update", batch)
    _after_status(db, user, batch, t, batch.status)
    return t


def review_test(db: Session, user: User, t: TestRecord, decision: str, comment: str) -> TestRecord:
    ensure_access(db, user, t.project_id, REVIEW_ROLES)
    t.review = {"decision": decision, "comment": comment, "by": user.id, "by_name": user.full_name, "at": utcnow().isoformat() + "Z"}
    batch = get_or_404(db, Batch, t.batch_id, "Batch")
    recompute_batch_status(db, batch)
    changes.add(db, t.project_id, "test", t.id, "update", t)
    changes.add(db, t.project_id, "batch", batch.id, "update", batch)
    audit.stage(db, user, "test.review", "test", t.id, t.project_id, {"decision": decision, "comment": comment, "auto_status": t.status})
    if effective_status(t) == "FLAGGED" and t.status != "FLAGGED":
        open_investigation(db, user, batch, t)
    return t


# ───────────────────────── traceability ─────────────────────────
def add_usage(db: Session, user: User, data: UsageIn) -> BatchUsage:
    batch = get_or_404(db, Batch, data.batch_id, "Batch")
    el = get_or_404(db, Element, data.element_id, "Element")
    if batch.project_id != el.project_id:
        raise HTTPException(422, "Batch and element belong to different projects")
    ensure_access(db, user, batch.project_id, WRITE_ROLES)
    if data.id and db.get(BatchUsage, data.id):
        raise HTTPException(409, "Usage id already exists")
    u = BatchUsage(project_id=batch.project_id, batch_id=batch.id, element_id=el.id, volume_m3=data.volume_m3,
                   poured_at=data.poured_at or utcnow(), notes=data.notes, created_by=user.id)
    if data.id:
        u.id = data.id
    db.add(u)
    db.flush()
    changes.add(db, u.project_id, "usage", u.id, "create", u)
    audit.stage(db, user, "usage.create", "usage", u.id, u.project_id,
                {"batch": batch.batch_code, "element": el.name, "volume_m3": data.volume_m3})
    if batch.status == "FLAGGED":
        raise_alert(db, batch.project_id, "flagged_batch", "high",
                    f"Flagged batch {batch.batch_code} recorded as used in {element_path(db, el)}.", batch_id=batch.id, element_id=el.id)
    return u


def add_observation(db: Session, user: User, data: ObservationIn) -> Observation:
    el = get_or_404(db, Element, data.element_id, "Element")
    ensure_access(db, user, el.project_id, WRITE_ROLES | {"lab"})
    if data.id and db.get(Observation, data.id):
        raise HTTPException(409, "Observation id already exists")
    o = Observation(project_id=el.project_id, element_id=el.id, kind=data.kind, value=data.value, unit=data.unit,
                    observed_at=data.observed_at or utcnow(), notes=data.notes, created_by=user.id)
    if data.id:
        o.id = data.id
    db.add(o)
    db.flush()
    changes.add(db, o.project_id, "observation", o.id, "create", o)
    audit.stage(db, user, "observation.create", "observation", o.id, o.project_id, {"element": el.name, "kind": o.kind, "value": o.value})
    return o


# ───────────────────────── investigations ─────────────────────────
def _suggest_actions(db: Session, batch: Batch, t: TestRecord | None) -> list[dict]:
    locs = batch_locations(db, batch.id)
    ttype = t.test_type if t else "cube_compressive_strength"
    acts = [{"stage": 0, "action_type": "quarantine_batch", "element_id": None, "reference": "Site QA procedure",
             "description": f"Hold further use of batch {batch.batch_code} until the engineer decides. Do not cast more elements with it."},
            {"stage": 0, "action_type": "engineer_review", "element_id": None, "reference": "IS 456:2000 Cl. 17",
             "description": "Responsible engineer reviews the failing evidence, records, curing and testing conditions."}]
    if ttype in ("slump", "fresh_temperature"):
        acts.append({"stage": 1, "action_type": "retest", "element_id": None, "reference": "IS 1199",
                     "description": "Repeat the fresh-concrete test on the next truck/sample and check the mix ticket."})
    else:
        for loc in locs:
            acts.append({"stage": 1, "action_type": "rebound_hammer", "element_id": loc["element_id"], "reference": "IS 13311 (Part 2)",
                         "description": f"Rebound hammer survey on {loc['path']} (screening only)."})
            acts.append({"stage": 1, "action_type": "upv", "element_id": loc["element_id"], "reference": "IS 13311 (Part 1)",
                         "description": f"UPV survey on {loc['path']}."})
            acts.append({"stage": 2, "action_type": "core_test", "element_id": loc["element_id"], "reference": "IS 456:2000 Cl. 17.4",
                         "description": f"If NDT is doubtful, take cores from {loc['path']}; acceptable if mean >= 85% and each >= 75% of fck."})
        acts.append({"stage": 3, "action_type": "structural_assessment", "element_id": None, "reference": "IS 456:2000 Cl. 17.6",
                     "description": "If cores fail, structural assessment / load test decided by the design engineer."})
        if not locs:
            acts.append({"stage": 1, "action_type": "trace_usage", "element_id": None, "reference": "BUILDGUARD traceability",
                         "description": "No usage recorded for this batch yet: confirm where it was poured and record it."})
    return acts


def open_investigation(db: Session, user: User, batch: Batch, t: TestRecord | None, reason: str | None = None) -> Investigation:
    if t is not None:
        existing = db.scalar(select(Investigation).where(Investigation.test_id == t.id, Investigation.status != "closed"))
        if existing:
            return existing
    inv = Investigation(project_id=batch.project_id, batch_id=batch.id, test_id=t.id if t else None, opened_by=user.id,
                        reason=reason or (f"{t.test_type} flagged: " + "; ".join(o["explanation"] for o in t.validation if o["status"] == "FLAGGED")
                                          if t else "Manual investigation"))
    db.add(inv)
    db.flush()
    for a in _suggest_actions(db, batch, t):
        db.add(InvestigationAction(investigation_id=inv.id, **a))
    db.flush()
    changes.add(db, inv.project_id, "investigation", inv.id, "create", inv)
    audit.stage(db, user, "investigation.open", "investigation", inv.id, inv.project_id, {"batch": batch.batch_code, "reason": inv.reason})
    return inv


def close_investigation(db: Session, user: User, inv: Investigation, note: str) -> Investigation:
    ensure_access(db, user, inv.project_id, REVIEW_ROLES)
    open_actions = db.scalars(select(InvestigationAction).where(
        InvestigationAction.investigation_id == inv.id, InvestigationAction.status == "pending")).all()
    if open_actions:
        raise HTTPException(409, f"{len(open_actions)} action(s) still pending: complete or waive them first")
    inv.status, inv.closure_note, inv.closed_at = "closed", note, utcnow()
    changes.add(db, inv.project_id, "investigation", inv.id, "update", inv)
    audit.stage(db, user, "investigation.close", "investigation", inv.id, inv.project_id, {"note": note})
    return inv
