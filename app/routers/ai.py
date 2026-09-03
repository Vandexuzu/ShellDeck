"""AI assistant router for chat and command generation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai_service import AIService, AIClientError, get_ai_service
from app.db import get_db
from app.models import User, Device
from app.schemas_ai import (
    AIChatRequest,
    AIChatResponse,
    AIChatMessageOut,
    AISettingsUpdate,
    AISettingsOut,
    AICommandRequest,
    AICommandResponse,
    AIDiagnosticRequest,
    AIDiagnosticResponse,
)
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


def get_device_or_404(device_id: int, user: User, db: Session) -> Device:
    """Get device owned by user or raise 404."""
    device = db.query(Device).filter(
        Device.id == device_id,
        Device.owner_id == user.id
    ).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found or access denied"
        )
    return device


@router.get("/settings", response_model=AISettingsOut)
async def get_ai_settings(
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    """Get AI/LLM settings."""
    settings = ai_service.get_settings()
    
    if not settings:
        # Return default settings
        return AISettingsOut(
            id=0,
            enabled=False,
            provider="openai",
            api_base_url="",
            model="gpt-4o-mini",
            max_tokens=1024,
            temperature=0.7,
            context_window=10,
            system_prompt="You are a helpful Linux system administration assistant.",
            is_configured=False,
        )
    
    return AISettingsOut(
        id=settings.id,
        enabled=settings.enabled,
        provider=settings.provider,
        api_base_url=settings.api_base_url,
        model=settings.model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature / 10.0,
        context_window=settings.context_window,
        system_prompt=settings.system_prompt,
        is_configured=bool(settings.api_key_enc),
    )


@router.put("/settings", response_model=AISettingsOut)
async def update_ai_settings(
    settings_update: AISettingsUpdate,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
):
    """Update AI/LLM settings. Admin only."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required to configure AI settings"
        )
    
    settings = ai_service.update_settings(
        enabled=settings_update.enabled,
        provider=settings_update.provider,
        api_key=settings_update.api_key,
        api_base_url=settings_update.api_base_url,
        model=settings_update.model,
        max_tokens=settings_update.max_tokens,
        temperature=settings_update.temperature,
        context_window=settings_update.context_window,
        system_prompt=settings_update.system_prompt,
    )
    
    return AISettingsOut(
        id=settings.id,
        enabled=settings.enabled,
        provider=settings.provider,
        api_base_url=settings.api_base_url,
        model=settings.model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature / 10.0,
        context_window=settings.context_window,
        system_prompt=settings.system_prompt,
        is_configured=bool(settings.api_key_enc),
    )


@router.post("/chat", response_model=AIChatResponse)
async def send_chat_message(
    request: AIChatRequest,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
):
    """Send a message to the AI assistant and get a response."""
    if not ai_service.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is not enabled or configured"
        )
    
    # Get device context if provided
    device = None
    if request.device_id:
        device = get_device_or_404(request.device_id, current_user, db)
    
    # Get conversation history
    history = ai_service.get_conversation_history(
        user_id=current_user.id,
        device_id=request.device_id,
        limit=20,
    )
    # Reverse to get chronological order
    history = list(reversed(history))
    
    try:
        # Save user message
        user_msg = ai_service.save_message(
            user_id=current_user.id,
            content=request.message,
            role="user",
            device_id=request.device_id,
        )
        
        # Get AI response
        assistant_response = await ai_service.chat(
            user_message=request.message,
            user=current_user,
            device=device,
            conversation_history=history,
        )
        
        # Save assistant response
        assistant_msg = ai_service.save_message(
            user_id=current_user.id,
            content=assistant_response,
            role="assistant",
            device_id=request.device_id,
        )
        
        # Build device context for response
        device_context = None
        if device:
            device_context = {
                "id": device.id,
                "name": device.name,
                "host": device.host,
                "os": device.os,
            }
        
        return AIChatResponse(
            message_id=user_msg.id,
            assistant_message_id=assistant_msg.id,
            response=assistant_response,
            device_context=device_context,
        )
        
    except AIClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e


