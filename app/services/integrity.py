"""Result-integrity screening: statistical and consistency checks that make fabricated or copied lab results stand out.
INDICATIVE ONLY. A finding is a reason to look at the original paperwork, never proof of wrongdoing.
Read-only: nothing here changes a test's status."""
import statistics
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Batch, Document, Sample, TestRecord

CUBE = "cube_compressive_strength"
SEVERITY = {"high": 3, "medium": 2, "low": 1}
DISCLAIMER = ("Indicative screening only. A finding is a prompt to check the original lab paperwork and witness records; "
              "it is not evidence of misconduct and does not change any test status.")


def _specs(t: TestRecord) -> list[float]:
    s = (t.values or {}).get("specimens_mpa")
    return [float(x) for x in s] if isinstance(s, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in s) else []


def _ref(t: TestRecord, codes: dict[str, str]) -> dict:
    return {"test_id": t.id, "batch_code": codes.get(t.batch_id, "?"), "age_days": t.age_days, "lab": t.lab_name}


def _finding(check: str, severity: str, title: str, detail: str, refs: list[dict]) -> dict:
    return {"check": check, "severity": severity, "title": title, "detail": detail, "tests": refs}


def scan_project(db: Session, project_id: str) -> list[dict]:
    tests = [t for t in db.scalars(select(TestRecord).where(TestRecord.project_id == project_id, TestRecord.is_current == True,          # noqa: E712
                                                             TestRecord.test_type == CUBE))]
    codes = {b.id: b.batch_code for b in db.scalars(select(Batch).where(Batch.project_id == project_id))}
    samples = {s.id: s for s in db.scalars(select(Sample).where(Sample.project_id == project_id))}
    out: list[dict] = []

    # 1. impossible timeline: a "28-day" result recorded before the cubes were 28 days old
    for t in tests:
        s = samples.get(t.sample_id) if t.sample_id else None
        if s and t.age_days:
            due = s.cast_date + timedelta(days=t.age_days)
            early = (due - t.tested_at.date()).days
            if early > 1:
                out.append(_finding("timeline_impossible", "high", "Result dated before the cubes reached the stated age",
                                    f"A {t.age_days}-day result is dated {early} days before the cubes (cast {s.cast_date}) turned {t.age_days} days old.", [_ref(t, codes)]))

    # 2. identical specimen sets in different tests
    groups: dict[tuple, list[TestRecord]] = defaultdict(list)
    for t in tests:
        sp = _specs(t)
        if len(sp) >= 3:
            groups[tuple(sorted(round(x, 1) for x in sp))].append(t)
    for key, ts in groups.items():
        if len({t.sample_id or t.id for t in ts}) > 1:
            out.append(_finding("duplicate_specimens", "high", "Identical specimen strengths in different tests",
                                f"The same {len(key)} strengths {list(key)} appear in {len(ts)} separate tests. Independent cubes almost never match to 0.1 N/mm2.", [_ref(t, codes) for t in ts]))

    # 3. the same file attached to different tests/batches
    by_hash: dict[str, list[Document]] = defaultdict(list)
    for d in db.scalars(select(Document).where(Document.project_id == project_id)):
        by_hash[d.sha256].append(d)
    tmap = {t.id: t for t in tests}
    for docs in by_hash.values():
        ctx = {(d.test_id or d.batch_id) for d in docs if (d.test_id or d.batch_id)}
        if len(ctx) > 1:
            refs = [_ref(tmap[d.test_id], codes) for d in docs if d.test_id in tmap]
            out.append(_finding("document_reuse", "high", "The same evidence file is used for different results",
                                f"One file (sha256 {docs[0].sha256[:12]}...) is attached to {len(ctx)} different tests/batches.", refs))

    # 4. implausibly uniform specimens
    for t in tests:
        sp = _specs(t)
        if len(sp) >= 3 and statistics.mean(sp) >= 5:
            m = statistics.mean(sp)
            if statistics.pstdev(sp) / m < 0.003 and (max(sp) - min(sp)) <= 0.2:
                out.append(_finding("too_uniform", "medium", "Specimens agree unusually closely",
                                    f"{len(sp)} specimens {sp} vary by under 0.3%. Real cubes normally scatter by a few percent.", [_ref(t, codes)]))

    # 5. lab-level patterns
    by_lab: dict[str, list[TestRecord]] = defaultdict(list)
    for t in tests:
        if t.lab_name and _specs(t):
            by_lab[t.lab_name.strip().lower()].append(t)
    for ts in by_lab.values():
        vals = [x for t in ts for x in _specs(t)]
        if len(vals) >= 8 and sum(1 for x in vals if abs(x - round(x)) < 1e-9) / len(vals) >= 0.8:
            out.append(_finding("digit_preference", "low", "Almost all readings are whole numbers",
                                f"{len(vals)} readings from lab '{ts[0].lab_name}' are mostly integers; machines usually report to 0.1 N/mm2 or finer.", [_ref(t, codes) for t in ts[:10]]))
        covs = [statistics.pstdev(sp) / statistics.mean(sp) for t in ts if len(sp := _specs(t)) >= 3 and statistics.mean(sp) > 0]
        if len(covs) >= 5 and statistics.median(covs) < 0.006:
            out.append(_finding("lab_low_scatter", "medium", "One lab's results scatter far less than expected",
                                f"Median specimen scatter across {len(covs)} tests from '{ts[0].lab_name}' is {statistics.median(covs) * 100:.2f}%.", [_ref(t, codes) for t in ts[:10]]))

    out.sort(key=lambda f: -SEVERITY[f["severity"]])
    return out


def summarize(findings: list[dict]) -> dict:
    top = max((SEVERITY[f["severity"]] for f in findings), default=0)
    return {"level": {0: "none", 1: "low", 2: "medium", 3: "high"}[top], "count": len(findings), "findings": findings, "disclaimer": DISCLAIMER}
