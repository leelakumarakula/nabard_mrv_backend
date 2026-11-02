"""Versioned indicator calculation methods.

These are generic aggregation mechanics (sum, count, distinct farmers, rates). They are NOT approved programme
methodologies: each indicator states its method_reference and remains `requires_confirmation` until the programme
owner approves the method. Every run stores the method code/version, parameters and input record references.
"""

import ast
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, false, select, true
from sqlalchemy.orm import Session

from app.models import (
    Advisory,
    AdvisoryDelivery,
    BeneficiaryCategory,
    ClusterMembership,
    CommunityResourcePerson,
    DemonstrationSite,
    EvidenceLink,
    Farm,
    Farmer,
    FarmerCategoryAssignment,
    FarmPlot,
    FieldRecord,
    FieldRecordParticipant,
    IndicatorDefinition,
    MonitoringRecord,
    PestSurveillanceCluster,
    PracticeRecord,
    ProgrammeParticipation,
)
from app.services.record_types import CLIMATE_RESILIENT_ACTIVITIES
from app.services.units import area_in_ha


@dataclass
class CalcContext:
    db: Session
    indicator: IndicatorDefinition
    programme_id: uuid.UUID
    period_start: date
    period_end: date
    geo_ids: set[uuid.UUID] | None  # None = whole programme
    organisation_id: uuid.UUID | None
    params: dict[str, Any]

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.period_start, time.min, tzinfo=UTC)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.period_end, time.max, tzinfo=UTC)

    def scoped(self, stmt: Select, geo_col: Any, org_col: Any) -> Select:
        if self.geo_ids is not None:
            stmt = stmt.where(geo_col.in_(self.geo_ids or {uuid.UUID(int=0)}))
        if self.organisation_id is not None:
            stmt = stmt.where(org_col == self.organisation_id)
        return stmt


@dataclass
class CalcOutput:
    value: Decimal | None
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    inputs: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Method:
    code: str
    version: str
    name: str
    description: str
    parameters: dict[str, str]
    fn: Callable[[CalcContext], CalcOutput]


def _valid_monitoring(ctx: CalcContext) -> list[MonitoringRecord]:
    indicator_ids = select(IndicatorDefinition.id).where(IndicatorDefinition.code == ctx.indicator.code,
                                                         IndicatorDefinition.unit == ctx.indicator.unit)
    stmt = select(MonitoringRecord).where(
        MonitoringRecord.programme_id == ctx.programme_id,
        MonitoringRecord.indicator_id.in_(indicator_ids),
        MonitoringRecord.validation_status == "valid",
        MonitoringRecord.observed_at >= ctx.start_dt,
        MonitoringRecord.observed_at <= ctx.end_dt,
    )
    if ctx.params.get("require_verified"):
        stmt = stmt.where(MonitoringRecord.verification_status == "verified")
    stmt = ctx.scoped(stmt, MonitoringRecord.geo_unit_id, MonitoringRecord.organisation_id)
    return list(ctx.db.scalars(stmt).unique())


def monitoring_sum(ctx: CalcContext) -> CalcOutput:
    recs = _valid_monitoring(ctx)
    return CalcOutput(sum((Decimal(r.value) for r in recs), Decimal(0)), inputs=[("monitoring_record", r.id) for r in recs],
                      details={"records": len(recs)})


def monitoring_mean(ctx: CalcContext) -> CalcOutput:
    recs = _valid_monitoring(ctx)
    total = sum((Decimal(r.value) for r in recs), Decimal(0))
    value = (total / len(recs)) if recs else None
    return CalcOutput(value, numerator=total, denominator=Decimal(len(recs)),
                      inputs=[("monitoring_record", r.id) for r in recs], details={"records": len(recs)})


def monitoring_count(ctx: CalcContext) -> CalcOutput:
    recs = _valid_monitoring(ctx)
    return CalcOutput(Decimal(len(recs)), inputs=[("monitoring_record", r.id) for r in recs])


