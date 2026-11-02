import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict, Forbidden, InvalidState
from app.db.session import get_db
from app.models import BaselineRecord, IndicatorDefinition, MonitoringRecord, Programme
from app.schemas.common import Page, PageParams, page_of, paginate
from app.schemas.indicator import (
    BaselineIn,
    BaselineOut,
    BaselineUpdate,
    ExceptionResolutionIn,
    MonitoringIn,
    MonitoringOut,
    MonitoringUpdate,
)
from app.schemas.mrv import ReviewTaskOut, SubmitForReviewIn
from app.services import crud, mrv
from app.services.locate import can_edit_own, resolve
from app.services.scope import apply_scope
from app.services.units import UnitConversionError, can_convert, convert, normalize_unit
from app.services.validation import validate_baseline, validate_monitoring

router = APIRouter(tags=["baselines & monitoring"])

EDITABLE_REVIEW = {"not_submitted", "correction_requested"}


def _normalise_value(indicator: IndicatorDefinition, data: dict) -> dict:
    """Convert to the indicator unit when an approved conversion exists; keep the original value/unit."""
    unit = normalize_unit(data.get("unit"))
    if unit and unit != normalize_unit(indicator.unit) and can_convert(unit, indicator.unit):
        try:
            data["original_value"], data["original_unit"] = data["value"], unit
            data["value"] = convert(data["value"], unit, indicator.unit)
            data["unit"] = indicator.unit
        except UnitConversionError as exc:
            raise AppError(422, "unit_conversion", str(exc)) from exc
    return data


# -- baselines -----------------------------------------------------------------------------------------------------

def _scoped_baseline(db: Session, baseline_id: uuid.UUID, current: CurrentUser) -> BaselineRecord:
    rec = crud.get_or_404(db, BaselineRecord, baseline_id, "Baseline")
    if rec.scope_type != "programme":
        current.ensure_access(rec.geo_unit_id, rec.organisation_id)
    return rec


@router.get("/baselines", response_model=Page[BaselineOut])
def list_baselines(params: PageParams = Depends(), indicator_id: uuid.UUID | None = None, programme_id: uuid.UUID | None = None,
                   scope_type: str | None = None, validation_status: str | None = None,
                   verification_status: str | None = None, current: CurrentUser = Depends(require("baseline.read")),
                   db: Session = Depends(get_db)) -> dict:
    stmt = select(BaselineRecord).order_by(BaselineRecord.created_at.desc())
    for column, value in ((BaselineRecord.indicator_id, indicator_id), (BaselineRecord.programme_id, programme_id),
                          (BaselineRecord.scope_type, scope_type), (BaselineRecord.validation_status, validation_status),
                          (BaselineRecord.verification_status, verification_status)):
        if value:
            stmt = stmt.where(column == value)
    if not current.all_scope:
        stmt = apply_scope(stmt, current, BaselineRecord.geo_unit_id, BaselineRecord.organisation_id)
    return page_of(*paginate(db, stmt, params), params, BaselineOut)


@router.post("/baselines", response_model=BaselineOut, status_code=201)
def create_baseline(body: BaselineIn, current: CurrentUser = Depends(require("baseline.manage")),
                    db: Session = Depends(get_db)) -> BaselineRecord:
    indicator = crud.get_or_404(db, IndicatorDefinition, body.indicator_id, "Indicator")
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    data = body.model_dump()
    if body.scope_type in ("farmer", "plot"):
        loc = resolve(db, current, farmer_id=body.farmer_id, farm_plot_id=body.farm_plot_id)
        data.update(farmer_id=loc.farmer_id, farm_plot_id=loc.farm_plot_id, geo_unit_id=loc.geo_unit_id,
                    organisation_id=loc.organisation_id)
    elif body.scope_type != "programme":
        current.ensure_access(body.geo_unit_id, body.organisation_id)
    elif not current.all_scope:
        raise Forbidden("Programme-level baselines need access to every geography")
    if not can_convert(body.unit, indicator.unit):
        raise AppError(422, "unit_mismatch", f"Unit '{body.unit}' cannot be converted to the indicator unit '{indicator.unit}'")
    if normalize_unit(body.unit) != normalize_unit(indicator.unit):
        data["value"] = convert(body.value, body.unit, indicator.unit)
        data["unit"] = indicator.unit
        data["notes"] = ((data.get("notes") or "") + f" [Converted from {body.value} {body.unit}]").strip()
    obj = crud.create(db, current, BaselineRecord, {**data, "submission_status": "draft"}, "baseline_record")
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/baselines/{baseline_id}", response_model=BaselineOut)
def get_baseline(baseline_id: uuid.UUID, current: CurrentUser = Depends(require("baseline.read")),
                 db: Session = Depends(get_db)) -> BaselineRecord:
    return _scoped_baseline(db, baseline_id, current)


