"""What the harness fixes for each request. The model never chooses any of it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core import authz
from app.models.enums import RoleCode

# Contract names (/auth/me) of the roles that may use the agent and approve its writes.
CLINICAL_ROLE_NAMES = frozenset(authz.CONTRACT_ROLE_NAMES[r] for r in authz.AI_REVIEWERS)
# The referral rules of R08: only the medico creates referrals.
REFERRING_ROLE = authz.CONTRACT_ROLE_NAMES[RoleCode.IPS_CLINICAL_OPERATOR]
# Tools hidden from every other role.
REFERRING_ONLY_TOOLS = frozenset({"proponer_remision", "listar_especialistas"})


@dataclass(frozen=True)
class AgentContext:
    """Runtime context of one agent run.

    ``patient_id`` is the real id and stays on the server; the model only ever
    sees ``patient_alias`` (the project's pseudonym).
    """

    user_id: uuid.UUID
    username: str
    role: str
    patient_id: uuid.UUID | None = None
    patient_alias: str | None = None
    # Caller's address, for the audit and security logs of what the tools do.
    client_ip: str | None = None


def preferences_namespace(user_id: uuid.UUID | str) -> tuple[str, str]:
    """Long-term store namespace of one user (labels may not contain dots)."""

    return ("preferencias", str(user_id))
