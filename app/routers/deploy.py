"""Deploy Templates API - pre-configured deployment templates."""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeployTemplate
from app.security import get_current_user, operator_only, User

router = APIRouter(prefix="/api/deploy", tags=["deploy"])


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    stack_type: str  # lamp, node_mongo, static, custom
    config_json: str  # JSON string


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    stack_type: str
    config_json: str
    created_at: str


# Pre-built templates
BUILTIN_TEMPLATES = [
    {
        "name": "LAMP Stack",
        "description": "Apache + MySQL + PHP",
        "stack_type": "lamp",
        "config_json": json.dumps({
            "packages": ["apache2", "mysql-server", "php", "php-mysql"],
            "commands": [
                "sudo apt update",
                "sudo apt install -y apache2 mysql-server php php-mysql",
                "sudo systemctl enable apache2 mysql",
                "sudo systemctl start apache2 mysql"
            ]
        })
    },
    {
        "name": "Node.js + MongoDB",
        "description": "Node.js runtime with MongoDB database",
        "stack_type": "node_mongo",
        "config_json": json.dumps({
            "packages": ["nodejs", "npm", "mongodb"],
            "commands": [
                "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -",
                "sudo apt install -y nodejs mongodb",
                "sudo systemctl enable mongodb",
                "sudo systemctl start mongodb"
            ]
        })
    },
    {
        "name": "Static Site (Nginx)",
        "description": "Nginx for static file serving",
        "stack_type": "static",
        "config_json": json.dumps({
            "packages": ["nginx"],
            "commands": [
                "sudo apt update",
                "sudo apt install -y nginx",
                "sudo systemctl enable nginx",
                "sudo systemctl start nginx"
            ]
        })
    },
    {
        "name": "Docker + Docker Compose",
        "description": "Docker runtime with Compose",
        "stack_type": "docker",
        "config_json": json.dumps({
            "packages": ["docker.io", "docker-compose-v2"],
            "commands": [
                "sudo apt update",
                "sudo apt install -y docker.io docker-compose-v2",
                "sudo systemctl enable docker",
                "sudo systemctl start docker",
                "sudo usermod -aG docker $USER"
            ]
        })
    },
]


@router.get("/builtin")
def list_builtin_templates() -> list[dict]:
    """List built-in deployment templates."""
    return BUILTIN_TEMPLATES


@router.get("/")
def list_templates(db: Session = Depends(get_db), user: User = Depends(operator_only)) -> list[TemplateOut]:
    """List user's custom templates."""
    templates = db.query(DeployTemplate).filter(DeployTemplate.owner_id == user.id).all()
    return [
        TemplateOut(
            id=t.id,
            name=t.name,
            description=t.description,
            stack_type=t.stack_type,
            config_json=t.config_json,
            created_at=t.created_at.isoformat() if t.created_at else "",
        )
        for t in templates
    ]


@router.post("/")
def create_template(payload: TemplateCreate, db: Session = Depends(get_db), user: User = Depends(operator_only)) -> TemplateOut:
    """Create a custom template."""
    template = DeployTemplate(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        stack_type=payload.stack_type,
        config_json=payload.config_json,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    
    return TemplateOut(
        id=template.id,
        name=template.name,
        description=template.description,
        stack_type=template.stack_type,
        config_json=template.config_json,
        created_at=template.created_at.isoformat() if template.created_at else "",
    )


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(operator_only)):
    """Delete a custom template."""
    template = db.query(DeployTemplate).filter(
        DeployTemplate.id == template_id,
        DeployTemplate.owner_id == user.id
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    db.delete(template)
    db.commit()
    return {"status": "deleted"}
