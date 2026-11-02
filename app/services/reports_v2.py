"""Version 2.0 report sections: progress vs targets, stage funnel, inclusion, demonstrations, surveillance, verification.

Every count states what it means. Planned (targets), enrolled, reached, participating, adopted (reported / field-observed)
and verified are separate numbers and are never merged.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import false, func, select, true
from sqlalchemy.orm import Session

from app.models import (
    AdvisoryDelivery,
    BeneficiaryCategory,
    ClusterMembership,
    CommunityResourcePerson,
    DemonstrationSite,
    Farmer,
    FarmerCategoryAssignment,
    FieldRecord,
    FieldRecordParticipant,
    IndicatorDefinition,
    IndicatorResult,
    PestSurveillanceCluster,
    PracticeRecord,
    ProgrammeParticipation,
    ProgrammeTarget,
    VerificationSample,
    VerificationSampleItem,
)
from app.services.record_types import CLIMATE_RESILIENT_ACTIVITIES, CONSERVATION_ACTIVITIES

HOUSEHOLD = func.coalesce(Farmer.household_code, Farmer.farmer_code)

FUNNEL_DEFINITIONS = {
    "registered": "Households with at least one registered (non-archived) farmer.",
    "enrolled": "Households with an enrolled farmer whose consent is recorded.",
    "reached": "Households with a confirmed advisory delivery up to period end (delivery is not adoption).",
    "participating": "Households attending a valid training/demonstration or active in a pest-surveillance cluster.",
    "adopted_reported": "Households whose adoption of a climate-resilient practice is farmer-reported only.",
    "adopted_observed": "Households with field-observed adoption of a climate-resilient practice.",
    "adopted_verified": "Households whose observed adoption was confirmed by independent sample verification.",
}


def _s(value: Any) -> Any:
    return str(value) if value is not None and not isinstance(value, (int, str, bool)) else value


def _count_households(db: Session, stmt) -> int:
    sub = stmt.subquery()
    return db.scalar(select(func.count(func.distinct(sub.c.hh)))) or 0


def progress_vs_targets(db: Session, programme_id: uuid.UUID, scope: Any) -> list[dict[str, Any]]:
    """Latest version of each planning target, alongside the latest calculated result for its linked indicator."""
    targets = list(db.scalars(select(ProgrammeTarget).where(ProgrammeTarget.programme_id == programme_id)
                              .order_by(ProgrammeTarget.metric_code, ProgrammeTarget.target_version.desc())))
    latest: dict[str, ProgrammeTarget] = {}
    for t in targets:
        latest.setdefault(t.metric_code, t)
    out = []
    for t in latest.values():
        result = None
        if t.indicator_code:
            stmt = (select(IndicatorResult).join(IndicatorDefinition, IndicatorDefinition.id == IndicatorResult.indicator_id)
                    .where(IndicatorDefinition.code == t.indicator_code, IndicatorResult.programme_id == programme_id,
                           IndicatorResult.superseded_by_id.is_(None), IndicatorResult.scope_type == scope.type)
                    .order_by(IndicatorResult.period_end.desc(), IndicatorResult.created_at.desc()))
            stmt = scope.match_result(stmt)
            result = db.scalars(stmt.limit(1)).unique().first()
        out.append({
            "metric_code": t.metric_code, "metric_name": t.metric_name, "target_value": _s(t.target_value), "unit": t.unit,
            "target_status": t.status, "target_version": t.target_version, "indicator_code": t.indicator_code,
            "source_note": t.source_note,
            "latest_result": None if result is None else {
                "value": _s(result.value), "period_end": result.period_end.isoformat(),
                "review_status": result.review_status, "verification_status": result.verification_status,
                "provisional": result.is_provisional, "result_id": str(result.id)},
        })
    return out


def funnel(db: Session, programme_id: uuid.UUID, scope: Any, end_dt: datetime, period_end: date) -> dict[str, Any]:
    farmers = scope.apply(select(HOUSEHOLD.label("hh"), Farmer.id).where(Farmer.is_archived == false()),
                          Farmer.geo_unit_id, Farmer.primary_fpo_id)
    enrolled = scope.apply(select(HOUSEHOLD.label("hh")).join(ProgrammeParticipation, ProgrammeParticipation.farmer_id == Farmer.id)
                           .where(ProgrammeParticipation.programme_id == programme_id, ProgrammeParticipation.status == "enrolled",
                                  Farmer.consent_status == "given"), Farmer.geo_unit_id, Farmer.primary_fpo_id)
    reached = scope.apply(select(HOUSEHOLD.label("hh")).join(AdvisoryDelivery, AdvisoryDelivery.farmer_id == Farmer.id)
                          .where(AdvisoryDelivery.delivered_at <= end_dt), Farmer.geo_unit_id, Farmer.primary_fpo_id)
    trained = scope.apply(select(HOUSEHOLD.label("hh")).join(FieldRecordParticipant, FieldRecordParticipant.farmer_id == Farmer.id)
                          .join(FieldRecord, FieldRecord.id == FieldRecordParticipant.field_record_id)
                          .where(FieldRecordParticipant.attended == true(), FieldRecord.validation_status == "valid",
                                 FieldRecord.programme_id == programme_id, FieldRecord.observed_at <= end_dt),
                          Farmer.geo_unit_id, Farmer.primary_fpo_id)
    surveillance = scope.apply(select(HOUSEHOLD.label("hh")).join(ClusterMembership, ClusterMembership.farmer_id == Farmer.id)
                               .where(ClusterMembership.status == "active"), Farmer.geo_unit_id, Farmer.primary_fpo_id)
    participating = trained.union(surveillance)

    def adoption(**where: Any):
        stmt = (select(HOUSEHOLD.label("hh")).join(PracticeRecord, PracticeRecord.farmer_id == Farmer.id)
                .where(PracticeRecord.programme_id == programme_id,
                       PracticeRecord.activity_code.in_(CLIMATE_RESILIENT_ACTIVITIES),
                       (PracticeRecord.action_date.is_(None)) | (PracticeRecord.action_date <= period_end)))
        for key, value in where.items():
            stmt = stmt.where(getattr(PracticeRecord, key) == value)
        return scope.apply(stmt, PracticeRecord.geo_unit_id, PracticeRecord.organisation_id)

    stages = {
        "registered": _count_households(db, farmers),
        "enrolled": _count_households(db, enrolled),
        "reached": _count_households(db, reached),
        "participating": _count_households(db, participating),
        "adopted_reported": _count_households(db, adoption(status="reported")),
        "adopted_observed": _count_households(db, adoption(status="observed", evidence_basis="observed")),
        "adopted_verified": _count_households(db, adoption(status="observed", verification_status="verified")),
    }
    return {"unit": "households", "stages": stages, "definitions": FUNNEL_DEFINITIONS,
            "conservation_households_observed": _count_households(db, scope.apply(
                select(HOUSEHOLD.label("hh")).join(PracticeRecord, PracticeRecord.farmer_id == Farmer.id)
                .where(PracticeRecord.programme_id == programme_id, PracticeRecord.status == "observed",
                       PracticeRecord.activity_code.in_(CONSERVATION_ACTIVITIES)),
                PracticeRecord.geo_unit_id, PracticeRecord.organisation_id))}


def inclusion(db: Session, programme_id: uuid.UUID, scope: Any) -> dict[str, Any]:
    """Aggregate counts per configured beneficiary category (explicit assignments only)."""
    enrolled = (select(Farmer.id).join(ProgrammeParticipation, ProgrammeParticipation.farmer_id == Farmer.id)
                .where(ProgrammeParticipation.programme_id == programme_id, ProgrammeParticipation.status == "enrolled",
                       Farmer.consent_status == "given"))
    ids = set(db.scalars(scope.apply(enrolled, Farmer.geo_unit_id, Farmer.primary_fpo_id)))
    in_ids = Farmer.id.in_(ids or {uuid.UUID(int=0)})
    categories = []
    for category in db.scalars(select(BeneficiaryCategory).where(BeneficiaryCategory.status != "retired")
                               .order_by(BeneficiaryCategory.sort_order, BeneficiaryCategory.name)):
        members = (select(FarmerCategoryAssignment.farmer_id).where(
            FarmerCategoryAssignment.category_id == category.id, FarmerCategoryAssignment.status == "active"))
        member_filter = Farmer.id.in_(members)

        def adopted(member_filter: Any = member_filter, **where: Any) -> int:
            stmt = (select(func.count(func.distinct(Farmer.id))).join(PracticeRecord, PracticeRecord.farmer_id == Farmer.id)
                    .where(in_ids, member_filter))
            for key, value in where.items():
                stmt = stmt.where(getattr(PracticeRecord, key) == value)
            return db.scalar(stmt) or 0

        categories.append({
            "code": category.code, "name": category.name, "definition_status": category.status,
            "sensitive": category.is_sensitive,
            "enrolled_members": db.scalar(select(func.count()).select_from(Farmer).where(in_ids, member_filter)) or 0,
            "enrolled_households": db.scalar(select(func.count(func.distinct(HOUSEHOLD))).where(in_ids, member_filter)) or 0,
            "members_with_observed_adoption": adopted(status="observed"),
            "members_with_verified_adoption": adopted(verification_status="verified"),
        })
    crps = scope.apply(select(CommunityResourcePerson.status, CommunityResourcePerson.gender, func.count())
                       .where(CommunityResourcePerson.programme_id == programme_id)
                       .group_by(CommunityResourcePerson.status, CommunityResourcePerson.gender),
                       CommunityResourcePerson.geo_unit_id, CommunityResourcePerson.organisation_id)
    by_status: dict[str, int] = {}
    trained = women_trained = 0
    for status, gender, n in db.execute(crps).all():
        by_status[status] = by_status.get(status, 0) + n
        if status in ("trained", "active"):
            trained += n
            women_trained += n if gender == "female" else 0
    return {
        "enrolled_farmers": len(ids),
        "categories": categories,
        "community_resource_persons": {"by_status": by_status, "trained": trained, "women_trained": women_trained},
        "note": "Aggregate counts of explicitly assigned beneficiary categories; category definitions show their approval "
                "status. Training or advisory receipt is not counted as adoption.",
    }


def demonstrations(db: Session, programme_id: uuid.UUID, scope: Any, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    sites = list(db.scalars(scope.apply(select(DemonstrationSite).where(DemonstrationSite.programme_id == programme_id)
                                        .order_by(DemonstrationSite.category, DemonstrationSite.site_code),
                                        DemonstrationSite.geo_unit_id, DemonstrationSite.organisation_id)).unique())
    visits = {row[0]: row for row in db.execute(
        select(FieldRecord.demonstration_site_id, func.count(), func.max(FieldRecord.observed_at))
        .where(FieldRecord.demonstration_site_id.in_([s.id for s in sites] or [uuid.UUID(int=0)]),
               FieldRecord.validation_status == "valid", FieldRecord.observed_at.between(start_dt, end_dt))
        .group_by(FieldRecord.demonstration_site_id))}
    summary: dict[str, dict[str, int]] = {}
    for s in sites:
        summary.setdefault(s.category, {}).setdefault(s.status, 0)
        summary[s.category][s.status] += 1
    return {
        "by_category_and_status": summary,
        "sites": [{"site_code": s.site_code, "name": s.name, "category": s.category, "status": s.status,
                   "area": s.geo_unit.label, "practices": s.practices,
                   "visits_in_period": visits[s.id][1] if s.id in visits else 0,
                   "last_visit": visits[s.id][2].isoformat() if s.id in visits and visits[s.id][2] else None,
                   "is_demo": s.is_demo} for s in sites],
    }


def surveillance(db: Session, programme_id: uuid.UUID, scope: Any, start_dt: datetime, end_dt: datetime,
                 period_start: date, period_end: date) -> dict[str, Any]:
    clusters = list(db.scalars(scope.apply(select(PestSurveillanceCluster).where(PestSurveillanceCluster.programme_id == programme_id)
                                           .order_by(PestSurveillanceCluster.cluster_code),
                                           PestSurveillanceCluster.geo_unit_id, PestSurveillanceCluster.organisation_id)).unique())
    ids = [c.id for c in clusters] or [uuid.UUID(int=0)]
    members = dict(db.execute(select(ClusterMembership.cluster_id, func.count(func.distinct(HOUSEHOLD)))
                              .join(Farmer, Farmer.id == ClusterMembership.farmer_id)
                              .where(ClusterMembership.cluster_id.in_(ids), ClusterMembership.status == "active")
                              .group_by(ClusterMembership.cluster_id)).all())
    inspections = list(db.scalars(select(FieldRecord).where(
        FieldRecord.surveillance_cluster_id.in_(ids), FieldRecord.validation_status == "valid",
        FieldRecord.observed_at.between(start_dt, end_dt))).unique())
    responses = db.scalar(scope.apply(select(func.count()).select_from(PracticeRecord).where(
        PracticeRecord.programme_id == programme_id, PracticeRecord.activity_code.in_(["PEST_TREATMENT", "PEST_PREVENTION"]),
        PracticeRecord.action_date.between(period_start, period_end)), PracticeRecord.geo_unit_id, PracticeRecord.organisation_id)) or 0
    by_cluster: dict[uuid.UUID, list[FieldRecord]] = {}
    for rec in inspections:
        by_cluster.setdefault(rec.surveillance_cluster_id, []).append(rec)
    return {
        "clusters_by_status": {s: sum(1 for c in clusters if c.status == s) for s in {c.status for c in clusters}},
        "participating_households": db.scalar(select(func.count(func.distinct(HOUSEHOLD))).select_from(ClusterMembership)
                                              .join(Farmer, Farmer.id == ClusterMembership.farmer_id)
                                              .where(ClusterMembership.cluster_id.in_(ids), ClusterMembership.status == "active")) or 0,
        "inspections_in_period": len(inspections),
        "inspections_with_incidence": sum(1 for r in inspections if (r.value or 0) > 0),
        "follow_up_actions_in_period": responses,
        "clusters": [{"cluster_code": c.cluster_code, "name": c.name, "status": c.status, "area": c.geo_unit.label,
                      "member_households": members.get(c.id, 0), "inspections_in_period": len(by_cluster.get(c.id, [])),
                      "is_demo": c.is_demo} for c in clusters],
        "note": "Predictions are risk estimates; inspections record observed incidence. Follow-up actions are practice "
                "records for pest treatment/prevention.",
    }


def verification(db: Session, programme_id: uuid.UUID) -> dict[str, Any]:
    samples = list(db.scalars(select(VerificationSample).where(VerificationSample.programme_id == programme_id)
                              .order_by(VerificationSample.created_at)))
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for sid, result, n in db.execute(select(VerificationSampleItem.sample_id, VerificationSampleItem.result, func.count())
                                     .where(VerificationSampleItem.sample_id.in_([s.id for s in samples] or [uuid.UUID(int=0)]))
                                     .group_by(VerificationSampleItem.sample_id, VerificationSampleItem.result)):
        counts.setdefault(sid, {})[result] = n
    return {"samples": [{"title": s.title, "entity_type": s.entity_type, "selection_method": s.selection_method,
                         "frame_size": s.frame_size, "sample_size": s.sample_size, "random_seed": s.random_seed,
                         "status": s.status, "results": counts.get(s.id, {}), "conclusion": s.conclusion,
                         "conclusion_note": s.conclusion_note, "is_demo": s.is_demo} for s in samples]}
