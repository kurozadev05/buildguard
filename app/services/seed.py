"""Demo data so the app is judge-ready after every (free-tier) restart. Uses the real services,
so demo results are validated by the same rules engine and land in the audit chain."""
from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import Building, Element, Floor, Project, ProjectMember, User, utcnow
from ..schemas import BatchIn, ObservationIn, SampleIn, TestIn, UsageIn
from ..security import hash_password
from . import audit, core, risk

DEMO_PASSWORD = "Demo@1234"
USERS = [
    ("admin@buildguard.demo", "Demo Admin", "admin"),
    ("engineer@buildguard.demo", "Site Engineer", "site_engineer"),
    ("qa@buildguard.demo", "QA Manager", "qa"),
    ("lab@buildguard.demo", "Lab Technician", "lab"),
    ("client@buildguard.demo", "Client Representative", "client"),
    ("auditor@buildguard.demo", "Govt Auditor", "auditor"),
]


def seed_demo(db: Session) -> dict:
    users = {}
    for email, name, role in USERS:
        u = User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD))
        db.add(u)
        users[role] = u
    db.flush()
    admin, eng, lab = users["admin"], users["site_engineer"], users["lab"]

    p = Project(name="Demo Tower - Jaipur", location="Jaipur, Rajasthan", client_name="Demo Developers", created_by=admin.id)
    db.add(p)
    db.flush()
    for u in users.values():
        db.add(ProjectMember(project_id=p.id, user_id=u.id))
    bld = Building(project_id=p.id, name="Block A")
    db.add(bld)
    db.flush()
    els = {}
    for lvl, fname in enumerate(["Ground", "First", "Second"]):
        f = Floor(project_id=p.id, building_id=bld.id, name=f"{fname} Floor", level=lvl)
        db.add(f)
        db.flush()
        for nm, typ in ([("Footing F1", "footing"), ("Column C1", "column")] if lvl == 0 else [(f"Slab S{lvl}", "slab"), (f"Beam B{lvl}", "beam"), (f"Column C{lvl+1}", "column")]):
            e = Element(project_id=p.id, floor_id=f.id, name=nm, element_type=typ, exposure="moderate")
            db.add(e)
            db.flush()
            els[nm] = e
    audit.stage(db, admin, "demo.seed", "project", p.id, p.id, {"note": "demo data"})
    audit.commit(db)

    today = utcnow().date()

    def batch(grade, sup, qty, delivered_days_ago, wc=0.48, cement=320, exposure="moderate"):
        b = core.create_batch(db, eng, BatchIn(project_id=p.id, grade=grade, exposure=exposure, supplier=sup, supplier_ref="DC-" + str(delivered_days_ago),
                                               delivery_date=today - timedelta(days=delivered_days_ago), quantity_m3=qty,
                                               cement_content_kg_m3=cement, wc_ratio=wc))
        audit.commit(db)
        return b

    def sample(b, cast_ago):
        s = core.create_sample(db, eng, SampleIn(batch_id=b.id, cast_date=today - timedelta(days=cast_ago)))
        audit.commit(db)
        return s

    def test(b, s, ttype, age, values, when_ago=0, who=lab):
        t = core.create_test(db, who, TestIn(batch_id=b.id, sample_id=s.id if s else None, test_type=ttype, age_days=age, values=values,
                                            lab_name="City Testing Lab", tested_at=utcnow() - timedelta(days=when_ago)))
        audit.commit(db)
        return t

    def use(b, el, vol):
        core.add_usage(db, eng, UsageIn(batch_id=b.id, element_id=els[el].id, volume_m3=vol, poured_at=utcnow() - timedelta(days=120)))
        audit.commit(db)

    # Batch 1: healthy M25 from a reliable supplier
    b1 = batch("M25", "UltraMix RMC", 30, 150)
    s1 = sample(b1, 150)
    test(b1, s1, "slump", None, {"slump_mm": 85, "placing_condition": "lightly_reinforced"}, 150)
    test(b1, s1, "cube_compressive_strength", 7, {"specimens_mpa": [19.2, 18.8, 19.6]}, 143)
    test(b1, s1, "cube_compressive_strength", 28, {"specimens_mpa": [31.0, 30.2, 31.8]}, 122)
    use(b1, "Slab S1", 18)
    use(b1, "Beam B1", 12)

    # Batch 2: FLAGGED - low 28-day strength. Shows traceability + guided investigation.
    b2 = batch("M25", "CityCrete Ltd", 20, 140, wc=0.52, cement=290)   # w/c and cement content also breach exposure limits
    s2 = sample(b2, 140)
    test(b2, s2, "slump", None, {"slump_mm": 160, "placing_condition": "lightly_reinforced"}, 140)
    test(b2, s2, "cube_compressive_strength", 28, {"specimens_mpa": [19.8, 20.4, 19.5]}, 112)
    use(b2, "Slab S2", 12)
    use(b2, "Column C3", 8)

    # Batch 3: needs review (early-age strength on the low side)
    b3 = batch("M30", "UltraMix RMC", 15, 20)
    s3 = sample(b3, 20)
    test(b3, s3, "slump", None, {"slump_mm": 120, "placing_condition": "heavily_reinforced"}, 20)
    test(b3, s3, "cube_compressive_strength", 7, {"specimens_mpa": [17.0, 16.5, 17.4]}, 13)
    use(b3, "Column C2", 9)

    # Batch 4: brand new, pending
    batch("M25", "UltraMix RMC", 10, 2)

    # Durability history on Slab S2 (uses flagged batch) and Slab S1 (healthy). Assessments backfilled monthly.
    def obs(el, kind, value, days_ago):
        core.add_observation(db, eng, ObservationIn(element_id=els[el].id, kind=kind, value=value, observed_at=utcnow() - timedelta(days=days_ago)))

    s2_series = [(360, 0.05, 3.8, 4.2), (300, 0.06, 4.0, 4.1), (240, 0.08, 4.4, 4.0), (180, 0.12, 5.0, 3.8), (120, 0.16, 5.6, 3.6), (60, 0.22, 6.4, 3.3), (10, 0.31, 7.2, 3.0)]
    for d, crack, moist, upv in s2_series:
        obs("Slab S2", "crack_width_mm", crack, d)
        obs("Slab S2", "moisture_pct", moist, d)
        obs("Slab S2", "upv_km_s", upv, d)
    for d in (360, 240, 120, 10):
        obs("Slab S1", "crack_width_mm", 0.04, d)
        obs("Slab S1", "moisture_pct", 3.5, d)
        obs("Slab S1", "upv_km_s", 4.4, d)
    audit.commit(db)
    for el in ("Slab S2", "Slab S1"):
        for d in (300, 240, 180, 120, 60, 0):
            risk.assess_element(db, admin, els[el], as_of=utcnow() - timedelta(days=d))
        audit.commit(db)
    return {"project_id": p.id, "password": DEMO_PASSWORD, "users": [u[0] for u in USERS]}
