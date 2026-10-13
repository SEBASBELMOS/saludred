"""Vocabulary and builder of the ``facturas`` document.

The document deliberately carries identifiers only: no name, no document
number, no contact data and no clinical detail of the patient. The name is
resolved from PostgreSQL when a receipt is rendered. See ``docs/CONTABILIDAD.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SYSTEM_USER = "sistema"


class Servicio(str, Enum):
    ANALISIS_IA = "analisis_ia"
    DIA_CAMA = "dia_cama"
    REMISION = "remision"
    CONSULTA = "consulta"
    MEDICAMENTO = "medicamento"


# Services a person may bill by hand; the others are produced by the system.
MANUAL_SERVICES = (Servicio.CONSULTA, Servicio.MEDICAMENTO)


class EstadoPago(str, Enum):
    PENDIENTE = "pendiente"
    PAGADA = "pagada"
    ANULADA = "anulada"


class Origen(str, Enum):
    AUTOMATICA = "automatica"
    MANUAL = "manual"


# State machine: a payment state only moves out of ``pendiente``.
TRANSITIONS: dict[EstadoPago, tuple[EstadoPago, ...]] = {
    EstadoPago.PENDIENTE: (EstadoPago.PAGADA, EstadoPago.ANULADA),
    EstadoPago.PAGADA: (),
    EstadoPago.ANULADA: (),
}

# Optional links that identify the fact an automatic invoice bills.
LINK_FIELDS = ("reporte_id", "solicitud_cama_id", "cama_id", "remision_id")


def history_entry(
    estado: EstadoPago, usuario: str, nota: str | None, fecha: datetime | None = None
) -> dict[str, Any]:
    return {
        "estado": estado.value,
        "fecha": fecha or datetime.now(timezone.utc),
        "usuario": usuario,
        "nota": nota,
    }


def new_document(
    *,
    paciente_id: uuid.UUID,
    eps_id: uuid.UUID | None,
    ips_id: uuid.UUID | None,
    servicio: Servicio,
    descripcion: str,
    cantidad: int,
    valor_unitario: int,
    origen: Origen,
    creado_por: str,
    links: dict[str, uuid.UUID | None] | None = None,
    nota: str | None = None,
) -> dict[str, Any]:
    """A ``facturas`` document, always starting as ``pendiente``."""

    now = datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "paciente_id": str(paciente_id),
        "eps_id": str(eps_id) if eps_id else None,
        "ips_id": str(ips_id) if ips_id else None,
        "servicio": servicio.value,
        "descripcion": descripcion,
        "cantidad": cantidad,
        "valor_unitario": valor_unitario,
        "valor": cantidad * valor_unitario,
        "fecha": now,
        "estado_pago": EstadoPago.PENDIENTE.value,
        "origen": origen.value,
        "creado_por": creado_por,
        "historial": [history_entry(EstadoPago.PENDIENTE, creado_por, nota or "Factura creada", now)],
    }
    # Absent, not null: the idempotency indexes are partial on field existence.
    for name, value in (links or {}).items():
        if value is not None:
            document[name] = str(value)
    return document
