import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict, InvalidState
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import IndicatorDefinition, IndicatorResult, IndicatorTarget, Programme
from app.schemas.common import Page, PageParams, paginate
from app.schemas.indicator import (
    IndicatorIn,
    IndicatorOut,
    IndicatorStatusIn,
    IndicatorTargetIn,
    IndicatorTargetOut,
    IndicatorTargetUpdate,
    IndicatorUpdate,
)
from app.schemas.mrv import IndicatorResultOut, PreviewRequest
from app.services import audit, crud, mrv
from app.services.calculations import METHODS, describe_methods, validate_parameters
from app.services.record_types import MODULES_BY_CODE
from app.services.results import with_scope_labels

router = APIRouter(tags=["indicators"])

TRANSITIONS = {"draft": {"submitted", "retired"}, "submitted": {"draft", "approved", "retired"},
               "approved": {"retired"}, "retired": set()}


def _check(body: IndicatorIn | IndicatorUpdate, current: IndicatorDefinition | None = None) -> None:
    if body.method_code and body.method_code not in METHODS:
        raise AppError(422, "unknown_method", f"Unknown calculation method '{body.method_code}'")
    if body.module_code and body.module_code not in MODULES_BY_CODE:
        raise AppError(422, "unknown_module", f"Unknown module '{body.module_code}'")
    method = body.method_code or (current.method_code if current else None)
    params = body.method_parameters if body.method_parameters is not None else (current.method_parameters if current else {})
    try:
        validate_parameters(method, params or {})
    except ValueError as exc:
        raise AppError(422, "invalid_method_parameters", str(exc)) from exc


@router.get("/calculation-methods")
def list_methods(current: CurrentUser = Depends(require("indicator.read"))) -> list[dict]:
    return describe_methods()


@router.get("/indicators/results", response_model=Page[IndicatorResultOut])
def query_results(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, indicator_id: uuid.UUID | None = None,
                  indicator_code: str | None = None, scope_type: str | None = None, scope_geo_unit_id: uuid.UUID | None = None,
                  scope_organisation_id: uuid.UUID | None = None, period_start: date | None = None,
                  period_end: date | None = None, verification_status: str | None = None, review_status: str | None = None,
                  include_superseded: bool = False, current: CurrentUser = Depends(require("mrv.read")),
                  db: Session = Depends(get_db)) -> dict:
    stmt = select(IndicatorResult).order_by(IndicatorResult.created_at.desc())
    if indicator_code:
        stmt = stmt.join(IndicatorDefinition, IndicatorDefinition.id == IndicatorResult.indicator_id).where(
            IndicatorDefinition.code == indicator_code)
    for column, value in ((IndicatorResult.programme_id, programme_id), (IndicatorResult.indicator_id, indicator_id),
                          (IndicatorResult.scope_type, scope_type), (IndicatorResult.scope_geo_unit_id, scope_geo_unit_id),
                          (IndicatorResult.scope_organisation_id, scope_organisation_id),
                          (IndicatorResult.verification_status, verification_status),
                          (IndicatorResult.review_status, review_status)):
        if value:
            stmt = stmt.where(column == value)
    if period_start:
        stmt = stmt.where(IndicatorResult.period_start >= period_start)
    if period_end:
        stmt = stmt.where(IndicatorResult.period_end <= period_end)
    if not include_superseded:
        stmt = stmt.where(IndicatorResult.superseded_by_id.is_(None))
    if not current.all_scope:
        stmt = stmt.where(or_(IndicatorResult.scope_geo_unit_id.in_(current.geo_ids or {uuid.UUID(int=0)}),
                              IndicatorResult.scope_organisation_id.in_(current.org_ids or {uuid.UUID(int=0)})))
    items, total = paginate(db, stmt, params)
    return {"items": with_scope_labels(db, items), "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/indicators", response_model=Page[IndicatorOut])
