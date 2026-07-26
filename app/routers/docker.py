"""Docker manager: list / control containers on a device over SSH.

We shell out to the `docker` CLI on the remote host (via the existing SSH
connection) and parse its JSON output. No Docker SDK needed on the server.
"""
from __future__ import annotations

import asyncssh
import shlex

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, User
from app.routers.devices import load_credentials
from app.schemas import DockerContainer, DockerAction, DockerRun
from app.security import get_current_user, operator_only

router = APIRouter(prefix="/api/docker", tags=["docker"])


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


async def _run(device: Device, command: str, timeout: int = 30) -> tuple[str, str, int]:
    """Return (stdout, stderr, exit_status) from a command run on the device."""
    async with asyncssh.connect(**_connect_opts(device)) as conn:
        result = await conn.run(command, check=False, timeout=timeout)
        return result.stdout or "", result.stderr or "", result.exit_status or 0


def _parse_ps(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            import json
            obj = json.loads(line)
            out.append({
                "id": obj.get("ID", ""),
                "name": obj.get("Names", obj.get("Name", "")),
                "image": obj.get("Image", ""),
                "state": obj.get("State", ""),
                "status": obj.get("Status", ""),
                "ports": obj.get("Ports", ""),
            })
        except json.JSONDecodeError:
            continue
    return out


@router.get("/{device_id}/containers", response_model=list[DockerContainer])
async def list_containers(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    fmt = "{{json .}}"
    stdout, stderr, code = await _run(
        device,
        f"docker ps -a --format '{fmt}'",
        timeout=30,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker failed: {stderr.strip() or 'exit ' + str(code)}")
    return _parse_ps(stdout)


@router.get("/{device_id}/logs/{container_id}")
async def container_logs(device_id: int, container_id: str, lines: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    # guard against shell injection in container id
    if not container_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid container id")
    stdout, stderr, code = await _run(device, f"docker logs --tail {int(lines)} {container_id}", timeout=30)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker logs failed: {stderr.strip()}")
    return {"container_id": container_id, "logs": stdout}


@router.post("/{device_id}/action")
async def container_action(device_id: int, body: DockerAction, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    cid = body.container_id
    if not cid.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid container id")
    if body.action not in ("start", "stop", "restart", "pause", "unpause", "kill", "remove"):
        raise HTTPException(status_code=400, detail="Action must be start|stop|restart|pause|unpause|kill|remove")
    extra = " -f" if body.action == "remove" else ""
    stdout, stderr, code = await _run(device, f"docker {body.action} {cid}{extra}", timeout=60)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker {body.action} failed: {stderr.strip()}")
    return {"container_id": cid, "action": body.action, "ok": True}


@router.get("/{device_id}/stats")
async def container_stats(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    # `docker stats --no-stream` gives a one-shot snapshot (no live streaming needed).
    stdout, stderr, code = await _run(
        device,
        "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}'",
        timeout=30,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker stats failed: {stderr.strip()}")
    rows = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"name": parts[0], "cpu": parts[1], "mem": parts[2]})
    return {"stats": rows}


@router.post("/{device_id}/run")
async def docker_run(device_id: int, body: DockerRun, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Run an arbitrary `docker <command>` on the device (e.g. `images`, `network ls`,
    `run --rm alpine echo hi`, `compose -f app.yml up -d`)."""
    device = db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    cmd = body.command.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="Command is empty")
    if cmd.startswith("docker"):
        cmd = cmd[len("docker"):].lstrip()
    # exec `docker <args>` without a shell -> safe from `; rm -rf` injection.
    try:
        args = shlex.split(cmd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad command: {exc}")
    async with asyncssh.connect(**_connect_opts(device)) as conn:
        # `conn.run` only accepts a single command string (keyword-only options after),
        # so join args safely — each arg gets shell-quoted by shlex.join, neutralizing
        # any `; rm -rf` style injection.
        docker_cmd = "docker " + shlex.join(args)
        if body.pty:
            # A pseudo-terminal is allocated. For `docker exec`, drop the `-i` (interactive)
            # flag because there is no persistent interactive stdin in a one-shot call — keep
            # only `-t` so the command still runs with a TTY and returns its output.
            docker_cmd = docker_cmd.replace("exec -it", "exec -t").replace("exec -i ", "exec -t ")
        result = await conn.run(
            docker_cmd,
            check=False,
            timeout=120,
            request_pty=body.pty,
        )
        return {
            "command": "docker " + cmd,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "exit_status": result.exit_status or 0,
        }
