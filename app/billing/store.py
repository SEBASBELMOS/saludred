"""MongoDB access: the connection and a thin repository over ``facturas``.

Design points:

* The connection is lazy. ``pymongo`` does not connect when the client is built,
  so the API starts even when Mongo is down; the first operation that really
  needs it raises :class:`BillingUnavailableError` (HTTP 503 on billing routes,
  a logged warning in reactors).
* The repository takes a ``Collection``, so tests inject a ``mongomock`` one.
* Indexes are created idempotently, once per collection per process, and again
  after any failure: if Mongo was down at startup they are created on first use.
* Documents leave this module with ``id`` (the ObjectId as a string) and with
  timezone-aware datetimes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import DESCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.billing.documents import EstadoPago, history_entry
from app.core.config import get_settings
from app.services.errors import BillingUnavailableError

logger = logging.getLogger(__name__)

COLLECTION_NAME = "facturas"
MAX_LIST = 500

# Unique and partial: an automatic invoice cannot be created twice for the same
# fact. Manual invoices have none of these fields, so they are never constrained.
UNIQUE_INDEXES: tuple[tuple[str, str], ...] = (
    ("uq_servicio_reporte", "reporte_id"),
    ("uq_servicio_solicitud_cama", "solicitud_cama_id"),
    ("uq_servicio_remision", "remision_id"),
)


@contextmanager
def guard() -> Iterator[None]:
    """Turn any pymongo failure into :class:`BillingUnavailableError`.

    ``DuplicateKeyError`` is a business signal ("already billed"), not an outage,
    so it passes through untouched.
    """

    try:
        yield
    except DuplicateKeyError:
        raise
    except PyMongoError as exc:
        logger.warning("MongoDB failure in billing: %s", exc.__class__.__name__)
        raise BillingUnavailableError("MongoDB no esta disponible") from exc


def parse_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _utc(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def public(document: dict[str, Any]) -> dict[str, Any]:
    """The document as the service layer sees it: ``id`` instead of ``_id``."""

    result = {key: value for key, value in document.items() if key != "_id"}
    result["id"] = str(document["_id"])
    result["fecha"] = _utc(result.get("fecha"))
    for entry in result.get("historial", []):
        entry["fecha"] = _utc(entry.get("fecha"))
    return result


class FacturaRepository:
    def __init__(self, collection: Collection) -> None:
        self.collection = collection

    # -- Indexes ---------------------------------------------------------

    def ensure_indexes(self) -> None:
        """Create every index; running it again changes nothing."""

        with guard():
            for name, field in UNIQUE_INDEXES:
                self.collection.create_index(
                    [("servicio", 1), (field, 1)],
                    name=name,
                    unique=True,
                    partialFilterExpression={field: {"$exists": True}},
                )
            self.collection.create_index(
                [("eps_id", 1), ("fecha", DESCENDING)], name="ix_eps_fecha"
            )
            self.collection.create_index(
                [("paciente_id", 1), ("fecha", DESCENDING)], name="ix_paciente_fecha"
            )
            self.collection.create_index(
                [("estado_pago", 1), ("servicio", 1)], name="ix_estado_servicio"
            )

    # -- Writes ----------------------------------------------------------

    def insert(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert; on a duplicate automatic invoice return the existing one.

        Returns ``(document, created)``. "Already billed" is not an error.
        """

        with guard():
            try:
                self.collection.insert_one(document)
                return public(document), True
            except DuplicateKeyError:
                existing = self._existing(document)
                if existing is None:  # pragma: no cover -- raced with a delete; never hard-deleted
                    raise
                return public(existing), False

    def _existing(self, document: dict[str, Any]) -> dict[str, Any] | None:
        for _name, field in UNIQUE_INDEXES:
            if field in document:
                found = self.collection.find_one(
                    {"servicio": document["servicio"], field: document[field]}
                )
                if found is not None:
                    return found
        return None

    def transition(
        self,
        object_id: ObjectId,
        *,
        to: EstadoPago,
        allowed_from: tuple[EstadoPago, ...],
        usuario: str,
        nota: str | None,
    ) -> dict[str, Any] | None:
        """Atomic state change; ``None`` when the invoice is not in ``allowed_from``.

        The state check is part of the update filter, so two concurrent
        requests cannot both win. Nothing is ever deleted.
        """

        with guard():
            updated = self.collection.find_one_and_update(
                {"_id": object_id, "estado_pago": {"$in": [s.value for s in allowed_from]}},
                {
                    "$set": {"estado_pago": to.value},
                    "$push": {"historial": history_entry(to, usuario, nota)},
                },
                return_document=ReturnDocument.AFTER,
            )
        return public(updated) if updated else None

    # -- Reads -----------------------------------------------------------

    def get(self, object_id: ObjectId) -> dict[str, Any] | None:
        with guard():
            found = self.collection.find_one({"_id": object_id})
        return public(found) if found else None

    def list(
        self,
        *,
        eps_id: str | None = None,
        estado_pago: str | None = None,
        servicio: str | None = None,
        paciente_id: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Newest first. ``hasta`` is exclusive."""

        query: dict[str, Any] = {}
        if eps_id is not None:
            query["eps_id"] = eps_id
        if estado_pago is not None:
            query["estado_pago"] = estado_pago
        if servicio is not None:
            query["servicio"] = servicio
        if paciente_id is not None:
            query["paciente_id"] = paciente_id
        window: dict[str, datetime] = {}
        if desde is not None:
            window["$gte"] = desde
        if hasta is not None:
            window["$lt"] = hasta
        if window:
            query["fecha"] = window
        with guard():
            cursor = (
                self.collection.find(query)
                .sort([("fecha", DESCENDING), ("_id", DESCENDING)])
                .limit(min(limit, MAX_LIST))
            )
            return [public(d) for d in cursor]

    def billed_keys(self, servicio: str, field: str) -> set[str]:
        """Values of ``field`` already billed for ``servicio`` (reconciliation)."""

        with guard():
            cursor = self.collection.find(
                {"servicio": servicio, field: {"$exists": True}}, {field: 1}
            )
            return {d[field] for d in cursor}

    def totals(self, *, eps_id: str | None) -> list[dict[str, Any]]:
        """Count and total value per ``(estado_pago, servicio)``."""

        pipeline: list[dict[str, Any]] = []
        if eps_id is not None:
            pipeline.append({"$match": {"eps_id": eps_id}})
        pipeline.append(
            {
                "$group": {
                    "_id": {"estado": "$estado_pago", "servicio": "$servicio"},
                    "cantidad": {"$sum": 1},
                    "valor": {"$sum": "$valor"},
                }
            }
        )
        with guard():
            return [
                {
                    "estado_pago": row["_id"]["estado"],
                    "servicio": row["_id"]["servicio"],
                    "cantidad": row["cantidad"],
                    "valor": row["valor"],
                }
                for row in self.collection.aggregate(pipeline)
            ]


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_client: MongoClient | None = None
_real: Collection | None = None
_override: Collection | None = None
_indexed: set[int] = set()


def set_collection(collection: Collection | None) -> None:
    """Inject a collection (tests) or go back to the configured one."""

    global _override
    with _lock:
        _override = collection
        _indexed.clear()


def _collection() -> Collection:
    global _client, _real
    if _override is not None:
        return _override
    settings = get_settings()
    if not settings.mongo_url:
        raise BillingUnavailableError("MONGO_URL no esta configurada")
    with _lock:
        if _real is None:
            with guard():  # a malformed URL is a configuration failure, not a crash
                _client = MongoClient(
                    settings.mongo_url,
                    serverSelectionTimeoutMS=settings.mongo_timeout_ms,
                    tz_aware=True,
                )
                _real = _client[settings.mongo_db][COLLECTION_NAME]
        return _real


def get_repository() -> FacturaRepository:
    """The repository, with its indexes in place (created on first use)."""

    collection = _collection()
    repository = FacturaRepository(collection)
    if id(collection) not in _indexed:
        repository.ensure_indexes()
        _indexed.add(id(collection))
    return repository


def init_billing() -> bool:
    """Startup hook: create the indexes, never raise.

    Returns whether Mongo answered. A False leaves the API running; billing
    routes answer 503 until Mongo is back and the first request creates the
    indexes.
    """

    try:
        get_repository()
    except BillingUnavailableError as exc:
        logger.warning("Billing is unavailable at startup (%s); the API continues", exc)
        return False
    except Exception:  # noqa: BLE001 -- a bad MONGO_URL must not stop the API either
        logger.exception("Billing initialization failed; the API continues")
        return False
    logger.info("Billing ready: indexes ensured on %s", COLLECTION_NAME)
    return True