def list_indicators(params: PageParams = Depends(), family: str | None = None, module_code: str | None = None,
                    status: str | None = None, search: str | None = None, latest_only: bool = True,
                    current: CurrentUser = Depends(require("indicator.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(IndicatorDefinition).order_by(IndicatorDefinition.code, IndicatorDefinition.version.desc())
    if family:
        stmt = stmt.where(IndicatorDefinition.family == family)
    if module_code:
        stmt = stmt.where(IndicatorDefinition.module_code == module_code)
    if status:
        stmt = stmt.where(IndicatorDefinition.status == status)
    if search:
        stmt = stmt.where(or_(IndicatorDefinition.code.ilike(f"%{search}%"), IndicatorDefinition.name.ilike(f"%{search}%")))
    if latest_only:
        latest = select(IndicatorDefinition.code, func.max(IndicatorDefinition.version).label("v")).group_by(
            IndicatorDefinition.code).subquery()
        stmt = stmt.join(latest, (latest.c.code == IndicatorDefinition.code) & (latest.c.v == IndicatorDefinition.version))
    items, total = paginate(db, stmt, params)
    return {"items": [IndicatorOut.model_validate(i) for i in items], "total": total, "page": params.page,
            "page_size": params.page_size}


@router.post("/indicators", response_model=IndicatorOut, status_code=201)
def create_indicator(body: IndicatorIn, current: CurrentUser = Depends(require("indicator.manage")),
                     db: Session = Depends(get_db)) -> IndicatorDefinition:
    _check(body)
    if db.scalar(select(IndicatorDefinition.id).where(IndicatorDefinition.code == body.code)):
        raise Conflict(f"Indicator {body.code} already exists; create a new version instead")
    obj = crud.create(db, current, IndicatorDefinition, {**body.model_dump(), "version": 1, "status": "draft",
                                                          "requires_confirmation": True}, "indicator_definition")
    db.commit()
    return obj


@router.get("/indicators/{indicator_id}", response_model=IndicatorOut)
def get_indicator(indicator_id: uuid.UUID, current: CurrentUser = Depends(require("indicator.read")),
                  db: Session = Depends(get_db)) -> IndicatorDefinition:
    return crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")


@router.post("/indicators/{indicator_id}/preview")
def preview(indicator_id: uuid.UUID, body: PreviewRequest, current: CurrentUser = Depends(require("indicator.read")),
            db: Session = Depends(get_db)) -> dict:
    """Dry-run a (possibly draft) definition and formula. Nothing is stored; the output is labelled PREVIEW."""
    if not (current.has("indicator.manage") or current.has("mrv.calculate")):
        raise AppError(403, "forbidden", "Previews need indicator.manage or mrv.calculate")
    ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
    return mrv.preview_calculation(db, current, ind, body)


@router.get("/indicators/{indicator_id}/versions", response_model=list[IndicatorOut])
def indicator_versions(indicator_id: uuid.UUID, current: CurrentUser = Depends(require("indicator.read")),
                       db: Session = Depends(get_db)) -> list:
    ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
    return list(db.scalars(select(IndicatorDefinition).where(IndicatorDefinition.code == ind.code)
                           .order_by(IndicatorDefinition.version.desc())))


@router.patch("/indicators/{indicator_id}", response_model=IndicatorOut)
def update_indicator(indicator_id: uuid.UUID, body: IndicatorUpdate, current: CurrentUser = Depends(require("indicator.manage")),
                     db: Session = Depends(get_db)) -> IndicatorDefinition:
    ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
    if ind.status != "draft":
        raise Conflict("Only draft indicator versions can be edited; create a new version to change a submitted or approved one")
    _check(body, ind)
    obj = crud.update(db, current, ind, body, "indicator_definition")
    db.commit()
    return obj


@router.post("/indicators/{indicator_id}/new-version", response_model=IndicatorOut, status_code=201)
def new_version(indicator_id: uuid.UUID, current: CurrentUser = Depends(require("indicator.manage")),
                db: Session = Depends(get_db)) -> IndicatorDefinition:
    ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
    latest = db.scalar(select(func.max(IndicatorDefinition.version)).where(IndicatorDefinition.code == ind.code))
    if db.scalar(select(IndicatorDefinition.id).where(IndicatorDefinition.code == ind.code,
                                                      IndicatorDefinition.status == "draft")):
        raise Conflict("A draft version already exists for this indicator")
    data = audit.snapshot(ind)
    for key in ("id", "version", "status", "is_demo", "requires_confirmation"):
        data.pop(key, None)
    copy = IndicatorDefinition(**{k: getattr(ind, k) for k in data}, version=latest + 1, status="draft",
                               requires_confirmation=True, is_demo=ind.is_demo)
    audit.stamp(copy, current, created=True)
    db.add(copy)
    db.flush()
    audit.record(db, current, "new_version", "indicator_definition", copy.id, summary=f"{ind.code} v{copy.version} from v{ind.version}")
    db.commit()
    return copy


@router.post("/indicators/{indicator_id}/status", response_model=IndicatorOut)
def change_status(indicator_id: uuid.UUID, body: IndicatorStatusIn, current: CurrentUser = Depends(require("indicator.manage")),
                  db: Session = Depends(get_db)) -> IndicatorDefinition:
    ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
    if body.status not in TRANSITIONS[ind.status]:
        raise InvalidState(f"Cannot move an indicator from {ind.status} to {body.status}")
    if body.status == "approved":
        missing = [label for label, value in (
            ("definition", ind.definition), ("unit", ind.unit), ("calculation method", ind.method_code),
            ("method reference", ind.method_reference), ("eligibility", ind.eligibility),
            ("baseline method", ind.baseline_method), ("evidence requirements", ind.evidence_requirements),
            ("verification sample approach", ind.verification_sample_approach)) if not value]
        if missing:
            raise InvalidState(f"Approval needs a complete definition; missing: {', '.join(missing)}")
    changes = {"status": body.status}
    if body.requires_confirmation is not None:
        changes["requires_confirmation"] = body.requires_confirmation
    crud.update(db, current, ind, changes, "indicator_definition", action=f"status_{body.status}", summary=body.note)
    db.commit()
    return ind


@router.get("/indicator-targets", response_model=list[IndicatorTargetOut])
def list_indicator_targets(indicator_id: uuid.UUID | None = None, programme_id: uuid.UUID | None = None,
                           current: CurrentUser = Depends(require("indicator.read")), db: Session = Depends(get_db)) -> list:
    stmt = select(IndicatorTarget).order_by(IndicatorTarget.created_at)
    if indicator_id:
        ind = crud.get_or_404(db, IndicatorDefinition, indicator_id, "Indicator")
        stmt = stmt.where(IndicatorTarget.indicator_id.in_(select(IndicatorDefinition.id).where(IndicatorDefinition.code == ind.code)))
    if programme_id:
        stmt = stmt.where(IndicatorTarget.programme_id == programme_id)
    return list(db.scalars(stmt).unique())


@router.post("/indicator-targets", response_model=IndicatorTargetOut, status_code=201)
def create_indicator_target(body: IndicatorTargetIn, current: CurrentUser = Depends(require("indicator.manage")),
                            db: Session = Depends(get_db)) -> IndicatorTarget:
    ind = crud.get_or_404(db, IndicatorDefinition, body.indicator_id, "Indicator")
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    if body.unit != ind.unit:
        raise AppError(422, "unit_mismatch", f"Target unit must match the indicator unit ({ind.unit})")
    obj = crud.create(db, current, IndicatorTarget, {**body.model_dump(), "status": "proposed"}, "indicator_target")
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/indicator-targets/{target_id}", response_model=IndicatorTargetOut)
def update_indicator_target(target_id: uuid.UUID, body: IndicatorTargetUpdate,
                            current: CurrentUser = Depends(require("indicator.manage")), db: Session = Depends(get_db)) -> IndicatorTarget:
    target = crud.get_or_404(db, IndicatorTarget, target_id, "Target")
    data = body.model_dump(exclude_unset=True)
    if data.get("status") == "approved":
        data.update(approved_by_id=current.id, approved_at=utcnow())
    crud.update(db, current, target, data, "indicator_target")
    db.commit()
    db.refresh(target)
    return target