def _enrolled_farmers(ctx: CalcContext) -> list[tuple[uuid.UUID, uuid.UUID]]:
    stmt = (select(ProgrammeParticipation.id, Farmer.id).join(Farmer, Farmer.id == ProgrammeParticipation.farmer_id)
            .where(ProgrammeParticipation.programme_id == ctx.programme_id,
                   ProgrammeParticipation.status == "enrolled",
                   Farmer.is_archived == false(),
                   (ProgrammeParticipation.enrolled_on.is_(None)) | (ProgrammeParticipation.enrolled_on <= ctx.period_end)))
    if ctx.params.get("require_consent", True):
        stmt = stmt.where(Farmer.consent_status == "given")
    stmt = ctx.scoped(stmt, Farmer.geo_unit_id, Farmer.primary_fpo_id)
    return list(ctx.db.execute(stmt).all())


def enrolled_farmers(ctx: CalcContext) -> CalcOutput:
    rows = _enrolled_farmers(ctx)
    farmer_ids = {f for _, f in rows}
    return CalcOutput(Decimal(len(farmer_ids)), inputs=[("programme_participation", p) for p, _ in rows],
                      details={"distinct_farmers": len(farmer_ids)})


def enrolled_area_ha(ctx: CalcContext) -> CalcOutput:
    farmer_ids = {f for _, f in _enrolled_farmers(ctx)}
    plots = list(ctx.db.execute(select(FarmPlot.id, FarmPlot.area, FarmPlot.area_unit).join(Farm, Farm.id == FarmPlot.farm_id)
                                .where(Farm.farmer_id.in_(farmer_ids or {uuid.UUID(int=0)}), FarmPlot.is_archived == false())))
    total = sum((area_in_ha(a, u) for _, a, u in plots), Decimal(0))
    missing = sum(1 for _, a, _ in plots if a is None)
    return CalcOutput(round(total, 4), inputs=[("farm_plot", p) for p, _, _ in plots],
                      details={"plots": len(plots), "plots_without_area": missing})


def participating_fpos(ctx: CalcContext) -> CalcOutput:
    farmer_ids = {f for _, f in _enrolled_farmers(ctx)}
    fpos = set(ctx.db.scalars(select(Farmer.primary_fpo_id).where(Farmer.id.in_(farmer_ids or {uuid.UUID(int=0)}),
                                                                 Farmer.primary_fpo_id.is_not(None))))
    return CalcOutput(Decimal(len(fpos)), inputs=[("organisation", o) for o in fpos])


def _deliveries(ctx: CalcContext) -> list[AdvisoryDelivery]:
    stmt = (select(AdvisoryDelivery).join(Advisory, Advisory.id == AdvisoryDelivery.advisory_id)
            .where(Advisory.programme_id == ctx.programme_id, AdvisoryDelivery.farmer_id.is_not(None),
                   AdvisoryDelivery.delivered_at >= ctx.start_dt, AdvisoryDelivery.delivered_at <= ctx.end_dt))
    if ctx.params.get("advisory_type"):
        stmt = stmt.where(Advisory.advisory_type == ctx.params["advisory_type"])
    if ctx.params.get("origin_module"):
        stmt = stmt.where(Advisory.origin_module == ctx.params["origin_module"])
    stmt = ctx.scoped(stmt, AdvisoryDelivery.geo_unit_id, AdvisoryDelivery.organisation_id)
    return list(ctx.db.scalars(stmt).unique())


def farmers_reached(ctx: CalcContext) -> CalcOutput:
    deliveries = _deliveries(ctx)
    farmers = {d.farmer_id for d in deliveries}
    return CalcOutput(Decimal(len(farmers)), inputs=[("advisory_delivery", d.id) for d in deliveries],
                      details={"deliveries": len(deliveries)})


def advisory_response_rate(ctx: CalcContext) -> CalcOutput:
    deliveries = _deliveries(ctx)
    responded = [d for d in deliveries if d.response or d.understood is not None]
    denominator = Decimal(len(deliveries))
    value = (Decimal(len(responded)) * 100 / denominator).quantize(Decimal("0.01")) if deliveries else None
    return CalcOutput(value, numerator=Decimal(len(responded)), denominator=denominator,
                      inputs=[("advisory_delivery", d.id) for d in deliveries])


