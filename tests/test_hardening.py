"""Security, contract, resilience and configuration tests (things a reviewer would try to break)."""
import io
import time
from datetime import date, datetime, timedelta, timezone, UTC

import jwt
import pytest

from app.config import Settings, settings
from app.middleware import limiter
from tests.conftest import make_png


def H(t):
    return {"Authorization": f"Bearer {t}"}


def reg(client, email, name="User X", pw="Passw0rd!xx"):
    r = client.post("/api/auth/register", json={"email": email, "full_name": name, "password": pw})
    assert r.status_code == 201, r.text
    return r.json()["access_token"], r.json()["user"]["id"]


def login(client, email, pw="Passw0rd!xx"):
    return client.post("/api/auth/login", json={"email": email, "password": pw})


@pytest.fixture(scope="module")
def world(client):
    """Admin + two isolated tenants (A and B), each with one project, batch and test."""
    admin, admin_id = reg(client, "root@hard.com", "Root")
    from app.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:                                  # promote directly: only the very first account is auto-admin
        db.get(User, admin_id).role = "admin"
        db.commit()
    ea, ea_id = reg(client, "eng.a@hard.com")
    eb, eb_id = reg(client, "eng.b@hard.com")
    out = {"admin": admin, "ea": ea, "eb": eb, "ea_id": ea_id, "eb_id": eb_id}
    for k, tok in (("a", ea), ("b", eb)):
        p = client.post("/api/projects", json={"name": f"Project {k}"}, headers=H(tok)).json()
        b = client.post(f"/api/projects/{p['id']}/buildings", json={"name": "Blk"}, headers=H(tok)).json()
        f = client.post(f"/api/buildings/{b['id']}/floors", json={"name": "F1"}, headers=H(tok)).json()
        e = client.post(f"/api/floors/{f['id']}/elements", json={"name": "Slab", "element_type": "slab"}, headers=H(tok)).json()
        batch = client.post("/api/batches", json={"project_id": p["id"], "grade": "M25", "quantity_m3": 10, "cement_content_kg_m3": 320, "wc_ratio": 0.45, "supplier": f"Sup{k}"}, headers=H(tok)).json()
        t = client.post("/api/tests", json={"batch_id": batch["id"], "test_type": "cube_compressive_strength", "age_days": 28,
                                            "values": {"specimens_mpa": [19.8, 20.4, 19.5]}}, headers=H(tok)).json()
        doc = client.post("/api/documents", data={"test_id": t["id"], "kind": "photo"}, files={"file": ("a.png", io.BytesIO(make_png()), "image/png")}, headers=H(tok)).json()
        out[k] = {"project": p["id"], "element": e["id"], "batch": batch, "test": t, "doc": doc["id"]}
    return out


# ───────────── response contract ─────────────
def test_envelope_success_and_error_shapes(client, world):
    r = client.get("/api/projects", headers=H(world["ea"]))
    j = r.envelope()
    assert j["success"] is True and isinstance(j["data"], list) and "meta" in j
    r = client.get("/api/batches", params={"limit": 5}, headers=H(world["ea"]))
    assert r.envelope()["meta"]["limit"] == 5
    e = client.get("/api/batches/does-not-exist", headers=H(world["ea"]))
    j = e.json()
    assert e.status_code == 404 and j["success"] is False and j["error"]["code"] == "NOT_FOUND" and j["error"]["request_id"] == e.headers["x-request-id"]
    assert client.get("/no/such/route").json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/batches").json()["error"]["code"] == "UNAUTHENTICATED"


def test_validation_errors_do_not_echo_secrets(client):
    r = client.post("/api/auth/register", json={"email": "bad", "full_name": "x", "password": "SuperSecretPassword123"})
    body = r.text
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR" and "SuperSecretPassword123" not in body


