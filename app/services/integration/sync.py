"""Running a pull synchronisation – shared by the "Run pull" button and the scheduler.

Every attempt leaves a sync run: a pull that fails before any record is processed (expired token, source unreachable,
missing configuration) is recorded as a failed run with the reason, so a silent failure cannot hide in the history.
"""

import base64
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.db.mixins import utcnow
from app.models import IntegrationSource, SyncRun
from app.services.integration.adapters import ADAPTERS, get_adapter
from app.services.integration.base import TransientSourceError, with_retry
from app.services.integration.pipeline import process_batch


def run_pull(db: Session, source: IntegrationSource, current: CurrentUser, trigger: str = "pull") -> tuple[SyncRun, list]:
    if not source.enabled:
        raise AppError(403, "source_disabled", f"Source '{source.source_code}' is disabled")
    config = source.config or {}
    payload_kind = getattr(ADAPTERS.get(source.adapter_code), "payload_kind", "observations")
    records: list[dict[str, Any]] = []
    pages = 0
    try:
        adapter = get_adapter(db, source)
        payload_kind = adapter.payload_kind
        cursor = config.get("cursor")
        while True:
            page = with_retry(lambda c=cursor: adapter.fetch_page(c), attempts=int(config.get("retries", 3)), base_delay=0.05)
            records.extend(page.records)
            pages += 1
            if page.next_cursor is None or pages >= int(config.get("max_pages", 50)):
                break
            cursor = page.next_cursor
    except (AppError, TransientSourceError) as exc:
        db.rollback()
        reason = exc.message if isinstance(exc, AppError) else f"Source unavailable after retries: {exc}"
        db.add(SyncRun(source_id=source.id, payload_kind=payload_kind, trigger=trigger, status="failed",
                       finished_at=utcnow(), error_summary=reason[:2000], triggered_by_id=current.id))
        db.commit()
        if isinstance(exc, TransientSourceError):
            raise AppError(502, "source_unavailable", reason) from exc
        raise
    run, outcomes = process_batch(db, source, payload_kind, records, trigger, current)
    run.cursor = f"pages={pages}"
    db.commit()
    return run, outcomes


def token_expiry(token: str | None) -> dict[str, Any]:
    """Expiry of a JWT bearer token, read from its payload without verifying it (the token itself is never returned)."""
    if not token:
        return {"configured": False, "expires_at": None, "days_left": None}
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expires = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (IndexError, KeyError, ValueError, TypeError):
        return {"configured": True, "expires_at": None, "days_left": None}
    return {"configured": True, "expires_at": expires,
            "days_left": round((expires - datetime.now(UTC)).total_seconds() / 86400, 1)}


def farmfuture_token() -> dict[str, Any]:
    return token_expiry(get_settings().farmfuture_api_token)
