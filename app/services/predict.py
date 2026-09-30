"""Early warning: estimate the 28-day strength from the 7-day result, so problems are caught three weeks sooner.
An ESTIMATE, never an acceptance result: only the actual 28-day test can verify or flag a batch."""
import statistics
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Batch, Sample, TestRecord
from .rules_engine import group_threshold, sample_mean

CUBE = "cube_compressive_strength"
DEFAULT_RATIO, DEFAULT_BAND = 0.65, (0.55, 0.75)         # rule of thumb for 7-day / 28-day strength; verify for your cement type
MIN_PAIRS = 5
NOTE = ("Estimate from the 7-day result only. Cement type, temperature and curing change the 7-to-28-day gain. "
        "Only the actual 28-day test can verify or flag this batch.")


def _history(db: Session, project_id: str) -> list[float]:
    by_sample: dict[str, dict[int, float]] = {}
    for t in db.scalars(select(TestRecord).where(TestRecord.project_id == project_id, TestRecord.is_current == True,           # noqa: E712
                                                  TestRecord.test_type == CUBE, TestRecord.age_days.in_([7, 28]), TestRecord.sample_id != None)):     # noqa: E711
        m = sample_mean(t.values)
        if m and t.sample_id:
            by_sample.setdefault(t.sample_id, {})[t.age_days or 0] = m
    return [d[7] / d[28] for d in by_sample.values() if 7 in d and 28 in d and 0.3 < d[7] / d[28] < 1.2]


@dataclass
class RatioModel:
    source: str
    pairs: int
    ratio: float
    band: tuple[float, float]


def ratio_model(history: list[float]) -> RatioModel:
    if len(history) >= MIN_PAIRS:
        h = sorted(history)
        r = statistics.median(h)
        lo, hi = h[max(0, int(len(h) * 0.1))], h[min(len(h) - 1, int(len(h) * 0.9))]
        lo, hi = min(lo, r - 0.03), max(hi, r + 0.03)                        # never claim more certainty than +-0.03
        return RatioModel("project_history", len(h), round(r, 3), (max(0.4, lo), min(0.95, hi)))
    return RatioModel("default_rule_of_thumb", len(history), DEFAULT_RATIO, DEFAULT_BAND)


def predict_project(db: Session, project_id: str, batch_id: str | None = None) -> list[dict]:
    model = ratio_model(_history(db, project_id))
    lo_r, hi_r = model.band
    q = select(TestRecord).where(TestRecord.project_id == project_id, TestRecord.is_current == True, TestRecord.test_type == CUBE,        # noqa: E712
                                 TestRecord.age_days == 7, TestRecord.sample_id != None)                                              # noqa: E711
    if batch_id:
        q = q.where(TestRecord.batch_id == batch_id)
    done28 = {t.sample_id for t in db.scalars(select(TestRecord).where(TestRecord.project_id == project_id, TestRecord.is_current == True,       # noqa: E712
                                                                       TestRecord.test_type == CUBE, TestRecord.age_days == 28))}
    batches = {b.id: b for b in db.scalars(select(Batch).where(Batch.project_id == project_id))}
    samples = {s.id: s for s in db.scalars(select(Sample).where(Sample.project_id == project_id))}
    out: list[dict[str, Any]] = []
    for t in db.scalars(q):
        m7, b = sample_mean(t.values), batches.get(t.batch_id)
        if not m7 or not b or t.sample_id in done28:
            continue
        thr = group_threshold(b.fck, b.grade)
        mean, low, high = m7 / model.ratio, m7 / hi_r, m7 / lo_r
        status = "ON_TRACK" if low >= thr else "AT_RISK" if high < thr else "WATCH"
        s = samples.get(t.sample_id or "")
        out.append({"batch_code": b.batch_code, "batch_id": b.id, "sample_code": s.sample_code if s else None, "grade": b.grade, "mean_7d_mpa": round(m7, 2),
                    "predicted_28d_mpa": {"estimate": round(mean, 1), "low": round(low, 1), "high": round(high, 1)}, "required_group_mean_mpa": thr,
                    "status": status, "basis": {"source": model.source, "pairs": model.pairs, "ratio": model.ratio}, "note": NOTE})
    order = {"AT_RISK": 0, "WATCH": 1, "ON_TRACK": 2}
    out.sort(key=lambda r: (order[r["status"]], r["predicted_28d_mpa"]["estimate"]))
    return out
