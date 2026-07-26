"""User management: admin-only endpoints for RBAC.

Only `admin` users may list/create/delete users or change roles.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import UserCreate, UserOut, UserRoleUpdate
from app.security import get_current_user, hash_password, admin_only

router = APIRouter(prefix="/api/users", tags=["users"])


def _admin(user: User = Depends(admin_only)) -> User:
    return user


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(_admin)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(_admin)) -> User:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="Username already registered")
    role = payload.role if payload.role in ("admin", "operator", "viewer") else "viewer"
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=role,
        is_admin=(role == "admin"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/role", response_model=UserOut)
def change_role(user_id: int, payload: UserRoleUpdate, db: Session = Depends(get_db), _: User = Depends(_admin)) -> User:
    if payload.role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be admin|operator|viewer")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Prevent an admin from locking themselves out: last admin cannot be demoted.
    if user.role == "admin" and payload.role != "admin":
        admin_count = db.scalar(select(User).where(User.role == "admin"))
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last admin")
    user.role = payload.role
    user.is_admin = (payload.role == "admin")
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if user.role == "admin":
        admin_count = db.scalar(select(User).where(User.role == "admin"))
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    # Cascade: remove the user's devices and snippets first (FK NOT NULL on owner_id).
    from app.models import Device, Snippet
    db.query(Device).filter(Device.owner_id == user.id).delete()
    db.query(Snippet).filter(Snippet.owner_id == user.id).delete()
    db.delete(user)
    db.commit()
