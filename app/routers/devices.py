"""CRUD endpoints for managed devices."""
from __future__ import annotations

import json

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt, settings
from app.db import get_db
from app.models import Device, SessionLog, User
from app.schemas import DeviceCreate, DeviceOut, DeviceUpdate
from app.security import get_current_user, operator_only, admin_only

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _admin_user(db: Session) -> User | None:
    """The primary admin (is_admin, lowest id)."""
    return db.scalar(select(User).where(User.is_admin).order_by(User.id))


def _visible_devices(db: Session, user: User):
    """Devices a user may *see* (read-only cards / status).

    - admin:   every device
    - viewer:  every device
    - operator: the admin's shared fleet + the operator's own devices
    """
    if user.role in ("admin", "viewer"):
        return select(Device).order_by(Device.name)
    admin = _admin_user(db)
    admin_id = admin.id if admin else -1
    return select(Device).where(
        (Device.owner_id == admin_id) | (Device.owner_id == user.id)
    ).order_by(Device.name)


def _can_view(db: Session, device: Device, user: User) -> bool:
    """Whether the user may *view* a device (status, file browse, docker read).

    Admin and viewer may view any device; an operator may view the admin's
    fleet and their own devices.
    """
    if user.role in ("admin", "viewer"):
        return True
    admin = _admin_user(db)
    return device.owner_id == user.id or (admin is not None and device.owner_id == admin.id)


def _can_access(db: Session, device: Device, user: User) -> bool:
    """Whether the user may *act on* a device (shell, sftp write, docker, run).

    Admin: any device. Operator: only their own. Viewer: never.
    """
    if user.role == "admin":
        return True
    if user.role == "operator":
        return device.owner_id == user.id
    return False


def _owned(db: Session, device_id: int, user: User) -> Device:
    """Resolve a device the user is allowed to act on (admin: any, operator: own)."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if user.role == "admin":
        return device
    if user.role == "operator" and device.owner_id == user.id:
        return device
    raise HTTPException(status_code=404, detail="Device not found")


def _to_out(device: Device, db: Session | None = None) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    # Mark whether a live agent is linked to this device (for agent-terminal UI).
    if db is not None:
        from app.models import Agent
        from app.routers.agents import _LIVE
        try:
            agent = db.scalar(select(Agent).where(Agent.device_id == device.id))
            out.has_agent = bool(agent and agent.token in _LIVE)
        except Exception:
            out.has_agent = False
    return out


@router.get("", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Device]:
    return list(db.scalars(_visible_devices(db, user)))


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Device:
    # Operators own their own devices; admins own the shared fleet.
    owner_id = user.id
    device = Device(
        owner_id=owner_id,
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
        tags=payload.tags,
        tailscale=payload.tailscale,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return _to_out(device, db)


@router.get("/generate-key")
def generate_ssh_key(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Generate an ed25519 SSH keypair and return both private + public key (PEM)."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    kp = ed25519.Ed25519PrivateKey.generate()
    priv = kp.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = kp.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return {"private_key": priv, "public_key": pub}


@router.get("/tailscale/discover")
def tailscale_discover(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Discover Tailscale devices on the local network via `tailscale status --json`.

    Returns a list of nodes (name, ip, hostname, os) that are not yet added as
    ShellDeck devices. Requires the `tailscale` CLI on the host (the ShellDeck
    server box, e.g. your Tailscale node).
    """
    import json as _json
    import shutil
    import subprocess

    if shutil.which("tailscale") is None:
        return {"available": False, "nodes": [], "error": "tailscale CLI not found on host"}
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return {"available": True, "nodes": [], "error": out.stderr.strip()[:200]}
        data = _json.loads(out.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "nodes": [], "error": str(exc)[:200]}

    known_hosts = {d.host for d in db.scalars(_visible_devices(db, user)).all()}
    nodes = []
    # `Self` and `Peer` maps keyed by IP.
    for section in ("Self", "Peer"):
        for ip, node in (data.get(section) or {}).items():
            if ip in known_hosts:
                continue
            nodes.append({
                "ip": ip,
                "name": node.get("HostName") or node.get("DisplayName") or ip,
                "hostname": node.get("DNSName", "").rstrip(".") or None,
                "os": node.get("OS", "") or "",
                "online": bool(node.get("Online", False)),
            })
    return {"available": True, "nodes": nodes}


