"""One-off migration: add SessionLog.recording and User.totp_secret columns.

Run once after pulling this version:
    python migrations/add_recording_column.py
Safe to re-run (checks for existing columns first).
"""
from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
for table, col, ddl in [
    ("session_logs", "recording", "ALTER TABLE session_logs ADD COLUMN recording TEXT"),
    ("users", "totp_secret", "ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"),
]:
    cols = [c[1] for c in db.execute(text(f"PRAGMA table_info({table})")).fetchall()]
    if col not in cols:
        db.execute(text(ddl))
        db.commit()
        print(f"Added '{col}' column to {table}.")
    else:
        print(f"'{col}' column already present in {table} — nothing to do.")
db.close()
