"""AI assistant service for LLM integration."""
from __future__ import annotations

import json
import httpx
from typing import Literal

from app.config import encrypt, decrypt
from app.db import get_db
from app.models import AISettingsRow, AIChatMessage, Device, User


class AIClientError(Exception):
    """AI client error."""
    pass


class AIService:
    """Service for interacting with LLM providers."""

    def __init__(self, db_session):
        self.db = db_session

    def get_settings(self) -> AISettingsRow | None:
        """Get AI settings from database."""
        return self.db.get(AISettingsRow, 1)

    def is_enabled(self) -> bool:
        """Check if AI feature is enabled and configured."""
        settings = self.get_settings()
        return settings is not None and settings.enabled and settings.api_key_enc

    def get_api_key(self) -> str | None:
        """Get decrypted API key."""
        settings = self.get_settings()
        if not settings or not settings.api_key_enc:
            return None
        try:
            return decrypt(settings.api_key_enc)
        except Exception:
            return None

    def _build_messages(
        self,
        user_message: str,
        device: Device | None = None,
        conversation_history: list[AIChatMessage] | None = None,
        system_prompt: str | None = None,
    ) -> list[dict]:
        """Build message list for LLM API."""
        settings = self.get_settings()
        system_content = system_prompt or (settings.system_prompt if settings else "")

        messages = [{"role": "system", "content": system_content}]

        # Add conversation history
        if conversation_history:
            context_window = settings.context_window if settings else 10
            for msg in conversation_history[-context_window:]:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

        # Add device context if provided
        if device:
            device_context = f"""
Context: The user is managing a server with these details:
- Name: {device.name}
- Host: {device.host}
- OS: {device.os or 'Unknown'}
- Username: {device.username}

Please tailor your responses to this specific system.
"""
            messages.append({"role": "system", "content": device_context})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        return messages

    async def chat(
        self,
        user_message: str,
        user: User,
        device: Device | None = None,
        conversation_history: list[AIChatMessage] | None = None,
    ) -> str:
        """Send message to LLM and get response."""
        settings = self.get_settings()
        if not settings:
            raise AIClientError("AI settings not configured")

        api_key = self.get_api_key()
        if not api_key:
            raise AIClientError("API key not configured")

        messages = self._build_messages(
            user_message=user_message,
            device=device,
            conversation_history=conversation_history,
            system_prompt=settings.system_prompt,
        )

        provider = settings.provider.lower()
        
        if provider == "openai":
            return await self._call_openai(api_key, settings, messages)
        elif provider == "anthropic":
            return await self._call_anthropic(api_key, settings, messages)
        elif provider == "ollama":
            return await self._call_ollama(settings, messages)
        else:
            raise AIClientError(f"Unsupported provider: {provider}")

    async def _call_openai(
        self,
        api_key: str,
        settings: AISettingsRow,
        messages: list[dict],
    ) -> str:
        """Call OpenAI-compatible API."""
        base_url = settings.api_base_url or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": settings.model,
            "messages": messages,
            "max_tokens": settings.max_tokens,
            "temperature": settings.temperature / 10.0,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                raise AIClientError(f"OpenAI API error: {e.response.status_code}") from e
            except Exception as e:
                raise AIClientError(f"OpenAI request failed: {str(e)}") from e

    async def _call_anthropic(
        self,
        api_key: str,
        settings: AISettingsRow,
        messages: list[dict],
    ) -> str:
        """Call Anthropic Claude API."""
        base_url = settings.api_base_url or "https://api.anthropic.com"
        url = f"{base_url.rstrip('/')}/v1/messages"

        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        # Convert OpenAI format to Anthropic format
        system_message = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message += msg["content"] + "\n"
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        payload = {
            "model": settings.model,
            "messages": anthropic_messages,
            "system": system_message.strip(),
            "max_tokens": settings.max_tokens,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["content"][0]["text"]
            except httpx.HTTPStatusError as e:
                raise AIClientError(f"Anthropic API error: {e.response.status_code}") from e
            except Exception as e:
                raise AIClientError(f"Anthropic request failed: {str(e)}") from e

    async def _call_ollama(
        self,
        settings: AISettingsRow,
        messages: list[dict],
    ) -> str:
        """Call local Ollama API."""
        base_url = settings.api_base_url or "http://localhost:11434"
        url = f"{base_url.rstrip('/')}/api/chat"

        payload = {
            "model": settings.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": settings.max_tokens,
                "temperature": settings.temperature / 10.0,
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["message"]["content"]
            except httpx.HTTPStatusError as e:
                raise AIClientError(f"Ollama API error: {e.response.status_code}") from e
            except Exception as e:
                raise AIClientError(f"Ollama request failed: {str(e)}") from e

    def save_message(
        self,
        user_id: int,
        content: str,
        role: Literal["user", "assistant", "system"],
        device_id: int | None = None,
    ) -> AIChatMessage:
        """Save a chat message to database."""
        message = AIChatMessage(
            user_id=user_id,
            content=content,
            role=role,
            device_id=device_id,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_conversation_history(
        self,
        user_id: int,
        device_id: int | None = None,
        limit: int = 20,
    ) -> list[AIChatMessage]:
        """Get recent conversation history for a user/device."""
        query = self.db.query(AIChatMessage).filter(
            AIChatMessage.user_id == user_id
        )
        if device_id:
            query = query.filter(AIChatMessage.device_id == device_id)
        
        return query.order_by(AIChatMessage.created_at.desc()).limit(limit).all()

    def update_settings(
        self,
        enabled: bool | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        context_window: int | None = None,
        system_prompt: str | None = None,
    ) -> AISettingsRow:
        """Update AI settings."""
        settings = self.get_settings()
        
        if not settings:
            settings = AISettingsRow()
            self.db.add(settings)

        if enabled is not None:
            settings.enabled = enabled
        if provider is not None:
            settings.provider = provider
        if api_key is not None:
            if api_key:
                settings.api_key_enc = encrypt(api_key)
            else:
                settings.api_key_enc = ""
        if api_base_url is not None:
            settings.api_base_url = api_base_url
        if model is not None:
            settings.model = model
        if max_tokens is not None:
            settings.max_tokens = max_tokens
        if temperature is not None:
            # Store as int (0-10), divide by 10 when using
            settings.temperature = int(temperature * 10)
        if context_window is not None:
            settings.context_window = context_window
        if system_prompt is not None:
            settings.system_prompt = system_prompt

        self.db.commit()
        self.db.refresh(settings)
        return settings


# Singleton instance helper
def get_ai_service() -> AIService:
    """Get AI service instance with database session."""
    db = next(get_db())
    return AIService(db)
