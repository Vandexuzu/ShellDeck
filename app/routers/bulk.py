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
from app.routers.devices import connect_device, load_credentials, _can_access
from app.schemas import BulkResult, BulkRun
from app.security import get_current_user, operator_only
from app.audit import log_audit

router = APIRouter(prefix="/api/bulk", tags=["bulk"])


async def _run_on_device(device: Device, command: str, db: Session) -> BulkResult:
    username, password, private_key = load_credentials(device)
    try:
        conn, bastion = await connect_device(device, db)
        try:
            result = await conn.run(command, check=False, timeout=30)
            out = (result.stdout or "") + (result.stderr or "")
            return BulkResult(
                device_id=device.id, name=device.name, host=device.host,
                reachable=True, output=out[:20000],
            )
        finally:
            conn.close()
            if bastion is not None:
                bastion.close()
    except Exception as exc:  # noqa: BLE001
        return BulkResult(
            device_id=device.id, name=device.name, host=device.host,
            reachable=False, error=f"{type(exc).__name__}: {exc}",
        )


@router.post("/run", response_model=list[BulkResult])
async def bulk_run(payload: BulkRun, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[BulkResult]:
    devices = db.scalars(select(Device).where(Device.id.in_(payload.device_ids))).all()
    accessible = [d for d in devices if _can_access(db, d, user)]
    if len(accessible) != len(payload.device_ids):
        raise HTTPException(status_code=404, detail="Some devices not found or not accessible")
    results = await asyncio.gather(*[_run_on_device(d, payload.command, db) for d in accessible])
    log_audit(db, user, "bulk_run", f"command={payload.command[:200]} devices={[d.id for d in accessible]}")
    return list(results)

