import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.core.deps import CurrentUser
from app.models import AuditEvent

_SKIP = {"password_hash", "inbound_key_hash", "token_hash", "created_at", "updated_at", "created_by_id", "updated_by_id"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def snapshot(obj: Any, fields: list[str] | None = None) -> dict[str, Any]:
    """Column values of an ORM object, minus secrets, as JSON-safe values."""
    columns = [c.key for c in inspect(obj).mapper.column_attrs]
    keys = [k for k in (fields or columns) if k not in _SKIP]
    return {k: _json_safe(getattr(obj, k, None)) for k in keys}


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after[k] for k in changed}


def record(
    db: Session,
    actor: CurrentUser | None,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    summary: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary,
        before=_json_safe(before) if before else None,
        after=_json_safe(after) if after else None,
        correlation_id=request_id_var.get(),
    )
    db.add(event)
    return event


def stamp(obj: Any, actor: CurrentUser, created: bool = False) -> None:
    if created:
        obj.created_by_id = actor.id
    obj.updated_by_id = actor.id
