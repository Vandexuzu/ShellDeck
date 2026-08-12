"""App settings — admin-only. Currently exposes notification configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import httpx
from app.config import decrypt, encrypt
from app.db import get_db
from app.models import SettingsRow
from app.schemas import SettingsOut, SettingsUpdate
from app.security import get_current_user, admin_only
from app.notifications import send_telegram, send_discord, send_ntfy, send_gotify, send_slack, send_email, send_webhook
from app.audit import log_audit

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
    _: User = Depends(admin_only),
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
    if payload.oidc_enabled is not None:
        row.oidc_enabled = payload.oidc_enabled
    if payload.timezone is not None:
        # Only accept valid IANA timezone names so the frontend Intl formatter
        # never blows up on a garbage value.
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(payload.timezone)
            row.timezone = payload.timezone
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid timezone")
    db.commit()
    db.refresh(row)
    log_audit(db, _, "settings_update", f"monitor_interval={row.monitor_interval} public_dashboard={row.public_dashboard} oidc_enabled={row.oidc_enabled} timezone={row.timezone}")
    return row


@router.post("/test")
async def test_notification(
    db: Session = Depends(get_db), _: User = Depends(admin_only)
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


@router.get("/telegram/chatid")
async def telegram_chat_id(db: Session = Depends(get_db), _: User = Depends(admin_only)) -> dict:
    """Fetch the most recent chat id from Telegram updates (requires the user to have
    messaged the bot first). Used to auto-fill the Telegram chat id field."""
    row = _row(db)
    token = decrypt(row.telegram_token_enc) if row.telegram_token_enc else ""
    if not token:
        return {"ok": False, "error": "Set the Telegram bot token first, then message the bot, then click again."}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 10, "timeout": 0})
            data = r.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description", "Telegram error")}
        # Walk updates to find the most recent message chat id.
        chat_id = None
        for u in reversed(data.get("result", [])):
            msg = u.get("message") or u.get("edited_message") or u.get("channel_post")
            if msg and msg.get("chat", {}).get("id") is not None:
                chat_id = msg["chat"]["id"]
                break
        if chat_id is None:
            return {"ok": False, "error": "No messages yet — open a chat with the bot and send any message, then retry."}
        # Persist so test/send work immediately.
        row.telegram_chat_id = str(chat_id)
        db.commit()
        return {"ok": True, "chat_id": chat_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
