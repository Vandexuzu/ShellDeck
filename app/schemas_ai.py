"""Pydantic schemas for AI assistant feature."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------- AI Chat ------------------------------------
class AIChatMessageCreate(BaseModel):
    """Create a new chat message."""
    content: str = Field(min_length=1)
    device_id: int | None = None  # optional context device


class AIChatMessageOut(BaseModel):
    """Chat message output."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    device_id: int | None = None
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class AIChatRequest(BaseModel):
    """Request to send a message and get AI response."""
    message: str = Field(min_length=1)
    device_id: int | None = None  # optional: include device context
    conversation_id: int | None = None  # optional: continue existing conversation


class AIChatResponse(BaseModel):
    """AI response with generated reply."""
    message_id: int
    assistant_message_id: int
    response: str
    device_context: dict | None = None


# ------------------------------- AI Settings --------------------------------
class AISettingsUpdate(BaseModel):
    """Update AI/LLM settings."""
    enabled: bool | None = None
    provider: Literal["openai", "anthropic", "ollama"] | None = None
    api_key: str | None = None  # raw key, encrypted before storage
    api_base_url: str | None = None
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=64, le=32768)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    context_window: int | None = Field(default=None, ge=1, le=50)
    system_prompt: str | None = None


class AISettingsOut(BaseModel):
    """AI settings output (API key hidden)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    enabled: bool
    provider: str
    api_base_url: str
    model: str
    max_tokens: int
    temperature: float
    context_window: int
    system_prompt: str
    is_configured: bool  # true if API key is set


# ------------------------------- AI Diagnostics -----------------------------
class AIDiagnosticRequest(BaseModel):
    """Request AI to analyze an error or log."""
    error_message: str = Field(min_length=1)
    device_id: int | None = None
    log_snippet: str | None = None


class AIDiagnosticResponse(BaseModel):
    """AI diagnostic analysis result."""
    analysis: str
    suggested_commands: list[str]
    explanation: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"


# ------------------------------- AI Command Generation ----------------------
class AICommandRequest(BaseModel):
    """Request AI to generate a command from natural language."""
    description: str = Field(min_length=1)
    device_os: str | None = None  # linux, windows, darwin
    safety_check: bool = True  # if true, AI will warn about dangerous operations


class AICommandResponse(BaseModel):
    """Generated command with explanation."""
    command: str
    explanation: str
    warnings: list[str] = []
    alternative_commands: list[str] = []
