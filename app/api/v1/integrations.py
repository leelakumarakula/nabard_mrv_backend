import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import CurrentUser, get_current_user, oauth2_scheme, require
from app.core.errors import AppError, Conflict
from app.core.security import generate_token, hash_token
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import (
    Device,
    Farmer,
    FarmPlot,
    IntegrationFieldMapping,
    IntegrationSource,
    Programme,
    QuarantinedRecord,
    SourceRecordMap,
    SyncRun,
)
from app.schemas.common import Message, Page, PageParams, page_of, paginate
from app.schemas.integration import (
    BatchResultOut,
    DeviceImportOut,
    DiscardIn,
    FarmImportIn,
    FarmImportOut,
    IdentityMapIn,
    IdentityMapOut,
    InboundBatch,
    KeyOut,
    MappingIn,
    MappingOut,
    PlotMapIn,
    QuarantineOut,
    ReplayIn,
    SourceIn,
    SourceOut,
    SourceUpdate,
    SyncRunOut,
    TestPayloadIn,
)
from app.services import audit, crud
from app.services.integration.adapters import (
    LIVE_KEYED_ADAPTERS,
    PLATFORM_CALCULATION_ADAPTERS,
    catalogue,
    requires_mock_flag,
)
from app.services.integration.farmfuture import import_farms
from app.services.integration.farmfuture_data import import_devices
from app.services.integration.pipeline import process_batch
from app.services.integration.sync import farmfuture_token, run_pull

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _source(db: Session, source_code: str) -> IntegrationSource:
    source = db.scalar(select(IntegrationSource).where(IntegrationSource.source_code == source_code))
    if source is None:
        raise AppError(404, "not_found", f"Integration source '{source_code}' not found")
    return source


def _source_out(db: Session, source: IntegrationSource) -> SourceOut:
    last = db.scalar(select(SyncRun).where(SyncRun.source_id == source.id, SyncRun.trigger != "test")
                     .order_by(SyncRun.started_at.desc()).limit(1))
    open_q = db.scalar(select(func.count()).select_from(QuarantinedRecord).where(
        QuarantinedRecord.source_id == source.id, QuarantinedRecord.status == "open")) or 0
    schedule = (source.config or {}).get("schedule_daily_at")
    when = f"daily at {schedule} and on demand" if schedule else "on demand (Run pull)"
    if not source.enabled:
        status = "disabled"
    elif source.adapter_code == "unconfigured_rest":
        status = "not_configured – awaiting source API specification"
    elif source.is_mock:
        status = "MOCK adapter – synthetic data, not a live integration"
    elif source.adapter_code in LIVE_KEYED_ADAPTERS:
        status = (f"LIVE FarmFuture API – {when}" if get_settings().farmfuture_api_token
                  else "not_configured – FARMFUTURE_API_TOKEN is not set on the server")
    elif source.adapter_code in PLATFORM_CALCULATION_ADAPTERS:
        status = f"Platform calculation – {when}"
    elif requires_mock_flag(source.adapter_code) is False:
        status = f"LIVE public data service – {when}"
    elif source.source_type == "rest_push":
        status = "awaiting inbound data" if last is None else "receiving"
    else:
        status = "configured"
    return SourceOut.model_validate(source).model_copy(update={
        "connection_status": status, "last_sync_at": last.started_at if last else None,
        "last_sync_status": last.status if last else None, "open_quarantine": open_q})


def inbound_actor(source_code: str, request: Request, x_source_key: str | None = Header(None),
                  token: str | None = Depends(oauth2_scheme),
                  db: Session = Depends(get_db)) -> tuple[IntegrationSource, CurrentUser | None]:
    """Inbound calls authenticate with the source's API key (X-Source-Key) or a user token with integration.manage."""
    source = _source(db, source_code)
    if not source.enabled:
        raise AppError(403, "source_disabled", f"Source '{source_code}' is disabled")
    if x_source_key:
        if not source.inbound_key_hash or not hmac.compare_digest(source.inbound_key_hash, hash_token(x_source_key)):
            raise AppError(401, "invalid_source_key", "Invalid source key")
        return source, None
    current = get_current_user(request, token, db)
    current.require("integration.manage")
    return source, current


def _inbound(kind: str, body: InboundBatch, ctx: tuple, db: Session) -> BatchResultOut:
    source, actor = ctx
    run, outcomes = process_batch(db, source, kind, body.records, "push", actor)
    db.commit()
    return BatchResultOut(sync_run=SyncRunOut.model_validate(run), outcomes=outcomes)


@router.post("/{source_code}/measurements", response_model=BatchResultOut)
def receive_measurements(body: InboundBatch, ctx=Depends(inbound_actor), db: Session = Depends(get_db)):
    """Receive monitoring measurements. Idempotent on (source, source_record_id)."""
    return _inbound("measurements", body, ctx, db)


@router.post("/{source_code}/observations", response_model=BatchResultOut)
def receive_observations(body: InboundBatch, ctx=Depends(inbound_actor), db: Session = Depends(get_db)):
    """Receive climate, soil, pest/disease or device observations."""
    return _inbound("observations", body, ctx, db)


@router.post("/{source_code}/advisories", response_model=BatchResultOut)
def receive_advisories(body: InboundBatch, ctx=Depends(inbound_actor), db: Session = Depends(get_db)):
    """Receive advisories with delivery and response information."""
    return _inbound("advisories", body, ctx, db)


def _check_programme(db: Session, adapter_code: str, config: dict | None) -> None:
    """Catalogue pull adapters file their records under config.programme_code, so it must name an existing programme."""
    if not any(c["adapter_code"] == adapter_code for c in catalogue()):
        return
    code = str((config or {}).get("programme_code") or "").strip()
    if not code:
        raise AppError(422, "source_config", "Programme code is required for this adapter – enter the code shown on the "
                                             "Programmes page (e.g. TEST-FF)")
    if db.scalar(select(Programme.id).where(Programme.code == code)) is None:
        raise AppError(422, "source_config", f"Programme code '{code}' does not exist – use a code from the Programmes page")


def _check_mock_flag(adapter_code: str, is_mock: bool) -> None:
    """Synthetic adapters must be labelled mock and real-data adapters must not be, so data is never mislabelled."""
    required = requires_mock_flag(adapter_code)
    if required is True and not is_mock:
        raise AppError(422, "mock_adapter", f"Adapter '{adapter_code}' generates synthetic data; the source must be "
                                            "flagged is_mock=true")
    if required is False and is_mock:
        raise AppError(422, "mock_adapter", f"Adapter '{adapter_code}' fetches real data; the source must not be "
                                            "flagged is_mock")


@router.get("/catalogue")
def adapter_catalogue(current: CurrentUser = Depends(require("integration.read"))) -> list[dict]:
    """Ready-made pull adapters an operator can register: real public-data services and labelled samples."""
    return catalogue()


@router.get("/sources", response_model=list[SourceOut])
def list_sources(current: CurrentUser = Depends(require("integration.read")), db: Session = Depends(get_db)) -> list:
    return [_source_out(db, s) for s in db.scalars(select(IntegrationSource).order_by(IntegrationSource.name))]


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(body: SourceIn, current: CurrentUser = Depends(require("integration.manage")),
                  db: Session = Depends(get_db)) -> SourceOut:
    _check_mock_flag(body.adapter_code, body.is_mock)
    _check_programme(db, body.adapter_code, body.config)
    source = crud.create(db, current, IntegrationSource, body.model_dump(), "integration_source")
    db.commit()
    return _source_out(db, source)


@router.get("/sources/{source_code}", response_model=SourceOut)
def get_source(source_code: str, current: CurrentUser = Depends(require("integration.read")), db: Session = Depends(get_db)):
    return _source_out(db, _source(db, source_code))


@router.patch("/sources/{source_code}", response_model=SourceOut)
def update_source(source_code: str, body: SourceUpdate, current: CurrentUser = Depends(require("integration.manage")),
                  db: Session = Depends(get_db)) -> SourceOut:
    source = _source(db, source_code)
    if body.adapter_code is not None:
        _check_mock_flag(body.adapter_code, source.is_mock)
    if body.adapter_code is not None or body.config is not None:
        _check_programme(db, body.adapter_code or source.adapter_code,
                         body.config if body.config is not None else source.config)
    source = crud.update(db, current, source, body, "integration_source")
    db.commit()
    return _source_out(db, source)


@router.post("/sources/{source_code}/rotate-key", response_model=KeyOut)
def rotate_key(source_code: str, current: CurrentUser = Depends(require("integration.manage")),
               db: Session = Depends(get_db)) -> KeyOut:
    source = _source(db, source_code)
    key = generate_token()
    source.inbound_key_hash = hash_token(key)
    source.inbound_key_hint = f"…{key[-4:]}"
    audit.record(db, current, "rotate_key", "integration_source", source.id)
    db.commit()
    return KeyOut(source_code=source_code, inbound_key=key,
                  message="Copy this key now and store it in the source system's secret store; it is not shown again.")


@router.get("/sources/{source_code}/mappings", response_model=list[MappingOut])
def list_mappings(source_code: str, current: CurrentUser = Depends(require("integration.read")),
                  db: Session = Depends(get_db)) -> list:
    source = _source(db, source_code)
    return list(db.scalars(select(IntegrationFieldMapping).where(IntegrationFieldMapping.source_id == source.id)
                           .order_by(IntegrationFieldMapping.payload_kind, IntegrationFieldMapping.source_field)))


@router.post("/sources/{source_code}/mappings", response_model=MappingOut, status_code=201)
def add_mapping(source_code: str, body: MappingIn, current: CurrentUser = Depends(require("integration.manage")),
                db: Session = Depends(get_db)) -> IntegrationFieldMapping:
    source = _source(db, source_code)
    obj = crud.create(db, current, IntegrationFieldMapping, {**body.model_dump(), "source_id": source.id}, "integration_mapping")
    db.commit()
    return obj


@router.delete("/mappings/{mapping_id}", response_model=Message)
def delete_mapping(mapping_id: uuid.UUID, current: CurrentUser = Depends(require("integration.manage")),
                   db: Session = Depends(get_db)) -> Message:
    mapping = crud.get_or_404(db, IntegrationFieldMapping, mapping_id, "Mapping")
    audit.record(db, current, "delete", "integration_mapping", mapping.id, before=audit.snapshot(mapping))
    db.delete(mapping)
    db.commit()
    return Message(message="Mapping removed")


IDENTITY_MODELS = {"farm_plot": FarmPlot, "farmer": Farmer, "device": Device}


def _add_identity_map(db: Session, current: CurrentUser, source: IntegrationSource, body: IdentityMapIn) -> SourceRecordMap:
    crud.get_or_404(db, IDENTITY_MODELS[body.entity_type], body.internal_id, body.entity_type.replace("_", " ").title())
    existing = db.scalar(select(SourceRecordMap).where(SourceRecordMap.source_id == source.id,
                                                       SourceRecordMap.entity_type == body.entity_type,
                                                       SourceRecordMap.source_record_id == body.source_id))
    if existing:
        raise Conflict(f"Source {body.entity_type} '{body.source_id}' is already mapped")
    mapping = SourceRecordMap(source_id=source.id, entity_type=body.entity_type, source_record_id=body.source_id,
                              internal_record_id=body.internal_id, mapped_by_id=current.id, review_note=body.review_note)
    db.add(mapping)
    audit.record(db, current, "map_identity", "integration_source", source.id,
                 after={"entity_type": body.entity_type, "source_id": body.source_id, "internal_id": body.internal_id})
    return mapping


@router.get("/sources/{source_code}/identity-maps", response_model=list[IdentityMapOut])
def list_identity_maps(source_code: str, current: CurrentUser = Depends(require("integration.read")),
                       db: Session = Depends(get_db)) -> list:
    source = _source(db, source_code)
    return list(db.scalars(select(SourceRecordMap).where(SourceRecordMap.source_id == source.id,
                                                         SourceRecordMap.entity_type.in_(list(IDENTITY_MODELS)))
                           .order_by(SourceRecordMap.entity_type, SourceRecordMap.source_record_id)))


@router.post("/sources/{source_code}/identity-maps", response_model=IdentityMapOut, status_code=201)
def map_identity(source_code: str, body: IdentityMapIn, current: CurrentUser = Depends(require("integration.manage")),
                 db: Session = Depends(get_db)) -> SourceRecordMap:
    """Reviewed mapping of a source system's farmer/plot/device identifier to the platform record."""
    mapping = _add_identity_map(db, current, _source(db, source_code), body)
    db.commit()
    return mapping


