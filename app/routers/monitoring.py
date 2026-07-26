"""Monitoring endpoints. Polls devices over SSH and returns health metrics."""
from __future__ import annotations

import asyncio

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, User
from app.routers.devices import connect_device, load_credentials, _visible_devices, _can_view
from app.schemas import DeviceStatus
from app.security import get_current_user

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


async def _collect(device: Device, db: Session) -> DeviceStatus:
    try:
        conn, bastion = await connect_device(device, db)
        try:
            # uptime
            uptime = (await conn.run("uptime -p", check=False)).stdout.strip() or None
            # cpu load avg (1 min)
            load_out = (await conn.run("cat /proc/loadavg", check=False)).stdout.strip()
            cpu_load = float(load_out.split()[0]) if load_out else None
            # memory %
            mem = (await conn.run(
                "free | awk '/Mem:/ {printf \"%.0f\", $3/$2*100}'", check=False
            )).stdout.strip()
            mem_pct = float(mem) if mem else None
            # disk %
            disk = (await conn.run(
                "df -P / | awk 'NR==2 {gsub(\"%\",\"\"); print $5}'", check=False
            )).stdout.strip()
            disk_pct = float(disk) if disk else None
            return DeviceStatus(
                id=device.id, name=device.name, host=device.host,
                reachable=True, cpu_load=cpu_load, mem_used_pct=mem_pct,
                disk_used_pct=disk_pct, uptime=uptime, tailscale=device.tailscale,
            )
        finally:
            conn.close()
            if bastion is not None:
                bastion.close()
    except Exception as exc:  # noqa: BLE001 - report any SSH failure as unreachable
        return DeviceStatus(
            id=device.id, name=device.name, host=device.host,
            reachable=False, message=str(exc)[:200], tailscale=device.tailscale,
        )


@router.get("/status", response_model=list[DeviceStatus])
async def status_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[DeviceStatus]:
    devices = list(db.scalars(_visible_devices(db, user)))
    if not devices:
        return []
    results = await asyncio.gather(*[_collect(d, db) for d in devices])
    return list(results)


@router.get("/status/{device_id}", response_model=DeviceStatus)
async def status_one(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> DeviceStatus:
    device = db.get(Device, device_id)
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    return await _collect(device, db)
