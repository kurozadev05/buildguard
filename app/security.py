import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, UTC

import jwt

from .config import settings

_dummy: str | None = None


def hash_password(password: str, iterations: int | None = None) -> str:
    it = iterations or settings.password_iterations
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, it)
    return f"pbkdf2_sha256${it}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return int(stored.split("$")[1]) < settings.password_iterations
    except Exception:
        return True


def burn_time() -> None:
    """Equalise timing for unknown accounts so login latency does not reveal whether an email exists."""
    global _dummy
    if _dummy is None:
        _dummy = hash_password("not-a-real-password")
    verify_password("x", _dummy)


def create_access_token(user_id: str, role: str, token_version: int = 0) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_minutes)
    jti = uuid.uuid4().hex
    tok = jwt.encode({"sub": user_id, "role": role, "tv": token_version, "jti": jti, "iss": settings.jwt_issuer,
                      "aud": settings.jwt_audience, "iat": now, "exp": exp}, settings.jwt_secret, algorithm=settings.jwt_alg)
    return tok, jti, exp.replace(tzinfo=None)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg], issuer=settings.jwt_issuer,
                      audience=settings.jwt_audience, options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]})
