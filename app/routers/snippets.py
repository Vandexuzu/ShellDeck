"""Saved command snippets CRUD endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Snippet, User
from app.schemas import SnippetCreate, SnippetOut
from app.security import get_current_user, operator_only

router = APIRouter(prefix="/api/snippets", tags=["snippets"])


@router.get("", response_model=list[SnippetOut])
def list_snippets(category: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Snippet]:
    q = select(Snippet).where(Snippet.owner_id == user.id)
    if category:
        q = q.where(Snippet.category == category)
    return list(db.scalars(q.order_by(Snippet.name)))


@router.post("", response_model=SnippetOut, status_code=status.HTTP_201_CREATED)
def create_snippet(payload: SnippetCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Snippet:
    snippet = Snippet(owner_id=user.id, name=payload.name, command=payload.command, category=payload.category)
    db.add(snippet)
    db.commit()
    db.refresh(snippet)
    return snippet


@router.delete("/{snippet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snippet(snippet_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    snippet = db.get(Snippet, snippet_id)
    if snippet is None or snippet.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Snippet not found")
    db.delete(snippet)
    db.commit()


@router.get("/export")
def export_snippets(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    """Export all snippets as a plain list (no IDs) for backup."""
    return [{"name": s.name, "command": s.command} for s in db.scalars(select(Snippet).where(Snippet.owner_id == user.id))]


@router.post("/import")
def import_snippets(payload: list[SnippetCreate], db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Import snippets from an export (replaces if same name exists)."""
    created = 0
    for item in payload:
        existing = db.scalars(select(Snippet).where(Snippet.owner_id == user.id, Snippet.name == item.name)).first()
        if existing:
            existing.command = item.command
        else:
            db.add(Snippet(owner_id=user.id, name=item.name, command=item.command))
            created += 1
    db.commit()
    return {"imported": created}
