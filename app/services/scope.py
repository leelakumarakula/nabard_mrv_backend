from typing import Any

from sqlalchemy import ColumnElement, Select, false, or_

from app.core.deps import CurrentUser


def apply_scope(stmt: Select, current: CurrentUser, geo_col: Any = None, org_col: Any = None) -> Select:
    """Restrict a query to the user's geography subtree and/or organisations."""
    if current.all_scope:
        return stmt
    conditions = []
    if geo_col is not None and current.geo_ids:
        conditions.append(geo_col.in_(current.geo_ids))
    if org_col is not None and current.org_ids:
        conditions.append(org_col.in_(current.org_ids))
    return stmt.where(or_(*conditions)) if conditions else stmt.where(false())


def eq_or_null(column: Any, value: Any) -> ColumnElement[bool]:
    """column = value, or column IS NULL when value is None (scope keys of results and reports)."""
    return column == value if value is not None else column.is_(None)