def observed_practice_adoption(ctx: CalcContext) -> CalcOutput:
    stmt = select(PracticeRecord).where(
        PracticeRecord.programme_id == ctx.programme_id, PracticeRecord.status == "observed",
        PracticeRecord.evidence_basis == "observed", PracticeRecord.action_date >= ctx.period_start,
        PracticeRecord.action_date <= ctx.period_end)
    codes = ctx.params.get("activity_codes")
    if codes:
        stmt = stmt.where(PracticeRecord.activity_code.in_(codes))
    stmt = ctx.scoped(stmt, PracticeRecord.geo_unit_id, PracticeRecord.organisation_id)
    recs = list(ctx.db.scalars(stmt).unique())
    farmers = {r.farmer_id for r in recs}
    return CalcOutput(Decimal(len(farmers)), inputs=[("practice_record", r.id) for r in recs],
                      details={"observed_practice_records": len(recs)})


def _field_records(ctx: CalcContext) -> list[FieldRecord]:
    stmt = select(FieldRecord).where(
        FieldRecord.programme_id == ctx.programme_id, FieldRecord.validation_status == "valid",
        FieldRecord.observed_at >= ctx.start_dt, FieldRecord.observed_at <= ctx.end_dt)
    if ctx.params.get("record_type"):
        stmt = stmt.where(FieldRecord.record_type == ctx.params["record_type"])
    stmt = ctx.scoped(stmt, FieldRecord.geo_unit_id, FieldRecord.organisation_id)
    return list(ctx.db.scalars(stmt).unique())


def field_record_count(ctx: CalcContext) -> CalcOutput:
    recs = _field_records(ctx)
    return CalcOutput(Decimal(len(recs)), inputs=[("field_record", r.id) for r in recs])


def training_participants(ctx: CalcContext) -> CalcOutput:
    recs = [r for r in _field_records(ctx) if r.record_type in ("training_session", "demonstration")]
    ids = [r.id for r in recs] or [uuid.UUID(int=0)]
    participants = list(ctx.db.scalars(select(FieldRecordParticipant).where(
        FieldRecordParticipant.field_record_id.in_(ids), FieldRecordParticipant.attended == true())).unique())
    farmers = {p.farmer_id for p in participants}
    return CalcOutput(Decimal(len(farmers)), inputs=[("field_record", r.id) for r in recs],
                      details={"sessions": len(recs), "attendance_records": len(participants)})


# -- Version 2.0 programme methods ---------------------------------------------------------------------------------

def household_key(farmer: Farmer) -> str:
    """Households are identified by household_code; a farmer without one counts as their own household."""
    return farmer.household_code or farmer.farmer_code


def _adoption_records(ctx: CalcContext) -> list[tuple[PracticeRecord, Farmer]]:
    """Field-observed adoption (never advisory delivery, training attendance or self-report alone)."""
    codes = ctx.params.get("activity_codes") or CLIMATE_RESILIENT_ACTIVITIES
    stmt = (select(PracticeRecord, Farmer).join(Farmer, Farmer.id == PracticeRecord.farmer_id).where(
        PracticeRecord.programme_id == ctx.programme_id, PracticeRecord.activity_code.in_(codes),
        PracticeRecord.status == "observed", PracticeRecord.evidence_basis == "observed",
        PracticeRecord.action_date <= ctx.period_end, Farmer.is_archived == false()))
    if not ctx.params.get("cumulative", True):
        stmt = stmt.where(PracticeRecord.action_date >= ctx.period_start)
    if ctx.params.get("require_verified"):
        stmt = stmt.where(PracticeRecord.verification_status == "verified")
    if ctx.params.get("require_evidence"):
        stmt = stmt.where(PracticeRecord.id.in_(select(EvidenceLink.entity_id).where(
            EvidenceLink.entity_type == "practice_record", EvidenceLink.review_status == "accepted")))
    if ctx.params.get("category_code"):
        stmt = stmt.where(Farmer.id.in_(_category_members(ctx, ctx.params["category_code"])))
    stmt = ctx.scoped(stmt, PracticeRecord.geo_unit_id, PracticeRecord.organisation_id)
    return list(ctx.db.execute(stmt).unique().all())


def households_adopting(ctx: CalcContext) -> CalcOutput:
    rows = _adoption_records(ctx)
    households = {household_key(f) for _, f in rows}
    return CalcOutput(Decimal(len(households)), inputs=[("practice_record", r.id) for r, _ in rows],
                      details={"practice_records": len(rows), "farmers": len({f.id for _, f in rows})})


def _category_members(ctx: CalcContext, code: str):
    """Farmers explicitly assigned to a configured beneficiary category (never inferred from other attributes)."""
    return (select(FarmerCategoryAssignment.farmer_id)
            .join(BeneficiaryCategory, BeneficiaryCategory.id == FarmerCategoryAssignment.category_id)
            .where(BeneficiaryCategory.code == code, FarmerCategoryAssignment.status == "active"))


