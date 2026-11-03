"""Clinical RAG questions and the clinician's sign-off on their answers.

1. ``ask`` retrieves evidence, builds a pseudonymized prompt and answers. A question
   about a patient leaves a ``pendiente`` suggestion with the exact prompt that
   was sent. An abstention, or a question with no patient, stores nothing.
2. ``approve`` / ``reject`` are the clinician's signature, only from ``pendiente``
   (a second review is a 409). Reject keeps the suggestion as evidence.
3. Only an APPROVED suggestion enters the clinical record, and it is then indexed as a
   ``historia`` chunk of that patient: the next question about them can cite it
   ("retroalimentacion"). Pending and rejected suggestions are never searchable.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.core import authz
from app.core.config import get_settings
from app.models.clinical import Encounter
from app.models.enums import AuditAction, RagAnswerMode, RagBase, RagSuggestionStatus
from app.models.identity import User
from app.models.patient import Patient
from app.models.rag import EMBEDDING_DIM, RagChunk, RagSuggestion
from app.schemas.rag import RagSource, RagSuggestionDetail, RagSuggestionRead
from app.services import patient_access, pseudonymization, trail
from app.services import patients as patients_service
from app.services.ai_reports import MIN_REVIEW_NOTE
from app.services.errors import ConflictError, InvalidValueError, NotFoundError, commit
from app.services.rag import answer as rag_answer
from app.services.rag import privacy, store
from app.services.rag.prompt import (
    ABSTENTION_TEXT,
    PATIENT_CONTEXT_LABEL,
    PromptSource,
)
from app.services.rag.retrieval import embed_query, retrieve
from app.services.rag_context import PatientContext, build_patient_context

ENTITY = "rag_suggestions"
MAX_LIST = 100
HISTORY_DOC_TYPE = "sugerencia_aprobada"
_CITATION = re.compile(r"\s*\[\d+\]")


@dataclass(frozen=True)
class AskResult:
    answer: str
    mode: RagAnswerMode
    sources: list[dict[str, Any]]
    suggestion_id: uuid.UUID | None
    retrieval_degraded: bool
    degraded_reason: str | None = None


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


def _context_bm25(db: Session, question: str, context: PatientContext | None) -> float:
    """Lexical match between the question and the patient's summary lines."""

    if context is None or context.is_empty:
        return 0.0
    index = store.get_index(db)
    return max((index.bm25.score_text(question, line) for line in context.text.splitlines()), default=0.0)


