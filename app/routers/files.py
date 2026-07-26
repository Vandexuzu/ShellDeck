"""SFTP file manager: browse, read, write and delete files on a device."""
from __future__ import annotations

import asyncssh

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, User
from app.routers.devices import load_credentials, _can_view, _can_access
from app.schemas import FileEntry, FilePath, FileWrite
from app.security import get_current_user, operator_only

router = APIRouter(prefix="/api/files", tags=["files"])


def _connect_opts(device: Device) -> dict:
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
    return opts


async def _list_dir(sftp, path: str) -> list[FileEntry]:
    entries = []
    for item in await sftp.readdir(path):
        name = item.filename
        a = item.attrs
        full = f"{path.rstrip('/')}/{name}"
        is_dir = a.type == 2  # ssh2.SFTPAttrs: 2 == directory
        entries.append(FileEntry(name=name, path=full, is_dir=is_dir, size=a.size or 0, mtime=a.mtime))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


@router.get("/{device_id}/browse", response_model=list[FileEntry])
async def browse(device_id: int, path: str = "/", db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[FileEntry]:
    device = db.get(Device, device_id)
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                target = path or "/"
                # Allow returning the parent dir entry too by listing parent of target.
                parent = "/".join(target.rstrip("/").split("/")[:-1]) or "/"
                entries = await _list_dir(sftp, target)
                return entries
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"SFTP failed: {type(exc).__name__}: {exc}")


@router.post("/{device_id}/read")
async def read_file(device_id: int, body: FilePath, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(body.path, "r") as f:
                    data = await f.read(2_000_000)
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                return {"path": body.path, "content": data}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Read failed: {type(exc).__name__}: {exc}")


@router.post("/{device_id}/write")
async def write_file(device_id: int, body: FileWrite, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(body.path, "w") as f:
                    await f.write(body.content)
                return {"path": body.path, "written": len(body.content)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Write failed: {type(exc).__name__}: {exc}")


@router.post("/{device_id}/mkdir")
async def mkdir(device_id: int, body: FilePath, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.mkdir(body.path)
                return {"path": body.path, "created": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Mkdir failed: {type(exc).__name__}: {exc}")


@router.post("/{device_id}/delete")
async def delete_file(device_id: int, body: FilePath, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                try:
                    await sftp.remove(body.path)
                except Exception:
                    await sftp.rmtree(body.path)
                return {"path": body.path, "deleted": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Delete failed: {type(exc).__name__}: {exc}")
