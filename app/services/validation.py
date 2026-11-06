"""Data-quality checks: completeness, units, identity, time, location, duplicates and plausibility.

Outcome per record:
  rejected  – hard error; the record cannot be used
  exception – needs a human decision (e.g. possible duplicate, outside plausible range)
  valid     – passed all checks
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BaselineRecord, Device, Farm, FarmPlot, FieldRecord, IndicatorDefinition, MonitoringRecord, Programme
from app.services import record_types
from app.services.units import normalize_unit

FUTURE_TOLERANCE = timedelta(hours=12)


class Outcome:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def status(self) -> str:
        if self.errors:
            return "rejected"
        if self.warnings:
            return "exception"
        return "valid"

    @property
    def messages(self) -> list[str]:
        return [f"ERROR: {e}" for e in self.errors] + [f"WARNING: {w}" for w in self.warnings]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _check_plot_farmer(db: Session, out: Outcome, farmer_id, farm_plot_id) -> None:
    if farm_plot_id is None:
        return
    owner = db.scalar(select(Farm.farmer_id).join(FarmPlot, FarmPlot.farm_id == Farm.id).where(FarmPlot.id == farm_plot_id))
    if owner is None:
        out.errors.append("Plot does not exist")
    elif farmer_id is not None and owner != farmer_id:
        out.errors.append("Plot does not belong to the selected farmer")


def _check_time(db: Session, out: Outcome, programme_id, observed_at: datetime | None) -> None:
    if observed_at is None:
        return
    if _aware(observed_at) > datetime.now(UTC) + FUTURE_TOLERANCE:
        out.errors.append("Observation time is in the future")
    programme = db.get(Programme, programme_id)
    if programme and programme.start_date and _aware(observed_at).date() < programme.start_date - timedelta(days=366):
        out.warnings.append("Observation is more than a year before the programme start date")


def _check_range(out: Outcome, indicator: IndicatorDefinition, value: Decimal) -> None:
    rules = indicator.validation_rules or {}
    if rules.get("min") is not None and value < Decimal(str(rules["min"])):
        out.warnings.append(f"Value {value} is below the plausible minimum {rules['min']} for {indicator.code}")
    if rules.get("max") is not None and value > Decimal(str(rules["max"])):
        out.warnings.append(f"Value {value} is above the plausible maximum {rules['max']} for {indicator.code}")


def validate_monitoring(db: Session, rec: MonitoringRecord) -> Outcome:
    out = Outcome()
    indicator = db.get(IndicatorDefinition, rec.indicator_id)
    if indicator is None:
        out.errors.append("Indicator does not exist")
        return out
    if indicator.status == "retired":
        out.errors.append(f"Indicator {indicator.code} v{indicator.version} is retired")
    if rec.value is None:
        out.errors.append("Value is required")
    if not rec.observed_at:
        out.errors.append("Observation time is required")
    if normalize_unit(rec.unit) != normalize_unit(indicator.unit):
        out.errors.append(f"Unit '{rec.unit}' does not match indicator unit '{indicator.unit}'")
    _check_plot_farmer(db, out, rec.farmer_id, rec.farm_plot_id)
    _check_time(db, out, rec.programme_id, rec.observed_at)
    if rec.value is not None:
        _check_range(out, indicator, Decimal(rec.value))
    if rec.value is not None and rec.observed_at:
        dup = select(MonitoringRecord.id).where(
            MonitoringRecord.indicator_id == rec.indicator_id,
            MonitoringRecord.observed_at == rec.observed_at,
            MonitoringRecord.value == rec.value,
            MonitoringRecord.validation_status != "rejected",
        )
        if rec.id is not None:
            dup = dup.where(MonitoringRecord.id != rec.id)
        dup = dup.where(MonitoringRecord.farm_plot_id == rec.farm_plot_id) if rec.farm_plot_id else \
            dup.where(MonitoringRecord.farm_plot_id.is_(None), MonitoringRecord.geo_unit_id == rec.geo_unit_id)
        if db.scalar(dup.limit(1)):
            out.warnings.append("Possible duplicate: same indicator, location, time and value already recorded")
    return out


def validate_baseline(db: Session, rec: BaselineRecord) -> Outcome:
    out = Outcome()
    indicator = db.get(IndicatorDefinition, rec.indicator_id)
    if indicator is None:
        out.errors.append("Indicator does not exist")
        return out
    if normalize_unit(rec.unit) != normalize_unit(indicator.unit):
        out.errors.append(f"Unit '{rec.unit}' does not match indicator unit '{indicator.unit}'")
    if not rec.method:
        out.errors.append("Baseline method is required")
    if not rec.source:
        out.errors.append("Baseline data source is required")
    if not (rec.period_label or rec.period_start):
        out.errors.append("Baseline period is required")
    if rec.period_start and rec.period_end and rec.period_end < rec.period_start:
        out.errors.append("Baseline period end is before its start")
    scope_fields = {"state": rec.geo_unit_id, "district": rec.geo_unit_id, "cluster": rec.geo_unit_id,
                    "fpo": rec.organisation_id, "farmer": rec.farmer_id, "plot": rec.farm_plot_id}
    if rec.scope_type in scope_fields and scope_fields[rec.scope_type] is None:
        out.errors.append(f"A {rec.scope_type}-level baseline needs its {rec.scope_type} reference")
    _check_plot_farmer(db, out, rec.farmer_id, rec.farm_plot_id)
    if rec.value is not None:
        _check_range(out, indicator, Decimal(rec.value))
    return out


def _check_sensor_plausibility(out: Outcome, attributes: dict) -> None:
    """Readings outside the physically plausible range, or flagged by the device, need an analyst's decision."""
    parameter, unit = attributes.get("parameter"), attributes.get("unit")
    bounds = record_types.SENSOR_PLAUSIBLE_RANGES.get((parameter, unit))
    if bounds is not None:
        low, high = bounds
        for key in ("value", "value_min", "value_max"):
            value = attributes.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not low <= value <= high:
                out.warnings.append(f"{parameter} {key.replace('_', ' ')} {value} {unit} is outside the plausible range "
                                    f"{low}–{high} {unit} – possible sensor fault; review before use")
                break
    if attributes.get("quality_flag") in ("suspect", "out_of_range"):
        out.warnings.append(f"The device marked this reading as {attributes['quality_flag'].replace('_', ' ')}")


