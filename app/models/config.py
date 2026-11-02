"""Programme-configurable reference data: beneficiary categories, demographic fields, report templates and the
register of pending programme-owner decisions. Nothing here classifies people automatically."""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Integer, Unicode, UnicodeText, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col


class BeneficiaryCategory(Base, EntityBase):
    """A programme-defined beneficiary category (e.g. woman beneficiary). Membership is assigned explicitly."""

    __tablename__ = "beneficiary_categories"

    code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    definition: Mapped[str | None] = mapped_column(UnicodeText)  # approved eligibility definition (pending until approved)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    requires_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = status_col("proposed")  # proposed | approved | retired
    approval_note: Mapped[str | None] = mapped_column(UnicodeText)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    @property
    def label(self) -> str:
        return self.name


class FarmerCategoryAssignment(Base, EntityBase, DemoMixin):
    __tablename__ = "farmer_category_assignments"
    __table_args__ = (UniqueConstraint("farmer_id", "category_id"),)

    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    category_id: Mapped[uuid.UUID] = fk("beneficiary_categories", nullable=False)
    status: Mapped[str] = status_col("active")  # active | removed
    assigned_on: Mapped[date | None] = mapped_column(Date)
    # self_declared | document | field_verified | other
    basis: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="self_declared")
    note: Mapped[str | None] = mapped_column(UnicodeText)

    category: Mapped[BeneficiaryCategory] = relationship(lazy="joined")


class DemographicField(Base, EntityBase):
    """A configurable demographic attribute captured in Farmer.demographics."""

    __tablename__ = "demographic_fields"

    code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    field_type: Mapped[str] = mapped_column(Unicode(20), nullable=False)  # text | select | boolean | number | date
    options: Mapped[list[str] | None] = mapped_column(JSON)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    requires_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = status_col("proposed")  # proposed | approved | retired
    help_text: Mapped[str | None] = mapped_column(UnicodeText)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReportTemplate(Base, EntityBase):
    """Configurable report layout: which sections a report includes. Official external templates stay pending."""

    __tablename__ = "report_templates"

    code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    base_report_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    sections: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = status_col("draft")  # draft | approved | awaiting_official_template | retired
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(UnicodeText)


class ProgrammeDecision(Base, EntityBase):
    """Register of programme-owner decisions: what is pending, the provisional treatment and what it affects."""

    __tablename__ = "programme_decisions"

    programme_id: Mapped[uuid.UUID | None] = fk("programmes")
    code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    question: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    # scope | indicators | inclusion | field | verification | reporting | integration | privacy
    category: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    status: Mapped[str] = status_col("pending")  # pending | decided | superseded
    provisional_treatment: Mapped[str | None] = mapped_column(UnicodeText)
    decision: Mapped[str | None] = mapped_column(UnicodeText)
    decided_by: Mapped[str | None] = mapped_column(Unicode(200))
    decided_on: Mapped[date | None] = mapped_column(Date)
    owner: Mapped[str | None] = mapped_column(Unicode(200))
    affects: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
