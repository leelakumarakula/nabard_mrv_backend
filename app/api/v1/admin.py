import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, NotFound
from app.core.permissions import PERMISSIONS
from app.core.security import hash_password
from app.db.session import get_db
from app.models import AuditEvent, Role, RolePermission, SystemSetting, User, UserScope
from app.schemas.common import Message, Page, PageParams, paginate
from app.schemas.identity import (
    AdminSetPasswordIn,
    AuditEventOut,
    RoleOut,
    RolePermissionsIn,
    ScopeIn,
    SettingOut,
    SettingUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit
from app.services.crud import get_or_404

router = APIRouter(prefix="/admin", tags=["administration"])


def _roles_by_code(db: Session, codes: list[str]) -> list[Role]:
    roles = list(db.scalars(select(Role).where(Role.code.in_(codes))))
    missing = set(codes) - {r.code for r in roles}
    if missing:
        raise AppError(422, "unknown_role", f"Unknown role code(s): {', '.join(sorted(missing))}")
    return roles


def _scopes(scopes: list[ScopeIn]) -> list[UserScope]:
    result = []
    for s in scopes:
        if not (s.geo_unit_id or s.organisation_id):
            raise AppError(422, "invalid_scope", "Each scope needs a geography or an organisation")
        result.append(UserScope(geo_unit_id=s.geo_unit_id, organisation_id=s.organisation_id, programme_id=s.programme_id))
    return result


def _guard_super_admin_grant(current: CurrentUser, codes: list[str]) -> None:
    if "super_admin" in codes and "super_admin" not in current.role_codes:
        raise AppError(403, "forbidden", "Only a Platform Super Admin can grant the Super Admin role")


def _guard_super_admin_account(current: CurrentUser, user: User) -> None:
    """Super Admin accounts can be changed (status, roles, password) only by another Super Admin."""
    if any(r.code == "super_admin" for r in user.roles) and "super_admin" not in current.role_codes:
        raise AppError(403, "forbidden", "Only a Platform Super Admin can change a Super Admin account")


@router.get("/users", response_model=Page[UserOut])
def list_users(params: PageParams = Depends(), search: str | None = None, role: str | None = None,
               status: str | None = None, current: CurrentUser = Depends(require("admin.users")),
               db: Session = Depends(get_db)) -> dict:
    stmt = select(User).order_by(User.full_name)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if role:
        stmt = stmt.where(User.roles.any(Role.code == role))
    if status:
        stmt = stmt.where(User.status == status)
    items, total = paginate(db, stmt, params)
    return {"items": [UserOut.from_user(u) for u in items], "total": total, "page": params.page, "page_size": params.page_size}


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, current: CurrentUser = Depends(require("admin.users")),
                db: Session = Depends(get_db)) -> UserOut:
    _guard_super_admin_grant(current, body.role_codes)
    user = User(
        email=body.email.lower(), full_name=body.full_name, phone=body.phone,
        password_hash=hash_password(body.password), organisation_id=body.organisation_id, farmer_id=body.farmer_id,
        must_change_password=True, roles=_roles_by_code(db, body.role_codes), scopes=_scopes(body.scopes),
    )
    audit.stamp(user, current, created=True)
    db.add(user)
    db.flush()
    audit.record(db, current, "create", "user", user.id, summary=f"Created user {user.email}",
                 after={"email": user.email, "roles": body.role_codes, "scopes": [s.model_dump() for s in body.scopes]})
    db.commit()
    return UserOut.from_user(user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, current: CurrentUser = Depends(require("admin.users")),
             db: Session = Depends(get_db)) -> UserOut:
    return UserOut.from_user(get_or_404(db, User, user_id, "User"))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, current: CurrentUser = Depends(require("admin.users")),
                db: Session = Depends(get_db)) -> UserOut:
    user = get_or_404(db, User, user_id, "User")
    _guard_super_admin_account(current, user)
    data = body.model_dump(exclude_unset=True)
    before = {"status": user.status, "roles": [r.code for r in user.roles], "scopes": len(user.scopes)}
    if "role_codes" in data:
        _guard_super_admin_grant(current, data["role_codes"])
        if user.id == current.id and "super_admin" in current.role_codes and "super_admin" not in data["role_codes"]:
            raise AppError(422, "self_lockout", "You cannot remove your own Super Admin role")
        user.roles = _roles_by_code(db, data.pop("role_codes"))
    if "scopes" in data:
        data.pop("scopes")
        user.scopes = _scopes(body.scopes or [])
    if user.id == current.id and data.get("status") not in (None, "active"):
        raise AppError(422, "self_lockout", "You cannot deactivate your own account")
    for key, value in data.items():
        setattr(user, key, value)
    audit.stamp(user, current)
    audit.record(db, current, "update", "user", user.id, before=before,
                 after={"status": user.status, "roles": [r.code for r in user.roles], "scopes": len(user.scopes)})
    db.commit()
    return UserOut.from_user(user)


