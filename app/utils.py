import hashlib
import json
from datetime import date, datetime
from typing import Any
from collections.abc import Iterable


def canonical(obj: Any) -> str:
    """Deterministic JSON used for hashing (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sanitize(obj: Any) -> Any:
    """Round-trip through JSON so stored and hashed values are identical."""
    return json.loads(canonical(obj))


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def to_dict(obj, exclude: Iterable[str] = ()) -> dict:
    """Generic model -> JSON-safe dict (datetimes as ISO-8601 UTC)."""
    out = {}
    for c in obj.__table__.columns:
        if c.key in exclude:
            continue
        v = getattr(obj, c.key)
        if isinstance(v, datetime):
            v = v.isoformat() + "Z"
        elif isinstance(v, date):
            v = v.isoformat()
        out[c.key] = v
    return out
