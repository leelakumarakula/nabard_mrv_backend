import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel, Ref


class DemoSiteIn(BaseModel):
    programme_id: uuid.UUID
    site_code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=200)
    category: Literal["plantation", "soil_moisture"]
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    host_farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    practices: list[str] = []
    established_on: date | None = None
    status: Literal["planned", "established", "active", "closed"] = "planned"
    selection_note: str | None = None
    notes: str | None = None
    criteria_assessment: dict[str, Any] | None = None


class DemoSiteUpdate(BaseModel):
    name: str | None = None
    host_farmer_id: uuid.UUID | None = None
    farm_plot_id: uuid.UUID | None = None
    practices: list[str] | None = None
    established_on: date | None = None
    status: Literal["planned", "established", "active", "closed"] | None = None
    selection_note: str | None = None
    notes: str | None = None
    criteria_assessment: dict[str, Any] | None = None


class DemoSiteOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    site_code: str
    name: str
    category: str
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    host_farmer_id: uuid.UUID | None
    host_farmer: Ref | None
    farm_plot_id: uuid.UUID | None
    farm_plot: Ref | None
    practices: list[str]
    established_on: date | None
    status: str
    selection_note: str | None
    notes: str | None
    criteria_assessment: dict[str, Any] | None
    is_demo: bool
    visit_count: int = 0
    last_visit_at: datetime | None = None


class ClusterIn(BaseModel):
    programme_id: uuid.UUID
    cluster_code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=200)
    geo_unit_id: uuid.UUID
    organisation_id: uuid.UUID | None = None
    established_on: date | None = None
    status: Literal["planned", "active", "inactive"] = "planned"
    target_pests: list[str] = []
    notes: str | None = None
    criteria_assessment: dict[str, Any] | None = None


class ClusterUpdate(BaseModel):
    name: str | None = None
    organisation_id: uuid.UUID | None = None
    established_on: date | None = None
    status: Literal["planned", "active", "inactive"] | None = None
    target_pests: list[str] | None = None
    notes: str | None = None
    criteria_assessment: dict[str, Any] | None = None


class ClusterOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    cluster_code: str
    name: str
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    established_on: date | None
    status: str
    target_pests: list[str]
    notes: str | None
    criteria_assessment: dict[str, Any] | None
    is_demo: bool
    member_households: int = 0
    inspections: int = 0
    last_inspection_at: datetime | None = None


class MembershipIn(BaseModel):
    farmer_id: uuid.UUID
    joined_on: date | None = None
    role: Literal["participant", "lead_scout"] = "participant"


class MembershipUpdate(BaseModel):
    status: Literal["active", "left"] | None = None
    role: Literal["participant", "lead_scout"] | None = None


class MembershipOut(ORMModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    farmer_id: uuid.UUID
    farmer: Ref | None
    joined_on: date | None
    status: str
    role: str
    is_demo: bool


class CrpIn(BaseModel):
    programme_id: uuid.UUID
    crp_code: str = Field(min_length=2, max_length=50)
    full_name: str = Field(min_length=1, max_length=200)
    gender: Literal["female", "male", "other", "not_stated"] | None = None
    phone: str | None = None
    farmer_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    geo_unit_id: uuid.UUID | None = None
    status: Literal["nominated", "in_training", "trained", "active", "inactive"] = "nominated"
    training_started_on: date | None = None
    training_completed_on: date | None = None
    notes: str | None = None


class CrpUpdate(BaseModel):
    full_name: str | None = None
    gender: Literal["female", "male", "other", "not_stated"] | None = None
    phone: str | None = None
    organisation_id: uuid.UUID | None = None
    status: Literal["nominated", "in_training", "trained", "active", "inactive"] | None = None
    training_started_on: date | None = None
    training_completed_on: date | None = None
    competency_result: Literal["not_assessed", "competent", "needs_support"] | None = None
    competency_assessed_on: date | None = None
    notes: str | None = None


class CrpOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    crp_code: str
    full_name: str
    gender: str | None
    phone: str | None
    farmer_id: uuid.UUID | None
    user_id: uuid.UUID | None
    organisation_id: uuid.UUID | None
    organisation: Ref | None
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    status: str
    training_started_on: date | None
    training_completed_on: date | None
    competency_result: str
    competency_assessed_on: date | None
    notes: str | None
    is_demo: bool


SampleEntity = Literal["practice_record", "demonstration_site", "field_record", "monitoring_record"]


class FrameFilter(BaseModel):
    period_start: date | None = None
    period_end: date | None = None
    geo_unit_id: uuid.UUID | None = None
    activity_codes: list[str] | None = None  # practice_record
    practice_status: Literal["observed", "reported"] = "observed"  # practice_record
    record_type: str | None = None  # field_record
    indicator_id: uuid.UUID | None = None  # monitoring_record
    category: Literal["plantation", "soil_moisture"] | None = None  # demonstration_site


class SampleIn(BaseModel):
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=255)
    entity_type: SampleEntity
    frame_description: str = Field(min_length=3)
    frame_filter: FrameFilter = FrameFilter()
    selection_method: Literal["simple_random", "census", "purposive"]
    sample_size: int = Field(ge=1, le=5000)
    random_seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    verifier_id: uuid.UUID | None = None


class DrawIn(BaseModel):
    entity_ids: list[uuid.UUID] | None = None  # required for purposive selection

    @model_validator(mode="after")
    def unique(self) -> "DrawIn":
        if self.entity_ids and len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("entity_ids must be unique")
        return self


class SampleItemUpdate(BaseModel):
    result: Literal["confirmed", "discrepancy", "not_found", "not_visited"]
    visit_date: date | None = None
    observation: str | None = None
    discrepancy_note: str | None = None
    checklist_results: dict[str, Any] | None = None

    @model_validator(mode="after")
    def note_for_discrepancy(self) -> "SampleItemUpdate":
        if self.result == "discrepancy" and not self.discrepancy_note:
            raise ValueError("Describe the discrepancy")
        if self.result in ("confirmed", "discrepancy") and not self.visit_date:
            raise ValueError("Record the visit date")
        return self


class SampleCompleteIn(BaseModel):
    conclusion: Literal["confirmed", "discrepancies_found", "inconclusive"]
    conclusion_note: str = Field(min_length=5)


class SampleItemOut(ORMModel):
    id: uuid.UUID
    sample_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    entity_label: str | None
    result: str
    visit_date: date | None
    observation: str | None
    discrepancy_note: str | None
    checklist_results: dict[str, Any] | None
    verified_by_id: uuid.UUID | None
    verified_at: datetime | None


class SampleOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None
    title: str
    entity_type: str
    frame_description: str
    frame_filter: dict[str, Any]
    frame_size: int
    selection_method: str
    sample_size: int
    random_seed: int | None
    verifier_id: uuid.UUID | None
    status: str
    conclusion: str | None
    conclusion_note: str | None
    completed_at: datetime | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    is_demo: bool
    result_counts: dict[str, int] = {}


class SampleDetail(SampleOut):
    items: list[SampleItemOut] = []
    findings: list[dict[str, Any]] = []
