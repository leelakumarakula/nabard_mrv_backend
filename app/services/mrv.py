import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, build_current_user, descendant_geo_ids
from app.core.errors import AppError, Forbidden, InvalidState
from app.db.mixins import utcnow
from app.models import (
    BaselineRecord,
    CalculationRun,
    CalculationRunInput,
    IndicatorDefinition,
    IndicatorResult,
    MonitoringRecord,
    ReviewComment,
    ReviewTask,
    User,
    VerificationDecision,
    VerificationFinding,
)
from app.services import audit
from app.services.calculations import METHODS, CalcContext, validate_parameters
from app.services.scope import eq_or_null
from app.services.settings import get_setting
from app.services.units import normalize_unit

REVIEWABLE: dict[str, type] = {
    "indicator_result": IndicatorResult,
    "baseline_record": BaselineRecord,
    "monitoring_record": MonitoringRecord,
}


def resolve_scope(db: Session, scope_type: str, geo_unit_id: uuid.UUID | None,
                  organisation_id: uuid.UUID | None) -> tuple[set[uuid.UUID] | None, uuid.UUID | None]:
    if scope_type == "programme":
        return None, None
    if scope_type == "fpo":
        if not organisation_id:
            raise AppError(422, "scope_required", "Select the FPO for an FPO-level calculation")
        return None, organisation_id
    if not geo_unit_id:
        raise AppError(422, "scope_required", f"Select the {scope_type} for a {scope_type}-level calculation")
    return descendant_geo_ids(db, {geo_unit_id}), None


def find_baseline(db: Session, indicator: IndicatorDefinition, programme_id: uuid.UUID, scope_type: str,
                  geo_unit_id: uuid.UUID | None, organisation_id: uuid.UUID | None) -> BaselineRecord | None:
    codes = select(IndicatorDefinition.id).where(IndicatorDefinition.code == indicator.code)
    stmt = select(BaselineRecord).where(
        BaselineRecord.indicator_id.in_(codes), BaselineRecord.programme_id == programme_id,
        BaselineRecord.scope_type == scope_type, BaselineRecord.validation_status == "valid",
        BaselineRecord.verification_status.in_(["unverified", "verified", "conditional"]))
    stmt = stmt.where(eq_or_null(BaselineRecord.geo_unit_id, geo_unit_id))
    if scope_type == "fpo":
        stmt = stmt.where(BaselineRecord.organisation_id == organisation_id)
    # Prefer verified baselines, then the most recent.
    candidates = list(db.scalars(stmt).unique())
    candidates.sort(key=lambda b: (b.verification_status == "verified", b.created_at), reverse=True)
    return candidates[0] if candidates else None


def preview_calculation(db: Session, current: CurrentUser, indicator: IndicatorDefinition, body: Any) -> dict[str, Any]:
    """Evaluate a definition for configuration and testing. Never stored, never a result, allowed for drafts."""
    method_code = body.method_code or indicator.method_code
    method = METHODS.get(method_code or "")
    if method is None:
        raise AppError(422, "unknown_method", "Configure a calculation method first")
    params = body.parameters if body.parameters is not None else (indicator.method_parameters or {})
    try:
        validate_parameters(method_code, params)
    except ValueError as exc:
        raise AppError(422, "invalid_method_parameters", str(exc)) from exc
    if body.period_end < body.period_start:
        raise AppError(422, "invalid_period", "Period end is before period start")
    if body.scope_type != "programme":
        current.ensure_access(body.scope_geo_unit_id, body.scope_organisation_id)
    elif not current.all_scope:
        raise Forbidden("Programme-wide previews need access to every geography")
    geo_ids, org_id = resolve_scope(db, body.scope_type, body.scope_geo_unit_id, body.scope_organisation_id)
    try:
        out = method.fn(CalcContext(db, indicator, body.programme_id, body.period_start, body.period_end, geo_ids, org_id, params))
    except ValueError as exc:
        raise AppError(422, "calculation_error", str(exc)) from exc
    return {"preview": True, "stored": False, "indicator_status": indicator.status,
            "note": "PREVIEW – not a calculated result; not stored, reviewed or verified.",
            "method": f"{method.code} v{method.version}", "parameters": params,
            "value": None if out.value is None else str(out.value), "unit": indicator.unit,
            "numerator": None if out.numerator is None else str(out.numerator),
            "denominator": None if out.denominator is None else str(out.denominator),
            "input_count": len(out.inputs), "details": out.details}


