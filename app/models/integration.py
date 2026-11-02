import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, Unicode, UnicodeText, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import EntityBase, IdMixin, fk, status_col, utcnow


class IntegrationSource(Base, EntityBase):
    """An external system the MRV platform exchanges data with. Secrets are never stored here."""

    __tablename__ = "integration_sources"

    source_code: Mapped[str] = mapped_column(Unicode(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    module_code: Mapped[str | None] = mapped_column(Unicode(30))
    source_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # rest_push | rest_pull | file_exchange
    adapter_code: Mapped[str] = mapped_column(Unicode(60), nullable=False)  # adapter registry key
    base_url_env_var: Mapped[str | None] = mapped_column(Unicode(100))  # name of env var holding the URL
    auth_type: Mapped[str] = mapped_column(Unicode(30), nullable=False, default="api_key")
    owner: Mapped[str | None] = mapped_column(Unicode(200))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    availability_note: Mapped[str | None] = mapped_column(UnicodeText)
    inbound_key_hash: Mapped[str | None] = mapped_column(Unicode(64))
    inbound_key_hint: Mapped[str | None] = mapped_column(Unicode(12))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IntegrationFieldMapping(Base, EntityBase):
    __tablename__ = "integration_field_mappings"
    __table_args__ = (UniqueConstraint("source_id", "payload_kind", "source_field"),)

    source_id: Mapped[uuid.UUID] = fk("integration_sources", nullable=False)
    payload_kind: Mapped[str] = mapped_column(Unicode(30), nullable=False)  # measurements | observations | advisories
    source_field: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    target_field: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    value_map: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # optional code translation, e.g. {"L": "litre"}
    notes: Mapped[str | None] = mapped_column(UnicodeText)


class SyncRun(Base, IdMixin):
    __tablename__ = "sync_runs"

    source_id: Mapped[uuid.UUID] = fk("integration_sources", nullable=False)
    payload_kind: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    trigger: Mapped[str] = mapped_column(Unicode(20), nullable=False)  # push | pull | replay | test
    status: Mapped[str] = status_col("running")  # running | succeeded | partial | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[str | None] = mapped_column(Unicode(255))
    error_summary: Mapped[str | None] = mapped_column(UnicodeText)
    correlation_id: Mapped[str | None] = mapped_column(Unicode(64))
    replay_of_id: Mapped[uuid.UUID | None] = fk("sync_runs")
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class QuarantinedRecord(Base, IdMixin):
    """Invalid or ambiguous inbound record, kept with a readable error instead of being discarded."""

    __tablename__ = "quarantined_records"

    sync_run_id: Mapped[uuid.UUID] = fk("sync_runs", nullable=False)
    source_id: Mapped[uuid.UUID] = fk("integration_sources", nullable=False)
    payload_kind: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(Unicode(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = status_col("open")  # open | replayed | discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(UnicodeText)


class SourceRecordMap(Base, IdMixin):
    """Maps a source system's record ID to the internal record; provides idempotency for resubmissions."""

    __tablename__ = "source_record_maps"
    __table_args__ = (UniqueConstraint("source_id", "entity_type", "source_record_id"),)

    source_id: Mapped[uuid.UUID] = fk("integration_sources", nullable=False)
    entity_type: Mapped[str] = mapped_column(Unicode(40), nullable=False)
    source_record_id: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    internal_record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    checksum: Mapped[str | None] = mapped_column(Unicode(64))
    # Identity maps (farmer/plot/device) are created by a reviewer; record maps are created by the pipeline.
    mapped_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    review_note: Mapped[str | None] = mapped_column(UnicodeText)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
