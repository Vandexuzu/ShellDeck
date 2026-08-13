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
from app.routers.monitoring import _fmt_uptime

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
            os_kind = (device.os or "").lower()
            if not os_kind:
                try:
                    uname = (await conn.run("uname -s", check=False)).stdout.strip()
                    os_kind = "windows" if not uname else "linux"
                except Exception:
                    os_kind = "linux"

            if os_kind == "windows":
                import base64
                ps = (
                    "$c=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; "
                    "$o=Get-CimInstance Win32_OperatingSystem; "
                    "$mp=($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/$o.TotalVisibleMemorySize*100; "
                    "$d=Get-PSDrive C; "
                    "$dp=($d.Used/($d.Used+$d.Free))*100; "
                    "$up=(Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
                    "Write-Output ('{0:0.0}|{1:0.0}|{2:0.0}|{3}|{4}|{5}' -f $c,$mp,$dp,$up.Days,$up.Hours,$up.Minutes)"
                )
                b64 = base64.b64encode(ps.encode("utf-16-le")).decode()
                out = (await conn.run(f"powershell -NonInteractive -NoProfile -EncodedCommand {b64}", check=False)).stdout.strip()
                cpu = mem = disk = up = None
                if out and "|" in out:
                    p = out.split("|")
                    cpu, mem, disk = (p + [None, None, None])[:3]
                    try:
                        up = _fmt_uptime(int(p[3]), int(p[4]), int(p[5]))
                    except Exception:
                        up = None
            else:
                cpu = (await conn.run("cat /proc/loadavg", check=False)).stdout.strip().split()[0]
                mem = (await conn.run("free | awk '/Mem:/ {printf \"%.0f\", $3/$2*100}'", check=False)).stdout.strip()
                disk = (await conn.run("df -P / | awk 'NR==2 {gsub(\"%\",\"\"); print $5}'", check=False)).stdout.strip()
                up = (await conn.run("uptime -p", check=False)).stdout.strip()
            return {
                "name": device.name, "host": device.host, "os": os_kind,
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
        return {"name": device.name, "host": device.host, "os": (device.os or None), "reachable": False}


@router.get("/status")
async def public_status(db: Session = Depends(get_db)) -> dict:
    _public_enabled(db)
    devices = db.scalars(select(Device)).all()
    results = await asyncio.gather(*[_collect(d, db) for d in devices])
    up = sum(1 for r in results if r["reachable"])
    # Mask the last host octet on the public feed so internal IPs aren't
    # leaked to anonymous viewers (e.g. 10.0.0.42 -> 10.0.0.x).
    for r in results:
        h = r.get("host") or ""
        if h.count(".") == 3:  # IPv4
            prefix, _ = h.rsplit(".", 1)
            r["host"] = f"{prefix}.x"
        elif ":" in h:  # IPv6 — keep only the /64 prefix
            r["host"] = h.split(":")[0] + ":…"
    return {"total": len(results), "up": up, "down": len(results) - up, "devices": list(results)}
