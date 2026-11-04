"""Programme-owner instructions of 2 Nov 2026: configurable state list, indicator formulas, beneficiary categories and
demographic fields, configurable criteria/checklists, report templates with Excel/PDF exports, decisions register."""

from sqlalchemy import func, select

from app.models import CalculationRun, Farmer, GeoUnit, IndicatorDefinition, SystemSetting


def _farmer(db, consent="given"):
    return db.scalars(select(Farmer).where(Farmer.consent_status == consent).order_by(Farmer.farmer_code)).first()


# 1. sixth state + decisions register ----------------------------------------------------------------------------------

def test_sixth_state_is_odisha_and_decision_register(api, db) -> None:
    pa = api.as_("programme_admin")
    states = {g["name"]: g for g in pa.get("/geo-units", params={"unit_type": "state"}).json()}
    assert "Odisha" in states and "Meghalaya" not in states
    assert not any(s["requires_confirmation"] for s in states.values())
    decisions = {d["code"]: d for d in pa.get("/programme-decisions").json()}
    sixth = decisions["SIXTH_STATE"]
    assert sixth["status"] == "decided" and "Odisha" in sixth["decision"] and sixth["decided_on"] == "2026-10-29"
    assert {"INDICATOR_DEFINITIONS", "BENEFICIARY_CATEGORIES", "NABARD_TEMPLATES", "VERIFICATION_METHOD"} <= set(decisions)
    pending = [d for d in decisions.values() if d["status"] == "pending"]
    master = decisions["MASTER_LISTS"]
    assert pa.patch(f"/programme-decisions/{master['id']}", json={"status": "decided"}).status_code == 422
    assert api.as_("field").patch(f"/programme-decisions/{master['id']}", json={"owner": "x"}).status_code == 403
    done = pa.patch(f"/programme-decisions/{master['id']}", json={
        "status": "decided", "decision": "Lists supplied", "decided_by": "Programme owner", "decided_on": "2026-11-05"})
    assert done.json()["status"] == "decided"
    # States stay configurable: a state can be flagged for confirmation without code changes
    assert pa.patch(f"/geo-units/{states['Odisha']['id']}", json={"requires_confirmation": True}).status_code == 200
    status = pa.get("/system/feature-status").json()
    assert status["summary"]["pending_decisions"] == len(pending) - 1
    assert any(f["status"] == "awaiting_approval" and "NABARD" in f["feature"] for f in status["features"])


# 2/3. beneficiary categories and demographic fields --------------------------------------------------------------------

