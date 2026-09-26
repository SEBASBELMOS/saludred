"""CRUD operations for imaging studies.

The database holds the metadata; the pixels live in the PACS. Keeping the two
apart is deliberate: PostgreSQL is a poor place for multi-megabyte binaries,
and a PACS already knows how to index by patient, study and series.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinical import Encounter
from app.models.enums import ImagingModality, ImagingStudyStatus
from app.models.identity import User
from app.models.imaging import ImagingStudy
from app.schemas.imaging import ImagingStudyCreate, ImagingStudyUpdate
from app.services import soft_ops
from app.services.errors import ConflictError, NotFoundError, commit


def list_studies(
    db: Session,
    *,
    page: int,
    page_size: int,
    patient_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
    modality: ImagingModality | None = None,
    organization_id: uuid.UUID | None = None,
    include_deleted: bool = False,
) -> tuple[list[ImagingStudy], int]:
    stmt = select(ImagingStudy)
    if not include_deleted:
        stmt = stmt.where(ImagingStudy.deleted_at.is_(None))
    if patient_id is not None:
        stmt = stmt.where(ImagingStudy.patient_id == patient_id)
    if encounter_id is not None:
        stmt = stmt.where(ImagingStudy.encounter_id == encounter_id)
    if modality is not None:
        stmt = stmt.where(ImagingStudy.modality == modality)
    if organization_id is not None:
        # The institutional filter goes into the query, never into a list
        # comprehension afterwards: a row another IPS owns must not travel
        # to this process at all.
        stmt = stmt.where(ImagingStudy.organization_id == organization_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(ImagingStudy.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def get_study(db: Session, study_id: uuid.UUID) -> ImagingStudy:
    study = db.scalar(
        select(ImagingStudy).where(
            ImagingStudy.id == study_id, ImagingStudy.deleted_at.is_(None)
        )
    )
    if study is None:
        raise NotFoundError("Estudio de imagen no encontrado")
    return study


def create_study(
    db: Session, payload: ImagingStudyCreate, *, actor: User
) -> ImagingStudy:
    """Register a study.

    The uniqueness of the DICOM identifiers is checked before inserting so the
    caller gets a 409 that names the problem, instead of the raw constraint
    violation. The constraint stays in the database regardless: it is what
    makes a retried upload idempotent rather than duplicated.
    """

    for column, value, label in (
        (ImagingStudy.study_instance_uid, payload.study_instance_uid, "study_instance_uid"),
        (ImagingStudy.accession_number, payload.accession_number, "accession_number"),
    ):
        existing = db.scalar(select(ImagingStudy.id).where(column == value))
        if existing is not None:
            raise ConflictError(f"Ya existe un estudio con ese {label}")

    if payload.encounter_id is not None:
        encounter = db.scalar(
            select(Encounter).where(
                Encounter.id == payload.encounter_id,
                Encounter.deleted_at.is_(None),
            )
        )
        if encounter is None:
            raise NotFoundError("Encuentro no encontrado")
        if encounter.patient_id != payload.patient_id:
            # A study attached to the wrong encounter would show up in another
            # patient's record. This is the check that prevents it.
            raise ConflictError(
                "El encuentro pertenece a otro paciente"
            )
        if encounter.organization_id != payload.organization_id:
            raise ConflictError(
                "El encuentro pertenece a otra institucion"
            )

    study = ImagingStudy(
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        organization_id=payload.organization_id,
        study_instance_uid=payload.study_instance_uid,
        accession_number=payload.accession_number,
        modality=payload.modality,
        status=payload.status,
        body_site=payload.body_site,
        description=payload.description,
        series_count=payload.series_count,
        instance_count=payload.instance_count,
        started_at=payload.started_at,
        created_by=actor.id,
    )
    db.add(study)
    db.flush()
    soft_ops.audit_create(db, study, actor=actor)
    commit(db)
    db.refresh(study)
    return study


def update_study(
    db: Session, study: ImagingStudy, payload: ImagingStudyUpdate, *, actor: User
) -> ImagingStudy:
    """Apply a partial update, keeping the previous version.

    The identifiers are not updatable on purpose: a DICOM UID names a study
    for the rest of its life. Correcting one means the study was registered
    wrong, and that is a delete plus a new registration -- with both steps
    visible in the trail -- not a quiet overwrite.
    """

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return study

    soft_ops.snapshot_before_update(
        db, study, actor=actor, changed_fields=sorted(changes)
    )
    for field, value in changes.items():
        setattr(study, field, value)
    study.updated_by = actor.id
    commit(db)
    db.refresh(study)
    return study


def soft_delete_study(db: Session, study: ImagingStudy, *, actor: User) -> None:
    soft_ops.soft_delete(db, study, actor=actor)


def list_for_patient(db: Session, patient_id: uuid.UUID) -> list[ImagingStudy]:
    return list(
        db.scalars(
            select(ImagingStudy)
            .where(
                ImagingStudy.patient_id == patient_id,
                ImagingStudy.deleted_at.is_(None),
            )
            .order_by(ImagingStudy.started_at.desc())
        )
    )


def attach_pacs_study(
    db: Session, study: ImagingStudy, pacs_study_id: str, *, actor: User
) -> ImagingStudy:
    """Record that the pixels of this study now live in the PACS.

    The previous version is kept, as for any other edit: the moment a study
    went from "registered" to "available" is part of its history.
    """

    changes: dict[str, object] = {}
    if study.pacs_study_id != pacs_study_id:
        changes["pacs_study_id"] = pacs_study_id
    if study.status != ImagingStudyStatus.AVAILABLE:
        changes["status"] = ImagingStudyStatus.AVAILABLE
    if not changes:
        return study
    soft_ops.snapshot_before_update(db, study, actor=actor, changed_fields=sorted(changes))
    for field, value in changes.items():
        setattr(study, field, value)
    study.updated_by = actor.id
    commit(db)
    db.refresh(study)
    return study


def find_by_pacs_study(db: Session, pacs_study_id: str) -> ImagingStudy:
    study = db.scalar(
        select(ImagingStudy).where(
            ImagingStudy.pacs_study_id == pacs_study_id,
            ImagingStudy.deleted_at.is_(None),
        )
    )
    if study is None:
        raise NotFoundError("Imagen no encontrada")
    return study
