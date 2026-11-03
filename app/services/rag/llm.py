"""Language-model client (Groq, OpenAI-compatible chat completions).

A single client instance is held at module level so tests can swap in a fake
(``set_client``) and no test ever opens a socket. With no API key ``get_client``
returns ``None`` and the caller answers extractively.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.services.errors import LlmUnavailableError

logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK = re.compile(r"<think>.*\Z", re.DOTALL)

Messages = list[dict[str, str]]


class LlmClient(Protocol):
    model: str

    def complete(self, messages: Messages) -> str: ...


BACKOFF_SECONDS = (2.0, 4.0, 8.0, 16.0, 32.0)
MAX_RETRY_AFTER = 120.0
# gpt-oss models spend completion tokens on reasoning before the answer, so a
# judge needs far more room than a short cited answer does.
JUDGE_MIN_TOKENS = 1500


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return min(max(float(response.headers.get("retry-after", "")), 0.0), MAX_RETRY_AFTER)
    except ValueError:
        return None


class GroqClient:
    """Groq chat completions with optional-parameter fallback and 429 back-off.

    ``reasoning_format: hidden`` (and ``reasoning_effort: low`` for gpt-oss) are
    asked for; a model that answers 400 to one of them is retried without it and
    the client remembers, so the next call does not fail again. On HTTP 429 it
    waits the ``retry-after`` header, or 2, 4, 8, 16, 32 s, up to ``max_retries``.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        max_tokens: int = 600,
        *,
        max_retries: int = 0,
        reasoning_effort: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._max_tokens = max_tokens
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        self._max_retries = max_retries
        self._reasoning_effort = reasoning_effort
        self._sleep = sleep
        self._unsupported: set[str] = set()
        self.rate_limit_hits = 0

    def _body(self, messages: Messages) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "reasoning_format": "hidden",
        }
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        for name in self._unsupported:
            body.pop(name, None)
        return body

    def _send(self, messages: Messages) -> httpx.Response:
        """One request, dropping optional parameters the model rejects (400)."""

        while True:
            body = self._body(messages)
            response = self._post(body)
            if response.status_code != 400:
                return response
            droppable = [n for n in ("reasoning_format", "reasoning_effort") if n in body]
            if not droppable:
                return response
            self._unsupported.add(droppable[0])

    def complete(self, messages: Messages) -> str:
        try:
            attempt = 0
            while True:
                response = self._send(messages)
                if response.status_code != 429:
                    break
                self.rate_limit_hits += 1
                if attempt >= self._max_retries:
                    break
                wait = _retry_after(response)
                self._sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] if wait is None else wait)
                attempt += 1
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("empty content")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("LLM call failed: HTTP %s", status)
            reason = "rate_limited" if status == 429 else "unavailable"
            raise LlmUnavailableError("El modelo de lenguaje no esta disponible", reason) from exc
        except httpx.TimeoutException as exc:
            logger.warning("LLM call failed: timeout")
            raise LlmUnavailableError("El modelo de lenguaje no esta disponible", "timeout") from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Never log the response body: it can echo the prompt.
            logger.warning("LLM call failed: %s", exc.__class__.__name__)
            raise LlmUnavailableError("El modelo de lenguaje no esta disponible") from exc
        return _OPEN_THINK.sub("", _THINK.sub("", content)).strip()

    def _post(self, body: dict[str, object]) -> httpx.Response:
        return httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
            timeout=self._timeout,
        )


class ThrottledClient:
    """Pauses ``seconds`` after every call, to stay under a per-minute quota."""

    def __init__(self, inner: LlmClient, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
        self._inner = inner
        self._seconds = seconds
        self._sleep = sleep
        self.model = inner.model

    @property
    def rate_limit_hits(self) -> int:
        return int(getattr(self._inner, "rate_limit_hits", 0))

    def complete(self, messages: Messages) -> str:
        try:
            return self._inner.complete(messages)
        finally:
            if self._seconds > 0:
                self._sleep(self._seconds)


class _Unset:
    pass


_UNSET = _Unset()
_override: LlmClient | None | _Unset = _UNSET


def build_client(
    model: str | None = None, *, max_retries: int = 0, max_tokens: int | None = None
) -> GroqClient | None:
    """A Groq client for ``model`` (default: the generator model); ``None`` without a key."""

    settings = get_settings()
    if not settings.has_llm_key:
        return None
    chosen = model or settings.groq_model
    return GroqClient(
        str(settings.groq_api_key).strip(),
        chosen,
        settings.groq_base_url,
        settings.groq_timeout_seconds,
        max_tokens or settings.groq_max_tokens,
        max_retries=max_retries,
        reasoning_effort="low" if "gpt-oss" in chosen else None,
    )


def get_client(*, max_retries: int = 0) -> LlmClient | None:
    if not isinstance(_override, _Unset):
        return _override
    return build_client(max_retries=max_retries)


_judge_override: LlmClient | None | _Unset = _UNSET


def get_judge_client(*, model: str | None = None, max_retries: int = 0) -> LlmClient | None:
    """The judge's OWN client: ``model``, else ``RAG_JUDGE_MODEL``, else the generator model.

    Groq rate limits are per model, so a different judge splits the quota, and it
    removes the bias of a model grading its own answers.
    """

    if not isinstance(_judge_override, _Unset):
        return _judge_override
    chosen = model or get_settings().rag_judge_model or None
    settings = get_settings()
    tokens = max(settings.groq_max_tokens, JUDGE_MIN_TOKENS)
    return build_client(chosen, max_retries=max_retries, max_tokens=tokens)


def set_judge_client(client: LlmClient | None | _Unset = _UNSET) -> None:
    """Replace the judge client (tests)."""

    global _judge_override
    _judge_override = client


def set_client(client: LlmClient | None | _Unset = _UNSET) -> None:
    """Replace the client (tests). ``None`` forces "no model"; no argument restores the default."""

    global _override
    _override = client
