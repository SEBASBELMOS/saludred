"""Patient demographics."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ColumnElement,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    event,
    or_,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import field_crypto
from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import (
    AdministrativeGender,
    ClinicalProfile,
    DocumentType,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.clinical import Encounter, Observation
    from app.models.identity import User
    from app.models.organization import Organization


class Patient(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A person affiliated to the EPS.

    The ``(document_type, document_number)`` pair is the business identifier that
    is carried over to ``Patient.identifier`` in FHIR. Reusing the real-world key
    instead of the internal UUID is what makes the synchronization idempotent:
    the FHIR server can resolve "this same person" on its own.
    """

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("document_type", "document_number", name="uq_patients_document_type_document_number"),
        # Uniqueness of the (document type, blind index) pair. Partial: rows that have
        # no index yet (legacy plain text, before the backfill) are not constrained.
        Index(
            "uq_patients_document_type_documento_idx",
            "document_type",
            "documento_idx",
            unique=True,
            postgresql_where=text("documento_idx IS NOT NULL"),
            sqlite_where=text("documento_idx IS NOT NULL"),
        ),
    )

    document_type: Mapped[DocumentType] = mapped_column(
        enum_column(DocumentType, "document_type"), nullable=False
    )
    # The four direct identifiers are stored in the ``_``-prefixed attributes: plain
    # text while no key is configured, ``v1.<kid>.<nonce>.<ciphertext>`` once it is
    # (app/core/field_crypto.py). The rest of the code keeps using ``document_number``,
    # ``phone``, ``email`` and ``address``, which are properties that encrypt on write
    # and decrypt on read. The column names in the database do not change.
    _document_number: Mapped[str] = mapped_column(
        "document_number", Text, nullable=False, index=True
    )
    # HMAC-SHA256 of the normalized document (blind index): the only way to search
    # by document once it is encrypted. NULL for rows that have not been backfilled.
    documento_idx: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Federated id (HMAC-SHA256 of the normalized type + number with FEDERATED_SECRET,
    # first 32 hex): what travels in ``subject.identifier`` to a partner instead of the
    # document. Filled on insert/update while FEDERATED_SECRET is set; NULL for the rows
    # not backfilled yet (the receiver then resolves them by computing, see
    # app/services/interop/federado.py).
    id_federado: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[AdministrativeGender] = mapped_column(
        enum_column(AdministrativeGender, "administrative_gender"), nullable=False
    )
    _phone: Mapped[str | None] = mapped_column("phone", Text, nullable=True)
    _email: Mapped[str | None] = mapped_column("email", Text, nullable=True)
    _address: Mapped[str | None] = mapped_column("address", Text, nullable=True)

    # Perfil clinico de referencia.
    #
    # En los datos sinteticos es la etiqueta con la que el generador produce
    # signos vitales coherentes, y sirve de verdad conocida para evaluar si un
    # agrupamiento automatico descubre las mismas cohortes. En un sistema real
    # equivaldria a una categoria de riesgo asignada por el equipo clinico.
    clinical_profile: Mapped[ClinicalProfile | None] = mapped_column(
        enum_column(ClinicalProfile, "clinical_profile"), nullable=True, index=True
    )

    eps_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Clinician who answers for the patient. Recipient of the reactive flows
    # (R21 and Epic 7). Nullable on purpose: when unset, the recipients fall
    # back to the clinicians of the IPS of the latest encounter, then admins.
    # use_alter for the same reason as the authorship columns: users already
    # points back at patients.
    responsible_clinician_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )

    eps_organization: Mapped["Organization"] = relationship(
        back_populates="affiliated_patients", foreign_keys=[eps_organization_id]
    )
    responsible_clinician: Mapped["User | None"] = relationship(
        foreign_keys=[responsible_clinician_id], lazy="joined"
    )
    encounters: Mapped[list["Encounter"]] = relationship(
        back_populates="patient", foreign_keys="Encounter.patient_id"
    )
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="patient", foreign_keys="Observation.patient_id"
    )
    user_account: Mapped["User | None"] = relationship(
        back_populates="patient", foreign_keys="User.patient_id", uselist=False
    )

    def __init__(self, **kwargs: Any) -> None:
        # The id is fixed BEFORE any field is encrypted: the AAD of every ciphertext
        # contains it, and a column default only applies at INSERT time.
        patient_id = kwargs.pop("id", None) or uuid.uuid4()
        super().__init__(id=patient_id, **kwargs)

    # -- Encrypted identifiers ------------------------------------------------

    @hybrid_property
    def document_number(self) -> str:
        return field_crypto.reveal(self._document_number, self.id, "documento")  # type: ignore[return-value]

    @document_number.inplace.setter
    def _set_document_number(self, value: str) -> None:
        self._document_number = field_crypto.seal(value, self.id, "documento")  # type: ignore[assignment]
        self.documento_idx = field_crypto.blind_index(value)

    @document_number.inplace.expression
    @classmethod
    def _document_number_expression(cls) -> Any:
        # In SQL this is the stored column: only meaningful while documents are plain
        # text. Searches by document go through ``document_matches``.
        return cls._document_number

    @hybrid_property
    def phone(self) -> str | None:
        return field_crypto.reveal(self._phone, self.id, "telefono")

    @phone.inplace.setter
    def _set_phone(self, value: str | None) -> None:
        self._phone = field_crypto.seal(value, self.id, "telefono")

    @phone.inplace.expression
    @classmethod
    def _phone_expression(cls) -> Any:
        return cls._phone

    @hybrid_property
    def email(self) -> str | None:
        return field_crypto.reveal(self._email, self.id, "correo")

    @email.inplace.setter
    def _set_email(self, value: str | None) -> None:
        self._email = field_crypto.seal(value, self.id, "correo")

    @email.inplace.expression
    @classmethod
    def _email_expression(cls) -> Any:
        return cls._email

    @hybrid_property
    def address(self) -> str | None:
        return field_crypto.reveal(self._address, self.id, "direccion")

    @address.inplace.setter
    def _set_address(self, value: str | None) -> None:
        self._address = field_crypto.seal(value, self.id, "direccion")

    @address.inplace.expression
    @classmethod
    def _address_expression(cls) -> Any:
        return cls._address

    @classmethod
    def document_matches(cls, number: str) -> ColumnElement[bool]:
        """SQL condition: this document number, however it is typed or stored.

        * Blind index configured: rows with an index match by it (so ``1144-123-456``
          finds ``1.144.123.456``), and rows WITHOUT an index (legacy plain text, not
          yet backfilled) match by exact equality on the stored value.
        * No blind index key: exact equality, as the application always did.
        """

        index = field_crypto.blind_index(number)
        legacy = cls._document_number == number
        if index is None:
            return legacy
        return or_(cls.documento_idx == index, and_(cls.documento_idx.is_(None), legacy))

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def responsible_clinician_name(self) -> str | None:
        clinician = self.responsible_clinician
        return clinician.full_name if clinician is not None else None

    @property
    def business_identifier(self) -> str:
        """Value used as Patient.identifier when synchronizing to FHIR."""
        return f"{self.document_type.value}-{self.document_number}"

    def __repr__(self) -> str:
        return f"<Patient {self.business_identifier}>"


@event.listens_for(Patient, "before_insert")
@event.listens_for(Patient, "before_update")
def _fill_federated_id(_mapper: Any, _connection: Any, target: Patient) -> None:
    """Keep ``id_federado`` in step with the document while FEDERATED_SECRET is set."""

    # Imported here: the interop package reads the settings and the models are loaded first.
    from app.services.interop import federado

    value = federado.for_patient(target)
    if value is not None and target.id_federado != value:
        target.id_federado = value