def _require_category(ctx: CalcContext) -> str:
    code = ctx.params.get("category_code")
    if not code:
        raise ValueError("Parameter 'category_code' (a configured beneficiary category) is required")
    return code


def category_adopting(ctx: CalcContext) -> CalcOutput:
    _require_category(ctx)
    rows = _adoption_records(ctx)
    farmers = {f.id for _, f in rows}
    return CalcOutput(Decimal(len(farmers)), inputs=[("practice_record", r.id) for r, _ in rows],
                      details={"practice_records": len(rows)})


def households_in_surveillance(ctx: CalcContext) -> CalcOutput:
    stmt = (select(ClusterMembership, Farmer).join(Farmer, Farmer.id == ClusterMembership.farmer_id)
            .join(PestSurveillanceCluster, PestSurveillanceCluster.id == ClusterMembership.cluster_id)
            .where(PestSurveillanceCluster.programme_id == ctx.programme_id, ClusterMembership.status == "active",
                   (ClusterMembership.joined_on.is_(None)) | (ClusterMembership.joined_on <= ctx.period_end)))
    stmt = ctx.scoped(stmt, PestSurveillanceCluster.geo_unit_id, PestSurveillanceCluster.organisation_id)
    rows = list(ctx.db.execute(stmt).unique().all())
    if ctx.params.get("require_inspection"):
        inspected = set(ctx.db.scalars(select(FieldRecord.farmer_id).where(
            FieldRecord.record_type == "pest_inspection", FieldRecord.validation_status == "valid",
            FieldRecord.surveillance_cluster_id.is_not(None), FieldRecord.observed_at <= ctx.end_dt)))
        rows = [(m, f) for m, f in rows if f.id in inspected]
    households = {household_key(f) for _, f in rows}
    return CalcOutput(Decimal(len(households)), inputs=[("cluster_membership", m.id) for m, _ in rows],
                      details={"memberships": len(rows), "clusters": len({m.cluster_id for m, _ in rows})})


def category_beneficiaries(ctx: CalcContext) -> CalcOutput:
    code = _require_category(ctx)
    farmer_ids = {f for _, f in _enrolled_farmers(ctx)}
    members = set(ctx.db.scalars(_category_members(ctx, code)))
    selected = sorted(farmer_ids & members)
    return CalcOutput(Decimal(len(selected)), inputs=[("farmer", i) for i in selected])


def crps_trained(ctx: CalcContext) -> CalcOutput:
    stmt = select(CommunityResourcePerson).where(
        CommunityResourcePerson.programme_id == ctx.programme_id,
        CommunityResourcePerson.status.in_(["trained", "active"]),
        CommunityResourcePerson.training_completed_on <= ctx.period_end)
    if ctx.params.get("women_only"):
        stmt = stmt.where(CommunityResourcePerson.gender == "female")
    if ctx.params.get("require_competency"):
        stmt = stmt.where(CommunityResourcePerson.competency_result == "competent")
    stmt = ctx.scoped(stmt, CommunityResourcePerson.geo_unit_id, CommunityResourcePerson.organisation_id)
    crps = list(ctx.db.scalars(stmt).unique())
    return CalcOutput(Decimal(len(crps)), inputs=[("community_resource_person", c.id) for c in crps])


def demonstration_sites_active(ctx: CalcContext) -> CalcOutput:
    stmt = select(DemonstrationSite).where(DemonstrationSite.programme_id == ctx.programme_id,
                                           DemonstrationSite.status.in_(["established", "active"]),
                                           (DemonstrationSite.established_on.is_(None))
                                           | (DemonstrationSite.established_on <= ctx.period_end))
    if ctx.params.get("category"):
        stmt = stmt.where(DemonstrationSite.category == ctx.params["category"])
    stmt = ctx.scoped(stmt, DemonstrationSite.geo_unit_id, DemonstrationSite.organisation_id)
    sites = list(ctx.db.scalars(stmt).unique())
    return CalcOutput(Decimal(len(sites)), inputs=[("demonstration_site", s.id) for s in sites])


