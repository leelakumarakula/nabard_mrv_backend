import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict, Forbidden
from app.db.mixins import utcnow
from app.db.session import get_db
from app.models import Programme, Report, ReportingPeriod, ReportTemplate
from app.schemas.common import Page, PageParams, page_of, paginate
from app.schemas.evidence import ReportDetail, ReportOut, ReportRequest
from app.services import audit, crud, report_export
from app.services import reports as report_service
from app.services.scope import eq_or_null

router = APIRouter(prefix="/reports", tags=["reports"])


def _period(db: Session, reporting_period_id: uuid.UUID | None, start: date | None, end: date | None) -> tuple[date, date]:
    if reporting_period_id:
        period = crud.get_or_404(db, ReportingPeriod, reporting_period_id, "Reporting period")
        start, end = start or period.start_date, end or period.end_date
    if not (start and end):
        raise AppError(422, "period_required", "Select a reporting period or give period start and end dates")
    if end < start:
        raise AppError(422, "invalid_period", "Period end is before period start")
    return start, end


def _check_scope(current: CurrentUser, scope_type: str, geo_unit_id: uuid.UUID | None, org_id: uuid.UUID | None) -> None:
    if scope_type == "fpo" and not org_id:
        raise AppError(422, "scope_required", "Select the FPO")
    if scope_type in ("state", "district", "cluster") and not geo_unit_id:
        raise AppError(422, "scope_required", f"Select the {scope_type}")
    if current.all_scope:
        return
    if scope_type == "programme":
        raise Forbidden("Programme-wide reports need programme-wide access")
    current.ensure_access(geo_unit_id, org_id)


def _visible(stmt, current: CurrentUser):
    """Programme-wide users see all reports; scoped users see their scope; unscoped users (e.g. research partners)
    see published reports only."""
    if current.all_scope:
        return stmt
    conditions = [Report.status == "published"]
    if current.geo_ids:
        conditions.append(Report.scope_geo_unit_id.in_(current.geo_ids))
    if current.org_ids:
        conditions.append(Report.scope_organisation_id.in_(current.org_ids))
    return stmt.where(or_(*conditions))


def _visible_report(db: Session, report_id: uuid.UUID, current: CurrentUser) -> Report:
    report = db.scalar(_visible(select(Report).where(Report.id == report_id), current))
    if report is None:
        raise AppError(404, "not_found", "Report not found")
    return report


@router.get("/programme")
def programme_report(programme_id: uuid.UUID, reporting_period_id: uuid.UUID | None = None,
                     period_start: date | None = None, period_end: date | None = None,
                     scope_type: str = Query("programme", pattern="^(programme|state|district|cluster|fpo)$"),
                     scope_geo_unit_id: uuid.UUID | None = None, scope_organisation_id: uuid.UUID | None = None,
                     current: CurrentUser = Depends(require("report.read")), db: Session = Depends(get_db)) -> dict:
    """Live programme report data computed from current records (not stored)."""
    programme = crud.get_or_404(db, Programme, programme_id, "Programme")
    start, end = _period(db, reporting_period_id, period_start, period_end)
    _check_scope(current, scope_type, scope_geo_unit_id, scope_organisation_id)
    report_type = "programme" if scope_type == "programme" else ("fpo" if scope_type == "fpo" else "geography")
    return report_service.build(db, programme, report_type, start, end, scope_type, scope_geo_unit_id, scope_organisation_id)


