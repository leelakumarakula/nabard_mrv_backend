import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Forbidden
from app.db.session import get_db
from app.models import (
    Advisory,
    AdvisoryDelivery,
    BaselineRecord,
    CommunityResourcePerson,
    DemonstrationSite,
    Device,
    EvidenceAsset,
    EvidenceLink,
    Farmer,
    FarmPlot,
    FieldRecord,
    IndicatorResult,
    MonitoringRecord,
    PestSurveillanceCluster,
    PracticeRecord,
    ReviewTask,
    VerificationFinding,
    VerificationSample,
    VerificationSampleItem,
)
from app.schemas.common import Page, PageParams, paginate
from app.schemas.evidence import EvidenceLinkIn, EvidenceLinkOut, EvidenceOut, EvidenceReviewIn
from app.services import audit, crud
from app.services.settings import get_setting
from app.services.storage import checksum, get_storage, validate_upload

router = APIRouter(prefix="/evidence", tags=["evidence"])

LINK_MODELS: dict[str, type] = {
    "farmer": Farmer, "farm_plot": FarmPlot, "field_record": FieldRecord, "monitoring_record": MonitoringRecord,
    "baseline_record": BaselineRecord, "practice_record": PracticeRecord, "advisory": Advisory,
    "advisory_delivery": AdvisoryDelivery, "indicator_result": IndicatorResult, "review_task": ReviewTask,
    "verification_finding": VerificationFinding, "device": Device, "demonstration_site": DemonstrationSite,
    "pest_surveillance_cluster": PestSurveillanceCluster, "community_resource_person": CommunityResourcePerson,
    "verification_sample": VerificationSample, "verification_sample_item": VerificationSampleItem,
}


def _entity_scope(db: Session, entity_type: str, entity_id: uuid.UUID) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    model = LINK_MODELS.get(entity_type)
    if model is None:
        raise AppError(422, "invalid_entity", f"Evidence cannot be linked to '{entity_type}'")
    entity = db.get(model, entity_id)
    if entity is None:
        raise AppError(404, "not_found", "Linked record not found")
    if isinstance(entity, Farmer):
        return entity.geo_unit_id, entity.primary_fpo_id
    if isinstance(entity, FarmPlot):
        farmer = db.get(Farmer, entity.farm.farmer_id)
        return farmer.geo_unit_id, farmer.primary_fpo_id
    if isinstance(entity, Advisory):
        return entity.target_geo_unit_id, entity.target_organisation_id
    return getattr(entity, "geo_unit_id", None), getattr(entity, "organisation_id", None)


def _can_read(current: CurrentUser, asset: EvidenceAsset) -> bool:
    if asset.access_classification == "personal" and not current.has("farmer.read_pii"):
        return False
    if asset.created_by_id == current.id or current.all_scope:
        return True
    if asset.geo_unit_id is None and asset.organisation_id is None:
        return False
    return current.can_access(asset.geo_unit_id, asset.organisation_id)


def _out(db: Session, asset: EvidenceAsset, current: CurrentUser) -> EvidenceOut:
    links = list(db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_asset_id == asset.id)))
    out = EvidenceOut.model_validate(asset).model_copy(update={"links": [EvidenceLinkOut.model_validate(link) for link in links]})
    if not current.has("farmer.read_pii"):
        out = out.model_copy(update={"latitude": None, "longitude": None})
    return out


@router.post("", response_model=EvidenceOut, status_code=201)
async def upload_evidence(
    file: UploadFile = File(...),
    description: str | None = Form(None),
    captured_at: datetime | None = Form(None),
    access_classification: Literal["internal", "restricted", "personal"] = Form("internal"),
    evidence_type: Literal["photo", "document", "field_form", "certificate", "dataset", "other"] | None = Form(None),
    location_consent: bool = Form(False),
    latitude: Decimal | None = Form(None),
    longitude: Decimal | None = Form(None),
    entity_type: str | None = Form(None),
    entity_id: uuid.UUID | None = Form(None),
    purpose: str | None = Form(None),
    current: CurrentUser = Depends(require("evidence.upload")),
    db: Session = Depends(get_db),
) -> EvidenceOut:
    content = await file.read()
    filename = (file.filename or "upload").replace("\\", "/").split("/")[-1][:255]
    mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    validate_upload(filename, mime, content, get_setting(db, "evidence.allowed_types"))
    if (latitude is not None or longitude is not None) and not location_consent:
        raise AppError(422, "location_consent_required", "GPS coordinates can only be stored with recorded consent")
    geo_unit_id = organisation_id = None
    if entity_type and entity_id:
        geo_unit_id, organisation_id = _entity_scope(db, entity_type, entity_id)
        if geo_unit_id or organisation_id:
            current.ensure_access(geo_unit_id, organisation_id)
    elif entity_type or entity_id:
        raise AppError(422, "link_incomplete", "Provide both entity_type and entity_id to link evidence")
    digest = checksum(content)
    key = get_storage().save(content, filename)
    asset = crud.create(db, current, EvidenceAsset, {
        "storage_key": key, "filename": filename, "mime_type": mime, "size_bytes": len(content),
        "checksum_sha256": digest, "description": description, "captured_at": captured_at,
        "location_consent": location_consent, "latitude": latitude if location_consent else None,
        "longitude": longitude if location_consent else None, "access_classification": access_classification,
        "evidence_type": evidence_type or ("photo" if mime.startswith("image/") else "document"), "source": "field_upload",
        "geo_unit_id": geo_unit_id, "organisation_id": organisation_id,
    }, "evidence_asset", summary=f"Uploaded {filename} ({len(content)} bytes)")
    if entity_type and entity_id:
        crud.create(db, current, EvidenceLink, {"evidence_asset_id": asset.id, "entity_type": entity_type,
                                                "entity_id": entity_id, "purpose": purpose or "supporting_evidence"},
                    "evidence_link")
    db.commit()
    return _out(db, asset, current)


