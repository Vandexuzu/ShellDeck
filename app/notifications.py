"""Notification delivery (Telegram bot + Discord webhook) + reachability monitor.

Uses httpx (already a dependency). Telegram bot tokens are encrypted at rest
via app.config.encrypt; chat id and Discord webhook are stored in plaintext
(the webhook is not a secret that grants shell access).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt
from app.db import Base, SessionLocal
from app.models import Device, SettingsRow

# In-memory last-reachability state for change detection across poll cycles.
_last_state: dict[int, bool] = {}


def _get_settings(db: Session) -> SettingsRow:
    row = db.get(SettingsRow, 1)
    if row is None:
        row = SettingsRow(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


async def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            return r.status_code == 200
    except Exception:
        return False


async def send_discord(webhook: str, text: str) -> bool:
    if not webhook:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json={"content": text})
            return r.status_code in (200, 204)
    except Exception:
        return False


async def notify(message: str, db: Session) -> None:
    """Send a notification through whichever channels are configured."""
    s = _get_settings(db)
    if not s.notify_enabled:
        return
    token = decrypt(s.telegram_token_enc) if s.telegram_token_enc else ""
    if token and s.telegram_chat_id:
        await send_telegram(token, s.telegram_chat_id, message)
    if s.discord_webhook:
        await send_discord(s.discord_webhook, message)


async def _probe(device: Device) -> bool:
    """Return True if the device is reachable over SSH."""
    from app.routers.devices import load_credentials
    from app.config import settings
    import asyncssh

    username, password, private_key = load_credentials(device)
    opts = {
        "host": device.host,
        "port": device.port,
        "username": username,
        "known_hosts": None if settings.ssh_ignore_known_hosts else False,
        "connect_timeout": 8,
    }
    if private_key:
        opts["client_keys"] = [private_key]
    else:
        opts["password"] = password
    try:
        async with asyncssh.connect(**opts) as _conn:
            return True
    except Exception:
        return False


async def monitor_loop(interval: int = 60) -> None:
    """Periodically probe all devices and notify on reachability changes."""
    global _last_state
    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                settings_row = _get_settings(db)
                if not settings_row.notify_enabled:
                    continue
                devices = db.scalars(select(Device)).all()
                for d in devices:
                    try:
                        ok = await _probe(d)
                    except Exception:
                        ok = False
                    prev = _last_state.get(d.id)
                    if prev is not None and prev != ok:
                        state = "✅ reachable again" if ok else "🔥 UNREACHABLE"
                        msg = f"<b>ShellDeck alert</b>\nDevice <b>{d.name}</b> ({d.host}) is now {state}."
                        await notify(msg, db)
                    _last_state[d.id] = ok
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            # never let the loop die
            await asyncio.sleep(5)
