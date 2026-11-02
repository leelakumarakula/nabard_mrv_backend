from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.errors import NotFound
from app.services import audit

M = TypeVar("M")


def get_or_404(db: Session, model: type[M], record_id: Any, name: str | None = None) -> M:
    obj = db.get(model, record_id)
    if obj is None:
        raise NotFound(name or model.__name__)
    return obj


def create(db: Session, current: CurrentUser, model: type[M], data: dict[str, Any], entity_type: str,
           summary: str | None = None) -> M:
    obj = model(**data)
    audit.stamp(obj, current, created=True)
    db.add(obj)
    db.flush()
    audit.record(db, current, "create", entity_type, obj.id, summary=summary, after=audit.snapshot(obj))
    return obj


def update(db: Session, current: CurrentUser, obj: Any, changes: BaseModel | dict[str, Any], entity_type: str,
           action: str = "update", summary: str | None = None) -> Any:
    values = changes.model_dump(exclude_unset=True) if isinstance(changes, BaseModel) else changes
    before = audit.snapshot(obj)
    for key, value in values.items():
        setattr(obj, key, value)
    audit.stamp(obj, current)
    db.flush()
    changed_before, changed_after = audit.diff(before, audit.snapshot(obj))
    if changed_after:
        audit.record(db, current, action, entity_type, obj.id, summary=summary, before=changed_before, after=changed_after)
    return obj
