from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemSetting

# Configurable rules seeded on first run. requires_confirmation=True flags values the programme owner must confirm.
DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "mrv.independent_verification_required": (
        True, "The user who submitted a record cannot record its verification decision (separation of duties)."),
    "mrv.allow_conditional_verification": (
        False, "Allow a 'conditional / needs follow-up' verification decision. Depends on programme approval policy."),
    "mrv.block_decision_with_open_major_findings": (
        True, "A record cannot be verified while major or critical findings remain open."),
    "mrv.verifier_independent_of_reviewer": (
        True, "The independent verifier cannot be the internal reviewer who completed the review."),
    "mrv.allow_provisional_calculations": (
        False, "Allow calculations for indicators whose definition is not yet approved. Results are marked PROVISIONAL. "
               "Enable only for testing/demonstration."),
    "integration.allow_exact_code_matching": (
        True, "Resolve inbound plot/device references by exact platform code when no reviewed identity map exists. "
              "Ambiguous references are always quarantined."),
    "reports.endline_methodology_approved": (
        False, "Endline/impact assessment reports are enabled only after the endline methodology is approved."),
    "reports.include_unverified_results": (
        True, "Include unverified indicator results in reports (always labelled with their status)."),
    "demonstration.selection_criteria": (
        [], "Demonstration-site selection criteria [{code, label, required}]. Empty until approved by the programme owner."),
    "surveillance.cluster_criteria": (
        [], "Pest-surveillance cluster criteria [{code, label, required}]. Empty until approved by the programme owner."),
    "verification.visit_checklist": (
        [], "Independent verification visit checklist [{code, label, required}]. Empty until the verifier's methodology "
            "is approved."),
    "evidence.allowed_types": (
        ["image/jpeg", "image/png", "application/pdf", "text/csv"], "MIME types accepted for evidence uploads."),
}


def get_setting(db: Session, key: str) -> Any:
    setting = db.get(SystemSetting, key)
    if setting is not None:
        return setting.value
    return DEFAULT_SETTINGS[key][0] if key in DEFAULT_SETTINGS else None
