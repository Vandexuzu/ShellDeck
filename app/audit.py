"""Shared audit-trail logging for ShellDeck.

Every notable, state-changing action (create/update/delete device, file ops,
run command, docker action, role change, 2FA changes, etc.) is recorded into
the `audit_log` table so admins get a per-user activity trail.

This mirrors the original `log_audit()` in `app/routers/auth.py` but is usable
from any router (sync or async) without forcing a `Request` parameter — when no
request/IP is available it simply records a NULL ip.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def log_audit(
    db: Session,
    user,
    action: str,
    detail: str | None = None,
    *,
    request: Request | None = None,
    ip: str | None = None,
) -> None:
    """Best-effort append to the audit trail.

    Failures are swallowed (and the partial audit row rolled back) so the
    calling endpoint never breaks because of a logging issue. The caller's
    own committed state is unaffected because `log_audit` commits only the
    audit row it just added.
    """
    try:
        db.add(AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else None,
            action=action,
            detail=detail,
            ip=ip or _client_ip(request),
        ))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
