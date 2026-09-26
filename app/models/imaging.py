"""Medical imaging studies.

La base de datos guarda los *metadatos* del estudio, no los pixeles. Las
imagenes viven en un PACS (Orthanc), que es un almacen especializado: sabe
hablar DICOM, indexa por paciente y estudio, y no obliga a meter archivos de
decenas de megabytes dentro de PostgreSQL.

Esta tabla es el puente entre los dos mundos: relaciona un estudio con el
paciente y el encuentro de nuestro modelo, y guarda el identificador con el
que el PACS lo reconoce.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import ImagingModality, ImagingStudyStatus, enum_column

if TYPE_CHECKING:
    from app.models.clinical import Encounter
    from app.models.organization import Organization
    from app.models.patient import Patient


class ImagingStudy(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """An imaging study performed during an encounter."""

    __tablename__ = "imaging_studies"
    __table_args__ = (
        # El identificador DICOM es unico en el mundo por definicion del
        # estandar. Declararlo unico aqui impide registrar dos veces el mismo
        # estudio, que es lo que pasa cuando se reintenta una carga.
        UniqueConstraint("study_instance_uid", name="uq_imaging_studies_study_instance_uid"),
        UniqueConstraint("accession_number", name="uq_imaging_studies_accession_number"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    # Identificadores del estandar DICOM.
    study_instance_uid: Mapped[str] = mapped_column(String(120), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(40), nullable=False)

    modality: Mapped[ImagingModality] = mapped_column(
        enum_column(ImagingModality, "imaging_modality"), nullable=False, index=True
    )
    status: Mapped[ImagingStudyStatus] = mapped_column(
        enum_column(ImagingStudyStatus, "imaging_study_status"),
        nullable=False, default=ImagingStudyStatus.AVAILABLE,
    )
    body_site: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    series_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    instance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Identificador del estudio dentro del PACS. Nulo mientras los metadatos
    # existen pero las imagenes todavia no se cargaron: registrar el estudio y
    # subir los pixeles son dos momentos distintos.
    pacs_study_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter | None"] = relationship()
    organization: Mapped["Organization"] = relationship()

    def __repr__(self) -> str:
        return f"<ImagingStudy {self.modality} {self.accession_number}>"
