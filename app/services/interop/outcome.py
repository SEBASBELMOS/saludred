"""FHIR ``OperationOutcome``: how the receiver tells the sender what to correct."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import JSONResponse

FHIR_JSON = "application/fhir+json"


@dataclass(frozen=True)
class Issue:
    """One problem found. ``code`` is a FHIR issue-type (``value``, ``code-invalid``...)."""

    code: str
    diagnostics: str
    expression: tuple[str, ...] = field(default_factory=tuple)
    severity: str = "error"

    def as_fhir(self) -> dict[str, Any]:
        issue: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "diagnostics": self.diagnostics,
        }
        if self.expression:
            issue["expression"] = list(self.expression)
        return issue


def operation_outcome(issues: list[Issue] | tuple[Issue, ...]) -> dict[str, Any]:
    return {"resourceType": "OperationOutcome", "issue": [i.as_fhir() for i in issues]}


def respond(
    status_code: int,
    issues: list[Issue] | tuple[Issue, ...],
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=operation_outcome(issues),
        media_type=FHIR_JSON,
        headers=headers,
    )


def informational(status_code: int, diagnostics: str) -> JSONResponse:
    """A success answer (201/202) that is also an OperationOutcome."""

    body = operation_outcome([Issue("informational", diagnostics, severity="information")])
    return JSONResponse(status_code=status_code, content=body, media_type=FHIR_JSON)
