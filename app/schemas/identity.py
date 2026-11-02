import uuid
from datetime import datetime
from typing import Any, Literal

import email_validator
from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel, Ref

# Allow organisation-internal ".local" domains (common in on-premise directories, and used by the demo accounts).
if "local" in email_validator.SPECIAL_USE_DOMAIN_NAMES:
    email_validator.SPECIAL_USE_DOMAIN_NAMES.remove("local")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ForgotPasswordOut(BaseModel):
    message: str
    # Only returned outside production while no email/SMS delivery service is configured.
    dev_reset_token: str | None = None


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=30)


class RoleOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    permissions: list[str] = []

    @classmethod
    def from_role(cls, role: Any) -> "RoleOut":
        return cls(id=role.id, code=role.code, name=role.name, description=role.description,
                   permissions=sorted(p.permission_code for p in role.permissions))


class RolePermissionsIn(BaseModel):
    permissions: list[str]


class ScopeIn(BaseModel):
    geo_unit_id: uuid.UUID | None = None
    organisation_id: uuid.UUID | None = None
    programme_id: uuid.UUID | None = None


class ScopeOut(ORMModel):
    id: uuid.UUID
    geo_unit_id: uuid.UUID | None
    organisation_id: uuid.UUID | None
    programme_id: uuid.UUID | None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    status: str
    organisation_id: uuid.UUID | None
    farmer_id: uuid.UUID | None
    last_login_at: datetime | None
    must_change_password: bool
    is_demo: bool
    roles: list[Ref] = []
    role_codes: list[str] = []
    scopes: list[ScopeOut] = []

    @classmethod
    def from_user(cls, user: Any) -> "UserOut":
        return cls(
            id=user.id, email=user.email, full_name=user.full_name, phone=user.phone, status=user.status,
            organisation_id=user.organisation_id, farmer_id=user.farmer_id, last_login_at=user.last_login_at,
            must_change_password=user.must_change_password, is_demo=user.is_demo,
            roles=[Ref(id=r.id, label=r.name) for r in user.roles], role_codes=[r.code for r in user.roles],
            scopes=[ScopeOut.model_validate(s) for s in user.scopes],
        )


class MeOut(UserOut):
    permissions: list[str]
    all_scope: bool


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    password: str = Field(min_length=10, max_length=128)
    role_codes: list[str] = Field(min_length=1)
    organisation_id: uuid.UUID | None = None
    farmer_id: uuid.UUID | None = None
    scopes: list[ScopeIn] = []


class UserUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    status: Literal["active", "inactive", "locked"] | None = None
    role_codes: list[str] | None = None
    organisation_id: uuid.UUID | None = None
    farmer_id: uuid.UUID | None = None
    scopes: list[ScopeIn] | None = None


class AdminSetPasswordIn(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)


class AuditEventOut(ORMModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None
    action: str
    entity_type: str
    entity_id: str | None
    summary: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str | None


class SettingOut(ORMModel):
    key: str
    value: Any
    description: str | None
    requires_confirmation: bool
    updated_at: datetime


class SettingUpdate(BaseModel):
    value: Any
    requires_confirmation: bool | None = None
