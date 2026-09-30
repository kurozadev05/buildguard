import html

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import EnvelopeRoute
from ..database import get_db
from ..models import Batch
from ..services import passport

api = APIRouter(prefix="/public", tags=["public QR passport"], route_class=EnvelopeRoute)
pages = APIRouter(tags=["public QR passport"])


def _resolve(db: Session, token: str) -> Batch:
    """Public access is by unguessable token only (sequential batch/sample codes are not accepted)."""
    b = db.scalar(select(Batch).where(Batch.public_token == token)) if 8 <= len(token) <= 24 else None
    if not b:
        raise HTTPException(404, "Unknown code")
    return b


@api.get("/passport/{code}")
def public_json(code: str, db: Session = Depends(get_db)):
    """Read-only summary for whoever scans the QR sticker (no locations, no internal ids)."""
    return passport.public_passport(db, _resolve(db, code))


_COLORS = {"VERIFIED": "#1a9641", "REVIEW_REQUIRED": "#e6a100", "FLAGGED": "#d7191c", "PENDING": "#6b7280"}


@pages.get("/p/{code}", response_class=HTMLResponse, include_in_schema=False)
def public_page(code: str, db: Session = Depends(get_db)):
    p = passport.public_passport(db, _resolve(db, code))
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(t['type'].replace('_', ' '))}{' · ' + str(t['age_days']) + 'd' if t['age_days'] else ''}</td>"
        f"<td style='color:{_COLORS.get(t['status'], '#333')};font-weight:600'>{e(t['status'].replace('_', ' '))}</td>"
        f"<td>{'✔ sealed' if t['seal_valid'] else '✖ modified'}</td></tr>" for t in p["tests"]) or "<tr><td colspan=3>No tests recorded yet</td></tr>"
    col = _COLORS.get(p["status"], "#333")
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(p['batch_code'])} · BUILDGUARD</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#f5f5f4;color:#1c1917}}main{{max-width:520px;margin:0 auto;padding:16px}}
.card{{background:#fff;border-radius:14px;padding:16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.badge{{display:inline-block;padding:6px 14px;border-radius:999px;color:#fff;font-weight:700;background:{col}}}
table{{width:100%;border-collapse:collapse}}td{{padding:8px 4px;border-bottom:1px solid #eee;font-size:14px}}small{{color:#78716c}}</style></head><body><main>
<div class="card"><small>BUILDGUARD · Material Passport</small><h2 style="margin:4px 0">{e(p['batch_code'])}</h2>
<span class="badge">{e(p['status'].replace('_', ' '))}</span>
<p>{e(p['material'].title())} · {e(p['grade'])} · {p['quantity_m3']:g} m³<br>Supplier: {e(p['supplier'] or '—')}<br>Delivered: {e(p['delivery_date'] or '—')}</p></div>
<div class="card"><b>Test history</b><table>{rows}</table></div>
<div class="card"><small>{e(p['note'])} Decision support only; does not replace laboratory testing or engineering judgement.</small></div>
</main></body></html>""")
