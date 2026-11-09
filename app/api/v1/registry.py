import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, descendant_geo_ids, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import (
    Farm,
    Farmer,
    FarmerCategoryAssignment,
    FarmPlot,
    GeoUnit,
    Organisation,
    Programme,
    ProgrammeParticipation,
)
from app.schemas.common import Page, PageParams, paginate
from app.schemas.config import AssignmentIn, AssignmentOut, AssignmentUpdate
from app.schemas.registry import (
    FarmerDetail,
    FarmerIn,
    FarmerOut,
    FarmerUpdate,
    FarmIn,
    FarmOut,
    FarmUpdate,
    FarmWithPlots,
    ParticipationIn,
    ParticipationOut,
    PlotIn,
    PlotLookupOut,
    PlotOut,
    PlotUpdate,
)
from app.services import audit, configuration, crud
from app.services.scope import apply_scope
from app.services.units import area_in_ha

router = APIRouter(tags=["farmer registry"])

_LOCATION_FIELDS = {"latitude": None, "longitude": None}


def farmer_out(farmer: Farmer, current: CurrentUser, db: Session,
               category_map: dict | None = None) -> FarmerOut:
    """Masks personal data without farmer.read_pii and sensitive demographics/categories without farmer.read_sensitive."""
    out = FarmerOut.model_validate(farmer)
    if not current.has("farmer.read_pii"):
        out = out.model_copy(update={"phone": None, "pii_masked": True})
    demographics, masked = configuration.visible_demographics(db, current, farmer.demographics)
    assigned = (category_map if category_map is not None else configuration.assignments(db, [farmer.id])).get(farmer.id, [])
    sensitive_ok = current.has("farmer.read_sensitive")
    categories = [{"code": a.category.code, "name": a.category.name, "status": a.category.status, "basis": a.basis}
                  for a in assigned if sensitive_ok or not a.category.is_sensitive]
    masked = masked or len(categories) < len(assigned)
    return out.model_copy(update={"demographics": demographics, "categories": categories, "sensitive_masked": masked})


def _mask(model_out, current: CurrentUser):
    return model_out if current.has("farmer.read_pii") else model_out.model_copy(update=_LOCATION_FIELDS)


def get_scoped_farmer(db: Session, farmer_id: uuid.UUID, current: CurrentUser) -> Farmer:
    farmer = crud.get_or_404(db, Farmer, farmer_id, "Farmer")
    current.ensure_access(farmer.geo_unit_id, farmer.primary_fpo_id)
    return farmer


def _check_geo_fpo(db: Session, current: CurrentUser, geo_unit_id: uuid.UUID | None, fpo_id: uuid.UUID | None) -> None:
    if geo_unit_id and db.get(GeoUnit, geo_unit_id) is None:
        raise AppError(422, "invalid_reference", "Geography unit does not exist")
    if fpo_id:
        fpo = db.get(Organisation, fpo_id)
        if fpo is None or fpo.org_type != "fpo":
            raise AppError(422, "invalid_reference", "Primary FPO must be an existing FPO organisation")
    current.ensure_access(geo_unit_id, fpo_id)


