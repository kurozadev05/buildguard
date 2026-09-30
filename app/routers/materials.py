from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..deps import accessible_project_ids, current_user, ensure_access, lang_dep
from ..models import Batch, BatchUsage, Element, Investigation, Sample, TestRecord, User
from ..schemas import AmendIn, BatchIn, ReviewIn, SampleIn, TestIn, UsageIn
from ..services import audit, core, passport
from ..utils import to_dict

router = APIRouter(tags=["materials & testing"], route_class=EnvelopeRoute)


# ───────── batches (material passport) ─────────
@router.post("/batches", status_code=201)
def create_batch(body: BatchIn, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    b = core.create_batch(db, user, body)
    audit.commit(db)
    return passport.batch_out(b, lang)


@router.get("/batches")
def list_batches(project_id: str | None = None, status: str | None = None, grade: str | None = None, supplier: str | None = None,
                 limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    pids = accessible_project_ids(db, user)
    if project_id:
        ensure_access(db, user, project_id)
        pids = [project_id]
    q = select(Batch).where(Batch.project_id.in_(pids))
    if status:
        q = q.where(Batch.status == status.upper())
    if grade:
        q = q.where(Batch.grade == grade.upper())
    if supplier:
        q = q.where(Batch.supplier.ilike(f"%{supplier}%"))
    rows = db.scalars(q.order_by(Batch.created_at.desc()).limit(limit).offset(offset)).all()
    return [passport.batch_out(b, lang) for b in rows]


def _batch_for(db: Session, user: User, batch_id: str) -> Batch:
    b = core.get_or_404(db, Batch, batch_id, "Batch")
    ensure_access(db, user, b.project_id)
    return b


@router.get("/batches/by-code/{code}")
def by_code(code: str, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Resolve a scanned QR (batch or sample code) to the full passport."""
    code = code.upper()
    b = db.scalar(select(Batch).where(Batch.batch_code == code))
    if not b:
        s = db.scalar(select(Sample).where(Sample.sample_code == code))
        b = db.get(Batch, s.batch_id) if s else None
    if not b:
        raise HTTPException(404, "No batch or sample with this code")
    ensure_access(db, user, b.project_id)
    return passport.full_passport(db, b, lang)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    return passport.full_passport(db, _batch_for(db, user, batch_id), lang)


@router.get("/batches/{batch_id}/qr.png")
def batch_qr(batch_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch_for(db, user, batch_id)
    return Response(passport.qr_png(passport.qr_url(str(request.base_url), b.public_token)), media_type="image/png")


@router.get("/samples/{sample_id}/qr.png")
def sample_qr(sample_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = core.get_or_404(db, Sample, sample_id, "Sample")
    ensure_access(db, user, s.project_id)
    b = core.get_or_404(db, Batch, s.batch_id, "Batch")
    return Response(passport.qr_png(passport.qr_url(str(request.base_url), b.public_token)), media_type="image/png")


@router.get("/batches/{batch_id}/locations")
def locations(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch_for(db, user, batch_id)
    return {"batch_code": b.batch_code, "status": b.status, "locations": core.batch_locations(db, b.id)}


@router.get("/batches/{batch_id}/impact")
def impact(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Investigation scope for a (flagged) batch: every element it went into, the failing evidence, and open actions."""
    b = _batch_for(db, user, batch_id)
    tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == b.id, TestRecord.is_current == True)).all()  # noqa: E712
    bad = [passport.test_out(t) for t in tests if core.effective_status(t) != "VERIFIED"]
    invs = db.scalars(select(Investigation).where(Investigation.batch_id == b.id)).all()
    locs = core.batch_locations(db, b.id)
    return {"batch_code": b.batch_code, "status": b.status, "supplier": b.supplier,
            "affected_locations": locs, "affected_volume_m3": round(sum(loc["volume_m3"] or 0 for loc in locs), 2),
            "problem_tests": bad, "investigations": [to_dict(i) for i in invs]}


@router.get("/elements/{element_id}/batches")
def element_batches(element_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    el = core.get_or_404(db, Element, element_id, "Element")
    ensure_access(db, user, el.project_id)
    rows = db.scalars(select(BatchUsage).where(BatchUsage.element_id == el.id)).all()
    return {"element": to_dict(el), "path": core.element_path(db, el),
            "batches": [{**passport.batch_out(core.get_or_404(db, Batch, u.batch_id, "Batch")), "volume_m3": u.volume_m3, "poured_at": u.poured_at.isoformat() + "Z"} for u in rows]}


# ───────── samples ─────────
@router.post("/samples", status_code=201)
def create_sample(body: SampleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = core.create_sample(db, user, body)
    audit.commit(db)
    return to_dict(s)


@router.get("/batches/{batch_id}/samples")
def batch_samples(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch_for(db, user, batch_id)
    return [to_dict(s) for s in db.scalars(select(Sample).where(Sample.batch_id == b.id).order_by(Sample.created_at))]


# ───────── tests ─────────
@router.post("/tests", status_code=201)
def create_test(body: TestIn, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Record a test. The rules engine validates it immediately and returns the requirement applied."""
    t = core.create_test(db, user, body)
    audit.commit(db)
    return passport.test_out(t, lang)


@router.get("/tests")
def list_tests(batch_id: str | None = None, status: str | None = None, current_only: bool = True,
               limit: int = Query(100, ge=1, le=300), offset: int = Query(0, ge=0),
               user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    pids = accessible_project_ids(db, user)
    q = select(TestRecord).where(TestRecord.project_id.in_(pids))
    if batch_id:
        q = q.where(TestRecord.batch_id == batch_id)
    if status:
        q = q.where(TestRecord.status == status.upper())
    if current_only:
        q = q.where(TestRecord.is_current == True)  # noqa: E712
    return [passport.test_out(t, lang) for t in db.scalars(q.order_by(TestRecord.created_at.desc()).limit(limit).offset(offset))]


def _test_for(db: Session, user: User, test_id: str) -> TestRecord:
    t = core.get_or_404(db, TestRecord, test_id, "Test")
    ensure_access(db, user, t.project_id)
    return t


@router.get("/tests/{test_id}")
def get_test(test_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    t = _test_for(db, user, test_id)
    versions = db.scalars(select(TestRecord).where(TestRecord.batch_id == t.batch_id, TestRecord.sample_id == t.sample_id,
                                                   TestRecord.test_type == t.test_type, TestRecord.age_days == t.age_days).order_by(TestRecord.version)).all()
    return {**passport.test_out(t, lang), "history": [{"id": v.id, "version": v.version, "status": v.status, "is_current": v.is_current} for v in versions]}


@router.post("/tests/{test_id}/amend", status_code=201)
def amend(test_id: str, body: AmendIn, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Correct a result. The original stays on record; a new version supersedes it (append-only)."""
    t = core.amend_test(db, user, _test_for(db, user, test_id), body.values, body.age_days, body.reason)
    audit.commit(db)
    return passport.test_out(t, lang)


@router.post("/tests/{test_id}/review")
def review(test_id: str, body: ReviewIn, user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Engineer decision on a Review Required / Flagged result. Recorded with name, time and comment."""
    t = core.review_test(db, user, _test_for(db, user, test_id), body.decision, body.comment)
    audit.commit(db)
    return passport.test_out(t, lang)


@router.get("/tests/{test_id}/verify")
def verify_test(test_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _test_for(db, user, test_id)
    ok = core.test_record_hash(t) == t.record_hash
    return {"test_id": t.id, "seal_valid": ok, "record_hash": t.record_hash,
            "message": "Record matches its seal." if ok else "RECORD HAS BEEN MODIFIED after it was sealed."}


# ───────── traceability ─────────
@router.post("/usage", status_code=201)
def add_usage(body: UsageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Record where a batch was used (Project → Building → Floor → Element)."""
    u = core.add_usage(db, user, body)
    audit.commit(db)
    return {**to_dict(u), "path": core.element_path(db, core.get_or_404(db, Element, u.element_id, "Element"))}
