import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.db.session import get_db
from app.models import Farm, Farmer, FarmPlot, Organisation, ProgrammeParticipation
from app.schemas.common import Page, PageParams, page_of, paginate
from app.schemas.programme import OrganisationDetail, OrganisationIn, OrganisationOut, OrganisationUpdate
from app.services import crud
from app.services.scope import apply_scope
from app.services.units import area_in_ha

router = APIRouter(prefix="/organisations", tags=["organisations"])


@router.get("", response_model=Page[OrganisationOut])
def list_organisations(params: PageParams = Depends(), org_type: str | None = None, geo_unit_id: uuid.UUID | None = None,
                       search: str | None = None, include_archived: bool = False,
                       current: CurrentUser = Depends(require("org.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(Organisation).order_by(Organisation.name)
    if org_type:
        stmt = stmt.where(Organisation.org_type == org_type)
    if geo_unit_id:
        stmt = stmt.where(Organisation.geo_unit_id == geo_unit_id)
    if search:
        stmt = stmt.where(or_(Organisation.name.ilike(f"%{search}%"), Organisation.code.ilike(f"%{search}%")))
    if not include_archived:
        stmt = stmt.where(Organisation.is_archived == false())
    stmt = apply_scope(stmt, current, Organisation.geo_unit_id, Organisation.id)
    return page_of(*paginate(db, stmt, params), params, OrganisationOut)


@router.post("", response_model=OrganisationOut, status_code=201)
def create_organisation(body: OrganisationIn, current: CurrentUser = Depends(require("org.manage")),
                        db: Session = Depends(get_db)) -> Organisation:
    current.ensure_access(body.geo_unit_id)
    obj = crud.create(db, current, Organisation, body.model_dump(), "organisation")
    db.commit()
    return obj


@router.get("/{organisation_id}", response_model=OrganisationDetail)
def get_organisation(organisation_id: uuid.UUID, current: CurrentUser = Depends(require("org.read")),
                     db: Session = Depends(get_db)) -> OrganisationDetail:
    org = crud.get_or_404(db, Organisation, organisation_id, "Organisation")
    current.ensure_access(org.geo_unit_id, org.id)
    farmer_count = db.scalar(select(func.count()).select_from(Farmer).where(
        Farmer.primary_fpo_id == org.id, Farmer.is_archived == false())) or 0
    enrolled = db.scalar(select(func.count(func.distinct(ProgrammeParticipation.farmer_id)))
                         .join(Farmer, Farmer.id == ProgrammeParticipation.farmer_id)
                         .where(Farmer.primary_fpo_id == org.id, ProgrammeParticipation.status == "enrolled")) or 0
    plots = db.execute(select(FarmPlot.area, FarmPlot.area_unit).join(Farm, Farm.id == FarmPlot.farm_id)
                       .join(Farmer, Farmer.id == Farm.farmer_id)
                       .where(Farmer.primary_fpo_id == org.id, FarmPlot.is_archived == false())).all()
    area = sum((area_in_ha(a, u) for a, u in plots), Decimal(0))
    return OrganisationDetail.model_validate(org).model_copy(update={
        "farmer_count": farmer_count, "enrolled_farmer_count": enrolled, "plot_area_ha": round(area, 4)})


@router.patch("/{organisation_id}", response_model=OrganisationOut)
def update_organisation(organisation_id: uuid.UUID, body: OrganisationUpdate,
                        current: CurrentUser = Depends(require("org.manage")), db: Session = Depends(get_db)) -> Organisation:
    org = crud.get_or_404(db, Organisation, organisation_id, "Organisation")
    current.ensure_access(org.geo_unit_id, org.id)
    obj = crud.update(db, current, org, body, "organisation")
    db.commit()
    return obj
