import time
from datetime import datetime

from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..ai import service as ai
from ..deps import TEST_ROLES, accessible_project_ids, current_user, ensure_access, lang_dep
from ..models import Batch, Document, OcrDraft, Sample, TestRecord, User
from ..ai.schemas import AttachIn
from ..schemas import OcrConfirmIn, OcrTextIn, TestIn
from ..services import audit, core, evidence, ocr, passport
from ..utils import to_dict

router = APIRouter(tags=["evidence & OCR"], route_class=EnvelopeRoute)


def _doc_out(d: Document) -> dict:
    return {k: v for k, v in to_dict(d).items() if k != "storage_path"}


def _resolve_project(db: Session, batch_id, sample_id, test_id, project_id):
    if test_id:
        t = core.get_or_404(db, TestRecord, test_id, "Test")
        return t.project_id, t.batch_id, t.sample_id
    if sample_id:
        s = core.get_or_404(db, Sample, sample_id, "Sample")
        return s.project_id, s.batch_id, s.id
    if batch_id:
        b = core.get_or_404(db, Batch, batch_id, "Batch")
        return b.project_id, b.id, None
    if project_id:
        return project_id, None, None
    raise HTTPException(422, "Provide one of test_id, sample_id, batch_id or project_id")


@router.post("/documents", status_code=201)
def upload_document(file: UploadFile = File(...), kind: str = Form("photo"), project_id: str | None = Form(None),
                          batch_id: str | None = Form(None), sample_id: str | None = Form(None), test_id: str | None = Form(None),
                          latitude: float | None = Form(None), longitude: float | None = Form(None),
                          captured_at: datetime | None = Form(None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Upload a photo/report as tamper-evident evidence: SHA-256, server timestamp and optional geotag are recorded."""
    pid, bid, sid = _resolve_project(db, batch_id, sample_id, test_id, project_id)
    ensure_access(db, user, pid, TEST_ROLES)
    doc = evidence.save_upload(db, user, file, project_id=pid, kind=kind, batch_id=batch_id or bid if not test_id else bid,
                                     sample_id=sample_id or sid, test_id=test_id, latitude=latitude, longitude=longitude, captured_at=captured_at)
    audit.commit(db)
    return _doc_out(doc)


@router.get("/documents")
def list_documents(batch_id: str | None = None, test_id: str | None = None, sample_id: str | None = None,
                   limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Document).where(Document.project_id.in_(accessible_project_ids(db, user)))     # tenant filter in SQL
    if batch_id:
        q = q.where(Document.batch_id == batch_id)
    if test_id:
        q = q.where(Document.test_id == test_id)
    if sample_id:
        q = q.where(Document.sample_id == sample_id)
    return [_doc_out(d) for d in db.scalars(q.order_by(Document.uploaded_at.desc()).limit(limit).offset(offset))]


def _doc_for(db: Session, user: User, doc_id: str) -> Document:
    d = core.get_or_404(db, Document, doc_id, "Document")
    ensure_access(db, user, d.project_id)
    return d


@router.get("/documents/{doc_id}/download")
def download(doc_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _doc_for(db, user, doc_id)
    return FileResponse(d.storage_path, media_type=d.content_type, filename=d.filename)


@router.get("/documents/{doc_id}/verify")
def verify(doc_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return evidence.verify_document(_doc_for(db, user, doc_id))


# ───────── OCR digitisation (draft → human confirm) ─────────
@router.post("/ocr/parse-text")
def parse_text(body: OcrTextIn, user: User = Depends(current_user)):
    """Extract draft fields from OCR text produced on the device (works offline-first, no server OCR needed)."""
    return {"provider": "regex", **ocr.parse_report_text(body.text), "needs_human_check": True}


@router.post("/ocr/extract", status_code=201)
async def extract(request: Request, file: UploadFile = File(...), project_id: str = Form(...), batch_id: str | None = Form(None),
                  user: User = Depends(current_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Photo/scan of a lab report -> stored as evidence + a DRAFT record. Nothing becomes official until a human confirms it."""
    def save():
        ensure_access(db, user, project_id, TEST_ROLES)
        doc = evidence.save_upload(db, user, file, project_id=project_id, kind="report", batch_id=batch_id)
        audit.commit(db)
        with open(doc.storage_path, "rb") as fh:
            return doc, fh.read()

    doc, raw = await run_in_threadpool(save)
    caller = ai.Caller(user, getattr(request.state, "request_id", "-"), lang)
    await ai.enforce_limits(user.id)
    t0 = time.perf_counter()
    result, usage = await ocr.extract(raw, doc.content_type)
    await ai.record(caller, "ocr", usage, (time.perf_counter() - t0) * 1000, None, "ok" if result["provider"] != "none" else "fallback")

    def draft():
        dr = OcrDraft(project_id=project_id, document_id=doc.id, provider=result["provider"], extracted=result, created_by=user.id)
        db.add(dr)
        db.flush()
        audit.stage(db, user, "ocr.draft", "ocr_draft", dr.id, project_id, {"provider": dr.provider, "document": doc.id})
        audit.commit(db)
        return dr.id

    return {"draft_id": await run_in_threadpool(draft), "document": _doc_out(doc), "extracted": result}


@router.post("/documents/{doc_id}/attach")
def attach_document(doc_id: str, body: AttachIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Link an already-uploaded photo (e.g. a machine-display photo) to the test it supports. Same project only; the file hash is unchanged."""
    d = _doc_for(db, user, doc_id)
    ensure_access(db, user, d.project_id, TEST_ROLES)
    t = core.get_or_404(db, TestRecord, body.test_id, "Test")
    if t.project_id != d.project_id:
        raise HTTPException(422, "Document and test belong to different projects")
    d.test_id, d.batch_id, d.sample_id = t.id, t.batch_id, t.sample_id
    audit.stage(db, user, "document.attach", "document", d.id, d.project_id, {"test_id": t.id, "sha256": d.sha256})
    audit.commit(db)
    return _doc_out(d)


@router.get("/ocr/drafts")
def drafts(status: str = Query("pending", pattern="^(pending|confirmed|rejected)$"), limit: int = Query(100, ge=1, le=200),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(OcrDraft).where(OcrDraft.status == status, OcrDraft.project_id.in_(accessible_project_ids(db, user)))
                      .order_by(OcrDraft.created_at.desc()).limit(limit))
    return [to_dict(r) for r in rows]


@router.post("/ocr/drafts/{draft_id}/confirm", status_code=201)
def confirm(draft_id: str, body: OcrConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A human reviews/corrects the draft; only now is the official test record created (and linked to the source document)."""
    dr = core.get_or_404(db, OcrDraft, draft_id, "Draft")
    ensure_access(db, user, dr.project_id, TEST_ROLES)
    if dr.status != "pending":
        raise HTTPException(409, f"Draft already {dr.status}")
    batch_id = body.batch_id
    if not batch_id:
        code = (dr.extracted.get("fields") or {}).get("batch_code")
        b = db.scalar(select(Batch).where(Batch.batch_code == code)) if code else None
        batch_id = b.id if b else None
    if not batch_id:
        raise HTTPException(422, "batch_id required (could not be read from the report)")
    t = core.create_test(db, user, TestIn(batch_id=batch_id, sample_id=body.sample_id, test_type=body.test_type, age_days=body.age_days,
                                          values=body.values, lab_name=body.lab_name, tested_at=body.tested_at, notes=f"Digitised from report (draft {dr.id})"))
    if dr.document_id:
        d = core.get_or_404(db, Document, dr.document_id, "Document")
        d.test_id = t.id
        d.batch_id = batch_id
    dr.status, dr.test_id, dr.confirmed_by = "confirmed", t.id, user.id
    audit.stage(db, user, "ocr.confirm", "ocr_draft", dr.id, dr.project_id, {"test_id": t.id})
    audit.commit(db)
    return passport.test_out(t)


@router.post("/ocr/drafts/{draft_id}/reject")
def reject(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    dr = core.get_or_404(db, OcrDraft, draft_id, "Draft")
    ensure_access(db, user, dr.project_id, TEST_ROLES)
    dr.status = "rejected"
    audit.stage(db, user, "ocr.reject", "ocr_draft", dr.id, dr.project_id, {})
    audit.commit(db)
    return {"draft_id": dr.id, "status": "rejected"}
