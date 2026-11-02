import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, build_current_user, get_current_user, require
from app.core.errors import AppError
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import (
    AuditEvent,
    BaselineRecord,
    CalculationRun,
    CalculationRunInput,
    Device,
    FieldRecord,
    IndicatorDefinition,
    IndicatorResult,
    MonitoringRecord,
    PracticeRecord,
    Programme,
    ReportingPeriod,
    ReviewComment,
    ReviewTask,
    User,
    VerificationDecision,
    VerificationFinding,
)
from app.schemas.common import Page, PageParams, paginate
from app.schemas.mrv import (
    CalculationOut,
    CalculationRequest,
    CalculationRunOut,
    CommentIn,
    CommentOut,
    CorrectionIn,
    DecisionIn,
    DecisionOut,
    FindingIn,
    FindingOut,
    FindingUpdate,
    IndicatorResultOut,
    NoteIn,
    ReviewTaskDetail,
    ReviewTaskOut,
    RunInputOut,
    SubmitForReviewIn,
)
from app.services import crud, mrv
from app.services.audit import snapshot
from app.services.results import with_scope_labels
from app.services.settings import get_setting

router = APIRouter(prefix="/mrv", tags=["mrv workspace"])

# Permission that submits each reviewable record type for review (see the submit-for-review endpoints)
SUBMIT_PERMISSION = {"indicator_result": "mrv.calculate", "monitoring_record": "mrv.calculate",
                     "baseline_record": "baseline.manage"}


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids or {uuid.UUID(int=0)}))).all())


@router.post("/calculations", response_model=CalculationOut, status_code=201)
def calculate(body: CalculationRequest, current: CurrentUser = Depends(require("mrv.calculate")),
              db: Session = Depends(get_db)) -> CalculationOut:
    indicator = crud.get_or_404(db, IndicatorDefinition, body.indicator_id, "Indicator")
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    start, end = body.period_start, body.period_end
    if body.reporting_period_id:
        period = crud.get_or_404(db, ReportingPeriod, body.reporting_period_id, "Reporting period")
        start, end = start or period.start_date, end or period.end_date
    if not (start and end):
        raise AppError(422, "period_required", "Select a reporting period or give period start and end dates")
    run, result = mrv.run_calculation(db, current, indicator, body.programme_id, start, end, body.reporting_period_id,
                                      body.scope_type, body.scope_geo_unit_id, body.scope_organisation_id, body.parameters)
    db.commit()
    db.refresh(run)
    if result is not None:
        db.refresh(result)
    return CalculationOut(run=CalculationRunOut.model_validate(run),
                          result=with_scope_labels(db, [result])[0] if result else None)


@router.get("/calculation-runs", response_model=Page[CalculationRunOut])
def list_runs(params: PageParams = Depends(), indicator_id: uuid.UUID | None = None, status: str | None = None,
              current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(CalculationRun).order_by(CalculationRun.started_at.desc())
    if indicator_id:
        stmt = stmt.where(CalculationRun.indicator_id == indicator_id)
    if status:
        stmt = stmt.where(CalculationRun.status == status)
    items, total = paginate(db, stmt, params)
    return {"items": [CalculationRunOut.model_validate(i) for i in items], "total": total, "page": params.page,
            "page_size": params.page_size}


@router.get("/calculation-runs/{run_id}", response_model=CalculationRunOut)
def get_run(run_id: uuid.UUID, current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)):
    return crud.get_or_404(db, CalculationRun, run_id, "Calculation run")


@router.get("/calculation-runs/{run_id}/inputs", response_model=Page[RunInputOut])
def get_run_inputs(run_id: uuid.UUID, params: PageParams = Depends(), current: CurrentUser = Depends(require("mrv.read")),
                   db: Session = Depends(get_db)) -> dict:
    stmt = (select(CalculationRunInput).where(CalculationRunInput.calculation_run_id == run_id)
            .order_by(CalculationRunInput.entity_type, CalculationRunInput.entity_id))
    items, total = paginate(db, stmt, params, unique=False)
    return {"items": [RunInputOut.model_validate(i) for i in items], "total": total, "page": params.page,
            "page_size": params.page_size}


