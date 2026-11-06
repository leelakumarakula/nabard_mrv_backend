"""Daily automatic pulls.

A pull source runs automatically when its config has ``schedule_daily_at`` ("HH:MM", India time). Run due sources with

    python -m app.services.integration.scheduler            (e.g. every 15 minutes from Windows Task Scheduler / cron)
    python -m app.services.integration.scheduler --dry-run  (list what is due, run nothing)

or set SCHEDULER_ENABLED=true for a single-process deployment to check every few minutes inside the API. A source is due
once its time has passed today and it has not had a successful or partial pull since then (a failed run is retried on
the next check). Sources due at the same time run in source-code order; give dependent sources a later time (e.g. the
rainfall status calculation after the weather source). Runs are recorded with trigger "schedule" and the scheduler
account (MRV_SCHEDULER_EMAIL, default: the first active super administrator).
"""

import argparse
import logging
import os
import re
import threading
from datetime import datetime, time

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, build_current_user
from app.core.errors import AppError
from app.models import IntegrationSource, SyncRun, User
from app.services.integration.platform_adapters import IST
from app.services.integration.sync import run_pull

log = logging.getLogger("mrv.scheduler")
TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def scheduled_time(source: IntegrationSource, now: datetime) -> datetime | None:
    match = TIME.match(str((source.config or {}).get("schedule_daily_at") or ""))
    if not match:
        return None
    return datetime.combine(now.astimezone(IST).date(), time(int(match[1]), int(match[2])), IST)


def due_sources(db: Session, now: datetime | None = None) -> list[tuple[IntegrationSource, datetime]]:
    now = now or datetime.now(IST)
    due = []
    for source in db.scalars(select(IntegrationSource).where(IntegrationSource.enabled == true(),
                                                              IntegrationSource.source_type == "rest_pull")):
        at = scheduled_time(source, now)
        if at is None or now < at:
            continue
        done = db.scalar(select(SyncRun.id).where(
            SyncRun.source_id == source.id, SyncRun.trigger.in_(("pull", "schedule")),
            SyncRun.status.in_(("succeeded", "partial")), SyncRun.started_at >= at).limit(1))
        if done is None:
            due.append((source, at))
    return sorted(due, key=lambda item: (item[1], item[0].source_code))


def scheduler_user(db: Session) -> CurrentUser:
    email = os.environ.get("MRV_SCHEDULER_EMAIL")
    users = db.scalars(select(User).where(User.email == email)) if email else db.scalars(select(User).order_by(User.created_at))
    for user in users:
        if user.status == "active" and (email or "super_admin" in {r.code for r in user.roles}):
            return build_current_user(db, user)
    raise AppError(422, "no_scheduler_user", "No active scheduler account: set MRV_SCHEDULER_EMAIL to an active user with "
                                             "integration.manage, or create a super administrator")


def run_due(db: Session, now: datetime | None = None, dry_run: bool = False) -> list[dict]:
    """Run every due source once; returns one line per source (a failure never stops the others)."""
    results = []
    due = due_sources(db, now)
    actor = scheduler_user(db) if due and not dry_run else None
    for source, at in due:
        line = {"source_code": source.source_code, "scheduled_at": at.isoformat()}
        if dry_run:
            results.append({**line, "status": "due"})
            continue
        try:
            run, _ = run_pull(db, source, actor, trigger="schedule")
            line.update(status=run.status, accepted=run.records_accepted, rejected=run.records_rejected)
        except AppError as exc:
            line.update(status="failed", error=exc.message)
        log.info("scheduled pull %s", line)
        results.append(line)
    return results


def start_background(interval_seconds: int = 300) -> threading.Thread:
    """In-process runner for single-process deployments (SCHEDULER_ENABLED=true). Use the command instead when the API
    runs with several workers, so a source is not pulled twice."""
    from app.db.session import SessionLocal

    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_seconds):
            with SessionLocal() as db:
                try:
                    run_due(db)
                except Exception:  # noqa: BLE001 – keep the runner alive; the error is logged
                    log.exception("scheduler check failed")

    thread = threading.Thread(target=loop, name="mrv-scheduler", daemon=True)
    thread.stop = stop  # type: ignore[attr-defined]
    thread.start()
    return thread


def main() -> None:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser(description="Run the pull sources whose daily time has passed")
    parser.add_argument("--dry-run", action="store_true", help="list the due sources without running them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        results = run_due(db, dry_run=args.dry_run)
    for line in results:
        print(line)
    if not results:
        print("No source is due.")


if __name__ == "__main__":
    main()
