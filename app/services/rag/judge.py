"""LLM-as-judge metrics, RAGAS-style (the ``ragas`` library is NOT used).

Each metric follows the RAGAS definition and is a number in [0, 1]:

* **faithfulness**: share of the answer's claims that the retrieved context supports.
* **answer relevancy**: mean cosine between the question and questions the judge
  generates from the answer (an answer that addresses the question implies it).
* **context precision**: rank-weighted share of retrieved chunks that are relevant,
  ``sum(precision@k * relevant_k) / sum(relevant_k)``.
* **context recall**: share of the reference answer's sentences that the retrieved
  context can attribute.

Judge prompts ask for JSON only, run at temperature 0 (the client's default), and
are retried when the reply cannot be parsed or has the wrong shape.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from app.services.errors import LlmUnavailableError
from app.services.rag import fusion
from app.services.rag.llm import LlmClient

ATTEMPTS = 4
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


_TRUE_WORDS = frozenset({"true", "si", "sí", "yes", "1"})


def _truthy(value: object) -> bool:
    """Strict reading of a verdict: only true, "true", "sí" or 1 count; anything else is false."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in _TRUE_WORDS


class JudgeError(RuntimeError):
    """The judge never produced a usable reply."""


def parse_json(text: str) -> Any:
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(cleaned[start : end + 1])


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(" ".join(text.split())) if s.strip()]


class Judge:
    def __init__(
        self,
        client: LlmClient,
        embed: Callable[[str], list[float] | None],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self._client = client
        self._embed = embed
        self._sleep = sleep
        self.model = client.model

    def _ask(self, prompt: str, validate: Callable[[Any], Any]) -> Any:
        messages = [
            {"role": "system", "content": "You are a strict evaluator. Reply with ONE valid JSON object and nothing else."},
            {"role": "user", "content": prompt},
        ]
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                return validate(parse_json(self._client.complete(messages)))
            except LlmUnavailableError as exc:
                last = exc
                if exc.reason == "rate_limited":
                    # The client already waited and retried; asking again would
                    # only multiply the 429s.
                    break
                self._sleep(2.0 * (attempt + 1))  # outage: back off
            except (ValueError, KeyError, TypeError) as exc:  # unparsable or wrong shape: ask again
                last = exc
        raise JudgeError(f"judge failed: {last.__class__.__name__}")

    @staticmethod
    def _numbered(items: list[str]) -> str:
        return "\n".join(f"[{i}] {text}" for i, text in enumerate(items, start=1))

    def _verdicts(self, prompt: str, expected: int) -> list[bool]:
        def validate(data: Any) -> list[bool]:
            verdicts = [_truthy(v) for v in data["verdicts"]]
            if len(verdicts) != expected:
                raise ValueError("wrong number of verdicts")
            return verdicts

        return self._ask(prompt, validate)

    def faithfulness(self, answer: str, contexts: list[str]) -> float | None:
        def validate(data: Any) -> list[bool]:
            claims = data["claims"]
            return [_truthy(c["supported"]) for c in claims]

        verdicts = self._ask(
            "Split the ANSWER into atomic factual claims. For each claim decide whether it can be "
            "inferred from the CONTEXT alone (not from your own knowledge).\n"
            'Return {"claims": [{"claim": "...", "supported": true}]}.\n\n'
            f"CONTEXT:\n{self._numbered(contexts)}\n\nANSWER:\n{answer}",
            validate,
        )
        return sum(verdicts) / len(verdicts) if verdicts else None

    def answer_relevancy(self, question: str, answer: str, n: int = 3) -> float | None:
        def validate(data: Any) -> list[str]:
            generated = [str(q) for q in data["questions"] if str(q).strip()]
            if not generated:
                raise ValueError("no questions")
            return generated[:n]

        generated = self._ask(
            f"Write {n} different questions that the following ANSWER would be a direct reply to. "
            'Return {"questions": ["...", "..."]}.\n\n'
            f"ANSWER:\n{answer}",
            validate,
        )
        base = self._embed(question)
        if base is None:
            return None
        sims = []
        for text in generated:
            vector = self._embed(text)
            if vector is not None:
                sims.append(max(0.0, fusion.cosine(base, vector)))
        return sum(sims) / len(sims) if sims else None

    def context_precision(self, question: str, reference: str, contexts: list[str]) -> float | None:
        if not contexts:
            return None
        verdicts = self._verdicts(
            "For each numbered CONTEXT chunk decide whether it is useful to produce the REFERENCE "
            "answer to the QUESTION. "
            'Return {"verdicts": [true, false, ...]} with exactly one boolean per chunk, in order.\n\n'
            f"QUESTION: {question}\nREFERENCE: {reference}\n\nCONTEXT:\n{self._numbered(contexts)}",
            len(contexts),
        )
        relevant = hits = 0
        total = 0.0
        for rank, ok in enumerate(verdicts, start=1):
            if ok:
                hits += 1
                total += hits / rank
                relevant += 1
        return total / relevant if relevant else 0.0

    def context_recall(self, reference: str, contexts: list[str]) -> float | None:
        sentences = split_sentences(reference)
        if not sentences or not contexts:
            return None
        verdicts = self._verdicts(
            "For each numbered REFERENCE sentence decide whether it can be attributed to (is supported "
            "by) the CONTEXT. "
            'Return {"verdicts": [true, false, ...]} with exactly one boolean per sentence, in order.\n\n'
            f"REFERENCE:\n{self._numbered(sentences)}\n\nCONTEXT:\n{self._numbered(contexts)}",
            len(sentences),
        )
        return sum(verdicts) / len(verdicts)
