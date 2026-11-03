"""Answer generation: the language model when available, quotes otherwise."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.enums import RagAnswerMode, RagBase
from app.services.errors import LlmUnavailableError
from app.services.rag import llm
from app.services.rag.prompt import (
    ABSTENTION_TEXT,
    PATIENT_CONTEXT_LABEL,
    PromptSource,
    build_messages,
    extractive_answer,
)
from app.services.rag.retrieval import Hit

logger = logging.getLogger(__name__)

_DEFAULT = object()  # "use the configured client"; ``None`` means "no model"
EXTRACTIVE_MODEL = "extractivo"
MAX_LABEL_TITLE = 140


def source_from_hit(n: int, hit: Hit, *, text: str | None = None) -> PromptSource:
    """A retrieved chunk as the numbered, labelled context the prompt cites."""

    chunk = hit.chunk
    if chunk.base == RagBase.HISTORY.value:
        label = f"{PATIENT_CONTEXT_LABEL} · {chunk.title}"
    else:
        label = f"{chunk.source} · {chunk.title[:MAX_LABEL_TITLE]}"
    return PromptSource(
        n=n,
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        title=chunk.title,
        url=chunk.url,
        score=round(hit.score, 4),
        label=label,
        text=chunk.text if text is None else text,
    )


@dataclass(frozen=True)
class Composition:
    answer: str
    mode: RagAnswerMode
    model: str
    messages: list[dict[str, str]]
    # Why a model answer was not used (no key, rate limit, outage); never patient data.
    degraded_reason: str | None = None


def compose_answer(
    question: str,
    sources: list[PromptSource],
    *,
    degraded: bool = False,
    client: llm.LlmClient | None | object = _DEFAULT,
) -> Composition:
    """Ask the model with the pseudonymized prompt; on any failure, quote the sources.

    ``degraded`` marks that retrieval was lexical-only, which labels the
    extractive answer ``lexico`` instead of ``extractivo``.
    """

    messages = build_messages(question, sources)
    if client is _DEFAULT:
        client = llm.get_client()
    reason: str | None = "llm_not_configured"
    if client is not None:
        reason = None
        try:
            text = client.complete(messages).strip()
        except LlmUnavailableError as exc:
            text = ""
            reason = f"llm_{exc.reason}"
            logger.warning("RAG answer falls back to extractive mode (%s)", reason)
        if text:
            if ABSTENTION_TEXT in text and len(text) < len(ABSTENTION_TEXT) + 40:
                return Composition(ABSTENTION_TEXT, RagAnswerMode.ABSTENTION, client.model, messages)
            return Composition(text, RagAnswerMode.LLM, client.model, messages)
    mode = RagAnswerMode.LEXICAL if degraded else RagAnswerMode.EXTRACTIVE
    return Composition(extractive_answer(sources), mode, EXTRACTIVE_MODEL, messages, reason)
