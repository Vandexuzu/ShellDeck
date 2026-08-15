"""WebSocket-based agent for NAT-traversed devices (full relay).

An *agent* is a tiny helper process that runs ON the remote device (or anywhere
behind a NAT/firewall) and opens an *outbound* WebSocket to ShellDeck. Because
the connection is initiated from the device, no inbound port / port-forward is
required.

Relay protocol (JSON text frames over the agent WebSocket):
  server -> agent : {"t":"exec","cid":<int>,"cols":<int>,"rows":<int>,"data":"<init command or empty>"}
  server -> agent : {"t":"data","cid":<int>,"data":"<stdin bytes>"}
  server -> agent : {"t":"resize","cid":<int>,"cols":<int>,"rows":<int>}
  agent  -> server: {"t":"data","cid":<int>,"data":"<stdout/stderr bytes>"}
  agent  -> server: {"t":"exit","cid":<int>,"code":<int>}
  agent  -> server: {"t":"hb"}                         (heartbeat)
  server -> agent : {"t":"ack"}                        (heartbeat ack)

The agent runs each `exec` in a local PTY (pty.openpty + subprocess) so the user
gets a real interactive shell — arrows, tab-completion and colour all work.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import time

from app.db import get_db, SessionLocal
from app.models import Agent, Device, SessionLog, User, SettingsRow
from app.security import get_user_from_token_raw, operator_only

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Live agent tunnels: token -> asyncio.Queue (server -> agent messages)
_LIVE: dict[str, asyncio.Queue] = {}
# Reverse lookup: token -> agent WebSocket (to receive frames)
_LIVE_WS: dict[str, WebSocket] = {}
# Active relay sessions: conn_id -> {"browser": WebSocket, "device_id": int, "token": str}
_SESSIONS: dict[int, dict] = {}
# Pending file-system relay requests: cid -> asyncio.Future
_FS_WAIT: dict[int, asyncio.Future] = {}
# Active recording buffers: cid -> list of [delay, type, data]
_REC: dict[int, list] = {}
_NEXT_CID = 1


# ------------------------------- Schemas -----------------------------------
class AgentCreate(BaseModel):
    name: str
    device_id: int | None = None


class AgentOut(BaseModel):
    id: int
    name: str
    token: str
    device_id: int | None
    connected: bool
    ips: list[str] | None = None
    last_seen: datetime | None
    created_at: datetime


# ------------------------------- REST --------------------------------------
@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[dict]:
    agents = list(db.scalars(select(Agent).where(Agent.owner_id == user.id).order_by(Agent.name)))
    out = []
    for a in agents:
        out.append({
            "id": a.id, "name": a.name, "token": a.token, "device_id": a.device_id,
            "connected": a.connected,
            "ips": (json.loads(a.ips) if a.ips else None),
            "last_seen": a.last_seen, "created_at": a.created_at,
        })
    return out


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    token = secrets.token_urlsafe(24)
    agent = Agent(owner_id=user.id, name=payload.name, token=token, device_id=payload.device_id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return {
        "id": agent.id, "name": agent.name, "token": agent.token, "device_id": agent.device_id,
        "connected": agent.connected, "ips": None,
        "last_seen": agent.last_seen, "created_at": agent.created_at,
    }


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    _LIVE.pop(agent.token, None)
    _LIVE_WS.pop(agent.token, None)
    db.delete(agent)
    db.commit()
    return {"deleted": agent_id}


class AgentUpdate(BaseModel):
    name: str | None = None
    device_id: int | None = None  # link/unlink the agent to a device


@router.put("/{agent_id}")
def update_agent(agent_id: int, payload: AgentUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Update an agent — currently supports renaming and linking it to a device
    (so the device is reached through the agent tunnel instead of direct SSH)."""
    agent = db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if payload.name is not None:
        agent.name = payload.name.strip() or agent.name
    if payload.device_id is not None:
        # Unlink any other agent currently linked to the same device (one per device).
        if payload.device_id:
            for other in db.scalars(select(Agent).where(Agent.device_id == payload.device_id)).all():
                if other.id != agent.id:
                    other.device_id = None
        agent.device_id = payload.device_id or None
    db.commit()
    return {
        "id": agent.id, "name": agent.name, "token": agent.token,
        "device_id": agent.device_id, "connected": agent.connected,
        "ips": json.loads(agent.ips) if agent.ips else None,
        "last_seen": agent.last_seen, "created_at": agent.created_at,
    }


