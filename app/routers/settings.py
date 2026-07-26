"""App settings — admin-only. Currently exposes notification configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt
from app.db import get_db
from app.models import SettingsRow
from app.schemas import SettingsOut, SettingsUpdate
from app.security import get_current_user
from app.notifications import send_telegram, send_discord

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _row(db: Session) -> SettingsRow:
    row = db.get(SettingsRow, 1)
    if row is None:
        row = SettingsRow(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db), _: object = Depends(get_current_user)) -> SettingsRow:
    return _row(db)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
) -> SettingsRow:
    row = _row(db)
    if payload.notify_enabled is not None:
        row.notify_enabled = payload.notify_enabled
    if payload.telegram_token is not None:
        row.telegram_token_enc = encrypt(payload.telegram_token) if payload.telegram_token else ""
    if payload.telegram_chat_id is not None:
        row.telegram_chat_id = payload.telegram_chat_id
    if payload.discord_webhook is not None:
        row.discord_webhook = payload.discord_webhook
    if payload.monitor_interval is not None:
        row.monitor_interval = max(10, min(payload.monitor_interval, 3600))
    db.commit()
    db.refresh(row)
    return row


@router.post("/test")
async def test_notification(
    db: Session = Depends(get_db), _: object = Depends(get_current_user)
) -> dict:
    """Send a test message via the currently configured channels."""
    row = _row(db)
    token = decrypt(row.telegram_token_enc) if row.telegram_token_enc else ""
    results = {}
    msg = "✅ ShellDeck test notification — you're connected!"
    if token and row.telegram_chat_id:
        results["telegram"] = await send_telegram(token, row.telegram_chat_id, msg)
    else:
        results["telegram"] = "skipped (no token/chat id)"
    if row.discord_webhook:
        results["discord"] = await send_discord(row.discord_webhook, msg)
    else:
        results["discord"] = "skipped (no webhook)"
    return results