def surveillance_clusters_active(ctx: CalcContext) -> CalcOutput:
    stmt = select(PestSurveillanceCluster).where(PestSurveillanceCluster.programme_id == ctx.programme_id,
                                                 PestSurveillanceCluster.status == "active")
    stmt = ctx.scoped(stmt, PestSurveillanceCluster.geo_unit_id, PestSurveillanceCluster.organisation_id)
    clusters = list(ctx.db.scalars(stmt).unique())
    return CalcOutput(Decimal(len(clusters)), inputs=[("pest_surveillance_cluster", c.id) for c in clusters])


METHODS: dict[str, Method] = {m.code: m for m in [
    Method("monitoring_sum", "1.0", "Sum of valid monitoring values",
           "Sum of normalised values of valid monitoring records for the indicator in period and scope.",
           {"require_verified": "boolean – only include verified records"}, monitoring_sum),
    Method("monitoring_mean", "1.0", "Mean of valid monitoring values",
           "Arithmetic mean of valid monitoring records for the indicator in period and scope.",
           {"require_verified": "boolean"}, monitoring_mean),
    Method("monitoring_count", "1.0", "Count of valid monitoring records", "Number of valid monitoring records.",
           {"require_verified": "boolean"}, monitoring_count),
    Method("enrolled_farmers", "1.0", "Enrolled farmers (distinct)",
           "Distinct farmers with an 'enrolled' participation and recorded consent, as of period end.",
           {"require_consent": "boolean, default true"}, enrolled_farmers),
    Method("enrolled_area_ha", "1.0", "Plot area of enrolled farmers (ha)",
           "Sum of plot areas (converted to hectares) of enrolled farmers. Plots without area are counted separately.",
           {"require_consent": "boolean, default true"}, enrolled_area_ha),
    Method("participating_fpos", "1.0", "Participating FPOs",
           "Distinct primary FPOs of enrolled farmers.", {}, participating_fpos),
    Method("farmers_reached", "1.0", "Farmers reached by advisories",
           "Distinct farmers with an advisory delivery confirmed in the period. Delivery is not adoption.",
           {"advisory_type": "alert | advisory", "origin_module": "module code"}, farmers_reached),
    Method("advisory_response_rate", "1.0", "Advisory response rate (%)",
           "Deliveries with a recorded farmer response ÷ confirmed deliveries × 100.",
           {"advisory_type": "alert | advisory", "origin_module": "module code"}, advisory_response_rate),
    Method("observed_practice_adoption", "1.0", "Farmers with observed practice adoption",
           "Distinct farmers with practice records observed in the field (not self-reported) in the period.",
           {"activity_codes": "list of activity codes"}, observed_practice_adoption),
    Method("field_record_count", "1.0", "Count of valid workstream records",
           "Number of valid workstream records of a given type in the period.",
           {"record_type": "record type code"}, field_record_count),
    Method("training_participants", "1.0", "Distinct training/demonstration participants",
           "Distinct farmers marked as attending valid training sessions or demonstrations.", {}, training_participants),
    Method("households_adopting", "1.0", "Households with field-observed practice adoption",
           "Unique households (household code) with field-observed adoption of the listed practices up to period end.",
           {"activity_codes": "list of qualifying activity codes", "cumulative": "boolean, default true",
            "require_verified": "only independently verified adoption", "require_evidence": "only with accepted evidence",
            "category_code": "optional – restrict to a beneficiary category"},
           households_adopting),
    Method("category_adopting", "1.0", "Category members with field-observed practice adoption",
           "Unique farmers assigned to the given beneficiary category with field-observed adoption. Training or advisory "
           "receipt is not adoption.",
           {"category_code": "required – configured beneficiary category", "activity_codes": "list",
            "cumulative": "boolean", "require_verified": "boolean", "require_evidence": "boolean"}, category_adopting),
    Method("households_in_surveillance", "1.0", "Households participating in pest surveillance",
           "Unique households with an active pest-surveillance cluster membership at period end.",
           {"require_inspection": "boolean – only households with a valid cluster inspection"}, households_in_surveillance),
    Method("category_beneficiaries", "1.0", "Enrolled beneficiaries in a category",
           "Enrolled farmers (with consent) explicitly assigned to the given beneficiary category.",
           {"category_code": "required – configured beneficiary category"}, category_beneficiaries),
    Method("crps_trained", "1.0", "Community resource persons trained",
           "CRPs with training completed (status trained/active) by period end.",
           {"women_only": "boolean", "require_competency": "boolean"}, crps_trained),
    Method("demonstration_sites_active", "1.0", "Demonstration sites established",
           "Demonstration sites established or active by period end.", {"category": "plantation | soil_moisture"},
           demonstration_sites_active),
    Method("surveillance_clusters_active", "1.0", "Active pest surveillance clusters", "Clusters with status active.", {},
           surveillance_clusters_active),
]}


