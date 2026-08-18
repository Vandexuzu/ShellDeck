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
from app.routers.devices import connect_device, load_credentials, _can_view, _can_access
from app.routers.agents import device_agent_token, agent_exec_relay
from app.schemas import DockerContainer, DockerAction, DockerRun
from app.security import get_current_user, operator_only
from app.audit import log_audit

router = APIRouter(prefix="/api/docker", tags=["docker"])


async def _run(device: Device, command: str, db: Session, timeout: int = 30) -> tuple[str, str, int]:
    """Return (stdout, stderr, exit_status) from a command run on the device."""
    conn, bastion = await connect_device(device, db)
    try:
        result = await conn.run(command, check=False, timeout=timeout)
        return result.stdout or "", result.stderr or "", result.exit_status or 0
    finally:
        conn.close()
        if bastion is not None:
            bastion.close()


async def _run_smart(device: Device, command: str, db: Session, timeout: int = 30) -> tuple[str, str, int]:
    """Run a command on the device, preferring a live agent tunnel when the device
    is only reachable via the agent (no inbound SSH). Falls back to direct SSH."""
    token = device_agent_token(db, device.id)
    if token is not None:
        out, code = await agent_exec_relay(device.id, command, db, timeout=timeout, capture_code=True)
        return out, "", code
    return await _run(device, command, db, timeout=timeout)
    conn, bastion = await connect_device(device, db)
    try:
        result = await conn.run(command, check=False, timeout=timeout)
        return result.stdout or "", result.stderr or "", result.exit_status or 0
    finally:
        conn.close()
        if bastion is not None:
            bastion.close()


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
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    fmt = "{{json .}}"
    stdout, stderr, code = await _run_smart(
        device,
        f"docker ps -a --format '{fmt}'",
        db,
        timeout=30,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker failed: {stderr.strip() or 'exit ' + str(code)}")
    return _parse_ps(stdout)


@router.get("/{device_id}/logs/{container_id}")
async def container_logs(device_id: int, container_id: str, lines: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    # guard against shell injection in container id
    if not container_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid container id")
    stdout, stderr, code = await _run_smart(device, f"docker logs --tail {int(lines)} {container_id}", db, timeout=30)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker logs failed: {stderr.strip()}")
    return {"container_id": container_id, "logs": stdout}


@router.post("/{device_id}/action")
async def container_action(device_id: int, body: DockerAction, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_access(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    cid = body.container_id
    if not cid.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid container id")
    if body.action not in ("start", "stop", "restart", "pause", "unpause", "kill", "remove"):
        raise HTTPException(status_code=400, detail="Action must be start|stop|restart|pause|unpause|kill|remove")
    extra = " -f" if body.action == "remove" else ""
    stdout, stderr, code = await _run_smart(device, f"docker {body.action} {cid}{extra}", db, timeout=60)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"docker {body.action} failed: {stderr.strip()}")
    log_audit(db, user, "docker_action", f"device={device.name} container={cid} action={body.action}")
    return {"container_id": cid, "action": body.action, "ok": True}


@router.get("/{device_id}/stats")
async def container_stats(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    device = db.get(Device, device_id)
    if device is None or not _can_view(db, device, user):
        raise HTTPException(status_code=404, detail="Device not found")
    # `docker stats --no-stream` gives a one-shot snapshot (no live streaming needed).
    stdout, stderr, code = await _run_smart(
        device,
        "docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}\\t{{.MemPerc}}'",
        db,
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
    if device is None or not _can_access(db, device, user):
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
    token = device_agent_token(db, device.id)
    if token is not None:
        # Device reachable only via the agent tunnel: run through the relay.
        # The agent exec runs in a PTY, so pty requests are satisfied natively.
        docker_cmd = "docker " + shlex.join(args)
        try:
            out, code = await agent_exec_relay(device.id, docker_cmd, db, timeout=120, capture_code=True)
        except HTTPException as exc:
            raise
        return {
            "command": "docker " + cmd,
            "stdout": out,
            "stderr": "",
            "exit_status": code,
        }
    conn, bastion = await connect_device(device, db)
    try:
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
    finally:
        conn.close()
        if bastion is not None:
            bastion.close()
    log_audit(db, user, "docker_run", f"device={device.name} command={cmd[:200]}")
