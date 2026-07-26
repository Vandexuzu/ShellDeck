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
from app.models import Device, ScheduledTask, User
from app.routers.bulk import _run_on_device
from app.schemas import ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskUpdate
from app.security import operator_only

router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])


def _serialize(task: ScheduledTask) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "command": task.command,
        "device_ids": json.loads(task.device_ids or "[]"),
        "interval_minutes": task.interval_minutes,
        "enabled": task.enabled,
        "run_once": task.run_once,
        "last_run": task.last_run,
        "next_run": task.next_run,
        "created_at": task.created_at,
    }


@router.get("", response_model=list[ScheduledTaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[dict]:
    tasks = db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == user.id)).all()
    return [_serialize(t) for t in tasks]


@router.post("", response_model=ScheduledTaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: ScheduledTaskCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)
) -> dict:
    now = datetime.now(timezone.utc)
    task = ScheduledTask(
        owner_id=user.id,
        name=payload.name,
        command=payload.command,
        device_ids=json.dumps(payload.device_ids),
        interval_minutes=payload.interval_minutes,
        enabled=payload.enabled and not payload.run_once,
        run_once=payload.run_once,
        next_run=None if payload.run_once else now + timedelta(minutes=payload.interval_minutes),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    task = db.get(ScheduledTask, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()


@router.post("/{task_id}/run")
async def run_task_now(task_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Trigger a task immediately (Run Now), regardless of schedule."""
    task = db.get(ScheduledTask, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    await run_task(task, db)
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
        now = datetime.now(timezone.utc)
        db.add(ScheduledTask(
            owner_id=user.id,
            name=item.name,
            command=item.command,
            device_ids=json.dumps(item.device_ids),
            interval_minutes=item.interval_minutes,
            enabled=item.enabled and not item.run_once,
            run_once=item.run_once,
            next_run=None if item.run_once else now + timedelta(minutes=item.interval_minutes),
        ))
        created += 1
    db.commit()
    return {"imported": created}


async def run_task(task: ScheduledTask, db: Session) -> None:
    """Execute the task's command across its devices (best-effort)."""
    device_ids = json.loads(task.device_ids or "[]")
    devices = db.scalars(select(Device).where(Device.id.in_(device_ids), Device.owner_id == task.owner_id)).all()
    for d in devices:
        try:
            await _run_on_device(d, task.command)
        except Exception:
            pass
    task.last_run = datetime.now(timezone.utc)
    if task.run_once:
        task.enabled = False
        task.next_run = None
    else:
        task.next_run = task.last_run + timedelta(minutes=task.interval_minutes)
    db.commit()


async def scheduler_loop() -> None:
    """Wake every minute and run any due, enabled tasks."""
    while True:
        try:
            await asyncio.sleep(60)
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                due = db.scalars(
                    select(ScheduledTask).where(
                        ScheduledTask.enabled == True,  # noqa: E712
                        ScheduledTask.next_run <= now,
                    )
                ).all()
                for task in due:
                    await run_task(task, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)
