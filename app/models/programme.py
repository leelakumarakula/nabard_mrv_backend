import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, Numeric, Unicode, UnicodeText, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import ArchiveMixin, DemoMixin, EntityBase, fk, status_col


class Programme(Base, EntityBase, DemoMixin):
    __tablename__ = "programmes"

    code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("draft")  # draft | submitted | approved | active | inactive
    funding_framework: Mapped[str | None] = mapped_column(Unicode(255))
    funding_note: Mapped[str | None] = mapped_column(UnicodeText)
    # Programme version / source proposal (V2: the latest proposal is primary; earlier proposals are never blended)
    version_label: Mapped[str | None] = mapped_column(Unicode(80))
    proposal_title: Mapped[str | None] = mapped_column(Unicode(500))
    proposal_date: Mapped[date | None] = mapped_column(Date)
    duration_months: Mapped[int | None] = mapped_column(Integer)
    supersedes_programme_id: Mapped[uuid.UUID | None] = fk("programmes")

    @property
    def label(self) -> str:
        return self.name


class ProgrammeTarget(Base, EntityBase, DemoMixin):
    """Planning target (e.g. number of farmers). Status shows whether it is only proposed."""

    __tablename__ = "programme_targets"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    metric_code: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    metric_name: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    period_label: Mapped[str | None] = mapped_column(Unicode(80))
    status: Mapped[str] = status_col("proposed")  # proposed | approved | revised
    source_note: Mapped[str | None] = mapped_column(UnicodeText)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    indicator_code: Mapped[str | None] = mapped_column(Unicode(80))  # links a planning target to an indicator


class ReportingPeriod(Base, EntityBase, DemoMixin):
    __tablename__ = "reporting_periods"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    code: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    name: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    period_type: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="quarter")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = status_col("open")  # open | closed


class GeoUnit(Base, EntityBase, DemoMixin, ArchiveMixin):
    __tablename__ = "geo_units"

    parent_id: Mapped[uuid.UUID | None] = fk("geo_units")
    unit_type: Mapped[str] = mapped_column(Unicode(20), nullable=False, index=True)  # state | district | cluster
    name: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    geometry_ref: Mapped[str | None] = mapped_column(Unicode(500))
    # Provisional master data awaiting programme-owner confirmation (e.g. the sixth state)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    confirmation_note: Mapped[str | None] = mapped_column(UnicodeText)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.unit_type})"

    parent: Mapped["GeoUnit | None"] = relationship(remote_side="GeoUnit.id", lazy="joined", join_depth=2)


class Organisation(Base, EntityBase, DemoMixin, ArchiveMixin):
    __tablename__ = "organisations"

    org_type: Mapped[str] = mapped_column(Unicode(30), nullable=False, index=True)  # fpo | partner | department | implementing_agency
    code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    registration_ref: Mapped[str | None] = mapped_column(Unicode(120))
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    contact_name: Mapped[str | None] = mapped_column(Unicode(160))
    contact_phone: Mapped[str | None] = mapped_column(Unicode(30))
    contact_email: Mapped[str | None] = mapped_column(Unicode(255))
    status: Mapped[str] = status_col("active")  # active | inactive

    geo_unit: Mapped[GeoUnit | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return self.name


class ProgrammeOrganisation(Base, EntityBase):
    __tablename__ = "programme_organisations"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    organisation_id: Mapped[uuid.UUID] = fk("organisations", nullable=False)
    role: Mapped[str] = mapped_column(Unicode(50), nullable=False)  # implementing_fpo | technical_partner | funder | ...
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
