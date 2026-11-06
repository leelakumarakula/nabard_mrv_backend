"""Automatic daily pulls, failed-pull recording, source freshness and the FarmFuture token expiry."""

import base64
import json
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models import IntegrationSource, SyncRun
from app.services.integration.platform_adapters import IST
from app.services.integration.scheduler import due_sources, run_due
from app.services.integration.sync import token_expiry


def _token(expires: datetime) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': int(expires.timestamp()), 'sub': 'x'})}.signature"


def test_token_expiry_is_read_without_exposing_the_token() -> None:
    soon = token_expiry(_token(datetime.now(UTC) + timedelta(days=3)))
    assert soon["configured"] is True and 2.9 < soon["days_left"] < 3.1
    assert token_expiry(None) == {"configured": False, "expires_at": None, "days_left": None}
    assert token_expiry("not-a-jwt")["days_left"] is None


def _source(api, code: str, schedule: str | None) -> None:
    config = {"programme_code": "CCRL-INCLUSIVE", **({"schedule_daily_at": schedule} if schedule else {})}
    r = api.as_("admin").post("/integrations/sources", json={
        "source_code": code, "name": code, "module_code": "climate", "source_type": "rest_pull",
        "adapter_code": "platform_rainfall_status", "is_mock": False, "config": config})
    assert r.status_code == 201, r.text


def test_failed_pull_is_recorded_and_scheduler_runs_due_sources(api, db, monkeypatch) -> None:
    _source(api, "t_sched_due", "06:00")
    _source(api, "t_sched_later", "23:59")
    _source(api, "t_sched_manual", None)
    # a manual pull that fails (no plot has climate normals) leaves a failed run with its reason
    r = api.as_("admin").post("/integrations/t_sched_manual/pull")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_climate_normals"
    manual = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == "t_sched_manual"))
    failed = db.scalar(select(SyncRun).where(SyncRun.source_id == manual))
    assert failed.status == "failed" and "climate normals" in failed.error_summary and failed.trigger == "pull"

    now = datetime.combine(datetime.now(IST).date(), time(7, 0), IST)
    due = [s.source_code for s, _ in due_sources(db, now) if s.source_code.startswith("t_sched")]
    assert due == ["t_sched_due"]  # not before its time, never for a source without a schedule
    assert [line for line in run_due(db, now, dry_run=True) if line["source_code"] == "t_sched_due"][0]["status"] == "due"

    results = [line for line in run_due(db, now) if line["source_code"] == "t_sched_due"]
    assert results[0]["status"] == "failed" and "climate normals" in results[0]["error"]  # recorded, others continue
    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == "t_sched_due"))
    run = db.scalar(select(SyncRun).where(SyncRun.source_id == source_id))
    assert run.trigger == "schedule" and run.status == "failed"
    # a failed run is retried on the next check; a successful one would end the day's schedule
    assert "t_sched_due" in [s.source_code for s, _ in due_sources(db, now)]

    monkeypatch.setattr(get_settings(), "farmfuture_api_token", _token(datetime.now(UTC) + timedelta(days=2)))
    health = api.as_("analyst").get("/integrations/health").json()
    states = {s["source_code"]: s for s in health["sources"]}
    assert states["t_sched_due"]["state"] == "failed" and "climate normals" in states["t_sched_due"]["last_error"]
    assert states["t_sched_later"]["state"] == "never_run" and states["t_sched_later"]["schedule_daily_at"] == "23:59"
    assert 1.9 < health["farmfuture_token"]["days_left"] < 2.1
    assert set(health["farmfuture_token"]) == {"configured", "expires_at", "days_left"}  # the token itself is never returned
    assert api.as_("farmer").get("/integrations/health").status_code == 403
