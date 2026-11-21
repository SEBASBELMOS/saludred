"""R30 partner projection; the clinical RiskAssessment export stays unchanged."""

from app.models.ai import AiReport
from app.models.enums import AiReportStatus


def approved_diagnostic_report(report: AiReport, *, patient_ref: str = "") -> dict:
    """Reject unapproved evidence even when called outside the export service."""
    if report.status != AiReportStatus.APPROVED:
        raise ValueError("Only approved AI reports may be exported")
    return {
        "resourceType": "DiagnosticReport",
        "id": str(report.id),
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "11502-2",
                              "display": "Laboratory report"}],
                 "text": "Analisis de criticidad asistido por IA y aprobado por un clinico"},
        "subject": {"reference": patient_ref},
        "issued": (report.reviewed_at or report.created_at).isoformat(),
        "conclusion": f"Analisis de IA aprobado: nivel {report.level}.",
        "conclusionCode": [{"coding": [{"system": "urn:saludred:criticidad",
                                         "code": report.level}]}],
        "extension": [{"url": "urn:saludred:ai-model",
                       "valueString": report.model_version}],
    }
