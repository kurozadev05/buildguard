import io
import uuid
from datetime import date, datetime, timedelta

from tests.conftest import make_png

PNG = make_png()


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def setup_project(client):
    r = client.post("/api/auth/register", json={"email": "Admin@Site.com", "full_name": "Admin One", "password": "Passw0rd!xx"})
    assert r.status_code == 201, r.text
    admin = r.json()["access_token"]
    if r.json()["user"]["role"] != "admin":                     # other test modules may have registered first: promote directly
        from app.database import SessionLocal
        from app.models import User
        with SessionLocal() as db:
            db.get(User, r.json()["user"]["id"]).role = "admin"
            db.commit()
        admin = client.post("/api/auth/login", json={"email": "admin@site.com", "password": "Passw0rd!xx"}).json()["access_token"]
    assert "password_hash" not in r.json()["user"]
    r = client.post("/api/auth/register", json={"email": "lab@site.com", "full_name": "Lab Guy", "password": "Passw0rd!xx"})
    lab_id = r.json()["user"]["id"]
    assert r.json()["user"]["role"] == "site_engineer"          # self-registration never grants privileged roles
    pr = client.patch(f"/api/auth/users/{lab_id}", json={"role": "lab"}, headers=H(admin))
    assert pr.status_code == 200 and pr.json()["role"] == "lab"
    p = client.post("/api/projects", json={"name": "Tower A", "location": "Jaipur"}, headers=H(admin)).json()
    pid = p["id"]
    client.post(f"/api/projects/{pid}/members", json={"user_id": lab_id}, headers=H(admin))
    b = client.post(f"/api/projects/{pid}/buildings", json={"name": "Block A"}, headers=H(admin)).json()
    f = client.post(f"/api/buildings/{b['id']}/floors", json={"name": "Floor 3", "level": 3}, headers=H(admin)).json()
    e1 = client.post(f"/api/floors/{f['id']}/elements", json={"name": "Slab S2", "element_type": "slab"}, headers=H(admin)).json()
    e2 = client.post(f"/api/floors/{f['id']}/elements", json={"name": "Column C4", "element_type": "column"}, headers=H(admin)).json()
    lab = client.post("/api/auth/login", json={"email": "lab@site.com", "password": "Passw0rd!xx"}).json()["access_token"]
    return admin, lab, pid, e1["id"], e2["id"]


