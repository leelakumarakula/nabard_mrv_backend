"""Programme configuration: beneficiary categories, demographic fields, checklists, report templates, decisions."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import BeneficiaryCategory, DemographicField, ProgrammeDecision, ReportTemplate, SystemSetting
from app.schemas.config import (
    REPORT_SECTIONS,
    CategoryIn,
    CategoryOut,
    CategoryUpdate,
    ChecklistItem,
    DecisionIn,
    DecisionOut,
    DecisionUpdate,
    DemographicFieldIn,
    DemographicFieldOut,
    DemographicFieldUpdate,
    TemplateIn,
    TemplateOut,
    TemplateUpdate,
)
from app.services import audit, crud
from app.services.configuration import CHECKLIST_SETTINGS, checklist, feature_status
from app.services.settings import get_setting

router = APIRouter(tags=["configuration"])


@router.get("/beneficiary-categories", response_model=list[CategoryOut])
def list_categories(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list:
    stmt = select(BeneficiaryCategory).order_by(BeneficiaryCategory.sort_order, BeneficiaryCategory.name)
    if not current.has("farmer.read_sensitive"):
        stmt = stmt.where(BeneficiaryCategory.is_sensitive == false())
    return list(db.scalars(stmt))


@router.post("/beneficiary-categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> BeneficiaryCategory:
    obj = crud.create(db, current, BeneficiaryCategory, body.model_dump(), "beneficiary_category")
    db.commit()
    return obj


@router.patch("/beneficiary-categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryUpdate, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> BeneficiaryCategory:
    obj = crud.update(db, current, crud.get_or_404(db, BeneficiaryCategory, category_id, "Category"), body, "beneficiary_category")
    db.commit()
    return obj


@router.get("/demographic-fields", response_model=list[DemographicFieldOut])
def list_fields(include_retired: bool = False, current: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)) -> list:
    stmt = select(DemographicField).order_by(DemographicField.sort_order, DemographicField.label)
    if not include_retired:
        stmt = stmt.where(DemographicField.status != "retired")
    if not current.has("farmer.read_sensitive"):
        stmt = stmt.where(DemographicField.is_sensitive == false())
    return list(db.scalars(stmt))


@router.post("/demographic-fields", response_model=DemographicFieldOut, status_code=201)
def create_field(body: DemographicFieldIn, current: CurrentUser = Depends(require("programme.manage")),
                 db: Session = Depends(get_db)) -> DemographicField:
    obj = crud.create(db, current, DemographicField, body.model_dump(), "demographic_field")
    db.commit()
    return obj


@router.patch("/demographic-fields/{field_id}", response_model=DemographicFieldOut)
def update_field(field_id: uuid.UUID, body: DemographicFieldUpdate, current: CurrentUser = Depends(require("programme.manage")),
                 db: Session = Depends(get_db)) -> DemographicField:
    obj = crud.update(db, current, crud.get_or_404(db, DemographicField, field_id, "Field"), body, "demographic_field")
    db.commit()
    return obj


@router.get("/configuration/checklists")
def get_checklists(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Configured selection criteria and verification checklist (empty until the programme owner defines them)."""
    return {kind: {"setting": key, "items": checklist(db, kind)} for kind, key in CHECKLIST_SETTINGS.items()}


@router.put("/configuration/checklists/{kind}")
def set_checklist(kind: str, items: list[ChecklistItem], current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> dict:
    if kind not in CHECKLIST_SETTINGS:
        raise AppError(404, "not_found", f"Unknown checklist '{kind}'")
    codes = [i.code for i in items]
    if len(codes) != len(set(codes)):
        raise AppError(422, "duplicate_codes", "Checklist codes must be unique")
    setting = db.get(SystemSetting, CHECKLIST_SETTINGS[kind])
    before = setting.value
    setting.value = [i.model_dump() for i in items]
    setting.updated_by_id = current.id
    audit.record(db, current, "update", "system_setting", setting.key, before={"value": before}, after={"value": setting.value})
    db.commit()
    return {"setting": setting.key, "items": setting.value}


@router.get("/report-templates", response_model=list[TemplateOut])
def list_templates(current: CurrentUser = Depends(require("report.read")), db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(ReportTemplate).where(ReportTemplate.status != "retired").order_by(ReportTemplate.name)))


@router.get("/report-templates/sections")
def list_sections(current: CurrentUser = Depends(require("report.read"))) -> list[str]:
    return REPORT_SECTIONS


@router.post("/report-templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> ReportTemplate:
    obj = crud.create(db, current, ReportTemplate, body.model_dump(), "report_template")
    db.commit()
    return obj


@router.patch("/report-templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: uuid.UUID, body: TemplateUpdate, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> ReportTemplate:
    obj = crud.update(db, current, crud.get_or_404(db, ReportTemplate, template_id, "Template"), body, "report_template")
    db.commit()
    return obj


@router.get("/programme-decisions", response_model=list[DecisionOut])
def list_decisions(status: str | None = None, current: CurrentUser = Depends(require("programme.read")),
                   db: Session = Depends(get_db)) -> list:
    stmt = select(ProgrammeDecision).order_by(ProgrammeDecision.status.desc(), ProgrammeDecision.code)
    if status:
        stmt = stmt.where(ProgrammeDecision.status == status)
    return list(db.scalars(stmt))


@router.post("/programme-decisions", response_model=DecisionOut, status_code=201)
def create_decision(body: DecisionIn, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> ProgrammeDecision:
    obj = crud.create(db, current, ProgrammeDecision, body.model_dump(), "programme_decision")
    db.commit()
    return obj


@router.patch("/programme-decisions/{decision_id}", response_model=DecisionOut)
def update_decision(decision_id: uuid.UUID, body: DecisionUpdate, current: CurrentUser = Depends(require("programme.manage")),
                    db: Session = Depends(get_db)) -> ProgrammeDecision:
    obj = crud.update(db, current, crud.get_or_404(db, ProgrammeDecision, decision_id, "Decision"), body, "programme_decision")
    db.commit()
    return obj


@router.get("/system/feature-status")
def get_feature_status(current: CurrentUser = Depends(require("programme.read")), db: Session = Depends(get_db)) -> dict:
    out = feature_status(db)
    out["endline_methodology_approved"] = bool(get_setting(db, "reports.endline_methodology_approved"))
    return out