# -- configurable formula -------------------------------------------------------------------------------------------

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult,
                  ast.Div, ast.USub, ast.UAdd)


def parse_expression(expression: str, names: set[str]) -> ast.Expression:
    """Arithmetic over component names only (+ - * / and parentheses). No calls, attributes or other names."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid formula expression: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Formula may only use numbers, component names and + - * / ( ) – found {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Formula constants must be numbers")
        if isinstance(node, ast.Name) and node.id not in names:
            raise ValueError(f"Formula refers to unknown component '{node.id}'")
    return tree


def _evaluate(node: ast.AST, values: dict[str, Decimal]) -> Decimal | None:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, values)
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, values)
        return None if operand is None else (-operand if isinstance(node.op, ast.USub) else operand)
    left, right = _evaluate(node.left, values), _evaluate(node.right, values)
    if left is None or right is None:
        return None
    if isinstance(node.op, ast.Add):
        return left + right
    if isinstance(node.op, ast.Sub):
        return left - right
    if isinstance(node.op, ast.Mult):
        return left * right
    return None if right == 0 else left / right


def validate_parameters(method_code: str | None, params: dict[str, Any]) -> None:
    """Structural check of indicator method parameters (raises ValueError with a readable message)."""
    if not method_code:
        return
    if method_code not in METHODS:
        raise ValueError(f"Unknown calculation method '{method_code}'")
    if method_code != "configurable_formula":
        return
    components = params.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError("A formula needs 'components': {name: {method, parameters}}")
    for name, spec in components.items():
        if not name.isidentifier():
            raise ValueError(f"Component name '{name}' must be a simple identifier")
        if not isinstance(spec, dict) or spec.get("method") not in METHODS or spec.get("method") == "configurable_formula":
            raise ValueError(f"Component '{name}' must reference an aggregation method")
    parse_expression(str(params.get("expression") or ""), set(components))


def configurable_formula(ctx: CalcContext) -> CalcOutput:
    validate_parameters("configurable_formula", ctx.params)
    values: dict[str, Decimal] = {}
    inputs: list[tuple[str, uuid.UUID]] = []
    details: dict[str, Any] = {}
    for name, spec in ctx.params["components"].items():
        sub = CalcContext(ctx.db, ctx.indicator, ctx.programme_id, ctx.period_start, ctx.period_end, ctx.geo_ids,
                          ctx.organisation_id, spec.get("parameters") or {})
        out = METHODS[spec["method"]].fn(sub)
        if out.value is None:
            details[name] = None
            return CalcOutput(None, inputs=inputs, details={"components": details, "note": f"Component '{name}' has no value"})
        values[name] = Decimal(out.value)
        inputs.extend(out.inputs)
        details[name] = {"method": spec["method"], "value": str(out.value)}
    value = _evaluate(parse_expression(ctx.params["expression"], set(values)), values)
    return CalcOutput(None if value is None else value.quantize(Decimal("0.000001")), inputs=list(dict.fromkeys(inputs)),
                      details={"components": details, "expression": ctx.params["expression"],
                               "note": None if value is not None else "Division by zero – no value"})


METHODS["configurable_formula"] = Method(
    "configurable_formula", "1.0", "Configurable formula",
    "Combines named components – each an aggregation method with parameters – using an arithmetic expression, e.g. "
    "{\"components\": {\"adopted\": {\"method\": \"households_adopting\"}, \"enrolled\": {\"method\": \"enrolled_farmers\"}}, "
    "\"expression\": \"adopted / enrolled * 100\"}. The formula itself is the indicator owner's configuration.",
    {"components": "{name: {method, parameters}}", "expression": "arithmetic using component names"}, configurable_formula)


def describe_methods() -> list[dict[str, Any]]:
    return [{"code": m.code, "version": m.version, "name": m.name, "description": m.description,
             "parameters": m.parameters} for m in METHODS.values()]
