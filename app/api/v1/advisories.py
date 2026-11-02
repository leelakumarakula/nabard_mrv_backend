import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict, Forbidden
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import Advisory, AdvisoryDelivery, Farmer, FieldRecord, PracticeRecord, Programme
from app.schemas.common import Page, PageParams, page_of, paginate
from app.schemas.workstream import (
    AdvisoryDetail,
    AdvisoryIn,
    AdvisoryOut,
    AdvisoryUpdate,
    BulkDeliveryIn,
    DeliveryIn,
    DeliveryOut,
    DeliveryUpdate,
    PracticeIn,
    PracticeObserveIn,
    PracticeOut,
    PracticeUpdate,
)
from app.services import crud
from app.services.locate import can_edit_own, resolve
from app.services.record_types import MODULES_BY_CODE
from app.services.scope import apply_scope

router = APIRouter(tags=["advisories & practices"])


def _check_module(code: str) -> None:
    if code not in MODULES_BY_CODE:
        raise AppError(422, "unknown_module", f"Unknown module '{code}'")


def _scoped_advisory(db: Session, advisory_id: uuid.UUID, current: CurrentUser) -> Advisory:
    adv = crud.get_or_404(db, Advisory, advisory_id, "Advisory")
    current.ensure_access(adv.target_geo_unit_id, adv.target_organisation_id)
    return adv