# ------------------------------- Bootstrap helper ---------------------------
_CLIENT_RAW = "https://raw.githubusercontent.com/Vandexuzu/ShellDeck/main/agent/client.py"


@router.get("/{agent_id}/bootstrap")
def agent_bootstrap(agent_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Return copy-paste helpers so a user can configure the agent client on the
    target device in seconds (no manual editing of client.py / env vars)."""
    agent = db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Derive the public base URL from the incoming request (scheme + host).
    base = f"{request.url.scheme}://{request.url.netloc}"
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    token = agent.token
    # Pull agent tuning from global settings so the bootstrap reflects admin config.
    settings = db.get(SettingsRow, 1)
    hb = settings.agent_heartbeat if settings else 15
    rc = settings.agent_reconnect if settings else 5
    env_linux = f"SHELLDECK_URL='{base}' SHELLDECK_AGENT_TOKEN='{token}' SHELLDECK_HEARTBEAT='{hb}' SHELLDECK_RECONNECT='{rc}'"
    env_ps = f"$env:SHELLDECK_URL='{base}'; $env:SHELLDECK_AGENT_TOKEN='{token}'; $env:SHELLDECK_HEARTBEAT='{hb}'; $env:SHELLDECK_RECONNECT='{rc}'"
    # One-liner for Linux/macOS/Termux (downloads client.py then runs it).
    oneliner = (
        f"curl -fsSL {_CLIENT_RAW} -o shelldeck_agent.py && "
        f"pip install websocket-client >/dev/null 2>&1; "
        f"{env_linux} python3 shelldeck_agent.py"
    )
    # Windows PowerShell one-liner.
    ps_oneliner = (
        f"Invoke-WebRequest -Uri '{_CLIENT_RAW}' -OutFile shelldeck_agent.py; "
        f"pip install websocket-client; "
        f"{env_ps}; "
        f"python shelldeck_agent.py"
    )
    # A standalone shell script the user can copy to the device.
    script_sh = (
        "#!/usr/bin/env bash\n"
        "# ShellDeck agent bootstrap — saves as run_agent.sh and: bash run_agent.sh\n"
        f"export SHELLDECK_URL='{base}'\n"
        f"export SHELLDECK_AGENT_TOKEN='{token}'\n"
        f"export SHELLDECK_HEARTBEAT='{hb}'\n"
        f"export SHELLDECK_RECONNECT='{rc}'\n"
        "pip install --quiet websocket-client\n"
        f"curl -fsSL {_CLIENT_RAW} -o shelldeck_agent.py\n"
        "exec python3 shelldeck_agent.py\n"
    )
    script_ps1 = (
        "# ShellDeck agent bootstrap — saves as run_agent.ps1 and: powershell -ExecutionPolicy Bypass -File run_agent.ps1\n"
        f"$env:SHELLDECK_URL = '{base}'\n"
        f"$env:SHELLDECK_AGENT_TOKEN = '{token}'\n"
        f"$env:SHELLDECK_HEARTBEAT = '{hb}'\n"
        f"$env:SHELLDECK_RECONNECT = '{rc}'\n"
        "pip install websocket-client\n"
        f"Invoke-WebRequest -Uri '{_CLIENT_RAW}' -OutFile shelldeck_agent.py\n"
        "python shelldeck_agent.py\n"
    )
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "url": base,
        "ws_url": f"{ws_base}/api/agents/ws?token={token}",
        "token": token,
        "client_url": _CLIENT_RAW,
        "oneliner": oneliner,
        "powershell_oneliner": ps_oneliner,
        "script_sh": script_sh,
        "script_ps1": script_ps1,
    }


# ------------------------------- WebSocket (device side) -------------------
@router.websocket("/ws")
async def agent_ws(websocket: WebSocket, token: str | None = Query(default=None)):
    """Device-side agent tunnel. The device connects here with its token."""
    db = next(get_db())
    try:
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        agent = db.scalar(select(Agent).where(Agent.token == token))
        if agent is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        _LIVE[agent.token] = queue
        _LIVE_WS[agent.token] = websocket
        agent.connected = True
        agent.last_seen = datetime.now(timezone.utc)
        db.commit()

        # Dedicated reader: continuously drain frames FROM the agent and route
        # them to the correct browser session. Runs concurrently with the
        # sender loop below so streaming output is never blocked on a send.
        async def agent_reader() -> None:
            try:
                while True:
                    raw = await websocket.receive_text()
                    await _route_agent_frame(raw)
            except WebSocketDisconnect:
                pass

        reader_task = asyncio.create_task(agent_reader())
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                try:
                    await websocket.send_text(frame)
                except (WebSocketDisconnect, RuntimeError):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            reader_task.cancel()
            _LIVE.pop(agent.token, None)
            _LIVE_WS.pop(agent.token, None)
            agent.connected = False
            db.commit()
    finally:
        db.close()


async def _route_agent_frame(raw: str) -> None:
    """Route a frame coming FROM the agent back to the right browser session."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    t = msg.get("t")
    cid = msg.get("cid")
    sess = _SESSIONS.get(cid) if cid is not None else None
    if t == "hb":
        # Heartbeat — update last_seen lazily (cheap: skip DB write every time).
        return
    if t == "ips":
        # Device IP discovery: the agent reports its local interface addresses.
        # The agent includes its token so we can persist the list on the right
        # Agent row. Open a short-lived session for the write.
        try:
            reported = msg.get("ips") or []
            if isinstance(reported, str):
                reported = [reported]
            token = msg.get("token")
            if token:
                with SessionLocal() as dbs:
                    ag = dbs.scalar(select(Agent).where(Agent.token == token))
                    if ag is not None:
                        ag.ips = json.dumps(reported)
                        dbs.commit()
        except Exception:
            pass
        return
    if t == "fs":
        fut = _FS_WAIT.pop(cid, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)
        return
    if sess is None or sess.get("browser") is None:
        return
    try:
        if t == "data":
            data = msg.get("data", "")
            buf = _REC.get(cid)
            if buf is not None:
                buf.append([round(time.monotonic() - buf[0], 3) if isinstance(buf[0], float) else 0.0, "o", data])
            await sess["browser"].send_text(data)
        elif t == "exit":
            # Shell exited — forward nothing; the browser WS will be closed
            # by the agent-terminal endpoint when its session ends.
            pass
    except WebSocketDisconnect:
        pass


# ------------------------------- Relay helpers -----------------------------
def device_agent_token(db: Session, device_id: int) -> str | None:
    agents = db.scalars(select(Agent).where(Agent.device_id == device_id)).all()
    for agent in agents:
        if agent.token in _LIVE:
            return agent.token
    return None


async def agent_relay_stdin(cid: int, data: str) -> None:
    sess = _SESSIONS.get(cid)
    if not sess:
        return
    q = _LIVE.get(sess["token"])
    if q:
        await q.put(json.dumps({"t": "data", "cid": cid, "data": data}))


async def agent_relay_resize(cid: int, cols: int, rows: int) -> None:
    sess = _SESSIONS.get(cid)
    if not sess:
        return
    q = _LIVE.get(sess["token"])
    if q:
        await q.put(json.dumps({"t": "resize", "cid": cid, "cols": cols, "rows": rows}))


async def agent_end_session(cid: int) -> None:
    sess = _SESSIONS.pop(cid, None)
    if sess:
        q = _LIVE.get(sess["token"])
        if q:
            await q.put(json.dumps({"t": "kill", "cid": cid}))


# ------------------------------- File-system relay (REST) -------------------
class FsRequest(BaseModel):
    op: str                                   # list | read | write | mkdir | delete | stat
    path: str = "/"
    data: str | None = None


async def agent_fs_relay(device_id: int, req: FsRequest, db: Session) -> object:
    """Relay a file-system operation to the device's connected agent and wait
    for the result. Raises HTTPException on agent error or timeout."""
    token = device_agent_token(db, device_id)
    if token is None:
        raise HTTPException(status_code=503, detail="Device agent not connected")
    q = _LIVE.get(token)
    if q is None:
        raise HTTPException(status_code=503, detail="Device agent not connected")
    global _NEXT_CID
    cid = _NEXT_CID
    _NEXT_CID += 1
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _FS_WAIT[cid] = fut
    frame = {"t": "fs", "cid": cid, "op": req.op, "path": req.path}
    if req.data is not None:
        frame["data"] = req.data
    await q.put(json.dumps(frame))
    try:
        resp = await asyncio.wait_for(fut, timeout=30)
    except asyncio.TimeoutError:
        _FS_WAIT.pop(cid, None)
        raise HTTPException(status_code=504, detail="Agent file operation timed out")
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail="Agent FS error: " + resp.get("err", "unknown"))
    return resp.get("result")


