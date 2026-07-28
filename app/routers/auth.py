"""Authentication endpoints: register, login, current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import ChangePassword, Token, TotpSetup, UserCreate, UserOut
from app.security import (
    create_access_token,
    generate_totp_secret,
    get_current_user,
    hash_password,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/registration-open")
def registration_open(db: Session = Depends(get_db)) -> dict:
    """Public: tells the login page whether self-registration is still allowed."""
    return {"open": db.scalar(select(User).limit(1)) is None}


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> Token:
    # Self-registration is only allowed for the very first user (who becomes admin).
    # After that, new accounts are created by an admin via /api/users to avoid
    # open registration on a self-hosted panel.
    if db.scalar(select(User).limit(1)) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled — an admin must create accounts.",
        )
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="Username already registered")
    role = "admin"  # first user is always admin
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=role,
        is_admin=(role == "admin"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # Seed starter snippets for the very first account so a fresh install
    # already has useful commands (no manual setup needed).
    from app.db import _seed_default_snippets

    _seed_default_snippets()
    return Token(access_token=create_access_token(user.id))


@router.post("/change-password")
def change_password(
    payload: ChangePassword,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Let the logged-in user change their own password (verify old first)."""
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    # If 2FA is enabled, the TOTP code must be supplied. We read it from a
    # dedicated `totp` form field (preferred); fall back to the OAuth2 `scope`
    # field for backwards compatibility with older clients.
    if user.totp_secret:
        posted = await request.form()
        raw_totp = posted.get("totp")
        code = (raw_totp.strip() if isinstance(raw_totp, str) else "") or (
            (form.scopes[0] if isinstance(form.scopes, list) and form.scopes else "").strip()
        )
        if not verify_totp(user.totp_secret, code):
            raise HTTPException(
                status_code=401,
                detail="2FA code required",
                headers={"X-ShellDeck-Need-Totp": "1"},
            )
    return Token(access_token=create_access_token(user.id))


@router.get("/2fa/status")
def totp_status(current_user: User = Depends(get_current_user)) -> dict:
    """Whether the current user has 2FA enabled."""
    return {"enabled": bool(current_user.totp_secret)}


@router.get("/2fa/qr")
def totp_qr(current_user: User = Depends(get_current_user), fmt: str = "svg") -> dict:
    """Generate a fresh TOTP secret + a QR code (PNG data URL) for enrollment.

    The client shows the QR, then confirms with a valid TOTP code
    (POST /2fa/setup) before the secret is stored.
    """
    from app.config import settings
    import base64
    import io

    import qrcode

    secret = generate_totp_secret()
    label = f"{current_user.username}@{settings.app_name}"
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={settings.app_name}&algorithm=SHA1&digits=6&period=30"
    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"secret": secret, "otpauth_uri": uri, "qr_png": f"data:image/png;base64,{b64}"}


@router.post("/2fa/setup")
def totp_setup(payload: TotpSetup, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    """Enable 2FA. Verifies the code against a freshly generated secret, then stores it.

    Call GET /2fa/setup to obtain the secret + otpauth URI first, then confirm here.
    """
    if not verify_totp(payload.secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")
    current_user.totp_secret = payload.secret
    db.commit()
    return {"ok": True, "enabled": True}


@router.post("/2fa/disable")
def totp_disable(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    """Disable 2FA for the current user."""
    current_user.totp_secret = None
    db.commit()
    return {"ok": True, "enabled": False}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
