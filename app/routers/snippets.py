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
def list_snippets(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Snippet]:
    return list(db.scalars(select(Snippet).where(Snippet.owner_id == user.id).order_by(Snippet.name)))


@router.post("", response_model=SnippetOut, status_code=status.HTTP_201_CREATED)
def create_snippet(payload: SnippetCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> Snippet:
    snippet = Snippet(owner_id=user.id, name=payload.name, command=payload.command)
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