async def agent_exec_relay(device_id: int, command: str, db: Session, timeout: int = 30) -> str:
    """Run a one-shot shell command on the device through its connected agent
    and return the combined output. Used by server-side monitoring for devices
    that are only reachable via the agent tunnel (no inbound SSH)."""
    token = device_agent_token(db, device_id)
    if token is None:
        raise HTTPException(status_code=503, detail="Device agent not connected")
    q = _LIVE.get(token)
    if q is None:
        raise HTTPException(status_code=503, detail="Device agent not connected")
    global _NEXT_CID
    cid = _NEXT_CID
    _NEXT_CID += 1
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _FS_WAIT[cid] = fut
    await q.put(json.dumps({"t": "fs", "cid": cid, "op": "exec", "data": command}))
    try:
        resp = await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _FS_WAIT.pop(cid, None)
        raise HTTPException(status_code=504, detail="Agent exec timed out")
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail="Agent exec error: " + resp.get("err", "unknown"))
    return (resp.get("result") or {}).get("output", "")


@router.post("/fs/{device_id}")
async def agent_fs(device_id: int, req: FsRequest, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not (user.role == "admin" or device.owner_id == user.id):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        return await agent_fs_relay(device_id, req, db)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"FS relay error: {type(exc).__name__}: {exc}")