def test_categories_are_explicit_and_sensitive_ones_restricted(api, db) -> None:
    analyst, fpo_admin, field = api.as_("analyst"), api.as_("fpo_admin"), api.as_("field")
    assert {c["code"] for c in analyst.get("/beneficiary-categories").json()} == {"WOMAN_BENEFICIARY"}
    assert {c["code"] for c in fpo_admin.get("/beneficiary-categories").json()} >= {"WOMAN_BENEFICIARY", "TRIBAL_COMMUNITY"}
    no_consent = _farmer(db, consent="not_recorded")
    r = api.as_("programme_admin").post(f"/farmers/{no_consent.id}/categories", json={"category_code": "WOMAN_BENEFICIARY"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    farmer = fpo_admin.get("/farmers", params={"consent_status": "given"}).json()["items"][0]
    assert field.post(f"/farmers/{farmer['id']}/categories", json={"category_code": "TRIBAL_COMMUNITY"}).status_code in (403, 404)
    a = fpo_admin.post(f"/farmers/{farmer['id']}/categories", json={"category_code": "TRIBAL_COMMUNITY", "basis": "document"})
    assert a.status_code == 201 and a.json()["is_sensitive"] is True
    assert any(c["code"] == "TRIBAL_COMMUNITY" for c in fpo_admin.get(f"/farmers/{farmer['id']}").json()["categories"])
    seen = analyst.get(f"/farmers/{farmer['id']}").json()
    assert all(c["code"] != "TRIBAL_COMMUNITY" for c in seen["categories"]) and seen["sensitive_masked"] is True
    assert analyst.get("/farmers", params={"category_code": "TRIBAL_COMMUNITY"}).status_code == 403
    women = fpo_admin.get("/farmers", params={"category_code": "WOMAN_BENEFICIARY", "page_size": 100}).json()["items"]
    assert women and all(any(c["code"] == "WOMAN_BENEFICIARY" for c in f["categories"]) for f in women)
    removed = fpo_admin.patch(f"/farmer-category-assignments/{a.json()['id']}", json={"status": "removed"})
    assert removed.json()["status"] == "removed"


def test_category_membership_is_never_inferred(api, db) -> None:
    pa = api.as_("programme_admin")
    district = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    cluster = db.scalar(select(GeoUnit).where(GeoUnit.parent_id == district.id))
    created = pa.post("/farmers", json={"farmer_code": "T-NOINF-1", "full_name": "Test", "gender": "female",
                                        "geo_unit_id": str(cluster.id), "consent_status": "given", "consent_date": "2026-10-01"}).json()
    assert created["categories"] == []  # gender alone never makes someone a "woman beneficiary"


def test_demographic_fields_are_configurable_and_validated(api, db) -> None:
    pa, field = api.as_("programme_admin"), api.as_("field")
    assert pa.post("/demographic-fields", json={"code": "land_tenure", "label": "Land tenure", "field_type": "select"}).status_code == 422
    assert pa.post("/demographic-fields", json={"code": "land_tenure", "label": "Land tenure", "field_type": "select",
                                                "options": ["owned", "leased", "community"]}).status_code == 201
    farmer = _farmer(db)
    ok = pa.patch(f"/farmers/{farmer.id}", json={"demographics": {"land_tenure": "leased"}})
    assert ok.status_code == 200 and ok.json()["demographics"]["land_tenure"] == "leased"
    bad = pa.patch(f"/farmers/{farmer.id}", json={"demographics": {"land_tenure": "rented"}})
    assert bad.status_code == 422 and "must be one of" in bad.json()["error"]["message"]
    assert pa.patch(f"/farmers/{farmer.id}", json={"demographics": {"unknown_field": 1}}).status_code == 422
    unconsented = _farmer(db, consent="not_recorded")
    r = pa.patch(f"/farmers/{unconsented.id}", json={"demographics": {"land_tenure": "owned"}})
    assert r.status_code == 422 and "consent" in r.json()["error"]["message"]
    # Sensitive field (community_name): field officers cannot write it; analysts do not see it
    if field.get(f"/farmers/{farmer.id}").status_code == 200:
        assert field.patch(f"/farmers/{farmer.id}", json={"demographics": {"community_name": "x"}}).status_code == 403
    pa.patch(f"/farmers/{farmer.id}", json={"demographics": {"community_name": "Test community"}})
    assert "community_name" not in api.as_("analyst").get(f"/farmers/{farmer.id}").json()["demographics"]


# 2. configurable indicator formulas and preview ----------------------------------------------------------------------

def test_formula_indicator_preview_is_not_stored(api, db, programme_id) -> None:
    pa = api.as_("programme_admin")
    bad = pa.post("/indicators", json={"code": "T_FORMULA_BAD", "name": "x", "family": "x", "unit": "%", "definition": "x",
                                       "method_code": "configurable_formula",
                                       "method_parameters": {"components": {"a": {"method": "enrolled_farmers"}},
                                                             "expression": "__import__('os')"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_method_parameters"
    ind = pa.post("/indicators", json={
        "code": "T_FORMULA", "name": "Women share of enrolled", "family": "Inclusion", "unit": "%", "definition": "Draft",
        "method_code": "configurable_formula",
        "method_parameters": {"components": {"women": {"method": "category_beneficiaries",
                                                       "parameters": {"category_code": "WOMAN_BENEFICIARY"}},
                                             "enrolled": {"method": "enrolled_farmers"}},
                              "expression": "women / enrolled * 100"}}).json()
    runs_before = db.scalar(select(func.count()).select_from(CalculationRun))
    preview = pa.post(f"/indicators/{ind['id']}/preview", json={"programme_id": str(programme_id), "period_start": "2026-07-01",
                                                                "period_end": "2026-12-31"}).json()
    assert preview["preview"] is True and preview["stored"] is False and preview["indicator_status"] == "draft"
    components = preview["details"]["components"]
    women, enrolled = float(components["women"]["value"]), float(components["enrolled"]["value"])
    assert abs(float(preview["value"]) - women / enrolled * 100) < 0.001
    assert db.scalar(select(func.count()).select_from(CalculationRun)) == runs_before
    # Trying an alternative, unsaved method in preview
    alt = pa.post(f"/indicators/{ind['id']}/preview", json={"programme_id": str(programme_id), "period_start": "2026-07-01",
                                                            "period_end": "2026-12-31", "method_code": "category_beneficiaries",
                                                            "parameters": {}})
    assert alt.status_code == 422 and "category_code" in alt.json()["error"]["message"]
    # Stored calculations remain gated on approval
    db.get(SystemSetting, "mrv.allow_provisional_calculations").value = False
    db.flush()
    r = api.as_("analyst").post("/mrv/calculations", json={"indicator_id": ind["id"], "programme_id": str(programme_id),
                                                           "period_start": "2026-07-01", "period_end": "2026-12-31"})
    assert r.status_code == 422


def test_category_indicator_uses_assignments(api, db, programme_id) -> None:
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "WOMEN_BENEFICIARIES"))
    assert ind.method_code == "category_beneficiaries" and ind.method_parameters["category_code"] == "WOMAN_BENEFICIARY"
    out = api.as_("analyst").post(f"/indicators/{ind.id}/preview", json={
        "programme_id": str(programme_id), "period_start": "2026-07-01", "period_end": "2026-12-31"}).json()
    assert float(out["value"]) > 0


# 4. configurable criteria and checklists -----------------------------------------------------------------------------

def test_configured_criteria_and_checklists_are_enforced_only_when_required(api, db, programme_id) -> None:
    pa = api.as_("programme_admin")
    assert api.as_("field").put("/configuration/checklists/demonstration_site", json=[]).status_code == 403
    pa.put("/configuration/checklists/demonstration_site", json=[
        {"code": "SLOPE_OK", "label": "Slope suitable", "required": True}, {"code": "ROAD", "label": "Road access"}])
    farmer = _farmer(db)
    site = pa.post("/demonstration-sites", json={"programme_id": str(programme_id), "site_code": "T-CRIT-1", "name": "Crit",
                                                 "category": "plantation", "host_farmer_id": str(farmer.id)}).json()
    r = pa.patch(f"/demonstration-sites/{site['id']}", json={"status": "active", "established_on": "2026-10-01"})
    assert r.status_code == 422 and "Slope suitable" in r.json()["error"]["message"]
    assert pa.patch(f"/demonstration-sites/{site['id']}", json={"criteria_assessment": {"UNKNOWN": {"met": True}}}).status_code == 422
    ok = pa.patch(f"/demonstration-sites/{site['id']}", json={"status": "active", "established_on": "2026-10-01",
                                                              "criteria_assessment": {"SLOPE_OK": {"met": True, "note": "15%"}}})
    assert ok.status_code == 200 and ok.json()["criteria_assessment"]["SLOPE_OK"]["met"] is True
    checklists = api.as_("field").get("/configuration/checklists").json()
    assert [i["code"] for i in checklists["demonstration_site"]["items"]] == ["SLOPE_OK", "ROAD"]

    pa.put("/configuration/checklists/verification_visit", json=[{"code": "PHOTO", "label": "Photo taken", "required": True}])
    ver = api.as_("verifier")
    sample = ver.post("/verification-samples", json={
        "programme_id": str(programme_id), "title": "Purposive", "entity_type": "practice_record", "frame_description": "Oct",
        "selection_method": "purposive", "sample_size": 1,
        "frame_filter": {"period_start": "2026-10-01", "period_end": "2026-10-31"}}).json()
    frame = ver.get(f"/verification-samples/{sample['id']}/frame").json()
    assert frame["total"] == sample["frame_size"] and frame["items"][0]["label"]
    drawn = ver.post(f"/verification-samples/{sample['id']}/draw", json={"entity_ids": [frame["items"][-1]["id"]]}).json()
    item = drawn["items"][0]
    assert item["entity_id"] == frame["items"][-1]["id"]
    r = ver.patch(f"/verification-samples/items/{item['id']}", json={"result": "confirmed", "visit_date": "2026-11-03"})
    assert r.status_code == 422 and "Photo taken" in r.json()["error"]["message"]
    ok = ver.patch(f"/verification-samples/items/{item['id']}", json={"result": "confirmed", "visit_date": "2026-11-03",
                                                                       "checklist_results": {"PHOTO": {"ok": True}}})
    assert ok.status_code == 200


# 5. report templates and exports --------------------------------------------------------------------------------------

def test_report_templates_and_excel_pdf_exports(api, programme_id) -> None:
    pa = api.as_("programme_admin")
    templates = {t["code"]: t for t in pa.get("/report-templates").json()}
    assert templates["NABARD_CCF_ID"]["status"] == "awaiting_official_template"
    body = {"programme_id": str(programme_id), "period_start": "2026-07-01", "period_end": "2026-12-31"}
    nabard = pa.post("/reports", json={**body, "template_code": "NABARD_CCF_ID"}).json()
    assert nabard["template_code"] == "NABARD_CCF_ID"
    assert nabard["content"]["meta"]["template_status"] == "awaiting_official_template"
    assert "awaiting the official template" in nabard["content"]["meta"]["template_note"]
    demo = pa.post("/reports", json={**body, "template_code": "DEMONSTRATION"}).json()
    assert set(demo["content"]) == {"meta", "demonstrations"}
    custom = pa.post("/report-templates", json={"code": "T_STATE_BRIEF", "name": "State brief", "base_report_type": "geography",
                                                "sections": ["funnel", "inclusion"]})
    assert custom.status_code == 201
    assert pa.post("/report-templates", json={"code": "T_BAD", "name": "x", "base_report_type": "programme",
                                              "sections": ["nonsense"]}).status_code == 422
    xlsx = pa.get(f"/reports/{nabard['id']}/export", params={"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK" and "spreadsheetml" in xlsx.headers["content-type"]
    pdf = pa.get(f"/reports/{nabard['id']}/export", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    from io import BytesIO

    from openpyxl import load_workbook
    sheets = load_workbook(BytesIO(xlsx.content)).sheetnames
    assert sheets[0] == "Disclaimer" and any("Progress" in s for s in sheets)


def test_dashboard_breakdown_by_state_respects_scope(api) -> None:
    admin = api.as_("programme_admin").get("/dashboard/breakdown", params={"by": "state"}).json()
    names = {r["name"] for r in admin["rows"]}
    assert len(names) == 6 and "Odisha" in names and not any(r["requires_confirmation"] for r in admin["rows"])
    assert {"WOMAN_BENEFICIARY", "TRIBAL_COMMUNITY"} <= {c["code"] for c in admin["categories"]}
    field = api.as_("field").get("/dashboard/breakdown", params={"by": "state"}).json()
    assert [r["name"] for r in field["rows"]] == ["Andhra Pradesh"]
    analyst = api.as_("analyst").get("/dashboard/breakdown", params={"by": "district"}).json()
    assert "TRIBAL_COMMUNITY" not in {c["code"] for c in analyst["categories"]}
