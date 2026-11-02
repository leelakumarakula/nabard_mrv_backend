"""Programme-configurable rules: demographic field validation, category assignment, checklists and feature status.

Configuration starts empty or *proposed*; the platform never invents eligibility, selection or verification rules.
"""

from datetime import date
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.models import (
    BeneficiaryCategory,
    DemographicField,
    Farmer,
    FarmerCategoryAssignment,
    GeoUnit,
    IndicatorDefinition,
    ProgrammeDecision,
    ReportTemplate,
)
from app.services.settings import get_setting

CHECKLIST_SETTINGS = {
    "demonstration_site": "demonstration.selection_criteria",
    "pest_surveillance_cluster": "surveillance.cluster_criteria",
    "verification_visit": "verification.visit_checklist",
}


def checklist(db: Session, kind: str) -> list[dict[str, Any]]:
    return list(get_setting(db, CHECKLIST_SETTINGS[kind]) or [])


def missing_required(db: Session, kind: str, answers: dict[str, Any] | None, flag: str) -> list[str]:
    """Labels of required checklist items not answered positively (empty when nothing is configured)."""
    answers = answers or {}
    return [item["label"] for item in checklist(db, kind)
            if item.get("required") and not (answers.get(item["code"]) or {}).get(flag)]


def clean_assessment(db: Session, kind: str, answers: dict[str, Any] | None, flag: str) -> dict[str, Any]:
    known = {item["code"] for item in checklist(db, kind)}
    unknown = set(answers or {}) - known
    if unknown:
        raise AppError(422, "unknown_criteria", f"Not configured: {', '.join(sorted(unknown))}")
    return {code: {flag: bool(v.get(flag)), "note": v.get("note")} for code, v in (answers or {}).items()}


# -- demographics ----------------------------------------------------------------------------------------------------

def demographic_fields(db: Session, include_retired: bool = False) -> list[DemographicField]:
    stmt = select(DemographicField).order_by(DemographicField.sort_order, DemographicField.label)
    if not include_retired:
        stmt = stmt.where(DemographicField.status != "retired")
    return list(db.scalars(stmt))


def validate_demographics(db: Session, current: CurrentUser, farmer: Farmer | None, values: dict[str, Any],
                          consent_status: str) -> dict[str, Any]:
    fields = {f.code: f for f in demographic_fields(db)}
    clean: dict[str, Any] = dict((farmer.demographics or {}) if farmer else {})
    errors = []
    for code, value in values.items():
        field = fields.get(code)
        if field is None:
            errors.append(f"Unknown demographic field '{code}'")
            continue
        if field.is_sensitive and not current.has("farmer.read_sensitive"):
            raise AppError(403, "forbidden", "You are not permitted to record sensitive demographic attributes")
        if value is None or value == "":
            clean.pop(code, None)
            continue
        if field.requires_consent and consent_status != "given":
            errors.append(f"'{field.label}' can only be recorded after consent is given")
            continue
        if field.field_type == "select" and value not in (field.options or []):
            errors.append(f"'{field.label}' must be one of: {', '.join(field.options or [])}")
        elif field.field_type == "boolean" and not isinstance(value, bool):
            errors.append(f"'{field.label}' must be true or false")
        elif field.field_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"'{field.label}' must be a number")
        elif field.field_type == "date":
            try:
                date.fromisoformat(str(value))
            except ValueError:
                errors.append(f"'{field.label}' must be a date")
        clean[code] = value
    if errors:
        raise AppError(422, "invalid_demographics", "; ".join(errors))
    return clean


