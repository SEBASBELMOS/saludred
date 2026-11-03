"""Fakes and a tiny corpus for the RAG tests. No model, no network."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from sqlalchemy.orm import Session

from app.models.rag import EMBEDDING_DIM
from app.services.errors import LlmUnavailableError, MlServiceUnavailableError
from app.services.rag import ingest
from app.services.rag.corpus import CorpusDocument, parse_document
from app.services.rag.text import tokenize


def toy_vector(text: str) -> list[float]:
    """Deterministic bag-of-words embedding: texts sharing tokens have a high cosine."""

    vector = [0.0] * EMBEDDING_DIM
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class FakeEmbedMl:
    """ml client that only knows ``embed``; every call is recorded."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> dict[str, Any]:
        self.calls.append(list(texts))
        return {"model": "toy", "dim": EMBEDDING_DIM, "vectors": [toy_vector(t) for t in texts]}

    def _down(self, *_a: object, **_k: object) -> dict:
        raise MlServiceUnavailableError("test: not used")

    predict_criticality = predict_cleaning_delay = models = _down


class FakeLlm:
    """Language model that answers with a canned text and remembers the prompts."""

    model = "fake-llm"

    def __init__(self, reply: str = "Respuesta de prueba [1].") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.reply


class BrokenLlm:
    model = "broken-llm"

    def __init__(self, reason: str = "unavailable") -> None:
        self.reason = reason

    def complete(self, messages: list[dict[str, str]]) -> str:
        raise LlmUnavailableError("test: down", self.reason)


def _doc(doc_id: str, base: str, title: str, paragraphs: list[str]) -> str:
    body = "\n\n".join(paragraphs)
    return (
        f"---\nid: {doc_id}\nbase: {base}\ntitle: \"{title}\"\npublisher: \"Fuente de prueba\"\n"
        f"url: \"https://example.test/{doc_id}\"\nyear: 2024\ndoc_type: guia\nlicense: \"test\"\n"
        f"language: es\nkeywords: [prueba]\n---\n\n{body}\n"
    )


HYPO = (
    "Hipoglucemia: glucosa menor de 70 mg/dL con temblor, sudoración y confusión. "
    "En el paciente consciente se administran 15 gramos de carbohidratos de absorción rápida "
    "y se repite la medición de glucosa a los 15 minutos."
)
HYPO_UNCONSCIOUS = (
    "Si el paciente con hipoglucemia está inconsciente no se da nada por vía oral: se usa glucagón "
    "o dextrosa intravenosa y se activa la atención de urgencias para el paciente."
)
DIABETES = (
    "Diagnóstico de diabetes: glucosa en ayunas mayor o igual a 126 mg/dL, hemoglobina glicosilada "
    "mayor o igual a 6,5 por ciento o glucosa mayor o igual a 200 mg/dL dos horas después de la carga oral."
)
TRIAGE_ONE = (
    "Triage I: requiere atención inmediata porque la condición clínica del paciente representa "
    "un riesgo vital y necesita maniobras de reanimación en urgencias."
)
TRIAGE_TWO = (
    "Triage II: la condición puede evolucionar hacia un rápido deterioro, por lo tanto la atención "
    "en urgencias no debe superar los treinta minutos."
)

CORPUS_TEXT = {
    "G01": _doc("G01", "general", "Guía de hipoglucemia", [HYPO, HYPO_UNCONSCIOUS]),
    "G02": _doc("G02", "general", "Criterios de diabetes", [DIABETES]),
    "T01": _doc("T01", "triage", "Resolución de triage", [TRIAGE_ONE, TRIAGE_TWO]),
}


def tiny_corpus() -> list[CorpusDocument]:
    return [parse_document(text) for text in CORPUS_TEXT.values()]


def load_tiny_corpus(db: Session, *, embed: bool = True) -> None:
    ingest.ingest_corpus(db, tiny_corpus(), embed=embed)
