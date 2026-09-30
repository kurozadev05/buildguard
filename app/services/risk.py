"""Durability & long-term performance RISK (not a service-life prediction).

A transparent additive index (0-100) built from named factors, each with its own
threshold and code reference, plus simple statistics (robust z-score, slope) for
anomalies and trends. Every output lists its contributing factors so an engineer can
challenge it. It is NOT a certificate of structural safety and is not ML-calibrated;
replace the weights with a validated model once longitudinal data exists.
"""
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BatchUsage, Batch, Element, Observation, RiskAssessment, utcnow
from . import audit, changes, rules_engine as re_
from .core import raise_alert

EXPOSURE_BASE = {"mild": 0, "moderate": 3, "severe": 6, "very_severe": 8, "extreme": 10}
LEVELS = [(50, "High"), (25, "Moderate"), (0, "Low")]


def _level(score: float) -> str:
    for cut, name in LEVELS:
        if score >= cut:
            return name
    return "Low"


def _series(db: Session, element_id: str, kind: str, as_of: datetime) -> list[tuple[datetime, float]]:
    rows = db.scalars(select(Observation).where(Observation.element_id == element_id, Observation.kind == kind,
                                                Observation.observed_at <= as_of).order_by(Observation.observed_at)).all()
    return [(r.observed_at, r.value) for r in rows]


def _slope_per_year(s: list[tuple[datetime, float]]) -> float | None:
    if len(s) < 3:
        return None
    t0 = s[0][0]
    xs = [(t - t0).total_seconds() / (365.25 * 86400) for t, _ in s]
    ys = [v for _, v in s]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den


def _anomaly(kind: str, s: list[tuple[datetime, float]]) -> dict | None:
    vals = [v for _, v in s]
    if len(vals) < 5:
        return None
    hist, last = vals[:-1], vals[-1]
    med = median(hist)
    mad = median(abs(v - med) for v in hist) or 1e-9
    z = 0.6745 * (last - med) / mad
    steps = [b - a for a, b in zip(hist, hist[1:], strict=False)]
    jump = last - hist[-1]
    med_step = median(abs(x) for x in steps) or 1e-9
    if abs(z) > 3.5 or abs(jump) > 5 * med_step and abs(jump) > 0:
        return {"kind": kind, "latest": last, "baseline_median": round(med, 3), "robust_z": round(z, 2),
                "message": f"Latest {kind} reading ({last}) departs sharply from its history (median {med:.3g})."}
    return None


