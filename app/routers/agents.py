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
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from app.db import get_db, SessionLocal
from app.models import Agent, Device, SessionLog, User, SettingsRow
from app.audit import log_audit
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
    os: str | None = None
    last_seen: datetime | None
    created_at: datetime | None
    pending: bool = False


# ------------------------------- REST --------------------------------------
@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[dict]:
    agents = list(db.scalars(
        select(Agent)
        .where((Agent.owner_id == user.id) | (Agent.pending == True))  # type: ignore[comparison-overlap]
        .order_by(Agent.name)
    ))
    out = []
    for a in agents:
        out.append({
            "id": a.id, "name": a.name, "token": a.token, "device_id": a.device_id,
            "connected": a.connected,
            "ips": (json.loads(a.ips) if a.ips else None),
            "os": a.os,
            "last_seen": a.last_seen, "created_at": a.created_at,
            "pending": a.pending,
        })
    return out


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    token = secrets.token_urlsafe(24)
    agent = Agent(
        owner_id=user.id,
        name=payload.name,
        token=token,
        device_id=payload.device_id,
        install_slug=secrets.token_urlsafe(16),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return {
        "id": agent.id, "name": agent.name, "token": agent.token, "device_id": agent.device_id,
        "connected": agent.connected, "ips": None,
        "os": agent.os,
        "last_seen": agent.last_seen, "created_at": agent.created_at, "pending": agent.pending,
    }


# ------------------------------- Self-enrollment ----------------------------
# A device runs the generic install.sh (which carries only the revocable enroll
# secret, never a per-device token). On first run the agent POSTs here to mint a
# per-device token, then connects. The agent lands as `pending` (owner_id NULL)
# until an operator claims it from the UI.
_ENROLL_FAILS: dict[str, list[float]] = defaultdict(list)
_ENROLL_WINDOW = 600      # 10 minutes
_ENROLL_MAX_FAILS = 20    # lock an IP after this many bad-secret attempts
_ENROLL_CAP_PER_OWNER = 50  # max pending (unclaimed) agents per owner


class AgentEnroll(BaseModel):
    secret: str                       # the revocable enroll secret
    name: str | None = None          # optional device label from the client
    os: str | None = None            # optional OS hint


@router.post("/enroll", status_code=status.HTTP_201_CREATED)
def enroll_agent(payload: AgentEnroll, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _ENROLL_FAILS[ip] = [t for t in _ENROLL_FAILS[ip] if now - t < _ENROLL_WINDOW]
    if len(_ENROLL_FAILS[ip]) >= _ENROLL_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Too many enrollment attempts. Try again later.")

    row = db.get(SettingsRow, 1)
    if not row or not row.enroll_secret or not secrets.compare_digest(payload.secret, row.enroll_secret):
        _ENROLL_FAILS[ip].append(now)
        # Audit failed enrollment attempts (wrong/leaked secret) for threat visibility.
        try:
            log_audit(db, None, "agent_enroll_failed", f"invalid_secret ip={ip} name={payload.name or '?'}")
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid enrollment secret")

    owner_id = row.enroll_owner_id
    if owner_id is None:
        # No owner configured (e.g. secret seeded before any user existed). Fall
        # back to the first admin so the agent is claimable rather than orphaned.
        from app.models import User
        owner = db.scalar(select(User).where(User.role == "admin").order_by(User.id)) or db.get(User, 1)
        owner_id = owner.id if owner else None

    # Cap unclaimed (pending) agents to avoid secret-leak spam filling the table.
    pending_count = db.scalar(
        select(func.count(Agent.id)).where(Agent.pending == True, Agent.owner_id == owner_id)  # type: ignore[comparison-overlap]
    ) or 0
    if pending_count >= _ENROLL_CAP_PER_OWNER:
        raise HTTPException(status_code=429, detail="Too many pending agents. Claim or delete existing ones first.")

    token = secrets.token_urlsafe(24)
    name = (payload.name or "enrolled-device").strip()[:128] or "enrolled-device"
    agent = Agent(
        owner_id=owner_id,            # NULL if no owner at all -> still claimable by any operator
        name=name,
        token=token,
        os=payload.os,
        pending=True,
        install_slug=secrets.token_urlsafe(16),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    # Audit: self-enrollment is an unauthenticated, security-relevant event — record
    # it with the enroll name + source IP so admins can trace who joined.
    log_audit(
        db, None, "agent_enroll",
        f"agent={agent.id} name={name} os={payload.os or '?'} owner={owner_id} ip={ip}",
        ip=ip,
    )
    return {
        "id": agent.id,
        "token": agent.token,         # the per-device token — sent once, client persists it locally
        "name": agent.name,
        "pending": True,
    }


class AgentClaim(BaseModel):
    name: str | None = None


@router.post("/{agent_id}/claim", response_model=AgentOut)
def claim_agent(agent_id: int, payload: AgentClaim, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Claim a pending (self-enrolled) agent: assign it to the claiming operator
    and clear the pending flag so it becomes a normal, owned agent."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.pending:
        raise HTTPException(status_code=409, detail="Agent is already claimed")
    # If the agent had no owner (no admin at seed time), set it now; otherwise it
    # stays owned by its enroll_owner but is now actively managed by this operator.
    agent.owner_id = user.id
    agent.pending = False
    if payload.name and payload.name.strip():
        agent.name = payload.name.strip()[:128]
    db.commit()
    log_audit(db, user, "agent_claim", f"agent={agent.id} name={agent.name}")
    db.refresh(agent)
    return {
        "id": agent.id, "name": agent.name, "token": agent.token, "device_id": agent.device_id,
        "connected": agent.connected,
        "ips": (json.loads(agent.ips) if agent.ips else None),
        "os": agent.os,
        "last_seen": agent.last_seen, "created_at": agent.created_at, "pending": agent.pending,
    }


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    # If the agent is currently connected, nudge its server-side reader loop to
    # terminate (push the sentinel). The loop's finally closes the socket with the
    # "revoked" reason so the device-side client drops its local token and
    # re-enrolls itself — no manual file deletion needed (this is the UI "Reset").
    q = _LIVE.pop(agent.token, None)
    _LIVE_WS.pop(agent.token, None)
    if q is not None:
        try:
            q.put_nowait(None)
        except Exception:
            pass
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
        "os": agent.os,
        "last_seen": agent.last_seen, "created_at": agent.created_at,
    }


# ------------------------------- Bootstrap helper ---------------------------
_CLIENT_RAW = "https://raw.githubusercontent.com/Vandexuzu/ShellDeck/main/agent/client.py"


@router.get("/{agent_id}/bootstrap")
def agent_bootstrap(agent_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Return a single one-liner that installs the agent on the target device.

    The token is injected by the server, so the user never has to copy-paste it.
    """
    agent = db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    base = f"{request.url.scheme}://{request.url.netloc}"
    token = agent.token
    settings = db.get(SettingsRow, 1)
    hb = settings.agent_heartbeat if settings else 15
    rc = settings.agent_reconnect if settings else 5
    # One-liner: download installer, inject token via env, pipe to bash. No manual copy-paste.
    install_sh = (
        f"curl -fsSL {base}/install.sh | "
        f"SHELLDECK_URL='{base}' SHELLDECK_AGENT_TOKEN='{token}' "
        f"SHELLDECK_HEARTBEAT='{hb}' SHELLDECK_RECONNECT='{rc}' bash"
    )
    install_ps1 = (
        f"Invoke-WebRequest -Uri '{base}/install.ps1' -OutFile install.ps1; "
        f"$env:SHELLDECK_URL='{base}'; $env:SHELLDECK_AGENT_TOKEN='{token}'; "
        f"$env:SHELLDECK_HEARTBEAT='{hb}'; $env:SHELLDECK_RECONNECT='{rc}'; .\\install.ps1"
    )
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "url": base,
        "ws_url": f"{base.replace('http://', 'ws://').replace('https://', 'wss://')}/api/agents/ws?token={token}",
        "token": token,
        "install_sh": install_sh,
        "install_ps1": install_ps1,
    }


# ------------------------------- WebSocket (device side) -------------------
@router.websocket("/ws")
async def agent_ws(websocket: WebSocket, token: str | None = Query(default=None)):
    """Device-side agent tunnel. The device connects here with its token."""
    db = next(get_db())
    try:
        if not token:
            await websocket.accept()
            # Tell the client (via app frame) it was revoked so it self-re-enrolls.
            # Hold the socket open briefly so the frame is delivered before the
            # close (otherwise websocket-client may process the close first and the
            # client never learns it was revoked -> loops with the stale token).
            try:
                await websocket.send_text(json.dumps({"t": "revoked"}))
                await asyncio.sleep(1.5)
            except Exception:
                pass
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="revoked")
            return
        agent = db.scalar(select(Agent).where(Agent.token == token))
        if agent is None:
            await websocket.accept()
            try:
                await websocket.send_text(json.dumps({"t": "revoked"}))
                await asyncio.sleep(1.5)
            except Exception:
                pass
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="revoked")
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
                    # Sentinel pushed by delete_agent() — the operator reset/removed
                    # this agent. Notify the client via an app frame (reliable across
                    # websocket-client versions) then hold the socket open briefly so
                    # the frame is delivered before closing, so it drops its local
                    # token and re-enrolls itself.
                    try:
                        await websocket.send_text(json.dumps({"t": "revoked"}))
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                    try:
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="revoked")
                    except Exception:
                        pass
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
                        if msg.get("os"):
                            ag.os = str(msg.get("os"))[:32]
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


async def agent_exec_relay(device_id: int, command: str, db: Session, timeout: int = 30, capture_code: bool = False):
    """Run a one-shot shell command on the device through its connected agent
    and return the combined output. Used by server-side monitoring for devices
    that are only reachable via the agent tunnel (no inbound SSH).

    When ``capture_code`` is True the exit status is appended as a trailing
    ``SD_EXIT=<n>`` line and a ``(stdout, code)`` tuple is returned instead of
    the raw string — used by the Docker manager so it can detect command failures.
    """
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
    exec_cmd = command + "; echo \"SD_EXIT=$?\"" if capture_code else command
    await q.put(json.dumps({"t": "fs", "cid": cid, "op": "exec", "data": exec_cmd}))
    try:
        resp = await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _FS_WAIT.pop(cid, None)
        raise HTTPException(status_code=504, detail="Agent exec timed out")
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail="Agent exec error: " + resp.get("err", "unknown"))
    out = (resp.get("result") or {}).get("output", "")
    if not capture_code:
        return out
    # Split off the SD_EXIT=<n> marker. The agent runs `<cmd>; echo "SD_EXIT=$?"`,
    # but stderr ordering can place the marker anywhere in the combined stream, so
    # scan every line (not just the last) and remove the matching one.
    code = 0
    kept = []
    for ln in out.split("\n"):
        if ln.startswith("SD_EXIT="):
            try:
                code = int(ln.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                code = 0
            continue
        kept.append(ln)
    out = "\n".join(kept).rstrip("\n")
    return (out, code)


@router.post("/fs/{device_id}")
async def agent_fs(device_id: int, req: FsRequest, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Any:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not (user.role == "admin" or device.owner_id == user.id):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        result = await agent_fs_relay(device_id, req, db)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"FS relay error: {type(exc).__name__}: {exc}")
    # Audit destructive / mutating file operations (skip read-only list/read/stat).
    # Action names mirror app/routers/files.py so the audit trail is consistent.
    if req.op == "delete":
        log_audit(db, user, "file_delete", f"via-agent device={device.name} ({device.host}) path={req.path}")
    elif req.op in ("write", "write_b64"):
        log_audit(db, user, "file_write", f"via-agent device={device.name} ({device.host}) path={req.path}")
    elif req.op == "mkdir":
        log_audit(db, user, "file_mkdir", f"via-agent device={device.name} ({device.host}) path={req.path}")
    return result


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
        # Plain-text transcript accumulator (mirrors terminal.py so the Commands and
        # Raw transcript tabs work for agent sessions too). Built from typed input
        # (commands) and from "o" events (device output).
        transcript_buf: list[str] = []

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
            # Per-session accumulator for the current (unterminated) typed line.
            cmd_buf = ""
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
                        # Accumulate typed characters so a command is captured even
                        # when the browser streams one keystroke per WebSocket frame.
                        if "\x1b" not in msg and msg not in ("\r", "\n"):
                            cmd_buf += msg
                        # Record typed command lines (on Enter) for the audit trail.
                        if "\r" in msg or "\n" in msg:
                            cmd_buf += msg.replace("\r", "\n")
                            while "\n" in cmd_buf:
                                line, cmd_buf = cmd_buf.split("\n", 1)
                                line = line.strip()
                                if line:
                                    log.commands = (log.commands + "\n" + line) if log.commands else line
                                    db.commit()
                                    # Echo the typed command into the playback transcript.
                                    transcript_buf.append("$ " + line + "\n")
            except WebSocketDisconnect:
                pass
            finally:
                await agent_end_session(cid)

        # The agent->browser direction is handled by _route_agent_frame (pump
        # in agent_ws) which sends frames straight to this websocket. That pump
        # also appends "o" events to _REC; we mirror them into the transcript.
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
                    # Build the plain-text transcript from the recorded events so the
                    # Raw transcript tab is populated for agent sessions (not just SSH).
                    for ev in events:
                        if len(ev) >= 3 and ev[1] == "o":
                            transcript_buf.append(ev[2])
                    full = "".join(transcript_buf)
                    if full:
                        log.transcript = (log.transcript + full) if log.transcript else full
                    db.commit()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        db.close()
