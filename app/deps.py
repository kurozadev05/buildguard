from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .i18n import pick_lang
from .models import Project, ProjectMember, RevokedToken, User
from .security import decode_token

import jwt

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

ALL_ROLES = {"admin", "site_engineer", "qa", "lab", "client", "auditor"}
WRITE_ROLES = {"admin", "site_engineer", "qa"}          # register batches, usage, observations
TEST_ROLES = WRITE_ROLES | {"lab"}                        # record tests
REVIEW_ROLES = {"admin", "qa", "site_engineer"}          # review flagged results, investigations
AUDIT_ROLES = {"admin", "auditor", "qa"}


def current_user(request: Request, token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    unauth = HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    try:
        data = decode_token(token)
    except jwt.PyJWTError:
        raise unauth from None
    if db.get(RevokedToken, data["jti"]):
        raise unauth                                   # logged out
    user = db.get(User, data["sub"])
    if not user or not user.is_active or user.token_version != data.get("tv", -1):
        raise unauth                                   # deactivated, or password changed since issue
    request.state.token = data
    return user


def user_release_db(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """For endpoints that await slow external calls (AI providers): end the auth transaction so no pooled DB connection is held
    while waiting. Without this, N in-flight requests pin N connections and then all deadlock waiting for a second one.
    The user is detached first: rollback() would otherwise expire it and the next attribute read would silently check a connection out again."""
    db.expunge(user)
    db.rollback()
    return user


def require_roles(*roles: str):
    allowed = set(roles)

    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, f"Role '{user.role}' is not permitted for this action")
        return user

    return dep


def lang_dep(request: Request, lang: str | None = None) -> str:
    return pick_lang(request.headers.get("accept-language"), lang)


def accessible_project_ids(db: Session, user: User) -> list[str]:
    if user.role == "admin":
        return list(db.scalars(select(Project.id)))
    return list(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)))


def ensure_access(db: Session, user: User, project_id: str, roles: set[str] | None = None) -> None:
    """Project membership (admin sees all) plus optional role gate for writes."""
    if roles is not None and user.role not in roles:
        raise HTTPException(403, f"Role '{user.role}' is not permitted for this action")
    if user.role == "admin":
        if not db.get(Project, project_id):
            raise HTTPException(404, "Project not found")
        return
    member = db.scalar(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user.id
        )
    )
    if not member:
        raise HTTPException(403, "You are not a member of this project")
