"""Clinical agent (docs/AGENTE.md). Field names follow the public Spanish contract."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONVERSATION_PATTERN = r"^[\w-]{1,40}$"


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mensaje: str = Field(min_length=2, max_length=1000)
    conversacion_id: str = Field(default="principal", pattern=CONVERSATION_PATTERN)
    paciente_id: uuid.UUID | None = None

    @field_validator("mensaje")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("must not be blank")
        return value


class AgentDecision(BaseModel):
    """``cancel`` discards the pending action without running it (and frees the thread)."""

    model_config = ConfigDict(extra="forbid")

    conversacion_id: str = Field(pattern=CONVERSATION_PATTERN)
    decision: Literal["approve", "reject", "cancel"]
    motivo: str | None = Field(default=None, max_length=300)


class AgentStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversacion_id: str = Field(default="principal", pattern=CONVERSATION_PATTERN)
    paciente_id: uuid.UUID | None = None


class AgentSource(BaseModel):
    """A passage the answer rests on: the document and the fragment (R23)."""

    n: int
    documento: str
    titulo: str
    fragmento: str
    chunk_id: str
    url: str | None = None


class AgentReply(BaseModel):
    """``estado``: respondido, pendiente_aprobacion, degradado, tiempo_agotado,
    decision_aplicada, ocupado, inicio. ``conversacion`` is the ``conversacion_id`` sent.

    ``fuentes`` lists the documents and fragments the answer rests on. It is empty only
    when there was no support (the answer then says so) or the answer did not need the
    guidelines (a fixed reply, a pending action).

    ``acciones`` lists what waits for the clinician (``accion``, plus the specialist or
    the text). ``ruta`` (router branch) and ``motivo`` (technical reason) only travel
    in test mode (``AGENT_DEBUG``)."""

    estado: str
    conversacion: str
    respuesta: str | None = None
    fuentes: list[AgentSource] | None = None
    mensajes: list[str] | None = None
    atajos: list[str] | None = None
    acciones: list[dict[str, Any]] | None = None
    detalle: str | None = None
    motivo: str | None = None
    ruta: str | None = None
