import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Forbidden
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import Activity, Advisory, AdvisoryDelivery, Farm, Farmer, FarmPlot, FieldRecord, PracticeRecord
from app.schemas.registry import FarmerOut, PlotOut
from app.services import audit, crud
from app.services.integration.farmfuture import farm_boundary
from app.services.record_types import MODULES_BY_CODE, NATURE_LABELS, RECORD_TYPES_BY_CODE

MAX_RECORDS = 500

router = APIRouter(prefix="/me", tags=["farmer self-service"])


class MyAdvisoryOut(BaseModel):
    delivery_id: uuid.UUID
    advisory_id: uuid.UUID
    advisory_type: str
    title: str
    message: str
    recommendation: str | None
    issued_at: str | None
    channel: str
    response: str | None
    understood: bool | None
    is_demo: bool


class MyResponseIn(BaseModel):
    response: str = Field(min_length=1, max_length=2000)
    understood: bool | None = None


def _farmer(db: Session, current: CurrentUser) -> Farmer:
    if not current.user.farmer_id:
        raise AppError(404, "no_farmer_profile", "This account is not linked to a farmer profile")
    return crud.get_or_404(db, Farmer, current.user.farmer_id, "Farmer")


@router.get("/farmer")
def my_profile(current: CurrentUser = Depends(require("self.read")), db: Session = Depends(get_db)) -> dict:
    farmer = _farmer(db, current)
    plots = db.scalars(select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id).where(Farm.farmer_id == farmer.id)).unique()
    return {"farmer": FarmerOut.model_validate(farmer), "plots": [PlotOut.model_validate(p) for p in plots]}


@router.get("/advisories", response_model=list[MyAdvisoryOut])
def my_advisories(current: CurrentUser = Depends(require("self.read")), db: Session = Depends(get_db)) -> list[MyAdvisoryOut]:
    farmer = _farmer(db, current)
    rows = db.execute(select(AdvisoryDelivery, Advisory).join(Advisory, Advisory.id == AdvisoryDelivery.advisory_id)
                      .where(AdvisoryDelivery.farmer_id == farmer.id, Advisory.status == "issued")
                      .order_by(Advisory.issued_at.desc())).unique().all()
    return [MyAdvisoryOut(delivery_id=d.id, advisory_id=a.id, advisory_type=a.advisory_type, title=a.title,
                          message=a.message, recommendation=a.recommendation,
                          issued_at=a.issued_at.isoformat() if a.issued_at else None, channel=d.channel,
                          response=d.response, understood=d.understood, is_demo=a.is_demo) for d, a in rows]


@router.post("/advisories/{delivery_id}/response", response_model=MyAdvisoryOut)
def respond(delivery_id: uuid.UUID, body: MyResponseIn, current: CurrentUser = Depends(require("self.read")),
            db: Session = Depends(get_db)) -> MyAdvisoryOut:
    farmer = _farmer(db, current)
    delivery = crud.get_or_404(db, AdvisoryDelivery, delivery_id, "Advisory")
    if delivery.farmer_id != farmer.id:
        raise Forbidden("This advisory was not sent to you")
    delivery.response, delivery.understood, delivery.response_at = body.response, body.understood, utcnow()
    delivery.read_at = delivery.read_at or utcnow()
    audit.record(db, current, "farmer_response", "advisory_delivery", delivery.id, after=body.model_dump())
    db.commit()
    a = db.get(Advisory, delivery.advisory_id)
    return MyAdvisoryOut(delivery_id=delivery.id, advisory_id=a.id, advisory_type=a.advisory_type, title=a.title,
                         message=a.message, recommendation=a.recommendation,
                         issued_at=a.issued_at.isoformat() if a.issued_at else None, channel=delivery.channel,
                         response=delivery.response, understood=delivery.understood, is_demo=a.is_demo)


def _number(value):
    return None if value is None else float(value)


def _record_fields(rec: FieldRecord) -> list[dict]:
    """The record's own measurements, labelled from its record type; reference ids are left out."""
    rt = RECORD_TYPES_BY_CODE.get(rec.record_type)
    attrs = rec.attributes or {}
    if rt is None:
        return []
    return [{"name": f.name, "label": f.label, "value": attrs[f.name], "unit": f.unit, "type": f.type}
            for f in rt.fields if attrs.get(f.name) is not None and not f.name.endswith(("_ref", "_id"))]


