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


def _fmt_uptime(days: int, hours: int, minutes: int) -> str:
    """Format an uptime like Linux `uptime -p`: 'up 3 days, 3 hours, 27 minutes'."""
    parts = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if not parts:
        return "up less than a minute"
    return "up " + ", ".join(parts)


async def _collect(device: Device, db: Session) -> DeviceStatus:
    try:
        conn, bastion = await connect_device(device, db)
        try:
            # Detect OS: trust the device.os field if set, otherwise probe.
            os_kind = (device.os or "").lower()
            if not os_kind:
                try:
                    uname = (await conn.run("uname -s", check=False)).stdout.strip()
                    os_kind = "windows" if not uname else "linux"
                except Exception:
                    os_kind = "linux"

            if os_kind == "windows":
                # PowerShell via SSH. Use -EncodedCommand (base64 UTF-16LE) to
                # avoid shell quoting pitfalls.
                ps = (
                    "$c=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; "
                    "$o=Get-CimInstance Win32_OperatingSystem; "
                    "$mp=($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/$o.TotalVisibleMemorySize*100; "
                    "$d=Get-PSDrive C; "
                    "$dp=($d.Used/($d.Used+$d.Free))*100; "
                    "$up=(Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
                    "Write-Output ('{0:0.0}|{1:0.0}|{2:0.0}|{3}|{4}|{5}' -f $c,$mp,$dp,$up.Days,$up.Hours,$up.Minutes)"
                )
                import base64
                b64 = base64.b64encode(ps.encode("utf-16-le")).decode()
                out = (await conn.run(f"powershell -NonInteractive -NoProfile -EncodedCommand {b64}", check=False)).stdout.strip()
                cpu_load = mem_pct = disk_pct = None
                uptime = None
                if out and "|" in out:
                    parts = out.split("|")
                    try: cpu_load = float(parts[0])
                    except Exception: cpu_load = None
                    try: mem_pct = float(parts[1])
                    except Exception: mem_pct = None
                    try: disk_pct = float(parts[2])
                    except Exception: disk_pct = None
                    # Format like Linux `uptime -p` for consistency.
                    try:
                        uptime = _fmt_uptime(int(parts[3]), int(parts[4]), int(parts[5]))
                    except Exception:
                        uptime = None
            else:
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
                os=os_kind,
            )
        finally:
            conn.close()
            if bastion is not None:
                bastion.close()
    except Exception as exc:  # noqa: BLE001 - report any SSH failure as unreachable
        return DeviceStatus(
            id=device.id, name=device.name, host=device.host,
            reachable=False, message=str(exc)[:200], tailscale=device.tailscale,
            os=(device.os or None),
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
