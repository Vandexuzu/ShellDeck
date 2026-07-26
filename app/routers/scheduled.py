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
        enabled=payload.enabled,
        next_run=now + timedelta(minutes=payload.interval_minutes),
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
