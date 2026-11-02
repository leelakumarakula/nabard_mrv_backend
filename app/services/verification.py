"""Independent sample verification: frame → reproducible selection → field visits → conclusion.

Separate from internal review (review tasks). A confirmed visit marks practice adoption as *verified*; a discrepancy or
missing record marks it *not verified*. Nothing is silently overwritten – results and conclusions are audited.
"""

import random
import secrets
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, descendant_geo_ids
from app.core.errors import AppError, Forbidden, InvalidState
from app.db.mixins import utcnow
from app.models import (
    DemonstrationSite,
    FieldRecord,
    MonitoringRecord,
    PracticeRecord,
    VerificationSample,
    VerificationSampleItem,
)
from app.services import audit, configuration
from app.services.settings import get_setting

MODELS: dict[str, Any] = {
    "practice_record": PracticeRecord,
    "demonstration_site": DemonstrationSite,
    "field_record": FieldRecord,
    "monitoring_record": MonitoringRecord,
}


def frame_query(db: Session, sample: VerificationSample) -> Select:
    """Eligible records for the sample (deterministically ordered so seeded draws are reproducible)."""
    f = sample.frame_filter or {}
    start = datetime.combine(_date(f.get("period_start")), time.min, tzinfo=UTC) if f.get("period_start") else None
    end = datetime.combine(_date(f.get("period_end")), time.max, tzinfo=UTC) if f.get("period_end") else None
    geo_ids = descendant_geo_ids(db, {uuid.UUID(str(f["geo_unit_id"]))}) if f.get("geo_unit_id") else None
    if sample.entity_type == "practice_record":
        stmt = select(PracticeRecord).where(PracticeRecord.programme_id == sample.programme_id,
                                            PracticeRecord.status == f.get("practice_status", "observed"))
        if f.get("activity_codes"):
            stmt = stmt.where(PracticeRecord.activity_code.in_(f["activity_codes"]))
        if start:
            stmt = stmt.where(PracticeRecord.action_date >= start.date())
        if end:
            stmt = stmt.where(PracticeRecord.action_date <= end.date())
        geo_col, order = PracticeRecord.geo_unit_id, PracticeRecord.id
    elif sample.entity_type == "demonstration_site":
        stmt = select(DemonstrationSite).where(DemonstrationSite.programme_id == sample.programme_id,
                                               DemonstrationSite.status.in_(["established", "active"]))
        if f.get("category"):
            stmt = stmt.where(DemonstrationSite.category == f["category"])
        geo_col, order = DemonstrationSite.geo_unit_id, DemonstrationSite.id
    elif sample.entity_type == "field_record":
        stmt = select(FieldRecord).where(FieldRecord.programme_id == sample.programme_id,
                                         FieldRecord.validation_status == "valid")
        if f.get("record_type"):
            stmt = stmt.where(FieldRecord.record_type == f["record_type"])
        if start:
            stmt = stmt.where(FieldRecord.observed_at >= start)
        if end:
            stmt = stmt.where(FieldRecord.observed_at <= end)
        geo_col, order = FieldRecord.geo_unit_id, FieldRecord.id
    else:
        stmt = select(MonitoringRecord).where(MonitoringRecord.programme_id == sample.programme_id,
                                              MonitoringRecord.validation_status == "valid")
        if f.get("indicator_id"):
            stmt = stmt.where(MonitoringRecord.indicator_id == uuid.UUID(str(f["indicator_id"])))
        if start:
            stmt = stmt.where(MonitoringRecord.observed_at >= start)
        if end:
            stmt = stmt.where(MonitoringRecord.observed_at <= end)
        geo_col, order = MonitoringRecord.geo_unit_id, MonitoringRecord.id
    if geo_ids is not None:
        stmt = stmt.where(geo_col.in_(geo_ids or {uuid.UUID(int=0)}))
    return stmt.order_by(order)


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def label_for(entity: Any) -> str:
    if isinstance(entity, PracticeRecord):
        return f"{entity.activity_code} – {entity.farmer.label}" + (f" ({entity.action_date})" if entity.action_date else "")
    if isinstance(entity, DemonstrationSite):
        return entity.label
    if isinstance(entity, FieldRecord):
        who = entity.farmer.label if entity.farmer else entity.geo_unit.label
        return f"{entity.record_type} – {who}"
    return f"{entity.indicator.code} = {entity.value} {entity.unit} ({entity.observed_at:%Y-%m-%d})"


def refresh_frame_size(db: Session, sample: VerificationSample) -> int:
    sample.frame_size = len(db.execute(frame_query(db, sample).with_only_columns(MODELS[sample.entity_type].id)).all())
    return sample.frame_size


