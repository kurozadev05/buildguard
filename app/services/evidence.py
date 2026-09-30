"""Tamper-evident evidence: type/size checks, SHA-256, server timestamp, optional geotag."""
import os
import uuid
from datetime import datetime

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Document, User, utcnow
from ..utils import sha256_hex
from . import audit, changes

ALLOWED = {
    "image/jpeg": (".jpg", [b"\xff\xd8\xff"]),
    "image/png": (".png", [b"\x89PNG\r\n\x1a\n"]),
    "image/webp": (".webp", [b"RIFF"]),
    "application/pdf": (".pdf", [b"%PDF"]),
}
KINDS = {"photo", "crushing_photo", "slump_photo", "report", "certificate", "delivery_challan", "curing_log", "other"}


def _sniff(data: bytes, ctype: str) -> bool:
    return any(data.startswith(sig) for sig in ALLOWED[ctype][1]) and (ctype != "image/webp" or data[8:12] == b"WEBP")


def save_upload(db: Session, user: User, file: UploadFile, *, project_id: str, kind: str, batch_id=None, sample_id=None,
                      test_id=None, latitude=None, longitude=None, captured_at: datetime | None = None) -> Document:
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    ctype = (file.content_type or "").lower()
    if ctype not in ALLOWED:
        raise HTTPException(415, f"Unsupported file type. Allowed: {', '.join(ALLOWED)}")
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB")
    if not data or not _sniff(data, ctype):
        raise HTTPException(415, "File content does not match its declared type")
    if ctype.startswith("image/"):
        try:                                    # structural check: rejects polyglots / truncated garbage that only has a magic header
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as im:
                if im.width * im.height > 60_000_000:
                    raise ValueError("too many pixels")
                im.verify()
        except Exception:
            raise HTTPException(415, "Image is corrupt or not a valid image") from None
    if latitude is not None and not -90 <= latitude <= 90:
        raise HTTPException(422, "latitude out of range")
    if longitude is not None and not -180 <= longitude <= 180:
        raise HTTPException(422, "longitude out of range")
    doc_id = str(uuid.uuid4())
    folder = os.path.join(settings.upload_dir, project_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, doc_id + ALLOWED[ctype][0])  # never trust the client filename for the path
    with open(path, "wb") as fh:
        fh.write(data)
    name = os.path.basename(file.filename or "upload")[:200]
    doc = Document(id=doc_id, project_id=project_id, batch_id=batch_id, sample_id=sample_id, test_id=test_id, kind=kind,
                   filename=name, content_type=ctype, size_bytes=len(data), sha256=sha256_hex(data), storage_path=path,
                   latitude=latitude, longitude=longitude, captured_at=captured_at, uploaded_by=user.id, uploaded_at=utcnow())
    db.add(doc)
    db.flush()
    changes.add(db, project_id, "document", doc.id, "create", doc, extra={"storage_path": None})
    audit.stage(db, user, "document.upload", "document", doc.id, project_id,
                {"sha256": doc.sha256, "kind": kind, "size": doc.size_bytes, "geotagged": latitude is not None and longitude is not None})
    return doc


def verify_document(doc: Document) -> dict:
    try:
        with open(doc.storage_path, "rb") as fh:
            actual = sha256_hex(fh.read())
    except FileNotFoundError:
        return {"ok": False, "stored_sha256": doc.sha256, "actual_sha256": None, "message": "File missing from storage."}
    return {"ok": actual == doc.sha256, "stored_sha256": doc.sha256, "actual_sha256": actual,
            "message": "File matches its recorded hash." if actual == doc.sha256 else "FILE HAS BEEN ALTERED since upload."}
