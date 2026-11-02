import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, Ref


class CalculationRequest(BaseModel):
    indicator_id: uuid.UUID
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None
    scope_type: Literal["programme", "state", "district", "cluster", "fpo"] = "programme"
    scope_geo_unit_id: uuid.UUID | None = None
    scope_organisation_id: uuid.UUID | None = None
    parameters: dict[str, Any] = {}


class PreviewRequest(BaseModel):
    programme_id: uuid.UUID
    period_start: date
    period_end: date
    scope_type: Literal["programme", "state", "district", "cluster", "fpo"] = "programme"
    scope_geo_unit_id: uuid.UUID | None = None
    scope_organisation_id: uuid.UUID | None = None
    method_code: str | None = None  # try an alternative method without saving it
    parameters: dict[str, Any] | None = None  # replaces the stored method parameters for the preview


class CalculationRunOut(ORMModel):
    id: uuid.UUID
    indicator_id: uuid.UUID
    indicator: Ref | None
    indicator_version: int
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None
    period_start: date
    period_end: date
    method_code: str
    method_version: str
    parameters: dict[str, Any]
    scope_type: str
    scope_geo_unit_id: uuid.UUID | None
    scope_organisation_id: uuid.UUID | None
    status: str
    output: dict[str, Any] | None
    error: str | None
    input_count: int
    started_at: datetime
    finished_at: datetime | None
    created_by_id: uuid.UUID | None
    is_demo: bool


class RunInputOut(ORMModel):
    entity_type: str
    entity_id: uuid.UUID


class IndicatorResultOut(ORMModel):
    id: uuid.UUID
    indicator_id: uuid.UUID
    indicator: Ref | None
    indicator_version: int
    calculation_run_id: uuid.UUID
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None
    period_start: date
    period_end: date
    value: Decimal | None
    unit: str
    numerator: Decimal | None
    denominator: Decimal | None
    baseline_record_id: uuid.UUID | None
    baseline_value: Decimal | None
    change_from_baseline: Decimal | None
    method_code: str
    method_version: str
    scope_type: str
    scope_geo_unit_id: uuid.UUID | None
    scope_organisation_id: uuid.UUID | None
    calculation_status: str
    review_status: str
    verification_status: str
    submitted_by_id: uuid.UUID | None
    submitted_at: datetime | None
    superseded_by_id: uuid.UUID | None
    is_provisional: bool
    created_at: datetime
    is_demo: bool
    scope_label: str | None = None


class CalculationOut(BaseModel):
    run: CalculationRunOut
    result: IndicatorResultOut | None


class SubmitForReviewIn(BaseModel):
    reviewer_id: uuid.UUID | None = None
    due_date: date | None = None
    note: str | None = None


class ReviewTaskOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    title: str
    submitted_by_id: uuid.UUID
    assigned_reviewer_id: uuid.UUID | None
    due_date: date | None
    status: str
    submission_note: str | None
    reviewed_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    is_demo: bool
    submitted_by_name: str | None = None
    reviewer_name: str | None = None


class CommentIn(BaseModel):
    comment: str = Field(min_length=1)


class CommentOut(ORMModel):
    id: uuid.UUID
    author_id: uuid.UUID
    comment: str
    created_at: datetime
    author_name: str | None = None


class FindingIn(BaseModel):
    finding_type: Literal["data_quality", "evidence_gap", "method", "calculation", "other"]
    severity: Literal["minor", "major", "critical"]
    description: str = Field(min_length=3)
    evidence_asset_id: uuid.UUID | None = None
    corrective_action: str | None = None


class FindingUpdate(BaseModel):
    corrective_action: str | None = None
    corrective_status: Literal["open", "in_progress", "resolved", "accepted"] | None = None
    resolution_note: str | None = None


class FindingOut(ORMModel):
    id: uuid.UUID
    review_task_id: uuid.UUID | None
    verification_sample_id: uuid.UUID | None
    finding_type: str
    severity: str
    description: str
    evidence_asset_id: uuid.UUID | None
    corrective_action: str | None
    corrective_status: str
    resolution_note: str | None
    resolved_by_id: uuid.UUID | None
    resolved_at: datetime | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    is_demo: bool


class NoteIn(BaseModel):
    note: str | None = None


class CorrectionIn(BaseModel):
    note: str = Field(min_length=3)


class DecisionIn(BaseModel):
    decision: Literal["verified", "not_verified", "rejected", "conditional"]
    rationale: str = Field(min_length=5)


class DecisionOut(ORMModel):
    id: uuid.UUID
    review_task_id: uuid.UUID
    decision: str
    rationale: str
    decided_by_id: uuid.UUID
    decided_at: datetime
    version: int
    decider_name: str | None = None


class ReviewTaskDetail(ReviewTaskOut):
    entity: dict[str, Any]
    comments: list[CommentOut] = []
    findings: list[FindingOut] = []
    decisions: list[DecisionOut] = []
    open_blocking_findings: int = 0
    can_review: bool = False
    can_decide: bool = False
