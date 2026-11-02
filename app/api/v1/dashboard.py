import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import false, func, or_, select, true
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import CurrentUser, descendant_geo_ids, get_current_user, require
from app.db.session import get_db
from app.models import (
    Advisory,
    AuditEvent,
    BeneficiaryCategory,
    CommunityResourcePerson,
    DemonstrationSite,
    Farmer,
    FarmerCategoryAssignment,
    FieldRecord,
    GeoUnit,
    MonitoringRecord,
    Organisation,
    PestSurveillanceCluster,
    PracticeRecord,
    Programme,
    ProgrammeParticipation,
    ProgrammeTarget,
    QuarantinedRecord,
    ReviewTask,
)
from app.services import reports_v2
from app.services.record_types import MODULES
from app.services.reports import Scope
from app.services.scope import apply_scope

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _count(db: Session, stmt) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


@router.get("/breakdown")
def breakdown(by: str = Query("state", pattern="^(state|district)$"), programme_id: uuid.UUID | None = None,
              current: CurrentUser = Depends(require("farmer.read")), db: Session = Depends(get_db)) -> dict:
    """Coverage, adoption and community figures per state or district, within the user's scope."""
    programme = db.get(Programme, programme_id) if programme_id else db.scalars(
        select(Programme).order_by(Programme.proposal_date.desc(), Programme.name)).first()
    if programme is None:
        return {"by": by, "rows": [], "categories": []}
    units = list(db.scalars(select(GeoUnit).where(GeoUnit.unit_type == by, GeoUnit.is_archived == false())
                            .order_by(GeoUnit.name)).unique())
    if not current.all_scope:
        units = [u for u in units if descendant_geo_ids(db, {u.id}) & current.geo_ids]
    categories = [c for c in db.scalars(select(BeneficiaryCategory).where(BeneficiaryCategory.status != "retired")
                                        .order_by(BeneficiaryCategory.sort_order))
                  if current.has("farmer.read_sensitive") or not c.is_sensitive]
    household = func.coalesce(Farmer.household_code, Farmer.farmer_code)
    rows = []
    for unit in units:
        geo_ids = descendant_geo_ids(db, {unit.id})
        if not current.all_scope:
            geo_ids &= current.geo_ids
        in_geo = Farmer.geo_unit_id.in_(geo_ids or {uuid.UUID(int=0)})
        enrolled = (select(ProgrammeParticipation.farmer_id).where(ProgrammeParticipation.programme_id == programme.id,
                                                                   ProgrammeParticipation.status == "enrolled"))
        row = {
            "geo_unit_id": unit.id, "name": unit.name, "requires_confirmation": unit.requires_confirmation,
            "households_registered": db.scalar(select(func.count(func.distinct(household)))
                                               .where(in_geo, Farmer.is_archived == false())) or 0,
            "farmers_enrolled": db.scalar(select(func.count()).select_from(Farmer).where(
                in_geo, Farmer.id.in_(enrolled), Farmer.consent_status == "given")) or 0,
            "households_adopted_observed": db.scalar(select(func.count(func.distinct(household))).join(
                PracticeRecord, PracticeRecord.farmer_id == Farmer.id).where(in_geo, PracticeRecord.status == "observed")) or 0,
            "households_adopted_verified": db.scalar(select(func.count(func.distinct(household))).join(
                PracticeRecord, PracticeRecord.farmer_id == Farmer.id).where(
                in_geo, PracticeRecord.verification_status == "verified")) or 0,
            "demonstration_sites": db.scalar(select(func.count()).select_from(DemonstrationSite).where(
                DemonstrationSite.geo_unit_id.in_(geo_ids or {uuid.UUID(int=0)}))) or 0,
            "active_clusters": db.scalar(select(func.count()).select_from(PestSurveillanceCluster).where(
                PestSurveillanceCluster.geo_unit_id.in_(geo_ids or {uuid.UUID(int=0)}),
                PestSurveillanceCluster.status == "active")) or 0,
            "crps_trained": db.scalar(select(func.count()).select_from(CommunityResourcePerson).where(
                CommunityResourcePerson.geo_unit_id.in_(geo_ids or {uuid.UUID(int=0)}),
                CommunityResourcePerson.status.in_(["trained", "active"]))) or 0,
            "categories": {},
        }
        for category in categories:
            row["categories"][category.code] = db.scalar(select(func.count()).select_from(Farmer).where(
                in_geo, Farmer.id.in_(enrolled), Farmer.id.in_(select(FarmerCategoryAssignment.farmer_id).where(
                    FarmerCategoryAssignment.category_id == category.id, FarmerCategoryAssignment.status == "active")))) or 0
        rows.append(row)
    return {"by": by, "programme": programme.name, "rows": rows,
            "categories": [{"code": c.code, "name": c.name, "status": c.status} for c in categories]}