def run_calculation(db: Session, current: CurrentUser, indicator: IndicatorDefinition, programme_id: uuid.UUID,
                    period_start: date, period_end: date, reporting_period_id: uuid.UUID | None, scope_type: str,
                    scope_geo_unit_id: uuid.UUID | None, scope_organisation_id: uuid.UUID | None,
                    parameters: dict[str, Any] | None = None) -> tuple[CalculationRun, IndicatorResult | None]:
    if indicator.status == "retired":
        raise InvalidState("Retired indicators cannot be calculated")
    provisional = indicator.status != "approved"
    if provisional and not get_setting(db, "mrv.allow_provisional_calculations"):
        raise InvalidState(f"Indicator {indicator.code} v{indicator.version} is pending programme-owner approval; "
                           "calculations are enabled only for approved definitions")
    method = METHODS.get(indicator.method_code or "")
    if method is None:
        raise InvalidState(f"Indicator {indicator.code} has no supported calculation method configured")
    if period_end < period_start:
        raise AppError(422, "invalid_period", "Period end is before period start")
    if scope_type != "programme":
        current.ensure_access(scope_geo_unit_id, scope_organisation_id)
    elif not current.all_scope:
        raise Forbidden("Programme-wide calculations need access to every geography")
    params = {**(indicator.method_parameters or {}), **(parameters or {})}
    geo_ids, org_id = resolve_scope(db, scope_type, scope_geo_unit_id, scope_organisation_id)

    run = CalculationRun(
        indicator_id=indicator.id, indicator_version=indicator.version, programme_id=programme_id,
        reporting_period_id=reporting_period_id, period_start=period_start, period_end=period_end,
        method_code=method.code, method_version=method.version, parameters=params, scope_type=scope_type,
        scope_geo_unit_id=scope_geo_unit_id, scope_organisation_id=scope_organisation_id, status="running",
        started_at=utcnow(), created_by_id=current.id, updated_by_id=current.id,
    )
    db.add(run)
    db.flush()
    ctx = CalcContext(db, indicator, programme_id, period_start, period_end, geo_ids, org_id, params)
    try:
        output = method.fn(ctx)
    except Exception as exc:  # recorded on the run so the failure is visible and reproducible
        run.status = "calculation_error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = utcnow()
        audit.record(db, current, "calculation_error", "calculation_run", run.id, summary=run.error)
        return run, None

    run.status = "calculated"
    run.finished_at = utcnow()
    run.input_count = len(output.inputs)
    run.output = {"value": None if output.value is None else str(output.value),
                  "numerator": None if output.numerator is None else str(output.numerator),
                  "denominator": None if output.denominator is None else str(output.denominator),
                  "details": output.details}
    db.add_all([CalculationRunInput(calculation_run_id=run.id, entity_type=t, entity_id=i) for t, i in output.inputs])

    baseline = find_baseline(db, indicator, programme_id, scope_type, scope_geo_unit_id, scope_organisation_id)
    change = None
    if baseline is not None and output.value is not None and normalize_unit(baseline.unit) == normalize_unit(indicator.unit):
        change = Decimal(output.value) - Decimal(baseline.value)
    result = IndicatorResult(
        indicator_id=indicator.id, indicator_version=indicator.version, calculation_run_id=run.id,
        programme_id=programme_id, reporting_period_id=reporting_period_id, period_start=period_start,
        period_end=period_end, value=output.value, unit=indicator.unit, numerator=output.numerator,
        denominator=output.denominator, baseline_record_id=baseline.id if baseline else None,
        baseline_value=baseline.value if baseline else None, change_from_baseline=change,
        method_code=method.code, method_version=method.version, calculation_status="calculated",
        scope_type=scope_type, scope_geo_unit_id=scope_geo_unit_id, scope_organisation_id=scope_organisation_id,
        is_demo=indicator.is_demo, is_provisional=provisional, created_by_id=current.id, updated_by_id=current.id,
    )
    db.add(result)
    db.flush()
    # Older unreviewed results for the same indicator/period/scope are superseded (kept for history).
    db.execute(update(IndicatorResult).where(
        IndicatorResult.id != result.id, IndicatorResult.indicator_id == indicator.id,
        IndicatorResult.period_start == period_start, IndicatorResult.period_end == period_end,
        IndicatorResult.scope_type == scope_type,
        eq_or_null(IndicatorResult.scope_geo_unit_id, scope_geo_unit_id),
        eq_or_null(IndicatorResult.scope_organisation_id, scope_organisation_id),
        IndicatorResult.review_status == "not_submitted", IndicatorResult.superseded_by_id.is_(None),
    ).values(superseded_by_id=result.id))
    audit.record(db, current, "calculate", "indicator_result", result.id,
                 summary=f"{indicator.code} = {output.value} {indicator.unit} ({method.code} v{method.version})",
                 after={"calculation_run_id": run.id, "inputs": len(output.inputs)})
    return run, result


