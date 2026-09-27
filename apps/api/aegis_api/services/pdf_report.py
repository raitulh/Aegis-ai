"""Render an audit report to PDF (reportlab)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from aegis_api.models import Report

INK = colors.HexColor("#0B0E14")
ACCENT = colors.HexColor("#3B82F6")
MUTED = colors.HexColor("#6B7280")
SEVERITY_COLORS = {
    "critical": colors.HexColor("#B91C1C"),
    "high": colors.HexColor("#DC2626"),
    "medium": colors.HexColor("#D97706"),
    "low": colors.HexColor("#2563EB"),
    "info": colors.HexColor("#6B7280"),
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("T", parent=base["Title"], textColor=INK, fontSize=22, spaceAfter=4),
        "sub": ParagraphStyle("S", parent=base["Normal"], textColor=MUTED, fontSize=10, spaceAfter=12),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], textColor=INK, fontSize=13, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("B", parent=base["Normal"], textColor=INK, fontSize=9.5, leading=14),
        "small": ParagraphStyle("SM", parent=base["Normal"], textColor=MUTED, fontSize=8, leading=11),
    }


def render_report_pdf(report: Report) -> bytes:
    content: dict[str, Any] = report.content
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.8 * inch,
        bottomMargin=0.7 * inch,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        title=report.title,
    )
    story: list[Any] = []
    sysd = content.get("system_description", {})
    summary = content.get("executive_summary", {})

    story.append(Paragraph("AEGIS AI — AI Assurance Report", styles["title"]))
    story.append(
        Paragraph(
            f"{sysd.get('name', 'System')} · {sysd.get('environment', '')} · generated {content.get('generated_at', '')[:10]}",
            styles["sub"],
        )
    )
    story.append(HRFlowable(width="100%", color=ACCENT, thickness=2))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Executive Summary", styles["h2"]))
    story.append(Paragraph(summary.get("headline", ""), styles["body"]))
    dims = summary.get("dimensions", {})
    if dims:
        rows = [["Dimension", "Score (higher is better)"]] + [[k.title(), f"{v:.0f}"] for k, v in dims.items()]
        table = Table(rows, colWidths=[3 * inch, 2.5 * inch])
        table.setStyle(_table_style())
        story.append(Spacer(1, 6))
        story.append(table)

    story.append(Paragraph("Detailed Findings", styles["h2"]))
    findings = content.get("detailed_findings", [])
    if findings:
        rows = [["#", "Title", "Severity", "Risk", "Occ."]]
        for f in findings[:40]:
            rows.append(
                [
                    str(f["number"]),
                    _truncate(f["title"], 52),
                    f["severity"].title(),
                    f["risk_level"].title(),
                    f"{f['occurrences']}/{f['sample_size']}",
                ]
            )
        table = Table(rows, colWidths=[0.5 * inch, 3.3 * inch, 0.9 * inch, 0.8 * inch, 0.6 * inch], repeatRows=1)
        table.setStyle(_finding_table_style(findings))
        story.append(table)
    else:
        story.append(Paragraph("No findings were identified in this audit.", styles["body"]))

    story.append(Paragraph("Methodology", styles["h2"]))
    story.append(Paragraph(content.get("methodology", {}).get("principle", ""), styles["body"]))

    story.append(Paragraph("Limitations", styles["h2"]))
    for limitation in content.get("limitations", []):
        story.append(Paragraph(f"• {limitation}", styles["small"]))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=MUTED, thickness=0.5))
    meta = content.get("audit_metadata", {})
    story.append(
        Paragraph(
            f"Audit ID {meta.get('audit_id', '')} · Evidence head {content.get('evidence_integrity', {}).get('head_hash', 'n/a')}",
            styles["small"],
        )
    )

    doc.build(story)
    return buf.getvalue()


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _finding_table_style(findings: list[dict[str, Any]]) -> TableStyle:
    style = _table_style()
    for i, f in enumerate(findings[:40], start=1):
        style.add("TEXTCOLOR", (2, i), (2, i), SEVERITY_COLORS.get(f["severity"], MUTED))
    return style


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
