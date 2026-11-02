import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, Unicode, UnicodeText, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, fk, status_col


class EvidenceAsset(Base, EntityBase, DemoMixin):
    """File metadata; bytes live in private storage under storage_key, never publicly exposed."""

    __tablename__ = "evidence_assets"

    storage_key: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(Unicode(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    # photo | document | field_form | certificate | dataset | other
    evidence_type: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="document", server_default="document")
    source: Mapped[str] = mapped_column(Unicode(60), nullable=False, default="field_upload", server_default="field_upload")
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # internal | restricted | personal
    access_classification: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="internal")
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    review_status: Mapped[str] = status_col("pending")  # pending | accepted | rejected
    review_note: Mapped[str | None] = mapped_column(UnicodeText)


class EvidenceLink(Base, EntityBase):
    __tablename__ = "evidence_links"
    __table_args__ = (Index("ix_evidence_links_entity", "entity_type", "entity_id"),)

    evidence_asset_id: Mapped[uuid.UUID] = fk("evidence_assets", nullable=False)
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(Unicode(60), nullable=False)  # e.g. practice_photo, measurement_record, attendance_sheet
    review_status: Mapped[str] = status_col("pending")
