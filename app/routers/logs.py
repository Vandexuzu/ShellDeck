"""Security AI APIs: log anomaly, config review, command audit.

All scan results are persisted to `security_findings` and can trigger
notifications (gated per-event in settings). A background loop can re-run
the command-anomaly scan periodically over new sessions.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_service import AIService, AIClientError, get_ai_service
from app.db import get_db, SessionLocal
from app.models import SecurityFinding, SessionLog, SettingsRow, User
from app.notifications import notify
from app.security import get_current_user, User as _U

router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = logging.getLogger("shelldeck.security")

_SEV_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _sev_rank(s: str) -> int:
    return _SEV_RANK.get((s or "low").lower(), 0)


class LogAnalyzeRequest(BaseModel):
    log_text: str
    context: str | None = None


class ConfigReviewRequest(BaseModel):
    filename: str
    content: str


class CommandAnomalyRequest(BaseModel):
    commands: str  # newline-separated


def _strip_fences(response: str) -> str:
    clean = response.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1]
    if clean.endswith("```"):
        clean = clean.rsplit("```", 1)[0]
    return clean.strip()


async def _ask_json(ai_service: AIService, user: User, msg: str, fallback: dict) -> dict:
    """Send prompt, parse JSON answer; on garbage return fallback with raw summary."""
    try:
        response = await ai_service.chat(msg, user)
    except AIClientError as e:
        raise HTTPException(status_code=502, detail=f"AI service error: {e}")
    try:
        return json.loads(_strip_fences(response))
    except json.JSONDecodeError:
        out = dict(fallback)
        out["summary"] = response[:2000]
        return out


def _finding_severity(kind: str, result: dict) -> str:
    """Overall severity from AI result, per kind."""
    if kind == "log":
        return str(result.get("severity") or "low").lower()
    if kind == "config":
        return str(result.get("risk") or "low").lower()
    if kind == "command":
        flagged = result.get("flagged") or []
        return max((str(f.get("risk") or "low").lower() for f in flagged), key=_sev_rank, default="low")
    return "low"


_EVENT_FOR_KIND = {"log": "log_anomaly", "config": "config_review", "command": "command_anomaly"}


def save_finding(db: Session, kind: str, result: dict, source: str = "manual") -> SecurityFinding:
    """Persist a scan result. Prunes to the newest 200 rows to bound growth."""
    severity = _finding_severity(kind, result)
    summary = str(result.get("summary") or "")[:2000]
    f = SecurityFinding(
        kind=kind, severity=severity, summary=summary,
        detail_json=json.dumps(result)[:60000], source=source,
    )
    db.add(f)
    db.flush()
    # ponytail: prune keeps newest 200 rows; fine for single-instance SQLite.
    old = db.scalars(
        select(SecurityFinding.id).order_by(SecurityFinding.id.desc()).offset(200)
    ).all()
    if old:
        for fid in old:
            row = db.get(SecurityFinding, fid)
            if row:
                db.delete(row)
    db.commit()
    db.refresh(f)
    return f


async def maybe_notify_finding(db: Session, f: SecurityFinding, min_sev: str = "high") -> None:
    """Notify if finding severity >= min_sev and the per-event gate allows it."""
    if _sev_rank(f.severity) < _sev_rank(min_sev):
        return
    msg = (f"<b>ShellDeck security [{f.severity.upper()}]</b>\n"
           f"{f.kind} scan: {f.summary[:300] or '(no summary)'}")
    await notify(msg, db, event=_EVENT_FOR_KIND.get(f.kind))


async def _ask_and_save(
    ai_service: AIService, user: User, db: Session,
    kind: str, msg: str, fallback: dict, source: str = "manual", notify_min: str = "high",
) -> dict:
    result = await _ask_json(ai_service, user, msg, fallback)
    f = save_finding(db, kind, result, source=source)
    result = dict(result)
    result["finding_id"] = f.id
    await maybe_notify_finding(db, f, min_sev=notify_min)
    return result


@router.post("/analyze")
async def analyze_logs(
    req: LogAnalyzeRequest,
    current_user: _U = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
) -> dict:
    """Analyze log text for anomalies using AI; persist result."""
    if not req.log_text or len(req.log_text.strip()) < 10:
        raise HTTPException(status_code=400, detail="Log text too short")
    log_text = req.log_text[:50000]

    msg = f"""Analyze the following system log for security anomalies, errors, and suspicious patterns.

Context: {req.context or 'General system log analysis'}

Log:
{log_text}

Focus on: failed logins, privilege escalation, unusual access patterns, errors, warnings, resource issues.
Return ONLY valid JSON: {{"anomalies": [{{"type": "...", "severity": "low|medium|high|critical", "description": "..."}}], "severity": "low|medium|high|critical", "summary": "...", "recommendations": ["..."]}}"""

    return await _ask_and_save(
        ai_service, current_user, db, "log", msg,
        {"anomalies": [], "severity": "low", "summary": "", "recommendations": ["Review log manually — AI returned non-JSON response"]},
    )


@router.post("/config-review")
async def review_config(
    req: ConfigReviewRequest,
    current_user: _U = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
) -> dict:
    """Security-review a config file; persist result."""
    if not req.content or len(req.content.strip()) < 5:
        raise HTTPException(status_code=400, detail="Config content too short")
    content = req.content[:80000]

    msg = f"""You are a security engineer. Review this config file for security issues, misconfigurations, and hardening opportunities.