def test_full_flow(client):
    admin, lab, pid, slab, col = setup_project(client)

    # auth
    assert client.get("/api/batches").status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@site.com", "password": "wrong"}).status_code == 401

    # advisor
    r = client.post("/api/advisor/checklist", json={"grade": "M25", "element_type": "slab", "exposure": "moderate", "quantity_m3": 40, "explain": True}, headers=H(admin))
    assert r.status_code == 200
    ck = r.json()
    assert ck["sample_plan"]["required_samples"] == 4
    assert any(i["key"] == "cube_28d" for i in ck["items"]) and ck["explanation"]["summary"]
    hi = client.post("/api/advisor/checklist", json={"grade": "M25"}, headers={**H(admin), "Accept-Language": "hi"}).json()
    assert any("परीक्षण" in i["name"] for i in hi["items"])
    kn = client.get("/api/advisor/knowledge", params={"q": "core test low strength"}, headers=H(admin)).json()
    assert kn["results"][0]["id"] == "K8"

    # batches
    good = client.post("/api/batches", json={"project_id": pid, "grade": "M25", "exposure": "moderate", "supplier": "UltraMix", "quantity_m3": 30,
                                             "cement_content_kg_m3": 320, "wc_ratio": 0.48, "delivery_date": str(date.today())}, headers=H(admin)).json()
    assert good["batch_code"].startswith("CON-M25-") and good["status"] == "PENDING"
    n = int(good["batch_code"].rsplit("-", 1)[1])
    bad = client.post("/api/batches", json={"project_id": pid, "grade": "M25", "supplier": "CityCrete", "quantity_m3": 20,
                                            "cement_content_kg_m3": 320, "wc_ratio": 0.48}, headers=H(admin)).json()
    assert bad["batch_code"] == f"CON-M25-{n + 1:03d}"
    # lab cannot register batches
    assert client.post("/api/batches", json={"project_id": pid, "grade": "M25", "quantity_m3": 5}, headers=H(lab)).status_code == 403
    # registration limits: grade below minimum for exposure
    low = client.post("/api/batches", json={"project_id": pid, "grade": "M20", "exposure": "moderate", "quantity_m3": 5,
                                            "cement_content_kg_m3": 320, "wc_ratio": 0.48}, headers=H(admin)).json()
    assert low["status"] == "FLAGGED"

    # QR + public passport
    qr = client.get(f"/api/batches/{good['id']}/qr.png", headers=H(admin))
    assert qr.status_code == 200 and qr.content[:4] == b"\x89PNG"
    assert client.get(f"/api/public/passport/{good['batch_code']}").status_code == 404      # sequential codes are NOT public keys
    pub = client.get(f"/api/public/passport/{good['public_token']}").json()
    assert pub["batch_code"] == good["batch_code"] and "locations" not in pub
    page = client.get(f"/p/{good['public_token']}")
    assert page.status_code == 200 and good["batch_code"] in page.text

    # samples + tests (good batch)
    s1 = client.post("/api/samples", json={"batch_id": good["id"], "cast_date": str(date.today() - timedelta(days=30))}, headers=H(lab)).json()
    assert s1["sample_code"] == good["batch_code"] + "-S1"
    t = client.post("/api/tests", json={"batch_id": good["id"], "sample_id": s1["id"], "test_type": "slump", "values": {"slump_mm": 80}}, headers=H(lab)).json()
    assert t["status"] == "VERIFIED" and t["seal_valid"]
    t28 = client.post("/api/tests", json={"batch_id": good["id"], "sample_id": s1["id"], "test_type": "cube_compressive_strength", "age_days": 28,
                                          "values": {"specimens_mpa": [31.0, 30.2, 31.8]}}, headers=H(lab)).json()
    assert t28["status"] == "VERIFIED"
    assert client.get(f"/api/batches/{good['id']}", headers=H(admin)).json()["status"] == "VERIFIED"
    assert client.get(f"/api/tests/{t28['id']}/verify", headers=H(admin)).json()["seal_valid"]

    # failing batch -> FLAGGED -> alert + investigation with staged actions
    s2 = client.post("/api/samples", json={"batch_id": bad["id"], "cast_date": str(date.today() - timedelta(days=28))}, headers=H(lab)).json()
    usage = client.post("/api/usage", json={"batch_id": bad["id"], "element_id": slab, "volume_m3": 12}, headers=H(admin))
    assert usage.status_code == 201 and "Slab S2" in usage.json()["path"]
    client.post("/api/usage", json={"batch_id": bad["id"], "element_id": col, "volume_m3": 8}, headers=H(admin))
    tf = client.post("/api/tests", json={"batch_id": bad["id"], "sample_id": s2["id"], "test_type": "cube_compressive_strength", "age_days": 28,
                                         "values": {"specimens_mpa": [19.8, 20.4, 19.5]}}, headers=H(lab)).json()
    assert tf["status"] == "FLAGGED"
    assert client.get(f"/api/batches/{bad['id']}", headers=H(admin)).json()["status"] == "FLAGGED"
    impact = client.get(f"/api/batches/{bad['id']}/impact", headers=H(admin)).json()
    assert len(impact["affected_locations"]) == 2 and impact["affected_volume_m3"] == 20
    invs = client.get("/api/investigations", params={"batch_id": bad["id"]}, headers=H(admin)).json()
    assert len(invs) == 1
    types = {a["action_type"] for a in invs[0]["actions"]}
    assert {"quarantine_batch", "rebound_hammer", "upv", "core_test", "structural_assessment"} <= types
    # cannot close while actions pending
    assert client.post(f"/api/investigations/{invs[0]['id']}/close", json={"closure_note": "trying to close early"}, headers=H(admin)).status_code == 409
    # complete a core test through the guided flow
    core_act = next(a for a in invs[0]["actions"] if a["action_type"] == "core_test" and a["element_id"] == slab)
    core = client.post("/api/tests", json={"batch_id": bad["id"], "test_type": "core_strength", "values": {"cores_equiv_cube_mpa": [22, 23, 24]},
                                           "investigation_action_id": core_act["id"]}, headers=H(admin)).json()
    assert core["status"] == "VERIFIED"
    inv = client.get(f"/api/investigations/{invs[0]['id']}", headers=H(admin)).json()
    assert next(a for a in inv["actions"] if a["id"] == core_act["id"])["status"] == "done"
    for a in inv["actions"]:
        if a["status"] == "pending":
            client.patch(f"/api/investigations/actions/{a['id']}", json={"status": "waived", "note": "covered by core test"}, headers=H(admin))
    closed = client.post(f"/api/investigations/{invs[0]['id']}/close", json={"closure_note": "Cores acceptable per Cl 17.4"}, headers=H(admin))
    assert closed.status_code == 200 and closed.json()["status"] == "closed"

    # engineer review overrides
    rv = client.post(f"/api/tests/{tf['id']}/review", json={"decision": "accepted", "comment": "Cores passed, accepted with note"}, headers=H(admin)).json()
    assert rv["effective_status"] == "VERIFIED"

    # append-only amendment
    am = client.post(f"/api/tests/{t['id']}/amend", json={"values": {"slump_mm": 82}, "reason": "Transcription error fixed"}, headers=H(admin))
    assert am.status_code == 201 and am.json()["version"] == 2
    assert client.get(f"/api/tests/{t['id']}", headers=H(admin)).json()["is_current"] is False

    # documents: hash, tamper detection
    up = client.post("/api/documents", data={"kind": "crushing_photo", "test_id": t28["id"], "latitude": "26.91", "longitude": "75.78"},
                     files={"file": ("cube.png", io.BytesIO(PNG), "image/png")}, headers=H(lab))
    assert up.status_code == 201, up.text
    doc = up.json()
    assert len(doc["sha256"]) == 64 and "storage_path" not in doc
    assert client.get(f"/api/documents/{doc['id']}/verify", headers=H(lab)).json()["ok"] is True
    assert client.post("/api/documents", data={"test_id": t28["id"]}, files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}, headers=H(lab)).status_code == 415
    assert client.post("/api/documents", data={"test_id": t28["id"]}, files={"file": ("fake.png", io.BytesIO(b"not a png"), "image/png")}, headers=H(lab)).status_code == 415

    # OCR: regex path + draft/confirm
    parsed = client.post("/api/ocr/parse-text", json={"text": "Lab report CON-M25-001-S1 Grade M25 tested 12/03/2026 28 days: 31.0 N/mm2, 30.2 N/mm2, 31.8 N/mm2"}, headers=H(lab)).json()
    assert parsed["fields"]["sample_code"] == "CON-M25-001-S1" and parsed["fields"]["specimens_mpa"] == [31.0, 30.2, 31.8] and parsed["fields"]["age_days"] == 28
    ex = client.post("/api/ocr/extract", data={"project_id": pid, "batch_id": good["id"]}, files={"file": ("rep.png", io.BytesIO(PNG), "image/png")}, headers=H(lab))
    assert ex.status_code == 201
    conf = client.post(f"/api/ocr/drafts/{ex.json()['draft_id']}/confirm", json={"batch_id": good["id"], "age_days": 7, "values": {"specimens_mpa": [19.0, 19.4, 19.2]}}, headers=H(lab))
    assert conf.status_code == 201 and conf.json()["status"] == "VERIFIED"

    # durability
    base = datetime.utcnow() - timedelta(days=330)
    for i, (crack, moist, upv) in enumerate([(0.05, 3.8, 4.3), (0.06, 4.0, 4.2), (0.09, 4.5, 4.0), (0.14, 5.4, 3.7), (0.2, 6.5, 3.3), (0.32, 7.5, 2.9)]):
        for kind, val in (("crack_width_mm", crack), ("moisture_pct", moist), ("upv_km_s", upv)):
            r = client.post("/api/observations", json={"element_id": slab, "kind": kind, "value": val, "observed_at": (base + timedelta(days=60 * i)).isoformat()}, headers=H(admin))
            assert r.status_code == 201
    levels = []
    for d in (240, 180, 120, 60, 0):
        res = client.post(f"/api/durability/elements/{slab}/assess", params={"as_of": (datetime.utcnow() - timedelta(days=d)).isoformat()}, headers=H(admin)).json()
        levels.append(res["level"])
    assert levels[0] in ("Low", "Moderate") and levels[-1] == "High"
    assert res["trend"] == "DETERIORATING" and res["factors"] and "not" in res["disclaimer"].lower()
    assert client.get(f"/api/durability/projects/{pid}/risk-map", headers=H(admin)).json()[0]["level"] == "High"

    # dashboard
    d = client.get("/api/dashboard/summary", params={"project_id": pid}, headers=H(admin)).json()
    assert d["totals"]["batches"] == 3 and d["batch_status"]["FLAGGED"] >= 1
    assert any(s["supplier"] == "CityCrete" for s in d["supplier_scorecards"])
    assert d["durability_risk"]["counts"].get("High", 0) >= 1
    assert isinstance(d["overdue_tests"], list) and d["sampling_shortfall"]

    # handover passport
    hp = client.get(f"/api/projects/{pid}/handover-passport", headers=H(admin)).json()
    assert hp["audit_chain_ok"] and len(hp["batches"]) == 3

    # audit chain intact
    v = client.get("/api/audit/verify", headers=H(admin)).json()
    assert v["ok"] and v["checked"] > 20
    assert client.get("/api/audit/verify", headers=H(lab)).status_code == 403