@router.get("/chat/history", response_model=list[AIChatMessageOut])
async def get_chat_history(
    device_id: int | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    """Get chat history for the current user."""
    messages = ai_service.get_conversation_history(
        user_id=current_user.id,
        device_id=device_id,
        limit=limit,
    )
    # Return in chronological order
    return list(reversed(messages))


@router.post("/command/generate", response_model=AICommandResponse)
async def generate_command(
    request: AICommandRequest,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    """Generate a shell command from natural language description."""
    if not ai_service.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is not enabled or configured"
        )
    
    # Build prompt for command generation
    os_context = f"Target OS: {request.device_os or 'Linux'}."
    safety_context = "IMPORTANT: If the command could be dangerous (rm, dd, chmod, etc.), include clear warnings." if request.safety_check else ""
    
    prompt = f"""{os_context} {safety_context}

Generate a shell command for this task: {request.description}

Respond in JSON format with these fields:
- command: The shell command
- explanation: Brief explanation of what it does
- warnings: Array of any safety warnings (empty if safe)
- alternative_commands: Array of alternative approaches (optional)

Only return valid JSON, no markdown or extra text."""

    try:
        response_text = await ai_service.chat(
            user_message=prompt,
            user=current_user,
            device=None,
            conversation_history=[],
        )
        
        # Parse JSON response
        import json
        try:
            # Try to extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                data = json.loads(json_str)
            else:
                data = json.loads(response_text)
            
            return AICommandResponse(
                command=data.get("command", ""),
                explanation=data.get("explanation", ""),
                warnings=data.get("warnings", []),
                alternative_commands=data.get("alternative_commands", []),
            )
        except json.JSONDecodeError:
            # Fallback: treat entire response as command
            return AICommandResponse(
                command=response_text.strip(),
                explanation="Generated from natural language",
                warnings=["Could not parse structured response"],
            )
            
    except AIClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e


@router.post("/diagnostic/analyze", response_model=AIDiagnosticResponse)
async def analyze_diagnostic(
    request: AIDiagnosticRequest,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
    db: Session = Depends(get_db),
):
    """Analyze an error message or log snippet and provide diagnostic suggestions."""
    if not ai_service.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is not enabled or configured"
        )
    
    # Get device context if provided
    device = None
    if request.device_id:
        device = get_device_or_404(request.device_id, current_user, db)
    
    # Build diagnostic prompt
    prompt_parts = [
        "You are a Linux system administration expert. Analyze this error/log and provide diagnostic help.",
        "",
        f"Error Message:\n{request.error_message}",
    ]
    
    if request.log_snippet:
        prompt_parts.append(f"\nLog Snippet:\n{request.log_snippet}")
    
    if device:
        prompt_parts.append(f"\nSystem Context:\n- OS: {device.os or 'Unknown'}\n- Hostname: {device.name}\n- Host: {device.host}")
    
    prompt_parts.extend([
        "",
        "Provide your analysis in JSON format with these fields:",
        "- analysis: Brief explanation of what the error means",
        "- suggested_commands: Array of shell commands to investigate or fix the issue",
        "- explanation: Detailed explanation of the root cause and solution approach",
        "- severity: One of 'low', 'medium', 'high', 'critical' based on potential impact",
        "",
        "Only return valid JSON, no markdown or extra text."
    ])
    
    prompt = "\n".join(prompt_parts)
    
    try:
        response_text = await ai_service.chat(
            user_message=prompt,
            user=current_user,
            device=device,
            conversation_history=[],
        )
        
        # Parse JSON response
        import json
        try:
            # Try to extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                data = json.loads(json_str)
            else:
                data = json.loads(response_text)
            
            return AIDiagnosticResponse(
                analysis=data.get("analysis", "Unable to analyze error"),
                suggested_commands=data.get("suggested_commands", []),
                explanation=data.get("explanation", ""),
                severity=data.get("severity", "medium"),
            )
        except json.JSONDecodeError:
            # Fallback: return basic response
            return AIDiagnosticResponse(
                analysis=response_text[:500],
                suggested_commands=[],
                explanation="Could not parse structured response",
                severity="medium",
            )
            
    except AIClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e


@router.post("/command/explain")
async def explain_command(
    request: AICommandRequest,
    current_user: User = Depends(get_current_user),
    ai_service: AIService = Depends(get_ai_service),
):
    """Explain what a given command does."""
    if not ai_service.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is not enabled or configured"
        )
    
    prompt = f"""You are a Linux security expert. Explain this shell command in detail:

Command: {request.description}

Provide your explanation in JSON format:
- explanation: Clear breakdown of what each part does
- risks: Array of potential security risks or dangers
- safer_alternatives: Array of safer approaches if applicable

Only return valid JSON, no markdown."""

    try:
        response_text = await ai_service.chat(
            user_message=prompt,
            user=current_user,
            device=None,
            conversation_history=[],
        )
        
        import json
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                data = json.loads(json_str)
            else:
                data = json.loads(response_text)
            
            return {
                "explanation": data.get("explanation", ""),
                "risks": data.get("risks", []),
                "safer_alternatives": data.get("safer_alternatives", []),
            }
        except json.JSONDecodeError:
            return {"explanation": response_text, "risks": [], "safer_alternatives": []}
            
    except AIClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e
