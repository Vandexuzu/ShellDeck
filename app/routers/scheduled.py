"""Scheduled tasks: run a command periodically across selected devices.

The scheduler loop lives in app.scheduler and is started from the app lifespan.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Device, ScheduledTask, User, SettingsRow, SessionLog
from app.routers.bulk import _run_on_device
from app.schemas import ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskUpdate
from app.security import operator_only
from app.audit import log_audit

router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])


def _serialize(task: ScheduledTask) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "command": task.command,
        "device_ids": json.loads(task.device_ids or "[]"),
        "interval_minutes": task.interval_minutes,
        "cron": task.cron,
        "enabled": task.enabled,
        "run_once": task.run_once,
        "run_at": task.run_at,
        "last_run": task.last_run,
        "last_output": task.last_output,
        "next_run": task.next_run,
        "created_at": task.created_at,
    }


def _next_run_for(task: ScheduledTask, now: datetime) -> datetime | None:
    """Compute the next scheduled run time for a recurring (non-run_once) task."""
    if task.cron:
        from app.cron import next_fire
        return next_fire(task.cron, now)
    if task.interval_minutes and task.interval_minutes > 0:
        return now + timedelta(minutes=task.interval_minutes)
    return None


def _validate_cron(cron: str | None) -> str | None:
    if not cron:
        return None
    from app.cron import parse_cron
    if parse_cron(cron) is None:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {cron}")
    return cron


@router.get("", response_model=list[ScheduledTaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[dict]:
    tasks = db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == user.id)).all()
    return [_serialize(t) for t in tasks]


@router.post("", response_model=ScheduledTaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: ScheduledTaskCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)
) -> dict:
    now = datetime.now()
    cron = _validate_cron(payload.cron)
    if cron and payload.run_once:
        raise HTTPException(status_code=400, detail="Cron cannot be used with run_once")
    task = ScheduledTask(
        owner_id=user.id,
        name=payload.name,
        command=payload.command,
        device_ids=json.dumps(payload.device_ids),
        interval_minutes=payload.interval_minutes,
        cron=cron,
        enabled=payload.enabled and not (payload.run_once and not payload.run_at),
        run_once=payload.run_once,
        run_at=payload.run_at,
        next_run=(None if payload.run_once else _next_run_for(
            ScheduledTask(interval_minutes=payload.interval_minutes, cron=cron), now))
            if not (payload.run_once and payload.run_at) else payload.run_at,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    log_audit(db, user, "scheduled_create", f"name={task.name} device_ids={payload.device_ids}"
              + (f" cron={task.cron}" if task.cron else f" interval={task.interval_minutes}m"))
    return _serialize(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    task = db.get(ScheduledTask, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    log_audit(db, user, "scheduled_delete", f"id={task_id} name={task.name}")


@router.post("/{task_id}/run")
async def run_task_now(task_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Trigger a task immediately (Run Now), regardless of schedule."""
    task = db.get(ScheduledTask, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    await run_task(task, db)
    log_audit(db, user, "scheduled_run", f"id={task_id} name={task.name}")
    return {"ok": True, "last_run": task.last_run.isoformat() if task.last_run else None}


@router.get("/export")
def export_tasks(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[dict]:
    """Export all scheduled tasks (no IDs/run-state) for backup."""
    tasks = db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == user.id)).all()
    return [_serialize(t) | {"id": None, "last_run": None, "next_run": None, "created_at": None} for t in tasks]


@router.post("/import")
def import_tasks(payload: list[ScheduledTaskCreate], db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Import scheduled tasks from an export (re-creates with fresh schedule)."""
    from datetime import timedelta
    created = 0
    for item in payload:
        now = datetime.now()
        cron = _validate_cron(item.cron)
        db.add(ScheduledTask(
            owner_id=user.id,
            name=item.name,
            command=item.command,
            device_ids=json.dumps(item.device_ids),
            interval_minutes=item.interval_minutes,
            cron=cron,
            enabled=item.enabled and not (item.run_once and not item.run_at),
            run_once=item.run_once,
            run_at=item.run_at,
            next_run=(None if item.run_once else _next_run_for(
                ScheduledTask(interval_minutes=item.interval_minutes, cron=cron), now))
                if not (item.run_once and item.run_at) else item.run_at,
        ))
        created += 1
    db.commit()
    log_audit(db, user, "scheduled_import", f"imported={created}")
    return {"imported": created}


async def run_task(task: ScheduledTask, db: Session) -> None:
    """Execute the task's command across its devices (best-effort)."""
    from app.routers.bulk import _run_on_device
    device_ids = json.loads(task.device_ids or "[]")
    devices = db.scalars(select(Device).where(Device.id.in_(device_ids), Device.owner_id == task.owner_id)).all()
    outputs = []
    for d in devices:
        try:
            res = await _run_on_device(d, task.command, db)
            if res.reachable:
                outputs.append(f"[{res.name}] {res.output}")
            else:
                outputs.append(f"[{res.name}] ERROR: {res.error}")
        except Exception as exc:  # noqa: BLE001
            outputs.append(f"[{d.name}] EXCEPTION: {type(exc).__name__}: {exc}")
            import logging
            logging.getLogger("shelldeck.scheduler").exception("scheduled task %s failed on %s", task.id, d.name)
    task.last_output = "\n".join(outputs)
    task.last_run = datetime.now()
    if task.run_once:
        task.enabled = False
        task.next_run = None
    else:
        task.next_run = _next_run_for(task, task.last_run)
    db.commit()


async def scheduler_loop() -> None:
    """Wake every minute and run any due, enabled tasks."""
    while True:
        try:
            await asyncio.sleep(60)
            db = SessionLocal()
            try:
                now = datetime.now()
                due = db.scalars(
                    select(ScheduledTask).where(
                        ScheduledTask.enabled == True,  # noqa: E712
                        ScheduledTask.next_run <= now,
                    )
                ).all()
                for task in due:
                    await run_task(task, db)
                # Session-log retention: purge old session records so the DB
                # doesn't grow unbounded (recordings can be large). Respect the
                # global setting (0 = keep forever).
                settings = db.get(SettingsRow, 1)
                if settings and settings.session_retention_days > 0:
                    cutoff = now - timedelta(days=settings.session_retention_days)
                    deleted = db.query(SessionLog).filter(
                        SessionLog.started_at < cutoff
                    ).delete()
                    if deleted:
                        db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)
