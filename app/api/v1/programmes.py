import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict
from app.db.session import get_db
from app.models import Organisation, Programme, ProgrammeOrganisation, ProgrammeTarget, ReportingPeriod
from app.schemas.common import Page, PageParams, page_of, paginate
from app.schemas.programme import (
    PeriodIn,
    PeriodOut,
    PeriodUpdate,
    ProgrammeIn,
    ProgrammeOrgIn,
    ProgrammeOrgOut,
    ProgrammeOut,
    ProgrammeUpdate,
    TargetIn,
    TargetOut,
    TargetRevision,
    TargetUpdate,
)
from app.services import crud
from app.services.scope import eq_or_null

router = APIRouter(tags=["programmes"])


@router.get("/programmes", response_model=Page[ProgrammeOut])
def list_programmes(params: PageParams = Depends(), status: str | None = None,
                    current: CurrentUser = Depends(require("programme.read")), db: Session = Depends(get_db)) -> dict:
    # Latest proposal first (V2: the latest proposal is the primary programme)
    stmt = select(Programme).order_by(Programme.proposal_date.desc(), Programme.name)
    if status:
        stmt = stmt.where(Programme.status == status)
    return page_of(*paginate(db, stmt, params), params, ProgrammeOut)


@router.post("/programmes", response_model=ProgrammeOut, status_code=201)
def create_programme(body: ProgrammeIn, current: CurrentUser = Depends(require("programme.manage")),
                     db: Session = Depends(get_db)) -> Programme:
    obj = crud.create(db, current, Programme, body.model_dump(), "programme")
    db.commit()
    return obj


@router.get("/programmes/{programme_id}", response_model=ProgrammeOut)
def get_programme(programme_id: uuid.UUID, current: CurrentUser = Depends(require("programme.read")),
                  db: Session = Depends(get_db)) -> Programme:
    return crud.get_or_404(db, Programme, programme_id, "Programme")


@router.patch("/programmes/{programme_id}", response_model=ProgrammeOut)
def update_programme(programme_id: uuid.UUID, body: ProgrammeUpdate,
                     current: CurrentUser = Depends(require("programme.manage")), db: Session = Depends(get_db)) -> Programme:
    obj = crud.update(db, current, crud.get_or_404(db, Programme, programme_id, "Programme"), body, "programme")
    db.commit()
    return obj


@router.get("/programmes/{programme_id}/targets", response_model=list[TargetOut])
def list_targets(programme_id: uuid.UUID, current: CurrentUser = Depends(require("programme.read")),
                 db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(ProgrammeTarget).where(ProgrammeTarget.programme_id == programme_id)
                           .order_by(ProgrammeTarget.metric_code, ProgrammeTarget.target_version.desc())))


def _latest_version(db: Session, programme_id: uuid.UUID, metric_code: str, geo_unit_id: uuid.UUID | None) -> int:
    """Highest stored version of a target (same programme, metric and geography); 0 when the target is new."""
    return db.scalar(select(func.max(ProgrammeTarget.target_version)).where(
        ProgrammeTarget.programme_id == programme_id, ProgrammeTarget.metric_code == metric_code,
        eq_or_null(ProgrammeTarget.geo_unit_id, geo_unit_id))) or 0


