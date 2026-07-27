"""One-off migration: add SessionLog.recording column for TTY playback.

Run once after pulling this version:
    python migrations/add_recording_column.py
Safe to re-run (checks for existing column first).
"""
from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
cols = [c[1] for c in db.execute(text("PRAGMA table_info(session_logs)")).fetchall()]
if "recording" not in cols:
    db.execute(text("ALTER TABLE session_logs ADD COLUMN recording TEXT"))
    db.commit()
    print("Added 'recording' column to session_logs.")
else:
    print("'recording' column already present — nothing to do.")
db.close()
