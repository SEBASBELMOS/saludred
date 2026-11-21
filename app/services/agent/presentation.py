"""What the clinician reads: no tool trace, no internal ids, no status codes.

Tools answer the model with coded strings (``SIN_EVIDENCIA: ...``) and the model
may repeat them, a tool name or an id. ``clean_answer`` is the last filter before
the text leaves the harness. The detail goes to the logs and the audit, not here.
"""

from __future__ import annotations

import re

# Status code -> the phrase a person reads.
STATUS_TEXT: dict[str, str] = {
    "NO_AUTORIZADO": "No autorizado",
    "SIN_EVIDENCIA": "Sin evidencia en las guías",
    "SIN_PACIENTE": "No hay paciente abierto",
    "NO_ENCONTRADO": "No encontrado",
    "DATO_INVALIDO": "Dato no válido",
    "ERROR_REMISION": "No se pudo crear la remisión",
    "REMISION_CREADA": "Remisión creada",
    "SIN_MEMORIA": "Sin memoria de largo plazo",
    "SKILL_NO_EXISTE": "La skill pedida no existe",
    "SIN_ESPECIALISTAS": "No hay especialistas activos",
    "PREFERENCIA_GUARDADA": "Preferencia guardada",
    "PREFERENCIAS_LLENAS": "Ya hay demasiadas preferencias guardadas",
    "PREFERENCIA_RECHAZADA": "La preferencia no es una pista de estilo válida",
}

# What the clinician reads when the guidelines were searched and nothing supports the
# answer (R23): said plainly, never improvised by the model.
NO_SUPPORT_TEXT = (
    "No encontré soporte en las guías ni en la historia aprobada para responder esa pregunta, "
    "y no voy a inventar una respuesta. Reformule la pregunta con términos clínicos más "
    "específicos o consulte la guía directamente."
)
TOOL_TEXT: dict[str, str] = {
    "consultar_paciente": "consulta del paciente",
    "buscar_guias": "búsqueda en las guías",
    "listar_especialistas": "lista de especialistas",
    "proponer_remision": "propuesta de remisión",
    "guardar_preferencia": "preferencia",
    "cargar_skill": "procedimiento",
}

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ID_FIELD = re.compile(r"\s*\b\w*id=" + _UUID)
_BARE_UUID = re.compile(r"\s*\(?\b" + _UUID + r"\b\)?")
_STATUS = re.compile(r"\b(" + "|".join(STATUS_TEXT) + r")\b:?")
_TOOL = re.compile(r"`?\b(" + "|".join(TOOL_TEXT) + r")\b`?")


def clean_answer(text: str) -> str:
    text = _ID_FIELD.sub("", text)
    text = _BARE_UUID.sub("", text)
    text = _STATUS.sub(lambda m: STATUS_TEXT[m.group(1)] + ":" if m.group(0).endswith(":") else STATUS_TEXT[m.group(1)], text)
    text = _TOOL.sub(lambda m: TOOL_TEXT[m.group(1)], text)
    return text.strip()
