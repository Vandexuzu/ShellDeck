"""CRUD endpoints for managed devices."""
from __future__ import annotations

import json

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt, settings
from app.db import get_db
from app.models import Device, SessionLog
from app.schemas import DeviceCreate, DeviceOut, DeviceUpdate
from app.security import get_current_user, operator_only

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _owned(db: Session, device_id: int, user: User) -> Device:
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _to_out(device: Device) -> DeviceOut:
    return DeviceOut.model_validate(device)


@router.get("", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Device]:
    return list(db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.name)))


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Device:
    device = Device(
        owner_id=user.id,
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        auth_method=payload.auth_method,
        password_enc=encrypt(payload.password or ""),
        private_key_enc=encrypt(payload.private_key or ""),
        os=payload.os,
        notes=payload.notes,
        bastion_id=payload.bastion_id,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return _to_out(device)


@router.get("/export")
def export_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """Export the user's devices (without secrets — user re-enters creds on import)."""
    devices = list(db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.name)))
    payload = [
        {
            "name": d.name,
            "host": d.host,
            "port": d.port,
            "username": d.username,
            "auth_method": d.auth_method,
            "os": d.os,
            "notes": d.notes,
        }
        for d in devices
    ]
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=shelldeck-devices.json"},
    )


@router.get("/inventory/{fmt}")
def inventory_export(fmt: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """Export devices as an Ansible inventory (ini) or Terraform inventory (yaml)."""
    devices = list(db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.name)))
    if fmt == "ansible":
        lines = ["[shelldeck]", ""]
        for d in devices:
            lines.append(f"{d.name} ansible_host={d.host} ansible_port={d.port} ansible_user={d.username}")
        content = "\n".join(lines) + "\n"
        return Response(content=content, media_type="text/plain",
                        headers={"Content-Disposition": "attachment; filename=shelldeck-inventory.ini"})
    if fmt == "terraform":
        hosts = []
        for d in devices:
            hosts.append({
                "name": d.name,
                "connection": d.host,
                "user": d.username,
                "port": d.port,
                "os": d.os or "unknown",
            })
        yaml_block = "hosts = " + json.dumps(hosts, indent=2)
        content = f"# Terraform-style inventory for ShellDeck devices\n{yaml_block}\n"
        return Response(content=content, media_type="text/plain",
                        headers={"Content-Disposition": "attachment; filename=shelldeck-inventory.tf"})
    raise HTTPException(status_code=400, detail="fmt must be 'ansible' or 'terraform'")


@router.post("/import")
def import_devices(payload: list[DeviceCreate], db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Import devices from an export. Secrets must be supplied in each entry."""
    created = 0
    for item in payload:
        device = Device(
            owner_id=user.id,
            name=item.name,
            host=item.host,
            port=item.port,
            username=item.username,
            auth_method=item.auth_method,
            password_enc=encrypt(item.password or ""),
            private_key_enc=encrypt(item.private_key or ""),
            os=item.os,
            notes=item.notes,
            bastion_id=item.bastion_id if hasattr(item, "bastion_id") else None,
        )
        db.add(device)
        created += 1
    db.commit()
    return {"imported": created}


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Device:
    return _to_out(_owned(db, device_id, user))


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(device_id: int, payload: DeviceUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Device:
    device = _owned(db, device_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "password" and value:
            device.password_enc = encrypt(value)
        elif field == "private_key" and value:
            device.private_key_enc = encrypt(value)
        elif field in ("password", "private_key"):
            continue
        else:
            setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return _to_out(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    from app.models import SessionLog
    device = _owned(db, device_id, user)
    db.query(SessionLog).filter(SessionLog.device_id == device.id).delete()
    db.delete(device)
    db.commit()


def load_credentials(device: Device) -> tuple[str, str | None, str | None]:
    """Return (username, password, private_key) with decrypted secrets."""
    return device.username, decrypt(device.password_enc) or None, decrypt(device.private_key_enc) or None


def _ssh_opts(device: Device, tunnel: object | None = None) -> dict:
    """Build asyncssh connect options for a device. If `tunnel` (a bastion
    SSHClientConnection) is provided, the connection is routed through it."""
    username, password, private_key = load_credentials(device)
    opts: dict = {
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
    if tunnel is not None:
        opts["tunnel"] = tunnel
    return opts


async def connect_device(device: Device, db: Session) -> tuple[object, object | None]:
    """Open an SSH connection to `device`, routing through its bastion if set.

    Returns (conn, bastion_conn). The caller MUST close both (the bastion first
    is not required; closing conn then bastion_conn is safe). If no bastion is
    configured, bastion_conn is None.
    """
    bastion_conn = None
    if device.bastion_id is not None:
        bastion = db.get(Device, device.bastion_id)
        if bastion is not None and bastion.owner_id == device.owner_id:
            bastion_conn = await asyncssh.connect(**_ssh_opts(bastion))
            conn = await asyncssh.connect(**_ssh_opts(device, tunnel=bastion_conn))
            return conn, bastion_conn
    conn = await asyncssh.connect(**_ssh_opts(device))
    return conn, None


async def _probe_reachable(device: Device, db: Session) -> bool:
    """Return True if the device is reachable (directly or via bastion)."""
    try:
        conn, bastion = await connect_device(device, db)
        conn.close()
        if bastion:
            bastion.close()
        return True
    except Exception:
        return False
