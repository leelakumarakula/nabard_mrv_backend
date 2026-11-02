import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
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
    TargetUpdate,
)
from app.services import crud

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


@router.post("/programmes/{programme_id}/targets", response_model=TargetOut, status_code=201)
def create_target(programme_id: uuid.UUID, body: TargetIn, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ProgrammeTarget:
    crud.get_or_404(db, Programme, programme_id, "Programme")
    obj = crud.create(db, current, ProgrammeTarget, {**body.model_dump(), "programme_id": programme_id}, "programme_target")
    db.commit()
    return obj


@router.patch("/programme-targets/{target_id}", response_model=TargetOut)
def update_target(target_id: uuid.UUID, body: TargetUpdate, current: CurrentUser = Depends(require("programme.manage")),
                  db: Session = Depends(get_db)) -> ProgrammeTarget:
    obj = crud.update(db, current, crud.get_or_404(db, ProgrammeTarget, target_id, "Target"), body, "programme_target")
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
