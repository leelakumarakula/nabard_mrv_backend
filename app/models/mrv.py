import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Index, Integer, Numeric, Unicode, UnicodeText, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, IdMixin, fk, status_col
from app.models.indicator import IndicatorDefinition


class ScopeMixin:
    scope_type: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="programme")  # programme|state|district|cluster|fpo
    scope_geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    scope_organisation_id: Mapped[uuid.UUID | None] = fk("organisations")


class CalculationRun(Base, EntityBase, DemoMixin, ScopeMixin):
    __tablename__ = "calculation_runs"

    indicator_id: Mapped[uuid.UUID] = fk("indicator_definitions", nullable=False)
    indicator_version: Mapped[int] = mapped_column(Integer, nullable=False)
    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    reporting_period_id: Mapped[uuid.UUID | None] = fk("reporting_periods")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    method_code: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    method_version: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = status_col("running")  # running | calculated | calculation_error
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(UnicodeText)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    indicator: Mapped[IndicatorDefinition] = relationship(lazy="joined")


class CalculationRunInput(Base, IdMixin):
    __tablename__ = "calculation_run_inputs"

    calculation_run_id: Mapped[uuid.UUID] = fk("calculation_runs", nullable=False)
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class IndicatorResult(Base, EntityBase, DemoMixin, ScopeMixin):
    __tablename__ = "indicator_results"
    __table_args__ = (Index("ix_indicator_results_indicator_period", "indicator_id", "reporting_period_id"),)

    indicator_id: Mapped[uuid.UUID] = fk("indicator_definitions", nullable=False)
    indicator_version: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_run_id: Mapped[uuid.UUID] = fk("calculation_runs", nullable=False)
    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    reporting_period_id: Mapped[uuid.UUID | None] = fk("reporting_periods")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    numerator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    denominator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    baseline_record_id: Mapped[uuid.UUID | None] = fk("baseline_records")
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    change_from_baseline: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    method_code: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    method_version: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    calculation_status: Mapped[str] = status_col("calculated")  # not_calculated | calculated | calculation_error
    review_status: Mapped[str] = status_col("not_submitted")  # not_submitted | pending_review | correction_requested | reviewed
    verification_status: Mapped[str] = status_col("unverified")  # unverified | verified | not_verified | rejected | conditional
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # True when calculated from an indicator definition not yet approved (provisional mode only)
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    indicator: Mapped[IndicatorDefinition] = relationship(lazy="joined")


class ReviewTask(Base, EntityBase, DemoMixin):
    __tablename__ = "review_tasks"
    __table_args__ = (Index("ix_review_tasks_entity", "entity_type", "entity_id"),)

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)  # indicator_result | baseline_record | monitoring_record
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    assigned_reviewer_id: Mapped[uuid.UUID | None] = fk("users")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("pending_review")  # pending_review | correction_requested | reviewed | closed
    submission_note: Mapped[str | None] = mapped_column(UnicodeText)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # internal reviewer who completed review


class ReviewComment(Base, EntityBase):
    __tablename__ = "review_comments"

    review_task_id: Mapped[uuid.UUID] = fk("review_tasks", nullable=False)
    author_id: Mapped[uuid.UUID] = fk("users", nullable=False)
    comment: Mapped[str] = mapped_column(UnicodeText, nullable=False)


class VerificationFinding(Base, EntityBase, DemoMixin):
    __tablename__ = "verification_findings"

    review_task_id: Mapped[uuid.UUID | None] = fk("review_tasks")
    verification_sample_id: Mapped[uuid.UUID | None] = fk("verification_samples")
    finding_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)  # data_quality | evidence_gap | method | calculation | other
    severity: Mapped[str] = mapped_column(Unicode(20), nullable=False)  # minor | major | critical
    description: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    evidence_asset_id: Mapped[uuid.UUID | None] = fk("evidence_assets")
    corrective_action: Mapped[str | None] = mapped_column(UnicodeText)
    corrective_status: Mapped[str] = status_col("open")  # open | in_progress | resolved | accepted
    resolution_note: Mapped[str | None] = mapped_column(UnicodeText)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationDecision(Base, EntityBase, DemoMixin):
    __tablename__ = "verification_decisions"

    review_task_id: Mapped[uuid.UUID] = fk("review_tasks", nullable=False)
    decision: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # verified | not_verified | rejected | conditional
    rationale: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    decided_by_id: Mapped[uuid.UUID] = fk("users", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Report(Base, EntityBase, DemoMixin, ScopeMixin):
    __tablename__ = "reports"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    reporting_period_id: Mapped[uuid.UUID | None] = fk("reporting_periods")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    template_code: Mapped[str | None] = mapped_column(Unicode(60))
    module_code: Mapped[str | None] = mapped_column(Unicode(30))
    indicator_id: Mapped[uuid.UUID | None] = fk("indicator_definitions")
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = status_col("generated")  # draft | generated | approved | published
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    generated_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