def ask(
    db: Session,
    user: User,
    *,
    question: str,
    patient_id: uuid.UUID | None,
    base: str,
    method: str,
    k: int,
) -> AskResult:
    patient: Patient | None = None
    context: PatientContext | None = None
    if patient_id is not None:
        # Patient data leaves for an external model: only the clinicians who
        # would sign the answer may trigger that.
        authz.require_role(user, *authz.AI_REVIEWERS)
        patient = patients_service.get_patient(db, patient_id)
        patient_access.ensure_staff_patient_scope(db, user, patient.id)
        context = build_patient_context(db, user, patient)
        safe_question = privacy.scrub(question, context.identity)
    else:
        authz.require_staff(user)
        # No patient, no name to look up; still never let a typed document, phone
        # or e-mail reach the embedder or the model.
        safe_question = privacy.scrub_generic(question)
    pseudonym = context.pseudonym if context is not None else None

    result = retrieve(
        db,
        safe_question,
        method=method,
        k=k,
        base_filter=base,
        patient_pseudonym=pseudonym,
    )
    has_context = context is not None and not context.is_empty
    evidence = result.has_evidence(extra_bm25=_context_bm25(db, safe_question, context))

    sources: list[PromptSource] = []
    if evidence:
        if has_context:
            assert context is not None
            sources.append(
                PromptSource(
                    n=1,
                    chunk_id=f"CTX-{context.pseudonym}",
                    doc_id=RagBase.HISTORY.value,
                    title="Historia clínica (seudonimizada)",
                    url=None,
                    score=None,
                    label=PATIENT_CONTEXT_LABEL,
                    text=context.text,
                )
            )
        for hit in result.hits:
            text = hit.chunk.text
            if context is not None and hit.chunk.base == RagBase.HISTORY.value:
                text = privacy.scrub(text, context.identity)
            sources.append(rag_answer.source_from_hit(len(sources) + 1, hit, text=text))
    if sources:
        composition = rag_answer.compose_answer(safe_question, sources, degraded=result.degraded)
    else:
        composition = rag_answer.Composition(ABSTENTION_TEXT, RagAnswerMode.ABSTENTION, "", [])
    answer, mode = composition.answer, composition.mode
    if mode == RagAnswerMode.ABSTENTION:
        sources = []

    source_rows = [
        RagSource(
            n=s.n, chunk_id=s.chunk_id, doc_id=s.doc_id, title=s.title, url=s.url, score=s.score
        ).model_dump()
        for s in sources
    ]

    suggestion: RagSuggestion | None = None
    if patient is not None and mode != RagAnswerMode.ABSTENTION:
        suggestion = RagSuggestion(
            patient_id=patient.id,
            question=safe_question,
            answer=answer,
            mode=mode,
            sources=source_rows,
            # Evidence of what LEFT the system: only a model answer sent a prompt.
            prompt_sent=composition.messages if mode == RagAnswerMode.LLM else [],
            model=composition.model,
            status=RagSuggestionStatus.PENDING,
            requested_by=user.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(suggestion)
        db.flush()

    # The audit trail records WHAT was consulted, never the question or the answer.
    trail.audit(
        db,
        action=AuditAction.RAG_QUERY,
        entity_type=ENTITY,
        entity_id=suggestion.id if suggestion is not None else None,
        actor=user,
        metadata={
            "patient_id": str(patient.id) if patient is not None else None,
            "pseudonym": pseudonym,
            "chunk_ids": [s.chunk_id for s in sources],
            "mode": mode.value,
            "method": method,
            "base": base,
            "k": k,
            "retrieval_degraded": result.degraded,
            "suggestion_id": str(suggestion.id) if suggestion is not None else None,
        },
    )
    commit(db)
    return AskResult(
        answer=answer,
        mode=mode,
        sources=source_rows,
        suggestion_id=suggestion.id if suggestion is not None else None,
        retrieval_degraded=result.degraded,
        degraded_reason=composition.degraded_reason if mode != RagAnswerMode.LLM else None,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_suggestion(db: Session, suggestion_id: uuid.UUID) -> RagSuggestion:
    suggestion = db.scalar(
        select(RagSuggestion)
        .options(joinedload(RagSuggestion.reviewer))
        .where(RagSuggestion.id == suggestion_id)
        # Re-read the row: after a review the cached reviewer would be stale.
        .execution_options(populate_existing=True)
    )
    if suggestion is None:
        raise NotFoundError("Sugerencia no encontrada")
    return suggestion


def suggestions_stmt(
    user: User,
    *,
    status: RagSuggestionStatus | None = None,
    patient_id: uuid.UUID | None = None,
) -> Select[tuple[RagSuggestion]]:
    """Suggestions in the caller's scope (own IPS, plus patients referred to a specialist)."""

    stmt = (
        select(RagSuggestion)
        .options(joinedload(RagSuggestion.reviewer))
        .join(Patient, Patient.id == RagSuggestion.patient_id)
        .where(Patient.deleted_at.is_(None))
    )
    scope = authz.org_filter_for(user)
    if scope is not None:
        in_scope = RagSuggestion.patient_id.in_(
            select(Encounter.patient_id).where(
                Encounter.organization_id == scope, Encounter.deleted_at.is_(None)
            )
        )
        granted = patient_access.referred_patient_ids(user)
        if granted is not None:
            in_scope = or_(in_scope, RagSuggestion.patient_id.in_(granted))
        stmt = stmt.where(in_scope)
    if status is not None:
        stmt = stmt.where(RagSuggestion.status == status)
    if patient_id is not None:
        stmt = stmt.where(RagSuggestion.patient_id == patient_id)
    return stmt


def list_suggestions(
    db: Session,
    user: User,
    *,
    status: RagSuggestionStatus | None = None,
    patient_id: uuid.UUID | None = None,
) -> list[RagSuggestion]:
    stmt = suggestions_stmt(user, status=status, patient_id=patient_id)
    return list(db.scalars(stmt.order_by(RagSuggestion.created_at.desc()).limit(MAX_LIST)))


def approved_suggestions(db: Session, patient_id: uuid.UUID) -> list[RagSuggestion]:
    return list(
        db.scalars(
            select(RagSuggestion)
            .options(joinedload(RagSuggestion.reviewer))
            .where(
                RagSuggestion.patient_id == patient_id,
                RagSuggestion.status == RagSuggestionStatus.APPROVED,
            )
            .order_by(RagSuggestion.reviewed_at.desc())
        )
    )


def to_read(suggestion: RagSuggestion) -> RagSuggestionRead:
    return RagSuggestionRead(**_read_fields(suggestion))


def to_detail(suggestion: RagSuggestion) -> RagSuggestionDetail:
    return RagSuggestionDetail(**_read_fields(suggestion), prompt_sent=suggestion.prompt_sent)


def _read_fields(suggestion: RagSuggestion) -> dict[str, Any]:
    return {
        "id": suggestion.id,
        "patient_id": suggestion.patient_id,
        "question": suggestion.question,
        "answer": suggestion.answer,
        "mode": suggestion.mode,
        "sources": suggestion.sources,
        "model": suggestion.model,
        "estado": suggestion.status,
        "requested_by": suggestion.requested_by,
        "reviewed_by": suggestion.reviewed_by,
        "firmado_por": suggestion.reviewer.full_name if suggestion.reviewer is not None else None,
        "reviewed_at": suggestion.reviewed_at,
        "observaciones": suggestion.notes,
        "motivo_rechazo": suggestion.rejection_reason,
        "created_at": suggestion.created_at,
    }


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _audit_meta(suggestion: RagSuggestion, **extra: object) -> dict[str, Any]:
    return {
        "suggestion_id": str(suggestion.id),
        "patient_id": str(suggestion.patient_id),
        "mode": suggestion.mode.value,
        "model": suggestion.model,
        "chunk_ids": [s["chunk_id"] for s in suggestion.sources],
        **extra,
    }


def _ensure_pending(suggestion: RagSuggestion) -> None:
    if suggestion.status != RagSuggestionStatus.PENDING:
        raise ConflictError(f"La sugerencia ya fue revisada ({suggestion.status.value})")


def _transition(
    db: Session,
    suggestion: RagSuggestion,
    new_status: RagSuggestionStatus,
    *,
    reviewed_by: uuid.UUID,
    notes: str | None,
    rejection_reason: str | None = None,
) -> None:
    """``UPDATE ... WHERE status = 'pendiente'``: two concurrent reviews cannot both win.

    The loser matches no row and gets a 409; nothing else it prepared is written.
    """

    values: dict[str, Any] = {
        "status": new_status,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(timezone.utc),
        "notes": notes,
    }
    if rejection_reason is not None:
        values["rejection_reason"] = rejection_reason
    result = db.execute(
        update(RagSuggestion)
        .where(RagSuggestion.id == suggestion.id, RagSuggestion.status == RagSuggestionStatus.PENDING)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ConflictError("La sugerencia ya fue revisada")


def _require_review_note(text: str | None, *, what: str) -> None:
    """Same rule as AI reports: a written concept when ``REQUIRE_REVIEW_NOTE`` is on."""

    if get_settings().require_review_note and len((text or "").strip()) < MIN_REVIEW_NOTE:
        raise InvalidValueError(
            f"{what} requiere el concepto clinico del profesional: "
            f"escriba al menos {MIN_REVIEW_NOTE} caracteres"
        )


def _history_chunk(
    db: Session, suggestion: RagSuggestion, patient: Patient, notes: str | None
) -> RagChunk:
    """The approved suggestion as a searchable ``historia`` chunk of its patient."""

    identity = privacy.PatientIdentity.of(patient, pseudonymization.pseudonym_for(patient.id))
    answer = _CITATION.sub("", suggestion.answer).strip()
    parts = [
        f"Pregunta clínica: {suggestion.question}",
        f"Respuesta aprobada por el médico: {answer}",
    ]
    if notes:
        parts.append(f"Concepto del médico: {notes}")
    text = privacy.scrub("\n".join(parts), identity)
    doc_id = f"HS-{suggestion.id.hex[:12]}"
    # Best effort: an embedding outage must not block a clinical sign-off. Without
    # a vector the chunk is still found lexically.
    vector = embed_query(text)
    if vector is not None and len(vector) != EMBEDDING_DIM:
        vector = None
    return RagChunk(
        chunk_id=f"{doc_id}-0",
        doc_id=doc_id,
        base=RagBase.HISTORY,
        doc_type=HISTORY_DOC_TYPE,
        estado=store.HISTORY_APPROVED,
        patient_pseudonym=identity.pseudonym,
        source="Historia clínica (seudonimizada)",
        title=f"Sugerencia aprobada {datetime.now(timezone.utc):%Y-%m-%d}",
        url=None,
        keywords=[],
        text=text,
        word_count=len(text.split()),
        embedding=vector,
    )


def approve(
    db: Session, suggestion: RagSuggestion, *, actor: User, observaciones: str | None
) -> RagSuggestion:
    _ensure_pending(suggestion)
    _require_review_note(observaciones, what="Aprobar la sugerencia")
    notes = (observaciones or "").strip() or None
    patient = patients_service.get_patient(db, suggestion.patient_id)
    chunk = _history_chunk(db, suggestion, patient, notes)

    _transition(
        db,
        suggestion,
        RagSuggestionStatus.APPROVED,
        reviewed_by=actor.id,
        notes=notes,
    )
    # Only reached when THIS request won the transition: no history chunk otherwise.
    db.add(chunk)
    trail.audit(
        db,
        action=AuditAction.RAG_SUGGESTION_APPROVED,
        entity_type=ENTITY,
        entity_id=suggestion.id,
        actor=actor,
        metadata=_audit_meta(suggestion, history_chunk_id=chunk.chunk_id),
    )
    commit(db)
    store.invalidate_cache()
    return get_suggestion(db, suggestion.id)


def reject(
    db: Session,
    suggestion: RagSuggestion,
    *,
    actor: User,
    motivo: str,
    observaciones: str | None,
) -> RagSuggestion:
    _ensure_pending(suggestion)
    _require_review_note(motivo, what="Rechazar la sugerencia")
    _transition(
        db,
        suggestion,
        RagSuggestionStatus.REJECTED,
        reviewed_by=actor.id,
        notes=(observaciones or "").strip() or None,
        rejection_reason=motivo.strip(),
    )
    trail.audit(
        db,
        action=AuditAction.RAG_SUGGESTION_REJECTED,
        entity_type=ENTITY,
        entity_id=suggestion.id,
        actor=actor,
        metadata=_audit_meta(suggestion, motivo=motivo.strip()),
    )
    commit(db)
    return get_suggestion(db, suggestion.id)