def get_entity(db: Session, entity_type: str, entity_id: uuid.UUID) -> Any:
    model = REVIEWABLE.get(entity_type)
    if model is None:
        raise AppError(422, "not_reviewable", f"'{entity_type}' cannot be submitted for review")
    entity = db.get(model, entity_id)
    if entity is None:
        raise AppError(404, "not_found", "Record not found")
    return entity


def _check_reviewer(db: Session, reviewer_id: uuid.UUID | None, submitter_id: uuid.UUID) -> None:
    if reviewer_id is None:
        return
    reviewer = db.get(User, reviewer_id)
    if reviewer is None or reviewer.status != "active":
        raise AppError(422, "invalid_reviewer", "Reviewer not found or inactive")
    if not build_current_user(db, reviewer).has("mrv.review"):
        raise AppError(422, "invalid_reviewer", "The selected user does not have the reviewer permission")
    if reviewer_id == submitter_id and get_setting(db, "mrv.independent_verification_required"):
        raise AppError(422, "separation_of_duties", "A reviewer cannot review their own submission")


def submit_for_review(db: Session, current: CurrentUser, entity_type: str, entity_id: uuid.UUID,
                      reviewer_id: uuid.UUID | None, due_date: date | None, note: str | None) -> ReviewTask:
    entity = get_entity(db, entity_type, entity_id)
    if entity_type == "indicator_result":
        if entity.calculation_status != "calculated":
            raise InvalidState("Only successfully calculated results can be submitted for review")
        if entity.superseded_by_id:
            raise InvalidState("This result has been superseded by a newer calculation")
    elif entity.validation_status != "valid":
        raise InvalidState("Only records that passed validation can be submitted for review")
    if entity.review_status not in ("not_submitted", "correction_requested"):
        raise InvalidState(f"Record is already {entity.review_status.replace('_', ' ')}")
    if entity.verification_status != "unverified":
        raise InvalidState("Record already has a verification decision")
    _check_reviewer(db, reviewer_id, current.id)

    task = db.scalar(select(ReviewTask).where(ReviewTask.entity_type == entity_type, ReviewTask.entity_id == entity_id,
                                              ReviewTask.status == "correction_requested"))
    if task is None:
        title = getattr(entity, "indicator", None)
        task = ReviewTask(
            programme_id=entity.programme_id, entity_type=entity_type, entity_id=entity_id,
            title=f"{entity_type.replace('_', ' ').title()}: {title.label if title else entity_id}",
            submitted_by_id=current.id, assigned_reviewer_id=reviewer_id, due_date=due_date, submission_note=note,
            is_demo=getattr(entity, "is_demo", False), created_by_id=current.id, updated_by_id=current.id,
        )
        db.add(task)
    else:
        task.status = "pending_review"
        task.assigned_reviewer_id = reviewer_id or task.assigned_reviewer_id
        task.due_date = due_date or task.due_date
        if note:
            db.add(ReviewComment(review_task_id=task.id, author_id=current.id, comment=f"Resubmitted: {note}"))
    entity.review_status = "pending_review"
    if entity_type == "indicator_result":
        entity.submitted_by_id = current.id
        entity.submitted_at = utcnow()
    db.flush()
    audit.record(db, current, "submit_for_review", entity_type, entity_id, summary=note,
                 after={"review_task_id": task.id, "reviewer_id": reviewer_id})
    return task