def draw(db: Session, current: CurrentUser, sample: VerificationSample, entity_ids: list[uuid.UUID] | None) -> list[VerificationSampleItem]:
    if sample.status != "planned":
        raise InvalidState("The sample has already been drawn")
    frame = list(db.scalars(frame_query(db, sample)).unique())
    sample.frame_size = len(frame)
    if not frame:
        raise InvalidState("The sample frame is empty – adjust the frame filter")
    by_id = {e.id: e for e in frame}
    if sample.selection_method == "census":
        chosen = frame
    elif sample.selection_method == "purposive":
        if not entity_ids:
            raise AppError(422, "selection_required", "Purposive selection needs the list of records to visit")
        missing = [i for i in entity_ids if i not in by_id]
        if missing:
            raise AppError(422, "outside_frame", f"{len(missing)} selected record(s) are not in the sample frame")
        chosen = [by_id[i] for i in entity_ids]
    else:
        if sample.random_seed is None:
            sample.random_seed = secrets.randbelow(2_147_483_647)
        chosen = random.Random(sample.random_seed).sample(frame, min(sample.sample_size, len(frame)))
    sample.sample_size = len(chosen)
    items = [VerificationSampleItem(sample_id=sample.id, entity_type=sample.entity_type, entity_id=e.id,
                                    entity_label=label_for(e)[:255], created_by_id=current.id, updated_by_id=current.id)
             for e in chosen]
    db.add_all(items)
    sample.status = "drawn"
    db.flush()
    audit.record(db, current, "draw_sample", "verification_sample", sample.id,
                 summary=f"{sample.selection_method}: {len(items)} of {len(frame)} (seed {sample.random_seed})")
    return items


def record_result(db: Session, current: CurrentUser, item: VerificationSampleItem, sample: VerificationSample,
                  data: dict[str, Any]) -> VerificationSampleItem:
    if sample.status not in ("drawn", "in_progress"):
        raise InvalidState(f"Results cannot be recorded on a sample that is {sample.status}")
    if sample.verifier_id and sample.verifier_id != current.id and not current.has("admin.users"):
        raise Forbidden("This sample is assigned to another verifier")
    entity = db.get(MODELS[item.entity_type], item.entity_id)
    if get_setting(db, "mrv.independent_verification_required") and entity is not None:
        involved = {getattr(entity, "created_by_id", None), getattr(entity, "observed_by_id", None)}
        if current.id in involved:
            raise Forbidden("Independent verification must be done by someone not involved in the original record")
    data = dict(data)
    if data.get("checklist_results") is not None:
        data["checklist_results"] = configuration.clean_assessment(db, "verification_visit", data["checklist_results"], "ok")
    if data.get("result") == "confirmed":
        missing = configuration.missing_required(db, "verification_visit", data.get("checklist_results"), "ok")
        if missing:
            raise AppError(422, "checklist_incomplete", f"Required checks not confirmed: {', '.join(missing)}")
    before = {"result": item.result}
    for key, value in data.items():
        setattr(item, key, value)
    item.verified_by_id, item.verified_at = current.id, utcnow()
    sample.status = "in_progress"
    if isinstance(entity, PracticeRecord):
        status = {"confirmed": "verified", "discrepancy": "not_verified", "not_found": "not_verified"}.get(item.result)
        if status:
            audit.record(db, current, "verification_result", "practice_record", entity.id,
                         before={"verification_status": entity.verification_status}, after={"verification_status": status})
            entity.verification_status = status
    audit.record(db, current, "record_result", "verification_sample_item", item.id, before=before,
                 after={"result": item.result, "discrepancy_note": item.discrepancy_note})
    return item


def complete(db: Session, current: CurrentUser, sample: VerificationSample, conclusion: str, note: str) -> VerificationSample:
    if sample.status not in ("drawn", "in_progress"):
        raise InvalidState(f"A sample that is {sample.status} cannot be completed")
    db.flush()
    pending = db.scalars(select(VerificationSampleItem.id).where(VerificationSampleItem.sample_id == sample.id,
                                                                 VerificationSampleItem.result == "pending")).first()
    if pending:
        raise InvalidState("Record a result (or 'not visited') for every sampled record first")
    sample.status, sample.conclusion, sample.conclusion_note, sample.completed_at = "completed", conclusion, note, utcnow()
    audit.record(db, current, "complete_sample", "verification_sample", sample.id, summary=f"{conclusion}: {note}")
    return sample