def assess_element(db: Session, user, element: Element, as_of: datetime | None = None, persist: bool = True) -> dict:
    as_of = as_of or utcnow()
    factors: list[dict] = []

    def add(name, points, detail, ref):
        if points > 0:
            factors.append({"factor": name, "points": round(points, 1), "detail": detail, "reference": ref})

    usages = db.scalars(select(BatchUsage).where(BatchUsage.element_id == element.id, BatchUsage.poured_at <= as_of)).all()
    batches = [b for b in (db.get(Batch, u.batch_id) for u in usages) if b is not None]
    exposure = re_.norm_exposure(element.exposure or (batches[0].exposure if batches else "moderate"))
    table = re_.RULES["exposure"].get(exposure, re_.RULES["exposure"]["moderate"])

    add("Exposure class", EXPOSURE_BASE.get(exposure, 3), f"{exposure} exposure raises baseline risk", "IS 456 Table 3/5")

    crack = _series(db, element.id, "crack_width_mm", as_of)
    if crack:
        lim = table["crack_limit_mm"]
        r = crack[-1][1] / lim
        add("Crack width", min(30, 30 * r), f"latest {crack[-1][1]} mm vs limit {lim} mm ({r*100:.0f}% of limit)", "IS 456 Cl. 35.3.2")

    moist = _series(db, element.id, "moisture_pct", as_of)
    if moist:
        m = moist[-1][1]
        add("Moisture", 10 * max(0.0, min(1.0, (m - 4) / 4)), f"latest moisture {m}% (indicative, >4% raises corrosion risk)", "Engineering practice")

    upv = _series(db, element.id, "upv_km_s", as_of)
    if upv:
        v = upv[-1][1]
        pts = 0 if v >= 4.5 else 5 if v >= 3.5 else 12 if v >= 3.0 else 20
        add("Pulse velocity", pts, f"latest UPV {v} km/s", "IS 13311 (Part 1)")

    reb = _series(db, element.id, "rebound_number", as_of)
    if len(reb) >= 2 and reb[0][1] > 0:
        drop = (reb[0][1] - reb[-1][1]) / reb[0][1] * 100
        add("Rebound decline", max(0.0, min(10, drop / 2)), f"rebound fell {drop:.0f}% from baseline ({reb[0][1]} to {reb[-1][1]})", "IS 13311 (Part 2)")

    carb = _series(db, element.id, "carbonation_depth_mm", as_of)
    if carb:
        cover = table["nominal_cover_mm"]
        add("Carbonation vs cover", min(15, 15 * carb[-1][1] / cover), f"carbonation {carb[-1][1]} mm vs cover {cover} mm", "IS 456 Table 16")

    hc = _series(db, element.id, "half_cell_mv", as_of)
    if hc:
        p = hc[-1][1]
        add("Corrosion potential", 0 if p > -200 else 8 if p > -350 else 15, f"half-cell {p} mV CSE (more negative = higher corrosion likelihood)", "ASTM C876 (indicative)")

    cure = _series(db, element.id, "curing_days", as_of)
    if cure:
        need = re_.RULES["curing_min_days"]["opc"]
        short = max(0.0, need - cure[0][1])
        add("Curing shortfall", min(8, short * 2), f"cured {cure[0][1]} days vs minimum {need}", "IS 456 Cl. 13.5")

    worst_b = "VERIFIED"
    for b in batches:
        worst_b = re_.worst([worst_b, b.status if b.status in re_.SEV else "VERIFIED"])
    if any(b.status == "FLAGGED" for b in batches):
        add("Batch quality history", 15, "a flagged batch was used in this element", "BUILDGUARD traceability")
    elif any(b.status == "REVIEW_REQUIRED" for b in batches):
        add("Batch quality history", 7, "a batch with unresolved review was used here", "BUILDGUARD traceability")

    score = min(100.0, sum(f["points"] for f in factors))
    level = _level(score)

    # anomalies and per-metric trends
    anomalies, trends = [], {}
    for kind in ("crack_width_mm", "moisture_pct", "upv_km_s", "rebound_number", "carbonation_depth_mm", "half_cell_mv"):
        s = _series(db, element.id, kind, as_of)
        a = _anomaly(kind, s)
        if a:
            anomalies.append(a)
        sl = _slope_per_year(s)
        if sl is not None:
            trends[kind] = round(sl, 4)

    # overall trend from stored history (needs >= 2 earlier assessments to be meaningful)
    hist = db.scalars(select(RiskAssessment).where(RiskAssessment.element_id == element.id, RiskAssessment.computed_at < as_of)
                      .order_by(RiskAssessment.computed_at.desc()).limit(3)).all()
    if len(hist) < 2:
        trend = "INSUFFICIENT_DATA"
    else:
        delta = score - hist[-1].score
        trend = "DETERIORATING" if delta >= 8 else "IMPROVING" if delta <= -8 else "STABLE"

    recs = []
    if level == "High":
        recs += ["Arrange an engineer's inspection of this element.", "Carry out UPV and rebound survey; add half-cell/carbonation tests if reinforcement corrosion is suspected.",
                 "Review the drainage/waterproofing and any recorded flagged batches for this element."]
    elif level == "Moderate":
        recs += ["Increase monitoring frequency (crack width and moisture).", "Schedule an NDT check at the next inspection."]
    else:
        recs += ["Continue routine inspection."]
    if anomalies:
        recs.append("Verify the anomalous readings on site (instrument error vs real change).")
    if trend == "DETERIORATING":
        recs.append("Risk is rising versus earlier assessments: bring the next inspection forward.")

    result = {
        "element_id": element.id, "score": round(score, 1), "level": level, "trend": trend,
        "factors": sorted(factors, key=lambda f: -f["points"]), "anomalies": anomalies, "metric_trends_per_year": trends,
        "recommendations": recs, "computed_at": as_of.isoformat() + "Z",
        "disclaimer": "Risk indicator for prioritising inspection. Not a service-life prediction or a structural safety certificate.",
    }
    if persist:
        ra = RiskAssessment(project_id=element.project_id, element_id=element.id, computed_at=as_of, score=result["score"],
                            level=level, trend=trend, factors=result["factors"], anomalies=anomalies, recommendations=recs)
        db.add(ra)
        db.flush()
        changes.add(db, element.project_id, "risk_assessment", ra.id, "create", ra)
        audit.stage(db, user, "risk.assess", "element", element.id, element.project_id, {"score": result["score"], "level": level, "trend": trend})
        if level == "High" and (as_of >= utcnow() - timedelta(minutes=5)):
            raise_alert(db, element.project_id, "high_risk", "high", f"Durability risk HIGH (score {result['score']}) for element {element.name}.", element_id=element.id)
    return result