def _require_task_actor(current: CurrentUser, task: ReviewTask) -> None:
    if task.assigned_reviewer_id and task.assigned_reviewer_id != current.id and not current.has("admin.users"):
        raise Forbidden("This review task is assigned to another reviewer")


def request_correction(db: Session, current: CurrentUser, task: ReviewTask, note: str) -> ReviewTask:
    if task.status != "pending_review":
        raise InvalidState("Corrections can only be requested on tasks pending review")
    _require_task_actor(current, task)
    entity = get_entity(db, task.entity_type, task.entity_id)
    task.status = "correction_requested"
    entity.review_status = "correction_requested"
    db.add(ReviewComment(review_task_id=task.id, author_id=current.id, comment=f"Correction requested: {note}"))
    audit.record(db, current, "request_correction", task.entity_type, task.entity_id, summary=note)
    return task


def open_blocking_findings(db: Session, task: ReviewTask) -> int:
    return db.scalar(select(func.count()).select_from(VerificationFinding).where(
        VerificationFinding.review_task_id == task.id, VerificationFinding.severity.in_(["major", "critical"]),
        VerificationFinding.corrective_status.in_(["open", "in_progress"]))) or 0


def mark_reviewed(db: Session, current: CurrentUser, task: ReviewTask, note: str | None) -> ReviewTask:
    if task.status != "pending_review":
        raise InvalidState("Only tasks pending review can be marked reviewed")
    _require_task_actor(current, task)
    if task.submitted_by_id == current.id and get_setting(db, "mrv.independent_verification_required"):
        raise AppError(403, "separation_of_duties", "You cannot review your own submission")
    entity = get_entity(db, task.entity_type, task.entity_id)
    task.status = "reviewed"
    task.reviewed_by_id = current.id
    entity.review_status = "reviewed"
    if note:
        db.add(ReviewComment(review_task_id=task.id, author_id=current.id, comment=f"Reviewed: {note}"))
    audit.record(db, current, "mark_reviewed", task.entity_type, task.entity_id, summary=note)
    return task


def decide(db: Session, current: CurrentUser, task: ReviewTask, decision: str, rationale: str) -> VerificationDecision:
    if decision == "conditional" and not get_setting(db, "mrv.allow_conditional_verification"):
        raise AppError(422, "decision_not_allowed", "Conditional verification is disabled by programme policy")
    if decision in ("verified", "conditional") and task.status != "reviewed":
        raise InvalidState("A record cannot be verified before its review is completed")
    if task.status not in ("reviewed", "pending_review"):
        raise InvalidState(f"No decision can be recorded on a task that is {task.status.replace('_', ' ')}")
    if get_setting(db, "mrv.independent_verification_required") and task.submitted_by_id == current.id:
        raise AppError(403, "separation_of_duties", "You cannot verify a record you submitted")
    if get_setting(db, "mrv.verifier_independent_of_reviewer") and task.reviewed_by_id == current.id:
        raise AppError(403, "separation_of_duties", "Independent verification must be done by someone other than the internal reviewer")
    if (decision == "verified" and get_setting(db, "mrv.block_decision_with_open_major_findings")
            and open_blocking_findings(db, task)):
        raise InvalidState("Resolve or accept all major/critical findings before verifying")
    entity = get_entity(db, task.entity_type, task.entity_id)
    version = (db.scalar(select(func.count()).select_from(VerificationDecision)
                         .where(VerificationDecision.review_task_id == task.id)) or 0) + 1
    record = VerificationDecision(review_task_id=task.id, decision=decision, rationale=rationale,
                                  decided_by_id=current.id, decided_at=utcnow(), version=version,
                                  is_demo=task.is_demo, created_by_id=current.id, updated_by_id=current.id)
    db.add(record)
    before = entity.verification_status
    entity.verification_status = decision
    if task.status == "pending_review":
        entity.review_status = "reviewed"
    task.status = "closed"
    db.flush()
    audit.record(db, current, "verification_decision", task.entity_type, task.entity_id, summary=rationale,
                 before={"verification_status": before}, after={"verification_status": decision, "decision_id": record.id})
    return record
