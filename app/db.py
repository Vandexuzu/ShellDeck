"""Database engine, session factory and helpers."""
from __future__ import annotations

import secrets
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# SQLite needs check_same_thread=False for use across FastAPI threads.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Import models first so they register on Base."""
    import app.models  # noqa: F401  (side-effect: registers models)

    Base.metadata.create_all(bind=engine)
    # Idempotent column migration for existing databases (does not drop data).
    # create_all() won't add columns to already-created tables, so new columns
    # added in later releases (e.g. users.totp_secret) must be added explicitly
    # or existing installs break / silently lose data.
    _migrate_columns()
    # Seed a few useful starter snippets for the first user (only if the
    # snippets table is empty) so a fresh install isn't a blank slate.
    _seed_default_snippets()


# Popular starter snippets seeded on first run (owner = first user, id 1).
_DEFAULT_SNIPPETS = [
    ("Disk usage", "df -h"),
    ("Memory usage", "free -m"),
    ("System uptime", "uptime"),
    ("Update package lists", "sudo apt update"),
    ("Upgrade packages", "sudo apt upgrade -y"),
    ("Service status", "systemctl status"),
    ("Listening ports", "ss -tulpn"),
    ("Recent log lines", "journalctl -n 50 --no-pager"),
    ("Reboot", "sudo reboot"),
    ("Show IP addresses", "ip -br addr"),
]


def _seed_default_snippets() -> None:
    from sqlalchemy import select

    from app.models import Snippet, User

    with SessionLocal() as db:
        if db.scalar(select(Snippet).limit(1)) is not None:
            return  # already seeded
        owner = db.get(User, 1) or db.scalar(select(User).order_by(User.id))
        if owner is None:
            return  # no users yet; seeded on first registration instead
        for name, command in _DEFAULT_SNIPPETS:
            db.add(Snippet(owner_id=owner.id, name=name, command=command))
        db.commit()


def _migrate_columns() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with SessionLocal() as db:
        # (table, column, DDL) — only added if missing.
        expected = [
            ("users", "totp_secret", "VARCHAR(64)"),
            ("snippets", "category", "VARCHAR(64)"),
            ("settings", "timezone", "VARCHAR(64) DEFAULT 'Asia/Jakarta'"),
            ("agents", "ips", "TEXT"),
            ("agents", "os", "VARCHAR(32)"),
            ("settings", "theme", "VARCHAR(32) DEFAULT 'dark'"),
            ("settings", "session_retention_days", "INTEGER DEFAULT 90"),
            ("settings", "agent_heartbeat", "INTEGER DEFAULT 15"),
            ("settings", "agent_reconnect", "INTEGER DEFAULT 5"),
            ("settings", "enroll_secret", "VARCHAR(64)"),
            ("settings", "enroll_owner_id", "INTEGER"),
            ("agents", "pending", "BOOLEAN DEFAULT 0"),
            ("agents", "install_slug", "VARCHAR(64)"),
        ]
        for table, col, ddl in expected:
            if table not in insp.get_table_names():
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        db.commit()
    _seed_enroll_secret()


def _seed_enroll_secret() -> None:
    """Create a self-enrollment secret on first run so `install.sh` works out of
    the box. The secret is owned by the first admin user (id 1 or first admin found).
    Admins can rotate/revoke it later from Settings."""
    from sqlalchemy import select

    from app.models import SettingsRow, User

    with SessionLocal() as db:
        row = db.get(SettingsRow, 1)
        if row is None:
            row = SettingsRow(id=1)
            db.add(row)
        if row.enroll_secret:
            return  # already set — don't overwrite a rotated value
        owner = (
            db.scalar(select(User).where(User.role == "admin").order_by(User.id))
            or db.get(User, 1)
            or db.scalar(select(User).order_by(User.id))
        )
        row.enroll_secret = secrets.token_urlsafe(24)
        row.enroll_owner_id = owner.id if owner else None
        db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
