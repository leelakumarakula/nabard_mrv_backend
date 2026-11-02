"""Inbound processing: map → validate → resolve references → idempotent upsert → quarantine on failure."""

import hashlib
import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.core.deps import CurrentUser
from app.db.mixins import utcnow
from app.models import (
    Advisory,
    AdvisoryDelivery,
    Device,
    Farm,
    Farmer,
    FarmPlot,
    FieldRecord,
    GeoUnit,
    IndicatorDefinition,
    IntegrationFieldMapping,
    IntegrationSource,
    MonitoringRecord,
    Programme,
    QuarantinedRecord,
    SourceRecordMap,
    SyncRun,
)
from app.services import audit, record_types
from app.services.integration.canonical import (
    PAYLOAD_SCHEMAS,
    CanonicalAdvisory,
    CanonicalMeasurement,
    CanonicalObservation,
)
from app.services.settings import get_setting
from app.services.units import UnitConversionError, convert, normalize_unit
from app.services.validation import validate_field_record, validate_monitoring


class RecordError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def apply_mappings(raw: dict[str, Any], mappings: list[IntegrationFieldMapping],
                   defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rename source fields to canonical names and translate coded values. Unmapped fields pass through.

    A dotted target (``attributes.value``) writes into a nested object; ``defaults`` (source config ``payload_defaults``)
    supply constant canonical fields such as programme_code or record_type that a source does not send.
    """
    by_source = {m.source_field: m for m in mappings}
    result: dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (defaults or {}).items()}
    for key, value in raw.items():
        mapping = by_source.get(key)
        if mapping is None:
            result[key] = value
            continue
        if mapping.value_map and isinstance(value, str) and value in mapping.value_map:
            value = mapping.value_map[value]
        target = mapping.target_field
        if "." in target:
            parent, child = target.split(".", 1)
            result.setdefault(parent, {})[child] = value
        else:
            result[target] = value
    return result


def _parse(kind: str, mapped: dict[str, Any]):
    try:
        return PAYLOAD_SCHEMAS[kind].model_validate(mapped)
    except ValidationError as exc:
        raise RecordError([f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}" for e in exc.errors()]) from exc


class Processor:
    def __init__(self, db: Session, source: IntegrationSource, actor: CurrentUser | None) -> None:
        self.db = db
        self.source = source
        self.actor = actor
        self._programmes: dict[str, Programme | None] = {}
        self.exact_code_matching = bool(get_setting(db, "integration.allow_exact_code_matching"))
        self.received_at = utcnow()

    # -- reference resolution -------------------------------------------------------------------------------------
    def programme(self, code: str) -> Programme:
        if code not in self._programmes:
            self._programmes[code] = self.db.scalar(select(Programme).where(Programme.code == code))
        programme = self._programmes[code]
        if programme is None:
            raise RecordError([f"Unknown programme code '{code}'"])
        return programme

    def identity(self, entity_type: str, model: type, code_column: Any, external_id: str, label: str) -> Any:
        """Resolve an external identifier through a reviewed identity map; fall back to the exact platform code only
        when the setting allows it. Never guesses between candidates."""
        mapped = self.db.scalar(select(SourceRecordMap.internal_record_id).where(
            SourceRecordMap.source_id == self.source.id, SourceRecordMap.entity_type == entity_type,
            SourceRecordMap.source_record_id == external_id))
        if mapped:
            return self.db.get(model, mapped)
        if self.exact_code_matching:
            found = self.db.scalars(select(model).where(code_column == external_id).limit(2)).unique().all()
            if len(found) == 1:
                return found[0]
        raise RecordError([f"Unknown {label} reference '{external_id}' – add a reviewed {label} identity map for this "
                           "source or correct the source record"])

    def plot(self, external_id: str) -> FarmPlot:
        return self.identity("farm_plot", FarmPlot, FarmPlot.plot_code, external_id, "plot")

    def geo(self, code: str) -> GeoUnit:
        geo = self.db.scalar(select(GeoUnit).where(GeoUnit.code == code))
        if geo is None:
            raise RecordError([f"Unknown geography code '{code}'"])
        return geo

    def indicator(self, code: str) -> IndicatorDefinition:
        candidates = list(self.db.scalars(select(IndicatorDefinition).where(
            IndicatorDefinition.code == code, IndicatorDefinition.status != "retired")).unique())
        if not candidates:
            raise RecordError([f"Unknown or retired indicator code '{code}'"])
        candidates.sort(key=lambda i: (i.status == "approved", i.version), reverse=True)
        return candidates[0]

    def farmer_for_plot(self, plot: FarmPlot) -> Farmer:
        return self.db.get(Farmer, self.db.scalar(select(Farm.farmer_id).where(Farm.id == plot.farm_id)))

    def existing(self, entity_type: str, source_record_id: str) -> SourceRecordMap | None:
        return self.db.scalar(select(SourceRecordMap).where(
            SourceRecordMap.source_id == self.source.id, SourceRecordMap.entity_type == entity_type,
            SourceRecordMap.source_record_id == source_record_id))

    def remember(self, entity_type: str, source_record_id: str, internal_id: uuid.UUID, digest: str) -> None:
        self.db.add(SourceRecordMap(source_id=self.source.id, entity_type=entity_type, source_record_id=source_record_id,
                                    internal_record_id=internal_id, checksum=digest))

    def _idempotency(self, entity_type: str, source_record_id: str, digest: str, model: type) -> tuple[str, Any]:
        """Returns ('new', None) | ('duplicate', record) | ('changed', record)."""
        link = self.existing(entity_type, source_record_id)
        if link is None:
            return "new", None
        link.last_seen_at = utcnow()
        record = self.db.get(model, link.internal_record_id)
        if link.checksum == digest:
            return "duplicate", record
        if getattr(record, "verification_status", "unverified") != "unverified" or \
                getattr(record, "review_status", "not_submitted") not in ("not_submitted", "correction_requested"):
            raise RecordError([f"Source record '{source_record_id}' changed after it entered review/verification – "
                               "needs manual reconciliation"])
        link.checksum = digest
        return "changed", record

    # -- handlers -------------------------------------------------------------------------------------------------
    def measurement(self, m: CanonicalMeasurement, digest: str) -> str:
        programme = self.programme(m.programme_code)
        indicator = self.indicator(m.indicator_code)
        if not (m.farm_plot_external_id or m.geo_code):
            raise RecordError(["Either farm_plot_external_id or geo_code is required"])
        plot = self.plot(m.farm_plot_external_id) if m.farm_plot_external_id else None
        farmer = self.farmer_for_plot(plot) if plot else None
        geo_id = farmer.geo_unit_id if farmer else self.geo(m.geo_code).id
        try:
            value = convert(m.value, m.unit, indicator.unit)
        except UnitConversionError as exc:
            raise RecordError([str(exc)]) from exc
        state, rec = self._idempotency("monitoring_record", m.source_record_id, digest, MonitoringRecord)
        if state == "duplicate":
            return "duplicate"
        rec = rec or MonitoringRecord(programme_id=programme.id, source_system_id=self.source.id,
                                      source_record_id=m.source_record_id, submission_status="imported")
        rec.indicator_id = indicator.id
        rec.activity_code = m.activity_code
        rec.farmer_id = farmer.id if farmer else None
        rec.farm_plot_id = plot.id if plot else None
        rec.geo_unit_id = geo_id
        rec.organisation_id = farmer.primary_fpo_id if farmer else None
        rec.value = value
        rec.unit = indicator.unit
        rec.original_value = m.value
        rec.original_unit = normalize_unit(m.unit)
        rec.observed_at = m.observed_at
        rec.source_timestamp = m.observed_at
        rec.received_at = self.received_at
        rec.method = m.method
        rec.data_nature = "observed"
        rec.source_evidence_refs = [e.model_dump() for e in m.evidence] or None
        rec.is_demo = self.source.is_mock
        self.db.add(rec)
        self.db.flush()
        outcome = validate_monitoring(self.db, rec)
        rec.validation_status, rec.validation_messages = outcome.status, outcome.messages
        if state == "new":
            self.remember("monitoring_record", m.source_record_id, rec.id, digest)
        audit.record(self.db, self.actor, "import" if state == "new" else "import_update", "monitoring_record", rec.id,
                     summary=f"{self.source.source_code}:{m.source_record_id} → {outcome.status}")
        return "accepted"

    def observation(self, o: CanonicalObservation, digest: str) -> str:
        programme = self.programme(o.programme_code)
        rt = record_types.RECORD_TYPES_BY_CODE.get(o.record_type)
        if rt is None or rt.module_code != o.module_code:
            raise RecordError([f"Unknown record type '{o.record_type}' for module '{o.module_code}'"])
        attributes, errors = record_types.validate_attributes(rt, o.attributes)
        if errors:
            raise RecordError(errors)
        plot = self.plot(o.farm_plot_external_id) if o.farm_plot_external_id else None
        farmer = self.farmer_for_plot(plot) if plot else None
        device = None
        if o.device_code:
            device = self.identity("device", Device, Device.device_code, o.device_code, "device")
        if farmer:
            geo_id = farmer.geo_unit_id
        elif device:
            geo_id = device.geo_unit_id
        elif o.geo_code:
            geo_id = self.geo(o.geo_code).id
        else:
            raise RecordError(["A plot, device or geo_code is required to locate the observation"])
        state, rec = self._idempotency("field_record", o.source_record_id, digest, FieldRecord)
        if state == "duplicate":
            return "duplicate"
        value, unit = record_types.value_and_unit(rt, attributes)
        rec = rec or FieldRecord(programme_id=programme.id, source_system_id=self.source.id,
                                 source_record_id=o.source_record_id, submission_status="imported")
        rec.module_code, rec.record_type, rec.data_nature = rt.module_code, rt.code, rt.data_nature
        rec.activity_code = rt.default_activity_code
        rec.farmer_id = farmer.id if farmer else None
        rec.farm_plot_id = plot.id if plot else (device.farm_plot_id if device else None)
        rec.device_id = device.id if device else None
        rec.geo_unit_id = geo_id
        rec.organisation_id = farmer.primary_fpo_id if farmer else (device.organisation_id if device else None)
        rec.observed_at = o.observed_at
        rec.source_timestamp = o.observed_at
        rec.received_at = self.received_at
        rec.attributes = attributes
        rec.value, rec.unit = value, unit
        rec.is_demo = self.source.is_mock
        self.db.add(rec)
        self.db.flush()
        outcome = validate_field_record(self.db, rec)
        rec.validation_status, rec.validation_messages = outcome.status, outcome.messages
        if state == "new":
            self.remember("field_record", o.source_record_id, rec.id, digest)
        audit.record(self.db, self.actor, "import", "field_record", rec.id,
                     summary=f"{self.source.source_code}:{o.source_record_id} → {outcome.status}")
        return "accepted"

    def advisory(self, a: CanonicalAdvisory, digest: str) -> str:
        programme = self.programme(a.programme_code)
        geo = self.geo(a.target_geo_code)
        farmers: dict[str, Farmer] = {}
        for d in a.deliveries:
            try:
                farmer = self.identity("farmer", Farmer, Farmer.farmer_code, d.farmer_code, "farmer")
            except RecordError as exc:
                raise RecordError([f"{exc.errors[0]} (delivery '{d.source_delivery_id}')"]) from exc
            farmers[d.source_delivery_id] = farmer
        state, adv = self._idempotency("advisory", a.source_record_id, digest, Advisory)
        if state == "duplicate":
            return "duplicate"
        adv = adv or Advisory(programme_id=programme.id, source_system_id=self.source.id, source_record_id=a.source_record_id)
        for key in ("origin_module", "advisory_type", "hazard_type", "risk_level", "title", "message", "recommendation",
                    "issued_at", "valid_until"):
            setattr(adv, key, getattr(a, key))
        adv.target_geo_unit_id = geo.id
        adv.status = "issued"
        adv.is_demo = self.source.is_mock
        self.db.add(adv)
        self.db.flush()
        for d in a.deliveries:
            link = self.existing("advisory_delivery", d.source_delivery_id)
            delivery = self.db.get(AdvisoryDelivery, link.internal_record_id) if link else AdvisoryDelivery(
                advisory_id=adv.id, source_system_id=self.source.id, source_record_id=d.source_delivery_id)
            farmer = farmers[d.source_delivery_id]
            delivery.farmer_id, delivery.organisation_id, delivery.geo_unit_id = farmer.id, farmer.primary_fpo_id, farmer.geo_unit_id
            for key in ("channel", "sent_at", "delivered_at", "read_at", "response", "response_at", "understood"):
                setattr(delivery, key, getattr(d, key))
            delivery.is_demo = self.source.is_mock
            self.db.add(delivery)
            self.db.flush()
            if link is None:
                self.remember("advisory_delivery", d.source_delivery_id, delivery.id, digest)
        if state == "new":
            self.remember("advisory", a.source_record_id, adv.id, digest)
        audit.record(self.db, self.actor, "import", "advisory", adv.id,
                     summary=f"{self.source.source_code}:{a.source_record_id} with {len(a.deliveries)} deliveries")
        return "accepted"

    def handle(self, kind: str, raw: dict[str, Any], mappings: list[IntegrationFieldMapping]) -> str:
        mapped = apply_mappings(raw, mappings, (self.source.config or {}).get("payload_defaults"))
        parsed = _parse(kind, mapped)
        if parsed.source_system and parsed.source_system != self.source.source_code:
            raise RecordError([f"source_system '{parsed.source_system}' does not match the receiving source "
                               f"'{self.source.source_code}'"])
        digest = checksum(mapped)
        return {"measurements": self.measurement, "observations": self.observation, "advisories": self.advisory}[kind](parsed, digest)


def process_batch(db: Session, source: IntegrationSource, kind: str, records: list[dict[str, Any]], trigger: str,
                  actor: CurrentUser | None, replay_of: SyncRun | None = None,
                  quarantined: list[QuarantinedRecord] | None = None, dry_run: bool = False) -> tuple[SyncRun, list[dict]]:
    """Process records one by one, each in its own savepoint so one bad record never blocks the batch."""
    run = SyncRun(source_id=source.id, payload_kind=kind, trigger=trigger, status="running",
                  correlation_id=request_id_var.get(), replay_of_id=replay_of.id if replay_of else None,
                  triggered_by_id=actor.id if actor else None)
    db.add(run)
    db.flush()
    mappings = list(db.scalars(select(IntegrationFieldMapping).where(
        IntegrationFieldMapping.source_id == source.id, IntegrationFieldMapping.payload_kind == kind)))
    processor = Processor(db, source, actor)
    outcomes: list[dict] = []
    for index, raw in enumerate(records):
        run.records_read += 1
        source_record_id = str(raw.get("source_record_id") or apply_mappings(raw, mappings).get("source_record_id") or "")
        savepoint = db.begin_nested()
        try:
            result = processor.handle(kind, raw, mappings)
            if dry_run:
                savepoint.rollback()
            else:
                savepoint.commit()
            if result == "duplicate":
                run.records_duplicate += 1
            else:
                run.records_accepted += 1
            outcomes.append({"index": index, "source_record_id": source_record_id, "result": result})
            if quarantined:
                quarantined[index].status = "replayed"
                quarantined[index].resolved_at = utcnow()
                quarantined[index].resolved_by_id = actor.id if actor else None
        except RecordError as exc:
            savepoint.rollback()
            run.records_rejected += 1
            outcomes.append({"index": index, "source_record_id": source_record_id, "result": "rejected", "errors": exc.errors})
            if dry_run:
                continue
            if quarantined:
                quarantined[index].errors = exc.errors
            else:
                db.add(QuarantinedRecord(sync_run_id=run.id, source_id=source.id, payload_kind=kind,
                                         source_record_id=source_record_id or None, payload=raw, errors=exc.errors))
    run.finished_at = utcnow()
    if run.records_rejected == 0:
        run.status = "succeeded"
    elif run.records_accepted + run.records_duplicate == 0:
        run.status = "failed"
    else:
        run.status = "partial"
    if run.records_rejected:
        run.error_summary = f"{run.records_rejected} of {run.records_read} record(s) quarantined"
    if dry_run:
        run.trigger = "test"
    audit.record(db, actor, "sync_run", "integration_source", source.id,
                 summary=f"{trigger} {kind}: read {run.records_read}, accepted {run.records_accepted}, "
                         f"duplicate {run.records_duplicate}, rejected {run.records_rejected}")
    return run, outcomes
