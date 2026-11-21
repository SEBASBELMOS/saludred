"""A scripted chat model and wiring for the agent tests. No network, no Groq."""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from contextlib import contextmanager
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field

from app.services.agent import db as agent_db
from app.services.agent import runtime, security
from app.services.agent.harness import Harness, build_agent

SPECIALIST_ID = re.compile(r"especialista_id=([0-9a-f-]{36})")


class ScriptedModel(BaseChatModel):
    """Imitates the FORMAT of a tool-calling LLM with keyword rules.

    Every message list it receives is kept in ``calls`` so a test can assert what
    the model was (and was not) shown.
    """

    calls: list = Field(default_factory=list)
    offered: list = Field(default_factory=list)  # tool names bound on each model call
    tool_names: list = Field(default_factory=list)
    delay: float = 0.0
    error: Any = None
    # Shared with the copies ``bind_tools`` makes: concurrent calls now / at most.
    stats: dict = Field(default_factory=lambda: {"active": 0, "max": 0})
    # Called at the start of every model call (a test can look at the world there).
    on_call: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        self.offered.append(names)
        return self.model_copy(update={"tool_names": names})

    def _steps(self, text: str, results: list[ToolMessage]):  # noqa: ANN202
        t = text.lower()
        steps: list[tuple[str, Any]] = []
        match = re.search(r"cargar skill (\S+)", t)
        if match:
            steps.append(("cargar_skill", lambda r, n=match.group(1): {"nombre": n}))
        if "recuerda que" in t:
            steps.append(("guardar_preferencia", lambda r: {"preferencia": text}))
        if "remit" in t:
            steps.append(("consultar_paciente", lambda r: {}))
            steps.append(("listar_especialistas", lambda r: {}))

            def referral_args(done: list[ToolMessage]) -> dict:
                listing = str(done[-1].content)
                wanted = re.search(r"con (\S+)", text.lower())
                lines = [ln for ln in listing.splitlines() if wanted and wanted.group(1) in ln.lower()]
                found = SPECIALIST_ID.search(lines[0] if lines else listing)
                return {
                    "especialista_id": found.group(1) if found else str(uuid.uuid4()),
                    "motivo": "Saturacion baja con dificultad respiratoria; criterio de la guia [1]",
                }

            steps.append(("proponer_remision", referral_args))
        elif "paciente" in t:
            steps.append(("consultar_paciente", lambda r: {}))
        # The skill name itself (e.g. "triage-criticidad") is not a request to search.
        if re.search(r"gu[ií]a|triage|norma|criterio", re.sub(r"cargar skill \S+", "", t)):
            steps.append(("buscar_guias", lambda r: {"pregunta": text}))
        return [s for s in steps if s[0] in self.tool_names]

    def _answer(self, messages) -> AIMessage:  # noqa: ANN001
        humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        if not humans:
            return AIMessage(content="[fake] sin pregunta")
        i = humans[-1]
        text = str(messages[i].content)
        results = [m for m in messages[i + 1 :] if isinstance(m, ToolMessage)]
        if "doble propuesta" in text.lower() and len(results) < 2:
            return self._double_proposal(results)
        steps = self._steps(text, results)
        if len(results) < len(steps):
            name, args_fn = steps[len(results)]
            call = {"name": name, "args": args_fn(results), "id": "call_" + uuid.uuid4().hex[:8],
                    "type": "tool_call"}
            return AIMessage(content="", tool_calls=[call])
        summary = " | ".join(f"{m.name}: {str(m.content)[:400]}" for m in results) or "sin herramientas"
        return AIMessage(content=f"[fake] {summary}")

    def _double_proposal(self, results: list[ToolMessage]) -> AIMessage:
        """List the specialists, then propose TWO referrals in one response."""

        if not results:
            call = {"name": "listar_especialistas", "args": {}, "id": "call_" + uuid.uuid4().hex[:8],
                    "type": "tool_call"}
            return AIMessage(content="", tool_calls=[call])
        found = SPECIALIST_ID.search(str(results[-1].content))
        specialist = found.group(1) if found else str(uuid.uuid4())
        calls = [
            {"name": "proponer_remision",
             "args": {"especialista_id": specialist, "motivo": f"Motivo {n} segun la guia [1]"},
             "id": "call_" + uuid.uuid4().hex[:8], "type": "tool_call"}
            for n in (1, 2)
        ]
        return AIMessage(content="", tool_calls=calls)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(messages))
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return ChatResult(generations=[ChatGeneration(message=self._answer(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(messages))
        if self.on_call is not None:
            self.on_call()
        self.stats["active"] += 1
        self.stats["max"] = max(self.stats["max"], self.stats["active"])
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return ChatResult(generations=[ChatGeneration(message=self._answer(messages))])
        finally:
            self.stats["active"] -= 1

    @property
    def seen_text(self) -> str:
        """Everything any model call received, as one string."""

        return "\n".join(f"{type(m).__name__}: {m.content} {getattr(m, 'tool_calls', '')}"
                         for call in self.calls for m in call)


def install(  # noqa: ANN201
    db,  # noqa: ANN001
    model: ScriptedModel | None,
    *,
    timeout_s: float = 20.0,
    single_gated_action: bool = True,
) -> Harness:
    """Wire the agent to the shared test database and a scripted model."""

    lock = threading.RLock()  # one shared SQLite connection: tools take turns

    @contextmanager
    def shared_session():  # noqa: ANN202
        with lock:
            yield db

    agent_db.set_session_factory(shared_session)
    security.blocklist.reset()  # the block list is process-wide state
    store = InMemoryStore()
    agent = (
        build_agent(model, InMemorySaver(), store, single_gated_action=single_gated_action)
        if model is not None
        else None
    )
    harness = Harness(agent, store, timeout_s=timeout_s)
    runtime.set_harness(harness)
    return harness


def uninstall() -> None:
    agent_db.set_session_factory(None)
    security.blocklist.reset()
    runtime.set_harness(None)
