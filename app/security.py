"""Security helpers: password hashing, JWT tokens, auth dependency."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

_PBKDF2_ITERS = 200_000


# ----------------------------- Password hashing -----------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters_s))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ------------------------------- TOTP (2FA) ---------------------------------
# RFC 6238 implementation using only the stdlib (no external dependency).
import base64 as _b64
import hmac as _hmac
import struct as _struct
import time as _time

_TOTP_STEP = 30
_TOTP_DIGITS = 6


def generate_totp_secret() -> str:
    """Return a new base32-encoded TOTP secret (no padding)."""
    return _b64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, counter: int) -> str:
    key = _b64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    msg = _struct.pack(">Q", counter)
    h = _hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0xF
    code = (_struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** _TOTP_DIGITS)
    return str(code).zfill(_TOTP_DIGITS)


def verify_totp(secret: str, token: str) -> bool:
    """Check a 6-digit TOTP token allowing +/-1 step of clock skew."""
    if not secret or not token:
        return False
    token = token.strip().replace(" ", "")
    if not token.isdigit() or len(token) != _TOTP_DIGITS:
        return False
    counter = int(_time.time() // _TOTP_STEP)
    return any(_totp_at(secret, counter + i) == token for i in (-1, 0, 1))


# ------------------------------- JWT tokens ---------------------------------
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# ------------------------------- Auth deps ----------------------------------
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_user_from_token_raw(token: str, db: Session) -> User | None:
    """Used by the WebSocket endpoint (which cannot use Depends easily)."""
    user_id = decode_token(token)
    if user_id is None:
        return None
    return db.get(User, user_id)


def require_role(*allowed: str):
    """Return a dependency that rejects users whose `role` is not in `allowed`."""
    allowed_set = set(allowed)

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(sorted(allowed_set))}",
            )
        return user

    return checker


def admin_only(user: User = Depends(get_current_user)) -> User:
    """Dependency: only `admin` users pass."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def operator_only(user: User = Depends(get_current_user)) -> User:
    """Dependency: `admin` or `operator` users pass."""
    if user.role not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return user
