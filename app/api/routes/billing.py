"""Billing endpoints (R09). Invoices live in MongoDB.

Quien puede usarlos: `admin` y `contable`. El contable ve solo las facturas de
la EPS a la que pertenece su cuenta; una factura de otra EPS responde 404, como
si no existiera. Medico, especialista, paciente y coordinador reciben 403 (el
rol se comprueba antes de tocar MongoDB). Si MongoDB no responde: 503; el resto
del sistema sigue funcionando.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.billing import service
from app.billing.documents import EstadoPago, Servicio
from app.schemas.billing import (
    ConciliacionRead,
    FacturaAnular,
    FacturaCreate,
    FacturaPagar,
    FacturaRead,
    ResumenRead,
)

router = APIRouter(prefix="/api/v1/facturas", tags=["contabilidad"])


@router.get(
    "",
    response_model=list[FacturaRead],
    summary="Listar facturas (la mas reciente primero)",
)
def list_invoices(
    user: CurrentUser,
    estado_pago: EstadoPago | None = Query(default=None),
    servicio: Servicio | None = Query(default=None),
    paciente_id: uuid.UUID | None = Query(default=None),
    desde: date | None = Query(default=None, description="Dia inicial, inclusive (UTC)"),
    hasta: date | None = Query(default=None, description="Dia final, inclusive (UTC)"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FacturaRead]:
    return [
        FacturaRead.from_document(d)
        for d in service.list_invoices(
            user, estado_pago=estado_pago, servicio=servicio, paciente_id=paciente_id,
            desde=desde, hasta=hasta, limit=limit,
        )
    ]


@router.post(
    "",
    response_model=FacturaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una factura manual (consulta o medicamento)",
)
def create_invoice(db: DbSession, user: CurrentUser, payload: FacturaCreate) -> FacturaRead:
    return FacturaRead.from_document(service.create_manual(db, user, payload))


# Fixed paths before "/{factura_id}", or "resumen" would be read as an id.
@router.get(
    "/resumen",
    response_model=ResumenRead,
    summary="Totales por estado de pago y por servicio",
)
def summary(user: CurrentUser) -> ResumenRead:
    return ResumenRead.model_validate(service.summary(user))


@router.post(
    "/reconciliar",
    response_model=ConciliacionRead,
    summary="Crear las facturas automaticas que faltan (idempotente)",
)
def reconcile(db: DbSession, user: CurrentUser) -> ConciliacionRead:
    """Recorre los reportes de IA aprobados y las estancias de cama terminadas,
    y crea la factura que falte. Es la red de seguridad de los reactores."""

    return ConciliacionRead.model_validate(service.reconcile(db, user))


@router.get("/{factura_id}", response_model=FacturaRead, summary="Consultar una factura")
def get_invoice(user: CurrentUser, factura_id: str) -> FacturaRead:
    return FacturaRead.from_document(service.get(user, factura_id))


@router.post(
    "/{factura_id}/pagar",
    response_model=FacturaRead,
    summary="Registrar el pago (solo desde pendiente)",
)
def pay_invoice(
    db: DbSession, user: CurrentUser, factura_id: str, payload: FacturaPagar | None = None
) -> FacturaRead:
    return FacturaRead.from_document(
        service.pay(db, user, factura_id, nota=payload.nota if payload else None)
    )


@router.post(
    "/{factura_id}/anular",
    response_model=FacturaRead,
    summary="Anular una factura (solo desde pendiente, con motivo)",
)
def void_invoice(
    db: DbSession, user: CurrentUser, factura_id: str, payload: FacturaAnular
) -> FacturaRead:
    return FacturaRead.from_document(
        service.void(db, user, factura_id, motivo=payload.motivo)
    )


@router.get(
    "/{factura_id}/recibo.pdf",
    summary="Recibo en PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def get_receipt(db: DbSession, user: CurrentUser, factura_id: str) -> Response:
    content, _document = service.receipt_pdf(db, user, factura_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="recibo-{factura_id}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )
