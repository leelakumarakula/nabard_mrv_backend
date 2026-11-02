"""Report content assembled from underlying records so totals reconcile and every figure shows its status."""

import csv
import io
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import false, func, select, true
from sqlalchemy.orm import Session

from app.core.deps import descendant_geo_ids
from app.core.errors import AppError
from app.models import (
    Advisory,
    AdvisoryDelivery,
    Device,
    Farm,
    Farmer,
    FarmPlot,
    FieldRecord,
    GeoUnit,
    IndicatorResult,
    MonitoringRecord,
    Organisation,
    PracticeRecord,
    Programme,
    ProgrammeParticipation,
    ProgrammeTarget,
    QuarantinedRecord,
    ReviewTask,
    VerificationFinding,
)
from app.services import reports_v2 as v2
from app.services.record_types import MODULES_BY_CODE, NATURE_LABELS
from app.services.scope import eq_or_null
from app.services.settings import get_setting
from app.services.units import area_in_ha

DISCLAIMER = ("Programme targets are planning figures with the status shown and are not achievements. "
              "Indicator results show their review and verification status; unverified results are not verified "
              "outcomes. Model predictions are risk estimates, not confirmed field conditions.")


def _s(value: Any) -> Any:
    return str(value) if isinstance(value, (Decimal, uuid.UUID)) else value


class Scope:
    def __init__(self, db: Session, scope_type: str, geo_unit_id: uuid.UUID | None, organisation_id: uuid.UUID | None):
        self.type = scope_type
        self.geo_unit_id = geo_unit_id
        self.organisation_id = organisation_id
        self.geo_ids = descendant_geo_ids(db, {geo_unit_id}) if geo_unit_id else None
        if geo_unit_id:
            self.label = db.get(GeoUnit, geo_unit_id).label
        elif organisation_id:
            self.label = db.get(Organisation, organisation_id).name
        else:
            self.label = "Whole programme"

    def apply(self, stmt, geo_col, org_col):
        if self.geo_ids is not None:
            stmt = stmt.where(geo_col.in_(self.geo_ids or {uuid.UUID(int=0)}))
        if self.organisation_id is not None:
            stmt = stmt.where(org_col == self.organisation_id)
        return stmt

    def match_result(self, stmt):
        """Indicator results calculated for exactly this scope."""
        return stmt.where(eq_or_null(IndicatorResult.scope_geo_unit_id, self.geo_unit_id),
                          eq_or_null(IndicatorResult.scope_organisation_id, self.organisation_id))


def _count(db: Session, stmt) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def _group_counts(db: Session, stmt) -> dict[str, int]:
    return {str(k): int(v) for k, v in db.execute(stmt).all()}


def coverage(db: Session, programme: Programme, scope: Scope, period_end: date) -> dict[str, Any]:
    farmers = scope.apply(select(Farmer.id).where(Farmer.is_archived == false()), Farmer.geo_unit_id, Farmer.primary_fpo_id)
    enrolled = scope.apply(
        select(Farmer.id).join(ProgrammeParticipation, ProgrammeParticipation.farmer_id == Farmer.id).where(
            ProgrammeParticipation.programme_id == programme.id, ProgrammeParticipation.status == "enrolled",
            Farmer.consent_status == "given", Farmer.is_archived == false(),
            (ProgrammeParticipation.enrolled_on.is_(None)) | (ProgrammeParticipation.enrolled_on <= period_end)),
        Farmer.geo_unit_id, Farmer.primary_fpo_id)
    enrolled_ids = set(db.scalars(enrolled))
    plots = db.execute(select(FarmPlot.area, FarmPlot.area_unit).join(Farm, Farm.id == FarmPlot.farm_id).where(
        Farm.farmer_id.in_(enrolled_ids or {uuid.UUID(int=0)}), FarmPlot.is_archived == false())).all()
    fpos = scope.apply(select(Organisation.id).where(Organisation.org_type == "fpo", Organisation.is_archived == false()),
                       Organisation.geo_unit_id, Organisation.id)
    devices = scope.apply(select(Device.device_type, Device.status, func.count()).group_by(Device.device_type, Device.status),
                          Device.geo_unit_id, Device.organisation_id)
    return {
        "fpos_registered": _count(db, fpos),
        "farmers_registered": _count(db, farmers),
        "farmers_enrolled_with_consent": len(enrolled_ids),
        "plots_of_enrolled_farmers": len(plots),
        "plot_area_of_enrolled_farmers_ha": _s(round(sum((area_in_ha(a, u) for a, u in plots), Decimal(0)), 2)),
        "plots_without_area": sum(1 for a, _ in plots if a is None),
        "devices": [{"device_type": t, "status": s, "count": c} for t, s, c in db.execute(devices).all()],
        "note": "Registry counts; enrolment requires recorded consent. Area is as recorded (declared or measured).",
    }