def visible_demographics(db: Session, current: CurrentUser, values: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    if not values:
        return {}, False
    if current.has("farmer.read_sensitive"):
        return dict(values), False
    sensitive = {f.code for f in demographic_fields(db, include_retired=True) if f.is_sensitive}
    return {k: v for k, v in values.items() if k not in sensitive}, bool(sensitive & set(values))


# -- beneficiary categories ------------------------------------------------------------------------------------------

def category_by_code(db: Session, code: str) -> BeneficiaryCategory:
    category = db.scalar(select(BeneficiaryCategory).where(BeneficiaryCategory.code == code))
    if category is None or category.status == "retired":
        raise AppError(422, "unknown_category", f"Unknown or retired beneficiary category '{code}'")
    return category


def assignments(db: Session, farmer_ids: list) -> dict[Any, list[FarmerCategoryAssignment]]:
    out: dict[Any, list[FarmerCategoryAssignment]] = {}
    if not farmer_ids:
        return out
    for a in db.scalars(select(FarmerCategoryAssignment).where(FarmerCategoryAssignment.farmer_id.in_(farmer_ids),
                                                               FarmerCategoryAssignment.status == "active")).unique():
        out.setdefault(a.farmer_id, []).append(a)
    return out


# -- feature status --------------------------------------------------------------------------------------------------

def feature_status(db: Session) -> dict[str, Any]:
    """Implemented / configurable / awaiting-approval view, with live counts of what is still pending."""

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    indicators_total = count(select(func.count()).select_from(IndicatorDefinition).where(IndicatorDefinition.status != "retired"))
    indicators_approved = count(select(func.count()).select_from(IndicatorDefinition).where(IndicatorDefinition.status == "approved"))
    categories_total = count(select(func.count()).select_from(BeneficiaryCategory).where(BeneficiaryCategory.status != "retired"))
    categories_approved = count(select(func.count()).select_from(BeneficiaryCategory).where(BeneficiaryCategory.status == "approved"))
    fields_total = count(select(func.count()).select_from(DemographicField).where(DemographicField.status != "retired"))
    fields_approved = count(select(func.count()).select_from(DemographicField).where(DemographicField.status == "approved"))
    geo_pending = list(db.scalars(select(GeoUnit.name).where(GeoUnit.requires_confirmation == true())))
    templates_awaiting = list(db.scalars(select(ReportTemplate.name).where(ReportTemplate.status == "awaiting_official_template")))
    decisions_pending = count(select(func.count()).select_from(ProgrammeDecision).where(ProgrammeDecision.status == "pending"))
    checklists = {kind: len(checklist(db, kind)) for kind in CHECKLIST_SETTINGS}

    def item(area: str, feature: str, status: str, detail: str) -> dict[str, str]:
        return {"area": area, "feature": feature, "status": status, "detail": detail}

    def approval(done: int, total: int, when_done: str = "configurable") -> str:
        return "awaiting_approval" if done < total else when_done

    endline = "configurable" if get_setting(db, "reports.endline_methodology_approved") else "awaiting_approval"

    return {
        "summary": {"pending_decisions": decisions_pending, "indicators_approved": indicators_approved,
                    "indicators_total": indicators_total},
        "features": [
            item("Programme", "Programme versions, versioned targets, reporting periods", "implemented",
                 "Targets stored as proposed/approved/revised records"),
            item("Programme", "State / district / cluster geography", "configurable",
                 f"Awaiting confirmation: {', '.join(geo_pending)}" if geo_pending else "All geography confirmed"),
            item("Registry", "Households, farmers, farms, plots, consent, enrolment", "implemented", "Stable IDs, deduplicated codes"),
            item("Inclusion", "Beneficiary categories (e.g. women, tribal community)", approval(categories_approved, categories_total),
                 f"{categories_approved}/{categories_total} category definitions approved; membership assigned manually"),
            item("Inclusion", "Demographic data fields", approval(fields_approved, fields_total),
                 f"{fields_approved}/{fields_total} fields approved; sensitive fields need farmer.read_sensitive"),
            item("Workstreams", "Seven workstream modules and record types", "implemented", "Record types drive forms and validation"),
            item("Field", "Demonstration sites and visits", "implemented",
                 f"Selection criteria configured: {checklists['demonstration_site']}"),
            item("Field", "Pest surveillance clusters and household membership", "implemented",
                 f"Cluster criteria configured: {checklists['pest_surveillance_cluster']}"),
            item("Field", "Community resource persons", "implemented", "Competency method pending approval"),
            item("MRV", "Indicator catalogue, configurable formulas and preview", "configurable",
                 "Formulas combine whitelisted aggregation methods; preview results are never stored"),
            item("MRV", "Indicator calculations (stored results)", approval(indicators_approved, indicators_total, "implemented"),
                 f"{indicators_approved}/{indicators_total} definitions approved; others only produce PROVISIONAL results if enabled"),
            item("MRV", "Internal review and independent decisions", "implemented", "Separation of duties configurable"),
            item("MRV", "Independent verification sampling", "configurable",
                 f"Visit checklist items configured: {checklists['verification_visit']}; sampling methodology pending approval"),
            item("Reporting", "Internal dashboards, configurable report templates, Excel/PDF/CSV exports", "implemented", ""),
            item("Reporting", "Official NABARD CCF-ID template", "awaiting_approval",
                 f"Awaiting official template: {', '.join(templates_awaiting) or 'none registered'}"),
            item("Reporting", "Endline / impact assessment", endline, "Requires an approved endline methodology"),
            item("Integrations", "Adapter framework, mappings, identity maps, quarantine, replay", "implemented", ""),
            item("Integrations", "Live FarmFuture / IMD / satellite / IoT feeds", "awaiting_approval",
                 "Mock adapters only until owners confirm API contracts and access"),
            item("Platform", "Offline field use, maps/geospatial layers", "not_started",
                 "Needs confirmed requirements and licensed datasets"),
        ],
    }
