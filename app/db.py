"""Database engine, session factory and helpers."""
from __future__ import annotations

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
        ]
        for table, col, ddl in expected:
            if table not in insp.get_table_names():
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