@router.get("/results/{result_id}", response_model=IndicatorResultOut)
def get_result(result_id: uuid.UUID, current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)):
    return with_scope_labels(db, [crud.get_or_404(db, IndicatorResult, result_id, "Result")])[0]


@router.post("/results/{result_id}/submit-for-review", response_model=ReviewTaskOut)
def result_for_review(result_id: uuid.UUID, body: SubmitForReviewIn, current: CurrentUser = Depends(require("mrv.calculate")),
                      db: Session = Depends(get_db)):
    task = mrv.submit_for_review(db, current, "indicator_result", result_id, body.reviewer_id, body.due_date, body.note)
    db.commit()
    return task


@router.get("/reviewers")
def list_reviewers(current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)) -> list[dict]:
    """Internal reviewers and independent verifiers, with what each may do."""
    out = []
    for user in db.scalars(select(User).where(User.status == "active").order_by(User.full_name)):
        perms = build_current_user(db, user)
        if perms.has("mrv.review") or perms.has("mrv.verify"):
            out.append({"id": user.id, "full_name": user.full_name, "email": user.email,
                        "can_review": perms.has("mrv.review"), "can_verify": perms.has("mrv.verify"),
                        "can_sample": perms.has("verification.sample")})
    return out


@router.get("/review-tasks", response_model=Page[ReviewTaskOut])
def list_tasks(params: PageParams = Depends(), mine: bool = False, status: str | None = None, entity_type: str | None = None,
               current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(ReviewTask).order_by(ReviewTask.created_at.desc())
    if mine:
        stmt = stmt.where(or_(ReviewTask.assigned_reviewer_id == current.id, ReviewTask.submitted_by_id == current.id))
    if status:
        stmt = stmt.where(ReviewTask.status == status)
    if entity_type:
        stmt = stmt.where(ReviewTask.entity_type == entity_type)
    items, total = paginate(db, stmt, params)
    names = _names(db, {t.submitted_by_id for t in items} | {t.assigned_reviewer_id for t in items})
    out = [ReviewTaskOut.model_validate(t).model_copy(update={
        "submitted_by_name": names.get(t.submitted_by_id), "reviewer_name": names.get(t.assigned_reviewer_id)}) for t in items]
    return {"items": out, "total": total, "page": params.page, "page_size": params.page_size}


def _detail(db: Session, task: ReviewTask, current: CurrentUser) -> ReviewTaskDetail:
    entity = mrv.get_entity(db, task.entity_type, task.entity_id)
    comments = list(db.scalars(select(ReviewComment).where(ReviewComment.review_task_id == task.id).order_by(ReviewComment.created_at)))
    findings = list(db.scalars(select(VerificationFinding).where(VerificationFinding.review_task_id == task.id)
                               .order_by(VerificationFinding.created_at)))
    decisions = list(db.scalars(select(VerificationDecision).where(VerificationDecision.review_task_id == task.id)
                                .order_by(VerificationDecision.version)))
    names = _names(db, {task.submitted_by_id, task.assigned_reviewer_id, task.reviewed_by_id} | {c.author_id for c in comments}
                   | {d.decided_by_id for d in decisions})
    independent = get_setting(db, "mrv.independent_verification_required")
    not_submitter = not (independent and task.submitted_by_id == current.id)
    assigned_ok = task.assigned_reviewer_id in (None, current.id)
    entity_data = snapshot(entity)
    if hasattr(entity, "indicator") and entity.indicator is not None:
        entity_data["indicator_label"] = entity.indicator.label
        entity_data["indicator_requires_confirmation"] = entity.indicator.requires_confirmation
    base = ReviewTaskOut.model_validate(task).model_copy(update={
        "submitted_by_name": names.get(task.submitted_by_id), "reviewer_name": names.get(task.assigned_reviewer_id)})
    return ReviewTaskDetail(
        **base.model_dump(), entity=entity_data,
        comments=[CommentOut.model_validate(c).model_copy(update={"author_name": names.get(c.author_id)}) for c in comments],
        findings=[FindingOut.model_validate(f) for f in findings],
        decisions=[DecisionOut.model_validate(d).model_copy(update={"decider_name": names.get(d.decided_by_id)}) for d in decisions],
        open_blocking_findings=mrv.open_blocking_findings(db, task),
        can_review=current.has("mrv.review") and task.status == "pending_review" and not_submitter and assigned_ok,
        can_decide=(current.has("mrv.verify") and task.status in ("reviewed", "pending_review") and not_submitter
                    and not (get_setting(db, "mrv.verifier_independent_of_reviewer") and task.reviewed_by_id == current.id)),
    )


@router.get("/review-tasks/{task_id}", response_model=ReviewTaskDetail)
def get_task(task_id: uuid.UUID, current: CurrentUser = Depends(require("mrv.read")), db: Session = Depends(get_db)):
    return _detail(db, crud.get_or_404(db, ReviewTask, task_id, "Review task"), current)


@router.post("/review-tasks/{task_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(task_id: uuid.UUID, body: CommentIn, current: CurrentUser = Depends(require("mrv.read")),
                db: Session = Depends(get_db)):
    task = crud.get_or_404(db, ReviewTask, task_id, "Review task")
    if not (current.has("mrv.review") or current.id == task.submitted_by_id):
        raise AppError(403, "forbidden", "Only the submitter or reviewers can comment")
    comment = ReviewComment(review_task_id=task.id, author_id=current.id, comment=body.comment,
                            created_by_id=current.id, updated_by_id=current.id)
    db.add(comment)
    db.commit()
    return CommentOut.model_validate(comment).model_copy(update={"author_name": current.user.full_name})


@router.post("/review-tasks/{task_id}/findings", response_model=FindingOut, status_code=201)
def add_finding(task_id: uuid.UUID, body: FindingIn, current: CurrentUser = Depends(require("mrv.review")),
                db: Session = Depends(get_db)):
    task = crud.get_or_404(db, ReviewTask, task_id, "Review task")
    if task.status == "closed":
        raise AppError(422, "invalid_state", "Findings cannot be added to a closed task")
    finding = crud.create(db, current, VerificationFinding, {**body.model_dump(), "review_task_id": task.id,
                                                             "is_demo": task.is_demo}, "verification_finding",
                          summary=f"{body.severity} {body.finding_type}")
    db.commit()
    return finding


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def update_finding(finding_id: uuid.UUID, body: FindingUpdate, current: CurrentUser = Depends(require("mrv.read")),
                   db: Session = Depends(get_db)):
    finding = crud.get_or_404(db, VerificationFinding, finding_id, "Finding")
    task = db.get(ReviewTask, finding.review_task_id) if finding.review_task_id else None
    submitter_id = task.submitted_by_id if task else None
    data = body.model_dump(exclude_unset=True)
    status = data.get("corrective_status")
    can_accept = current.has("mrv.review") or (task is None and current.has("verification.sample"))
    if status == "accepted" and not can_accept:
        raise AppError(403, "forbidden", "Only reviewers (or the independent verifier, for sample findings) can accept a "
                                         "corrective action")
    if status in ("in_progress", "resolved") and not (can_accept or current.id == submitter_id
                                                      or current.has("mrv.calculate")):
        raise AppError(403, "forbidden", "Only the submitter, analysts or reviewers can update corrective actions")
    if status in ("resolved", "accepted"):
        data.update(resolved_by_id=current.id, resolved_at=utcnow())
    crud.update(db, current, finding, data, "verification_finding")
    db.commit()
    return finding


@router.post("/review-tasks/{task_id}/request-correction", response_model=ReviewTaskOut)
def request_correction(task_id: uuid.UUID, body: CorrectionIn, current: CurrentUser = Depends(require("mrv.review")),
                       db: Session = Depends(get_db)):
    task = mrv.request_correction(db, current, crud.get_or_404(db, ReviewTask, task_id, "Review task"), body.note)
    db.commit()
    return task


@router.post("/review-tasks/{task_id}/resubmit", response_model=ReviewTaskOut)
def resubmit(task_id: uuid.UUID, body: SubmitForReviewIn, current: CurrentUser = Depends(require("mrv.read")),
             db: Session = Depends(get_db)):
    task = crud.get_or_404(db, ReviewTask, task_id, "Review task")
    # Resubmitting needs the same permission as the original submission for this kind of record
    current.require(SUBMIT_PERMISSION.get(task.entity_type, "mrv.calculate"))
    if task.status != "correction_requested":
        raise AppError(422, "invalid_state", "Only tasks with a correction request can be resubmitted")
    task = mrv.submit_for_review(db, current, task.entity_type, task.entity_id, body.reviewer_id or task.assigned_reviewer_id,
                                 body.due_date, body.note)
    db.commit()
    return task


@router.post("/review-tasks/{task_id}/mark-reviewed", response_model=ReviewTaskOut)
def mark_reviewed(task_id: uuid.UUID, body: NoteIn, current: CurrentUser = Depends(require("mrv.review")),
                  db: Session = Depends(get_db)):
    task = mrv.mark_reviewed(db, current, crud.get_or_404(db, ReviewTask, task_id, "Review task"), body.note)
    db.commit()
    return task


@router.post("/review-tasks/{task_id}/decision", response_model=DecisionOut, status_code=201)
def record_decision(task_id: uuid.UUID, body: DecisionIn, current: CurrentUser = Depends(require("mrv.verify")),
                    db: Session = Depends(get_db)):
    decision = mrv.decide(db, current, crud.get_or_404(db, ReviewTask, task_id, "Review task"), body.decision, body.rationale)
    db.commit()
    return DecisionOut.model_validate(decision).model_copy(update={"decider_name": current.user.full_name})


HISTORY_TYPES = {"field_record", "monitoring_record", "baseline_record", "indicator_result", "practice_record",
                 "advisory", "advisory_delivery", "evidence_asset", "device", "indicator_definition", "report",
                 "verification_sample"}
# Record-level types whose history is limited to users with access to the record's geography/organisation
SCOPED_HISTORY = {"field_record": FieldRecord, "monitoring_record": MonitoringRecord, "baseline_record": BaselineRecord,
                  "practice_record": PracticeRecord, "device": Device}


@router.get("/entity-history/{entity_type}/{entity_id}")
def entity_history(entity_type: str, entity_id: uuid.UUID, current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> list[dict]:
    """Status history of a record from the audit log (used by review and detail screens).

    Person-level entities (farmers, users) are only visible through the admin audit log."""
    if not current.has("admin.audit"):
        if entity_type not in HISTORY_TYPES or not (current.has("mrv.read") or current.has("module.read")):
            raise AppError(403, "forbidden", "You do not have access to this history")
    record = db.get(SCOPED_HISTORY[entity_type], entity_id) if entity_type in SCOPED_HISTORY else None
    if record is not None and (record.geo_unit_id or record.organisation_id):
        current.ensure_access(record.geo_unit_id, record.organisation_id)
    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == entity_type,
                                                 AuditEvent.entity_id == str(entity_id)).order_by(AuditEvent.occurred_at))
    return [{"occurred_at": e.occurred_at, "action": e.action, "actor_email": e.actor_email, "summary": e.summary,
             "before": e.before, "after": e.after} for e in events]
