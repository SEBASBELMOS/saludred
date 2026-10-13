"""Payment receipt as a PDF (fpdf2, same approach as the clinical record).

The patient's name and document are resolved from PostgreSQL by the caller at
render time; they are never stored in MongoDB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fpdf import FPDF

SERVICIOS = {
    "analisis_ia": "Analisis de IA",
    "dia_cama": "Dia cama",
    "remision": "Remision",
    "consulta": "Consulta",
    "medicamento": "Medicamento",
}
ESTADOS = {"pendiente": "PENDIENTE DE PAGO", "pagada": "PAGADA", "anulada": "ANULADA"}


def _latin(text: str | None) -> str:
    """Core PDF fonts are Latin-1; anything else degrades to '?', never crashes."""

    return (text or "").encode("latin-1", "replace").decode("latin-1")


def _fmt(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def _cop(value: int) -> str:
    return f"${value:,} COP".replace(",", ".")


def render_pdf(
    document: dict[str, Any],
    *,
    patient_name: str | None,
    patient_document: str | None,
    eps_name: str | None,
    ips_name: str | None,
) -> bytes:
    pdf = FPDF(format="A4")
    # Uncompressed on purpose: the text stays readable to any PDF text extractor.
    pdf.set_compression(False)
    pdf.set_title("Recibo de factura")
    pdf.set_author("SaludRed")
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    def line(text: str, style: str = "") -> None:
        pdf.set_font("Helvetica", style, 10)
        pdf.multi_cell(0, 5.5, _latin(text), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Recibo de factura", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Factura {document['id']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Fecha: {_fmt(document['fecha'])} UTC", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Paciente", new_x="LMARGIN", new_y="NEXT")
    line(patient_name or "(paciente no disponible)", "B")
    if patient_document:
        line(f"Documento: {patient_document}")
    line(f"EPS: {eps_name or '-'}")
    if ips_name:
        line(f"IPS: {ips_name}")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Detalle", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    widths = (72, 22, 40, 40)
    for width, title in zip(widths, ("Concepto", "Cantidad", "Valor unitario", "Subtotal"), strict=True):
        pdf.cell(width, 7, title, border="B")
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    concept = f"{SERVICIOS.get(document['servicio'], document['servicio'])}: {document['descripcion']}"
    x, y = pdf.get_x(), pdf.get_y()
    pdf.multi_cell(widths[0], 5.5, _latin(concept), new_x="RIGHT", new_y="TOP")
    bottom = pdf.get_y() + 5.5
    pdf.set_xy(x + widths[0], y)
    pdf.cell(widths[1], 5.5, str(document["cantidad"]))
    pdf.cell(widths[2], 5.5, _cop(document["valor_unitario"]))
    pdf.cell(widths[3], 5.5, _cop(document["valor"]))
    pdf.set_xy(x, max(bottom, y + 5.5) + 2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 9, f"Total: {_cop(document['valor'])}", new_x="LMARGIN", new_y="NEXT")
    line(f"Estado de pago: {ESTADOS.get(document['estado_pago'], document['estado_pago'])}", "B")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Historial de estados", new_x="LMARGIN", new_y="NEXT")
    for entry in document.get("historial", []):
        note = f" - {entry['nota']}" if entry.get("nota") else ""
        line(f"{_fmt(entry['fecha'])}  {entry['estado']}{note}")

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0, 4,
        _latin(
            "Valores en pesos colombianos (COP). Las tarifas del proyecto son ilustrativas, "
            "no corresponden a un manual tarifario oficial."
        ),
        new_x="LMARGIN", new_y="NEXT",
    )
    return bytes(pdf.output())
