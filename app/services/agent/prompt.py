"""Builds the layered system prompt from ``prompts/sistema.md`` for every model call."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.agent.context import AgentContext

TEMPLATE = (Path(__file__).parent / "prompts" / "sistema.md").read_text(encoding="utf-8")
MAX_PREFERENCES = 10
PREFERENCES_TAG = "preferencias_estilo_no_confiables"


def neutralize(text: str) -> str:
    """A stored hint cannot close the preferences tag and escape it."""

    return text.replace("<", "&lt;")


class _Safe(dict):
    def __missing__(self, key: str) -> str:  # a missing field stays as {field}
        return "{" + key + "}"


def preferences_block(preferences: list[str]) -> str:
    """The stored style hints as UNTRUSTED text: a hint can never change a rule."""

    if not preferences:
        return "(sin preferencias guardadas)"
    lines = "\n".join(f"- {neutralize(p)}" for p in preferences[:MAX_PREFERENCES])
    return f"<{PREFERENCES_TAG}>\n{lines}\n</{PREFERENCES_TAG}>"


def build_prompt(ctx: AgentContext, skills_index: str, preferences: list[str]) -> str:
    return TEMPLATE.format_map(
        _Safe(
            usuario=ctx.username,
            rol=ctx.role,
            paciente=ctx.patient_alias or "ninguno",
            fecha=date.today().isoformat(),
            skills=skills_index or "(ninguna)",
            preferencias=preferences_block(preferences),
        )
    )
