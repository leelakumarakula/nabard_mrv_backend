from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import AppError
from app.core.security import create_access_token, generate_token, hash_password, hash_token, verify_password
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import PasswordResetToken, User
from app.schemas.common import Message
from app.schemas.identity import (
    ChangePasswordIn,
    ForgotPasswordIn,
    ForgotPasswordOut,
    MeOut,
    ProfileUpdate,
    ResetPasswordIn,
    TokenOut,
    UserOut,
)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def me_out(current: CurrentUser) -> MeOut:
    base = UserOut.from_user(current.user).model_dump()
    return MeOut(**base, permissions=sorted(current.permissions), all_scope=current.all_scope)


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenOut:
    email = form.username.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(form.password, user.password_hash):
        audit.record(db, None, "login_failed", "user", user.id if user else None, summary=f"Failed login for {email}")
        db.commit()
        raise AppError(401, "invalid_credentials", "Invalid email or password")
    if user.status != "active":
        raise AppError(403, "account_inactive", "This account is not active. Contact your administrator.")
    user.last_login_at = utcnow()
    audit.record(db, None, "login", "user", user.id, summary=f"{email} signed in")
    db.commit()
    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in=expires_in)


@router.post("/forgot-password", response_model=ForgotPasswordOut)
def forgot_password(body: ForgotPasswordIn, db: Session = Depends(get_db)) -> ForgotPasswordOut:
    settings = get_settings()
    message = "If the account exists, password reset instructions have been issued."
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or user.status != "active":
        return ForgotPasswordOut(message=message)
    token = generate_token()
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_expire_minutes),
    ))
    audit.record(db, None, "password_reset_requested", "user", user.id)
    db.commit()
    # No email/SMS delivery service is configured yet; outside production the token is returned for testing.
    return ForgotPasswordOut(message=message, dev_reset_token=None if settings.is_production else token)


@router.post("/reset-password", response_model=Message)
def reset_password(body: ResetPasswordIn, db: Session = Depends(get_db)) -> Message:
    record = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token)))
    now = datetime.now(UTC)
    if record is None or record.used_at is not None or record.expires_at < now:
        raise AppError(400, "invalid_token", "The reset link is invalid or has expired")
    user = db.get(User, record.user_id)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    record.used_at = now
    audit.record(db, None, "password_reset", "user", user.id)
    db.commit()
    return Message(message="Password has been reset. You can now sign in.")


@router.get("/me", response_model=MeOut)
def me(current: CurrentUser = Depends(get_current_user)) -> MeOut:
    return me_out(current)


@router.patch("/me", response_model=MeOut)
def update_profile(body: ProfileUpdate, current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> MeOut:
    user = db.merge(current.user)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    audit.record(db, current, "update_profile", "user", user.id, after=body.model_dump(exclude_unset=True))
    db.commit()
    current.user = user
    return me_out(current)


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordIn, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Message:
    user = db.merge(current.user)
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "invalid_password", "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    audit.record(db, current, "change_password", "user", user.id)
    db.commit()
    return Message(message="Password changed")
