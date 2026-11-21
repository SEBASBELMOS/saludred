"""Explicit router graph in front of the agent: clasificar -> (agente | administrativo | fuera).

The classifier is rule-based (no extra model call). Its one rule of safety: when
unsure, the question goes to the agent, whose prompt already abstains on
off-topic content. Only questions that are clearly administrative, or clearly
unrelated to care, skip the model and get a fixed answer.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

ROUTE_AGENT = "agente"
ROUTE_ADMIN = "administrativo"
ROUTE_OUT = "fuera"

ADMIN_REPLY = (
    "Esa consulta es administrativa (facturas, citas, horarios, cuentas o contraseñas) "
    "y la gestiona el área administrativa o el administrador del sistema. "
    "Yo apoyo preguntas clínicas de urgencias: triage, criticidad, camas y remisiones."
)
OUT_REPLY = (
    "Fuera de alcance: solo atiendo temas clínicos de urgencias (triage, criticidad, "
    "signos vitales, camas y remisiones)."
)

# Clinical vocabulary of SaludRed. Any hit sends the message to the agent.
_CLINICAL = re.compile(
    r"\b(triage|triaje|criticidad|critico|critica|urgencia\w*|paciente\w*|signos?|vital\w*|saturacion|"
    r"frecuencia|presion|temperatura|oxigeno|respirat\w*|cardiac\w*|sintoma\w*|diagnostic\w*|"
    r"remision\w*|remit\w*|especialista\w*|cama\w*|limpieza|traslado\w*|guia\w*|norma\w*|protocolo\w*|"
    r"resolucion|hba1c|glucosa|diabetes|hipoglucemia|dolor|fiebre|ingreso|atencion|encuentro|"
    r"hospitaliz\w*|uci|reanimacion|prioridad|riesgo|historia clinica|skill\w*|segun la guia|"
    r"convulsi\w*|codigo (rojo|azul|amarillo)|clave (roja|azul|amarilla)|fuente\w*|bibliograf\w*)\b"
)
# Clearly administrative: only used when there is no clinical vocabulary at all.
# Narrow on purpose: a bare "clave" ("clave roja") or "cita" ("cítame la fuente") is not admin.
_ADMIN = re.compile(
    r"\b(factura\w*|facturacion|cobro\w*|pago\w*|cita medica|citas medicas|cita con|cita para|"
    r"mi cita|mis citas|agendar|horario\w*|contrasena\w*|mi clave|"
    r"mi cuenta|cuenta de usuario|usuario nuevo|restablecer|correo institucional|vacaciones|nomina|"
    r"certificado laboral|carnet)\b"
)
# Clearly unrelated to care. Also only when there is no clinical vocabulary.
# "serie de" is gone ("una serie de convulsiones"): a series of films is not worth a false positive.
_OFF_TOPIC = re.compile(
    r"\b(futbol|mundial|partido|pelicula\w*|musica|cancion\w*|chiste\w*|poema\w*|"
    r"receta de cocina|cocinar|clima|horoscopo|bitcoin|criptomoneda\w*|videojuego\w*|"
    r"capital de|quien gano|presidente de|programar en|codigo en python)\b"
)


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


# A bare greeting or courtesy: the only agent turns that may answer without evidence (R23).
_PLEASANTRY = re.compile(
    r"^(hola|holi|hey|buenas|buenos dias|buenas tardes|buenas noches|saludos|gracias|muchas gracias|"
    r"ok|vale|listo|perfecto|de acuerdo|adios|chao|hasta luego|que puedes hacer|"
    r"en que me puedes ayudar|quien eres|ayuda)"
    r"( (doctor|doctora|dr|dra|asistente|agente|a todos|de nuevo))?$"
)


def is_pleasantry(message: str) -> bool:
    """True for a greeting or courtesy with no question in it ("hola", "gracias").

    The agent may answer these without citing anything; every other question that reaches
    the agent must be supported by evidence or abstain (R23).
    """

    text = re.sub(r"[^a-z0-9 ]+", " ", _fold(message))
    text = " ".join(text.split())
    return bool(text) and not _CLINICAL.search(text) and _PLEASANTRY.match(text) is not None


def classify(message: str) -> str:
    """Route name for a message. Ambiguous or empty of signals means the agent."""

    text = _fold(message)
    if _CLINICAL.search(text):
        return ROUTE_AGENT
    if _ADMIN.search(text):
        return ROUTE_ADMIN
    if _OFF_TOPIC.search(text):
        return ROUTE_OUT
    return ROUTE_AGENT


class FlowState(TypedDict, total=False):
    message: str
    conversation: str
    ctx: Any
    route: str
    result: dict[str, Any]


def build_flow(get_harness: Any):  # noqa: ANN201
    """Compile the router graph. ``get_harness`` is called lazily, per request."""

    def classify_node(state: FlowState) -> dict[str, Any]:
        return {"route": classify(state["message"])}

    async def agent_node(state: FlowState) -> dict[str, Any]:
        harness = get_harness()
        result = await harness.attend(state["ctx"], state["message"], state["conversation"])
        return {"result": result}

    def fixed(reply: str):  # noqa: ANN202
        async def node(state: FlowState) -> dict[str, Any]:
            return {
                "result": {"estado": "respondido", "conversacion": state["conversation"], "respuesta": reply}
            }

        return node

    graph = StateGraph(FlowState)
    graph.add_node("clasificar", classify_node)
    graph.add_node(ROUTE_AGENT, agent_node)
    graph.add_node(ROUTE_ADMIN, fixed(ADMIN_REPLY))
    graph.add_node(ROUTE_OUT, fixed(OUT_REPLY))
    graph.add_edge(START, "clasificar")
    graph.add_conditional_edges(
        "clasificar",
        lambda state: state["route"],
        {ROUTE_AGENT: ROUTE_AGENT, ROUTE_ADMIN: ROUTE_ADMIN, ROUTE_OUT: ROUTE_OUT},
    )
    for node in (ROUTE_AGENT, ROUTE_ADMIN, ROUTE_OUT):
        graph.add_edge(node, END)
    return graph.compile()
