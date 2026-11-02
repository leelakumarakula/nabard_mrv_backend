"""Permission codes and the default role → permission mapping.

Role permissions are stored in the role_permissions table (seeded from DEFAULT_ROLE_PERMISSIONS) so
Platform Super Admins can adjust them without a code change.
"""

# Data scope: users holding this permission see every geography/organisation.
SCOPE_ALL = "scope.all"

PERMISSIONS: dict[str, str] = {
    SCOPE_ALL: "Access records in every geography and organisation",
    "admin.users": "Manage users, role assignments and scopes",
    "admin.roles": "Manage roles and permissions",
    "admin.audit": "View the audit log",
    "admin.settings": "Manage system settings",
    "programme.read": "View programmes, targets and reporting periods",
    "programme.manage": "Create and edit programmes, targets and reporting periods",
    "geo.read": "View the geography hierarchy",
    "geo.manage": "Manage the geography hierarchy",
    "org.read": "View organisations and FPOs",
    "org.manage": "Manage organisations and FPOs",
    "farmer.read": "View farmers, farms and plots",
    "farmer.read_pii": "View farmer contact and precise location details",
    "farmer.manage": "Register and edit farmers, farms, plots and enrolment",
    "module.read": "View workstream records",
    "module.write": "Create and edit workstream records",
    "advisory.read": "View advisories and deliveries",
    "advisory.manage": "Create advisories and record deliveries",
    "indicator.read": "View the indicator catalogue and targets",
    "indicator.manage": "Manage indicator definitions and targets",
    "baseline.read": "View baselines",
    "baseline.manage": "Create and edit baselines",
    "monitoring.read": "View monitoring records",
    "monitoring.write": "Create and submit monitoring records",
    "evidence.read": "View and download evidence",
    "evidence.upload": "Upload and link evidence",
    "evidence.review": "Accept or reject evidence",
    "mrv.read": "View calculation runs, results, reviews and decisions",
    "mrv.calculate": "Run indicator calculations and submit results for review",
    "mrv.review": "Review assigned items, record findings and request corrections",
    "mrv.verify": "Record verification decisions",
    "integration.read": "View integration sources and sync history",
    "integration.manage": "Configure integration sources and mappings, run syncs",
    "integration.replay": "Replay quarantined or failed integration records",
    "report.read": "View reports",
    "report.generate": "Generate reports",
    "report.approve": "Approve and publish reports",
    "self.read": "View own farmer profile and advisories",
    "farmer.read_sensitive": "View sensitive demographic attributes (e.g. tribal community membership)",
    "community.manage": "Manage demonstration sites, pest surveillance clusters and community resource persons",
    "verification.read": "View independent verification samples and results",
    "verification.sample": "Design and draw independent verification samples and record visit results",
}

_READ_ALL = [p for p in PERMISSIONS if p.endswith(".read") and p != "self.read"]

DEFAULT_ROLES: dict[str, tuple[str, str]] = {
    "super_admin": ("Platform Super Admin", "Platform settings, users/roles and integration configuration."),
    "programme_admin": ("Programme Admin", "Programme version, targets, geography, indicators, workstreams, reporting and assignments."),
    "state_coordinator": ("State/District Coordinator",
                          "Assigned geography, implementation, coverage, demonstrations and workstream monitoring."),
    "fpo_admin": ("FPO/Grower Group Coordinator",
                  "Beneficiary onboarding, training, advisory communication, adoption and local coordination."),
    "field_officer": ("Field Officer / Community Resource Person",
                      "Assigned surveys, inspections, demonstrations, pest surveillance, observations and evidence."),
    "mrv_analyst": ("MRV Analyst", "Data-quality exceptions, calculations, baseline comparisons and review-package preparation."),
    "internal_reviewer": ("Internal Reviewer", "Reviews submissions and calculations, requests corrections and records findings."),
    "verifier": ("Independent Verifier",
                 "Reviews assigned samples and records independent findings and decisions; separate from submitter and reviewer."),
    "farmer": ("Farmer/Beneficiary", "Views own permitted information and submits approved responses."),
    "research_partner": ("Research/Partner Read-only", "Approved, appropriately anonymised and scoped information only."),
}

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": list(PERMISSIONS),
    "programme_admin": _READ_ALL + [
        SCOPE_ALL, "programme.manage", "geo.manage", "org.manage", "farmer.manage", "farmer.read_pii", "farmer.read_sensitive",
        "module.write", "advisory.manage", "community.manage", "indicator.manage", "baseline.manage", "report.generate",
        "report.approve", "admin.audit", "admin.users",
    ],
    "state_coordinator": [
        "programme.read", "geo.read", "org.read", "org.manage", "farmer.read", "farmer.read_pii", "farmer.read_sensitive",
        "farmer.manage", "module.read", "module.write", "community.manage", "advisory.read", "advisory.manage",
        "indicator.read", "baseline.read", "monitoring.read", "evidence.read", "mrv.read", "verification.read",
        "report.read", "report.generate",
    ],
    "fpo_admin": [
        "programme.read", "geo.read", "org.read", "farmer.read", "farmer.read_pii", "farmer.read_sensitive", "farmer.manage",
        "module.read", "module.write", "community.manage", "advisory.read", "advisory.manage", "indicator.read",
        "monitoring.read", "evidence.read", "evidence.upload", "report.read",
    ],
    "field_officer": [
        "programme.read", "geo.read", "org.read", "farmer.read", "farmer.read_pii", "module.read", "module.write",
        "advisory.read", "indicator.read", "baseline.read", "monitoring.read", "monitoring.write",
        "evidence.read", "evidence.upload",
    ],
    "mrv_analyst": _READ_ALL + [
        SCOPE_ALL, "baseline.manage", "monitoring.write", "evidence.upload", "mrv.calculate", "report.generate",
    ],
    "internal_reviewer": _READ_ALL + [SCOPE_ALL, "evidence.review", "mrv.review"],
    "verifier": _READ_ALL + [SCOPE_ALL, "evidence.review", "mrv.verify", "verification.sample", "evidence.upload"],
    "farmer": ["self.read"],
    "research_partner": ["programme.read", "geo.read", "indicator.read", "mrv.read", "report.read"],
}
