import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Index, Integer, Numeric, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col
from app.models.programme import GeoUnit, Organisation
from app.models.registry import Farmer, FarmPlot


class Workstream(Base, EntityBase):
    __tablename__ = "workstreams"

    module_code: Mapped[str] = mapped_column(Unicode(30), unique=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)


class Activity(Base, EntityBase):
    __tablename__ = "activities"

    workstream_id: Mapped[uuid.UUID] = fk("workstreams", nullable=False)
    programme_id: Mapped[uuid.UUID | None] = fk("programmes")
    activity_code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    status: Mapped[str] = status_col("active")


class FieldRecord(Base, EntityBase, DemoMixin):
    """A workstream-specific record (inspection, soil test, risk estimate, training session, ...).

    record_type selects a definition in app/services/record_types.py which declares the module, the nature
    of the data (observed fact vs. model prediction vs. user-reported) and the attribute schema.
    """

    __tablename__ = "field_records"
    __table_args__ = (Index("ix_field_records_module_type_observed", "module_code", "record_type", "observed_at"),)

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    module_code: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    record_type: Mapped[str] = mapped_column(Unicode(60), nullable=False)
    activity_code: Mapped[str | None] = mapped_column(Unicode(60))
    title: Mapped[str | None] = mapped_column(Unicode(255))
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    device_id: Mapped[uuid.UUID | None] = fk("devices")
    demonstration_site_id: Mapped[uuid.UUID | None] = fk("demonstration_sites")
    surveillance_cluster_id: Mapped[uuid.UUID | None] = fk("pest_surveillance_clusters")
    # observed | user_reported | model_prediction | calculated | assessment
    data_nature: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(Unicode(40))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    submission_status: Mapped[str] = status_col("draft")  # draft | submitted | imported
    validation_status: Mapped[str] = status_col("pending_validation")  # pending_validation | valid | rejected | exception
    validation_messages: Mapped[list[str] | None] = mapped_column(JSON)
    source_system_id: Mapped[uuid.UUID | None] = fk("integration_sources")
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    farmer: Mapped[Farmer | None] = relationship(lazy="joined")
    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")
    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")
    device: Mapped["Device | None"] = relationship(lazy="joined")


class FieldRecordParticipant(Base, EntityBase):
    """Farmers attached to a record, e.g. training attendance or demonstration participants."""

    __tablename__ = "field_record_participants"

    field_record_id: Mapped[uuid.UUID] = fk("field_records", nullable=False)
    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    role: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="attendee")
    attended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    farmer: Mapped[Farmer] = relationship(lazy="joined")


class Device(Base, EntityBase, DemoMixin):
    __tablename__ = "devices"

    device_code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(Unicode(60), nullable=False)  # soilsync_7in1 | moisture_probe | microclime | rainsync
    provider: Mapped[str | None] = mapped_column(Unicode(120))
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    installed_on: Mapped[date | None] = mapped_column(Date)
    removed_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = status_col("planned")  # planned | active | faulty | removed
    deployment_justification: Mapped[str | None] = mapped_column(UnicodeText)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    # Installed position (precise location – shown only with farmer.read_pii); source: FarmFuture or entered
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    location_source: Mapped[str | None] = mapped_column(Unicode(40))
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")
    geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        # FarmFuture devices are coded FF-<MAC ID>; people know them by the MAC ID that FarmFuture shows
        if self.device_code.startswith("FF-"):
            return self.device_code[3:]
        return f"{self.device_code} ({self.device_type})"


class DeviceCalibration(Base, EntityBase):
    __tablename__ = "device_calibrations"

    device_id: Mapped[uuid.UUID] = fk("devices", nullable=False)
    calibrated_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str | None] = mapped_column(Unicode(200))
    result: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # pass | fail | adjusted
    next_due_on: Mapped[date | None] = mapped_column(Date)
    performed_by: Mapped[str | None] = mapped_column(Unicode(160))
    notes: Mapped[str | None] = mapped_column(UnicodeText)


class Advisory(Base, EntityBase, DemoMixin):
    __tablename__ = "advisories"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    origin_module: Mapped[str] = mapped_column(Unicode(30), nullable=False, index=True)
    advisory_type: Mapped[str] = mapped_column(Unicode(20), nullable=False)  # alert | advisory
    hazard_type: Mapped[str | None] = mapped_column(Unicode(40))  # drought | heat | excess_rainfall | pest | disease | ...
    risk_level: Mapped[str | None] = mapped_column(Unicode(20))
    risk_record_id: Mapped[uuid.UUID | None] = fk("field_records")
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    message: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(UnicodeText)
    recommended_activity_code: Mapped[str | None] = mapped_column(Unicode(60))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    target_organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    status: Mapped[str] = status_col("draft")  # draft | issued | withdrawn
    source_system_id: Mapped[uuid.UUID | None] = fk("integration_sources")
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))

    target_geo_unit: Mapped[GeoUnit] = relationship(lazy="joined")
    target_organisation: Mapped[Organisation | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return self.title


class AdvisoryDelivery(Base, EntityBase, DemoMixin):
    __tablename__ = "advisory_deliveries"

    advisory_id: Mapped[uuid.UUID] = fk("advisories", nullable=False)
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    channel: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # sms | whatsapp | ivr | app | fpo_meeting | field_visit
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[str | None] = mapped_column(UnicodeText)
    response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    understood: Mapped[bool | None] = mapped_column(Boolean)
    source_system_id: Mapped[uuid.UUID | None] = fk("integration_sources")
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))

    farmer: Mapped[Farmer | None] = relationship(lazy="joined")
    organisation: Mapped[Organisation | None] = relationship(lazy="joined")


class PracticeRecord(Base, EntityBase, DemoMixin):
    """Recommended vs. reported vs. observed adoption of a practice. Delivery of advice is never adoption."""

    __tablename__ = "practice_records"

    programme_id: Mapped[uuid.UUID] = fk("programmes", nullable=False)
    farmer_id: Mapped[uuid.UUID] = fk("farmers", nullable=False)
    farm_plot_id: Mapped[uuid.UUID | None] = fk("farm_plots")
    geo_unit_id: Mapped[uuid.UUID] = fk("geo_units", nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    module_code: Mapped[str] = mapped_column(Unicode(30), nullable=False, index=True)
    activity_code: Mapped[str] = mapped_column(Unicode(60), nullable=False, index=True)
    advisory_id: Mapped[uuid.UUID | None] = fk("advisories")
    recommended_action: Mapped[str | None] = mapped_column(UnicodeText)
    reported_action: Mapped[str | None] = mapped_column(UnicodeText)
    observed_action: Mapped[str | None] = mapped_column(UnicodeText)
    action_date: Mapped[date | None] = mapped_column(Date)
    evidence_basis: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="reported")  # reported | observed
    status: Mapped[str] = status_col("recommended")  # recommended | reported | observed | not_adopted
    # Set only through independent sample verification: unverified | verified | not_verified
    verification_status: Mapped[str] = status_col("unverified")
    demonstration_site_id: Mapped[uuid.UUID | None] = fk("demonstration_sites")
    observed_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_system_id: Mapped[uuid.UUID | None] = fk("integration_sources")
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))

    farmer: Mapped[Farmer] = relationship(lazy="joined")
    farm_plot: Mapped[FarmPlot | None] = relationship(lazy="joined")
