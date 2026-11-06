"""Register of programme-owner decisions for the V2 programme (spec §15 and programme-owner instructions of 2 Nov 2026).

These are real open questions, not demo data. Each states the provisional treatment the platform applies meanwhile.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgrammeDecision

DECISIONS = [
    ("SIXTH_STATE", "scope", "Sixth programme state",
     "Is the sixth state Meghalaya (V2 specification text) or Odisha (V2 architecture diagram)?",
     "Odisha configured as in the proposal; the state list is configurable.",
     ["Geography", "State reports", "Dashboard breakdown"]),
    ("MASTER_LISTS", "scope", "District, cluster and FPO/grower-group master lists",
     "Which districts, clusters and FPOs/grower groups are in the programme?",
     "Geography and organisations are configurable; demo placement is synthetic.", ["Geography", "Organisations"]),
    ("INDICATOR_DEFINITIONS", "indicators", "Approved indicator definitions and methods",
     "Approved definitions, eligibility, denominators, baseline methods, evidence and verification rules, frequency.",
     "Indicators are configurable drafts; formulas can be previewed; stored results only for approved definitions "
     "(PROVISIONAL results in demo/test mode only).", ["Indicator catalogue", "Calculations", "Reports"]),
    ("BENEFICIARY_CATEGORIES", "inclusion", "Woman-beneficiary and tribal-community definitions",
     "How are 'woman beneficiary' and 'tribal community' defined, evidenced and recorded?",
     "Categories exist as 'proposed'; membership is assigned manually with a recorded basis – never inferred.",
     ["Beneficiary registry", "Inclusion indicators", "Inclusion report"]),
    ("DEMOGRAPHIC_DATA", "privacy", "Demographic attributes, consent and access",
     "Which demographic attributes may be collected, with what consent, and who may view them?",
     "Demographic fields are configurable; sensitive fields need farmer.read_sensitive and recorded consent.",
     ["Beneficiary registry", "Privacy controls"]),
    ("DEMO_SITE_CRITERIA", "field", "Demonstration-site selection criteria, visit frequency and forms",
     "Which criteria qualify a plantation or soil/moisture demonstration site, and how often are sites visited?",
     "Selection criteria are a configurable checklist (empty until approved); visit form uses the platform record type.",
     ["Demonstration sites"]),
    ("CLUSTER_RULES", "field", "Pest-surveillance cluster definition and membership",
     "How is a cluster defined and which households count as participating?",
     "Cluster criteria configurable; participation counted from active memberships (inspection requirement optional).",
     ["Surveillance clusters", "HH_PEST_SURVEILLANCE indicator"]),
    ("VERIFICATION_METHOD", "verification", "Independent verifier, sampling methodology and checklist",
     "Who is the independent verifier, what sampling approach and evidence checklist apply, and who approves?",
     "Sampling supports simple random (seeded), census and purposive selection; visit checklist configurable.",
     ["Independent verification", "Verified adoption"]),
    ("NABARD_TEMPLATES", "reporting", "Official NABARD CCF-ID report templates",
     "What are the official quarterly/annual NABARD CCF-ID templates and submission requirements?",
     "Internal templates and Excel/PDF exports available; NABARD template marked 'awaiting official template'.",
     ["Reports"]),
    ("ENDLINE_METHOD", "reporting", "Endline / impact assessment methodology",
     "What endline and impact assessment methodology is approved?",
     "Endline report disabled until the methodology is approved (setting reports.endline_methodology_approved).",
     ["Reports"]),
    ("FARMFUTURE_APIS", "integration", "FarmFuture engine availability and API contracts",
     "Which FarmFuture capabilities exist, who owns them, and what are their API contracts and credentials? Is a "
     "Climate Intelligence (drought / heat / excess-rain risk) API planned, with which risk levels and method?",
     "Live read-only connectors for the water engine, soil & microclimate sensors, scouting, satellite, advisories and "
     "weather / forecast (test token). The API has no climate-risk endpoint, so climate risk stays SAMPLE (DEMO).",
     ["Integrations", "Climate module"]),
    ("IOT_SCHEDULE", "integration", "Final IoT equipment schedule and protocols",
     "Exact device models, quantities, protocols, payloads and calibration requirements?",
     "Device capability map and mock gateway are provisional; up to 1,200 devices is a planning ceiling.",
     ["IoT devices", "Soil & microclimate module"]),
    ("DATA_LICENCES", "integration", "Weather, satellite and institutional data access",
     "Access and licences for IMD, satellite, Coffee Board, state department and KVK data?",
     "No external data connected; maps/geospatial layers not started.", ["Climate module", "Maps"]),
    ("PRIVACY_RETENTION", "privacy", "Consent, retention and data-sharing rules",
     "What privacy, consent, data-sharing and retention rules apply?",
     "Consent recorded per farmer; PII and sensitive attributes masked by role; retention policy not yet configured.",
     ["Registry", "Evidence", "Exports"]),
    ("LOGIN_LOCKOUT", "security", "Sign-in lockout policy",
     "After how many failed sign-ins, and for how long, should an account be locked or throttled?",
     "Failed sign-ins are recorded in the audit log; no lockout or throttling is applied yet.",
     ["Sign-in", "Security"]),
    ("FARMFUTURE_SENSOR_UNITS", "integration", "Units and series of FarmFuture sensor data",
     "Which FarmFuture sensor series counts for reporting – the values the FarmFuture app shows, or the raw, calibrated "
     "or lab-anchored series (they differ widely, e.g. N 185 / 331 / 360 / 331 kg/ha for one device and week)? What is "
     "the unit of leaf wetness, and is MicroClime wind speed in m/s (API field name) or km/h (app label)?",
     "N/P/K in kg/ha and EC in µS/cm as labelled in the FarmFuture app (reviewed 6 Nov 2026). The series shown in the "
     "FarmFuture app is imported (source config 'series' = app) and every reading names its series; leaf wetness is a "
     "raw value; wind is stored in m/s per the API field name; rain is the day's station total.",
     ["Soil & microclimate module", "IoT devices"]),
    ("FARMFUTURE_ADVISORY_DELIVERY", "integration", "Do FarmFuture app advisories count as delivered?",
     "When the FarmFuture app shows an advisory on a farmer's farm, does it count as delivered (or read) for the "
     "advisory-reach indicators, and do farmer-recorded app scouting records count as surveillance?",
     "App advisories are recorded as sent only (not delivered or read), so they do not count towards reach; app scouting "
     "is imported as scouting records marked 'FarmFuture app scouting'.",
     ["Climate module", "Pest & disease module", "Indicators"]),
    ("RAINFALL_STATUS_BANDS", "indicators", "Rainfall status bands and drought definition",
     "Which bands classify rain against the 30-year normal (share of usual rain for the same days), which normal and "
     "station data apply, how much missing station data is acceptable, and which status counts as a drought warning?",
     "The platform calculates the share of usual rain per plot (month and year to date) from observed station rain and "
     "FarmFuture's 30-year CHIRPS normals, with FarmFuture's bands: extra ≥110 %, usual 90–110 %, little less 75–90 %, "
     "much less 50–75 %, very much less <50 % (setting climate.rainfall_status_bands). A band is given only when at least 80 % "
     "of the days have a station reading (climate.rainfall_status_min_coverage_pct), otherwise 'insufficient data'; "
     "measured days are shown with every result. No status is treated as a drought warning yet.",
     ["Climate module", "Farm map", "Dashboard"]),
    ("FPO_REGISTRATION_GEOGRAPHY", "scope", "Geography of farmers registered by an FPO coordinator",
     "Must a farmer registered by an FPO/grower-group coordinator be located within that FPO's area?",
     "Allowed when the coordinator's FPO is the farmer's primary FPO; geography pickers offer the FPO's own area.",
     ["Registry"]),
]

# Questions already answered by the proposal submitted to NABARD (the primary scope document).
DECIDED = {
    "SIXTH_STATE": {
        "decision": "Odisha (Koraput). The proposal lists Andhra Pradesh, Odisha, Nagaland, Arunachal Pradesh, Mizoram and "
                    "Manipur; 'Meghalaya' in the V2 specification text was an error.",
        "decided_by": "Proposal submitted to NABARD (29 Oct 2026, §1 and §5.1)",
        "decided_on": date(2026, 10, 29),
    },
}


def seed_programme_decisions(db: Session, programme_id: uuid.UUID | None) -> int:
    """Add missing decisions; refresh the wording of decisions that are still pending (a decided entry is never
    changed – it records what the programme owner decided)."""
    added = 0
    for code, category, title, question, provisional, affects in DECISIONS:
        existing = db.scalar(select(ProgrammeDecision).where(ProgrammeDecision.code == code))
        if existing is not None and existing.status == "pending" and code not in DECIDED:
            existing.title, existing.question, existing.provisional_treatment = title, question, provisional
            existing.affects = affects
        if existing is None:
            decided = DECIDED.get(code)
            db.add(ProgrammeDecision(programme_id=programme_id, code=code, category=category, title=title, question=question,
                                     provisional_treatment=provisional, affects=affects, owner="Programme owner (NABARD / Varsapradaya)",
                                     status="decided" if decided else "pending", **(decided or {})))
            added += 1
    db.flush()
    return added