def indicator_results(db: Session, programme: Programme, scope: Scope, start: date, end: date,
                      indicator_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    stmt = select(IndicatorResult).where(
        IndicatorResult.programme_id == programme.id, IndicatorResult.superseded_by_id.is_(None),
        IndicatorResult.period_start >= start, IndicatorResult.period_end <= end,
        IndicatorResult.scope_type == scope.type)
    stmt = stmt.where(eq_or_null(IndicatorResult.scope_geo_unit_id, scope.geo_unit_id),
                      eq_or_null(IndicatorResult.scope_organisation_id, scope.organisation_id))
    if indicator_id:
        stmt = stmt.where(IndicatorResult.indicator_id == indicator_id)
    if not get_setting(db, "reports.include_unverified_results"):
        stmt = stmt.where(IndicatorResult.verification_status == "verified")
    rows = list(db.scalars(stmt.order_by(IndicatorResult.period_start)).unique())
    return [{
        "result_id": _s(r.id), "indicator_code": r.indicator.code, "indicator_version": r.indicator_version,
        "indicator_name": r.indicator.name, "indicator_status": r.indicator.status,
        "method_requires_confirmation": r.indicator.requires_confirmation,
        "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
        "value": _s(r.value), "unit": r.unit, "numerator": _s(r.numerator), "denominator": _s(r.denominator),
        "baseline_value": _s(r.baseline_value), "change_from_baseline": _s(r.change_from_baseline),
        "method": f"{r.method_code} v{r.method_version}", "calculation_run_id": _s(r.calculation_run_id),
        "review_status": r.review_status, "verification_status": r.verification_status, "is_provisional": r.is_provisional,
        "is_demo": r.is_demo,
    } for r in rows]


def data_quality(db: Session, programme: Programme, scope: Scope, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    mon = scope.apply(select(MonitoringRecord.validation_status, func.count()).where(
        MonitoringRecord.programme_id == programme.id, MonitoringRecord.observed_at.between(start_dt, end_dt))
        .group_by(MonitoringRecord.validation_status), MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)
    ver = scope.apply(select(MonitoringRecord.verification_status, func.count()).where(
        MonitoringRecord.programme_id == programme.id, MonitoringRecord.observed_at.between(start_dt, end_dt))
        .group_by(MonitoringRecord.verification_status), MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)
    src = scope.apply(select(MonitoringRecord.submission_status, func.count()).where(
        MonitoringRecord.programme_id == programme.id, MonitoringRecord.observed_at.between(start_dt, end_dt))
        .group_by(MonitoringRecord.submission_status), MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)
    fr = scope.apply(select(FieldRecord.validation_status, func.count()).where(
        FieldRecord.programme_id == programme.id, FieldRecord.created_at.between(start_dt, end_dt))
        .group_by(FieldRecord.validation_status), FieldRecord.geo_unit_id, FieldRecord.organisation_id)
    return {
        "monitoring_by_validation_status": _group_counts(db, mon),
        "monitoring_by_verification_status": _group_counts(db, ver),
        "monitoring_by_source": _group_counts(db, src),
        "workstream_records_by_validation_status": _group_counts(db, fr),
        "open_quarantined_integration_records": db.scalar(select(func.count()).select_from(QuarantinedRecord)
                                                          .where(QuarantinedRecord.status == "open")) or 0,
    }


def module_activity(db: Session, programme: Programme, scope: Scope, start_dt: datetime, end_dt: datetime,
                    module_code: str | None = None) -> dict[str, Any]:
    fr = select(FieldRecord.module_code, FieldRecord.record_type, FieldRecord.data_nature, FieldRecord.validation_status,
                func.count()).where(FieldRecord.programme_id == programme.id,
                                    func.coalesce(FieldRecord.observed_at, FieldRecord.created_at).between(start_dt, end_dt))
    if module_code:
        fr = fr.where(FieldRecord.module_code == module_code)
    fr = scope.apply(fr.group_by(FieldRecord.module_code, FieldRecord.record_type, FieldRecord.data_nature,
                                 FieldRecord.validation_status), FieldRecord.geo_unit_id, FieldRecord.organisation_id)
    records = [{"module": MODULES_BY_CODE[m].name if m in MODULES_BY_CODE else m, "module_code": m, "record_type": t,
                "data_nature": n, "data_nature_label": NATURE_LABELS.get(n, n), "validation_status": v, "count": c}
               for m, t, n, v, c in db.execute(fr).all()]
    adv = select(Advisory.advisory_type, Advisory.status, func.count()).where(
        Advisory.programme_id == programme.id, Advisory.issued_at.between(start_dt, end_dt))
    if module_code:
        adv = adv.where(Advisory.origin_module == module_code)
    adv = scope.apply(adv.group_by(Advisory.advisory_type, Advisory.status), Advisory.target_geo_unit_id,
                      Advisory.target_organisation_id)
    deliveries = scope.apply(
        select(func.count(AdvisoryDelivery.id), func.count(AdvisoryDelivery.delivered_at), func.count(AdvisoryDelivery.response))
        .join(Advisory, Advisory.id == AdvisoryDelivery.advisory_id)
        # Deliveries count in the period they happened (as in the indicator methods), not when the advisory was issued.
        .where(Advisory.programme_id == programme.id,
               func.coalesce(AdvisoryDelivery.delivered_at, AdvisoryDelivery.sent_at, AdvisoryDelivery.created_at)
               .between(start_dt, end_dt),
               *([Advisory.origin_module == module_code] if module_code else [])),
        AdvisoryDelivery.geo_unit_id, AdvisoryDelivery.organisation_id)
    total, delivered, responded = db.execute(deliveries).one()
    prac = select(PracticeRecord.activity_code, PracticeRecord.status, func.count()).where(
        PracticeRecord.programme_id == programme.id,
        PracticeRecord.created_at.between(start_dt, end_dt))
    if module_code:
        prac = prac.where(PracticeRecord.module_code == module_code)
    prac = scope.apply(prac.group_by(PracticeRecord.activity_code, PracticeRecord.status), PracticeRecord.geo_unit_id,
                       PracticeRecord.organisation_id)
    return {
        "workstream_records": records,
        "advisories": [{"advisory_type": t, "status": s, "count": c} for t, s, c in db.execute(adv).all()],
        "advisory_deliveries": {"recorded": total, "delivered": delivered, "with_response": responded,
                                "note": "Delivery and response are not evidence of practice adoption."},
        "practice_records": [{"activity_code": a, "status": s, "count": c} for a, s, c in db.execute(prac).all()],
    }


def mrv_status(db: Session, programme: Programme) -> dict[str, Any]:
    tasks = _group_counts(db, select(ReviewTask.status, func.count()).where(ReviewTask.programme_id == programme.id)
                          .group_by(ReviewTask.status))
    findings = _group_counts(db, select(VerificationFinding.severity, func.count())
                             .join(ReviewTask, ReviewTask.id == VerificationFinding.review_task_id)
                             .where(ReviewTask.programme_id == programme.id,
                                    VerificationFinding.corrective_status.in_(["open", "in_progress"]))
                             .group_by(VerificationFinding.severity))
    return {"review_tasks_by_status": tasks, "open_findings_by_severity": findings}


def build(db: Session, programme: Programme, report_type: str, period_start: date, period_end: date,
          scope_type: str, geo_unit_id: uuid.UUID | None, organisation_id: uuid.UUID | None,
          module_code: str | None = None, indicator_id: uuid.UUID | None = None,
          template: Any = None) -> dict[str, Any]:
    scope = Scope(db, scope_type, geo_unit_id, organisation_id)
    start_dt = datetime.combine(period_start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(period_end, time.max, tzinfo=UTC)
    targets = list(db.scalars(select(ProgrammeTarget).where(ProgrammeTarget.programme_id == programme.id)
                              .order_by(ProgrammeTarget.metric_code)))
    if report_type == "endline" and not get_setting(db, "reports.endline_methodology_approved"):
        raise AppError(422, "methodology_pending",
                       "Endline/impact assessment reports need an approved endline methodology (programme-owner confirmation)")
    demo_present = db.scalar(scope.apply(select(Farmer.id).where(Farmer.is_demo == true()), Farmer.geo_unit_id,
                                         Farmer.primary_fpo_id).limit(1)) is not None
    content: dict[str, Any] = {
        "meta": {
            "programme_code": programme.code, "programme_name": programme.name, "programme_version": programme.version_label,
            "report_type": report_type, "report_type_label": REPORT_TYPE_LABELS.get(report_type, report_type),
            "scope_type": scope_type, "scope_label": scope.label, "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(), "generated_at": datetime.now(UTC).isoformat(),
            "module_code": module_code, "contains_demo_data": demo_present or programme.is_demo, "disclaimer": DISCLAIMER,
        },
    }
    if report_type == "nabard_ccf_id":
        content["meta"]["template_note"] = ("The official NABARD CCF-ID reporting template has not been supplied. Sections follow "
                                           "the platform structure and must be mapped to the official template before submission.")
    full = template is not None or report_type in ("programme", "geography", "fpo", "quarterly", "annual", "nabard_ccf_id",
                                                     "endline")
    if full:
        content["planning_targets"] = [{"metric_code": t.metric_code, "metric_name": t.metric_name,
                                        "target_value": _s(t.target_value), "unit": t.unit, "status": t.status,
                                        "target_version": t.target_version, "source_note": t.source_note} for t in targets]
        content["progress_vs_targets"] = v2.progress_vs_targets(db, programme.id, scope)
        content["funnel"] = v2.funnel(db, programme.id, scope, end_dt, period_end)
        content["coverage"] = coverage(db, programme, scope, period_end)
        content["inclusion"] = v2.inclusion(db, programme.id, scope)
        content["demonstrations"] = v2.demonstrations(db, programme.id, scope, start_dt, end_dt)
        content["surveillance"] = v2.surveillance(db, programme.id, scope, start_dt, end_dt, period_start, period_end)
        content["indicator_results"] = indicator_results(db, programme, scope, period_start, period_end)
        content["data_quality"] = data_quality(db, programme, scope, start_dt, end_dt)
        content["module_activity"] = module_activity(db, programme, scope, start_dt, end_dt)
        content["mrv"] = mrv_status(db, programme)
        content["verification"] = v2.verification(db, programme.id)
    elif report_type == "demonstration":
        content["demonstrations"] = v2.demonstrations(db, programme.id, scope, start_dt, end_dt)
    elif report_type == "surveillance":
        content["surveillance"] = v2.surveillance(db, programme.id, scope, start_dt, end_dt, period_start, period_end)
    elif report_type == "inclusion":
        content["inclusion"] = v2.inclusion(db, programme.id, scope)
        content["funnel"] = v2.funnel(db, programme.id, scope, end_dt, period_end)
    elif report_type == "verification":
        content["verification"] = v2.verification(db, programme.id)
        content["mrv"] = mrv_status(db, programme)
        content["indicator_results"] = indicator_results(db, programme, scope, period_start, period_end)
    if template is not None:
        content = {k: v for k, v in content.items() if k == "meta" or k in template.sections}
        content["meta"].update(template_code=template.code, template_name=template.name, template_status=template.status)
        if template.status == "awaiting_official_template":
            content["meta"]["template_note"] = (f"'{template.name}' is awaiting the official template from the programme "
                                                "owner; the layout follows the platform structure.")
        return content
    if report_type == "module":
        content["module_activity"] = module_activity(db, programme, scope, start_dt, end_dt, module_code)
        content["data_quality"] = data_quality(db, programme, scope, start_dt, end_dt)
    elif report_type == "indicator":
        content["indicator_results"] = indicator_results(db, programme, scope, period_start, period_end, indicator_id)
    return content


REPORT_TYPE_LABELS = {
    "programme": "Programme report", "geography": "State/district report", "fpo": "FPO/grower group report",
    "module": "Workstream report", "indicator": "Indicator report", "quarterly": "Quarterly progress report",
    "annual": "Annual progress report", "nabard_ccf_id": "NABARD CCF-ID report (template pending)",
    "demonstration": "Demonstration-site report", "surveillance": "Pest-surveillance report",
    "inclusion": "Beneficiary & inclusion report", "verification": "Monitoring & verification report",
    "endline": "Endline / impact assessment",
}


def to_csv(content: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "item", "value", "unit", "status", "notes"])
    meta = content.get("meta", {})
    for key, value in meta.items():
        writer.writerow(["meta", key, value, "", "", ""])
    for t in content.get("planning_targets", []):
        writer.writerow(["planning_targets", t["metric_name"], t["target_value"], t["unit"], t["status"], t.get("source_note") or ""])
    for t in content.get("progress_vs_targets", []):
        res = t.get("latest_result") or {}
        writer.writerow(["progress_vs_targets", t["metric_name"], res.get("value", ""), t["unit"],
                         f"target={t['target_value']} ({t['target_status']} v{t['target_version']}); "
                         f"verification={res.get('verification_status', 'not calculated')}",
                         "PROVISIONAL" if res.get("provisional") else ""])
    funnel = content.get("funnel")
    if funnel:
        for stage, n in funnel["stages"].items():
            writer.writerow(["funnel", stage, n, funnel["unit"], "", funnel["definitions"].get(stage, "")])
    inclusion = content.get("inclusion")
    if inclusion:
        writer.writerow(["inclusion", "enrolled_farmers", inclusion["enrolled_farmers"], "farmers", "", ""])
        for c in inclusion["categories"]:
            for key in ("enrolled_members", "enrolled_households", "members_with_observed_adoption",
                        "members_with_verified_adoption"):
                writer.writerow(["inclusion", f"{c['code']}:{key}", c[key], "", f"definition={c['definition_status']}",
                                 "sensitive" if c["sensitive"] else ""])
        for k, v in inclusion["community_resource_persons"].items():
            writer.writerow(["inclusion", f"crp_{k}", v if not isinstance(v, dict) else str(v), "", "", ""])
    for s in (content.get("demonstrations") or {}).get("sites", []):
        writer.writerow(["demonstrations", f"{s['site_code']} {s['name']}", s["visits_in_period"], "visits", s["status"], s["category"]])
    surv = content.get("surveillance")
    if surv:
        for key in ("participating_households", "inspections_in_period", "inspections_with_incidence", "follow_up_actions_in_period"):
            writer.writerow(["surveillance", key, surv[key], "", "", ""])
    for s in (content.get("verification") or {}).get("samples", []):
        writer.writerow(["verification", s["title"], s["sample_size"], s["entity_type"], s["status"],
                         f"frame={s['frame_size']}; method={s['selection_method']}; results={s['results']}; conclusion={s['conclusion']}"])
    for key, value in content.get("coverage", {}).items():
        if key == "devices":
            for d in value:
                writer.writerow(["coverage", f"devices:{d['device_type']}", d["count"], "devices", d["status"], ""])
        else:
            writer.writerow(["coverage", key, value, "", "", ""])
    for r in content.get("indicator_results", []):
        writer.writerow(["indicator_results", f"{r['indicator_code']} v{r['indicator_version']} {r['period_start']}..{r['period_end']}",
                         r["value"], r["unit"], f"review={r['review_status']}; verification={r['verification_status']}",
                         ("PROVISIONAL; " if r.get("is_provisional") else "")
                         + f"baseline={r['baseline_value']}; change={r['change_from_baseline']}; method={r['method']}"])
    for group, counts in content.get("data_quality", {}).items():
        if isinstance(counts, dict):
            for status, n in counts.items():
                writer.writerow(["data_quality", group, n, "records", status, ""])
        else:
            writer.writerow(["data_quality", group, counts, "records", "", ""])
    activity = content.get("module_activity", {})
    for r in activity.get("workstream_records", []):
        writer.writerow(["module_activity", f"{r['module_code']}:{r['record_type']}", r["count"], "records",
                         r["validation_status"], r["data_nature_label"]])
    for r in activity.get("practice_records", []):
        writer.writerow(["practice_records", r["activity_code"], r["count"], "records", r["status"], ""])
    return buffer.getvalue()
