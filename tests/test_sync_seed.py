import uuid
from tests.conftest import make_png
from datetime import date, datetime


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def login(client):
    return client.post("/api/auth/login", json={"email": "admin@site.com", "password": "Passw0rd!xx"}).json()["access_token"]


def test_offline_sync_push_pull(client):
    tok = login(client)
    pid = next(p["id"] for p in client.get("/api/projects", headers=H(tok)).json() if p["name"] == "Tower A")
    boot = client.get("/api/sync/bootstrap", params={"project_id": pid}, headers=H(tok)).json()
    cursor = boot["cursor"]
    assert boot["rules"]["exposure"] and boot["elements"]

    bid, sid, tid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    ops = [
        {"op_id": "op-" + uuid.uuid4().hex, "type": "batch.create", "client_ts": datetime.utcnow().isoformat(),
         "data": {"id": bid, "project_id": pid, "grade": "M30", "exposure": "moderate", "quantity_m3": 12, "cement_content_kg_m3": 330, "wc_ratio": 0.45, "supplier": "OfflineMix"}},
        {"op_id": "op-" + uuid.uuid4().hex, "type": "sample.create", "data": {"id": sid, "batch_id": bid, "cast_date": str(date.today())}},
        {"op_id": "op-" + uuid.uuid4().hex, "type": "test.create", "data": {"id": tid, "batch_id": bid, "sample_id": sid, "test_type": "slump", "values": {"slump_mm": 120, "placing_condition": "heavily_reinforced"}}},
        {"op_id": "op-" + uuid.uuid4().hex, "type": "test.create", "data": {"batch_id": "does-not-exist", "test_type": "slump", "values": {"slump_mm": 90}}},
        {"op_id": "op-" + uuid.uuid4().hex, "type": "test.create", "data": {"batch_id": bid, "test_type": "slump", "values": {}, "age_days": 0}},   # invalid age -> validation error
    ]
    r = client.post("/api/sync/push", json={"device_id": "phone-1", "ops": ops}, headers=H(tok)).json()
    st = [x["status"] for x in r["results"]]
    assert st == ["applied", "applied", "applied", "rejected", "rejected"], r
    assert r["results"][0]["batch_code"].startswith("CON-M30-")
    assert r["results"][2]["status"] == "applied" and "record_hash" in r["results"][2]

    # replay the same payload (network retry): nothing is duplicated
    again = client.post("/api/sync/push", json={"device_id": "phone-1", "ops": ops[:3]}, headers=H(tok)).json()
    assert [x["status"] for x in again["results"]] == ["duplicate"] * 3
    assert len(client.get("/api/batches", params={"project_id": pid, "grade": "M30"}, headers=H(tok)).json()) == 1

    # the failed ops left no partial data behind
    tests = client.get("/api/tests", params={"batch_id": bid}, headers=H(tok)).json()
    assert len(tests) == 1 and tests[0]["id"] == tid

    # another device pulls everything since its cursor
    pull = client.get("/api/sync/pull", params={"since": cursor, "project_id": pid}, headers=H(tok)).json()
    ents = {(c["entity"], c["op"]) for c in pull["changes"]}
    assert {("batch", "create"), ("sample", "create"), ("test", "create")} <= ents
    assert pull["next_cursor"] > cursor and pull["has_more"] is False
    assert client.get("/api/sync/pull", params={"since": pull["next_cursor"]}, headers=H(tok)).json()["changes"] == []

    # audit chain still valid after partial failures and rollbacks
    assert client.get("/api/audit/verify", headers=H(tok)).json()["ok"] is True


def test_security_basics(client):
    tok = login(client)
    # Bad/forged token
    assert client.get("/api/auth/me", headers=H(tok + "x")).status_code == 401
    # security headers
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and "x-request-id" in r.headers
    # validation errors are 422, not 500
    assert client.post("/api/batches", json={"grade": "bad"}, headers=H(tok)).status_code == 422
    # path traversal in filename never reaches the filesystem path
    pid = next(p["id"] for p in client.get("/api/projects", headers=H(tok)).json() if p["name"] == "Tower A")
    import io
    up = client.post("/api/documents", data={"project_id": pid, "kind": "photo"},
                     files={"file": ("../../etc/passwd.png", io.BytesIO(make_png()), "image/png")}, headers=H(tok))
    assert up.status_code == 201 and ".." not in up.json()["filename"]


def test_seed_demo_runs_and_is_consistent():
    """Seeds into a throwaway DB using the real services, then checks the story the demo tells."""
    import os
    import tempfile
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import Batch, RiskAssessment
    from app.services import audit
    from app.services.seed import seed_demo

    eng = create_engine(f"sqlite:///{tempfile.mkdtemp()}/seed.db")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as db:
        info = seed_demo(db)
        st = {b.batch_code: b.status for b in db.scalars(select(Batch))}
        assert list(st.values()).count("FLAGGED") >= 1 and "VERIFIED" in st.values() and "PENDING" in st.values() and "REVIEW_REQUIRED" in st.values(), st
        risks = db.scalars(select(RiskAssessment).order_by(RiskAssessment.computed_at)).all()
        assert any(r.level == "High" for r in risks)
        assert audit.verify_chain(db)["ok"]
