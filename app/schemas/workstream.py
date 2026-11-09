import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, Ref


class FieldRecordIn(BaseModel):
    programme_id: uuid.UUID
    record_type: str
    title: str | None = Field(default=None, max_length=255)
    activity_code: str | None = None
    farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    demonstration_site_id: uuid.UUID | None = None
    surveillance_cluster_id: uuid.UUID | None = None
    observed_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    attributes: dict[str, Any] = {}
    notes: str | None = None


class FieldRecordUpdate(BaseModel):
    title: str | None = None
    activity_code: str | None = None
    farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    demonstration_site_id: uuid.UUID | None = None
    surveillance_cluster_id: uuid.UUID | None = None
    observed_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None


class FieldRecordOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    module_code: str
    record_type: str
    activity_code: str | None
    title: str | None
    farmer_id: uuid.UUID | None
    farmer: Ref | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    device_id: uuid.UUID | None
    device: Ref | None
    demonstration_site_id: uuid.UUID | None
    surveillance_cluster_id: uuid.UUID | None
    data_nature: str
    observed_at: datetime | None
    period_start: date | None
    period_end: date | None
    value: Decimal | None
    unit: str | None
    attributes: dict[str, Any]
    notes: str | None
    submission_status: str
    validation_status: str
    validation_messages: list[str] | None
    source_system_id: uuid.UUID | None
    source_record_id: str | None
    source_timestamp: datetime | None
    received_at: datetime | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    is_demo: bool


class ParticipantIn(BaseModel):
    farmer_id: uuid.UUID
    role: Literal["attendee", "host", "facilitator"] = "attendee"
    attended: bool = True


class ParticipantOut(ORMModel):
    id: uuid.UUID
    farmer_id: uuid.UUID
    farmer: Ref | None
    role: str
    attended: bool


class DeviceIn(BaseModel):
    device_code: str = Field(min_length=2, max_length=60)
    device_type: Literal["soilsync_7in1", "moisture_probe", "microclime", "rainsync", "analogue_weather", "other"]
    provider: str | None = None
    farm_plot_id: uuid.UUID | None = None
    geo_unit_id: uuid.UUID | None = None  # derived from the plot when given
    organisation_id: uuid.UUID | None = None
    installed_on: date | None = None
    status: Literal["planned", "active", "faulty", "removed"] = "planned"
    deployment_justification: str | None = None
    notes: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)  # installed position (both or neither)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class DeviceUpdate(BaseModel):
    provider: str | None = None
    farm_plot_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    installed_on: date | None = None
    removed_on: date | None = None
    status: Literal["planned", "active", "faulty", "removed"] | None = None
    deployment_justification: str | None = None
    notes: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class DeviceOut(ORMModel):
    id: uuid.UUID
    device_code: str
    device_type: str
    provider: str | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    installed_on: date | None
    removed_on: date | None
    status: str
    deployment_justification: str | None
    notes: str | None
    is_demo: bool
    last_calibrated_on: date | None = None
    calibration_due_on: date | None = None
    # Data completeness, from the readings stored in the platform (last 30 days) and the latest health snapshot
    last_reading_at: datetime | None = None
    reporting_days_30: int = 0
    expected_days_30: int = 0
    completeness_pct: float | None = None
    battery_pct: float | None = None
    signal_csq: int | None = None
    reporting_state: str | None = None  # reporting | late | silent | faulty | planned | removed
    # Installed position: coordinates only with farmer.read_pii; the check against the plot for everyone
    latitude: float | None = None
    longitude: float | None = None
    location_source: str | None = None
    location_updated_at: datetime | None = None
    location_check: str | None = None  # ok | far_from_plot | plot_not_located | no_position
    distance_from_plot_km: float | None = None


class CalibrationIn(BaseModel):
    calibrated_on: date
    method: str | None = None
    result: Literal["pass", "fail", "adjusted"]
    next_due_on: date | None = None
    performed_by: str | None = None
    notes: str | None = None


class CalibrationOut(ORMModel):
    id: uuid.UUID
    device_id: uuid.UUID
    calibrated_on: date
    method: str | None
    result: str
    next_due_on: date | None
    performed_by: str | None
    notes: str | None


