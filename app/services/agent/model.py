"""The chat model, through the project's Groq settings.

Same account and model as the RAG (``GROQ_*``). Temperature 0, bounded output,
SDK retries on 429, and a request timeout. Without a key there is no model: the
harness then answers with the extractive guideline search instead of failing.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings


def build_model() -> Any | None:
    settings = get_settings()
    if not settings.has_llm_key:
        return None
    from langchain_groq import ChatGroq

    extra: dict[str, Any] = {}
    if "gpt-oss" in settings.groq_model:
        extra["reasoning_effort"] = "low"
    else:
        extra["reasoning_format"] = "hidden"
    if settings.agent_reasoning_effort:
        # Minimal reasoning (the professor: off or low). The value depends on the model
        # ("none" for qwen3, "low" for gpt-oss); it is set explicitly, never guessed.
        extra["reasoning_effort"] = settings.agent_reasoning_effort
    return ChatGroq(
        model=settings.groq_model,
        api_key=str(settings.groq_api_key).strip(),
        temperature=0,
        max_tokens=settings.agent_max_tokens,
        max_retries=settings.agent_model_retries,
        timeout=settings.groq_timeout_seconds,
        **extra,
    )


def failure_reason(error: BaseException) -> str:
    """Short, patient-free reason for the audit trail and the degraded answer."""

    name = type(error).__name__
    if "RateLimit" in name:
        return "limite_de_uso"
    if "Timeout" in name:
        return "timeout"
    if "Connection" in name or "InternalServer" in name or "Unavailable" in name:
        return "no_disponible"
    return "error_del_modelo"
