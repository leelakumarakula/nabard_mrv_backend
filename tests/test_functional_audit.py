"""Functional audit: one synthetic household through the complete MRV lifecycle, the role × endpoint permission matrix,
record-level scoping, data consistency and report exports."""

import io
import json
import uuid

import openpyxl
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select

from app.main import app
from app.models import Farmer, GeoUnit, Organisation, ProgrammeParticipation, User
from app.seed.demo import PLACEHOLDER_PNG
from app.services.integrity import run_checks
from tests.conftest import EMAILS


def _district(db, geo_id):
    unit = db.get(GeoUnit, geo_id)
    while unit.unit_type != "district":
        unit = db.get(GeoUnit, unit.parent_id)
    return unit


def _ok(r, status=200):
    assert r.status_code == status, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text}"
    return r.json()


def test_complete_mrv_lifecycle_for_new_household(api, db, programme_id) -> None:
    """Programme setup → registration → farm/plot → baseline → intervention → monitoring → evidence → validation →
    calculation → review → independent verification → reporting, with every step read back from the database."""
    pa, fpo, field = api.as_("programme_admin"), api.as_("fpo_admin"), api.as_("field")
    analyst, rev, ver = api.as_("analyst"), api.as_("reviewer"), api.as_("verifier")
    reviewer = db.scalar(select(User).where(User.email == EMAILS["reviewer"]))
    fpo_org = db.get(Organisation, db.scalar(select(User.organisation_id).where(User.email == EMAILS["fpo_admin"])))
    district = _district(db, fpo_org.geo_unit_id)
    assert district.name == "Alluri Sitharama Raju"  # the field officer's district – both act on this household
    pid = str(programme_id)
    registered_before = pa.get("/dashboard").json()["coverage"]["farmers_registered"]

    # 1. Programme setup: reporting period, approved indicator definition, proposed target
    period = _ok(pa.post(f"/programmes/{pid}/reporting-periods", json={
        "code": "AUDIT-2026-P1", "name": "Audit period (synthetic)", "period_type": "custom",
        "start_date": "2026-08-01", "end_date": "2026-10-31"}), 201)
    ind = _ok(pa.post("/indicators", json={
        "code": "AUDIT_SOIL_MOISTURE", "name": "Audit soil moisture (synthetic)", "family": "Soil & microclimate", "unit": "%",
        "definition": "Mean volumetric soil moisture of monitored plots", "method_code": "monitoring_mean"}), 201)
    _ok(pa.patch(f"/indicators/{ind['id']}", json={
        "method_reference": "AUDIT synthetic method note", "eligibility": "Monitored plots", "baseline_method": "Pre-season survey",
        "evidence_requirements": "Probe reading photo", "verification_sample_approach": "Purposive re-reading",
        "validation_rules": {"min": 0, "max": 100}}))
    _ok(pa.post(f"/indicators/{ind['id']}/status", json={"status": "submitted"}))
    ind = _ok(pa.post(f"/indicators/{ind['id']}/status", json={"status": "approved", "requires_confirmation": False}))
    target = _ok(pa.post(f"/programmes/{pid}/targets", json={
        "metric_code": "AUDIT_SM_TARGET", "metric_name": "Audit soil moisture target", "target_value": 30, "unit": "%",
        "indicator_code": "AUDIT_SOIL_MOISTURE", "source_note": "AUDIT synthetic"}), 201)
    assert target["status"] == "proposed"

    # 2. Farmer registration (FPO coordinator), enrolment, explicit category assignment
    farmer_body = {"farmer_code": "AUDIT-F-0001", "full_name": "Audit Synthetic Farmer", "gender": "female",
                   "geo_unit_id": str(fpo_org.geo_unit_id), "primary_fpo_id": str(fpo_org.id), "consent_status": "given",
                   "consent_date": "2026-08-02", "consent_purpose": "MRV monitoring", "household_code": "AUDIT-HH-0001"}
    farmer = _ok(fpo.post("/farmers", json=farmer_body), 201)
    assert fpo.post("/farmers", json=farmer_body).status_code == 409  # duplicate code refused
    _ok(fpo.post(f"/farmers/{farmer['id']}/participations", json={"programme_id": pid, "enrolled_on": "2026-08-02"}), 201)
    # Enrolling again updates the single enrolment instead of duplicating it, and keeps the enrolment date
    again = _ok(fpo.post(f"/farmers/{farmer['id']}/participations", json={"programme_id": pid, "notes": "re-confirmed"}), 201)
    assert again["enrolled_on"] == "2026-08-02"
    assert db.scalar(select(func.count()).select_from(ProgrammeParticipation)
                     .where(ProgrammeParticipation.farmer_id == uuid.UUID(farmer["id"]))) == 1
    category = _ok(fpo.post(f"/farmers/{farmer['id']}/categories", json={"category_code": "WOMAN_BENEFICIARY"}), 201)
    assert category["basis"] == "self_declared"

    # 3. Farm and plot; the plot belongs to the farmer's farm
    farm = _ok(fpo.post(f"/farmers/{farmer['id']}/farms", json={"farm_code": "AUDIT-FARM-0001", "total_area": 1.5}), 201)
    plot = _ok(fpo.post(f"/farms/{farm['id']}/plots", json={"plot_code": "AUDIT-PLOT-0001", "area": 1.0, "crop": "arabica"}), 201)
    _ok(fpo.patch(f"/farms/{farm['id']}", json={"name": "Audit Estate"}))
    detail = _ok(fpo.get(f"/farmers/{farmer['id']}"))
    assert [f["farm_code"] for f in detail["farms"]] == ["AUDIT-FARM-0001"]
    assert [p["plot_code"] for p in detail["farms"][0]["plots"]] == ["AUDIT-PLOT-0001"]
    assert float(detail["total_plot_area_ha"]) == 1.0 and detail["participations"][0]["status"] == "enrolled"

    # 4. Baseline: recorded, validated, reviewed, independently verified
    baseline = _ok(analyst.post("/baselines", json={
        "indicator_id": ind["id"], "programme_id": pid, "scope_type": "district", "geo_unit_id": str(district.id), "value": 18,
        "unit": "%", "period_label": "Pre-season 2026", "method": "Probe survey", "source": "AUDIT synthetic survey"}), 201)
    assert _ok(analyst.post(f"/baselines/{baseline['id']}/submit"))["validation_status"] == "valid"
    btask = _ok(analyst.post(f"/baselines/{baseline['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}))
    _ok(rev.post(f"/mrv/review-tasks/{btask['id']}/mark-reviewed", json={"note": "Survey sheets checked"}))
    _ok(ver.post(f"/mrv/review-tasks/{btask['id']}/decision", json={"decision": "verified", "rationale": "Survey confirmed"}), 201)

    # 5. Field intervention: training with attendance, recommended → observed practice
    training = _ok(fpo.post("/field-records", json={
        "programme_id": pid, "record_type": "training_session", "organisation_id": str(fpo_org.id),
        "observed_at": "2026-08-10T10:00:00+05:30",
        "attributes": {"topic": "Mulching for moisture", "trainer": "AUDIT trainer", "mode": "field"}}), 201)
    attendee = _ok(fpo.post(f"/field-records/{training['id']}/participants", json={"farmer_id": farmer["id"]}), 201)
    submitted = _ok(fpo.post(f"/field-records/{training['id']}/submit"))
    assert submitted["validation_status"] == "valid", submitted["validation_messages"]
    assert fpo.delete(f"/field-record-participants/{attendee['id']}").status_code == 409  # attendance locked once valid
    practice = _ok(fpo.post("/practice-records", json={
        "programme_id": pid, "farmer_id": farmer["id"], "farm_plot_id": plot["id"], "module_code": "water",
        "activity_code": "WATER_MANAGEMENT", "recommended_action": "Mulch basins"}), 201)
    practice = _ok(field.post(f"/practice-records/{practice['id']}/observe",
                              json={"observed_action": "Basins mulched", "action_date": "2026-08-20"}))
    assert (practice["status"], practice["evidence_basis"]) == ("observed", "observed")

    # 6–8. Monitoring data with evidence; validation keeps implausible values out of calculations
    readings = []
    for value, day in ((22, 15), (26, 25)):
        rec = _ok(field.post("/monitoring-records", json={
            "programme_id": pid, "indicator_id": ind["id"], "farm_plot_id": plot["id"], "value": value, "unit": "%",
            "observed_at": f"2026-09-{day}T09:00:00+05:30", "method": "Probe"}), 201)
        assert rec["farmer_id"] == farmer["id"] and rec["geo_unit_id"] == farmer["geo_unit_id"]  # derived from the plot
        assert _ok(field.post(f"/monitoring-records/{rec['id']}/submit"))["validation_status"] == "valid"
        readings.append(rec["id"])
    bad = _ok(field.post("/monitoring-records", json={
        "programme_id": pid, "indicator_id": ind["id"], "farm_plot_id": plot["id"], "value": 250, "unit": "%",
        "observed_at": "2026-09-26T09:00:00+05:30"}), 201)
    assert _ok(field.post(f"/monitoring-records/{bad['id']}/submit"))["validation_status"] == "exception"  # configured range
    photo = _ok(field.post("/evidence", files={"file": ("probe.png", PLACEHOLDER_PNG, "image/png")}, data={
        "entity_type": "monitoring_record", "entity_id": readings[0], "purpose": "probe_photo"}), 201)
    assert _ok(ver.post(f"/evidence/{photo['id']}/review", json={"review_status": "accepted"}))["review_status"] == "accepted"

    # 9. Calculation on the approved definition: not provisional, traceable to exactly the valid readings
    out = _ok(analyst.post("/mrv/calculations", json={
        "indicator_id": ind["id"], "programme_id": pid, "reporting_period_id": period["id"], "scope_type": "district",
        "scope_geo_unit_id": str(district.id)}), 201)
    result = out["result"]
    assert float(result["value"]) == 24 and result["is_provisional"] is False
    assert float(result["baseline_value"]) == 18 and float(result["change_from_baseline"]) == 6
    inputs = _ok(analyst.get(f"/mrv/calculation-runs/{out['run']['id']}/inputs"))
    assert sorted(i["entity_id"] for i in inputs["items"]) == sorted(readings)

    # 10–11. Internal review, then independent verification
    task = _ok(analyst.post(f"/mrv/results/{result['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}))
    _ok(rev.post(f"/mrv/review-tasks/{task['id']}/findings", json={
        "finding_type": "evidence_gap", "severity": "minor", "description": "Second reading has no photo"}), 201)
    _ok(rev.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={"note": "Values match probe sheets"}))
    _ok(ver.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "Re-read on site"}), 201)
    sample = _ok(ver.post("/verification-samples", json={
        "programme_id": pid, "title": "AUDIT purposive check", "entity_type": "practice_record",
        "frame_description": "Audit household practice", "selection_method": "purposive", "sample_size": 1,
        "frame_filter": {"period_start": "2026-08-01", "period_end": "2026-10-31"}}), 201)
    drawn = _ok(ver.post(f"/verification-samples/{sample['id']}/draw", json={"entity_ids": [practice["id"]]}))
    _ok(ver.patch(f"/verification-samples/items/{drawn['items'][0]['id']}",
                  json={"result": "confirmed", "visit_date": "2026-10-05", "observation": "Mulch present"}))
    _ok(ver.post(f"/verification-samples/{sample['id']}/complete",
                 json={"conclusion": "confirmed", "conclusion_note": "Practice confirmed on site"}))
    assert _ok(fpo.get(f"/practice-records/{practice['id']}"))["verification_status"] == "verified"
    sample_history = [h["action"] for h in _ok(ver.get(f"/mrv/entity-history/verification_sample/{sample['id']}"))]
    assert sample_history and sample_history[0] == "create"

    history = [h["action"] for h in _ok(ver.get(f"/mrv/entity-history/indicator_result/{result['id']}"))]
    assert history[0] == "calculate" and "submit_for_review" in history and history[-1] == "verification_decision"

    # 12. Reporting: the verified result, the target still labelled as proposed, identical numbers in every export
    # Reports show results calculated for exactly their scope, so the district result appears in the district report
    report = _ok(pa.post("/reports", json={"programme_id": pid, "report_type": "geography", "reporting_period_id": period["id"],
                                           "period_start": "2026-08-01", "period_end": "2026-10-31", "scope_type": "district",
                                           "scope_geo_unit_id": str(district.id)}), 201)
    content = report["content"]
    row = next(r for r in content["indicator_results"] if r["indicator_code"] == "AUDIT_SOIL_MOISTURE")
    assert (float(row["value"]), row["verification_status"], row["is_provisional"]) == (24, "verified", False)
    planned = next(t for t in content["planning_targets"] if t["metric_code"] == "AUDIT_SM_TARGET")
    assert planned["status"] == "proposed"
    exports = {fmt: pa.get(f"/reports/{report['id']}/export", params={"format": fmt}) for fmt in ("json", "csv", "xlsx", "pdf")}
    assert all(r.status_code == 200 for r in exports.values())
    assert exports["json"].json()["indicator_results"] == content["indicator_results"]
    assert "AUDIT_SOIL_MOISTURE" in exports["csv"].text
    book = openpyxl.load_workbook(io.BytesIO(exports["xlsx"].content))
    cells = {str(c.value) for ws in book.worksheets for r in ws.iter_rows() for c in r if c.value is not None}
    assert "AUDIT_SOIL_MOISTURE" in cells and "verified" in cells
    assert exports["pdf"].content.startswith(b"%PDF")

    # Dashboard and data consistency after the run
    assert pa.get("/dashboard").json()["coverage"]["farmers_registered"] == registered_before + 1
    errors = [c.as_dict() for c in run_checks(db, programme_id) if c.status == "error"]
    assert errors == []


def test_correction_requires_edit_and_revalidation_before_resubmission(api, db, programme_id) -> None:
    field, analyst, rev = api.as_("field"), api.as_("analyst"), api.as_("reviewer")
    reviewer = db.scalar(select(User).where(User.email == EMAILS["reviewer"]))
    ind = next(i for i in analyst.get("/indicators", params={"page_size": 200}).json()["items"]
               if i["code"] == "SOIL_MOISTURE_MEAN")
    plot = _ok(field.get("/plots", params={"page_size": 1}))["items"][0]
    m = _ok(field.post("/monitoring-records", json={"programme_id": str(programme_id), "indicator_id": ind["id"],
                                                    "farm_plot_id": plot["id"], "value": 31, "unit": "%",
                                                    "observed_at": "2026-10-03T09:00:00+05:30"}), 201)
    checked = _ok(field.post(f"/monitoring-records/{m['id']}/submit"))
    assert checked["validation_status"] == "valid", checked["validation_messages"]
    task = _ok(analyst.post(f"/monitoring-records/{m['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}))
    _ok(rev.post(f"/mrv/review-tasks/{task['id']}/request-correction", json={"note": "Wrong probe depth"}))

    # Only someone who may submit the record can resubmit it
    for who in ("researcher", "coordinator", "reviewer", "verifier"):
        assert api.as_(who).post(f"/mrv/review-tasks/{task['id']}/resubmit", json={}).status_code == 403, who
    # The correction is an edit, which resets validation; resubmission needs a fresh validation
    edited = _ok(field.patch(f"/monitoring-records/{m['id']}", json={"value": 29}))
    assert (float(edited["value"]), edited["validation_status"]) == (29, "pending_validation")
    assert analyst.post(f"/mrv/review-tasks/{task['id']}/resubmit", json={}).status_code == 422
    checked = _ok(field.post(f"/monitoring-records/{m['id']}/submit"))
    assert checked["validation_status"] == "valid", checked["validation_messages"]
    assert _ok(analyst.post(f"/mrv/review-tasks/{task['id']}/resubmit", json={}))["status"] == "pending_review"


# -- role × endpoint permission matrix ------------------------------------------------------------------------------

# Routes that intentionally need only a signed-in user (or nothing) rather than a specific permission.
OPEN_ROUTES = {
    ("GET", "/api/v1/health"), ("GET", "/api/v1/health/db"),
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/forgot-password"), ("POST", "/api/v1/auth/reset-password"),
    ("GET", "/api/v1/auth/me"), ("PATCH", "/api/v1/auth/me"), ("POST", "/api/v1/auth/change-password"),
    ("GET", "/api/v1/dashboard"), ("GET", "/api/v1/dashboard/system-info"),
    # Configuration metadata needed by every form; sensitive categories/fields are filtered by permission
    ("GET", "/api/v1/beneficiary-categories"), ("GET", "/api/v1/demographic-fields"),
    ("GET", "/api/v1/configuration/checklists"),
    # Checks mrv.read/module.read and the record's geography inside the handler
    ("GET", "/api/v1/mrv/entity-history/{entity_type}/{entity_id}"),
}
# Permitted roles that are still refused by an additional rule in the handler (documented business rules).
EXTRA_RULES = {
    # Programme-wide data needs access to every geography
    ("POST", "/api/v1/mrv/calculations"), ("POST", "/api/v1/indicators/{indicator_id}/preview"),
    ("GET", "/api/v1/reports/programme"),
}


def _required(route: APIRoute) -> set[str]:
    perms, stack = set(), list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        for cell in getattr(dep.call, "__closure__", None) or ():
            if isinstance(cell.cell_contents, tuple) and all(isinstance(p, str) for p in cell.cell_contents):
                perms |= set(cell.cell_contents)
        stack.extend(dep.dependencies)
    return perms


def _routes():
    for entry in app.routes:
        # FastAPI ≥0.160 wraps included routers; their flattened routes carry path, methods and dependant
        routes = entry.effective_route_contexts() if hasattr(entry, "effective_route_contexts") else [entry]
        for route in routes:
            if isinstance(route, APIRoute | type(route)) and getattr(route, "dependant", None) and route.path.startswith("/api/v1"):
                for method in route.methods:
                    yield method, route


def test_every_route_declares_a_permission_or_is_intentionally_open() -> None:
    undeclared = {(m, r.path) for m, r in _routes() if not _required(r)} - OPEN_ROUTES
    # Integration inbound endpoints authenticate with the source key, and /me needs a linked farmer account
    undeclared = {(m, p) for m, p in undeclared if not p.startswith("/api/v1/integrations/{source_code}/")}
    assert undeclared == set()


@pytest.mark.parametrize("role", [r for r in EMAILS if r != "admin"])
def test_role_permission_matrix(api, role) -> None:
    """Every permission-protected route returns 403 exactly when the role lacks the permission."""
    me = api.as_(role).get("/auth/me").json()
    held = set(me["permissions"])
    user = api.as_(role)
    wrong, checked = [], 0
    for method, route in _routes():
        needed = _required(route)
        if not needed:
            continue
        checked += 1
        path = route.path.removeprefix("/api/v1")
        for param in route.dependant.path_params:
            value = str(uuid.uuid4()) if "id" in param.name else "x"
            path = path.replace("{" + param.name + "}", value)
        r = user.client.request(method, f"/api/v1{path}", headers=user.headers, json={} if method != "GET" else None)
        expected_denied = not needed <= held
        denied = r.status_code == 403
        if denied != expected_denied and not (denied and (method, route.path) in EXTRA_RULES):
            wrong.append(f"{method} {route.path} needs {sorted(needed)} → {r.status_code}")
    assert checked > 180 and wrong == []


def test_scoped_roles_see_only_their_records(api, db) -> None:
    """List endpoints never return records outside a scoped user's geography/organisation."""
    def descendants(root):
        ids, frontier = {root}, {root}
        while frontier:
            frontier = set(db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id.in_(frontier))))
            ids |= frontier
        return {str(i) for i in ids}

    users = {u.email: u for u in db.scalars(select(User))}
    coord = users[EMAILS["coordinator"]]
    field = users[EMAILS["field"]]
    fpo = users[EMAILS["fpo_admin"]]
    scopes = {"coordinator": descendants(coord.scopes[0].geo_unit_id), "field": descendants(field.scopes[0].geo_unit_id)}
    lists = ["/farmers", "/field-records", "/monitoring-records", "/practice-records", "/demonstration-sites",
             "/surveillance-clusters", "/advisory-deliveries"]
    for who, allowed in scopes.items():
        for path in lists:
            r = api.as_(who).get(path, params={"page_size": 200})
            if r.status_code in (403, 404, 405):
                continue
            items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
            outside = [i for i in items if i.get("geo_unit_id") and i["geo_unit_id"] not in allowed]
            assert outside == [], f"{who} {path}: {len(outside)} records outside scope"
    farmers = api.as_("fpo_admin").get("/farmers", params={"page_size": 200}).json()["items"]
    assert farmers and {f["primary_fpo_id"] for f in farmers} == {str(fpo.organisation_id)}
    # Farmers and research partners never see registry data
    for who in ("farmer", "researcher"):
        assert api.as_(who).get("/farmers").status_code == 403
        assert api.as_(who).get("/monitoring-records").status_code == 403
    # Record history follows the record's geography
    outside = next(r for r in api.as_("analyst").get("/field-records", params={"page_size": 200}).json()["items"]
                   if r["geo_unit_id"] not in scopes["field"])
    inside = api.as_("field").get("/field-records", params={"page_size": 1}).json()["items"][0]
    assert api.as_("field").get(f"/mrv/entity-history/field_record/{outside['id']}").status_code == 403
    assert api.as_("field").get(f"/mrv/entity-history/field_record/{inside['id']}").status_code == 200
    # Sensitive configuration is hidden without the permission
    assert all(not f["is_sensitive"] for f in api.as_("field").get("/demographic-fields").json())


# -- data consistency, dashboards and reports -------------------------------------------------------------------------

def test_demo_data_passes_integrity_checks(db, programme_id) -> None:
    problems = [c.as_dict() for c in run_checks(db, programme_id) if c.status != "ok"]
    assert problems == []


def test_dashboard_totals_reconcile_with_records(api, db) -> None:
    dash = api.as_("programme_admin").get("/dashboard").json()
    registered = db.scalar(select(func.count()).select_from(Farmer).where(Farmer.is_archived == False))  # noqa: E712
    assert dash["coverage"]["farmers_registered"] == registered
    enrolled = db.scalar(select(func.count(func.distinct(ProgrammeParticipation.farmer_id)))
                         .join(Farmer, Farmer.id == ProgrammeParticipation.farmer_id)
                         .where(ProgrammeParticipation.status == "enrolled", Farmer.is_archived == False))  # noqa: E712
    assert dash["coverage"]["farmers_enrolled"] == enrolled
    household = func.coalesce(Farmer.household_code, func.cast(Farmer.id, Farmer.farmer_code.type))
    households = db.scalar(select(func.count(func.distinct(household))).where(Farmer.is_archived == False))  # noqa: E712
    stages = dash["funnel"]["stages"]
    assert stages["registered"] == households
    assert stages["enrolled"] <= stages["registered"]
    assert stages["adopted_verified"] <= stages["adopted_observed"] + stages["adopted_verified"]
    # State breakdown adds up to the programme total
    states = api.as_("programme_admin").get("/dashboard/breakdown", params={"by": "state"}).json()
    rows = states["rows"] if isinstance(states, dict) else states
    assert sum(r["households_registered"] for r in rows) == stages["registered"]
    assert sum(r["farmers_enrolled"] for r in rows) == enrolled
    # Targets are never presented as results
    for p in dash["progress"]:
        assert p["target_status"] in ("proposed", "approved", "revised")
        if p["latest_result"] is not None:
            assert "value" in p["latest_result"] and "verification_status" in p["latest_result"]


def test_report_exports_are_consistent(api, programme_id) -> None:
    pa = api.as_("programme_admin")
    report = _ok(pa.post("/reports", json={"programme_id": str(programme_id), "report_type": "quarterly",
                                           "period_start": "2026-10-01", "period_end": "2026-12-31"}), 201)
    content = report["content"]
    assert content["meta"]["period_start"] == "2026-10-01" and content["meta"]["period_end"] == "2026-12-31"
    as_json = pa.get(f"/reports/{report['id']}/export", params={"format": "json"}).json()
    assert json.dumps(as_json, sort_keys=True) == json.dumps(content, sort_keys=True)
    csv = pa.get(f"/reports/{report['id']}/export", params={"format": "csv"}).text
    book = openpyxl.load_workbook(io.BytesIO(pa.get(f"/reports/{report['id']}/export", params={"format": "xlsx"}).content))
    cells = [c.value for ws in book.worksheets for r in ws.iter_rows() for c in r]
    for r in content["indicator_results"]:
        assert r["indicator_code"] in csv and r["indicator_code"] in cells
        assert r["verification_status"] in ("unverified", "verified", "not_verified", "conditional", "rejected")
        line = next(x for x in csv.splitlines() if x.startswith("indicator_results") and r["indicator_code"] in x)
        assert ("PROVISIONAL" in line) == r["is_provisional"]
    if content["indicator_results"]:
        assert "is_provisional" in cells  # Excel/PDF show the flag as its own column
    coverage = content["coverage"]
    assert coverage["farmers_registered"] in cells or str(coverage["farmers_registered"]) in csv
    # Out-of-period results are excluded
    assert all(r["period_end"] >= "2026-10-01" and r["period_start"] <= "2026-12-31" for r in content["indicator_results"])


def test_programme_admin_cannot_take_over_super_admin_accounts(api, db) -> None:
    pa, admin = api.as_("programme_admin"), api.as_("admin")
    super_admin = db.scalar(select(User).where(User.email == EMAILS["admin"]))
    field = db.scalar(select(User).where(User.email == EMAILS["field"]))
    assert pa.post(f"/admin/users/{super_admin.id}/set-password", json={"new_password": "Takeover-12345"}).status_code == 403
    assert pa.patch(f"/admin/users/{super_admin.id}", json={"status": "locked"}).status_code == 403
    assert pa.patch(f"/admin/users/{super_admin.id}", json={"role_codes": ["programme_admin"]}).status_code == 403
    # Ordinary accounts remain manageable by programme admins; super admins manage everyone
    assert pa.post(f"/admin/users/{field.id}/set-password", json={"new_password": "Temporary-12345"}).status_code == 200
    assert admin.post(f"/admin/users/{field.id}/set-password", json={"new_password": "Temporary-67890"}).status_code == 200


def test_fpo_coordinator_can_pick_the_fpo_geography(api, db) -> None:
    """Organisation-scoped users need their FPO's area in geography pickers to register farmers."""
    fpo_org = db.get(Organisation, db.scalar(select(User.organisation_id).where(User.email == EMAILS["fpo_admin"])))
    units = _ok(api.as_("fpo_admin").get("/geo-units"))
    assert str(fpo_org.geo_unit_id) in {u["id"] for u in units}
    assert all(u["unit_type"] != "state" for u in units)  # only the FPO's own area, not the wider state
    # Their record-level scope is unchanged: still only their FPO's farmers
    farmers = _ok(api.as_("fpo_admin").get("/farmers", params={"page_size": 200}))["items"]
    assert {f["primary_fpo_id"] for f in farmers} == {str(fpo_org.id)}
