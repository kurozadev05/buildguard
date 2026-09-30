from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from ..models import (Alert, Batch, Document, Investigation, RiskAssessment, Sample, TestRecord, utcnow)
from . import rules_engine as re_
from .core import effective_status

SAMPLE_AGES = (7, 28)


def _latest_risk(db: Session, project_ids: list[str]) -> dict[str, RiskAssessment]:
    latest = (select(RiskAssessment.element_id, func.max(RiskAssessment.computed_at).label("m"))
              .where(RiskAssessment.project_id.in_(project_ids)).group_by(RiskAssessment.element_id).subquery())
    rows = db.scalars(select(RiskAssessment).join(latest, (RiskAssessment.element_id == latest.c.element_id)
                                                  & (RiskAssessment.computed_at == latest.c.m))).all()
    return {r.element_id: r for r in rows}


def summary(db: Session, project_ids: list[str]) -> dict:
    if not project_ids:
        return {"totals": {}, "message": "No accessible projects"}
    batches = db.scalars(select(Batch).where(Batch.project_id.in_(project_ids))).all()
    tests = db.scalars(select(TestRecord).options(load_only(
        TestRecord.id, TestRecord.batch_id, TestRecord.sample_id, TestRecord.test_type, TestRecord.age_days,
        TestRecord.status, TestRecord.review, TestRecord.values))
        .where(TestRecord.project_id.in_(project_ids), TestRecord.is_current == True)).all()  # noqa: E712
    samples = db.scalars(select(Sample).options(load_only(Sample.id, Sample.batch_id, Sample.sample_code, Sample.cast_date))
                         .where(Sample.project_id.in_(project_ids))).all()
    n_docs = db.scalar(select(func.count(Document.id)).where(Document.project_id.in_(project_ids))) or 0
    doc_test_ids = set(db.scalars(select(Document.test_id).where(Document.project_id.in_(project_ids), Document.test_id != None).distinct()))  # noqa: E711
    now = utcnow()

    by_status = Counter(b.status for b in batches)
    test_status = Counter(effective_status(t) for t in tests)
    material: defaultdict[str, Counter] = defaultdict(Counter)
    for b in batches:
        material[f"{b.material}:{b.grade}"][b.status] += 1

    # pending / overdue cube tests
    bmap = {b.id: b for b in batches}
    done = {(t.sample_id, t.age_days) for t in tests if t.test_type == "cube_compressive_strength"}
    pending: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    for s in samples:
        bt = bmap.get(s.batch_id)
        for age in SAMPLE_AGES:
            if (s.id, age) in done:
                continue
            due = s.cast_date + timedelta(days=age)
            item = {"sample_code": s.sample_code, "batch_code": bt.batch_code if bt else None, "age_days": age, "due_date": due.isoformat()}
            (overdue if due < now.date() else pending).append(item)

    # sampling shortfall vs IS 456 Table 12
    cnt = Counter(s.batch_id for s in samples)
    shortfall = []
    for b in batches:
        need = re_.required_samples(b.quantity_m3)
        if cnt.get(b.id, 0) < need:
            shortfall.append({"batch_code": b.batch_code, "required": need, "taken": cnt.get(b.id, 0), "quantity_m3": b.quantity_m3})

    # evidence: every current test should have a photo/report attached
    with_doc = doc_test_ids
    missing_docs = [{"test_id": t.id, "batch_id": t.batch_id, "test_type": t.test_type, "age_days": t.age_days}
                    for t in tests if t.id not in with_doc]

    alerts = db.scalars(select(Alert).where(Alert.project_id.in_(project_ids)).order_by(Alert.created_at.desc()).limit(15)).all()
    open_alerts = db.scalars(select(Alert).where(Alert.project_id.in_(project_ids), Alert.acknowledged_at == None)).all()  # noqa: E711
    invs = db.scalars(select(Investigation).where(Investigation.project_id.in_(project_ids), Investigation.status != "closed")).all()
    risks = _latest_risk(db, project_ids)

    return {
        "totals": {"batches": len(batches), "samples": len(samples), "tests": len(tests), "documents": n_docs,
                   "open_alerts": len(open_alerts), "open_investigations": len(invs)},
        "batch_status": {k: by_status.get(k, 0) for k in ("VERIFIED", "REVIEW_REQUIRED", "FLAGGED", "PENDING")},
        "test_status": {k: test_status.get(k, 0) for k in ("VERIFIED", "REVIEW_REQUIRED", "FLAGGED")},
        "material_status": {k: dict(v) for k, v in material.items()},
        "pending_tests": pending[:50],
        "overdue_tests": overdue[:50],
        "sampling_shortfall": shortfall,
        "tests_missing_evidence": missing_docs[:50],
        "recent_alerts": [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message,
                           "created_at": a.created_at.isoformat() + "Z", "acknowledged": a.acknowledged_at is not None} for a in alerts],
        "durability_risk": {
            "counts": dict(Counter(r.level for r in risks.values())),
            "elements": [{"element_id": eid, "score": r.score, "level": r.level, "trend": r.trend} for eid, r in
                         sorted(risks.items(), key=lambda kv: -kv[1].score)[:20]],
        },
        "supplier_scorecards": supplier_scorecards(batches, tests),
    }


def supplier_scorecards(batches: Sequence[Batch], tests: Sequence[TestRecord]) -> list[dict]:
    by_sup = defaultdict(list)
    for b in batches:
        by_sup[b.supplier or "Unknown"].append(b)
    tmap = defaultdict(list)
    for t in tests:
        tmap[t.batch_id].append(t)
    out = []
    for sup, bl in by_sup.items():
        flagged = sum(b.status == "FLAGGED" for b in bl)
        review = sum(b.status == "REVIEW_REQUIRED" for b in bl)
        margins = []
        for b in bl:
            for t in tmap.get(b.id, []):
                if t.test_type == "cube_compressive_strength" and t.age_days == 28:
                    m = re_.sample_mean(t.values)
                    if m is not None:
                        margins.append(m - b.fck)
        n = len(bl)
        score = max(0.0, 100 * (1 - flagged / n) - 30 * (review / n))
        out.append({"supplier": sup, "batches": n, "flagged": flagged, "review_required": review,
                    "flag_rate": round(flagged / n, 3), "avg_28d_margin_over_fck_mpa": round(sum(margins) / len(margins), 2) if margins else None,
                    "reliability_score": round(score, 1),
                    "note": "Indicative: based only on recorded batches; small samples are noisy."})
    return sorted(out, key=lambda r: -r["reliability_score"])
