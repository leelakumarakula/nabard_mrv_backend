import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden
from app.core.permissions import SCOPE_ALL
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import GeoUnit, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


@dataclass
class CurrentUser:
    user: User
    permissions: set[str]
    role_codes: set[str]
    all_scope: bool
    geo_ids: set[uuid.UUID] = field(default_factory=set)  # assigned geo units plus all descendants
    org_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def email(self) -> str:
        return self.user.email

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden(f"Missing permission: {permission}")

    def can_access(self, geo_unit_id: uuid.UUID | None, organisation_id: uuid.UUID | None = None) -> bool:
        if self.all_scope:
            return True
        return (geo_unit_id is not None and geo_unit_id in self.geo_ids) or (
            organisation_id is not None and organisation_id in self.org_ids
        )

    def ensure_access(self, geo_unit_id: uuid.UUID | None, organisation_id: uuid.UUID | None = None) -> None:
        if not self.can_access(geo_unit_id, organisation_id):
            raise Forbidden("This record is outside your assigned geography or organisation")


def descendant_geo_ids(db: Session, roots: set[uuid.UUID]) -> set[uuid.UUID]:
    if not roots:
        return set()
    children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for gid, parent in db.execute(select(GeoUnit.id, GeoUnit.parent_id)):
        children.setdefault(parent, []).append(gid)
    result, stack = set(), list(roots)
    while stack:
        gid = stack.pop()
        if gid in result:
            continue
        result.add(gid)
        stack.extend(children.get(gid, []))
    return result


def build_current_user(db: Session, user: User) -> CurrentUser:
    permissions = {rp.permission_code for role in user.roles for rp in role.permissions}
    all_scope = SCOPE_ALL in permissions
    geo_roots = {s.geo_unit_id for s in user.scopes if s.geo_unit_id}
    org_ids = {s.organisation_id for s in user.scopes if s.organisation_id}
    return CurrentUser(
        user=user,
        permissions=permissions,
        role_codes={r.code for r in user.roles},
        all_scope=all_scope,
        geo_ids=set() if all_scope else descendant_geo_ids(db, geo_roots),
        org_ids=org_ids,
    )


def get_current_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> CurrentUser:
    unauthorized = AppError(401, "unauthorized", "Not authenticated")
    if not token:
        raise unauthorized
    user_id = decode_access_token(token)
    user = db.get(User, user_id) if user_id else None
    if user is None or user.status != "active":
        raise unauthorized
    return build_current_user(db, user)


def require(*permissions: str) -> Callable[..., CurrentUser]:
    """Dependency: the current user must hold every listed permission."""

    def dependency(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        for permission in permissions:
            current.require(permission)
        return current

    return dependency