@router.post("/programmes/{programme_id}/targets", response_model=TargetOut, status_code=201)
def create_target(programme_id: uuid.UUID, body: TargetIn, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ProgrammeTarget:
    crud.get_or_404(db, Programme, programme_id, "Programme")
    latest = _latest_version(db, programme_id, body.metric_code, body.geo_unit_id)
    if latest and body.target_version <= latest:
        raise Conflict(f"Target '{body.metric_code}' already has version {latest}. Record a changed figure as a new "
                       "version (Revise target) – earlier versions are never overwritten.")
    obj = crud.create(db, current, ProgrammeTarget, {**body.model_dump(), "programme_id": programme_id}, "programme_target")
    db.commit()
    return obj


# Fields that define the figure itself; they change only through a new version.
_FIGURE_FIELDS = ("target_value", "unit", "period_label", "target_version")


@router.patch("/programme-targets/{target_id}", response_model=TargetOut)
def update_target(target_id: uuid.UUID, body: TargetUpdate, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ProgrammeTarget:
    target = crud.get_or_404(db, ProgrammeTarget, target_id, "Target")
    data = body.model_dump(exclude_unset=True)
    changed = [f for f in _FIGURE_FIELDS if f in data and data[f] != getattr(target, f)]
    if changed:
        raise AppError(422, "target_figure_locked",
                       f"{', '.join(changed)} cannot be changed in place. Use 'Revise target' to record the changed "
                       "figure as a new version; the current version stays in the history.")
    for f in _FIGURE_FIELDS:
        data.pop(f, None)
    obj = crud.update(db, current, target, data, "programme_target")
    db.commit()
    return obj


@router.post("/programme-targets/{target_id}/revise", response_model=TargetOut, status_code=201)
def revise_target(target_id: uuid.UUID, body: TargetRevision, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ProgrammeTarget:
    """Record a changed figure as the next version of the target. The earlier version is kept unchanged."""
    base = crud.get_or_404(db, ProgrammeTarget, target_id, "Target")
    version = _latest_version(db, base.programme_id, base.metric_code, base.geo_unit_id) + 1
    obj = crud.create(db, current, ProgrammeTarget, {
        "programme_id": base.programme_id, "metric_code": base.metric_code, "metric_name": base.metric_name,
        "geo_unit_id": base.geo_unit_id, "indicator_code": base.indicator_code, "is_demo": base.is_demo,
        "target_value": body.target_value, "unit": body.unit or base.unit,
        "period_label": body.period_label if body.period_label is not None else base.period_label,
        "status": body.status, "source_note": body.source_note, "target_version": version,
    }, "programme_target", summary=f"{base.metric_code} revised to v{version}: {base.target_value} → {body.target_value}")
    db.commit()
    return obj


@router.get("/programmes/{programme_id}/reporting-periods", response_model=list[PeriodOut])
def list_periods(programme_id: uuid.UUID, current: CurrentUser = Depends(require("programme.read")),
                 db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(ReportingPeriod).where(ReportingPeriod.programme_id == programme_id)
                           .order_by(ReportingPeriod.start_date)))


@router.post("/programmes/{programme_id}/reporting-periods", response_model=PeriodOut, status_code=201)
def create_period(programme_id: uuid.UUID, body: PeriodIn, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ReportingPeriod:
    crud.get_or_404(db, Programme, programme_id, "Programme")
    obj = crud.create(db, current, ReportingPeriod, {**body.model_dump(), "programme_id": programme_id}, "reporting_period")
    db.commit()
    return obj


@router.patch("/reporting-periods/{period_id}", response_model=PeriodOut)
def update_period(period_id: uuid.UUID, body: PeriodUpdate, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ReportingPeriod:
    obj = crud.update(db, current, crud.get_or_404(db, ReportingPeriod, period_id, "Reporting period"), body,
                      "reporting_period")
    db.commit()
    return obj


@router.get("/programmes/{programme_id}/organisations", response_model=list[ProgrammeOrgOut])
def list_programme_orgs(programme_id: uuid.UUID, current: CurrentUser = Depends(require("programme.read")),
                        db: Session = Depends(get_db)) -> list[ProgrammeOrgOut]:
    rows = db.execute(select(ProgrammeOrganisation, Organisation.name)
                      .join(Organisation, Organisation.id == ProgrammeOrganisation.organisation_id)
                      .where(ProgrammeOrganisation.programme_id == programme_id).order_by(Organisation.name))
    return [ProgrammeOrgOut.model_validate(po).model_copy(update={"organisation_name": name}) for po, name in rows]


@router.post("/programmes/{programme_id}/organisations", response_model=ProgrammeOrgOut, status_code=201)
def add_programme_org(programme_id: uuid.UUID, body: ProgrammeOrgIn,
                      current: CurrentUser = Depends(require("programme.manage")), db: Session = Depends(get_db)) -> ProgrammeOrganisation:
    crud.get_or_404(db, Programme, programme_id, "Programme")
    crud.get_or_404(db, Organisation, body.organisation_id, "Organisation")
    obj = crud.create(db, current, ProgrammeOrganisation, {**body.model_dump(), "programme_id": programme_id},
                      "programme_organisation")
    db.commit()
    return obj
