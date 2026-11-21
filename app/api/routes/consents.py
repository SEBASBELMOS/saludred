"""Consents (R32): a medico or the admin manages the consent of a patient.

Authorization, in short:

- register / list / revoke: ``medico`` and ``admin`` only, via ``authz.require_role``.
  Every other role receives 403 before the service layer runs.
- A consent binds exactly one destination: an in-network organization or a
  partner interop client. Both/neither is a 422; an overlapping live window is a
  409; unknown consent/destination/patient is a 404.
- The public ``estado`` is derived (``activa`` / ``revocada`` / ``expirada`` / ``programada``),
  never stored.

There is no domain event for consent changes: the audit trail (CONSENT_CREATED /
CONSENT_REVOKED) is the record (see docs/EVENTOS.md note added at E2 close).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import RoleCode
from app.schemas.consent import ConsentCreate, ConsentRead
from app.services import consents

router = APIRouter(prefix="/api/v1/consents", tags=["consentimientos"])

ALLOWED_ROLES = (RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)


@router.post(
    "",
    response_model=ConsentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar el consentimiento de un paciente para compartir datos con un destino",
)
def create_consent(db: DbSession, user: CurrentUser, payload: ConsentCreate) -> ConsentRead:
    authz.require_role(user, *ALLOWED_ROLES)
    consent = consents.register_consent(
        db,
        actor=user,
        patient_id=payload.paciente_id,
        organization_id=payload.organizacion_id,
        interop_client_id=payload.cliente_interop_id,
        valid_from=payload.vigente_desde,
        valid_until=payload.vigente_hasta,
    )
    return consents.to_read(consent)


@router.get(
    "",
    response_model=list[ConsentRead],
    summary="Consentimientos registrados, opcionalmente de un solo paciente",
)
def list_consents(
    db: DbSession,
    user: CurrentUser,
    paciente_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
) -> list[ConsentRead]:
    authz.require_role(user, *ALLOWED_ROLES)
    return [
        consents.to_read(consent)
        for consent in consents.list_consents(db, user, paciente_id=paciente_id, limit=limit)
    ]


@router.delete(
    "/{consent_id}",
    response_model=ConsentRead,
    summary="Revocar un consentimiento (queda registrado en la auditoria)",
)
def revoke_consent(db: DbSession, user: CurrentUser, consent_id: uuid.UUID) -> ConsentRead:
    authz.require_role(user, *ALLOWED_ROLES)
    return consents.to_read(consents.revoke_consent(db, actor=user, consent_id=consent_id))