@router.get("/export")
def export_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """Export the user's devices (without secrets — user re-enters creds on import)."""
    devices = list(db.scalars(_visible_devices(db, user)))
    payload = [
        {
            "name": d.name,
            "host": d.host,
            "port": d.port,
            "username": d.username,
            "auth_method": d.auth_method,
            "os": d.os,
            "notes": d.notes,
            "tags": d.tags,
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
    devices = list(db.scalars(_visible_devices(db, user)))
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
    """Import devices from an export. Secrets must be supplied in each entry.
    Admins import into the shared fleet; operators own their imports."""
    admin = _admin_user(db)
    owner_id = admin.id if (admin and user.role == "admin") else user.id
    created = 0
    for item in payload:
        device = Device(
            owner_id=owner_id,
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
            tags=item.tags if hasattr(item, "tags") else "",
            tailscale=item.tailscale if hasattr(item, "tailscale") else False,
        )
        db.add(device)
        created += 1
    db.commit()
    return {"imported": created}


# ----------------------------- Bulk operations -----------------------------
class BulkIds(BaseModel):
    device_ids: list[int] = Field(min_length=1)


class BulkUpdate(BulkIds):
    tags: str | None = None
    notes: str | None = None
    os: str | None = None
    bastion_id: int | None = None


@router.delete("/bulk")
def bulk_delete_devices(payload: BulkIds, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Delete multiple devices at once (own devices for operators; all for admins)."""
    from app.models import SessionLog
    deleted = 0
    for did in payload.device_ids:
        device = db.get(Device, did)
        if device and (user.role == "admin" or device.owner_id == user.id):
            db.query(SessionLog).filter(SessionLog.device_id == device.id).delete()
            db.delete(device)
            deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.put("/bulk")
def bulk_update_devices(payload: BulkUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Apply a set of fields (tags, notes, os, bastion_id) to owned (or all) devices."""
    updated = 0
    for did in payload.device_ids:
        device = db.get(Device, did)
        if device and (user.role == "admin" or device.owner_id == user.id):
            if payload.tags is not None:
                device.tags = payload.tags
            if payload.notes is not None:
                device.notes = payload.notes
            if payload.os is not None:
                device.os = payload.os
            if payload.bastion_id is not None:
                device.bastion_id = payload.bastion_id
            updated += 1
    db.commit()
    return {"updated": updated}


# ----------------------------- Session audit log -----------------------------
@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user), q: str | None = None) -> list[dict]:
    """List shell sessions (audit trail) for the current user's devices.

    If `q` is provided, filter by device name/host, username, or recorded command text.
    """
    from app.models import SessionLog
    vis = _visible_devices(db, user)
    visible_ids = {d.id for d in db.scalars(vis).all()}
    query = db.query(SessionLog).filter(SessionLog.device_id.in_(visible_ids))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (SessionLog.commands.ilike(like)) |
            (SessionLog.transcript.ilike(like))
        )
    rows = query.order_by(SessionLog.started_at.desc()).limit(500).all()
    out = []
    # Cache device names/hosts so we can also match on them without extra queries.
    dev_cache = {}
    for r in rows:
        dev = dev_cache.get(r.device_id)
        if dev is None:
            dev = db.get(Device, r.device_id)
            dev_cache[r.device_id] = dev
        dev_name = dev.name if dev else "(deleted)"
        dev_host = dev.host if dev else ""
        uname = None
        if r.user_id:
            u = db.get(User, r.user_id)
            uname = u.username if u else None
        if q:
            needle = q.strip().lower()
            if needle not in (dev_name + " " + dev_host + " " + (r.commands or "") + " " + (r.transcript or "") + " " + (uname or "")).lower():
                continue
        out.append({
            "id": r.id,
            "device_id": r.device_id,
            "device_name": dev_name,
            "device_host": dev_host,
            "username": uname,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "duration_s": int((r.ended_at - r.started_at).total_seconds()) if r.ended_at and r.started_at else None,
            "commands": r.commands or "",
            "transcript": r.transcript or "",
            "has_recording": bool(r.recording),
        })
    return out


@router.get("/sessions/{session_id}/recording")
def get_session_recording(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Return the asciinema-style TTY recording for a session (if captured)."""
    from app.models import SessionLog as _SL
    vis = _visible_devices(db, user)
    visible_ids = {d.id for d in db.scalars(vis).all()}
    log = db.get(_SL, session_id)
    if log is None or log.device_id not in visible_ids:
        raise HTTPException(status_code=404, detail="Session not found")
    if not log.recording:
        return {"events": [], "width": 80, "height": 24}
    try:
        rec = json.loads(log.recording)
    except Exception:
        return {"events": [], "width": 80, "height": 24}
    return {"events": rec.get("events", []), "width": rec.get("width", 80), "height": rec.get("height", 24)}


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Device:
    return _to_out(_owned(db, device_id, user), db)


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
    return _to_out(device, db)


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


@router.get("/{device_id}/test")
async def test_connection(device_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Probe SSH connectivity to a device and return reachability + error detail."""
    device = _owned(db, device_id, user)
    try:
        conn, bastion = await connect_device(device, db)
        conn.close()
        if bastion:
            bastion.close()
        return {"ok": True, "message": f"Connected to {device.username}@{device.host}:{device.port}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)[:300]}
