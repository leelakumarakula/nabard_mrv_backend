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
     "Which FarmFuture capabilities exist, who owns them, and what are their API contracts and credentials?",
     "Placeholder sources registered; mock adapters clearly labelled; no live data.", ["Integrations"]),
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
    added = 0
    for code, category, title, question, provisional, affects in DECISIONS:
        if db.scalar(select(ProgrammeDecision.id).where(ProgrammeDecision.code == code)) is None:
            decided = DECIDED.get(code)
            db.add(ProgrammeDecision(programme_id=programme_id, code=code, category=category, title=title, question=question,
                                     provisional_treatment=provisional, affects=affects, owner="Programme owner (NABARD / Varsapradaya)",
                                     status="decided" if decided else "pending", **(decided or {})))
            added += 1
    db.flush()
    return added
