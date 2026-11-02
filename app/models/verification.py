"""Independent sample verification (separate from internal review)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Index, Integer, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col


class VerificationSample(Base, EntityBase, DemoMixin):
    __tablename__ = "verification_samples"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    reporting_period_id: Mapped[uuid.UUID | None] = fk("reporting_periods")
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    # practice_record | demonstration_site | field_record | monitoring_record | farmer
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    frame_description: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    frame_filter: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    frame_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selection_method: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # simple_random | census | purposive
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(Integer)
    scope_geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    verifier_id: Mapped[uuid.UUID | None] = fk("users")
    status: Mapped[str] = status_col("planned")  # planned | drawn | in_progress | completed
    conclusion: Mapped[str | None] = mapped_column(Unicode(30))  # confirmed | discrepancies_found | inconclusive
    conclusion_note: Mapped[str | None] = mapped_column(UnicodeText)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationSampleItem(Base, EntityBase):
    __tablename__ = "verification_sample_items"
    __table_args__ = (Index("ix_verification_sample_items_entity", "entity_type", "entity_id"),)

    sample_id: Mapped[uuid.UUID] = fk("verification_samples", nullable=False)
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    entity_label: Mapped[str | None] = mapped_column(Unicode(255))
    # pending | confirmed | discrepancy | not_found | not_visited
    result: Mapped[str] = status_col("pending")
    visit_date: Mapped[date | None] = mapped_column(Date)
    observation: Mapped[str | None] = mapped_column(UnicodeText)
    discrepancy_note: Mapped[str | None] = mapped_column(UnicodeText)
    # {check_code: {"ok": bool, "note": str}} for the configured verification visit checklist
    checklist_results: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
