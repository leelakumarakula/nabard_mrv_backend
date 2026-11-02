"""Excel and PDF renderings of a report snapshot. Every section keeps its status labels and the disclaimer."""

import io
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SECTION_TITLES = {
    "planning_targets": "Planning targets (proposal figures – not achievements)",
    "progress_vs_targets": "Progress against targets",
    "funnel": "Planned → enrolled → reached → participating → adopted → verified",
    "coverage": "Registry coverage",
    "inclusion": "Inclusion (aggregate)",
    "demonstrations": "Demonstration sites",
    "surveillance": "Pest surveillance",
    "indicator_results": "Indicator results",
    "data_quality": "Data quality",
    "module_activity": "Workstream activity",
    "mrv": "Review status",
    "verification": "Independent verification",
}


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def section_tables(content: dict[str, Any]) -> list[tuple[str, list[str], list[list[Any]]]]:
    """Flatten report content into (title, header, rows) tables shared by the Excel and PDF renderers."""
    tables: list[tuple[str, list[str], list[list[Any]]]] = []
    meta = content.get("meta", {})
    tables.append(("Report", ["Item", "Value"], [[k, _cell(v)] for k, v in meta.items()]))
    for key, title in SECTION_TITLES.items():
        data = content.get(key)
        if not data:
            continue
        if key == "progress_vs_targets":
            rows = [[p["metric_name"], p["target_value"], p["unit"], f"{p['target_status']} v{p['target_version']}",
                     (p.get("latest_result") or {}).get("value", ""),
                     (p.get("latest_result") or {}).get("verification_status", "not calculated"),
                     "PROVISIONAL" if (p.get("latest_result") or {}).get("provisional") else ""] for p in data]
            tables.append((title, ["Target", "Planned", "Unit", "Target status", "Latest value", "Verification", "Note"], rows))
        elif key == "funnel":
            rows = [[stage, n, data["definitions"].get(stage, "")] for stage, n in data["stages"].items()]
            tables.append((title, ["Stage", f"Count ({data['unit']})", "Definition"], rows))
        elif key == "inclusion":
            rows = [[c["name"], c["definition_status"], "yes" if c["sensitive"] else "", c["enrolled_members"],
                     c["enrolled_households"], c["members_with_observed_adoption"], c["members_with_verified_adoption"]]
                    for c in data["categories"]]
            tables.append((title, ["Category", "Definition", "Sensitive", "Enrolled", "Households", "Observed adoption",
                                   "Verified adoption"], rows))
            crp = data["community_resource_persons"]
            tables.append(("Community resource persons", ["Item", "Value"],
                           [["Trained", crp["trained"]], ["Women trained", crp["women_trained"]],
                            ["By status", _cell(crp["by_status"])]]))
        elif key in ("demonstrations", "surveillance") and isinstance(data, dict):
            list_key = "sites" if key == "demonstrations" else "clusters"
            rows_data = data.get(list_key, [])
            if rows_data:
                header = [k for k in rows_data[0] if k not in ("is_demo",)]
                tables.append((title, header, [[_cell(r.get(h)) for h in header] for r in rows_data]))
            extra = [[k, _cell(v)] for k, v in data.items() if k != list_key]
            if extra:
                tables.append((f"{title} – summary", ["Item", "Value"], extra))
        elif key == "verification":
            samples = data.get("samples", [])
            if samples:
                header = ["title", "entity_type", "selection_method", "frame_size", "sample_size", "status", "results",
                          "conclusion"]
                tables.append((title, header, [[_cell(s.get(h)) for h in header] for s in samples]))
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            header = list(data[0].keys())
            tables.append((title, header, [[_cell(r.get(h)) for h in header] for r in data]))
        elif isinstance(data, dict):
            tables.append((title, ["Item", "Value"], [[k, _cell(v)] for k, v in data.items()]))
    return tables


def to_xlsx(content: dict[str, Any]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    header_fill = PatternFill("solid", fgColor="DCEFDC")
    for title, header, rows in section_tables(content):
        name = "".join(ch for ch in title if ch not in "[]:*?/\\")[:28] or "Sheet"
        base, n = name, 2
        while name in used:
            name, n = f"{base[:25]} {n}", n + 1
        used.add(name)
        ws = wb.create_sheet(name)
        ws.append([title])
        ws["A1"].font = Font(bold=True, size=12)
        ws.append(header)
        for cell in ws[2]:
            cell.font, cell.fill = Font(bold=True), header_fill
        for row in rows:
            ws.append([_cell(v) for v in row])
        for column in ws.columns:
            width = max(len(str(c.value or "")) for c in column[1:]) if len(column) > 1 else 10
            ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
            for c in column:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    disclaimer = wb.create_sheet("Disclaimer", 0)
    disclaimer.append([content.get("meta", {}).get("disclaimer", "")])
    if content.get("meta", {}).get("template_note"):
        disclaimer.append([content["meta"]["template_note"]])
    disclaimer.column_dimensions["A"].width = 120
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _pdf_text(value: Any) -> str:
    """Built-in PDF fonts are cp1252; replace characters outside it (e.g. ₹) rather than dropping them."""
    text = str(_cell(value)).replace("₹", "INR ").replace("→", "->").replace("≥", ">=").replace("≤", "<=")
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_pdf(content: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=_pdf_text(content.get("meta", {}).get("report_type_label", "Report")))
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=7.5, leading=9)
    meta = content.get("meta", {})
    story: list[Any] = [
        Paragraph(_pdf_text(f"{meta.get('programme_name', '')} – {meta.get('report_type_label', '')}"), styles["Title"]),
        Paragraph(_pdf_text(f"{meta.get('scope_label', '')} · {meta.get('period_start', '')} to {meta.get('period_end', '')}"
                            f" · generated {meta.get('generated_at', '')[:19]}"), styles["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph(_pdf_text(meta.get("disclaimer", "")), small),
    ]
    if meta.get("template_note"):
        story.append(Paragraph(_pdf_text(meta["template_note"]), small))
    if meta.get("contains_demo_data"):
        story.append(Paragraph("<b>Contains DEMO / SYNTHETIC data – not real beneficiaries or achievements.</b>", small))
    for title, header, rows in section_tables(content)[1:]:
        story += [Spacer(1, 5 * mm), Paragraph(_pdf_text(title), styles["Heading3"])]
        data = [[Paragraph(f"<b>{_pdf_text(h)}</b>", small) for h in header]]
        data += [[Paragraph(_pdf_text(v), small) for v in row] for row in rows] or [[Paragraph("—", small)]]
        table = Table(data, repeatRows=1, colWidths=[doc.width / len(header)] * len(header))
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEFDC")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
    doc.build(story)
    return buffer.getvalue()
