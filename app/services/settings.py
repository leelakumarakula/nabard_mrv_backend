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
    "climate.rainfall_status_bands": (
        [{"code": "extra", "label": "Extra rain", "min_pct": 110},
         {"code": "usual", "label": "Usual rain", "min_pct": 90},
         {"code": "little_less", "label": "Little less rain", "min_pct": 75},
         {"code": "much_less", "label": "Much less rain", "min_pct": 50},
         {"code": "very_much_less", "label": "Very much less rain", "min_pct": 0}],
        "Rainfall status bands: share of the 30-year normal for the same days (min_pct, wettest first). The defaults are "
        "the bands the FarmFuture rain report uses; provisional until the programme approves them (RAINFALL_STATUS_BANDS)."),
    "climate.rainfall_status_min_coverage_pct": (
        80, "Least share of days with a station rain reading (%) for a rainfall status band; below it the status is "
            "'insufficient data' (the rain figures are still shown). Provisional until the programme decides "
            "(RAINFALL_STATUS_BANDS)."),
    "evidence.allowed_types": (
        ["image/jpeg", "image/png", "application/pdf", "text/csv"], "MIME types accepted for evidence uploads."),
}


def get_setting(db: Session, key: str) -> Any:
    setting = db.get(SystemSetting, key)
    if setting is not None:
        return setting.value
    return DEFAULT_SETTINGS[key][0] if key in DEFAULT_SETTINGS else None