@router.patch("/baselines/{baseline_id}", response_model=BaselineOut)
def update_baseline(baseline_id: uuid.UUID, body: BaselineUpdate, current: CurrentUser = Depends(require("baseline.manage")),
                    db: Session = Depends(get_db)) -> BaselineRecord:
    rec = _scoped_baseline(db, baseline_id, current)
    if rec.review_status not in EDITABLE_REVIEW or rec.verification_status != "unverified":
        raise Conflict("Baselines under review or with a verification decision cannot be edited")
    crud.update(db, current, rec, {**body.model_dump(exclude_unset=True), "validation_status": "pending_validation",
                                   "submission_status": "draft"}, "baseline_record")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/baselines/{baseline_id}/submit", response_model=BaselineOut)
def submit_baseline(baseline_id: uuid.UUID, current: CurrentUser = Depends(require("baseline.manage")),
                    db: Session = Depends(get_db)) -> BaselineRecord:
    rec = _scoped_baseline(db, baseline_id, current)
    if rec.review_status not in EDITABLE_REVIEW:
        raise Conflict("Baseline is already in review")
    outcome = validate_baseline(db, rec)
    crud.update(db, current, rec, {"submission_status": "submitted", "validation_status": outcome.status,
                                   "validation_messages": outcome.messages}, "baseline_record", action="submit")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/baselines/{baseline_id}/submit-for-review", response_model=ReviewTaskOut)
def baseline_for_review(baseline_id: uuid.UUID, body: SubmitForReviewIn,
                        current: CurrentUser = Depends(require("baseline.manage")), db: Session = Depends(get_db)):
    _scoped_baseline(db, baseline_id, current)
    task = mrv.submit_for_review(db, current, "baseline_record", baseline_id, body.reviewer_id, body.due_date, body.note)
    db.commit()
    return task


# -- monitoring records --------------------------------------------------------------------------------------------

def _scoped_monitoring(db: Session, record_id: uuid.UUID, current: CurrentUser) -> MonitoringRecord:
    rec = crud.get_or_404(db, MonitoringRecord, record_id, "Monitoring record")
    current.ensure_access(rec.geo_unit_id, rec.organisation_id)
    return rec