# ------------------------------- Terminal over agent (browser side) ---------
@router.websocket("/terminal/{device_id}")
async def agent_terminal(websocket: WebSocket, device_id: int, token: str | None = Query(default=None)):
    """Browser opens this to get a shell on a device that is reached via an agent."""
    db = next(get_db())
    try:
        user = get_user_from_token_raw(token, db)
        if user is None:
            print(f"[agent_terminal] auth failed, token={token!r}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        device = db.get(Device, device_id)
        if device is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        # Access control: admin sees all; operator only own devices; viewer none.
        if not (user.role == "admin" or device.owner_id == user.id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        agent_token = device_agent_token(db, device_id)
        if agent_token is None:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            return
        await websocket.accept()

        global _NEXT_CID
        cid = _NEXT_CID
        _NEXT_CID += 1
        _SESSIONS[cid] = {"browser": websocket, "device_id": device_id, "token": agent_token}
        # Recording buffer: [start_mono, ...events]. First element is the t0 marker.
        _REC[cid] = [time.monotonic()]

        # Audit log entry.
        log = SessionLog(device_id=device.id, user_id=user.id)
        db.add(log)
        db.commit()

        # Start an interactive shell on the agent.
        q = _LIVE.get(agent_token)
        if q is None:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            _SESSIONS.pop(cid, None)
            _REC.pop(cid, None)
            return
        await q.put(json.dumps({"t": "exec", "cid": cid, "cols": 80, "rows": 24, "data": ""}))

        async def browser_to_agent() -> None:
            try:
                while True:
                    msg = await websocket.receive_text()
                    if msg.startswith("\x00resize\x00"):
                        _, cols_s, rows_s = msg.split("\x00")[1:4]
                        await agent_relay_resize(cid, int(cols_s), int(rows_s))
                    else:
                        buf = _REC.get(cid)
                        if buf is not None:
                            buf.append([round(time.monotonic() - buf[0], 3), "i", msg])
                        await agent_relay_stdin(cid, msg)
            except WebSocketDisconnect:
                pass
            finally:
                await agent_end_session(cid)

        # The agent->browser direction is handled by _route_agent_frame (pump
        # in agent_ws) which sends frames straight to this websocket. We just
        # keep this coroutine alive until the browser disconnects.
        try:
            await browser_to_agent()
        finally:
            buf = _REC.pop(cid, None)
            log.ended_at = datetime.now(timezone.utc)
            if buf and len(buf) > 1:
                try:
                    events = buf[1:]
                    rec = {"version": 2, "width": 80, "height": 24, "events": events}
                    log.recording = json.dumps(rec)
                    db.commit()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        db.close()
