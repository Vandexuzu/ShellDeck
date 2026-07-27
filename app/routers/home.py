"""Home / dashboard summary — one round-trip aggregation of key state.

Widgets: stat cards (A), device health (B), recent activity (C), scheduled
tasks (D), quick actions (E - frontend only), docker overview (G), and a
security/system strip.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, Device, ScheduledTask, SessionLog, SettingsRow, User
from app.routers.devices import _visible_devices
from app.routers.monitoring import _collect
from app.security import get_current_user

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("/summary")
async def home_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    devices = list(db.scalars(_visible_devices(db, user)))
    device_ids = [d.id for d in devices]

    # --- A: stat cards ---------------------------------------------------
    total = len(devices)
    # Lightweight reachability: probe ALL visible devices in parallel (reuse
    # monitor collector). No artificial 10-device cap — every device counts
    # toward the online stat and the health list.
    if devices:
        statuses = await asyncio.gather(*[_collect(d, db) for d in devices])
    else:
        statuses = []
    online = sum(1 for s in statuses if s.reachable)
    agents = db.scalars(
        select(Agent).where(Agent.owner_id == user.id, Agent.connected.is_(True))
    ).all()
    agents_connected = len(agents)

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sessions_today = 0
    if device_ids:
        sessions_today = db.scalar(
            select(func.count(SessionLog.id)).where(
                SessionLog.device_id.in_(device_ids),
                SessionLog.started_at >= start_of_day,
            )
        ) or 0

    settings = db.get(SettingsRow, 1)
    channels = 0
    if settings:
        for v in (
            settings.telegram_token_enc, settings.discord_webhook, settings.ntfy_url,
            settings.gotify_url, settings.slack_webhook, settings.webhook_url, settings.email_to,
        ):
            if v:
                channels += 1
    alerts_enabled = bool(settings and settings.notify_enabled and channels > 0)

    # --- F: security/system strip ---------------------------------------
    twofa_users = db.scalar(
        select(func.count(User.id)).where(User.totp_secret.isnot(None))
    ) or 0
    oidc_on = bool(settings and settings.oidc_enabled)
    public_on = bool(settings and settings.public_dashboard)

    # --- B: device health -----------------------------------------------
    health = [
        {
            "id": s.id, "name": s.name, "host": s.host, "reachable": s.reachable,
            "cpu_load": s.cpu_load, "mem_used_pct": s.mem_used_pct,
            "disk_used_pct": s.disk_used_pct, "uptime": s.uptime,
        }
        for s in statuses
    ]

    # --- C: recent activity ---------------------------------------------
    recent = []
    if device_ids:
        rows = db.scalars(
            select(SessionLog)
            .where(SessionLog.device_id.in_(device_ids))
            .order_by(SessionLog.started_at.desc())
            .limit(10)
        ).all()
        dev_names = {d.id: d.name for d in devices}
        for r in rows:
            dur = None
            if r.ended_at:
                dur = int((r.ended_at - r.started_at).total_seconds())
            recent.append({
                "id": r.id,
                "device": dev_names.get(r.device_id, f"#{r.device_id}"),
                "user_id": r.user_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration": dur,
                "has_recording": bool(r.recording),
            })

    # --- D: scheduled tasks ---------------------------------------------
    tasks = db.scalars(
        select(ScheduledTask).where(ScheduledTask.owner_id == user.id)
        .order_by(ScheduledTask.enabled.desc(), ScheduledTask.next_run.asc().nulls_last())
        .limit(5)
    ).all()
    scheduled = [
        {
            "id": t.id, "name": t.name, "enabled": t.enabled,
            "next_run": t.next_run.isoformat() if t.next_run else None,
            "interval_minutes": t.interval_minutes, "run_once": t.run_once,
        }
        for t in tasks
    ]

    # --- G: docker overview (best-effort, online devices only) ---------
    docker = []
    reachable = [d for d in devices if d.id in {s.id for s in statuses if s.reachable}][:6]
    if reachable:
        async def _docker_count(d: Device) -> dict:
            try:
                from app.routers.docker import _run
                out, _, code = await _run(d, "docker ps -a --format '{{.State}}'", db, timeout=8)
                if code != 0:
                    return {"id": d.id, "name": d.name, "available": False, "running": 0, "total": 0}
                states = [ln.strip() for ln in out.splitlines() if ln.strip()]
                running = sum(1 for st in states if st.lower().startswith("up"))
                return {"id": d.id, "name": d.name, "available": True, "running": running, "total": len(states)}
            except Exception:
                return {"id": d.id, "name": d.name, "available": False, "running": 0, "total": 0}
        docker = await asyncio.gather(*[_docker_count(d) for d in reachable])

    return {
        "stats": {
            "devices_total": total,
            "online": online,
            "agents_connected": agents_connected,
            "sessions_today": sessions_today,
            "alerts_enabled": alerts_enabled,
            "alert_channels": channels,
        },
        "security": {
            "twofa_users": twofa_users,
            "oidc_enabled": oidc_on,
            "public_dashboard": public_on,
        },
        "device_health": health,
        "recent_sessions": recent,
        "scheduled": scheduled,
        "docker": docker,
    }