@router.post("/sources/{source_code}/plot-maps", response_model=Message, status_code=201)
def map_plot(source_code: str, body: PlotMapIn, current: CurrentUser = Depends(require("integration.manage")),
             db: Session = Depends(get_db)) -> Message:
    """Shortcut for a plot identity map."""
    _add_identity_map(db, current, _source(db, source_code),
                      IdentityMapIn(entity_type="farm_plot", source_id=body.source_plot_id, internal_id=body.farm_plot_id))
    db.commit()
    return Message(message=f"Mapped source plot '{body.source_plot_id}'")


@router.post("/sources/{source_code}/import-farms", response_model=FarmImportOut)
def import_source_farms(source_code: str, body: FarmImportIn, current: CurrentUser = Depends(require("integration.manage")),
                        db: Session = Depends(get_db)) -> dict:
    """Create farmers, farms and plots (with identity maps) from the FarmFuture estates the token can see. Read-only on
    the FarmFuture side; imported farmers have no recorded consent and no enrolment."""
    if not current.has("farmer.manage"):
        raise AppError(403, "forbidden", "Importing farms also needs the farmer.manage permission")
    return import_farms(db, current, _source(db, source_code), body.geo_unit_id, body.primary_fpo_id, body.test_label)


@router.post("/sources/{source_code}/import-devices", response_model=DeviceImportOut)
def import_source_devices(source_code: str, current: CurrentUser = Depends(require("integration.manage")),
                          db: Session = Depends(get_db)) -> dict:
    """Register the SoilSync / MicroClime devices of the linked FarmFuture farms in the device registry, linked by
    identity maps. Read-only on the FarmFuture side."""
    if not current.has("module.write"):
        raise AppError(403, "forbidden", "Importing devices also needs the module.write permission")
    return import_devices(db, current, _source(db, source_code))


@router.post("/{source_code}/pull", response_model=BatchResultOut)
def pull(source_code: str, current: CurrentUser = Depends(require("integration.manage")), db: Session = Depends(get_db)):
    """Run a pull synchronisation through the source's adapter (pagination, retry with backoff). A pull that fails is
    recorded as a failed sync run with its reason."""
    run, outcomes = run_pull(db, _source(db, source_code), current)
    return BatchResultOut(sync_run=SyncRunOut.model_validate(run), outcomes=outcomes)


@router.get("/health")
def sources_health(current: CurrentUser = Depends(require("integration.read")), db: Session = Depends(get_db)) -> dict:
    """Freshness of every pull source (last run, last success, schedule, stale flag) and the FarmFuture token expiry –
    for the dashboard's data-sources panel. A scheduled source is stale when it has not succeeded for over 26 hours."""
    kinds = {c["adapter_code"]: c.get("kind") for c in catalogue()}
    now = datetime.now(UTC)
    sources = []
    for source in db.scalars(select(IntegrationSource).where(IntegrationSource.source_type == "rest_pull")
                             .order_by(IntegrationSource.module_code, IntegrationSource.source_code)):
        last = db.scalars(select(SyncRun).where(SyncRun.source_id == source.id, SyncRun.trigger.in_(("pull", "schedule")))
                          .order_by(SyncRun.started_at.desc())).first()
        ok = db.scalars(select(SyncRun).where(SyncRun.source_id == source.id, SyncRun.trigger.in_(("pull", "schedule")),
                                              SyncRun.status.in_(("succeeded", "partial")))
                        .order_by(SyncRun.started_at.desc())).first()
        schedule = (source.config or {}).get("schedule_daily_at")
        age = (now - _aware(ok.started_at)).total_seconds() / 3600 if ok else None
        if not source.enabled:
            state = "disabled"
        elif last is not None and last.status == "failed":
            state = "failed"
        elif ok is None:
            state = "never_run"
        elif schedule and age is not None and age > 26:
            state = "stale"
        else:
            state = "ok" if schedule else "manual"
        sources.append({"source_code": source.source_code, "name": source.name, "module_code": source.module_code,
                        "adapter_code": source.adapter_code, "kind": "sample" if source.is_mock else kinds.get(source.adapter_code),
                        "is_mock": source.is_mock, "enabled": source.enabled, "schedule_daily_at": schedule,
                        "last_run_at": last.started_at if last else None, "last_status": last.status if last else None,
                        "last_error": (last.error_summary or "")[:300] if last and last.status == "failed" else None,
                        "last_success_at": ok.started_at if ok else None,
                        "hours_since_success": round(age, 1) if age is not None else None, "state": state})
    return {"sources": sources, "farmfuture_token": farmfuture_token(),
            "summary": {s: sum(1 for x in sources if x["state"] == s)
                        for s in ("ok", "manual", "stale", "failed", "never_run", "disabled")}}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.post("/{source_code}/test-payload", response_model=BatchResultOut)
