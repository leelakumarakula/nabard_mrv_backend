import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Index, Integer, Numeric, Unicode, UnicodeText, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col
from app.models.programme import GeoUnit, Organisation
from app.models.registry import Farmer, FarmPlot


class IndicatorDefinition(Base, EntityBase, DemoMixin):
    __tablename__ = "indicator_definitions"
    __table_args__ = (UniqueConstraint("code", "version"),)

    code: Mapped[str] = mapped_column(Unicode(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    family: Mapped[str] = mapped_column(Unicode(60), nullable=False)
    module_code: Mapped[str | None] = mapped_column(Unicode(30))
    definition: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    numerator: Mapped[str | None] = mapped_column(UnicodeText)
    denominator: Mapped[str | None] = mapped_column(UnicodeText)
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    method_code: Mapped[str | None] = mapped_column(Unicode(80))  # calculation method registry key
    method_reference: Mapped[str | None] = mapped_column(UnicodeText)
    method_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    frequency: Mapped[str | None] = mapped_column(Unicode(40))
    scope_level: Mapped[str | None] = mapped_column(Unicode(40))
    aggregation_rule: Mapped[str | None] = mapped_column(Unicode(200))
    disaggregation: Mapped[str | None] = mapped_column(Unicode(255))
    baseline_method: Mapped[str | None] = mapped_column(UnicodeText)
    data_sources: Mapped[str | None] = mapped_column(UnicodeText)
    validation_rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_requirements: Mapped[str | None] = mapped_column(UnicodeText)
    limitations: Mapped[str | None] = mapped_column(UnicodeText)
    eligibility: Mapped[str | None] = mapped_column(UnicodeText)
    exclusions: Mapped[str | None] = mapped_column(UnicodeText)
    verification_sample_approach: Mapped[str | None] = mapped_column(UnicodeText)
    approval_note: Mapped[str | None] = mapped_column(UnicodeText)
    status: Mapped[str] = status_col("draft")  # draft | submitted | approved | retired
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    @property
    def label(self) -> str:
        return f"{self.code} v{self.version} – {self.name}"


class IndicatorTarget(Base, EntityBase, DemoMixin):
    __tablename__ = "indicator_targets"

    indicator_id: Mapped[uuid.UUID] = fk("indicator_definitions", nullable=False)
    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    period_label: Mapped[str | None] = mapped_column(Unicode(80))
    status: Mapped[str] = status_col("proposed")  # proposed | approved | revised
    approval_note: Mapped[str | None] = mapped_column(UnicodeText)
    source_note: Mapped[str | None] = mapped_column(UnicodeText)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    indicator: Mapped[IndicatorDefinition] = relationship(lazy="joined")
    geo_unit: Mapped[GeoUnit | None] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")


class LifecycleMixin:
    """Separate status dimensions; one record can be data-valid but not yet verified."""

    submission_status: Mapped[str] = status_col("draft")  # draft | submitted | imported
    validation_status: Mapped[str] = status_col("pending_validation")  # pending_validation | valid | rejected | exception
    validation_messages: Mapped[list[str] | None] = mapped_column(JSON)
    review_status: Mapped[str] = status_col("not_submitted")  # not_submitted | pending_review | correction_requested | reviewed
    verification_status: Mapped[str] = status_col("unverified")  # unverified | verified | not_verified | rejected | conditional


class BaselineRecord(Base, EntityBase, DemoMixin, LifecycleMixin):
    __tablename__ = "baseline_records"

    indicator_id: Mapped[uuid.UUID] = fk("indicator_definitions", nullable=False)
    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    # programme | state | district | cluster | fpo | farmer | plot
    scope_type: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="programme")
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    period_label: Mapped[str | None] = mapped_column(Unicode(80))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    method: Mapped[str | None] = mapped_column(UnicodeText)
    source: Mapped[str | None] = mapped_column(UnicodeText)
    data_nature: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="observed")
    notes: Mapped[str | None] = mapped_column(UnicodeText)

    indicator: Mapped[IndicatorDefinition] = relationship(lazy="joined")
    geo_unit: Mapped[GeoUnit | None] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")
    farmer: Mapped[Farmer | None] = relationship(lazy="joined")
    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")


class MonitoringRecord(Base, EntityBase, DemoMixin, LifecycleMixin):
    __tablename__ = "monitoring_records"
    __table_args__ = (Index("ix_monitoring_records_indicator_observed", "indicator_id", "observed_at"),)

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    indicator_id: Mapped[uuid.UUID] = fk("indicator_definitions", nullable=False)
    activity_code: Mapped[str | None] = mapped_column(Unicode(60))
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    field_record_id: Mapped[uuid.UUID | None] = fk("field_records")
    reporting_period_id: Mapped[uuid.UUID | None] = fk("reporting_periods")
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)  # normalised to the indicator unit
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    original_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    original_unit: Mapped[str | None] = mapped_column(Unicode(40))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str | None] = mapped_column(Unicode(120))
    data_nature: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="observed")
    source_system_id: Mapped[uuid.UUID | None] = fk("integration_sources")
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_evidence_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(UnicodeText)

    indicator: Mapped[IndicatorDefinition] = relationship(lazy="joined")
    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    farmer: Mapped[Farmer | None] = relationship(lazy="joined")
    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")
