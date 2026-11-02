import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict, Forbidden
from app.db.session import get_db
from app.models import (
    Activity,
    DemonstrationSite,
    Farmer,
    FieldRecord,
    FieldRecordParticipant,
    PestSurveillanceCluster,
    Programme,
    Workstream,
)
from app.schemas.common import Message, Page, PageParams, page_of, paginate
from app.schemas.workstream import (
    ActivityOut,
    FieldRecordIn,
    FieldRecordOut,
    FieldRecordUpdate,
    ParticipantIn,
    ParticipantOut,
)
from app.services import audit, crud, record_types
from app.services.locate import can_edit_own, resolve
from app.services.scope import apply_scope
from app.services.validation import validate_field_record

router = APIRouter(tags=["workstreams"])

EDITABLE_VALIDATION = {"pending_validation", "rejected", "exception"}


@router.get("/workstreams")
def list_workstreams(current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> list[dict]:
    counts_stmt = apply_scope(select(FieldRecord.module_code, FieldRecord.validation_status, func.count())
                              .group_by(FieldRecord.module_code, FieldRecord.validation_status), current,
                              FieldRecord.geo_unit_id, FieldRecord.organisation_id)
    counts: dict[str, dict[str, int]] = {}
    for module, status, n in db.execute(counts_stmt):
        counts.setdefault(module, {})[status] = n
    return [{
        "code": m.code, "number": m.number, "name": m.name, "description": m.description,
        "record_types": [record_types.describe(rt) for rt in record_types.RECORD_TYPES if rt.module_code == m.code],
        "record_counts": counts.get(m.code, {}),
    } for m in record_types.MODULES]


@router.get("/workstreams/record-types")
def list_record_types(module_code: str | None = None, current: CurrentUser = Depends(require("module.read"))) -> list[dict]:
    return [record_types.describe(rt) for rt in record_types.RECORD_TYPES if not module_code or rt.module_code == module_code]


@router.get("/activities", response_model=list[ActivityOut])
def list_activities(module_code: str | None = None, current: CurrentUser = Depends(require("module.read")),
                    db: Session = Depends(get_db)) -> list[ActivityOut]:
    stmt = (select(Activity, Workstream.module_code).join(Workstream, Workstream.id == Activity.workstream_id)
            .order_by(Activity.activity_code))
    if module_code:
        stmt = stmt.where(Workstream.module_code == module_code)
    return [ActivityOut.model_validate(a).model_copy(update={"module_code": m}) for a, m in db.execute(stmt)]


def _record_type(code: str) -> record_types.RecordType:
    rt = record_types.RECORD_TYPES_BY_CODE.get(code)
    if rt is None:
        raise AppError(422, "unknown_record_type", f"Unknown record type '{code}'")
    return rt


def _link_site_and_cluster(db: Session, current: CurrentUser, data: dict) -> dict:
    """Demonstration visits take their location from the site; cluster inspections must use a cluster in scope."""
    if data.get("demonstration_site_id"):
        site = crud.get_or_404(db, DemonstrationSite, data["demonstration_site_id"], "Demonstration site")
        current.ensure_access(site.geo_unit_id, site.organisation_id)
        data.setdefault("geo_unit_id", site.geo_unit_id)
        if not data.get("farm_plot_id") and not data.get("farmer_id"):
            data["farm_plot_id"] = site.farm_plot_id
        data["organisation_id"] = data.get("organisation_id") or site.organisation_id
        data["geo_unit_id"] = data.get("geo_unit_id") or site.geo_unit_id
    if data.get("surveillance_cluster_id"):
        cluster = crud.get_or_404(db, PestSurveillanceCluster, data["surveillance_cluster_id"], "Surveillance cluster")
        current.ensure_access(cluster.geo_unit_id, cluster.organisation_id)
    return data


def _scoped_record(db: Session, record_id: uuid.UUID, current: CurrentUser) -> FieldRecord:
    rec = crud.get_or_404(db, FieldRecord, record_id, "Record")
    current.ensure_access(rec.geo_unit_id, rec.organisation_id)
    return rec


@router.get("/field-records", response_model=Page[FieldRecordOut])
def list_field_records(params: PageParams = Depends(), module_code: str | None = None, record_type: str | None = None,
                       farmer_id: uuid.UUID | None = None, farm_plot_id: uuid.UUID | None = None,
                       organisation_id: uuid.UUID | None = None, device_id: uuid.UUID | None = None,
                       validation_status: str | None = None, submission_status: str | None = None,
                       data_nature: str | None = None, search: str | None = None,
                       demonstration_site_id: uuid.UUID | None = None, surveillance_cluster_id: uuid.UUID | None = None,
                       date_from: datetime | None = None, date_to: datetime | None = None,
                       current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(FieldRecord).order_by(FieldRecord.created_at.desc())
    for column, value in ((FieldRecord.module_code, module_code), (FieldRecord.record_type, record_type),
                          (FieldRecord.farmer_id, farmer_id), (FieldRecord.farm_plot_id, farm_plot_id),
                          (FieldRecord.organisation_id, organisation_id), (FieldRecord.device_id, device_id),
                          (FieldRecord.validation_status, validation_status),
                          (FieldRecord.submission_status, submission_status), (FieldRecord.data_nature, data_nature),
                          (FieldRecord.demonstration_site_id, demonstration_site_id),
                          (FieldRecord.surveillance_cluster_id, surveillance_cluster_id)):
        if value:
            stmt = stmt.where(column == value)
    if search:
        stmt = stmt.where(or_(FieldRecord.title.ilike(f"%{search}%"), FieldRecord.source_record_id.ilike(f"%{search}%")))
    if date_from:
        stmt = stmt.where(FieldRecord.observed_at >= date_from)
    if date_to:
        stmt = stmt.where(FieldRecord.observed_at <= date_to)
    stmt = apply_scope(stmt, current, FieldRecord.geo_unit_id, FieldRecord.organisation_id)
    return page_of(*paginate(db, stmt, params), params, FieldRecordOut)


@router.post("/field-records", response_model=FieldRecordOut, status_code=201)
def create_field_record(body: FieldRecordIn, current: CurrentUser = Depends(require("module.write")),
                        db: Session = Depends(get_db)) -> FieldRecord:
    rt = _record_type(body.record_type)
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    links = _link_site_and_cluster(db, current, body.model_dump())
    loc = resolve(db, current, farmer_id=links.get("farmer_id"), farm_plot_id=links.get("farm_plot_id"),
                  device_id=body.device_id, organisation_id=links.get("organisation_id"), geo_unit_id=links.get("geo_unit_id"))
    clean, errors = record_types.validate_attributes(rt, body.attributes)
    attributes = clean if not errors else body.attributes
    value, unit = record_types.value_and_unit(rt, clean) if not errors else (None, None)
    rec = crud.create(db, current, FieldRecord, {
        **body.model_dump(exclude={"farmer_id", "farm_plot_id", "geo_unit_id", "organisation_id", "device_id", "attributes"}),
        "module_code": rt.module_code, "data_nature": rt.data_nature, "attributes": attributes,
        "activity_code": body.activity_code or rt.default_activity_code, "value": value, "unit": unit,
        "farmer_id": loc.farmer_id, "farm_plot_id": loc.farm_plot_id, "geo_unit_id": loc.geo_unit_id,
        "organisation_id": loc.organisation_id, "device_id": loc.device_id, "submission_status": "draft",
    }, "field_record", summary=f"{rt.name} draft")
    db.commit()
    db.refresh(rec)
    return rec


@router.get("/field-records/{record_id}", response_model=FieldRecordOut)
def get_field_record(record_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                     db: Session = Depends(get_db)) -> FieldRecord:
    return _scoped_record(db, record_id, current)


@router.patch("/field-records/{record_id}", response_model=FieldRecordOut)
def update_field_record(record_id: uuid.UUID, body: FieldRecordUpdate,
                        current: CurrentUser = Depends(require("module.write")), db: Session = Depends(get_db)) -> FieldRecord:
    rec = _scoped_record(db, record_id, current)
    _ensure_editable(rec)
    if not can_edit_own(current, rec.created_by_id):
        raise Forbidden("You can only edit records you created")
    data = body.model_dump(exclude_unset=True)
    if "demonstration_site_id" in data or "surveillance_cluster_id" in data:
        _link_site_and_cluster(db, current, dict(data))
    rt = _record_type(rec.record_type)
    if {"farmer_id", "farm_plot_id", "device_id", "organisation_id"} & data.keys():
        loc = resolve(db, current, farmer_id=data.get("farmer_id", rec.farmer_id),
                      farm_plot_id=data.get("farm_plot_id", rec.farm_plot_id),
                      device_id=data.get("device_id", rec.device_id),
                      organisation_id=data.get("organisation_id", rec.organisation_id), geo_unit_id=rec.geo_unit_id)
        data.update(farmer_id=loc.farmer_id, farm_plot_id=loc.farm_plot_id, geo_unit_id=loc.geo_unit_id,
                    organisation_id=loc.organisation_id, device_id=loc.device_id)
    if "attributes" in data:
        clean, errors = record_types.validate_attributes(rt, data["attributes"] or {})
        if not errors:
            data["attributes"] = clean
            data["value"], data["unit"] = record_types.value_and_unit(rt, clean)
    data["validation_status"] = "pending_validation"
    data["submission_status"] = "draft"
    crud.update(db, current, rec, data, "field_record")
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/field-records/{record_id}/submit", response_model=FieldRecordOut)
def submit_field_record(record_id: uuid.UUID, current: CurrentUser = Depends(require("module.write")),
                        db: Session = Depends(get_db)) -> FieldRecord:
    rec = _scoped_record(db, record_id, current)
    if rec.submission_status == "imported":
        raise Conflict("Imported records are validated on import")
    if rec.submission_status == "submitted" and rec.validation_status == "valid":
        raise Conflict("Record is already submitted and valid")
    rt = _record_type(rec.record_type)
    clean, errors = record_types.validate_attributes(rt, rec.attributes or {})
    if not errors:
        rec.attributes = clean
        rec.value, rec.unit = record_types.value_and_unit(rt, clean)
    outcome = validate_field_record(db, rec)
    rec.submission_status = "submitted"
    rec.validation_status, rec.validation_messages = outcome.status, outcome.messages
    audit.stamp(rec, current)
    audit.record(db, current, "submit", "field_record", rec.id, summary=f"Validation: {outcome.status}",
                 after={"validation_status": outcome.status, "messages": outcome.messages})
    db.commit()
    db.refresh(rec)
    return rec


@router.get("/field-records/{record_id}/participants", response_model=list[ParticipantOut])
def list_participants(record_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                      db: Session = Depends(get_db)) -> list:
    _scoped_record(db, record_id, current)
    return list(db.scalars(select(FieldRecordParticipant).where(FieldRecordParticipant.field_record_id == record_id)
                           .order_by(FieldRecordParticipant.created_at)).unique())


def _ensure_editable(rec: FieldRecord) -> None:
    """A record and its participant list change only before it is submitted and validated."""
    if rec.submission_status == "imported":
        raise Conflict("Imported records are owned by their source system; correct them at the source and re-sync")
    if rec.submission_status == "submitted" and rec.validation_status not in EDITABLE_VALIDATION:
        raise Conflict("Submitted, valid records cannot be edited")


@router.post("/field-records/{record_id}/participants", response_model=ParticipantOut, status_code=201)
def add_participant(record_id: uuid.UUID, body: ParticipantIn, current: CurrentUser = Depends(require("module.write")),
                    db: Session = Depends(get_db)) -> FieldRecordParticipant:
    rec = _scoped_record(db, record_id, current)
    _ensure_editable(rec)
    if not _record_type(rec.record_type).supports_participants:
        raise AppError(422, "participants_not_supported", "This record type does not record participants")
    farmer = crud.get_or_404(db, Farmer, body.farmer_id, "Farmer")
    current.ensure_access(farmer.geo_unit_id, farmer.primary_fpo_id)
    if db.scalar(select(FieldRecordParticipant.id).where(FieldRecordParticipant.field_record_id == rec.id,
                                                         FieldRecordParticipant.farmer_id == farmer.id)):
        raise Conflict("Farmer is already listed as a participant")
    obj = crud.create(db, current, FieldRecordParticipant, {**body.model_dump(), "field_record_id": rec.id},
                      "field_record_participant")
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/field-record-participants/{participant_id}", response_model=Message)
def remove_participant(participant_id: uuid.UUID, current: CurrentUser = Depends(require("module.write")),
                       db: Session = Depends(get_db)) -> Message:
    participant = crud.get_or_404(db, FieldRecordParticipant, participant_id, "Participant")
    _ensure_editable(_scoped_record(db, participant.field_record_id, current))
    audit.record(db, current, "delete", "field_record_participant", participant.id, before=audit.snapshot(participant))
    db.delete(participant)
    db.commit()
    return Message(message="Participant removed")
