import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Table, Unicode, UnicodeText, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import DemoMixin, EntityBase, IdMixin, TimestampMixin, fk, status_col, utcnow

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Uuid, ForeignKey("roles.id"), primary_key=True),
)


class Role(Base, IdMixin, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Unicode(500))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=false())

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("roles.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(Unicode(80), primary_key=True)

    role: Mapped[Role] = relationship(back_populates="permissions")


class User(Base, EntityBase, DemoMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    status: Mapped[str] = status_col("active")  # active | inactive | locked
    phone: Mapped[str | None] = mapped_column(Unicode(30))
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")
    farmer_id: Mapped[uuid.UUID | None] = fk("farmers")  # set for Farmer-role users
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")
    scopes: Mapped[list["UserScope"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class UserScope(Base, IdMixin, TimestampMixin):
    """Grants a user access to a geography subtree and/or an organisation (e.g. their FPO)."""

    __tablename__ = "user_scopes"

    user_id: Mapped[uuid.UUID] = fk("users", nullable=False)
    programme_id: Mapped[uuid.UUID | None] = fk("programmes")
    geo_unit_id: Mapped[uuid.UUID | None] = fk("geo_units")
    organisation_id: Mapped[uuid.UUID | None] = fk("organisations")

    user: Mapped[User] = relationship(back_populates="scopes")


class PasswordResetToken(Base, IdMixin):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = fk("users", nullable=False)
    token_hash: Mapped[str] = mapped_column(Unicode(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(Unicode(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