@router.get("/monitoring-records", response_model=Page[MonitoringOut])
def list_monitoring(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, indicator_id: uuid.UUID | None = None,
                    farmer_id: uuid.UUID | None = None, farm_plot_id: uuid.UUID | None = None,
                    activity_code: str | None = None, validation_status: str | None = None,
                    verification_status: str | None = None, review_status: str | None = None,
                    source: str | None = None, source_system_id: uuid.UUID | None = None,
                    date_from: datetime | None = None, date_to: datetime | None = None,
                    current: CurrentUser = Depends(require("monitoring.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(MonitoringRecord).order_by(MonitoringRecord.observed_at.desc())
    for column, value in ((MonitoringRecord.programme_id, programme_id), (MonitoringRecord.indicator_id, indicator_id),
                          (MonitoringRecord.farmer_id, farmer_id), (MonitoringRecord.farm_plot_id, farm_plot_id),
                          (MonitoringRecord.activity_code, activity_code),
                          (MonitoringRecord.validation_status, validation_status),
                          (MonitoringRecord.verification_status, verification_status),
                          (MonitoringRecord.review_status, review_status),
                          (MonitoringRecord.source_system_id, source_system_id)):
        if value:
            stmt = stmt.where(column == value)
    if source == "imported":
        stmt = stmt.where(MonitoringRecord.source_system_id.is_not(None))
    elif source == "manual":
        stmt = stmt.where(MonitoringRecord.source_system_id.is_(None))
    if date_from:
        stmt = stmt.where(MonitoringRecord.observed_at >= date_from)
    if date_to:
        stmt = stmt.where(MonitoringRecord.observed_at <= date_to)
    stmt = apply_scope(stmt, current, MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)
    return page_of(*paginate(db, stmt, params), params, MonitoringOut)


@router.post("/monitoring-records", response_model=MonitoringOut, status_code=201)
def create_monitoring(body: MonitoringIn, current: CurrentUser = Depends(require("monitoring.write")),
                      db: Session = Depends(get_db)) -> MonitoringRecord:
    indicator = crud.get_or_404(db, IndicatorDefinition, body.indicator_id, "Indicator")
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    loc = resolve(db, current, farmer_id=body.farmer_id, farm_plot_id=body.farm_plot_id,
                  organisation_id=body.organisation_id, geo_unit_id=body.geo_unit_id)
    data = _normalise_value(indicator, body.model_dump())
    data.update(farmer_id=loc.farmer_id, farm_plot_id=loc.farm_plot_id, geo_unit_id=loc.geo_unit_id,
                organisation_id=loc.organisation_id, submission_status="draft")
    obj = crud.create(db, current, MonitoringRecord, data, "monitoring_record")
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/monitoring-records/{record_id}", response_model=MonitoringOut)
def get_monitoring(record_id: uuid.UUID, current: CurrentUser = Depends(require("monitoring.read")),
                   db: Session = Depends(get_db)) -> MonitoringRecord:
    return _scoped_monitoring(db, record_id, current)


@router.patch("/monitoring-records/{record_id}", response_model=MonitoringOut)
def update_monitoring(record_id: uuid.UUID, body: MonitoringUpdate, current: CurrentUser = Depends(require("monitoring.write")),
                      db: Session = Depends(get_db)) -> MonitoringRecord:
    rec = _scoped_monitoring(db, record_id, current)
    if rec.source_system_id:
        raise Conflict("Imported records are owned by their source system; correct them at the source and re-sync")
    if rec.review_status not in EDITABLE_REVIEW or rec.verification_status != "unverified":
        raise Conflict("Records under review or with a verification decision cannot be edited")
    if not can_edit_own(current, rec.created_by_id):
        raise Forbidden("You can only edit records you created")
    data = body.model_dump(exclude_unset=True)
    if "value" in data or "unit" in data:
        data.setdefault("value", rec.original_value if rec.original_value is not None else rec.value)
        data.setdefault("unit", rec.original_unit or rec.unit)
        data = _normalise_value(rec.indicator, data)
    crud.update(db, current, rec, {**data, "validation_status": "pending_validation", "submission_status": "draft"},
                "monitoring_record")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/monitoring-records/{record_id}/submit", response_model=MonitoringOut)
def submit_monitoring(record_id: uuid.UUID, current: CurrentUser = Depends(require("monitoring.write")),
                      db: Session = Depends(get_db)) -> MonitoringRecord:
    rec = _scoped_monitoring(db, record_id, current)
    if rec.source_system_id:
        raise Conflict("Imported records are validated on import")
    if rec.review_status not in EDITABLE_REVIEW:
        raise Conflict("Record is already in review")
    outcome = validate_monitoring(db, rec)
    crud.update(db, current, rec, {"submission_status": "submitted", "validation_status": outcome.status,
                                   "validation_messages": outcome.messages}, "monitoring_record", action="submit")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/monitoring-records/{record_id}/resolve-exception", response_model=MonitoringOut)
def resolve_exception(record_id: uuid.UUID, body: ExceptionResolutionIn,
                      current: CurrentUser = Depends(require("mrv.calculate")), db: Session = Depends(get_db)) -> MonitoringRecord:
    """MRV analyst decision on a data-quality exception (e.g. confirmed not a duplicate)."""
    rec = _scoped_monitoring(db, record_id, current)
    if rec.validation_status != "exception":
        raise InvalidState("Only records with a validation exception can be resolved")
    status = "valid" if body.decision == "accept" else "rejected"
    messages = (rec.validation_messages or []) + [f"RESOLVED ({body.decision}) by {current.email}: {body.note}"]
    crud.update(db, current, rec, {"validation_status": status, "validation_messages": messages}, "monitoring_record",
                action="resolve_exception", summary=body.note)
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/monitoring-records/{record_id}/submit-for-review", response_model=ReviewTaskOut)
def monitoring_for_review(record_id: uuid.UUID, body: SubmitForReviewIn,
                          current: CurrentUser = Depends(require("mrv.calculate")), db: Session = Depends(get_db)):
    _scoped_monitoring(db, record_id, current)
    task = mrv.submit_for_review(db, current, "monitoring_record", record_id, body.reviewer_id, body.due_date, body.note)
    db.commit()
    return task