def validate_field_record(db: Session, rec: FieldRecord) -> Outcome:
    out = Outcome()
    rt = record_types.RECORD_TYPES_BY_CODE.get(rec.record_type)
    if rt is None:
        out.errors.append(f"Unknown record type '{rec.record_type}'")
        return out
    _, errors = record_types.validate_attributes(rt, rec.attributes or {})
    out.errors.extend(errors)
    if rt.requires_plot and rec.farm_plot_id is None:
        out.errors.append("A plot is required for this record type")
    if rt.requires_device and rec.device_id is None:
        out.errors.append("A registered device is required for this record type")
    if rt.requires_organisation and rec.organisation_id is None:
        out.errors.append("An FPO/organisation is required for this record type")
    if rt.requires_demonstration_site and rec.demonstration_site_id is None:
        out.errors.append("A demonstration site is required for this record type")
    if rec.surveillance_cluster_id is not None and not rt.supports_surveillance_cluster:
        out.errors.append("Only pest inspections can be linked to a surveillance cluster")
    if rt.code == "sensor_observation" and rec.device_id is not None:
        device = db.get(Device, rec.device_id)
        supported = record_types.DEVICE_PARAMETERS.get(device.device_type) if device else None
        parameter = (rec.attributes or {}).get("parameter")
        if supported is not None and parameter and parameter not in supported:
            out.errors.append(f"Device type '{device.device_type}' does not report '{parameter}'")
        if device is not None and device.status in ("faulty", "removed"):
            out.warnings.append(f"Device {device.device_code} is {device.status}; reading needs review")
    if rt.code == "sensor_observation":
        _check_sensor_plausibility(out, rec.attributes or {})
    if rt.data_nature != "model_prediction" and rec.observed_at is None:
        out.errors.append("Observation/activity date-time is required")
    _check_plot_farmer(db, out, rec.farmer_id, rec.farm_plot_id)
    if rt.data_nature != "model_prediction":
        _check_time(db, out, rec.programme_id, rec.observed_at)
    if rec.source_record_id and rec.source_system_id:
        dup = db.scalar(select(FieldRecord.id).where(
            FieldRecord.source_system_id == rec.source_system_id, FieldRecord.source_record_id == rec.source_record_id,
            FieldRecord.id != rec.id).limit(1))
        if dup:
            out.warnings.append("Another record from the same source has this source record ID")
    return out
