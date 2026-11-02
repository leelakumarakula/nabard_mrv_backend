import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Unicode, UnicodeText, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import IdMixin, utcnow


class AuditEvent(Base, IdMixin):
    """Append-only event history. The application never updates or deletes rows in this table."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_entity", "entity_type", "entity_id"),)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_email: Mapped[str | None] = mapped_column(Unicode(255))
    action: Mapped[str] = mapped_column(Unicode(60), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(Unicode(60), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Unicode(64))
    summary: Mapped[str | None] = mapped_column(UnicodeText)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(Unicode(64))
