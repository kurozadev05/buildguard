"""Integrity screening and early warning: each check fires on the pattern it targets and stays quiet on honest data."""
import io
from datetime import date, timedelta

import pytest

from tests.conftest import make_png

PW = "Passw0rd!xx"


def H(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def w(client):
    def reg(email):
        return client.post("/api/auth/register", json={"email": email, "full_name": "Insight", "password": PW}).json()["access_token"]
    reg("ins.first@x.com")                                   # absorbs the auto-admin slot on an empty DB
    a, b = reg("ins.a@x.com"), reg("ins.b@x.com")
    p = client.post("/api/projects", json={"name": "Insights"}, headers=H(a)).json()["id"]
    return {"a": a, "b": b, "p": p}


def batch(client, w, grade="M25", supplier="Ins Supplier"):
    return client.post("/api/batches", json={"project_id": w["p"], "grade": grade, "quantity_m3": 10, "cement_content_kg_m3": 320, "wc_ratio": 0.45, "supplier": supplier}, headers=H(w["a"])).json()


def sample(client, w, b, cast):
    return client.post("/api/samples", json={"batch_id": b["id"], "cast_date": str(cast)}, headers=H(w["a"])).json()


def record(client, w, b, s, age, specs, tested=None, lab=None):
    body = {"batch_id": b["id"], "sample_id": s["id"] if s else None, "test_type": "cube_compressive_strength", "age_days": age, "values": {"specimens_mpa": specs}}
    if tested:
        body["tested_at"] = tested
    if lab:
        body["lab_name"] = lab
    r = client.post("/api/tests", json=body, headers=H(w["a"]))
    assert r.status_code == 201, r.text
    return r.json()


def checks(client, w, path="/api/integrity/projects/"):
    return {f["check"] for f in client.get(path + w["p"], headers=H(w["a"])).json()["findings"]}


def test_honest_data_raises_no_flags(client, w):
    b = batch(client, w)
    s = sample(client, w, b, date.today() - timedelta(days=28))
    record(client, w, b, s, 28, [31.2, 30.4, 32.1])
    assert checks(client, w) == set()
    r = client.get(f"/api/integrity/projects/{w['p']}", headers=H(w["a"])).json()
    assert r["level"] == "none" and "not evidence of misconduct" in r["disclaimer"]


def test_impossible_timeline_is_flagged_high(client, w):
    b = batch(client, w)
    s = sample(client, w, b, date.today() - timedelta(days=10))                     # only 10 days old...
    record(client, w, b, s, 28, [33.1, 32.4, 33.8])                             # ...but a 28-day result is recorded today
    r = client.get(f"/api/integrity/batches/{b['id']}", headers=H(w["a"])).json()
    f = next(x for x in r["findings"] if x["check"] == "timeline_impossible")
    assert f["severity"] == "high" and r["level"] == "high" and "18 days" in f["detail"] and f["tests"][0]["batch_code"] == b["batch_code"]


def test_copied_specimen_values_and_reused_files_are_flagged(client, w):
    b1, b2 = batch(client, w), batch(client, w)
    s1, s2 = sample(client, w, b1, date.today() - timedelta(days=30)), sample(client, w, b2, date.today() - timedelta(days=30))
    t1 = record(client, w, b1, s1, 28, [29.4, 30.1, 28.8])
    t2 = record(client, w, b2, s2, 28, [30.1, 28.8, 29.4])                      # same numbers, different order
    assert "duplicate_specimens" in checks(client, w)
    png = make_png()
    for t, bb in ((t1, b1), (t2, b2)):
        d = client.post("/api/documents", data={"project_id": w["p"], "kind": "report", "batch_id": bb["id"]}, files={"file": ("r.png", io.BytesIO(png), "image/png")}, headers=H(w["a"]))
        assert d.status_code == 201, d.text
        assert client.post(f"/api/documents/{d.json()['id']}/attach", json={"test_id": t["id"]}, headers=H(w["a"])).status_code == 200
    assert "document_reuse" in checks(client, w)


def test_too_uniform_and_lab_level_patterns(client, w):
    b = batch(client, w)
    for i in range(6):
        s = sample(client, w, b, date.today() - timedelta(days=40 + i))
        base = 28 + i
        record(client, w, b, s, 28, [float(base), float(base), float(base)], lab="Suspicious Lab")
    c = checks(client, w)
    assert {"too_uniform", "digit_preference", "lab_low_scatter"} <= c


def test_integrity_access_control(client, w):
    assert client.get(f"/api/integrity/projects/{w['p']}", headers=H(w["b"])).status_code == 403          # not a member
    assert client.get(f"/api/integrity/projects/{w['p']}").status_code == 401


def test_early_warning_uses_default_then_project_history(client, w):
    b = batch(client, w, grade="M25")
    s = sample(client, w, b, date.today() - timedelta(days=7))
    record(client, w, b, s, 7, [14.2, 13.8, 14.0])                                # 14.0 / 0.65 = 21.5; even the optimistic end (14.0 / 0.55 = 25.5) misses the M25 threshold
    p = client.get(f"/api/predict/batches/{b['id']}", headers=H(w["a"])).json()
    row = p["predictions"][0]
    assert p["estimate_only"] and row["status"] == "AT_RISK" and row["basis"]["source"] == "default_rule_of_thumb" and "Only the actual 28-day test" in row["note"]
    assert row["predicted_28d_mpa"]["low"] < row["predicted_28d_mpa"]["estimate"] < row["predicted_28d_mpa"]["high"]
    good = batch(client, w, grade="M25")
    gs = sample(client, w, good, date.today() - timedelta(days=7))
    record(client, w, good, gs, 7, [24.0, 24.4, 23.6])                            # 24 / 0.65 = 36.9; even the pessimistic end clears the threshold
    assert client.get(f"/api/predict/batches/{good['id']}", headers=H(w["a"])).json()["predictions"][0]["status"] == "ON_TRACK"
    for i in range(6):                                                                  # build project history: real 7/28 ratio here is ~0.80
        hb = batch(client, w)
        hs = sample(client, w, hb, date.today() - timedelta(days=40))
        record(client, w, hb, hs, 7, [24.0 + i * 0.2] * 3)
        record(client, w, hb, hs, 28, [30.0 + i * 0.25] * 3)
    row2 = client.get(f"/api/predict/batches/{b['id']}", headers=H(w["a"])).json()["predictions"][0]
    assert row2["basis"]["source"] == "project_history" and row2["basis"]["pairs"] >= 5 and 0.7 < row2["basis"]["ratio"] < 0.9
    assert row2["predicted_28d_mpa"]["estimate"] < row["predicted_28d_mpa"]["estimate"]        # this project gains less after day 7 than the default assumes => lower, more cautious estimate
    only = client.get(f"/api/predict/projects/{w['p']}?status=AT_RISK", headers=H(w["a"])).json()["predictions"]
    assert all(r["status"] == "AT_RISK" for r in only)
    assert client.get(f"/api/predict/projects/{w['p']}", headers=H(w["b"])).status_code == 403


def test_predictions_skip_samples_that_already_have_a_28_day_result(client, w):
    b = batch(client, w)
    s = sample(client, w, b, date.today() - timedelta(days=30))
    record(client, w, b, s, 7, [18.0, 18.2, 17.8])
    record(client, w, b, s, 28, [30.0, 30.4, 29.8])
    assert client.get(f"/api/predict/batches/{b['id']}", headers=H(w["a"])).json()["predictions"] == []
