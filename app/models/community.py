"""Version 2.0 programme structures: demonstration sites, pest surveillance clusters and community resource persons."""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import JSON, Date, Unicode, UnicodeText, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col
from app.models.programme import GeoUnit, Organisation
from app.models.registry import Farmer, FarmPlot


class DemonstrationSite(Base, EntityBase, DemoMixin):
    """Plantation or soil/moisture demonstration site. Visits are `demonstration_visit` workstream records."""

    __tablename__ = "demonstration_sites"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    site_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    category: Mapped[str] = mapped_column(Unicode(30), nullable=False, index=True)  # plantation | soil_moisture
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    host_farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    practices: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)  # activity codes demonstrated
    established_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("planned")  # planned | established | active | closed
    selection_note: Mapped[str | None] = mapped_column(UnicodeText)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    # {criterion_code: {"met": bool, "note": str}} for the configured selection criteria
    criteria_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")
    host_farmer: Mapped[Farmer | None] = relationship(lazy="joined")
    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return f"{self.site_code} – {self.name}"


class PestSurveillanceCluster(Base, EntityBase, DemoMixin):
    __tablename__ = "pest_surveillance_clusters"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    cluster_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    established_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("planned")  # planned | active | inactive
    target_pests: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    criteria_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return f"{self.cluster_code} – {self.name}"


class ClusterMembership(Base, EntityBase, DemoMixin):
    """Household (farmer) participation in a pest surveillance cluster."""

    __tablename__ = "cluster_memberships"
    __table_args__ = (UniqueConstraint("cluster_id", "farmer_id"),)

    cluster_id: Mapped[uuid.UUID] = fk("pest_surveillance_clusters", nullable=False)
    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    joined_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("active")  # active | left
    role: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="participant")  # participant | lead_scout

    farmer: Mapped[Farmer] = relationship(lazy="joined")


class CommunityResourcePerson(Base, EntityBase, DemoMixin):
    __tablename__ = "community_resource_persons"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    crp_code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(Unicode(20))
    phone: Mapped[str | None] = mapped_column(Unicode(30))  # personal data – restricted
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    user_id: Mapped[uuid.UUID | None] = fk("users")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    status: Mapped[str] = status_col("nominated")  # nominated | in_training | trained | active | inactive
    training_started_on: Mapped[date | None] = mapped_column(Date)
    training_completed_on: Mapped[date | None] = mapped_column(Date)
    # not_assessed | competent | needs_support – competency method pending programme approval
    competency_result: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="not_assessed")
    competency_assessed_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return f"{self.crp_code} – {self.full_name}"
