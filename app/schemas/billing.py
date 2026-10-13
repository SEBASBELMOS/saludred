"""Billing (R09). Field names follow the public Spanish contract."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.billing.documents import EstadoPago, Servicio


class HistorialEntry(BaseModel):
    estado: EstadoPago
    fecha: datetime
    usuario: str
    nota: str | None = None


class FacturaRead(BaseModel):
    id: str
    paciente_id: uuid.UUID
    eps_id: uuid.UUID | None = None
    ips_id: uuid.UUID | None = None
    servicio: Servicio
    descripcion: str
    cantidad: int
    valor_unitario: int
    valor: int
    fecha: datetime
    estado_pago: EstadoPago
    origen: str
    creado_por: str
    reporte_id: uuid.UUID | None = None
    solicitud_cama_id: uuid.UUID | None = None
    cama_id: uuid.UUID | None = None
    remision_id: uuid.UUID | None = None
    historial: list[HistorialEntry] = []

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "FacturaRead":
        return cls.model_validate(document)


class FacturaCreate(BaseModel):
    """Manual invoice: only the services a person bills by hand."""

    paciente_id: uuid.UUID
    servicio: Literal["consulta", "medicamento"]
    descripcion: str = Field(min_length=3, max_length=300)
    cantidad: int = Field(default=1, ge=1, le=999)
    # Omitted: a consulta uses the tariff. A medicamento must state its price.
    valor_unitario: int | None = Field(default=None, ge=1, le=100_000_000)
    ips_id: uuid.UUID | None = None


class FacturaPagar(BaseModel):
    nota: str | None = Field(default=None, max_length=300)


class FacturaAnular(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


class TotalRead(BaseModel):
    cantidad: int
    valor: int


class ResumenRead(BaseModel):
    """Totals for the accountant screen.

    ``por_estado`` counts every invoice; ``por_servicio`` and ``total_facturado``
    leave out the voided ones, which are not owed.
    """

    total_facturado: TotalRead
    por_estado: dict[str, TotalRead]
    por_servicio: dict[str, TotalRead]


class ConciliacionGrupo(BaseModel):
    revisados: int
    creadas: int
    ya_facturados: int


class ConciliacionRead(BaseModel):
    analisis_ia: ConciliacionGrupo
    dia_cama: ConciliacionGrupo
    remision: ConciliacionGrupo = ConciliacionGrupo(revisados=0, creadas=0, ya_facturados=0)
    total_creadas: int
