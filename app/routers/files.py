"""SFTP file manager: browse, read, write and delete files on a device."""
from __future__ import annotations

import asyncssh
import ipaddress
import socket

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
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
    if device is None or not _can_access(db, device, user):
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
    if device is None or not _can_access(db, device, user):
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


@router.get("/{device_id}/download")
async def download_file(device_id: int, path: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from fastapi.responses import Response
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                name = path.rsplit("/", 1)[-1] or "download"
                async with sftp.open(path, "rb") as f:
                    data = await f.read()
                return Response(
                    content=data,
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'},
                )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Download failed: {type(exc).__name__}: {exc}")


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


from fastapi import UploadFile, File, Form

MAX_UPLOAD = 50 * 1024 * 1024  # 50 MB safety cap

@router.post("/{device_id}/upload")
async def upload_file(
    device_id: int,
    path: str = Form("/"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(operator_only),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    if file.size and file.size > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    target_dir = path.rstrip("/") or "/"
    dest = f"{target_dir}/{file.filename}" if target_dir != "/" else f"/{file.filename}"
    try:
        async with asyncssh.connect(**_connect_opts(device)) as conn:
            async with conn.start_sftp_client() as sftp:
                try:
                    await sftp.stat(target_dir)
                except Exception:
                    await sftp.makedirs(target_dir)
                data = await file.read()
                async with sftp.open(dest, "wb") as f:
                    await f.write(data)
                return {"path": dest, "uploaded": len(data)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Upload failed: {type(exc).__name__}: {exc}")


import httpx

def _is_safe_fetch_url(url: str) -> bool:
    """Reject URLs that resolve to private/loopback/link-local addresses
    (SSRF protection for the server-side fetch in upload-link)."""
    from urllib.parse import urlparse

    p = urlparse(url)
    host = (p.hostname or "").strip().lower()
    if not host:
        return False
    # Block literal IPs in private ranges; also resolve hostnames and check.
    try:
        ips = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            ips = set()
            for info in infos:
                addr = info[4]
                ip_str = addr[0] if isinstance(addr, (tuple, list)) else str(addr)
                try:
                    ips.add(ipaddress.ip_address(ip_str))
                except ValueError:
                    continue
        except (socket.gaierror, UnicodeError):
            return False
    for ip in ips:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False
    return True


@router.post("/{device_id}/upload-link")
async def upload_link(
    device_id: int,
    url: str = Form(...),
    path: str = Form("/"),
    filename: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(operator_only),
) -> dict:
    if not _is_safe_fetch_url(url):
        raise HTTPException(status_code=400, detail="URL blocked: only public hosts are allowed")
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                name = filename or url.rsplit("/", 1)[-1].split("?")[0] or "download"
                if not name or name == "download" and "." not in name:
                    name = "download"
                target_dir = path.rstrip("/") or "/"
                dest = f"{target_dir}/{name}" if target_dir != "/" else f"/{name}"
                total = 0
                async with asyncssh.connect(**_connect_opts(device)) as conn:
                    async with conn.start_sftp_client() as sftp:
                        try:
                            await sftp.stat(target_dir)
                        except Exception:
                            await sftp.makedirs(target_dir)
                        async with sftp.open(dest, "wb") as f:
                            async for chunk in resp.aiter_bytes(65536):
                                await f.write(chunk)
                                total += len(chunk)
                return {"path": dest, "uploaded": total}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Upload-link failed: {type(exc).__name__}: {exc}")