@router.post("/users/{user_id}/set-password", response_model=Message)
def set_password(user_id: uuid.UUID, body: AdminSetPasswordIn, current: CurrentUser = Depends(require("admin.users")),
                 db: Session = Depends(get_db)) -> Message:
    user = get_or_404(db, User, user_id, "User")
    _guard_super_admin_account(current, user)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    audit.record(db, current, "admin_set_password", "user", user.id)
    db.commit()
    return Message(message="Temporary password set; the user must change it at next sign-in")


@router.get("/permissions")
def list_permissions(current: CurrentUser = Depends(require("admin.roles"))) -> list[dict[str, str]]:
    return [{"code": code, "description": desc} for code, desc in PERMISSIONS.items()]


@router.get("/roles", response_model=list[RoleOut])
def list_roles(current: CurrentUser = Depends(require("admin.users")), db: Session = Depends(get_db)) -> list[RoleOut]:
    return [RoleOut.from_role(r) for r in db.scalars(select(Role).order_by(Role.name))]


@router.put("/roles/{role_id}/permissions", response_model=RoleOut)
def set_role_permissions(role_id: uuid.UUID, body: RolePermissionsIn, current: CurrentUser = Depends(require("admin.roles")),
                         db: Session = Depends(get_db)) -> RoleOut:
    role = get_or_404(db, Role, role_id, "Role")
    unknown = set(body.permissions) - set(PERMISSIONS)
    if unknown:
        raise AppError(422, "unknown_permission", f"Unknown permission(s): {', '.join(sorted(unknown))}")
    if role.code == "super_admin" and not {"admin.roles", "admin.users"} <= set(body.permissions):
        raise AppError(422, "self_lockout", "Super Admin must keep admin.roles and admin.users")
    before = sorted(p.permission_code for p in role.permissions)
    role.permissions = [RolePermission(permission_code=p) for p in sorted(set(body.permissions))]
    audit.record(db, current, "update_permissions", "role", role.id, summary=f"Permissions for {role.code}",
                 before={"permissions": before}, after={"permissions": sorted(set(body.permissions))})
    db.commit()
    db.refresh(role)
    return RoleOut.from_role(role)


@router.get("/audit-events", response_model=Page[AuditEventOut])
def list_audit_events(params: PageParams = Depends(), entity_type: str | None = None, entity_id: str | None = None,
                      action: str | None = None, actor_id: uuid.UUID | None = None,
                      date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                      current: CurrentUser = Depends(require("admin.audit")), db: Session = Depends(get_db)) -> dict:
    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.desc())
    for column, value in ((AuditEvent.entity_type, entity_type), (AuditEvent.entity_id, entity_id),
                          (AuditEvent.action, action), (AuditEvent.actor_id, actor_id)):
        if value:
            stmt = stmt.where(column == value)
    if date_from:
        stmt = stmt.where(AuditEvent.occurred_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditEvent.occurred_at <= date_to)
    items, total = paginate(db, stmt, params, unique=False)
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/settings", response_model=list[SettingOut])
def list_settings(current: CurrentUser = Depends(require("admin.settings")), db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(SystemSetting).order_by(SystemSetting.key)))


@router.put("/settings/{key}", response_model=SettingOut)
def update_setting(key: str, body: SettingUpdate, current: CurrentUser = Depends(require("admin.settings")),
                   db: Session = Depends(get_db)) -> SystemSetting:
    setting = db.get(SystemSetting, key)
    if setting is None:
        raise NotFound("Setting")
    before = {"value": setting.value}
    setting.value = body.value
    if body.requires_confirmation is not None:
        setting.requires_confirmation = body.requires_confirmation
    setting.updated_by_id = current.id
    audit.record(db, current, "update", "system_setting", key, before=before, after={"value": body.value})
    db.commit()
    return setting
