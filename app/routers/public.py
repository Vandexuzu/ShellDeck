"""Public, read-only health dashboard (no auth required).

Only active when an admin enables it in Settings (public_dashboard=True).
Shows device reachability + basic metrics, never any credentials or shell.
"""
from __future__ import annotations

import asyncio

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, SettingsRow
from app.routers.devices import connect_device, load_credentials

router = APIRouter(prefix="/api/public", tags=["public"])


def _public_enabled(db: Session) -> SettingsRow:
    row = db.get(SettingsRow, 1)
    if row is None or not row.public_dashboard:
        raise HTTPException(status_code=404, detail="Public dashboard is disabled")
    return row


async def _collect(device: Device, db: Session) -> dict:
    username, password, private_key = load_credentials(device)
    try:
        conn, bastion = await connect_device(device, db)
        try:
            cpu = (await conn.run("cat /proc/loadavg", check=False)).stdout.strip().split()[0]
            mem = (await conn.run("free | awk '/Mem:/ {printf \"%.0f\", $3/$2*100}'", check=False)).stdout.strip()
            disk = (await conn.run("df -P / | awk 'NR==2 {gsub(\"%\",\"\"); print $5}'", check=False)).stdout.strip()
            up = (await conn.run("uptime -p", check=False)).stdout.strip()
            return {
                "name": device.name, "host": device.host, "os": device.os,
                "reachable": True,
                "cpu_load": float(cpu) if cpu else None,
                "mem_used_pct": float(mem) if mem else None,
                "disk_used_pct": float(disk) if disk else None,
                "uptime": up or None,
            }
        finally:
            conn.close()
            if bastion is not None:
                bastion.close()
    except Exception:
        return {"name": device.name, "host": device.host, "os": device.os, "reachable": False}


@router.get("/status")
async def public_status(db: Session = Depends(get_db)) -> dict:
    _public_enabled(db)
    devices = db.scalars(select(Device)).all()
    results = await asyncio.gather(*[_collect(d, db) for d in devices])
    up = sum(1 for r in results if r["reachable"])
    return {"total": len(results), "up": up, "down": len(results) - up, "devices": list(results)}