@router.get("/advisories", response_model=Page[AdvisoryOut])
def list_advisories(params: PageParams = Depends(), origin_module: str | None = None, advisory_type: str | None = None,
                    status: str | None = None, hazard_type: str | None = None,
                    current: CurrentUser = Depends(require("advisory.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(Advisory).order_by(Advisory.created_at.desc())
    for column, value in ((Advisory.origin_module, origin_module), (Advisory.advisory_type, advisory_type),
                          (Advisory.status, status), (Advisory.hazard_type, hazard_type)):
        if value:
            stmt = stmt.where(column == value)
    stmt = apply_scope(stmt, current, Advisory.target_geo_unit_id, Advisory.target_organisation_id)
    return page_of(*paginate(db, stmt, params), params, AdvisoryOut)


@router.post("/advisories", response_model=AdvisoryOut, status_code=201)
def create_advisory(body: AdvisoryIn, current: CurrentUser = Depends(require("advisory.manage")),
                    db: Session = Depends(get_db)) -> Advisory:
    _check_module(body.origin_module)
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    current.ensure_access(body.target_geo_unit_id, body.target_organisation_id)
    if body.risk_record_id:
        risk = crud.get_or_404(db, FieldRecord, body.risk_record_id, "Risk record")
        if risk.data_nature != "model_prediction" and risk.record_type not in ("pest_inspection", "climate_observation"):
            raise AppError(422, "invalid_risk_reference", "The linked record is not a risk estimate or observation")
    adv = crud.create(db, current, Advisory, {**body.model_dump(), "status": "draft"}, "advisory")
    db.commit()
    db.refresh(adv)
    return adv


@router.get("/advisories/{advisory_id}", response_model=AdvisoryDetail)
def get_advisory(advisory_id: uuid.UUID, current: CurrentUser = Depends(require("advisory.read")),
                 db: Session = Depends(get_db)) -> AdvisoryDetail:
    adv = _scoped_advisory(db, advisory_id, current)
    total, delivered, responded = db.execute(select(
        func.count(AdvisoryDelivery.id), func.count(AdvisoryDelivery.delivered_at),
        func.count(AdvisoryDelivery.response)).where(AdvisoryDelivery.advisory_id == adv.id)).one()
    return AdvisoryDetail.model_validate(adv).model_copy(update={
        "deliveries_total": total, "deliveries_delivered": delivered, "deliveries_responded": responded})


@router.patch("/advisories/{advisory_id}", response_model=AdvisoryOut)
def update_advisory(advisory_id: uuid.UUID, body: AdvisoryUpdate, current: CurrentUser = Depends(require("advisory.manage")),
                    db: Session = Depends(get_db)) -> Advisory:
    adv = _scoped_advisory(db, advisory_id, current)
    if adv.status != "draft":
        raise Conflict("Only draft advisories can be edited; withdraw and issue a new advisory instead")
    crud.update(db, current, adv, body, "advisory")
    db.commit()
    db.refresh(adv)
    return adv


@router.post("/advisories/{advisory_id}/issue", response_model=AdvisoryOut)
def issue_advisory(advisory_id: uuid.UUID, current: CurrentUser = Depends(require("advisory.manage")),
                   db: Session = Depends(get_db)) -> Advisory:
    adv = _scoped_advisory(db, advisory_id, current)
    if adv.status != "draft":
        raise Conflict(f"Advisory is already {adv.status}")
    crud.update(db, current, adv, {"status": "issued", "issued_at": utcnow()}, "advisory", action="issue")
    db.commit()
    db.refresh(adv)
    return adv


@router.post("/advisories/{advisory_id}/withdraw", response_model=AdvisoryOut)
def withdraw_advisory(advisory_id: uuid.UUID, current: CurrentUser = Depends(require("advisory.manage")),
                      db: Session = Depends(get_db)) -> Advisory:
    adv = _scoped_advisory(db, advisory_id, current)
    if adv.status == "withdrawn":
        raise Conflict("Advisory is already withdrawn")
    crud.update(db, current, adv, {"status": "withdrawn"}, "advisory", action="withdraw")
    db.commit()
    db.refresh(adv)
    return adv


@router.get("/advisories/{advisory_id}/deliveries", response_model=Page[DeliveryOut])
def list_deliveries(advisory_id: uuid.UUID, params: PageParams = Depends(),
                    current: CurrentUser = Depends(require("advisory.read")), db: Session = Depends(get_db)) -> dict:
    _scoped_advisory(db, advisory_id, current)
    stmt = apply_scope(select(AdvisoryDelivery).where(AdvisoryDelivery.advisory_id == advisory_id)
                       .order_by(AdvisoryDelivery.created_at), current, AdvisoryDelivery.geo_unit_id,
                       AdvisoryDelivery.organisation_id)
    return page_of(*paginate(db, stmt, params), params, DeliveryOut)


def _delivery_for(db: Session, current: CurrentUser, adv: Advisory, farmer_id: uuid.UUID | None,
                  organisation_id: uuid.UUID | None, data: dict) -> AdvisoryDelivery:
    loc = resolve(db, current, farmer_id=farmer_id, organisation_id=organisation_id)
    return crud.create(db, current, AdvisoryDelivery, {
        **data, "advisory_id": adv.id, "farmer_id": loc.farmer_id, "organisation_id": loc.organisation_id,
        "geo_unit_id": loc.geo_unit_id, "is_demo": adv.is_demo}, "advisory_delivery")


@router.post("/advisories/{advisory_id}/deliveries", response_model=DeliveryOut, status_code=201)
def add_delivery(advisory_id: uuid.UUID, body: DeliveryIn, current: CurrentUser = Depends(require("advisory.manage")),
                 db: Session = Depends(get_db)) -> AdvisoryDelivery:
    adv = _scoped_advisory(db, advisory_id, current)
    if adv.status != "issued":
        raise Conflict("Deliveries can only be recorded for issued advisories")
    if not (body.farmer_id or body.organisation_id):
        raise AppError(422, "recipient_required", "Select a farmer or FPO recipient")
    obj = _delivery_for(db, current, adv, body.farmer_id, body.organisation_id,
                        body.model_dump(exclude={"farmer_id", "organisation_id"}))
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/advisories/{advisory_id}/deliveries/bulk", response_model=list[DeliveryOut], status_code=201)
def add_deliveries_bulk(advisory_id: uuid.UUID, body: BulkDeliveryIn,
                        current: CurrentUser = Depends(require("advisory.manage")), db: Session = Depends(get_db)) -> list:
    adv = _scoped_advisory(db, advisory_id, current)
    if adv.status != "issued":
        raise Conflict("Deliveries can only be recorded for issued advisories")
    existing = set(db.scalars(select(AdvisoryDelivery.farmer_id).where(AdvisoryDelivery.advisory_id == adv.id,
                                                                       AdvisoryDelivery.channel == body.channel)))
    created = [_delivery_for(db, current, adv, fid, None, {"channel": body.channel, "sent_at": body.sent_at or utcnow(),
                                                           "delivered_at": body.delivered_at})
               for fid in dict.fromkeys(body.farmer_ids) if fid not in existing]
    db.commit()
    for obj in created:
        db.refresh(obj)
    return created


@router.patch("/advisory-deliveries/{delivery_id}", response_model=DeliveryOut)
def update_delivery(delivery_id: uuid.UUID, body: DeliveryUpdate, current: CurrentUser = Depends(require("advisory.manage")),
                    db: Session = Depends(get_db)) -> AdvisoryDelivery:
    delivery = crud.get_or_404(db, AdvisoryDelivery, delivery_id, "Delivery")
    current.ensure_access(delivery.geo_unit_id, delivery.organisation_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("response") and not data.get("response_at") and not delivery.response_at:
        data["response_at"] = utcnow()
    crud.update(db, current, delivery, data, "advisory_delivery")
    db.commit()
    db.refresh(delivery)
    return delivery


# -- practice records ---------------------------------------------------------------------------------------------

def _scoped_practice(db: Session, practice_id: uuid.UUID, current: CurrentUser) -> PracticeRecord:
    rec = crud.get_or_404(db, PracticeRecord, practice_id, "Practice record")
    current.ensure_access(rec.geo_unit_id, rec.organisation_id)
    return rec


@router.get("/practice-records", response_model=Page[PracticeOut])
def list_practices(params: PageParams = Depends(), farmer_id: uuid.UUID | None = None, module_code: str | None = None,
                   activity_code: str | None = None, status: str | None = None, advisory_id: uuid.UUID | None = None,
                   verification_status: str | None = None, demonstration_site_id: uuid.UUID | None = None,
                   current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(PracticeRecord).order_by(PracticeRecord.created_at.desc())
    for column, value in ((PracticeRecord.farmer_id, farmer_id), (PracticeRecord.module_code, module_code),
                          (PracticeRecord.activity_code, activity_code), (PracticeRecord.status, status),
                          (PracticeRecord.advisory_id, advisory_id),
                          (PracticeRecord.verification_status, verification_status),
                          (PracticeRecord.demonstration_site_id, demonstration_site_id)):
        if value:
            stmt = stmt.where(column == value)
    stmt = apply_scope(stmt, current, PracticeRecord.geo_unit_id, PracticeRecord.organisation_id)
    return page_of(*paginate(db, stmt, params), params, PracticeOut)


@router.post("/practice-records", response_model=PracticeOut, status_code=201)
def create_practice(body: PracticeIn, current: CurrentUser = Depends(require("module.write")),
                    db: Session = Depends(get_db)) -> PracticeRecord:
    _check_module(body.module_code)
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    loc = resolve(db, current, farmer_id=body.farmer_id, farm_plot_id=body.farm_plot_id)
    if body.status == "reported" and not body.reported_action:
        raise AppError(422, "reported_action_required", "Describe the action the farmer reported")
    obj = crud.create(db, current, PracticeRecord, {
        **body.model_dump(), "farm_plot_id": loc.farm_plot_id, "geo_unit_id": loc.geo_unit_id,
        "organisation_id": loc.organisation_id, "evidence_basis": "reported"}, "practice_record")
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/practice-records/{practice_id}", response_model=PracticeOut)
def get_practice(practice_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                 db: Session = Depends(get_db)) -> PracticeRecord:
    return _scoped_practice(db, practice_id, current)


@router.patch("/practice-records/{practice_id}", response_model=PracticeOut)
def update_practice(practice_id: uuid.UUID, body: PracticeUpdate, current: CurrentUser = Depends(require("module.write")),
                    db: Session = Depends(get_db)) -> PracticeRecord:
    rec = _scoped_practice(db, practice_id, current)
    if rec.evidence_basis == "observed":
        raise Conflict("Field-observed practice records cannot be edited")
    if rec.verification_status != "unverified":
        raise Conflict("Independently verified practice records cannot be edited")
    if not can_edit_own(current, rec.created_by_id):
        raise Forbidden("You can only edit records you created")
    crud.update(db, current, rec, body, "practice_record")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/practice-records/{practice_id}/observe", response_model=PracticeOut)
def observe_practice(practice_id: uuid.UUID, body: PracticeObserveIn, current: CurrentUser = Depends(require("module.write")),
                     db: Session = Depends(get_db)) -> PracticeRecord:
    """Record what was actually observed in the field. Delivery or self-report alone never counts as adoption."""
    rec = _scoped_practice(db, practice_id, current)
    if rec.verification_status != "unverified":
        raise Conflict("This practice record already has an independent verification result")
    crud.update(db, current, rec, {
        "observed_action": body.observed_action, "action_date": body.action_date, "evidence_basis": "observed",
        "status": "observed" if body.adopted else "not_adopted", "observed_by_id": current.id,
    }, "practice_record", action="observe")
    db.commit()
    db.refresh(rec)
    return rec


@router.get("/farmers/{farmer_id}/advisory-deliveries", response_model=list[DeliveryOut])
def farmer_deliveries(farmer_id: uuid.UUID, current: CurrentUser = Depends(require("advisory.read")),
                      db: Session = Depends(get_db)) -> list:
    farmer = crud.get_or_404(db, Farmer, farmer_id, "Farmer")
    current.ensure_access(farmer.geo_unit_id, farmer.primary_fpo_id)
    return list(db.scalars(select(AdvisoryDelivery).where(AdvisoryDelivery.farmer_id == farmer_id)
                           .order_by(AdvisoryDelivery.created_at.desc())).unique())
