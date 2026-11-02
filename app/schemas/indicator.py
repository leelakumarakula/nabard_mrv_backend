import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel, Ref

ScopeType = Literal["programme", "state", "district", "cluster", "fpo", "farmer", "plot"]


class IndicatorIn(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z0-9_]+$")
    name: str
    family: str
    module_code: str | None = None
    definition: str
    numerator: str | None = None
    denominator: str | None = None
    unit: str
    method_code: str | None = None
    method_reference: str | None = None
    method_parameters: dict[str, Any] = {}
    frequency: str | None = None
    scope_level: str | None = None
    aggregation_rule: str | None = None
    disaggregation: str | None = None
    baseline_method: str | None = None
    data_sources: str | None = None
    validation_rules: dict[str, Any] = {}
    evidence_requirements: str | None = None
    limitations: str | None = None
    eligibility: str | None = None
    exclusions: str | None = None
    verification_sample_approach: str | None = None
    approval_note: str | None = None


class IndicatorUpdate(BaseModel):
    name: str | None = None
    family: str | None = None
    module_code: str | None = None
    definition: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    method_code: str | None = None
    method_reference: str | None = None
    method_parameters: dict[str, Any] | None = None
    frequency: str | None = None
    scope_level: str | None = None
    aggregation_rule: str | None = None
    disaggregation: str | None = None
    baseline_method: str | None = None
    data_sources: str | None = None
    validation_rules: dict[str, Any] | None = None
    evidence_requirements: str | None = None
    limitations: str | None = None
    eligibility: str | None = None
    exclusions: str | None = None
    verification_sample_approach: str | None = None
    approval_note: str | None = None


class IndicatorStatusIn(BaseModel):
    status: Literal["draft", "submitted", "approved", "retired"]
    requires_confirmation: bool | None = None
    note: str | None = None


class IndicatorOut(ORMModel):
    id: uuid.UUID
    code: str
    version: int
    name: str
    family: str
    module_code: str | None
    definition: str
    numerator: str | None
    denominator: str | None
    unit: str
    method_code: str | None
    method_reference: str | None
    method_parameters: dict[str, Any]
    frequency: str | None
    scope_level: str | None
    aggregation_rule: str | None
    disaggregation: str | None
    baseline_method: str | None
    data_sources: str | None
    validation_rules: dict[str, Any]
    evidence_requirements: str | None
    limitations: str | None
    eligibility: str | None
    exclusions: str | None
    verification_sample_approach: str | None
    approval_note: str | None
    status: str
    requires_confirmation: bool
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class IndicatorTargetIn(BaseModel):
    indicator_id: uuid.UUID
    programme_id: uuid.UUID
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    target_value: Decimal
    unit: str
    period_label: str | None = None
    approval_note: str | None = None
    source_note: str | None = None
    target_version: int = Field(default=1, ge=1)


class IndicatorTargetUpdate(BaseModel):
    target_value: Decimal | None = None
    period_label: str | None = None
    status: Literal["proposed", "approved", "revised"] | None = None
    approval_note: str | None = None


class IndicatorTargetOut(ORMModel):
    id: uuid.UUID
    indicator_id: uuid.UUID
    indicator: Ref | None
    programme_id: uuid.UUID
    geo_unit_id: uuid.UUID | None
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    target_value: Decimal
    unit: str
    period_label: str | None
    status: str
    approval_note: str | None
    source_note: str | None
    target_version: int
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    is_demo: bool


class LifecycleOut(ORMModel):
    submission_status: str
    validation_status: str
    validation_messages: list[str] | None
    review_status: str
    verification_status: str


class BaselineIn(BaseModel):
    indicator_id: uuid.UUID
    programme_id: uuid.UUID
    scope_type: ScopeType = "programme"
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    value: Decimal
    unit: str
    period_label: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    method: str | None = None
    source: str | None = None
    data_nature: Literal["observed", "user_reported", "calculated", "assessment"] = "observed"
    notes: str | None = None


class BaselineUpdate(BaseModel):
    value: Decimal | None = None
    unit: str | None = None
    period_label: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    method: str | None = None
    source: str | None = None
    data_nature: Literal["observed", "user_reported", "calculated", "assessment"] | None = None
    notes: str | None = None


class BaselineOut(LifecycleOut):
    id: uuid.UUID
    indicator_id: uuid.UUID
    indicator: Ref | None
    programme_id: uuid.UUID
    scope_type: str
    geo_unit_id: uuid.UUID | None
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    farmer_id: uuid.UUID | None
    farmer: Ref | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    value: Decimal
    unit: str
    period_label: str | None
    period_start: date | None
    period_end: date | None
    method: str | None
    source: str | None
    data_nature: str
    notes: str | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    is_demo: bool


class MonitoringIn(BaseModel):
    programme_id: uuid.UUID
    indicator_id: uuid.UUID
    activity_code: str | None = None
    farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    field_record_id: uuid.UUID | None = None
    reporting_period_id: uuid.UUID | None = None
    value: Decimal
    unit: str
    observed_at: datetime
    method: str | None = None
    data_nature: Literal["observed", "user_reported"] = "observed"
    notes: str | None = None

    @model_validator(mode="after")
    def location(self) -> "MonitoringIn":
        if not (self.farm_plot_id or self.farmer_id or self.geo_unit_id):
            raise ValueError("A plot, farmer or geography is required")
        return self


class MonitoringUpdate(BaseModel):
    activity_code: str | None = None
    value: Decimal | None = None
    unit: str | None = None
    observed_at: datetime | None = None
    method: str | None = None
    reporting_period_id: uuid.UUID | None = None
    notes: str | None = None


class MonitoringOut(LifecycleOut):
    id: uuid.UUID
    programme_id: uuid.UUID
    indicator_id: uuid.UUID
    indicator: Ref | None
    activity_code: str | None
    farmer_id: uuid.UUID | None
    farmer: Ref | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    field_record_id: uuid.UUID | None
    reporting_period_id: uuid.UUID | None
    value: Decimal
    unit: str
    original_value: Decimal | None
    original_unit: str | None
    observed_at: datetime
    method: str | None
    data_nature: str
    source_system_id: uuid.UUID | None
    source_record_id: str | None
    source_timestamp: datetime | None
    source_evidence_refs: list[dict[str, Any]] | None
    received_at: datetime | None
    notes: str | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    is_demo: bool


class ExceptionResolutionIn(BaseModel):
    decision: Literal["accept", "reject"]
    note: str = Field(min_length=3)
