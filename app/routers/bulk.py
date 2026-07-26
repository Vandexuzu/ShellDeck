"""Bulk command runner: execute one command across multiple owned devices."""
from __future__ import annotations

import asyncio

import asyncssh

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, User
from app.routers.devices import load_credentials
from app.schemas import BulkResult, BulkRun
from app.security import get_current_user, operator_only

router = APIRouter(prefix="/api/bulk", tags=["bulk"])


async def _run_on_device(device: Device, command: str) -> BulkResult:
    username, password, private_key = load_credentials(device)
    opts = {
        "host": device.host,
        "port": device.port,
        "username": username,
        "known_hosts": None if settings.ssh_ignore_known_hosts else False,
        "connect_timeout": 10,
    }
    if private_key:
        opts["client_keys"] = [private_key]
    else:
        opts["password"] = password
    try:
        async with asyncssh.connect(**opts) as conn:
            result = await conn.run(command, check=False, timeout=30)
            out = (result.stdout or "") + (result.stderr or "")
            return BulkResult(
                device_id=device.id, name=device.name, host=device.host,
                reachable=True, output=out[:20000],
            )
    except Exception as exc:  # noqa: BLE001
        return BulkResult(
            device_id=device.id, name=device.name, host=device.host,
            reachable=False, error=f"{type(exc).__name__}: {exc}",
        )


@router.post("/run", response_model=list[BulkResult])
async def bulk_run(payload: BulkRun, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[BulkResult]:
    devices = db.scalars(
        select(Device).where(Device.id.in_(payload.device_ids), Device.owner_id == user.id)
    ).all()
    found = {d.id for d in devices}
    missing = [i for i in payload.device_ids if i not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Devices not found or not owned: {missing}")
    results = await asyncio.gather(*[_run_on_device(d, payload.command) for d in devices])
    return list(results)

