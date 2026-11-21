"""Backfill: encrypt the identifiers of the EXISTING patients and fill ``documento_idx``.

    python -m scripts.cifrar_pacientes              # dry run: only counts (default)
    python -m scripts.cifrar_pacientes --apply      # writes, in batches

This rewrites data that already lives in the shared database, so it is NOT part of
any migration and nothing runs it automatically. The procedure (backup first, dry
run, apply, verify, switch the unique constraint) is in docs/ENTREGA_FINAL.md,
decision D1. It needs ``FIELD_ENCRYPTION_KEYS``, ``FIELD_ENCRYPTION_ACTIVE_KID`` and
``BLIND_INDEX_KEY`` in the environment.

Guarantees:

* The dry run (default) reads and counts; it writes nothing.
* It never prints a value, an id or a name: only counts.
* Idempotent: a value that already starts with ``v1.`` is skipped, so a second run
  (or one interrupted halfway) finishes the job without touching what is done.
* Each batch is one transaction. Before it commits, every row of the batch is
  decrypted again and compared with what was there; any mismatch aborts the batch.
* ``updated_at`` is kept: encrypting is not an edit of the patient.
* It refuses to start if two patients would end up with the same (document type,
  normalized document): the unique index could not hold them.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import field_crypto
from app.models.governance import RecordVersion
from app.models.patient import Patient

# (label of the selected column, mapped attribute holding the stored value, AAD field)
FIELDS = (
    ("doc", "_document_number", "documento"),
    ("phone", "_phone", "telefono"),
    ("email", "_email", "correo"),
    ("address", "_address", "direccion"),
)


def _needs_encryption(stored: str | None) -> bool:
    return bool(stored) and not field_crypto.is_encrypted(stored)


def _rows(db: Session, batch_size: int) -> Any:
    """All patients (deleted ones too), in id order, ``batch_size`` at a time."""

    last = None
    while True:
        stmt = (
            select(
                Patient.id,
                Patient.document_type,
                *(getattr(Patient, attr).label(label) for label, attr, _ in FIELDS),
                Patient.documento_idx,
            )
            .order_by(Patient.id)
            .limit(batch_size)
        )
        if last is not None:
            stmt = stmt.where(Patient.id > last)
        batch = db.execute(stmt).all()
        if not batch:
            return
        yield batch
        last = batch[-1].id


def survey(db: Session, batch_size: int = 500) -> dict[str, int]:
    """Counts only. ``collisions``: groups of >1 patient with the same (type, document)."""

    counts = {
        "pacientes": 0,
        "documento_sin_cifrar": 0,
        "telefono_sin_cifrar": 0,
        "correo_sin_cifrar": 0,
        "direccion_sin_cifrar": 0,
        "sin_indice_ciego": 0,
        "colisiones_de_documento": 0,
    }
    seen: Counter[tuple[str, str]] = Counter()
    for batch in _rows(db, batch_size):
        for row in batch:
            counts["pacientes"] += 1
            if _needs_encryption(row.doc):
                counts["documento_sin_cifrar"] += 1
            if _needs_encryption(row.phone):
                counts["telefono_sin_cifrar"] += 1
            if _needs_encryption(row.email):
                counts["correo_sin_cifrar"] += 1
            if _needs_encryption(row.address):
                counts["direccion_sin_cifrar"] += 1
            if row.documento_idx is None:
                counts["sin_indice_ciego"] += 1
            document = field_crypto.reveal(row.doc, row.id, "documento", strict=True)
            if document:
                seen[(row.document_type.value, field_crypto.normalize_document(document))] += 1
    counts["colisiones_de_documento"] = sum(1 for n in seen.values() if n > 1)
    counts["copias_en_claro_en_historial"] = (
        db.scalar(
            select(func.count())
            .select_from(RecordVersion)
            .where(RecordVersion.entity_type == "patients")
        )
        or 0
    )
    return counts


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for name, value in counts.items():
        print(f"  {name:32} {value}")


def _encrypt_batch(db: Session, batch: list[Any]) -> int:
    """Encrypt one batch in the current transaction; return how many rows changed."""

    changed = 0
    for row in batch:
        plain = {
            field: field_crypto.reveal(getattr(row, label), row.id, field, strict=True)
            for label, _, field in FIELDS
        }
        values: dict[Any, Any] = {}
        for label, attr, field in FIELDS:
            stored = getattr(row, label)
            if _needs_encryption(stored):
                sealed = field_crypto.seal(plain[field], row.id, field)
                # Round trip BEFORE writing: what we store must decrypt to what was there.
                if field_crypto.reveal(sealed, row.id, field, strict=True) != plain[field]:
                    raise RuntimeError("la verificacion de ida y vuelta fallo; se aborta el lote")
                values[getattr(Patient, attr)] = sealed
        index = field_crypto.blind_index(plain["documento"])
        if row.documento_idx is None and index is not None:
            values[Patient.documento_idx] = index
        if not values:
            continue
        # Keep updated_at: encrypting a field is not an edit of the patient.
        values[Patient.updated_at] = Patient.updated_at
        db.execute(
            update(Patient)
            .where(Patient.id == row.id)
            .values(values)
            .execution_options(synchronize_session=False)
        )
        changed += 1
    return changed


def _verify_sample(db: Session, size: int) -> int:
    """Re-read ``size`` patients from the database and check they decrypt and are indexed."""

    checked = 0
    for batch in _rows(db, max(size, 1)):
        for row in batch:
            for label, _, field in FIELDS:
                # strict: the verification must raise if a value cannot be decrypted.
                field_crypto.reveal(getattr(row, label), row.id, field, strict=True)
            document = field_crypto.reveal(row.doc, row.id, "documento", strict=True)
            if row.documento_idx != field_crypto.blind_index(document):
                raise RuntimeError("el indice ciego no corresponde al documento")
            checked += 1
        break
    return checked


def main(
    argv: list[str] | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="solo cuenta (por defecto)")
    mode.add_argument("--apply", action="store_true", help="escribe los cambios")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--sample", type=int, default=25, help="filas a releer al final")
    args = parser.parse_args(argv)

    if session_factory is None:
        from app.core.database import SessionLocal

        session_factory = SessionLocal

    with session_factory() as db:
        before = survey(db)
        _print_counts("Estado actual", before)

        if before["colisiones_de_documento"]:
            print(
                "ABORTADO: hay pacientes que compartirian (tipo, documento normalizado); "
                "resuelvalos antes. Nada se modifico."
            )
            return 1
        if not args.apply:
            print("Modo prueba (--dry-run): no se escribio nada. Use --apply para ejecutar.")
            return 0

        if not field_crypto.encryption_enabled() or field_crypto.blind_index("0") is None:
            print(
                "ABORTADO: configure FIELD_ENCRYPTION_KEYS, FIELD_ENCRYPTION_ACTIVE_KID y "
                "BLIND_INDEX_KEY antes de --apply."
            )
            return 2

        changed = 0
        for batch in _rows(db, args.batch_size):
            changed += _encrypt_batch(db, batch)
            db.commit()
        after = survey(db)
        _print_counts("Estado final", after)
        checked = _verify_sample(db, args.sample)
        print(f"Filas modificadas: {changed}. Muestra releida y verificada: {checked}.")
        pending = sum(
            after[k]
            for k in (
                "documento_sin_cifrar",
                "telefono_sin_cifrar",
                "correo_sin_cifrar",
                "direccion_sin_cifrar",
                "sin_indice_ciego",
            )
        )
        if after["pacientes"] != before["pacientes"] or pending:
            print("ADVERTENCIA: el conteo final no es el esperado; revise antes de continuar.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