@router.get("", response_model=Page[EvidenceOut])
def list_evidence(params: PageParams = Depends(), entity_type: str | None = None, entity_id: uuid.UUID | None = None,
                  review_status: str | None = None, mime_type: str | None = None, search: str | None = None,
                  current: CurrentUser = Depends(require("evidence.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(EvidenceAsset).order_by(EvidenceAsset.created_at.desc())
    if entity_type and entity_id:
        stmt = stmt.where(EvidenceAsset.id.in_(select(EvidenceLink.evidence_asset_id).where(
            EvidenceLink.entity_type == entity_type, EvidenceLink.entity_id == entity_id)))
    if review_status:
        stmt = stmt.where(EvidenceAsset.review_status == review_status)
    if mime_type:
        stmt = stmt.where(EvidenceAsset.mime_type == mime_type)
    if search:
        stmt = stmt.where(or_(EvidenceAsset.filename.ilike(f"%{search}%"), EvidenceAsset.description.ilike(f"%{search}%")))
    if not current.has("farmer.read_pii"):
        stmt = stmt.where(EvidenceAsset.access_classification != "personal")
    if not current.all_scope:
        stmt = stmt.where(or_(EvidenceAsset.created_by_id == current.id,
                              EvidenceAsset.geo_unit_id.in_(current.geo_ids or {uuid.UUID(int=0)}),
                              EvidenceAsset.organisation_id.in_(current.org_ids or {uuid.UUID(int=0)})))
    items, total = paginate(db, stmt, params)
    return {"items": [_out(db, a, current) for a in items], "total": total, "page": params.page, "page_size": params.page_size}


def _readable(db: Session, evidence_id: uuid.UUID, current: CurrentUser) -> EvidenceAsset:
    asset = crud.get_or_404(db, EvidenceAsset, evidence_id, "Evidence")
    if not _can_read(current, asset):
        raise Forbidden("You do not have access to this evidence")
    return asset


@router.get("/{evidence_id}", response_model=EvidenceOut)
def get_evidence(evidence_id: uuid.UUID, current: CurrentUser = Depends(require("evidence.read")),
                 db: Session = Depends(get_db)) -> EvidenceOut:
    return _out(db, _readable(db, evidence_id, current), current)


@router.get("/{evidence_id}/download")
def download_evidence(evidence_id: uuid.UUID, current: CurrentUser = Depends(require("evidence.read")),
                      db: Session = Depends(get_db)) -> FileResponse:
    asset = _readable(db, evidence_id, current)
    storage = get_storage()
    if not storage.exists(asset.storage_key):
        raise AppError(404, "file_missing", "The stored file is missing; restore it from backup")
    audit.record(db, current, "download", "evidence_asset", asset.id, summary=asset.filename)
    db.commit()
    return FileResponse(storage.path(asset.storage_key), media_type=asset.mime_type, filename=asset.filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/{evidence_id}/links", response_model=EvidenceLinkOut, status_code=201)
def link_evidence(evidence_id: uuid.UUID, body: EvidenceLinkIn, current: CurrentUser = Depends(require("evidence.upload")),
                  db: Session = Depends(get_db)) -> EvidenceLink:
    asset = _readable(db, evidence_id, current)
    geo, org = _entity_scope(db, body.entity_type, body.entity_id)
    if geo or org:
        current.ensure_access(geo, org)
    if asset.geo_unit_id is None and geo:
        asset.geo_unit_id, asset.organisation_id = geo, org
    link = crud.create(db, current, EvidenceLink, {**body.model_dump(), "evidence_asset_id": asset.id}, "evidence_link")
    db.commit()
    return link


@router.post("/{evidence_id}/review", response_model=EvidenceOut)
def review_evidence(evidence_id: uuid.UUID, body: EvidenceReviewIn, current: CurrentUser = Depends(require("evidence.review")),
                    db: Session = Depends(get_db)) -> EvidenceOut:
    asset = _readable(db, evidence_id, current)
    if asset.created_by_id == current.id:
        raise Forbidden("You cannot review evidence you uploaded")
    crud.update(db, current, asset, body, "evidence_asset", action="review")
    for link in db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_asset_id == asset.id)):
        link.review_status = body.review_status
    db.commit()
    return _out(db, asset, current)
