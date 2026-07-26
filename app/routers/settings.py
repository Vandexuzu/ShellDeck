"""App settings — admin-only. Currently exposes notification configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import decrypt, encrypt
from app.db import get_db
from app.models import SettingsRow
from app.schemas import SettingsOut, SettingsUpdate
from app.security import get_current_user
from app.notifications import send_telegram, send_discord, send_ntfy, send_gotify, send_slack, send_email, send_webhook

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
    if payload.ntfy_url is not None:
        row.ntfy_url = payload.ntfy_url
    if payload.gotify_url is not None:
        row.gotify_url = payload.gotify_url
    if payload.slack_webhook is not None:
        row.slack_webhook = payload.slack_webhook
    if payload.email_to is not None:
        row.email_to = payload.email_to
    if payload.email_host is not None:
        row.email_host = payload.email_host
    if payload.email_port is not None:
        row.email_port = max(1, min(payload.email_port, 65535))
    if payload.email_user is not None:
        row.email_user = payload.email_user
    if payload.email_password is not None:
        row.email_pass_enc = encrypt(payload.email_password) if payload.email_password else ""
    if payload.webhook_url is not None:
        row.webhook_url = payload.webhook_url
    if payload.monitor_interval is not None:
        row.monitor_interval = max(10, min(payload.monitor_interval, 3600))
    if payload.public_dashboard is not None:
        row.public_dashboard = payload.public_dashboard
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
    if row.ntfy_url:
        results["ntfy"] = await send_ntfy(row.ntfy_url, msg)
    if row.gotify_url:
        results["gotify"] = await send_gotify(row.gotify_url, msg)
    if row.slack_webhook:
        results["slack"] = await send_slack(row.slack_webhook, msg)
    if row.webhook_url:
        results["webhook"] = await send_webhook(row.webhook_url, msg)
    if row.email_to:
        pw = decrypt(row.email_pass_enc) if row.email_pass_enc else ""
        results["email"] = await send_email(row.email_host, row.email_port or 587, row.email_user, pw, row.email_to, msg)
    return results
