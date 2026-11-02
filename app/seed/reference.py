"""Reference data needed in every environment: roles, permissions, settings, workstreams and activities."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import DEFAULT_ROLE_PERMISSIONS, DEFAULT_ROLES
from app.models import Activity, BeneficiaryCategory, ReportTemplate, Role, RolePermission, SystemSetting, Workstream
from app.schemas.config import REPORT_SECTIONS
from app.services.record_types import ACTIVITIES, MODULES
from app.services.settings import DEFAULT_SETTINGS


def seed_reference(db: Session, reset_role_permissions: bool = False) -> None:
    for code, (name, description) in DEFAULT_ROLES.items():
        role = db.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name, description=description, is_system=True)
            db.add(role)
            db.flush()
            reset = True
        else:
            role.name, role.description = name, description
            reset = reset_role_permissions
        if reset:
            role.permissions = [RolePermission(permission_code=p) for p in sorted(set(DEFAULT_ROLE_PERMISSIONS[code]))]

    for key, (value, description) in DEFAULT_SETTINGS.items():
        if db.get(SystemSetting, key) is None:
            db.add(SystemSetting(key=key, value=value, description=description, requires_confirmation=True))

    workstreams = {}
    for m in MODULES:
        ws = db.scalar(select(Workstream).where(Workstream.module_code == m.code))
        if ws is None:
            ws = Workstream(module_code=m.code, number=m.number, name=m.name, description=m.description)
            db.add(ws)
            db.flush()
        else:
            ws.name, ws.description = m.name, m.description
        workstreams[m.code] = ws
    for module_code, code, name in ACTIVITIES:
        activity = db.scalar(select(Activity).where(Activity.activity_code == code))
        if activity is None:
            db.add(Activity(workstream_id=workstreams[module_code].id, activity_code=code, name=name))
        else:
            activity.workstream_id, activity.name = workstreams[module_code].id, name
    db.flush()
    seed_categories_and_templates(db)


PENDING = "Pending programme-owner approval."
# Categories named by the V2 programme scope (women and tribal-community participation). Definitions stay pending;
# membership is only ever assigned explicitly.
CATEGORIES = [("WOMAN_BENEFICIARY", "Woman beneficiary", False, 10), ("TRIBAL_COMMUNITY", "Tribal community member", True, 20)]
TEMPLATES = [
    ("QUARTERLY_INTERNAL", "Quarterly progress report (internal)", "quarterly", REPORT_SECTIONS, "draft", False),
    ("ANNUAL_INTERNAL", "Annual progress report (internal)", "annual", REPORT_SECTIONS, "draft", False),
    ("NABARD_CCF_ID", "NABARD CCF-ID report", "nabard_ccf_id", REPORT_SECTIONS, "awaiting_official_template", True),
    ("DEMONSTRATION", "Demonstration-site report", "demonstration", ["demonstrations"], "draft", False),
    ("SURVEILLANCE", "Pest-surveillance report", "surveillance", ["surveillance"], "draft", False),
    ("INCLUSION", "Beneficiary & inclusion report", "inclusion", ["inclusion", "funnel"], "draft", False),
    ("VERIFICATION", "Monitoring & verification report", "verification", ["verification", "mrv", "indicator_results"],
     "draft", False),
]


def seed_categories_and_templates(db: Session) -> None:
    for code, name, sensitive, order in CATEGORIES:
        if db.scalar(select(BeneficiaryCategory).where(BeneficiaryCategory.code == code)) is None:
            db.add(BeneficiaryCategory(code=code, name=name, is_sensitive=sensitive, requires_consent=True, status="proposed",
                                       definition=None, approval_note=PENDING, sort_order=order))
    for code, name, base, sections, status, official in TEMPLATES:
        if db.scalar(select(ReportTemplate).where(ReportTemplate.code == code)) is None:
            db.add(ReportTemplate(code=code, name=name, base_report_type=base, sections=list(sections), status=status,
                                  is_official=official,
                                  notes="Official NABARD template not yet supplied – structure follows the platform."
                                  if official else "Internal template; layout configurable."))
    db.flush()