def test_audit_chain_detects_tampering(client):
    from app.database import SessionLocal
    from app.models import AuditLog
    from sqlalchemy import select
    admin, lab, pid, slab, col = setup_project_once(client)
    with SessionLocal() as db:
        row = db.scalars(select(AuditLog).where(AuditLog.action == "batch.create")).first()
        if row is None:
            client.post("/api/batches", json={"project_id": pid, "grade": "M30", "quantity_m3": 5, "cement_content_kg_m3": 330, "wc_ratio": 0.45, "exposure": "moderate"}, headers=H(admin))
            row = db.scalars(select(AuditLog).where(AuditLog.action == "batch.create")).first()
        assert client.get("/api/audit/verify", headers=H(admin)).json()["ok"]
        original = row.details
        row.details = {"tampered": True}
        db.commit()
        try:
            bad = client.get("/api/audit/verify", headers=H(admin)).json()
            assert bad["ok"] is False and bad["first_broken_id"] == row.id
        finally:                                   # restore so later tests share a clean DB
            row.details = original
            db.commit()
        assert client.get("/api/audit/verify", headers=H(admin)).json()["ok"] is True


def setup_project_once(client):
    r = client.post("/api/auth/login", json={"email": "admin@site.com", "password": "Passw0rd!xx"})
    admin = r.json()["access_token"]
    pid = next(p["id"] for p in client.get("/api/projects", headers=H(admin)).json() if p["name"] == "Tower A")
    return admin, None, pid, None, None
