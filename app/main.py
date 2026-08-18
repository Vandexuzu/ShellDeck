"""FastAPI application factory and entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import settings
from app.db import init_db
from app.models import SettingsRow
from app.routers import (
    auth, devices, docker, files, monitoring, snippets, bulk, terminal, users, settings as settings_router, scheduled, public, agents, backup, oidc, home,
)
from app.notifications import monitor_loop
from app.routers.scheduled import scheduler_loop

import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bg = asyncio.create_task(monitor_loop(60))
    sched = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        bg.cancel()
        sched.cancel()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


# ----------------------------- Login rate limiting -------------------------
# In-memory, per-client-IP throttling on /api/auth/login to blunt brute-force.
# Self-hosted single instance: good enough; swap for Redis if you scale out.
from collections import defaultdict
from time import time

_LOGIN_FAILS: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW = 900      # 15 minutes
_LOGIN_MAX_FAILS = 10    # lock out after this many failures in the window


def _client_ip(request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def login_rate_limit(request, call_next):
    if request.url.path == "/api/auth/login" and request.method == "POST":
        ip = _client_ip(request)
        now = time()
        # Drop failures older than the window.
        _LOGIN_FAILS[ip] = [t for t in _LOGIN_FAILS[ip] if now - t < _LOGIN_WINDOW]
        if len(_LOGIN_FAILS[ip]) >= _LOGIN_MAX_FAILS:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed login attempts. Try again later."},
            )
        response = await call_next(request)
        # A 401/403 counts as a failed attempt; any other status resets the counter.
        if response.status_code in (401, 403):
            _LOGIN_FAILS[ip].append(now)
        else:
            _LOGIN_FAILS[ip].clear()
        return response
    return await call_next(request)


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(monitoring.router)
app.include_router(terminal.router)
app.include_router(snippets.router)
app.include_router(bulk.router)
app.include_router(files.router)
app.include_router(docker.router)
app.include_router(users.router)
app.include_router(settings_router.router)
app.include_router(scheduled.router)
app.include_router(public.router)
app.include_router(agents.router)
app.include_router(backup.router)
app.include_router(oidc.router)
app.include_router(home.router)

# Static frontend (served at web root).
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Agent installer artifacts (served generically so `curl .../install.sh | bash`
# needs NO token/secret on the cmdline — the per-device token is requested by the
# agent itself at first run via POST /api/agents/enroll).
AGENT_DIR = Path(__file__).parent.parent / "agent"


def _install_settings(db) -> "SettingsRow | None":
    return db.get(SettingsRow, 1)


def _render_installer(template_name: str, base: str, secret: str, hb: str, rc: str) -> str:
    tpl = (AGENT_DIR / template_name).read_text()
    return (
        tpl.replace("__URL__", base)
        .replace("__SECRET__", secret)
        .replace("__HB__", hb)
        .replace("__RC__", rc)
    )


@app.get("/install.sh")
def install_sh(request: Request):
    from app.db import get_db
    db = next(get_db())
    row = _install_settings(db)
    base = f"{request.url.scheme}://{request.url.netloc}"
    secret = row.enroll_secret if row and row.enroll_secret else ""
    hb = str(row.agent_heartbeat if row else 15)
    rc = str(row.agent_reconnect if row else 5)
    script = _render_installer("install.sh", base, secret, hb, rc)
    return Response(script, media_type="text/x-shellscript")


@app.get("/install.ps1")
def install_ps1(request: Request):
    from app.db import get_db
    db = next(get_db())
    row = _install_settings(db)
    base = f"{request.url.scheme}://{request.url.netloc}"
    secret = row.enroll_secret if row and row.enroll_secret else ""
    hb = str(row.agent_heartbeat if row else 15)
    rc = str(row.agent_reconnect if row else 5)
    script = _render_installer("install.ps1", base, secret, hb, rc)
    return Response(script, media_type="text/plain")


@app.get("/agent_client")
def agent_client():
    return FileResponse(AGENT_DIR / "client.py", media_type="text/plain")


@app.get("/")
def index() -> HTMLResponse:
    from pathlib import Path
    html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text()
    # Inject the app version as a cache-buster on the static bundle so browsers
    # re-fetch app.js / style.css after every version bump (avoids stale caching).
    try:
        base = (Path(__file__).parent.parent / "VERSION").read_text().strip()
    except Exception:
        base = "0"
    # Build a cache-buster that changes on every deploy: prefer a short git
    # commit hash; fall back to the app.js file mtime (changes when the bundle
    # is rebuilt/copied into the image) so browsers always re-fetch.
    ver = base
    try:
        import subprocess
        ghash = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if ghash:
            ver = f"{base}-{ghash}"
        else:
            raise RuntimeError("no git hash")
    except Exception:
        try:
            import os
            mtime = int(os.path.getmtime(Path(__file__).parent.parent / "frontend" / "static" / "app.js"))
            ver = f"{base}-{mtime}"
        except Exception:
            pass
    html = html.replace("{{VERSION}}", ver)
    return HTMLResponse(html)


@app.get("/login")
def login_page() -> FileResponse:
    from pathlib import Path
    return FileResponse(Path(__file__).parent.parent / "frontend" / "login.html")


@app.get("/public")
def public_dashboard() -> FileResponse:
    from pathlib import Path
    p = Path(__file__).parent.parent / "frontend" / "public.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Public dashboard disabled")
    return FileResponse(p)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
