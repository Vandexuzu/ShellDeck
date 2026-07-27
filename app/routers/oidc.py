"""Optional OIDC login (social / corporate SSO).

Disabled unless `oidc_enabled=true` in the environment. When enabled, the login
page shows a "Sign in with <provider>" button that redirects to the IdP. After
the user authorizes, the IdP redirects back to /api/oidc/callback with a code;
we exchange it for tokens, read the userinfo, and map the email to a local
ShellDeck account (auto-provisioning on first login if the email matches an
existing username OR creating a viewer account).

No external OIDC library is required — we use httpx (already a dependency) and
the standard authorization-code flow with PKCE.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import create_access_token, hash_password

router = APIRouter(prefix="/api/oidc", tags=["oidc"])

# In-memory PKCE/state store (single-instance; good enough for self-hosted).
_STATES: dict[str, str] = {}  # state -> code_verifier


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def _discover() -> dict:
    if not settings.oidc_discovery_url:
        raise HTTPException(status_code=400, detail="OIDC not configured")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(settings.oidc_discovery_url)
        r.raise_for_status()
        return r.json()


@router.get("/enabled")
def oidc_enabled() -> dict:
    return {"enabled": bool(settings.oidc_enabled and settings.oidc_discovery_url)}


@router.get("/login")
async def oidc_login(request: Request) -> Response:
    """Begin the OIDC authorization-code (PKCE) flow. Returns a redirect."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC disabled")
    disc = await _discover()
    state = secrets.token_urlsafe(24)
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    _STATES[state] = verifier
    redirect = str(request.base_url).rstrip("/") + "/api/oidc/callback"
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect,
        "scope": settings.oidc_scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    return Response(status_code=302, headers={"Location": url})


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> Response:
    verifier = _STATES.pop(state, None)
    if verifier is None:
        raise HTTPException(status_code=400, detail="Invalid OIDC state")
    disc = await _discover()
    token_url = disc["token_endpoint"]
    # The redirect URI must exactly match what we sent in /login.
    redirect = str(request.base_url).rstrip("/") + "/api/oidc/callback"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(token_url, data=data)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail="OIDC token exchange failed")
        tokens = r.json()
        userinfo_url = disc.get("userinfo_endpoint")
        if not userinfo_url:
            raise HTTPException(status_code=400, detail="OIDC userinfo endpoint missing")
        ui = await c.get(userinfo_url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        ui.raise_for_status()
        info = ui.json()

    email = info.get("email") or info.get("preferred_username") or info.get("sub")
    if not email:
        raise HTTPException(status_code=400, detail="OIDC did not return an email")
    username = email.split("@")[0] if "@" in email else email

    # Map to a local account (reuse existing username, else create viewer).
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(16)),
            role="viewer",
            is_admin=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token(user.id)
    # Redirect to the app with the token in the URL fragment.
    html = f"""<!doctype html><html><head><meta http-equiv="refresh" content="0;url=/#token={token}"></head>
<body>Signing in…</body></html>"""
    return Response(content=html, media_type="text/html")