@router.get("/farm")
def my_farm(current: CurrentUser = Depends(require("self.read")), db: Session = Depends(get_db)) -> dict:
    """What the platform holds about the signed-in farmer's own farm: profile, plots, practices and the records about
    their plots. Only this farmer's data. Internal working detail is left out: drafts, rejected records, data-quality
    messages, reviewer notes and names."""
    farmer = _farmer(db, current)
    plots = list(db.scalars(select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id)
                            .where(Farm.farmer_id == farmer.id, FarmPlot.is_archived == false())
                            .order_by(FarmPlot.plot_code)).unique())
    plot_codes = {p.id: p.plot_code for p in plots}
    activity_names = dict(db.execute(select(Activity.activity_code, Activity.name)).all())

    # Read fully before the next query: SQL Server cannot run a second query while a result is still open
    practices = list(db.scalars(select(PracticeRecord).where(PracticeRecord.farmer_id == farmer.id)
                                .order_by(PracticeRecord.action_date.desc(), PracticeRecord.created_at.desc())).unique())
    mine = or_(FieldRecord.farmer_id == farmer.id, FieldRecord.farm_plot_id.in_(list(plot_codes) or [uuid.UUID(int=0)]))
    record_stmt = (select(FieldRecord).where(mine, FieldRecord.submission_status != "draft",
                                             FieldRecord.validation_status != "rejected")
                   .order_by(FieldRecord.observed_at.desc(), FieldRecord.created_at.desc()))
    records = list(db.scalars(record_stmt.limit(MAX_RECORDS + 1)).unique())

    def module_name(code: str) -> str:
        module = MODULES_BY_CODE.get(code)
        return f"{module.number}. {module.name}" if module else code

    return {
        "farmer": {"full_name": farmer.full_name, "farmer_code": farmer.farmer_code, "village": farmer.village,
                   "area": farmer.geo_unit.label if farmer.geo_unit else None,
                   "fpo": farmer.primary_fpo.name if farmer.primary_fpo else None,
                   "consent_status": farmer.consent_status, "consent_date": farmer.consent_date, "is_demo": farmer.is_demo},
        "plots": [{"id": p.id, "plot_code": p.plot_code, "area": _number(p.area), "area_unit": p.area_unit, "crop": p.crop,
                   "variety": p.variety, "plantation_year": p.plantation_year, "plantation_stage": p.plantation_stage,
                   "shade_type": p.shade_type, "irrigation_type": p.irrigation_type,
                   "latitude": _number(p.latitude), "longitude": _number(p.longitude), "is_demo": p.is_demo} for p in plots],
        "practices": [{"id": r.id, "activity_code": r.activity_code, "activity_name": activity_names.get(r.activity_code),
                       "workstream": module_name(r.module_code), "status": r.status,
                       "verification_status": r.verification_status, "recommended_action": r.recommended_action,
                       "reported_action": r.reported_action, "observed_action": r.observed_action,
                       "action_date": r.action_date, "recorded_at": r.created_at,
                       "plot_code": plot_codes.get(r.farm_plot_id), "is_demo": r.is_demo} for r in practices],
        "records": [{"id": r.id, "module_code": r.module_code, "workstream": module_name(r.module_code),
                     "record_type": r.record_type,
                     "type_name": RECORD_TYPES_BY_CODE[r.record_type].name if r.record_type in RECORD_TYPES_BY_CODE else r.record_type,
                     "nature": r.data_nature, "nature_label": NATURE_LABELS.get(r.data_nature, r.data_nature),
                     "observed_at": r.observed_at, "period_start": r.period_start, "period_end": r.period_end,
                     "plot_code": plot_codes.get(r.farm_plot_id), "title": r.title,
                     "fields": _record_fields(r), "is_demo": r.is_demo} for r in records[:MAX_RECORDS]],
        "records_truncated": len(records) > MAX_RECORDS,
    }


@router.get("/plots/{plot_id}/boundary")
def my_plot_boundary(plot_id: uuid.UUID, current: CurrentUser = Depends(require("self.read")),
                     db: Session = Depends(get_db)) -> dict:
    """Real outline of one of the farmer's own plots, read live from FarmFuture (not stored)."""
    farmer = _farmer(db, current)
    owner = db.scalar(select(Farm.farmer_id).join(FarmPlot, FarmPlot.farm_id == Farm.id)
                      .where(FarmPlot.id == plot_id, FarmPlot.is_archived == false()))
    if owner != farmer.id:
        raise Forbidden("This plot is not yours")
    return farm_boundary(db, plot_id)
