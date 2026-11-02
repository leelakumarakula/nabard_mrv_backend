import uuid
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Ref(ORMModel):
    """Lightweight reference to a related record for display."""

    id: uuid.UUID
    label: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)) -> None:
        self.page = page
        self.page_size = page_size


def paginate(db: Session, stmt: Select, params: PageParams, unique: bool = True) -> tuple[list, int]:
    """Run a paged query. stmt must have an ORDER BY (required by SQL Server OFFSET/FETCH)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    result = db.scalars(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size))
    items = list(result.unique() if unique else result)
    return items, total


def page_of(items: list, total: int, params: PageParams, schema: type[BaseModel]) -> dict:
    return {
        "items": [schema.model_validate(i) for i in items],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
    }


class Message(BaseModel):
    message: str
