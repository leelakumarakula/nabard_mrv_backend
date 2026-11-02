import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Unicode, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def fk(table: str, nullable: bool = True, index: bool = True) -> Any:
    """Foreign key to <table>.id (no cascades: SQL Server rejects multiple cascade paths)."""
    return mapped_column(Uuid, ForeignKey(f"{table}.id"), nullable=nullable, index=index)


def status_col(default: str, index: bool = True) -> Any:
    return mapped_column(Unicode(40), nullable=False, default=default, server_default=default, index=index)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ActorMixin:
    # Actor references are not FK-constrained (users ↔ organisations would form cycles);
    # the audit_events table carries the authoritative actor history.
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class DemoMixin:
    """Marks synthetic demonstration data so the UI can label it DEMO/SYNTHETIC."""

    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class ArchiveMixin:
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class EntityBase(IdMixin, TimestampMixin, ActorMixin):
    """Common columns for most business entities."""
