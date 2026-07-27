"""FastAPI application factory and entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
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


@app.get("/")
def index() -> FileResponse:
    from pathlib import Path
    return FileResponse(Path(__file__).parent.parent / "frontend" / "index.html")


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
