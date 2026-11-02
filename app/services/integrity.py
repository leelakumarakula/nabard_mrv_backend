"""Read-only data-consistency checks across the registry, MRV results and verification records.

Run against any database before reporting:  python -m app.services.integrity [--programme CODE]
Checks never change data. Each returns a status (ok | warning | error), a count and up to 10 example references.
"warning" marks items for a person to review (e.g. possible duplicates); "error" marks broken rules.
"""

import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    CalculationRun,
    CalculationRunInput,
    Farm,
    Farmer,
    FarmPlot,
    FieldRecord,
    GeoUnit,
    IndicatorDefinition,
    IndicatorResult,
    MonitoringRecord,
    PracticeRecord,
    Programme,
    ReviewTask,
    VerificationDecision,
    VerificationSampleItem,
)
from app.services.calculations import METHODS, CalcContext
from app.services.mrv import REVIEWABLE, resolve_scope
from app.services.units import area_in_ha

MAX_EXAMPLES = 10


@dataclass
class Check:
    code: str
    title: str
    severity: str  # severity when the check finds something: warning | error
    found: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.found else self.severity

    def as_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "status": self.status, "count": len(self.found),
                "examples": self.found[:MAX_EXAMPLES]}


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _district_of(db: Session) -> dict[uuid.UUID, uuid.UUID | None]:
    units = {g.id: g for g in db.scalars(select(GeoUnit))}

    def walk(gid: uuid.UUID | None) -> uuid.UUID | None:
        while gid is not None and units[gid].unit_type not in ("district", "state"):
            gid = units[gid].parent_id
        return gid
    return {gid: walk(gid) for gid in units}


def duplicate_farmers(db: Session) -> list[Check]:
    farmers = list(db.scalars(select(Farmer).where(Farmer.is_archived == False)))  # noqa: E712
    by_phone, by_household = defaultdict(list), defaultdict(list)
    for f in farmers:
        if f.phone:
            by_phone[(_norm(f.full_name), "".join(ch for ch in f.phone if ch.isdigit())[-10:])].append(f.farmer_code)
        if f.household_code:
            by_household[(_norm(f.full_name), f.household_code)].append(f.farmer_code)
    same_phone = Check("possible_duplicate_farmers_phone", "Same name and phone registered more than once", "warning",
                       [" / ".join(codes) for codes in by_phone.values() if len(codes) > 1])
    same_household = Check("possible_duplicate_farmers_household", "Same name registered twice in one household", "warning",
                           [" / ".join(codes) for codes in by_household.values() if len(codes) > 1])

    district = _district_of(db)
    households = defaultdict(set)
    for f in farmers:
        if f.household_code:
            households[f.household_code].add(district.get(f.geo_unit_id))
    split = Check("household_split_across_districts", "Household code used in more than one district", "warning",
                  [code for code, ds in households.items() if len(ds) > 1])
    return [same_phone, same_household, split]


def farm_plot_relationships(db: Session) -> list[Check]:
    archived_parent = Check("active_child_of_archived_parent", "Active farm/plot under an archived farmer/farm", "error")
    for farm in db.scalars(select(Farm).join(Farmer, Farmer.id == Farm.farmer_id)
                           .where(Farm.is_archived == False, Farmer.is_archived == True)):  # noqa: E712
        archived_parent.found.append(f"farm {farm.farm_code}")
    for plot in db.scalars(select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id)
                           .where(FarmPlot.is_archived == False, Farm.is_archived == True)):  # noqa: E712
        archived_parent.found.append(f"plot {plot.plot_code}")

    area = Check("plot_area_exceeds_farm_area", "Plot areas add up to more than the farm's total area", "warning")
    plots = defaultdict(list)
    for p in db.scalars(select(FarmPlot).where(FarmPlot.is_archived == False)):  # noqa: E712
        plots[p.farm_id].append(p)
    for farm in db.scalars(select(Farm).where(Farm.total_area.is_not(None))):
        total = sum((area_in_ha(p.area, p.area_unit) for p in plots[farm.id] if p.area is not None), Decimal(0))
        if total > area_in_ha(farm.total_area, farm.area_unit) * Decimal("1.001"):
            area.found.append(f"{farm.farm_code}: plots {total:.2f} ha > farm {area_in_ha(farm.total_area, farm.area_unit):.2f} ha")

    owner = {p: f for p, f in db.execute(select(FarmPlot.id, Farm.farmer_id).join(Farm, Farm.id == FarmPlot.farm_id))}
    mismatch = Check("record_plot_farmer_mismatch", "Record's plot belongs to a different farmer than the record", "error")
    for model, label in ((FieldRecord, "field record"), (MonitoringRecord, "monitoring record"),
                         (PracticeRecord, "practice record")):
        stmt = select(model.id, model.farm_plot_id, model.farmer_id).where(model.farm_plot_id.is_not(None),
                                                                          model.farmer_id.is_not(None))
        for rid, plot_id, farmer_id in db.execute(stmt):
            if owner.get(plot_id) != farmer_id:
                mismatch.found.append(f"{label} {rid}")
    return [archived_parent, area, mismatch]