class AdvisoryIn(BaseModel):
    programme_id: uuid.UUID
    origin_module: str
    advisory_type: Literal["alert", "advisory"]
    hazard_type: str | None = None
    risk_level: Literal["low", "moderate", "high", "very_high"] | None = None
    risk_record_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=255)
    message: str = Field(min_length=3)
    recommendation: str | None = None
    recommended_activity_code: str | None = None
    valid_until: datetime | None = None
    target_geo_unit_id: uuid.UUID
    target_organisation_id: uuid.UUID | None = None


class AdvisoryUpdate(BaseModel):
    hazard_type: str | None = None
    risk_level: Literal["low", "moderate", "high", "very_high"] | None = None
    title: str | None = None
    message: str | None = None
    recommendation: str | None = None
    recommended_activity_code: str | None = None
    valid_until: datetime | None = None
    target_organisation_id: uuid.UUID | None = None


class AdvisoryOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    origin_module: str
    advisory_type: str
    hazard_type: str | None
    risk_level: str | None
    risk_record_id: uuid.UUID | None
    title: str
    message: str
    recommendation: str | None
    recommended_activity_code: str | None
    issued_at: datetime | None
    valid_until: datetime | None
    target_geo_unit_id: uuid.UUID
    target_geo_unit: Ref | None
    target_organisation_id: uuid.UUID | None
    target_organisation: Ref | None
    status: str
    source_system_id: uuid.UUID | None
    source_record_id: str | None
    is_demo: bool
    created_at: datetime


class AdvisoryDetail(AdvisoryOut):
    deliveries_total: int = 0
    deliveries_delivered: int = 0
    deliveries_responded: int = 0


class DeliveryIn(BaseModel):
    farmer_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    channel: Literal["sms", "whatsapp", "ivr", "app", "fpo_meeting", "field_visit"]
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None


class BulkDeliveryIn(BaseModel):
    channel: Literal["sms", "whatsapp", "ivr", "app", "fpo_meeting", "field_visit"]
    farmer_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    sent_at: datetime | None = None
    delivered_at: datetime | None = None


class DeliveryUpdate(BaseModel):
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    response: str | None = None
    response_at: datetime | None = None
    understood: bool | None = None


class DeliveryOut(ORMModel):
    id: uuid.UUID
    advisory_id: uuid.UUID
    farmer_id: uuid.UUID | None
    farmer: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    channel: str
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    response: str | None
    response_at: datetime | None
    understood: bool | None
    is_demo: bool


class PracticeIn(BaseModel):
    programme_id: uuid.UUID
    farmer_id: uuid.UUID
    farm_plot_id: uuid.UUID | None = None
    module_code: str
    activity_code: str
    advisory_id: uuid.UUID | None = None
    demonstration_site_id: uuid.UUID | None = None
    recommended_action: str | None = None
    reported_action: str | None = None
    action_date: date | None = None
    status: Literal["recommended", "reported", "not_adopted"] = "recommended"


class PracticeUpdate(BaseModel):
    farm_plot_id: uuid.UUID | None = None
    recommended_action: str | None = None
    reported_action: str | None = None
    action_date: date | None = None
    status: Literal["recommended", "reported", "not_adopted"] | None = None


class PracticeObserveIn(BaseModel):
    observed_action: str = Field(min_length=3)
    action_date: date
    adopted: bool = True


class PracticeOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    farmer_id: uuid.UUID
    farmer: Ref | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    geo_unit_id: uuid.UUID
    organisation_id: uuid.UUID | None
    module_code: str
    activity_code: str
    advisory_id: uuid.UUID | None
    recommended_action: str | None
    reported_action: str | None
    observed_action: str | None
    action_date: date | None
    evidence_basis: str
    status: str
    verification_status: str
    demonstration_site_id: uuid.UUID | None
    observed_by_id: uuid.UUID | None
    source_system_id: uuid.UUID | None
    is_demo: bool
    created_at: datetime


class ActivityOut(ORMModel):
    id: uuid.UUID
    activity_code: str
    name: str
    description: str | None
    status: str
    module_code: str | None = None
