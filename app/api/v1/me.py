import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Forbidden
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import Advisory, AdvisoryDelivery, Farm, Farmer, FarmPlot
from app.schemas.registry import FarmerOut, PlotOut
from app.services import audit, crud

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