def indicator_results(db: Session, programme_id: uuid.UUID | None) -> list[Check]:
    stmt = select(IndicatorResult).where(IndicatorResult.calculation_status == "calculated",
                                         IndicatorResult.superseded_by_id.is_(None))
    if programme_id:
        stmt = stmt.where(IndicatorResult.programme_id == programme_id)
    results = list(db.scalars(stmt).unique())

    lineage = Check("result_lineage_incomplete", "Result's stored inputs do not match its calculation run", "error")
    changed = Check("result_differs_from_current_sources",
                    "Result no longer matches its source records (sources changed after calculation; recalculate)", "warning")
    provisional = Check("unapproved_result_not_provisional",
                        "Result from an unapproved definition is not marked PROVISIONAL", "error")
    for r in results:
        run = db.get(CalculationRun, r.calculation_run_id)
        indicator = db.get(IndicatorDefinition, r.indicator_id)
        label = f"{indicator.code} {r.period_start}..{r.period_end} ({r.scope_type})"
        stored = db.scalar(select(func.count()).select_from(CalculationRunInput)
                           .where(CalculationRunInput.calculation_run_id == run.id))
        if stored != run.input_count:
            lineage.found.append(f"{label}: run says {run.input_count} inputs, {stored} stored")
        if indicator.status != "approved" and not r.is_provisional:
            provisional.found.append(label)
        method = METHODS.get(run.method_code)
        if method is None:
            continue
        geo_ids, org_id = resolve_scope(db, run.scope_type, run.scope_geo_unit_id, run.scope_organisation_id)
        try:
            out = method.fn(CalcContext(db, indicator, run.programme_id, run.period_start, run.period_end, geo_ids, org_id,
                                        run.parameters or {}))
        except ValueError:
            continue
        now = None if out.value is None else Decimal(out.value)
        then = None if r.value is None else Decimal(r.value)
        if (now is None) != (then is None) or (now is not None and abs(now - then) > Decimal("0.0001")):
            changed.found.append(f"{label}: stored {then}, sources now give {now}")
    return [lineage, provisional, changed]


def verification_history(db: Session) -> list[Check]:
    decisions = list(db.scalars(select(VerificationDecision)))
    tasks = {t.id: t for t in db.scalars(select(ReviewTask))}

    missing_audit = Check("decision_without_audit_event", "Verification decision has no audit event", "error")
    independence = Check("decision_not_independent", "Decision made by the submitter or the internal reviewer", "error")
    for d in decisions:
        task = tasks[d.review_task_id]
        event = db.scalar(select(AuditEvent.id).where(
            AuditEvent.action == "verification_decision", AuditEvent.entity_type == task.entity_type,
            AuditEvent.entity_id == str(task.entity_id)).limit(1))
        if event is None:
            missing_audit.found.append(f"{task.entity_type} {task.entity_id}")
        if d.decided_by_id in (task.submitted_by_id, task.reviewed_by_id):
            independence.found.append(f"{task.entity_type} {task.entity_id}")

    unexplained = Check("verified_without_decision", "Record marked verified/not verified without a decision on file", "error")
    decided = {(tasks[d.review_task_id].entity_type, tasks[d.review_task_id].entity_id) for d in decisions}
    for entity_type, model in REVIEWABLE.items():
        for rid in db.scalars(select(model.id).where(model.verification_status != "unverified")):
            if (entity_type, rid) not in decided:
                unexplained.found.append(f"{entity_type} {rid}")
    sampled = set(db.scalars(select(VerificationSampleItem.entity_id).where(
        VerificationSampleItem.entity_type == "practice_record", VerificationSampleItem.result != "pending")))
    for pid in db.scalars(select(PracticeRecord.id).where(PracticeRecord.verification_status != "unverified")):
        if pid not in sampled:
            unexplained.found.append(f"practice_record {pid}")
    return [missing_audit, independence, unexplained]


def run_checks(db: Session, programme_id: uuid.UUID | None = None) -> list[Check]:
    return [*duplicate_farmers(db), *farm_plot_relationships(db), *indicator_results(db, programme_id),
            *verification_history(db)]


def main(argv: list[str]) -> int:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        programme_id = None
        if "--programme" in argv:
            code = argv[argv.index("--programme") + 1]
            programme_id = db.scalar(select(Programme.id).where(Programme.code == code))
            if programme_id is None:
                print(f"Programme {code} not found")
                return 2
        checks = run_checks(db, programme_id)
        db.rollback()  # read-only
    for c in checks:
        print(f"[{c.status.upper():7}] {c.title} ({len(c.found)})")
        for example in c.found[:MAX_EXAMPLES]:
            print(f"            - {example}")
    return 1 if any(c.status == "error" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
