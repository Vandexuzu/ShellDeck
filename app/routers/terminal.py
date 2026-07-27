"""WebSocket terminal: bridge a browser xterm.js session to a remote SSH shell.

Security notes:
- Token is passed as the `?token=` query param (WebSocket handshake can't set
  Authorization headers in the browser). We validate it against the same secret.
- We only allow opening a shell to a device the authenticated user owns.
- Every opened session is recorded in session_logs (audit trail).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncssh
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Device, SessionLog, User
from app.routers.devices import connect_device, _can_access
from app.security import get_user_from_token_raw

router = APIRouter(tags=["terminal"])

DEFAULT_COLS, DEFAULT_ROWS = 80, 24


async def _authenticate(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    return get_user_from_token_raw(token, db)


@router.websocket("/api/terminal/{device_id}")
async def terminal(websocket: WebSocket, device_id: int, token: str | None = Query(default=None)):
    db = next(get_db())
    try:
        user = await _authenticate(token, db)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        # Viewers are read-only and cannot open an interactive shell.
        if user.role == "viewer":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        device = db.get(Device, device_id)
        if device is None or not _can_access(db, device, user):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        try:
            conn, bastion = await connect_device(device, db)
        except Exception as exc:  # noqa: BLE001
            await websocket.send_text(f"\r\n\x1b[31m[connection failed] {exc}\x1b[0m\r\n")
            await websocket.close()
            return

        # Audit log entry.
        log = SessionLog(device_id=device.id, user_id=user.id)
        db.add(log)
        db.commit()

        # Open an interactive shell. Passing a session_factory of SSHClientProcess
        # with request_pty=True and no command spawns the user's login shell.
        cols, rows = DEFAULT_COLS, DEFAULT_ROWS
        try:
            chan, process = await conn.create_session(
                asyncssh.SSHClientProcess,
                command=(),
                request_pty=True,
                term_type="xterm-256color",
                term_size=(cols, rows, cols * 8, rows * 16),
            )
        except Exception as exc:  # noqa: BLE001
            await websocket.send_text(f"\r\n\x1b[31m[shell failed] {exc}\x1b[0m\r\n")
            conn.close()
            await websocket.close()
            return

        shell = process  # SSHClientProcess: stdin/stdout are the live shell

        # Buffer keystrokes to extract typed command lines for the audit log.
        cmd_buf = ""

        # Read from SSH -> browser.
        transcript_buf = []

        async def ssh_to_ws() -> None:
            try:
                while not shell.stdout.at_eof():
                    data = await shell.stdout.read(65536)
                    if not data:
                        break
                    await websocket.send_text(data)
                    transcript_buf.append(data)
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass

        # Read from browser -> SSH.
        async def ws_to_ssh() -> None:
            nonlocal cmd_buf
            try:
                while True:
                    msg = await websocket.receive_text()
                    # xterm.js resize frames: "\x00resize\x00<cols>\x00<rows>"
                    if msg.startswith("\x00resize\x00"):
                        _, cols_s, rows_s = msg.split("\x00")[1:4]
                        try:
                            await shell.change_terminal_size(int(cols_s), int(rows_s))
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        shell.stdin.write(msg)
                        transcript_buf.append(msg)
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
                                    entry = (log.commands + "\n" + line) if log.commands else line
                                    log.commands = entry
                                    db.commit()
                                    # Echo the typed command into the playback transcript.
                                    transcript_buf.append("$ " + line + "\n")
            except WebSocketDisconnect:
                pass
            except Exception:  # noqa: BLE001
                pass

        pump = asyncio.create_task(ssh_to_ws())
        try:
            await ws_to_ssh()
        finally:
            pump.cancel()
            try:
                shell.close()
                conn.close()
                if bastion is not None:
                    try:
                        bastion.close()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            log.ended_at = datetime.now(timezone.utc)
            # Persist the full TTY transcript (audit / playback).
            full = "".join(transcript_buf)
            if full:
                log.transcript = (log.transcript + full) if log.transcript else full
            db.commit()
    finally:
        db.close()
