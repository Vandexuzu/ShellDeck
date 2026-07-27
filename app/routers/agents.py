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

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, Device, User
from app.security import get_user_from_token_raw, operator_only

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Live agent tunnels: token -> asyncio.Queue (server -> agent messages)
_LIVE: dict[str, asyncio.Queue] = {}
# Reverse lookup: token -> agent WebSocket (to receive frames)
_LIVE_WS: dict[str, WebSocket] = {}
# Active relay sessions: conn_id -> {"browser": WebSocket, "device_id": int, "token": str}
_SESSIONS: dict[int, dict] = {}
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
    last_seen: datetime | None
    created_at: datetime


# ------------------------------- REST --------------------------------------
@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[Agent]:
    return list(db.scalars(select(Agent).where(Agent.owner_id == user.id).order_by(Agent.name)))


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Agent:
    token = secrets.token_urlsafe(24)
    agent = Agent(owner_id=user.id, name=payload.name, token=token, device_id=payload.device_id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


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
    if sess is None or sess.get("browser") is None:
        return
    try:
        if t == "data":
            await sess["browser"].send_text(msg.get("data", ""))
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

        # Start an interactive shell on the agent.
        q = _LIVE.get(agent_token)
        if q is None:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            _SESSIONS.pop(cid, None)
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
                        await agent_relay_stdin(cid, msg)
            except WebSocketDisconnect:
                pass
            finally:
                await agent_end_session(cid)

        # The agent->browser direction is handled by _route_agent_frame (pump
        # in agent_ws) which sends frames straight to this websocket. We just
        # keep this coroutine alive until the browser disconnects.
        await browser_to_agent()
    finally:
        db.close()
