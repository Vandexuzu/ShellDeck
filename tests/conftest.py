"""Test fixtures: give each test a clean database."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_shelldeck.db")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from app.db import Base, SessionLocal, init_db
import app.models  # noqa: F401  register models


@pytest.fixture(autouse=True)
def clean_db():
    # Recreate all tables before each test for full isolation.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Monkeypatch the module-level engine/session used by the app.
    import app.db as db_mod
    db_mod.engine = engine
    db_mod.SessionLocal = SessionLocal.__class__(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
