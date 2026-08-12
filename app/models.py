"""SQLAlchemy ORM models."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Password stored as pbkdf2 hash: "pbkdf2_sha256$iterations$salt$hash"
    password_hash: Mapped[str] = mapped_column(String(255))
    # Role-based access control. One of: "admin" | "operator" | "viewer".
    # - admin:    full access + user management
    # - operator: manage devices/shells/files, but cannot manage users
    # - viewer:   read-only (no shell, no writes)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # TOTP secret (base32) for optional 2FA. Empty/None = 2FA disabled.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)

    devices: Mapped[list["Device"]] = relationship(back_populates="owner")


class Device(Base):
    """A remote host the user can monitor and shell into over SSH."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128))
    auth_method: Mapped[str] = mapped_column(String(16), default="password")  # password | key
    # Encrypted credentials at rest.
    password_enc: Mapped[str] = mapped_column(Text, default="")
    private_key_enc: Mapped[str] = mapped_column(Text, default="")
    # Optional jump host: connect to this device THROUGH another owned device.
    bastion_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    # Optional metadata.
    os: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")  # comma-separated tags
    tailscale: Mapped[bool] = mapped_column(default=False)  # reachable via Tailscale (.ts.net)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped["User"] = relationship(back_populates="devices")
    sessions: Mapped[list["SessionLog"]] = relationship(back_populates="device")


class SessionLog(Base):
    """Audit log of every remote shell session opened through ShellDeck."""

    __tablename__ = "session_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Plain-text command transcript (audit trail), NOT the full TTY recording.
    transcript: Mapped[str] = mapped_column(Text, default="")
    # Newline-separated list of commands typed by the user during the session.
    commands: Mapped[str] = mapped_column(Text, default="")
    # Full TTY recording in asciinema-style event stream (JSON) for playback.
    # Format: {"version":2,"width":W,"height":H,"events":[[delay,type,data],...]}
    #   type "o" = output (device->user), "i" = input (user->device)
    recording: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped["Device"] = relationship(back_populates="sessions")


class Snippet(Base):
    """User-saved shell command snippets for quick reuse in the terminal."""

    __tablename__ = "snippets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped["User"] = relationship()


class SettingsRow(Base):
    """Singleton app settings (id is always 1). Currently holds notification config."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    notify_enabled: Mapped[bool] = mapped_column(default=False)
    telegram_token_enc: Mapped[str] = mapped_column(Text, default="")   # bot token, encrypted
    telegram_chat_id: Mapped[str] = mapped_column(String(64), default="")
    discord_webhook: Mapped[str] = mapped_column(Text, default="")      # raw URL, not secret-critical
    ntfy_url: Mapped[str] = mapped_column(Text, default="")          # ntfy/ntfy.sh topic URL
    gotify_url: Mapped[str] = mapped_column(Text, default="")         # Gotify server+token URL
    slack_webhook: Mapped[str] = mapped_column(Text, default="")       # Slack incoming webhook
    email_to: Mapped[str] = mapped_column(String(255), default="")       # SMTP recipient
    email_host: Mapped[str] = mapped_column(String(255), default="")
    email_port: Mapped[int] = mapped_column(Integer, default=587)
    email_user: Mapped[str] = mapped_column(String(255), default="")
    email_pass_enc: Mapped[str] = mapped_column(Text, default="")      # SMTP password, encrypted
    webhook_url: Mapped[str] = mapped_column(Text, default="")         # custom generic webhook (POST JSON)
    monitor_interval: Mapped[int] = mapped_column(Integer, default=60)  # seconds between checks
    public_dashboard: Mapped[bool] = mapped_column(default=False)       # allow unauth /public view
    oidc_enabled: Mapped[bool] = mapped_column(default=False)          # enable OIDC SSO login
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Jakarta")  # IANA tz for displaying timestamps


class ScheduledTask(Base):
    """A command scheduled to run periodically across selected devices."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(Text)
    # JSON list of device ids this task runs against.
    device_ids: Mapped[str] = mapped_column(Text, default="[]")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # Optional cron expression (5-field: "m h dom mon dow"). If set, overrides
    # interval_minutes for scheduling. Only used when run_once is False.
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    run_once: Mapped[bool] = mapped_column(default=False)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Captured stdout/stderr from the most recent run (audit / visibility).
    last_output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped["User"] = relationship()


class Agent(Base):
    """A reverse-tunnel agent connected from a NAT-traversed device.

    Agents connect outward from the device to ShellDeck over WebSocket, so no
    inbound port is needed on the device. ShellDeck can then relay commands to
    the device through the live tunnel. Each agent has a shared secret token.
    """

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # shared secret
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    connected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped["User"] = relationship()


class AuditLog(Base):
    """Security/activity audit trail: logins (success + failure), key actions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)  # snapshot, survives user deletion
    action: Mapped[str] = mapped_column(String(48), index=True)  # "login", "login_failed", "logout", etc.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    user: Mapped["User"] = relationship()


class TopologySnapshot(Base):
    """Stored topology scan result: nodes (devices + discovered hosts) + edges."""

    __tablename__ = "topology_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_time: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    nodes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    discovered_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    def nodes(self) -> list[dict]:
        return _json.loads(self.nodes_json or "[]")

    def edges(self) -> list[dict]:
        return _json.loads(self.edges_json or "[]")

    def discovered(self) -> list[dict]:
        return _json.loads(self.discovered_json or "[]")
