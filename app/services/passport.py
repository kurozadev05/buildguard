import io

import qrcode
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n
from ..config import settings
from ..models import Batch, Document, Investigation, Sample, TestRecord
from ..utils import to_dict
from . import core, rules_engine as re_


def test_out(t: TestRecord, lang: str = "en") -> dict:
    d = to_dict(t)
    eff = core.effective_status(t)
    d["effective_status"] = eff
    d["status_label"] = i18n.status_label(eff, lang)
    d["test_label"] = i18n.test_label(t.test_type, lang)
    d["seal_valid"] = core.test_record_hash(t) == t.record_hash
    return d


def batch_out(b: Batch, lang: str = "en") -> dict:
    d = to_dict(b)
    d["status_label"] = i18n.status_label(b.status, lang)
    d["required_samples"] = re_.required_samples(b.quantity_m3)
    return d


def full_passport(db: Session, b: Batch, lang: str = "en") -> dict:
    tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == b.id).order_by(TestRecord.created_at)).all()
    samples = db.scalars(select(Sample).where(Sample.batch_id == b.id).order_by(Sample.created_at)).all()
    docs = db.scalars(select(Document).where(Document.batch_id == b.id)).all()
    invs = db.scalars(select(Investigation).where(Investigation.batch_id == b.id)).all()
    out = batch_out(b, lang)
    out.update({
        "samples": [to_dict(s) for s in samples],
        "tests": [test_out(t, lang) for t in tests],
        "documents": [{k: v for k, v in to_dict(d).items() if k != "storage_path"} for d in docs],
        "locations": core.batch_locations(db, b.id),
        "investigations": [to_dict(i) for i in invs],
        "samples_taken": len(samples),
    })
    return out


def public_passport(db: Session, b: Batch) -> dict:
    """What a QR scan shows without login: no locations, no internal ids."""
    tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == b.id, TestRecord.is_current == True).order_by(TestRecord.created_at)).all()  # noqa: E712
    return {
        "batch_code": b.batch_code, "material": b.material, "grade": b.grade, "supplier": b.supplier,
        "delivery_date": b.delivery_date.isoformat() if b.delivery_date else None, "quantity_m3": b.quantity_m3,
        "status": b.status, "status_label": i18n.status_label(b.status, "en"),
        "tests": [{"type": t.test_type, "age_days": t.age_days, "status": core.effective_status(t),
                   "tested_at": t.tested_at.isoformat() + "Z", "record_hash": t.record_hash[:16] + "…",
                   "seal_valid": core.test_record_hash(t) == t.record_hash} for t in tests],
        "note": "Public summary. Sign in for full evidence, locations and documents.",
    }


def qr_url(base: str, code: str) -> str:
    return f"{(settings.public_base_url or base).rstrip('/')}/p/{code}"


def qr_png(url: str) -> bytes:
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