Filename: {req.filename}

```
{content}
```

Return ONLY valid JSON: {{"risk": "low|medium|high|critical", "findings": [{{"severity": "low|medium|high|critical", "issue": "...", "fix": "..."}}], "summary": "..."}}"""

    return await _ask_and_save(
        ai_service, current_user, db, "config", msg,
        {"risk": "low", "findings": [], "summary": ""},
    )


@router.post("/command-anomaly")
async def analyze_commands(
    req: CommandAnomalyRequest,
    current_user: _U = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
) -> dict:
    """Flag suspicious commands from an audit transcript; persist result."""
    cmds = req.commands.strip()
    if not cmds:
        raise HTTPException(status_code=400, detail="No commands provided")
    cmds = cmds[:30000]

    msg = f"""You are a security analyst. Review these shell commands from an audit log and flag suspicious or destructive ones (e.g. rm -rf /, chmod 777, curl | bash, credential access, reverse shells, history wiping).

Commands:
{cmds}

Return ONLY valid JSON: {{"flagged": [{{"command": "...", "risk": "low|medium|high|critical", "reason": "..."}}], "summary": "..."}}"""

    return await _ask_and_save(
        ai_service, current_user, db, "command", msg,
        {"flagged": [], "summary": ""},
    )


# ----------------------------- Findings history -----------------------------
@router.get("/findings")
def list_findings(
    include_dismissed: bool = False, limit: int = 50,
    db: Session = Depends(get_db), _: _U = Depends(get_current_user),
) -> list[dict]:
    q = select(SecurityFinding).order_by(SecurityFinding.created_at.desc()).limit(max(1, min(limit, 200)))
    if not include_dismissed:
        q = q.where(SecurityFinding.dismissed.is_(False))
    return [
        {
            "id": f.id, "kind": f.kind, "severity": f.severity, "summary": f.summary,
            "source": f.source, "dismissed": f.dismissed,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "detail": json.loads(f.detail_json or "{}"),
        }
        for f in db.scalars(q).all()
    ]


@router.post("/findings/{finding_id}/dismiss")
def dismiss_finding(
    finding_id: int, db: Session = Depends(get_db), _: _U = Depends(get_current_user)
) -> dict:
    f = db.get(SecurityFinding, finding_id)
    if not f:
        raise HTTPException(status_code=404, detail="Finding not found")
    f.dismissed = True
    db.commit()
    return {"status": "dismissed", "id": finding_id}


# ----------------------------- Auto-scan loop -------------------------------
def _get_settings(db: Session) -> SettingsRow:
    row = db.get(SettingsRow, 1)
    if row is None:
        row = SettingsRow(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


async def run_command_scan(db: Session, ai_service: AIService, since: datetime) -> SecurityFinding | None:
    """Collect commands from sessions started after `since` and AI-scan them."""
    rows = db.scalars(
        select(SessionLog).where(SessionLog.started_at >= since).limit(200)
    ).all()
    cmds = sorted({c for r in rows for c in (r.commands or "").split("\n") if c.strip()})[:500]
    if not cmds:
        return None
    admin = db.scalar(select(User).where(User.role == "admin").order_by(User.id))
    if admin is None:
        return None

    msg = f"""You are a security analyst. Review these shell commands from an audit log and flag suspicious or destructive ones (e.g. rm -rf /, chmod 777, curl | bash, credential access, reverse shells, history wiping).

Commands:
{chr(10).join(cmds)[:30000]}

Return ONLY valid JSON: {{"flagged": [{{"command": "...", "risk": "low|medium|high|critical", "reason": "..."}}], "summary": "..."}}"""

    try:
        result = await _ask_json(ai_service, admin, msg, {"flagged": [], "summary": ""})
    except HTTPException as e:  # AI not configured / provider error
        logger.warning("auto-scan skipped: %s", e.detail)
        return None
    f = save_finding(db, "command", result, source="auto")
    s = _get_settings(db)
    await maybe_notify_finding(db, f, min_sev=s.auto_scan_notify_min or "high")
    return f


async def security_scan_loop(check_every: int = 900) -> None:
    """Background loop: periodic AI command-anomaly scan over new sessions.

    Last scan time = created_at of the newest auto finding (no extra state);
    first run scans the last `interval` window.
    """
    while True:
        await asyncio.sleep(check_every)
        db = SessionLocal()
        try:
            s = _get_settings(db)
            if not s.auto_scan_enabled:
                continue
            interval = timedelta(hours=max(1, s.auto_scan_interval_h or 6))
            last_auto = db.scalar(
                select(SecurityFinding.created_at)
                .where(SecurityFinding.source == "auto", SecurityFinding.kind == "command")
                .order_by(SecurityFinding.created_at.desc()).limit(1)
            )
            since = last_auto if last_auto else datetime.now(timezone.utc).replace(tzinfo=None) - interval
            if datetime.now(timezone.utc).replace(tzinfo=None) - since < interval:
                continue  # not due yet
            ai_service = get_ai_service()
            f = await run_command_scan(db, ai_service, since)
            if f:
                logger.info("auto command scan done: severity=%s", f.severity)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("security_scan_loop error")  # never let the loop die
            await asyncio.sleep(30)
        finally:
            db.close()