def test_internal_errors_are_generic(client, world, monkeypatch):
    from app.routers import projects
    monkeypatch.setattr(projects.core, "get_or_404", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SELECT * FROM users /srv/app/secret.py")))
    r = client.get(f"/api/projects/{world['a']['project']}", headers=H(world["ea"]))
    j = r.json()
    assert r.status_code == 500 and j["error"]["code"] == "INTERNAL_ERROR"
    assert "SELECT" not in r.text and "/srv/app" not in r.text and "Traceback" not in r.text and j["error"]["request_id"]


def test_pagination_bounds_enforced(client, world):
    assert client.get("/api/batches", params={"limit": 100000}, headers=H(world["ea"])).status_code == 422
    assert client.get("/api/batches", params={"limit": 0}, headers=H(world["ea"])).status_code == 422
    assert client.get("/api/tests", params={"offset": -1}, headers=H(world["ea"])).status_code == 422


# ───────────── authentication ─────────────
def test_login_does_not_reveal_account_existence(client, world):
    a = login(client, "eng.a@hard.com", "wrong-password-1")
    b = login(client, "nobody@hard.com", "wrong-password-1")
    assert a.status_code == b.status_code == 401 and a.json()["error"]["message"] == b.json()["error"]["message"]


def test_account_lockout_after_repeated_failures(client):
    reg(client, "lock@hard.com")
    for _ in range(settings.login_max_failures):
        assert login(client, "lock@hard.com", "bad-password-1").status_code == 401
    assert login(client, "lock@hard.com").status_code == 401         # correct password refused while locked, same generic error


def test_logout_revokes_token_and_change_password_revokes_all(client):
    tok, _ = reg(client, "tok@hard.com")
    assert client.get("/api/auth/me", headers=H(tok)).status_code == 200
    assert client.post("/api/auth/logout", headers=H(tok)).status_code == 200
    assert client.get("/api/auth/me", headers=H(tok)).status_code == 401
    t1 = login(client, "tok@hard.com").json()["access_token"]
    t2 = login(client, "tok@hard.com").json()["access_token"]
    assert client.post("/api/auth/change-password", json={"current_password": "wrong-one-123", "new_password": "N3wPassword!!"}, headers=H(t1)).status_code == 403
    new = client.post("/api/auth/change-password", json={"current_password": "Passw0rd!xx", "new_password": "N3wPassword!!"}, headers=H(t1)).json()["access_token"]
    assert client.get("/api/auth/me", headers=H(t2)).status_code == 401 and client.get("/api/auth/me", headers=H(t1)).status_code == 401
    assert client.get("/api/auth/me", headers=H(new)).status_code == 200


def test_forged_and_misissued_tokens_rejected(client, world):
    uid = world["ea_id"]
    now = datetime.now(UTC)
    base = {"sub": uid, "role": "admin", "tv": 0, "jti": "x" * 16, "iat": now, "exp": now + timedelta(hours=1)}
    for claims, key in (({**base, "iss": "evil", "aud": settings.jwt_audience}, settings.jwt_secret),
                        ({**base, "iss": settings.jwt_issuer, "aud": "other"}, settings.jwt_secret),
                        ({**base, "iss": settings.jwt_issuer, "aud": settings.jwt_audience}, "wrong-secret-of-some-length-123456"),
                        ({**base, "iss": settings.jwt_issuer, "aud": settings.jwt_audience, "exp": now - timedelta(hours=1)}, settings.jwt_secret)):
        assert client.get("/api/auth/me", headers=H(jwt.encode(claims, key, algorithm="HS256"))).status_code == 401
    none_tok = jwt.encode({**base, "iss": settings.jwt_issuer, "aud": settings.jwt_audience}, None, algorithm="none")
    assert client.get("/api/auth/me", headers=H(none_tok)).status_code == 401


def test_role_change_takes_effect_immediately_and_no_self_lockout(client, world):
    tok, uid = reg(client, "temp@hard.com")
    assert client.get("/api/auth/me", headers=H(tok)).status_code == 200
    assert client.patch(f"/api/auth/users/{uid}", json={"role": "auditor"}, headers=H(world["admin"])).status_code == 200
    assert client.get("/api/auth/me", headers=H(tok)).status_code == 401           # old token (old role) is dead
    me = client.get("/api/auth/me", headers=H(world["admin"])).json()
    assert client.patch(f"/api/auth/users/{me['id']}", json={"role": "lab"}, headers=H(world["admin"])).status_code == 409
    assert client.get("/api/auth/users", headers=H(world["ea"])).status_code == 403


# ───────────── authorization / IDOR ─────────────
def test_cross_tenant_access_is_blocked_everywhere(client, world):
    a, eb = world["a"], world["eb"]
    cases = [("get", f"/api/batches/{a['batch']['id']}"), ("get", f"/api/batches/{a['batch']['id']}/locations"),
             ("get", f"/api/batches/{a['batch']['id']}/impact"), ("get", f"/api/batches/{a['batch']['id']}/qr.png"),
             ("get", f"/api/batches/by-code/{a['batch']['batch_code']}"), ("get", f"/api/tests/{a['test']['id']}"),
             ("get", f"/api/tests/{a['test']['id']}/verify"), ("get", f"/api/documents/{a['doc']}/download"),
             ("get", f"/api/documents/{a['doc']}/verify"), ("get", f"/api/projects/{a['project']}"),
             ("get", f"/api/projects/{a['project']}/tree"), ("get", f"/api/projects/{a['project']}/handover-passport"),
             ("get", f"/api/dashboard/summary?project_id={a['project']}"), ("get", f"/api/elements/{a['element']}/batches"),
             ("get", f"/api/elements/{a['element']}/observations"), ("get", f"/api/sync/bootstrap?project_id={a['project']}"),
             ("get", f"/api/sync/pull?project_id={a['project']}"), ("get", f"/api/durability/projects/{a['project']}/risk-map"),
             ("get", f"/api/durability/elements/{a['element']}/history")]
    for m, url in cases:
        assert getattr(client, m)(url, headers=H(eb)).status_code in (403, 404), url
    writes = [("/api/samples", {"batch_id": a["batch"]["id"], "cast_date": str(date.today())}),
              ("/api/tests", {"batch_id": a["batch"]["id"], "test_type": "slump", "values": {"slump_mm": 80}}),
              ("/api/usage", {"batch_id": a["batch"]["id"], "element_id": a["element"]}),
              ("/api/observations", {"element_id": a["element"], "kind": "crack_width_mm", "value": 0.1}),
              ("/api/batches", {"project_id": a["project"], "grade": "M25", "quantity_m3": 5}),
              (f"/api/tests/{a['test']['id']}/review", {"decision": "accepted", "comment": "trying to bypass"}),
              (f"/api/tests/{a['test']['id']}/amend", {"values": {"specimens_mpa": [40, 40, 40]}, "reason": "forging a pass"}),
              (f"/api/durability/elements/{a['element']}/assess", None), (f"/api/projects/{a['project']}/members", {"user_id": world["eb_id"]})]
    for url, body in writes:
        r = client.post(url, json=body, headers=H(eb)) if body is not None else client.post(url, headers=H(eb))
        assert r.status_code in (403, 404), (url, r.status_code)
    # the victim's data is untouched
    assert client.get(f"/api/tests/{a['test']['id']}", headers=H(world["ea"])).json()["status"] == "FLAGGED"


def test_list_endpoints_only_return_own_tenant(client, world):
    eb = world["eb"]
    a_codes = {world["a"]["batch"]["batch_code"]}
    assert not a_codes & {b["batch_code"] for b in client.get("/api/batches", headers=H(eb)).json()}
    assert all(t["project_id"] == world["b"]["project"] for t in client.get("/api/tests", headers=H(eb)).json())
    assert all(d["project_id"] == world["b"]["project"] for d in client.get("/api/documents", headers=H(eb)).json())
    assert all(i["project_id"] == world["b"]["project"] for i in client.get("/api/investigations", headers=H(eb)).json())
    assert all(a["project_id"] == world["b"]["project"] for a in client.get("/api/alerts", headers=H(eb)).json())
    assert all(p["id"] == world["b"]["project"] for p in client.get("/api/projects", headers=H(eb)).json())
    assert client.get("/api/documents", params={"batch_id": world["a"]["batch"]["id"]}, headers=H(eb)).json() == []
    assert client.get("/api/audit", headers=H(eb)).status_code == 403          # site_engineer is not an audit role


def test_sync_cannot_touch_other_tenant_or_replay_foreign_ops(client, world):
    a, eb = world["a"], world["eb"]
    op = {"op_id": "shared-op-id-0001", "type": "test.create", "data": {"batch_id": a["batch"]["id"], "test_type": "slump", "values": {"slump_mm": 90}}}
    r = client.post("/api/sync/push", json={"ops": [op]}, headers=H(eb)).json()
    assert r["results"][0]["status"] == "rejected"
    ok_op = {"op_id": "shared-op-id-0002", "type": "observation.create", "data": {"element_id": a["element"], "kind": "moisture_pct", "value": 4.0}}
    assert client.post("/api/sync/push", json={"ops": [ok_op]}, headers=H(world["ea"])).json()["results"][0]["status"] == "applied"
    replay = client.post("/api/sync/push", json={"ops": [ok_op]}, headers=H(eb)).json()["results"][0]
    assert replay["status"] == "rejected" and "entity_id" not in replay          # B never sees A's stored result


def test_backfill_timestamps_admin_only_and_future_dates_rejected(client, world):
    a = world["a"]
    r = client.post(f"/api/durability/elements/{a['element']}/assess", params={"as_of": "2020-01-01T00:00:00"}, headers=H(world["ea"]))
    assert r.status_code == 403
    future = (datetime.utcnow() + timedelta(days=30)).isoformat()
    assert client.post("/api/observations", json={"element_id": a["element"], "kind": "crack_width_mm", "value": 0.1, "observed_at": future}, headers=H(world["ea"])).status_code == 422


# ───────────── uploads ─────────────
def test_upload_rejects_disguised_and_corrupt_files(client, world):
    tid, tok = world["a"]["test"]["id"], world["ea"]
    def up(name, data, ctype):
        return client.post("/api/documents", data={"test_id": tid}, files={"file": (name, io.BytesIO(data), ctype)}, headers=H(tok)).status_code
    assert up("shell.php.png", b"<?php system($_GET[0]); ?>", "image/png") == 415
    assert up("x.png", b"\x89PNG\r\n\x1a\n" + b"junk" * 10, "image/png") == 415          # magic bytes only, not a real image
    assert up("x.svg", b"<svg onload=alert(1)/>", "image/svg+xml") == 415
    assert up("big.png", make_png() + b"0" * (settings.max_upload_mb * 1024 * 1024), "image/png") in (413, 415)
    assert up("../../evil.png", make_png(), "image/png") == 201


def test_oversize_json_body_rejected_early(client, world):
    big = '{"name": "%s"}' % ("x" * (settings.max_json_body_kb * 1024 + 10))
    r = client.post("/api/projects", content=big, headers={**H(world["ea"]), "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ───────────── public QR page ─────────────
def test_public_qr_page_is_token_gated_and_minimal(client, world):
    tok = world["a"]["batch"]["public_token"]
    j = client.get(f"/api/public/passport/{tok}").json()
    assert "locations" not in j and "project_id" not in j and "id" not in j
    page = client.get(f"/p/{tok}")
    assert page.status_code == 200 and "default-src 'none'" in page.headers["content-security-policy"]
    assert client.get("/p/AAAAAAAAAAAA").status_code == 404 and client.get(f"/p/{world['a']['batch']['batch_code']}").status_code == 404


# ───────────── rate limiting ─────────────
def test_rate_limits_are_per_bucket_and_configurable(client, monkeypatch):
    limiter.hits.clear()
    monkeypatch.setattr(settings, "rate_limit_login_per_min", 3)
    codes = [client.post("/api/auth/login", json={"email": "x@y.com", "password": "nope-nope-1"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    r = client.post("/api/auth/login", json={"email": "x@y.com", "password": "nope-nope-1"})
    assert int(r.headers["retry-after"]) >= 1 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert client.get("/health").status_code == 200 and client.get("/ready").status_code == 200        # exempt
    assert client.get("/api/projects").status_code == 401                                             # other buckets unaffected
    limiter.hits.clear()


def test_rate_limiter_memory_is_bounded():
    from app.middleware import RateLimiter
    rl = RateLimiter()
    for i in range(5000):
        rl.check(f"k{i}", 10, now=1000.0)
    rl.check("late", 10, now=2000.0)
    for i in range(600):
        rl.check("late", 10, now=2000.0 + 61)                # triggers prune
    assert len(rl.hits) < 10


# ───────────── configuration & migrations ─────────────
def test_production_config_fails_fast():
    base = dict(_env_file=None, env="production", jwt_secret="x" * 40, cors_origins="https://app.example.com", seed_demo=False, password_iterations=600000)
    Settings(**base)
    for bad in ({"jwt_secret": "short"}, {"jwt_secret": "dev-secret-" + "a" * 40}, {"cors_origins": "*"}, {"seed_demo": True},
                {"ai_provider": "anthropic"}, {"password_iterations": 1000}):
        with pytest.raises(ValueError):
            Settings(**{**base, **bad})


def test_migrations_match_models(tmp_path, monkeypatch):
    """Alembic upgrade on an empty DB must produce exactly the schema the models declare (no drift)."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine
    from app.database import Base
    from app.main import ROOT
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setattr(settings, "database_url", url)          # migrations/env.py reads settings each run
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"render_as_batch": True, "compare_type": True}), Base.metadata)
    assert diff == [], diff


# ───────────── refresh tokens ─────────────
def test_refresh_rotation_reuse_detection_and_revocation(client):
    r = client.post("/api/auth/register", json={"email": "rf@hard.com", "full_name": "Rf User", "password": "Passw0rd!xx"}).json()
    assert r["refresh_token"] and r["access_token"]
    rt1 = r["refresh_token"]
    r2 = client.post("/api/auth/refresh", json={"refresh_token": rt1})
    assert r2.status_code == 200 and r2.json()["refresh_token"] != rt1
    assert client.get("/api/auth/me", headers=H(r2.json()["access_token"])).status_code == 200
    rt2 = r2.json()["refresh_token"]
    # replaying the already-used token = theft signal: it fails AND kills the whole family (including the newest token)
    assert client.post("/api/auth/refresh", json={"refresh_token": rt1}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": rt2}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": "x" * 40}).status_code == 401


def test_refresh_dies_on_password_change_and_logout(client):
    login1 = client.post("/api/auth/register", json={"email": "rf2@hard.com", "full_name": "Rf Two", "password": "Passw0rd!xx"}).json()
    at, rt = login1["access_token"], login1["refresh_token"]
    out = client.post("/api/auth/logout", json={"refresh_token": rt}, headers=H(at))
    assert out.status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": rt}).status_code == 401
    l2 = client.post("/api/auth/login", json={"email": "rf2@hard.com", "password": "Passw0rd!xx"}).json()
    client.post("/api/auth/change-password", json={"current_password": "Passw0rd!xx", "new_password": "An0therPass!!"}, headers=H(l2["access_token"]))
    assert client.post("/api/auth/refresh", json={"refresh_token": l2["refresh_token"]}).status_code == 401


def test_admin_password_reset_revokes_sessions_and_needs_admin(client, world):
    tok, uid = reg(client, "forgot@hard.com")
    assert client.post(f"/api/auth/users/{uid}/reset-password", headers=H(world["ea"])).status_code == 403
    r = client.post(f"/api/auth/users/{uid}/reset-password", headers=H(world["admin"])).json()
    assert client.get("/api/auth/me", headers=H(tok)).status_code == 401
    assert login(client, "forgot@hard.com", "Passw0rd!xx").status_code == 401
    assert login(client, "forgot@hard.com", r["temporary_password"]).status_code == 200


def test_ready_reports_dependency_state_in_words_only(client):
    """/ready must say what is degraded without leaking URLs, credentials or versions."""
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ready", "degraded")
    assert set(body["checks"]) == {"database", "redis", "ai"}
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] in ("ok", "not_configured", "unavailable")
    assert body["checks"]["ai"] in ("configured", "not_configured", "misconfigured")
    text = r.text.lower()
    assert "postgres" not in text and "redis://" not in text and "sk-" not in text


def test_nul_bytes_and_database_data_errors_are_client_errors_not_500s(client):
    """PostgreSQL cannot store NUL in text. Whatever the DB, that must surface as a clean 4xx, never a 500."""
    from sqlalchemy.exc import DataError
    from app.main import app

    tok, _ = reg(client, f"nul{time.time_ns()}@t.io")
    admin_headers = H(tok)
    r = client.post("/api/projects", headers=admin_headers, json={"name": "a\u0000b"})
    assert r.status_code in (201, 422), r.text            # SQLite stores it; PostgreSQL rejects it -> 422
    r = client.get("/api/advisor/knowledge", headers=admin_headers, params={"q": "x\u0000y"})
    assert r.status_code < 500

    @app.get("/__test_dataerror")
    def _raise():  # a synthetic DataError so the handler is verified on every database
        raise DataError("stmt", {}, Exception("invalid byte sequence"))
    r = client.get("/__test_dataerror")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "invalid byte" not in r.text                     # driver detail never reaches the client


def test_suite_is_isolated_from_the_developers_real_configuration():
    """A developer's .env.local (real database, Redis, AI key) must never reach automated tests."""
    from app.config import settings
    assert settings.env == "test"
    assert settings.database_url.startswith("sqlite") or "test" in settings.database_url.lower()
    assert settings.redis_url == ""
    assert settings.ai_provider == "none" and settings.ai_api_key == "" and settings.ai_fallback_api_key == ""
    assert settings.admin_email in ("", None)