@router.get("/system-info")
def system_info(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Environment banner data: the UI shows a DEMO/SYNTHETIC notice whenever demo records exist."""
    return {
        "app_env": get_settings().app_env,
        "demo_data_present": db.scalar(select(Farmer.id).where(Farmer.is_demo == true()).limit(1)) is not None,
        "api_version": "1.0.0",
    }


@router.get("")
def dashboard(programme_id: uuid.UUID | None = None, current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> dict:
    """Role-aware summary; every block respects the user's permissions and geography scope."""
    programme = db.get(Programme, programme_id) if programme_id else db.scalars(
        select(Programme).order_by(Programme.proposal_date.desc(), Programme.name)).first()
    out: dict = {"user": {"full_name": current.user.full_name, "roles": sorted(current.role_codes)},
                 "programme": None if programme is None else {
                     "id": programme.id, "name": programme.name, "version_label": programme.version_label,
                     "status": programme.status, "duration_months": programme.duration_months}}
    if current.has("farmer.read"):
        farmers = apply_scope(select(Farmer.id).where(Farmer.is_archived == false()), current, Farmer.geo_unit_id,
                              Farmer.primary_fpo_id)
        enrolled = apply_scope(select(Farmer.id).join(ProgrammeParticipation, ProgrammeParticipation.farmer_id == Farmer.id)
                               .where(ProgrammeParticipation.status == "enrolled", Farmer.consent_status == "given"),
                               current, Farmer.geo_unit_id, Farmer.primary_fpo_id)
        fpos = apply_scope(select(Organisation.id).where(Organisation.org_type == "fpo"), current,
                           Organisation.geo_unit_id, Organisation.id)
        out["coverage"] = {"farmers_registered": _count(db, farmers), "farmers_enrolled": _count(db, enrolled.distinct()),
                           "fpos": _count(db, fpos)}
    if current.has("programme.read"):
        targets = db.scalars(select(ProgrammeTarget).where(ProgrammeTarget.programme_id == (programme.id if programme else None))
                             .order_by(ProgrammeTarget.metric_code, ProgrammeTarget.target_version.desc())) if programme else []
        latest: dict[str, ProgrammeTarget] = {}
        for t in targets:
            latest.setdefault(t.metric_code, t)
        out["planning_targets"] = [{"metric_name": t.metric_name, "target_value": str(t.target_value), "unit": t.unit,
                                    "status": t.status, "target_version": t.target_version} for t in latest.values()]
    if programme and current.all_scope and current.has("report.read"):
        scope = Scope(db, "programme", None, None)
        out["progress"] = reports_v2.progress_vs_targets(db, programme.id, scope)
        end_dt = datetime.now(UTC)
        out["funnel"] = reports_v2.funnel(db, programme.id, scope, end_dt, end_dt.date())
    if current.has("module.read"):
        out["community"] = {
            "demonstration_sites": dict(db.execute(apply_scope(
                select(DemonstrationSite.category, func.count()).group_by(DemonstrationSite.category), current,
                DemonstrationSite.geo_unit_id, DemonstrationSite.organisation_id)).all()),
            "surveillance_clusters_active": _count(db, apply_scope(
                select(PestSurveillanceCluster.id).where(PestSurveillanceCluster.status == "active"), current,
                PestSurveillanceCluster.geo_unit_id, PestSurveillanceCluster.organisation_id)),
            "crps_trained": _count(db, apply_scope(
                select(CommunityResourcePerson.id).where(CommunityResourcePerson.status.in_(["trained", "active"])), current,
                CommunityResourcePerson.geo_unit_id, CommunityResourcePerson.organisation_id)),
        }
    if current.has("module.read"):
        counts = dict(db.execute(apply_scope(select(FieldRecord.module_code, func.count()).group_by(FieldRecord.module_code),
                                             current, FieldRecord.geo_unit_id, FieldRecord.organisation_id)).all())
        out["modules"] = [{"code": m.code, "number": m.number, "name": m.name, "records": counts.get(m.code, 0)} for m in MODULES]
        exceptions = _count(db, apply_scope(select(FieldRecord.id).where(FieldRecord.validation_status.in_(["exception", "rejected"])),
                                            current, FieldRecord.geo_unit_id, FieldRecord.organisation_id))
        out.setdefault("data_quality", {})["workstream_exceptions"] = exceptions
    if current.has("monitoring.read"):
        dq = dict(db.execute(apply_scope(select(MonitoringRecord.validation_status, func.count())
                                         .group_by(MonitoringRecord.validation_status), current,
                                         MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)).all())
        out.setdefault("data_quality", {})["monitoring_by_validation_status"] = dq
    if current.has("integration.read"):
        out.setdefault("data_quality", {})["open_quarantine"] = db.scalar(
            select(func.count()).select_from(QuarantinedRecord).where(QuarantinedRecord.status == "open")) or 0
    if current.has("advisory.read"):
        now = datetime.now(UTC)
        alerts = apply_scope(select(Advisory).where(Advisory.advisory_type == "alert", Advisory.status == "issued",
                                                    or_(Advisory.valid_until.is_(None), Advisory.valid_until >= now))
                             .order_by(Advisory.issued_at.desc()).limit(5), current,
                             Advisory.target_geo_unit_id, Advisory.target_organisation_id)
        out["active_alerts"] = [{"id": a.id, "title": a.title, "hazard_type": a.hazard_type, "risk_level": a.risk_level,
                                 "issued_at": a.issued_at, "is_demo": a.is_demo, "area": a.target_geo_unit.label}
                                for a in db.scalars(alerts).unique()]
    if current.has("mrv.read"):
        tasks = db.scalars(select(ReviewTask).where(
            ReviewTask.status.in_(["pending_review", "correction_requested", "reviewed"]),
            or_(ReviewTask.assigned_reviewer_id == current.id, ReviewTask.submitted_by_id == current.id,
                (ReviewTask.assigned_reviewer_id.is_(None)) if current.has("mrv.review") else ReviewTask.id.is_(None),
                # independent verifiers see reviewed tasks awaiting their decision
                (ReviewTask.status == "reviewed") if current.has("mrv.verify") else ReviewTask.id.is_(None)))
            .order_by(ReviewTask.created_at.desc()).limit(10))
        out["my_tasks"] = [{"id": t.id, "title": t.title, "status": t.status, "due_date": t.due_date,
                            "role": "submitter" if t.submitted_by_id == current.id else
                    ("verifier" if t.status == "reviewed" and current.has("mrv.verify") else "reviewer")} for t in tasks]
    activity = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(10)
    if not current.has("admin.audit"):
        activity = activity.where(AuditEvent.actor_id == current.id)
    out["recent_activity"] = [{"occurred_at": e.occurred_at, "action": e.action, "entity_type": e.entity_type,
                               "summary": e.summary, "actor_email": e.actor_email} for e in db.scalars(activity)]
    return out