def test_payload(source_code: str, body: TestPayloadIn, current: CurrentUser = Depends(require("integration.manage")),
                 db: Session = Depends(get_db)):
    """Dry run: map and validate sample records without saving them."""
    source = _source(db, source_code)
    run, outcomes = process_batch(db, source, body.payload_kind, body.records, "test", current, dry_run=True)
    db.commit()
    return BatchResultOut(sync_run=SyncRunOut.model_validate(run), outcomes=outcomes)


@router.get("/{source_code}/sync-runs", response_model=Page[SyncRunOut])
def sync_runs(source_code: str, params: PageParams = Depends(), current: CurrentUser = Depends(require("integration.read")),
              db: Session = Depends(get_db)) -> dict:
    source = _source(db, source_code)
    stmt = select(SyncRun).where(SyncRun.source_id == source.id).order_by(SyncRun.started_at.desc())
    return page_of(*paginate(db, stmt, params, unique=False), params, SyncRunOut)


@router.get("/sync-runs/{run_id}/quarantine", response_model=list[QuarantineOut])
def run_quarantine(run_id: uuid.UUID, current: CurrentUser = Depends(require("integration.read")),
                   db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(QuarantinedRecord).where(QuarantinedRecord.sync_run_id == run_id)
                           .order_by(QuarantinedRecord.created_at)))


@router.get("/quarantine", response_model=Page[QuarantineOut])
def list_quarantine(params: PageParams = Depends(), status: str | None = "open", source_code: str | None = None,
                    current: CurrentUser = Depends(require("integration.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(QuarantinedRecord).order_by(QuarantinedRecord.created_at.desc())
    if status:
        stmt = stmt.where(QuarantinedRecord.status == status)
    if source_code:
        stmt = stmt.where(QuarantinedRecord.source_id == _source(db, source_code).id)
    return page_of(*paginate(db, stmt, params, unique=False), params, QuarantineOut)


@router.post("/{source_code}/replay", response_model=BatchResultOut)
def replay(source_code: str, body: ReplayIn, current: CurrentUser = Depends(require("integration.replay")),
           db: Session = Depends(get_db)):
    """Reprocess open quarantined records (e.g. after fixing a mapping or adding a plot map)."""
    source = _source(db, source_code)
    stmt = select(QuarantinedRecord).where(QuarantinedRecord.source_id == source.id, QuarantinedRecord.status == "open")
    if body.quarantine_ids:
        stmt = stmt.where(QuarantinedRecord.id.in_(body.quarantine_ids))
    elif body.sync_run_id:
        stmt = stmt.where(QuarantinedRecord.sync_run_id == body.sync_run_id)
    else:
        raise AppError(422, "selection_required", "Select quarantined records or a sync run to replay")
    items = list(db.scalars(stmt.order_by(QuarantinedRecord.created_at)))
    if not items:
        raise AppError(422, "nothing_to_replay", "No open quarantined records match the selection")
    kinds = {q.payload_kind for q in items}
    if len(kinds) > 1:
        raise AppError(422, "mixed_kinds", "Replay records of one payload kind at a time")
    original = db.get(SyncRun, items[0].sync_run_id)
    run, outcomes = process_batch(db, source, kinds.pop(), [q.payload for q in items], "replay", current,
                                  replay_of=original, quarantined=items)
    db.commit()
    return BatchResultOut(sync_run=SyncRunOut.model_validate(run), outcomes=outcomes)


@router.post("/quarantine/{quarantine_id}/discard", response_model=QuarantineOut)
def discard(quarantine_id: uuid.UUID, body: DiscardIn, current: CurrentUser = Depends(require("integration.replay")),
            db: Session = Depends(get_db)) -> QuarantinedRecord:
    item = crud.get_or_404(db, QuarantinedRecord, quarantine_id, "Quarantined record")
    if item.status != "open":
        raise Conflict(f"Record is already {item.status}")
    item.status, item.resolution_note, item.resolved_at, item.resolved_by_id = "discarded", body.note, utcnow(), current.id
    audit.record(db, current, "discard", "quarantined_record", item.id, summary=body.note)
    db.commit()
    return item
