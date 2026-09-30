import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import current_user, require_roles
from ..errors import EnvelopeRoute
from ..models import RefreshToken, RevokedToken, User, utcnow
from ..schemas import ChangePasswordIn, LoginIn, LogoutIn, RefreshIn, RegisterIn, UserPatch
from ..security import burn_time, create_access_token, hash_password, needs_rehash, verify_password
from ..services import audit
from ..utils import to_dict

router = APIRouter(prefix="/auth", tags=["auth"], route_class=EnvelopeRoute)
raw_router = APIRouter(prefix="/auth", tags=["auth"])      # OAuth2 form endpoint must return the bare OAuth2 body

_FAIL = "Incorrect email or password"


def _user_out(u: User) -> dict:
    return to_dict(u, exclude={"password_hash", "failed_attempts", "locked_until", "token_version"})


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _issue(db: Session, u: User, family_id: str | None = None) -> dict:
    """Access token (short) + rotating refresh token (long, opaque, stored hashed)."""
    tok, _, exp = create_access_token(u.id, u.role, u.token_version)
    refresh = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=u.id, family_id=family_id or secrets.token_hex(16), token_hash=_hash(refresh),
                        token_version=u.token_version, expires_at=utcnow() + timedelta(days=settings.refresh_token_days)))
    db.commit()
    return {"access_token": tok, "token_type": "bearer", "expires_at": exp.isoformat() + "Z",
            "refresh_token": refresh, "user": _user_out(u)}


def _authenticate(db: Session, email: str, password: str) -> User:
    """Same 401 and similar timing for unknown email, wrong password, inactive or locked account."""
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not u:
        burn_time()
        raise HTTPException(401, _FAIL)
    now = utcnow()
    locked = bool(u.locked_until and u.locked_until > now)
    ok = verify_password(password, u.password_hash)
    if locked or not u.is_active or not ok:
        if not locked and u.is_active and not ok:
            u.failed_attempts += 1
            if u.failed_attempts >= settings.login_max_failures:
                u.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                u.failed_attempts = 0
                audit.stage(db, u, "user.locked", "user", u.id, None, {"minutes": settings.login_lock_minutes})
            audit.commit(db)
        raise HTTPException(401, _FAIL)
    if u.failed_attempts or u.locked_until or needs_rehash(u.password_hash):
        u.failed_attempts, u.locked_until = 0, None
        if needs_rehash(u.password_hash):
            u.password_hash = hash_password(password)
        db.commit()
    return u


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Self-registration always creates a 'site_engineer'. The admin is bootstrapped via ADMIN_EMAIL
    (dev/test/demo fall back to 'first user is admin'); admins change roles afterwards."""
    count = db.scalar(select(func.count(User.id))) or 0
    is_admin = False
    if settings.admin_email:
        is_admin = body.email == settings.admin_email.strip().lower()
    elif count == 0:
        if settings.env == "production":
            raise HTTPException(403, "Admin bootstrap is not configured (set ADMIN_EMAIL)")
        is_admin = True
    if count > 0 and not is_admin and not settings.allow_self_register:
        raise HTTPException(403, "Self-registration is disabled")
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(409, "Email already registered")
    u = User(email=body.email, full_name=body.full_name, password_hash=hash_password(body.password),
             role="admin" if is_admin else "site_engineer", language=body.language)
    db.add(u)
    db.flush()
    audit.stage(db, u, "user.register", "user", u.id, None, {"role": u.role})
    audit.commit(db)
    return _issue(db, u)


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    return _issue(db, _authenticate(db, body.email, body.password))


@raw_router.post("/token")
def token_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 form login (used by the Swagger 'Authorize' button)."""
    u = _authenticate(db, form.username, form.password)
    return {"access_token": create_access_token(u.id, u.role, u.token_version)[0], "token_type": "bearer"}


@router.post("/refresh")
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    """Exchange a refresh token for a new access + refresh pair (rotation). Re-using an already-used token
    is treated as theft: the whole token family is revoked."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(body.refresh_token)))
    bad = HTTPException(401, "Invalid or expired refresh token")
    if not row:
        raise bad
    now = utcnow()
    if row.revoked_at is not None:
        db.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at == None).values(revoked_at=now))  # noqa: E711
        db.commit()
        raise bad
    user = db.get(User, row.user_id)
    if row.expires_at < now or not user or not user.is_active or user.token_version != row.token_version:
        raise bad
    row.revoked_at = now
    return _issue(db, user, row.family_id)


@router.post("/logout")
def logout(request: Request, body: LogoutIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Revokes the presented access token immediately and, if given, the refresh-token family."""
    t = request.state.token
    db.execute(delete(RevokedToken).where(RevokedToken.exp < utcnow()))          # opportunistic cleanup
    db.execute(delete(RefreshToken).where(RefreshToken.expires_at < utcnow()))
    db.add(RevokedToken(jti=t["jti"], exp=utcnow() + timedelta(minutes=settings.access_token_minutes + 5)))
    if body and body.refresh_token:
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(body.refresh_token), RefreshToken.user_id == user.id))
        if row:
            db.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at == None).values(revoked_at=utcnow()))  # noqa: E711
    audit.stage(db, user, "user.logout", "user", user.id, None, {})
    audit.commit(db)
    return {"logged_out": True}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Changes the password and invalidates every previously issued token; returns a fresh one."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    audit.stage(db, user, "user.change_password", "user", user.id, None, {})
    audit.commit(db)
    return _issue(db, user)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, admin: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """No-email recovery: an admin sets a one-time temporary password (shown once) and all of that user's sessions are revoked."""
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    temp = secrets.token_urlsafe(12)
    u.password_hash = hash_password(temp)
    u.token_version += 1
    u.failed_attempts, u.locked_until = 0, None
    audit.stage(db, admin, "user.reset_password", "user", u.id, None, {})
    audit.commit(db)
    return {"user_id": u.id, "temporary_password": temp, "note": "Give this to the user once; they should change it immediately."}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)


@router.get("/users")
def users(limit: int = 100, offset: int = 0, _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.created_at).limit(max(1, min(limit, 200))).offset(max(0, offset)))
    return [_user_out(u) for u in rows]


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, admin: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_none=True)
    if u.id == admin.id and (changes.get("role") not in (None, "admin") or changes.get("is_active") is False):
        raise HTTPException(409, "Admins cannot demote or deactivate themselves")
    for k, v in changes.items():
        setattr(u, k, v)
    if "role" in changes or "is_active" in changes:
        u.token_version += 1                                # role/activation changes take effect immediately
    audit.stage(db, admin, "user.update", "user", u.id, None, changes)
    audit.commit(db)
    return _user_out(u)
