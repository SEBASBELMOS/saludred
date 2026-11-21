"""The harness: the agent plus everything around it that is not the model.

* ``build_agent``: ``create_agent`` with the middleware stack (dynamic prompt, role
  filter, token context window, one approval-gated action per model response,
  provider limit, model-call limit, human approval of writes, audit).
* ``Harness``: one lock per thread (pruned when idle), a cap of turns in flight,
  wall-clock timeout with reconciliation against the checkpointer, the thread's
  patient binding, pending approvals (kept in the store, so they survive a restart
  when memory is Postgres) and a degraded answer when the model is unavailable.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    dynamic_prompt,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command

from app.core.config import get_settings
from app.models.enums import AuditAction
from app.services import pseudonymization, trail
from app.services.agent import guidelines, skills
from app.services.agent.context import (
    CLINICAL_ROLE_NAMES,
    REFERRING_ONLY_TOOLS,
    REFERRING_ROLE,
    AgentContext,
    preferences_namespace,
)
from app.services.agent.db import session_scope
from app.services.agent.flow import is_pleasantry
from app.services.agent.model import failure_reason
from app.services.agent.presentation import NO_SUPPORT_TEXT, STATUS_TEXT, clean_answer
from app.services.agent.prompt import build_prompt
from app.services.agent.tools import TOOLS, scrub_text, scrub_with
from app.services.errors import commit

logger = logging.getLogger(__name__)

THREADS = ("hilos",)  # store namespace: which patient a thread is bound to, pending flag
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK = re.compile(r"<think>.*\Z", re.DOTALL)
STATUS_PREFIXES = tuple(STATUS_TEXT)
# Tools that wait for the clinician. At most ONE per model response is processed.
GATED_TOOLS = frozenset({"proponer_remision", "guardar_preferencia"})
CANCEL_MESSAGE = "El clínico canceló la acción."
# Audit of a tool call (R24): the payload sent to the model, pseudonymized and bounded
# so a long passage cannot bloat the audit table.
AUDIT_RESULT_CHARS = 3000


def _debug(message: str, *args: Any) -> None:
    """Detail of the turn: written only in test mode (``AGENT_DEBUG``)."""

    if get_settings().agent_debug:
        logger.info(message, *args)


# ---------- 1. Dynamic prompt: rebuilt on EVERY model call ----------
@dynamic_prompt
async def clinical_prompt(request: ModelRequest) -> str:
    ctx = request.runtime.context
    preferences: list[str] = []
    if request.runtime.store is not None:
        saved = await request.runtime.store.asearch(preferences_namespace(ctx.user_id), limit=10)
        preferences = [item.value["texto"] for item in saved]
    return build_prompt(ctx, skills.index(), preferences)


# ---------- 2. Tools by role: the model is not even offered what it may not use ----------
class RoleTools(AgentMiddleware):
    def _filter(self, request):  # noqa: ANN001, ANN202
        if request.runtime.context.role == REFERRING_ROLE:
            return request
        return request.override(tools=[t for t in request.tools if t.name not in REFERRING_ONLY_TOOLS])

    def wrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return handler(self._filter(request))

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return await handler(self._filter(request))


# ---------- 3. Sanitize and audit every tool result before it reaches the model ----------
class ToolAudit(AgentMiddleware):
    @staticmethod
    def _sanitize(request: Any, result: Any) -> Any:
        """Return the same sanitized message that the audit and the model will see."""

        if not isinstance(result, ToolMessage):
            return result
        ctx = request.runtime.context
        with session_scope() as db:
            content = scrub_with(db, ctx, str(result.content))
        return result.model_copy(update={"content": content})

    @staticmethod
    def _payload(content: str) -> dict[str, Any]:
        """Bounded prefix of the actual sanitized tool result, not a second version."""

        return {"enviado_al_llm": content[:AUDIT_RESULT_CHARS], "truncado": len(content) > AUDIT_RESULT_CHARS}

    def _record(self, request, result, error: bool) -> None:  # noqa: ANN001
        ctx: AgentContext = request.runtime.context
        call = request.tool_call
        status = "ERROR" if error else "OK"
        artifact: dict[str, Any] = {}
        content = ""
        if isinstance(result, ToolMessage):
            content = str(result.content)
            status = next((p for p in STATUS_PREFIXES if content.startswith(p)), status)
            artifact = result.artifact if isinstance(result.artifact, dict) else {}
        metadata: dict[str, Any] = {
            "tool": call["name"],
            "arg_names": sorted(call["args"]),  # names only: values may carry free text
            "status": status,
            "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            "pseudonym": ctx.patient_alias,
            "role": ctx.role,
        }
        if call["name"] == "cargar_skill":
            metadata["skill"] = str(call["args"].get("nombre", ""))[:64]
        if call["name"] == "proponer_remision":
            metadata["especialista_id"] = str(call["args"].get("especialista_id", ""))[:40]
        metadata.update({k: v for k, v in artifact.items() if k in {"chunk_ids", "degraded", "referral_id"}})
        _debug("agent tool=%s status=%s args=%s", call["name"], status, metadata["arg_names"])
        with session_scope() as db:
            from app.models.identity import User

            actor = db.get(User, ctx.user_id)
            metadata["payload_llm"] = self._payload(content)
            trail.audit(
                db,
                action=AuditAction.AGENT_TOOL,
                entity_type="agent",
                entity_id=None,
                actor=actor,
                metadata=metadata,
                ip_address=ctx.client_ip,
            )
            commit(db)

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        try:
            result = await handler(request)
            result = await asyncio.to_thread(self._sanitize, request, result)
        except Exception:
            await asyncio.to_thread(self._record, request, None, True)
            raise
        await asyncio.to_thread(self._record, request, result, False)
        return result

    def wrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        try:
            result = handler(request)
            result = self._sanitize(request, result)
        except Exception:
            self._record(request, None, True)
            raise
        self._record(request, result, False)
        return result


# ---------- 4. Context window (working memory), in tokens ----------
def trim_to_window(messages: list[Any], max_tokens: int) -> list[Any]:
    """The most recent messages that fit ``max_tokens`` (approximate count), starting on a
    human message. Never empty: if even the last question does not fit, it is kept."""

    recent = trim_messages(
        messages,
        max_tokens=max_tokens,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
    )
    if recent:
        return recent
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    return messages[last_human:]


class ContextWindow(AgentMiddleware):
    def __init__(self, max_tokens: int | None = None) -> None:
        super().__init__()
        self.max_tokens = max_tokens or get_settings().agent_context_max_tokens

    def _trim(self, request):  # noqa: ANN001, ANN202
        return request.override(messages=trim_to_window(request.messages, self.max_tokens))

    def wrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return handler(self._trim(request))

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return await handler(self._trim(request))


# ---------- 5. One approval-gated action per model response ----------
def keep_first_gated(message: AIMessage) -> AIMessage:
    """Drop every gated tool call after the first. A response with several would pause
    with several approvals while the endpoint answers one decision."""

    gated = [c for c in message.tool_calls if c["name"] in GATED_TOOLS]
    if len(gated) <= 1:
        return message
    keep = gated[0]["id"]
    calls = [c for c in message.tool_calls if c["name"] not in GATED_TOOLS or c["id"] == keep]
    logger.warning("Agent proposed %d actions needing approval in one response; kept the first", len(gated))
    extras = {k: v for k, v in message.additional_kwargs.items() if k != "tool_calls"}
    return message.model_copy(update={"tool_calls": calls, "additional_kwargs": extras})


def _only_first_gated(response: Any) -> Any:
    if isinstance(response, AIMessage):
        return keep_first_gated(response)
    result = getattr(response, "result", None)
    if result is not None and dataclasses.is_dataclass(response):
        trimmed = [keep_first_gated(m) if isinstance(m, AIMessage) else m for m in result]
        return replace(response, result=trimmed)
    inner = getattr(response, "model_response", None)  # ExtendedModelResponse
    if inner is not None and dataclasses.is_dataclass(response):
        return replace(response, model_response=_only_first_gated(inner))
    return response


class SingleGatedAction(AgentMiddleware):
    def wrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return _only_first_gated(handler(request))

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return _only_first_gated(await handler(request))


# ---------- 6. Simultaneous provider calls ----------
class ProviderLimit(AgentMiddleware):
    def __init__(self, maximum: int = 4) -> None:
        super().__init__()
        self.maximum = maximum
        self._async_sem = asyncio.Semaphore(maximum)
        self._thread_sem = threading.Semaphore(maximum)

    def wrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        with self._thread_sem:
            return handler(request)

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        async with self._async_sem:
            return await handler(request)


def build_agent(  # noqa: ANN201
    model: Any,
    checkpointer: Any,
    store: Any,
    *,
    max_concurrent: int | None = None,
    context_max_tokens: int | None = None,
    single_gated_action: bool = True,
):
    gate = {"allowed_decisions": ["approve", "reject"]}
    middleware: list[Any] = [
        clinical_prompt,
        RoleTools(),
        ContextWindow(max_tokens=context_max_tokens),
    ]
    if single_gated_action:
        middleware.append(SingleGatedAction())
    middleware += [
        ProviderLimit(maximum=max_concurrent or get_settings().agent_max_concurrent_calls),
        ModelCallLimitMiddleware(run_limit=8),
        HumanInTheLoopMiddleware(interrupt_on={name: dict(gate) for name in sorted(GATED_TOOLS)}),
        ToolAudit(),
    ]
    return create_agent(
        model=model,
        tools=TOOLS,
        context_schema=AgentContext,
        middleware=middleware,
        checkpointer=checkpointer,
        store=store,
    )


EVIDENCE_TOOLS = frozenset({"consultar_paciente", "buscar_guias"})
# Tools whose fixed output may be shown as the answer when there is no clinical evidence:
# they confirm an action or return a procedure or a list, never a clinical claim.
OPERATIONAL_TOOLS = GATED_TOOLS | {"cargar_skill", "listar_especialistas"}


def cited_sources(messages: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    """The guideline passages and patient context returned in THIS turn (R23).

    Returns ``(sources, unsupported)``. Evidence comes ONLY from ``consultar_paciente``
    (the open patient's approved record) and from a non-empty ``buscar_guias``. The output
    of ``cargar_skill``, ``listar_especialistas`` or a gated tool is a procedure, a list or
    a confirmation, not clinical support, so it never counts: ``unsupported`` is True
    whenever no evidence tool returned data in this turn.
    A passage found twice is listed once. Citations use the stable chunk_id; ``n`` is
    only the display order, so multiple searches cannot assign the same citation to
    different documents.
    """

    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1) + 1
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    has_data = False
    for message in messages[start:]:
        if not isinstance(message, ToolMessage):
            continue
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        if message.name in EVIDENCE_TOOLS:
            # Both tools set ``found`` in their artifact only when they returned data; a
            # status code (SIN_PACIENTE, NO_AUTORIZADO, SIN_EVIDENCIA) comes with no artifact
            # or ``found: False``.
            has_data |= artifact.get("found") is True and message.status != "error"
        for source in artifact.get("sources") or []:
            if source["chunk_id"] not in seen:
                seen.add(source["chunk_id"])
                sources.append({**source, "n": len(sources) + 1})
    return sources, not has_data and not sources


def last_question(messages: list[Any]) -> str:
    """The text of the clinician's message that started this turn."""

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return clean_text(message.content)
    return ""


def clean_text(content: Any) -> str:
    if not isinstance(content, str):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return _OPEN_THINK.sub("", _THINK.sub("", content)).strip()


class Harness:
    def __init__(
        self,
        agent: Any | None,
        store: Any,
        timeout_s: float | None = None,
        max_inflight: int | None = None,
    ) -> None:
        settings = get_settings()
        self.agent = agent  # None: no model configured -> degraded answers only
        self.store = store
        self.timeout_s = timeout_s or settings.agent_timeout_seconds
        self.max_inflight = max_inflight or settings.agent_max_inflight_turns
        self._locks: dict[str, asyncio.Lock] = {}  # one per thread, dropped when idle
        self._waiting: dict[str, int] = {}
        self._inflight = 0

    @staticmethod
    def thread(user_id: Any, conversation: str) -> str:
        return f"{user_id}:{conversation}"

    @staticmethod
    def _config(thread: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread}}

    @asynccontextmanager
    async def _guard(self, thread: str) -> AsyncIterator[None]:
        """The thread's lock. The entry is removed when nobody holds or awaits it."""

        lock = self._locks.setdefault(thread, asyncio.Lock())
        self._waiting[thread] = self._waiting.get(thread, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._waiting[thread] -= 1
            if self._waiting[thread] <= 0:
                self._waiting.pop(thread, None)
                self._locks.pop(thread, None)

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[bool]:
        """A turn in flight. ``False`` when the process is already at its cap."""

        if self._inflight >= self.max_inflight:
            yield False
            return
        self._inflight += 1
        try:
            yield True
        finally:
            self._inflight -= 1

    @staticmethod
    def _busy(conversation: str) -> dict[str, Any]:
        return {
            "estado": "ocupado",
            "conversacion": conversation,
            "detalle": "El asistente tiene demasiadas consultas en curso; vuelva a intentarlo en unos segundos.",
        }

    # ----- thread registry (patient binding + pending approval) -----
    async def _info(self, thread: str) -> dict[str, Any] | None:
        item = await self.store.aget(THREADS, thread)
        return item.value if item is not None else None

    async def _save_info(self, thread: str, patient_id: Any, pending: bool) -> None:
        await self.store.aput(
            THREADS, thread, {"paciente_id": str(patient_id) if patient_id else None, "pendiente": pending}
        )

    async def patient_of(self, user_id: Any, conversation: str) -> uuid.UUID | None:
        """The patient a conversation is bound to (for the audit of a decision)."""

        info = await self._info(self.thread(user_id, conversation))
        return uuid.UUID(info["paciente_id"]) if info and info.get("paciente_id") else None

    # ----- packaging -----
    @staticmethod
    def _pending(interrupts: Any, conversation: str) -> dict[str, Any]:
        actions = interrupts[0].value["action_requests"]
        return {
            "estado": "pendiente_aprobacion",
            "conversacion": conversation,
            "acciones": [{"herramienta": a["name"], "args": a["args"]} for a in actions],
        }

    async def _degraded(
        self, ctx: AgentContext, message: str, conversation: str, reason: str
    ) -> dict[str, Any]:
        """The model is unavailable: answer with the extractive guideline search."""

        def extractive() -> guidelines.GuidelineSearch | None:
            from app.models.identity import User

            with session_scope() as db:
                user = db.get(User, ctx.user_id)
                if user is None:
                    return None
                return guidelines.search(db, user, ctx.patient_id, message)

        found = None
        try:
            found = await asyncio.to_thread(extractive)
        except Exception:  # noqa: BLE001
            logger.exception("Agent degraded answer: guideline search failed")
        body = (
            "El asistente conversacional no está disponible en este momento. "
            "Esto es lo que dicen las guías sobre su pregunta (búsqueda directa, sin redacción del modelo):\n"
            + (found.text if found is not None else "SIN_EVIDENCIA: la búsqueda en las guías tampoco respondió.")
        )
        return {
            "estado": "degradado",
            "conversacion": conversation,
            "motivo": reason,
            "respuesta": clean_answer(body),
            "fuentes": list(found.sources) if found is not None else [],
        }

    async def _run(self, payload: Any, ctx: AgentContext, thread: str) -> Any:
        # durability="sync": each step is checkpointed before the next one starts, so
        # after a timeout ``aget_state`` shows what really happened (see _after_timeout).
        return await asyncio.wait_for(
            self.agent.ainvoke(
                payload, self._config(thread), context=ctx, version="v2", durability="sync"
            ),
            timeout=self.timeout_s,
        )

    async def _after_timeout(
        self, ctx: AgentContext, thread: str, conversation: str, *, resumed: bool
    ) -> dict[str, Any]:
        """The wait ended: ask the checkpointer what really happened.

        ``wait_for`` cancels the await, not the work already done: a tool that was
        running keeps running, and a checkpoint may already hold the model's proposal
        or the consumed approval. The state is the truth, not the timer.
        """

        timed_out = {"estado": "tiempo_agotado", "conversacion": conversation}
        try:
            state = await self.agent.aget_state(self._config(thread))
        except Exception:  # noqa: BLE001
            logger.exception("Agent timeout: could not read the thread state")
            return timed_out
        if state.interrupts:  # still waiting for a decision
            await self._save_info(thread, ctx.patient_id, True)
            result = self._pending(state.interrupts, conversation)
            if resumed:
                result["detalle"] = "No se confirmó la decisión; vuelva a intentarlo."
            return result
        if resumed:  # the approval was consumed: the action ran
            await self._save_info(thread, ctx.patient_id, False)
            return {
                "estado": "decision_aplicada",
                "conversacion": conversation,
                "respuesta": "La decisión se aplicó, pero el asistente no alcanzó a redactar la respuesta final.",
            }
        return timed_out

    # ----- public API -----
    async def attend(self, ctx: AgentContext, message: str, conversation: str = "principal") -> dict[str, Any]:
        thread = self.thread(ctx.user_id, conversation)
        async with self._slot() as free:
            if not free:
                return self._busy(conversation)
            async with self._guard(thread):
                return await self._attend(ctx, message, conversation, thread)

    async def _attend(self, ctx: AgentContext, message: str, conversation: str, thread: str) -> dict[str, Any]:
        info = await self._info(thread)
        if info is not None:
            if info["paciente_id"] != (str(ctx.patient_id) if ctx.patient_id else None):
                return {"estado": "conversacion_de_otro_paciente", "conversacion": conversation}
            if info["pendiente"]:
                return {
                    "estado": "pendiente_aprobacion",
                    "conversacion": conversation,
                    "detalle": "Hay una acción esperando decisión del clínico.",
                }
        else:
            await self._save_info(thread, ctx.patient_id, False)
        if self.agent is None:
            return await self._degraded(ctx, message, conversation, "sin_modelo")
        _debug("agent turn start conversation=%s chars=%d", conversation, len(message))
        try:
            output = await self._run({"messages": [{"role": "user", "content": message}]}, ctx, thread)
        except asyncio.TimeoutError:
            return await self._after_timeout(ctx, thread, conversation, resumed=False)
        except Exception as error:  # noqa: BLE001 -- provider errors must not become a 500
            reason = failure_reason(error)
            logger.warning("Agent model run failed: %s (%s)", type(error).__name__, reason)
            return await self._degraded(ctx, message, conversation, reason)
        return await self._package(output, ctx, thread, conversation)

    async def _package(
        self, output: Any, ctx: AgentContext, thread: str, conversation: str
    ) -> dict[str, Any]:
        if output.interrupts:
            await self._save_info(thread, ctx.patient_id, True)
            return self._pending(output.interrupts, conversation)
        await self._save_info(thread, ctx.patient_id, False)
        messages = output.value["messages"]
        sources, unsupported = cited_sources(messages)
        # R23: a turn that reached the agent route and got no data from an evidence tool
        # (empty or skipped ``buscar_guias``, no open patient) abstains instead of returning
        # uncited clinical prose. Only a bare greeting or courtesy is exempt; the router's
        # fixed replies (administrative, out of scope) and ``/inicio`` never reach this code.
        no_support = unsupported and not is_pleasantry(last_question(messages))
        answer = NO_SUPPORT_TEXT if no_support else clean_answer(clean_text(messages[-1].content))
        if no_support:
            # Operational confirmations use fixed tool results, never unsourced model
            # prose that could append a clinical claim after confirming an action.
            start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1) + 1
            turn = [m for m in messages[start:] if isinstance(m, ToolMessage)]
            # After a search that found nothing, a loaded skill or a specialist list must not
            # stand in for the missing support: only the confirmation of an action may.
            searched_in_vain = any(
                m.name == "buscar_guias" and isinstance(m.artifact, dict) and m.artifact.get("found") is False
                for m in turn
            )
            allowed = GATED_TOOLS if searched_in_vain else OPERATIONAL_TOOLS
            confirmations = [m for m in turn if m.name in allowed]
            if confirmations:
                answer = clean_answer(clean_text(confirmations[-1].content))
        return {
            "estado": "respondido",
            "conversacion": conversation,
            "respuesta": answer,
            "fuentes": sources,
        }

    async def decide(
        self, ctx: AgentContext, conversation: str, decision: str, reason: str | None = None
    ) -> dict[str, Any]:
        """``approve`` / ``reject`` / ``cancel`` the action(s) the thread is waiting on.

        ``cancel`` discards the action without running it and frees the thread. The
        decision is sent once per pending action, so even a thread that paused with
        several (before the single-action rule) cannot stay stuck.
        """

        thread = self.thread(ctx.user_id, conversation)
        if ctx.role not in CLINICAL_ROLE_NAMES:
            return {"estado": "no_autorizado", "conversacion": conversation}
        async with self._slot() as free:
            if not free:
                return self._busy(conversation)
            async with self._guard(thread):
                return await self._decide(ctx, conversation, thread, decision, reason)

    async def _decide(
        self, ctx: AgentContext, conversation: str, thread: str, decision: str, reason: str | None
    ) -> dict[str, Any]:
        info = await self._info(thread)
        if info is None or not info["pendiente"] or self.agent is None:
            return {"estado": "sin_pendientes", "conversacion": conversation}
        patient_id = info["paciente_id"]
        run_ctx = replace(
            ctx,
            patient_id=uuid.UUID(patient_id) if patient_id else None,
            patient_alias=pseudonymization.pseudonym_for(patient_id) if patient_id else None,
        )
        try:
            state = await self.agent.aget_state(self._config(thread))
        except Exception:  # noqa: BLE001
            logger.exception("Agent decision: could not read the thread state")
            state = None
        if state is not None and not state.interrupts:  # the flag outlived its approval: heal it
            await self._save_info(thread, run_ctx.patient_id, False)
            return {"estado": "sin_pendientes", "conversacion": conversation}
        count = len(state.interrupts[0].value["action_requests"]) if state is not None else 1

        item: dict[str, Any] = {"type": "reject" if decision == "cancel" else decision}
        if decision == "cancel":
            item["message"] = CANCEL_MESSAGE
        elif decision == "reject" and reason:
            # The reason is free text typed by a person: it goes to the model and into
            # the checkpoint, so it is scrubbed with the thread's patient first.
            item["message"] = await asyncio.to_thread(scrub_text, run_ctx, reason)
        resume = Command(resume={"decisions": [dict(item) for _ in range(count)]})
        try:
            output = await self._run(resume, run_ctx, thread)
        except asyncio.TimeoutError:
            return await self._after_timeout(run_ctx, thread, conversation, resumed=True)
        except Exception as error:  # noqa: BLE001
            why = failure_reason(error)
            logger.warning("Agent resume failed: %s (%s)", type(error).__name__, why)
            state = await self.agent.aget_state(self._config(thread))
            if state.interrupts:  # nothing was applied: the approval is still waiting
                return {"estado": "pendiente_aprobacion", "conversacion": conversation, "motivo": why,
                        "detalle": "No se pudo aplicar la decisión; vuelva a intentarlo."}
            await self._save_info(thread, run_ctx.patient_id, False)
            return {
                "estado": "decision_aplicada",
                "conversacion": conversation,
                "motivo": why,
                "respuesta": "La decisión se aplicó, pero el asistente no pudo redactar la respuesta final.",
            }
        return await self._package(output, run_ctx, thread, conversation)
