"""Pydantic schemas for request/response bodies."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------- Auth ---------------------------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TotpSetup(BaseModel):
    secret: str
    code: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str = "viewer"  # admin | operator | viewer


class UserRoleUpdate(BaseModel):
    role: str  # admin | operator | viewer


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = None  # admin | operator | viewer


class ChangePassword(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_admin: bool
    created_at: datetime


# ------------------------------- Devices ------------------------------------
class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str
    port: int = 22
    username: str
    auth_method: str = "password"  # password | key
    password: str | None = None
    private_key: str | None = None
    os: str = ""
    notes: str = ""
    bastion_id: int | None = None  # optional jump host (another owned device)
    tags: str = ""  # comma-separated
    tailscale: bool = False


class DeviceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    auth_method: str | None = None
    password: str | None = None
    private_key: str | None = None
    os: str | None = None
    notes: str | None = None
    bastion_id: int | None = None
    tags: str | None = None
    tailscale: bool | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    name: str
    host: str
    port: int
    username: str
    auth_method: str
    os: str
    notes: str
    bastion_id: int | None = None
    tags: str = ""
    tailscale: bool = False
    last_seen: datetime | None
    created_at: datetime
    has_agent: bool = False


class SessionOut(BaseModel):
    id: int
    device_id: int
    device_name: str
    device_host: str
    started_at: str | None
    ended_at: str | None
    duration_s: int | None
    commands: str | None = ""
    transcript: str | None = ""


class DeviceStatus(BaseModel):
    id: int
    name: str
    host: str
    reachable: bool
    message: str = ""
    cpu_load: float | None = None
    mem_used_pct: float | None = None
    disk_used_pct: float | None = None
    uptime: str | None = None
    tailscale: bool = False


# ------------------------------- Snippets -----------------------------------
class SnippetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1)
    category: str | None = None


class SnippetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    command: str
    category: str | None = None
    created_at: datetime


# ------------------------------- Bulk ---------------------------------------
class BulkRun(BaseModel):
    device_ids: list[int] = Field(min_length=1)
    command: str = Field(min_length=1)


class BulkResult(BaseModel):
    device_id: int
    name: str
    host: str
    reachable: bool
    output: str = ""
    error: str = ""


# ------------------------------- Files (SFTP) -------------------------------
class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float | None = None


class FilePath(BaseModel):
    path: str = ""


class FileWrite(BaseModel):
    path: str
    content: str = ""


# ------------------------------- Docker -------------------------------------
class DockerContainer(BaseModel):
    id: str = ""
    name: str = ""
    image: str = ""
    state: str = ""
    status: str = ""
    ports: str = ""


class DockerAction(BaseModel):
    container_id: str
    action: str  # start | stop | restart | pause | unpause | kill | remove


class DockerRun(BaseModel):
    command: str  # everything after `docker`, e.g. "images" or "run --rm alpine echo hi"
    pty: bool = False  # allocate a pseudo-terminal (needed for `exec -it`)


# ------------------------------- Settings ----------------------------------
class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    notify_enabled: bool
    telegram_chat_id: str
    discord_webhook: str
    ntfy_url: str = ""
    gotify_url: str = ""
    slack_webhook: str = ""
    email_to: str = ""
    email_host: str = ""
    email_port: int = 587
    email_user: str = ""
    webhook_url: str = ""
    monitor_interval: int
    public_dashboard: bool
    oidc_enabled: bool
    timezone: str = "Asia/Jakarta"


class SettingsUpdate(BaseModel):
    notify_enabled: bool | None = None
    telegram_token: str | None = None   # raw token; encrypted before storing
    telegram_chat_id: str | None = None
    discord_webhook: str | None = None
    ntfy_url: str | None = None
    gotify_url: str | None = None
    slack_webhook: str | None = None
    email_to: str | None = None
    email_host: str | None = None
    email_port: int | None = None
    email_user: str | None = None
    email_password: str | None = None   # raw password; encrypted before storing
    webhook_url: str | None = None
    monitor_interval: int | None = None
    public_dashboard: bool | None = None
    oidc_enabled: bool | None = None
    timezone: str | None = None


# ------------------------------- Scheduled tasks ----------------------------
class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1)
    device_ids: list[int] = Field(default_factory=list)
    interval_minutes: int = Field(default=60, ge=1, le=10080)
    cron: str | None = None  # optional 5-field cron expression
    enabled: bool = True
    run_once: bool = False  # if True, run a single time then disable
    run_at: datetime | None = None  # for run_once: schedule the single run at this time; if None, run immediately


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    command: str | None = None
    device_ids: list[int] | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    cron: str | None = None
    enabled: bool | None = None


class ScheduledTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    command: str
    device_ids: list[int]
    interval_minutes: int
    cron: str | None = None
    enabled: bool
    run_once: bool = False
    run_at: datetime | None = None
    last_run: datetime | None
    last_output: str = ""
    next_run: datetime | None
    created_at: datetime
