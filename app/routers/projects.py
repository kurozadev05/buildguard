from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import EnvelopeRoute
from ..deps import WRITE_ROLES, accessible_project_ids, current_user, ensure_access, require_roles
from ..models import (Batch, BatchUsage, Building, Element, Floor, Project, ProjectMember, RiskAssessment, TestRecord, User)
from ..schemas import BuildingIn, ElementIn, FloorIn, MemberIn, ProjectIn
from ..services import audit, changes, core
from ..utils import to_dict

router = APIRouter(tags=["projects & locations"], route_class=EnvelopeRoute)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(require_roles("admin", "site_engineer", "qa")), db: Session = Depends(get_db)):
    p = Project(name=body.name, location=body.location, client_name=body.client_name, created_by=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id))
    changes.add(db, p.id, "project", p.id, "create", p)
    audit.stage(db, user, "project.create", "project", p.id, p.id, {"name": p.name})
    audit.commit(db)
    return to_dict(p)


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = accessible_project_ids(db, user)
    return [to_dict(p) for p in db.scalars(select(Project).where(Project.id.in_(ids)).order_by(Project.created_at))] if ids else []


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_access(db, user, project_id)
    return to_dict(core.get_or_404(db, Project, project_id, "Project"))


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn, user: User = Depends(require_roles("admin", "qa", "site_engineer")), db: Session = Depends(get_db)):
    ensure_access(db, user, project_id)
    core.get_or_404(db, User, body.user_id, "User")
    if db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == body.user_id)):
        raise HTTPException(409, "Already a member")
    db.add(ProjectMember(project_id=project_id, user_id=body.user_id))
    audit.stage(db, user, "project.add_member", "project", project_id, project_id, {"user_id": body.user_id})
    audit.commit(db)
    return {"project_id": project_id, "user_id": body.user_id}


@router.get("/projects/{project_id}/members")
def members(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_access(db, user, project_id)
    rows = db.execute(select(User).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id)).scalars()
    return [{"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role} for u in rows]


@router.post("/projects/{project_id}/buildings", status_code=201)
def add_building(project_id: str, body: BuildingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_access(db, user, project_id, WRITE_ROLES)
    b = Building(project_id=project_id, name=body.name)
    if body.id:
        b.id = body.id
    db.add(b)
    db.flush()
    changes.add(db, project_id, "building", b.id, "create", b)
    audit.stage(db, user, "building.create", "building", b.id, project_id, {"name": b.name})
    audit.commit(db)
    return to_dict(b)


@router.post("/buildings/{building_id}/floors", status_code=201)
def add_floor(building_id: str, body: FloorIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = core.get_or_404(db, Building, building_id, "Building")
    ensure_access(db, user, b.project_id, WRITE_ROLES)
    f = Floor(project_id=b.project_id, building_id=b.id, name=body.name, level=body.level)
    if body.id:
        f.id = body.id
    db.add(f)
    db.flush()
    changes.add(db, b.project_id, "floor", f.id, "create", f)
    audit.stage(db, user, "floor.create", "floor", f.id, b.project_id, {"name": f.name})
    audit.commit(db)
    return to_dict(f)


@router.post("/floors/{floor_id}/elements", status_code=201)
def add_element(floor_id: str, body: ElementIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = core.get_or_404(db, Floor, floor_id, "Floor")
    ensure_access(db, user, f.project_id, WRITE_ROLES)
    e = Element(project_id=f.project_id, floor_id=f.id, name=body.name, element_type=body.element_type, exposure=body.exposure)
    if body.id:
        e.id = body.id
    db.add(e)
    db.flush()
    changes.add(db, f.project_id, "element", e.id, "create", e)
    audit.stage(db, user, "element.create", "element", e.id, f.project_id, {"name": e.name, "type": e.element_type})
    audit.commit(db)
    return to_dict(e)


@router.get("/projects/{project_id}/tree")
def tree(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Project → Building → Floor → Element, with the batches recorded in each element."""
    ensure_access(db, user, project_id)
    usages = db.scalars(select(BatchUsage).where(BatchUsage.project_id == project_id)).all()
    bmap = {b.id: b for b in db.scalars(select(Batch).where(Batch.project_id == project_id))}
    per_el: dict[str, list] = {}
    for u in usages:
        b = bmap.get(u.batch_id)
        if b:
            per_el.setdefault(u.element_id, []).append({"batch_id": b.id, "batch_code": b.batch_code, "grade": b.grade, "status": b.status})
    floors_by_b: dict[str, list[Floor]] = {}
    els_by_f: dict[str, list[Element]] = {}
    for f in db.scalars(select(Floor).where(Floor.project_id == project_id).order_by(Floor.level)):
        floors_by_b.setdefault(f.building_id, []).append(f)
    for e in db.scalars(select(Element).where(Element.project_id == project_id)):
        els_by_f.setdefault(e.floor_id, []).append(e)
    out = []
    for bld in db.scalars(select(Building).where(Building.project_id == project_id)):
        floors = [{**to_dict(f), "elements": [{**to_dict(e), "batches": per_el.get(e.id, [])} for e in els_by_f.get(f.id, [])]}
                  for f in floors_by_b.get(bld.id, [])]
        out.append({**to_dict(bld), "floors": floors})
    return {"project_id": project_id, "buildings": out}


@router.get("/projects/{project_id}/handover-passport")
def handover_passport(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Owner-facing 'building quality passport': every batch, its tests, where it went, and latest durability risk."""
    ensure_access(db, user, project_id)
    project = core.get_or_404(db, Project, project_id, "Project")
    batches = db.scalars(select(Batch).where(Batch.project_id == project_id).order_by(Batch.created_at)).all()
    risks = {}
    for r in db.scalars(select(RiskAssessment).where(RiskAssessment.project_id == project_id).order_by(RiskAssessment.computed_at)):
        risks[r.element_id] = r
    entries = []
    for b in batches:
        tests = db.scalars(select(TestRecord).where(TestRecord.batch_id == b.id, TestRecord.is_current == True)).all()  # noqa: E712
        entries.append({"batch_code": b.batch_code, "grade": b.grade, "supplier": b.supplier, "delivery_date": b.delivery_date.isoformat() if b.delivery_date else None,
                        "status": b.status, "quantity_m3": b.quantity_m3, "locations": [loc["path"] for loc in core.batch_locations(db, b.id)],
                        "tests": [{"type": t.test_type, "age_days": t.age_days, "status": core.effective_status(t), "record_hash": t.record_hash,
                                   "tested_at": t.tested_at.isoformat() + "Z"} for t in tests]})
    integrity = audit.verify_chain(db)
    return {"project": to_dict(project), "generated_for": user.full_name, "audit_chain_ok": integrity["ok"], "batches": entries,
            "element_risk": [{"element_id": k, "score": v.score, "level": v.level, "trend": v.trend, "computed_at": v.computed_at.isoformat() + "Z"} for k, v in risks.items()],
            "note": "Quality records at handover. This is supporting evidence, not a structural safety certificate."}