@router.get("/farmers", response_model=Page[FarmerOut])
def list_farmers(params: PageParams = Depends(), search: str | None = None, geo_unit_id: uuid.UUID | None = None,
                 fpo_id: uuid.UUID | None = None, consent_status: str | None = None, programme_id: uuid.UUID | None = None,
                 enrolment_status: str | None = None, include_archived: bool = False,
                 category_code: str | None = None,
                 current: CurrentUser = Depends(require("farmer.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(Farmer).order_by(Farmer.farmer_code)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Farmer.full_name.ilike(like), Farmer.farmer_code.ilike(like), Farmer.village.ilike(like),
                              Farmer.household_code.ilike(like)))
    if geo_unit_id:
        stmt = stmt.where(Farmer.geo_unit_id.in_(descendant_geo_ids(db, {geo_unit_id})))
    if fpo_id:
        stmt = stmt.where(Farmer.primary_fpo_id == fpo_id)
    if consent_status:
        stmt = stmt.where(Farmer.consent_status == consent_status)
    if category_code:
        category = configuration.category_by_code(db, category_code)
        if category.is_sensitive and not current.has("farmer.read_sensitive"):
            raise AppError(403, "forbidden", "Filtering by a sensitive category is not permitted for your role")
        stmt = stmt.where(Farmer.id.in_(select(FarmerCategoryAssignment.farmer_id).where(
            FarmerCategoryAssignment.category_id == category.id, FarmerCategoryAssignment.status == "active")))
    if programme_id or enrolment_status:
        sub = select(ProgrammeParticipation.farmer_id)
        if programme_id:
            sub = sub.where(ProgrammeParticipation.programme_id == programme_id)
        if enrolment_status:
            sub = sub.where(ProgrammeParticipation.status == enrolment_status)
        stmt = stmt.where(Farmer.id.in_(sub))
    if not include_archived:
        stmt = stmt.where(Farmer.is_archived == false())
    stmt = apply_scope(stmt, current, Farmer.geo_unit_id, Farmer.primary_fpo_id)
    items, total = paginate(db, stmt, params)
    category_map = configuration.assignments(db, [f.id for f in items])
    return {"items": [farmer_out(f, current, db, category_map) for f in items], "total": total, "page": params.page,
            "page_size": params.page_size}


@router.post("/farmers", response_model=FarmerOut, status_code=201)
def create_farmer(body: FarmerIn, current: CurrentUser = Depends(require("farmer.manage")),
                  db: Session = Depends(get_db)) -> FarmerOut:
    _check_geo_fpo(db, current, body.geo_unit_id, body.primary_fpo_id)
    if body.consent_status == "given" and not body.consent_date:
        raise AppError(422, "consent_date_required", "Record the consent date when consent is given")
    data = body.model_dump()
    data["demographics"] = configuration.validate_demographics(db, current, None, data.pop("demographics") or {},
                                                               body.consent_status) or None
    farmer = crud.create(db, current, Farmer, data, "farmer", summary=f"Registered {body.farmer_code}")
    db.commit()
    db.refresh(farmer)
    return farmer_out(farmer, current, db)


@router.get("/farmers/{farmer_id}", response_model=FarmerDetail)
def get_farmer(farmer_id: uuid.UUID, current: CurrentUser = Depends(require("farmer.read")),
               db: Session = Depends(get_db)) -> FarmerDetail:
    farmer = get_scoped_farmer(db, farmer_id, current)
    farms = list(db.scalars(select(Farm).where(Farm.farmer_id == farmer.id).order_by(Farm.farm_code)).unique())
    plots = list(db.scalars(select(FarmPlot).where(FarmPlot.farm_id.in_([f.id for f in farms] or [uuid.UUID(int=0)]))
                            .order_by(FarmPlot.plot_code)).unique())
    farm_outs = []
    for farm in farms:
        farm_out = _mask(FarmWithPlots.model_validate(farm), current)
        farm_out.plots = [_mask(PlotOut.model_validate(p), current) for p in plots if p.farm_id == farm.id]
        farm_outs.append(farm_out)
    participations = list(db.scalars(select(ProgrammeParticipation).where(ProgrammeParticipation.farmer_id == farmer.id)))
    total_area = sum((area_in_ha(p.area, p.area_unit) for p in plots if not p.is_archived), Decimal(0))
    if current.has("farmer.read_pii"):
        audit.record(db, current, "read_personal_data", "farmer", farmer.id, summary="Viewed farmer contact/location")
        db.commit()
    base = farmer_out(farmer, current, db).model_dump()
    return FarmerDetail(**base, farms=farm_outs, total_plot_area_ha=round(total_area, 4),
                        participations=[ParticipationOut.model_validate(p) for p in participations])


@router.patch("/farmers/{farmer_id}", response_model=FarmerOut)
def update_farmer(farmer_id: uuid.UUID, body: FarmerUpdate, current: CurrentUser = Depends(require("farmer.manage")),
                  db: Session = Depends(get_db)) -> FarmerOut:
    farmer = get_scoped_farmer(db, farmer_id, current)
    data = body.model_dump(exclude_unset=True)
    if "demographics" in data:
        data["demographics"] = configuration.validate_demographics(
            db, current, farmer, data["demographics"] or {}, data.get("consent_status", farmer.consent_status)) or None
    if "geo_unit_id" in data or "primary_fpo_id" in data:
        _check_geo_fpo(db, current, data.get("geo_unit_id", farmer.geo_unit_id), data.get("primary_fpo_id", farmer.primary_fpo_id))
    if data.get("consent_status") == "given" and not (data.get("consent_date") or farmer.consent_date):
        raise AppError(422, "consent_date_required", "Record the consent date when consent is given")
    crud.update(db, current, farmer, data, "farmer")
    db.commit()
    db.refresh(farmer)
    return farmer_out(farmer, current, db)


@router.get("/farmers/{farmer_id}/categories", response_model=list[AssignmentOut])
def list_farmer_categories(farmer_id: uuid.UUID, current: CurrentUser = Depends(require("farmer.read")),
                           db: Session = Depends(get_db)) -> list[AssignmentOut]:
    get_scoped_farmer(db, farmer_id, current)
    rows = db.scalars(select(FarmerCategoryAssignment).where(FarmerCategoryAssignment.farmer_id == farmer_id)
                      .order_by(FarmerCategoryAssignment.created_at)).unique()
    return [_assignment_out(a) for a in rows if current.has("farmer.read_sensitive") or not a.category.is_sensitive]


def _assignment_out(a: FarmerCategoryAssignment) -> AssignmentOut:
    return AssignmentOut(id=a.id, farmer_id=a.farmer_id, category_id=a.category_id, category_code=a.category.code,
                         category_name=a.category.name, is_sensitive=a.category.is_sensitive, status=a.status,
                         basis=a.basis, assigned_on=a.assigned_on, note=a.note)


@router.post("/farmers/{farmer_id}/categories", response_model=AssignmentOut, status_code=201)
def assign_category(farmer_id: uuid.UUID, body: AssignmentIn, current: CurrentUser = Depends(require("farmer.manage")),
                    db: Session = Depends(get_db)) -> AssignmentOut:
    """Explicit, recorded assignment of a programme-defined beneficiary category (never inferred)."""
    farmer = get_scoped_farmer(db, farmer_id, current)
    category = configuration.category_by_code(db, body.category_code)
    if category.is_sensitive and not current.has("farmer.read_sensitive"):
        raise AppError(403, "forbidden", "You are not permitted to record sensitive beneficiary categories")
    if category.requires_consent and farmer.consent_status != "given":
        raise AppError(422, "consent_required", "Record the farmer's consent before assigning this category")
    existing = db.scalar(select(FarmerCategoryAssignment).where(FarmerCategoryAssignment.farmer_id == farmer.id,
                                                                FarmerCategoryAssignment.category_id == category.id))
    data = {"status": "active", "basis": body.basis, "assigned_on": body.assigned_on, "note": body.note}
    if existing:
        obj = crud.update(db, current, existing, data, "farmer_category_assignment")
    else:
        obj = crud.create(db, current, FarmerCategoryAssignment, {**data, "farmer_id": farmer.id, "category_id": category.id,
                                                                  "is_demo": farmer.is_demo}, "farmer_category_assignment")
    db.commit()
    db.refresh(obj)
    return _assignment_out(obj)


@router.patch("/farmer-category-assignments/{assignment_id}", response_model=AssignmentOut)
def update_assignment(assignment_id: uuid.UUID, body: AssignmentUpdate, current: CurrentUser = Depends(require("farmer.manage")),
                      db: Session = Depends(get_db)) -> AssignmentOut:
    obj = crud.get_or_404(db, FarmerCategoryAssignment, assignment_id, "Assignment")
    get_scoped_farmer(db, obj.farmer_id, current)
    if obj.category.is_sensitive and not current.has("farmer.read_sensitive"):
        raise AppError(403, "forbidden", "You are not permitted to change sensitive beneficiary categories")
    crud.update(db, current, obj, body, "farmer_category_assignment")
    db.commit()
    db.refresh(obj)
    return _assignment_out(obj)


@router.post("/farmers/{farmer_id}/participations", response_model=ParticipationOut, status_code=201)
def enrol_farmer(farmer_id: uuid.UUID, body: ParticipationIn, current: CurrentUser = Depends(require("farmer.manage")),
                 db: Session = Depends(get_db)) -> ProgrammeParticipation:
    farmer = get_scoped_farmer(db, farmer_id, current)
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    if body.status == "enrolled" and farmer.consent_status != "given":
        raise AppError(422, "consent_required", "Record the farmer's consent before enrolling them in a programme")
    existing = db.scalar(select(ProgrammeParticipation).where(
        ProgrammeParticipation.farmer_id == farmer.id, ProgrammeParticipation.programme_id == body.programme_id))
    if existing:
        # Only the fields sent change; e.g. a status update keeps the original enrolment date
        obj = crud.update(db, current, existing, body.model_dump(exclude={"programme_id"}, exclude_unset=True),
                          "programme_participation")
    else:
        obj = crud.create(db, current, ProgrammeParticipation, {**body.model_dump(), "farmer_id": farmer.id},
                          "programme_participation", summary=f"Enrolment of {farmer.farmer_code}")
    db.commit()
    return obj


@router.post("/farmers/{farmer_id}/farms", response_model=FarmOut, status_code=201)
def create_farm(farmer_id: uuid.UUID, body: FarmIn, current: CurrentUser = Depends(require("farmer.manage")),
                db: Session = Depends(get_db)) -> Farm:
    farmer = get_scoped_farmer(db, farmer_id, current)
    data = body.model_dump()
    data["geo_unit_id"] = data["geo_unit_id"] or farmer.geo_unit_id
    obj = crud.create(db, current, Farm, {**data, "farmer_id": farmer.id}, "farm")
    db.commit()
    return obj


@router.patch("/farms/{farm_id}", response_model=FarmOut)
def update_farm(farm_id: uuid.UUID, body: FarmUpdate, current: CurrentUser = Depends(require("farmer.manage")),
                db: Session = Depends(get_db)) -> Farm:
    farm = crud.get_or_404(db, Farm, farm_id, "Farm")
    get_scoped_farmer(db, farm.farmer_id, current)
    obj = crud.update(db, current, farm, body, "farm")
    db.commit()
    return obj


@router.post("/farms/{farm_id}/plots", response_model=PlotOut, status_code=201)
def create_plot(farm_id: uuid.UUID, body: PlotIn, current: CurrentUser = Depends(require("farmer.manage")),
                db: Session = Depends(get_db)) -> FarmPlot:
    farm = crud.get_or_404(db, Farm, farm_id, "Farm")
    get_scoped_farmer(db, farm.farmer_id, current)
    obj = crud.create(db, current, FarmPlot, {**body.model_dump(), "farm_id": farm.id}, "farm_plot")
    db.commit()
    return obj


@router.get("/plots", response_model=Page[PlotLookupOut])
def list_plots(params: PageParams = Depends(), search: str | None = None, farmer_id: uuid.UUID | None = None,
               current: CurrentUser = Depends(require("farmer.read")), db: Session = Depends(get_db)) -> dict:
    stmt = (select(FarmPlot, Farm.farm_code, Farm.name, Farmer).join(Farm, Farm.id == FarmPlot.farm_id)
            .join(Farmer, Farmer.id == Farm.farmer_id).where(FarmPlot.is_archived == false()).order_by(FarmPlot.plot_code))
    if search:
        stmt = stmt.where(or_(FarmPlot.plot_code.ilike(f"%{search}%"), Farm.name.ilike(f"%{search}%"),
                              Farmer.full_name.ilike(f"%{search}%"),
                              Farmer.farmer_code.ilike(f"%{search}%")))
    if farmer_id:
        stmt = stmt.where(Farmer.id == farmer_id)
    stmt = apply_scope(stmt, current, Farmer.geo_unit_id, Farmer.primary_fpo_id)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).unique().all()
    items = [PlotLookupOut(id=p.id, plot_code=p.plot_code, farm_code=fc, farm_name=fn, farmer_id=f.id,
                           farmer_label=f.label, area=p.area, area_unit=p.area_unit) for p, fc, fn, f in rows]
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/plots/{plot_id}", response_model=PlotOut)
def get_plot(plot_id: uuid.UUID, current: CurrentUser = Depends(require("farmer.read")),
             db: Session = Depends(get_db)) -> PlotOut:
    plot = crud.get_or_404(db, FarmPlot, plot_id, "Plot")
    get_scoped_farmer(db, plot.farm.farmer_id, current)
    return _mask(PlotOut.model_validate(plot), current)


@router.patch("/plots/{plot_id}", response_model=PlotOut)
def update_plot(plot_id: uuid.UUID, body: PlotUpdate, current: CurrentUser = Depends(require("farmer.manage")),
                db: Session = Depends(get_db)) -> FarmPlot:
    plot = crud.get_or_404(db, FarmPlot, plot_id, "Plot")
    get_scoped_farmer(db, plot.farm.farmer_id, current)
    obj = crud.update(db, current, plot, body, "farm_plot")
    db.commit()
    return obj
