"""Full-data backup export/import (admin only).

Exports users (without password hashes), devices (without secrets), snippets,
scheduled tasks, and settings as a single JSON document for migration/backup.
Secrets (passwords/keys, notification tokens) are intentionally NOT included —
the importer must re-enter credentials. On import, matching rows are recreated
with fresh IDs.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Device, ScheduledTask, SettingsRow, Snippet, User,
)
from app.security import get_current_user, admin_only

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/export")
def export_all(db: Session = Depends(get_db), _: object = Depends(admin_only)) -> Response:
    users = [{"username": u.username, "role": u.role, "is_admin": u.is_admin} for u in db.scalars(select(User))]
    devices = [{
        "name": d.name, "host": d.host, "port": d.port, "username": d.username,
        "auth_method": d.auth_method, "os": d.os, "notes": d.notes, "tags": d.tags,
        "tailscale": d.tailscale,
    } for d in db.scalars(select(Device))]
    snippets = [{"name": s.name, "command": s.command} for s in db.scalars(select(Snippet))]
    tasks = [{
        "name": t.name, "command": t.command,
        "device_ids": json.loads(t.device_ids or "[]"),
        "interval_minutes": t.interval_minutes, "cron": t.cron,
        "enabled": t.enabled, "run_once": t.run_once, "run_at": t.run_at,
    } for t in db.scalars(select(ScheduledTask))]
    settings = db.get(SettingsRow, 1)
    payload = {
        "version": 1,
        "exported_at": datetime.now().isoformat(),
        "users": users,
        "devices": devices,
        "snippets": snippets,
        "scheduled_tasks": tasks,
        "settings": {
            "monitor_interval": settings.monitor_interval if settings else 60,
            "public_dashboard": settings.public_dashboard if settings else False,
        } if settings else {},
    }
    return Response(content=json.dumps(payload, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=shelldeck-backup.json"})


@router.post("/import")
def import_all(payload: dict, db: Session = Depends(get_db), _: object = Depends(admin_only)) -> dict:
    created = {"users": 0, "devices": 0, "snippets": 0, "tasks": 0}
    # Devices (secrets must be supplied by caller; here we keep placeholders empty).
    for d in payload.get("devices", []):
        db.add(Device(
            owner_id=1,  # imported into the admin/shared fleet
            name=d["name"], host=d["host"], port=d.get("port", 22),
            username=d.get("username", "root"), auth_method=d.get("auth_method", "password"),
            password_enc="", private_key_enc="",
            os=d.get("os", ""), notes=d.get("notes", ""), tags=d.get("tags", ""),
            tailscale=d.get("tailscale", False),
        ))
        created["devices"] += 1
    for s in payload.get("snippets", []):
        db.add(Snippet(owner_id=1, name=s["name"], command=s["command"]))
        created["snippets"] += 1
    for t in payload.get("scheduled_tasks", []):
        db.add(ScheduledTask(
            owner_id=1, name=t["name"], command=t["command"],
            device_ids=json.dumps(t.get("device_ids", [])),
            interval_minutes=t.get("interval_minutes", 60), cron=t.get("cron"),
            enabled=t.get("enabled", True), run_once=t.get("run_once", False),
            run_at=t.get("run_at"),
        ))
        created["tasks"] += 1
    # Users (no password — caller must set via UI; default viewer).
    from app.security import hash_password
    for u in payload.get("users", []):
        if db.scalars(select(User).where(User.username == u["username"])).first():
            continue
        role = u.get("role", "viewer")
        db.add(User(username=u["username"], password_hash=hash_password("changeme123"),
                    role=role, is_admin=(role == "admin")))
        created["users"] += 1
    db.commit()
    return {"imported": created}
