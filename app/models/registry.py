import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, Integer, Numeric, Unicode, UnicodeText, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import ArchiveMixin, DemoMixin, EntityBase, fk, status_col
from app.models.programme import GeoUnit, Organisation


class Farmer(Base, EntityBase, DemoMixin, ArchiveMixin):
    __tablename__ = "farmers"

    farmer_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    household_code: Mapped[str | None] = mapped_column(Unicode(50), index=True)  # stable household identifier
    full_name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(Unicode(20))
    year_of_birth: Mapped[int | None] = mapped_column(Integer)
    phone: Mapped[str | None] = mapped_column(Unicode(30))  # personal data – restricted
    village: Mapped[str | None] = mapped_column(Unicode(160))
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    primary_fpo_id: Mapped[uuid.UUID | None] = fk("organisations")
    consent_status: Mapped[str] = status_col("not_recorded")  # not_recorded | given | withdrawn
    consent_date: Mapped[date | None] = mapped_column(Date)
    consent_purpose: Mapped[str | None] = mapped_column(UnicodeText)
    status: Mapped[str] = status_col("active")  # active | inactive
    # Values of configurable demographic fields (DemographicField codes). Sensitive fields are masked by permission.
    # Beneficiary categories are explicit FarmerCategoryAssignment rows – never inferred.
    demographics: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    primary_fpo: Mapped[Organisation | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return f"{self.farmer_code} – {self.full_name}"


class ProgrammeParticipation(Base, EntityBase, DemoMixin):
    __tablename__ = "programme_participations"
    __table_args__ = (UniqueConstraint("programme_id", "farmer_id"),)

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    enrolled_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("enrolled")  # enrolled | withdrawn | completed
    notes: Mapped[str | None] = mapped_column(UnicodeText)


class Farm(Base, EntityBase, DemoMixin, ArchiveMixin):
    __tablename__ = "farms"

    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    farm_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Unicode(200))
    village: Mapped[str | None] = mapped_column(Unicode(160))
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))  # precise location – restricted
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    total_area: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    area_unit: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="ha")
    area_source: Mapped[str | None] = mapped_column(Unicode(30))  # declared | surveyed | gps_measured | records

    farmer: Mapped[Farmer] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return self.farm_code if not self.name else f"{self.farm_code} – {self.name}"


class FarmPlot(Base, EntityBase, DemoMixin, ArchiveMixin):
    __tablename__ = "farm_plots"

    farm_id: Mapped[uuid.UUID] = fk("farms", nullable=False)
    plot_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    area: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    area_unit: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="ha")
    area_source: Mapped[str | None] = mapped_column(Unicode(30))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geometry_ref: Mapped[str | None] = mapped_column(Unicode(500))
    crop: Mapped[str | None] = mapped_column(Unicode(40))  # arabica | robusta | mixed
    variety: Mapped[str | None] = mapped_column(Unicode(80))
    plantation_year: Mapped[int | None] = mapped_column(Integer)
    # Proposal §5.2: interventions follow verified plantation maturity – productive | young | underperforming
    plantation_stage: Mapped[str | None] = mapped_column(Unicode(30))
    shade_type: Mapped[str | None] = mapped_column(Unicode(80))
    irrigation_type: Mapped[str | None] = mapped_column(Unicode(80))

    farm: Mapped[Farm] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return self.plot_code