@router.post("", response_model=ReportDetail, status_code=201)
def generate_report(body: ReportRequest, current: CurrentUser = Depends(require("report.generate")),
                    db: Session = Depends(get_db)) -> Report:
    programme = crud.get_or_404(db, Programme, body.programme_id, "Programme")
    start, end = _period(db, body.reporting_period_id, body.period_start, body.period_end)
    _check_scope(current, body.scope_type, body.scope_geo_unit_id, body.scope_organisation_id)
    template = None
    if body.template_code:
        template = db.scalar(select(ReportTemplate).where(
            ReportTemplate.code == body.template_code, ReportTemplate.status != "retired"))
        if template is None:
            raise AppError(422, "unknown_template", f"Unknown or retired report template '{body.template_code}'")
        body.report_type = template.base_report_type
    if body.report_type == "module" and not body.module_code:
        raise AppError(422, "module_required", "Select the module for a module report")
    if body.report_type == "indicator" and not body.indicator_id:
        raise AppError(422, "indicator_required", "Select the indicator for an indicator report")
    content = report_service.build(db, programme, body.report_type, start, end, body.scope_type, body.scope_geo_unit_id,
                                   body.scope_organisation_id, body.module_code, body.indicator_id, template)
    label = template.name if template else f"{body.report_type} report"
    title = body.title or f"{programme.code} {label} – {content['meta']['scope_label']} – {start}..{end}"
    version = (db.scalar(select(func.max(Report.version)).where(
        Report.programme_id == programme.id, Report.report_type == body.report_type, Report.period_start == start,
        Report.period_end == end, Report.scope_type == body.scope_type,
        eq_or_null(Report.scope_geo_unit_id, body.scope_geo_unit_id),
        eq_or_null(Report.scope_organisation_id, body.scope_organisation_id),
    )) or 0) + 1
    report = crud.create(db, current, Report, {
        "programme_id": programme.id, "reporting_period_id": body.reporting_period_id, "period_start": start,
        "period_end": end, "report_type": body.report_type, "module_code": body.module_code,
        "indicator_id": body.indicator_id, "title": title[:255], "template_code": body.template_code,
        "version": version, "status": "generated",
        "content": content, "generated_by_id": current.id, "generated_at": utcnow(), "scope_type": body.scope_type,
        "scope_geo_unit_id": body.scope_geo_unit_id, "scope_organisation_id": body.scope_organisation_id,
        "is_demo": programme.is_demo,
    }, "report", summary=title)
    db.commit()
    return report


@router.get("", response_model=Page[ReportOut])
def list_reports(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, report_type: str | None = None,
                 status: str | None = None, current: CurrentUser = Depends(require("report.read")),
                 db: Session = Depends(get_db)) -> dict:
    stmt = select(Report).order_by(Report.generated_at.desc())
    for column, value in ((Report.programme_id, programme_id), (Report.report_type, report_type), (Report.status, status)):
        if value:
            stmt = stmt.where(column == value)
    return page_of(*paginate(db, _visible(stmt, current), params, unique=False), params, ReportOut)


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: uuid.UUID, current: CurrentUser = Depends(require("report.read")), db: Session = Depends(get_db)):
    return _visible_report(db, report_id, current)


@router.get("/{report_id}/export")
def export_report(report_id: uuid.UUID, format: str = Query("csv", pattern="^(csv|json|xlsx|pdf)$"),
                  current: CurrentUser = Depends(require("report.read")), db: Session = Depends(get_db)) -> Response:
    report = _visible_report(db, report_id, current)
    audit.record(db, current, "export", "report", report.id, summary=f"format={format}")
    db.commit()
    name = f"report-{report.report_type}-{report.period_start}-{report.period_end}-v{report.version}"
    if format == "xlsx":
        return Response(report_export.to_xlsx(report.content),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    if format == "pdf":
        return Response(report_export.to_pdf(report.content), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    if format == "json":
        return Response(json.dumps(report.content, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    return Response(report_service.to_csv(report.content), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.post("/{report_id}/approve", response_model=ReportOut)
def approve_report(report_id: uuid.UUID, current: CurrentUser = Depends(require("report.approve")),
                   db: Session = Depends(get_db)) -> Report:
    report = crud.get_or_404(db, Report, report_id, "Report")
    if report.status != "generated":
        raise Conflict(f"Only generated reports can be approved (status: {report.status})")
    if report.generated_by_id == current.id:
        raise Forbidden("A report must be approved by someone other than the person who generated it")
    crud.update(db, current, report, {"status": "approved", "approved_by_id": current.id, "approved_at": utcnow()},
                "report", action="approve")
    db.commit()
    return report


@router.post("/{report_id}/publish", response_model=ReportOut)
def publish_report(report_id: uuid.UUID, current: CurrentUser = Depends(require("report.approve")),
                   db: Session = Depends(get_db)) -> Report:
    report = crud.get_or_404(db, Report, report_id, "Report")
    if report.status != "approved":
        raise Conflict("Only approved reports can be published")
    crud.update(db, current, report, {"status": "published"}, "report", action="publish")
    db.commit()
    return report
