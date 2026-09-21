"""Secret Manager API - encrypted storage for API keys, tokens, passwords."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Secret
from app.security import get_current_user, operator_only, User
from app.config import encrypt as encrypt_value, decrypt as decrypt_value

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


class SecretCreate(BaseModel):
    name: str
    value: str
    description: str | None = None


class SecretUpdate(BaseModel):
    value: str | None = None
    description: str | None = None


class SecretOut(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: str
    updated_at: str | None


@router.get("/")
def list_secrets(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[SecretOut]:
    """List all secrets (values hidden)."""
    secrets = db.query(Secret).filter(Secret.owner_id == user.id).all()
    return [
        SecretOut(
            id=s.id,
            name=s.name,
            description=s.description,
            created_at=s.created_at.isoformat() if s.created_at else "",
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
        )
        for s in secrets
    ]


@router.post("/")
def create_secret(payload: SecretCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> SecretOut:
    """Create a new secret."""
    # Check if name already exists
    existing = db.query(Secret).filter(Secret.owner_id == user.id, Secret.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Secret '{payload.name}' already exists")
    
    secret = Secret(
        owner_id=user.id,
        name=payload.name,
        value_enc=encrypt_value(payload.value),
        description=payload.description,
    )
    db.add(secret)
    db.commit()
    db.refresh(secret)
    
    return SecretOut(
        id=secret.id,
        name=secret.name,
        description=secret.description,
        created_at=secret.created_at.isoformat() if secret.created_at else "",
        updated_at=None,
    )


@router.get("/{secret_id}/value")
def get_secret_value(secret_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> dict:
    """Get decrypted secret value."""
    secret = db.query(Secret).filter(Secret.id == secret_id, Secret.owner_id == user.id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    
    return {"name": secret.name, "value": decrypt_value(secret.value_enc)}


@router.put("/{secret_id}")
def update_secret(secret_id: int, payload: SecretUpdate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> SecretOut:
    """Update a secret."""
    from datetime import datetime
    secret = db.query(Secret).filter(Secret.id == secret_id, Secret.owner_id == user.id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    
    if payload.value is not None:
        secret.value_enc = encrypt_value(payload.value)
    if payload.description is not None:
        secret.description = payload.description
    secret.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(secret)
    
    return SecretOut(
        id=secret.id,
        name=secret.name,
        description=secret.description,
        created_at=secret.created_at.isoformat() if secret.created_at else "",
        updated_at=secret.updated_at.isoformat() if secret.updated_at else None,
    )


@router.delete("/{secret_id}")
def delete_secret(secret_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    """Delete a secret."""
    secret = db.query(Secret).filter(Secret.id == secret_id, Secret.owner_id == user.id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    
    db.delete(secret)
    db.commit()
    return {"status": "deleted"}
