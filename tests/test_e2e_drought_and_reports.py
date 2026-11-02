"""End-to-end drought-adaptation scenario: climate risk → alert → delivery → farmer response → field action →
evidence → MRV calculation → review → verification → report."""

from sqlalchemy import func, select

from app.models import Farmer, GeoUnit, IndicatorDefinition, User
from app.seed.demo import PLACEHOLDER_PNG


def test_drought_scenario_end_to_end(api, db, programme_id) -> None:
    analyst, field, verifier_c, pa = api.as_("analyst"), api.as_("field"), api.as_("verifier"), api.as_("programme_admin")
    reviewer_c = api.as_("reviewer")
    farmer_user = api.as_("farmer")
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    farmer = db.get(Farmer, db.scalar(select(User.farmer_id).where(User.email == "farmer@demo.local")))
    reviewer = db.scalar(select(User).where(User.email == "internal.reviewer@demo.local"))

    # 1. Climate risk estimate (prediction) and alert, issued by the programme team
    risk = pa.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "climate_risk_assessment", "geo_unit_id": str(ap0.id),
        "attributes": {"hazard_type": "drought", "risk_level": "very_high", "valid_from": "2026-12-01",
                       "valid_to": "2026-12-20", "model_name": "E2E model"}}).json()
    assert pa.post(f"/field-records/{risk['id']}/submit").json()["validation_status"] == "valid"
    alert = pa.post("/advisories", json={
        "programme_id": str(programme_id), "origin_module": "climate", "advisory_type": "alert", "hazard_type": "drought",
        "risk_level": "very_high", "risk_record_id": risk["id"], "title": "E2E drought alert", "message": "Drought expected",
        "recommendation": "Mulch and irrigate early", "recommended_activity_code": "WATER_MANAGEMENT",
        "target_geo_unit_id": str(ap0.id)}).json()
    pa.post(f"/advisories/{alert['id']}/issue")

    # 2. Delivery to the farmer, farmer responds through self-service
    delivery = pa.post(f"/advisories/{alert['id']}/deliveries", json={
        "farmer_id": str(farmer.id), "channel": "sms", "sent_at": "2026-12-02T08:00:00+05:30",
        "delivered_at": "2026-12-02T08:01:00+05:30"}).json()
    mine = farmer_user.get("/me/advisories").json()
    assert any(a["delivery_id"] == delivery["id"] for a in mine)
    assert farmer_user.post(f"/me/advisories/{delivery['id']}/response",
                            json={"response": "Mulching today", "understood": True}).status_code == 200
    assert farmer_user.get("/farmers").status_code == 403  # farmers only see their own data

    # 3. Field officer records the observed action with photo evidence
    practice = field.post("/practice-records", json={
        "programme_id": str(programme_id), "farmer_id": str(farmer.id), "module_code": "water",
        "activity_code": "WATER_MANAGEMENT", "advisory_id": alert["id"], "recommended_action": "Mulch basins"}).json()
    field.post(f"/practice-records/{practice['id']}/observe", json={"observed_action": "Basins mulched",
                                                                   "action_date": "2026-12-05"})
    upload = field.post("/evidence", files={"file": ("mulch.png", PLACEHOLDER_PNG, "image/png")},
                        data={"entity_type": "practice_record", "entity_id": practice["id"], "purpose": "practice_photo"})
    assert upload.status_code == 201

    # 4. MRV: calculate reach, response rate and observed adoption for December
    def calc(code, **parameters):
        ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == code))
        return analyst.post("/mrv/calculations", json={
            "indicator_id": str(ind.id), "programme_id": str(programme_id), "period_start": "2026-12-01",
            "period_end": "2026-12-31", "scope_type": "district", "scope_geo_unit_id": str(ap0.id),
            "parameters": parameters}).json()["result"]

    reach, adoption, rate = (calc("CLIM_FARMERS_REACHED_ALERTS"), calc("HH_CLIMATE_RESILIENT_ADOPTION", cumulative=False),
                             calc("CLIM_ADVISORY_RESPONSE_RATE"))
    assert reach["is_provisional"] is True  # definitions are pending programme-owner approval
    assert float(reach["value"]) == 1 and float(adoption["value"]) == 1 and float(rate["value"]) == 100

    # 5. Review and independent verification of the adoption result
    task = analyst.post(f"/mrv/results/{adoption['id']}/submit-for-review",
                        json={"reviewer_id": str(reviewer.id), "note": "December adoption"}).json()
    detail = reviewer_c.get(f"/mrv/review-tasks/{task['id']}").json()
    assert detail["can_review"] and detail["entity"]["value"]
    reviewer_c.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={"note": "Photo evidence checked"})
    verifier_c.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "Evidence OK"})

    # 6. Report reconciles with records and shows status
    report = pa.post("/reports", json={"programme_id": str(programme_id), "report_type": "geography",
                                       "period_start": "2026-12-01", "period_end": "2026-12-31",
                                       "scope_type": "district", "scope_geo_unit_id": str(ap0.id)}).json()
    results = {r["indicator_code"]: r for r in report["content"]["indicator_results"]}
    assert results["HH_CLIMATE_RESILIENT_ADOPTION"]["verification_status"] == "verified"
    assert results["CLIM_FARMERS_REACHED_ALERTS"]["verification_status"] == "unverified"
    assert "not achievements" in report["content"]["meta"]["disclaimer"]
    deliveries = report["content"]["module_activity"]["advisory_deliveries"]
    assert deliveries["with_response"] >= 1
    csv = pa.get(f"/reports/{report['id']}/export", params={"format": "csv"})
    assert csv.status_code == 200 and "HH_CLIMATE_RESILIENT_ADOPTION" in csv.text and "verification=verified" in csv.text


def test_report_lifecycle_and_targets_labelled(api, db, programme_id) -> None:
    pa, admin = api.as_("programme_admin"), api.as_("admin")
    report = pa.post("/reports", json={"programme_id": str(programme_id), "period_start": "2026-07-01",
                                       "period_end": "2026-12-31"}).json()
    targets = report["content"]["planning_targets"]
    assert targets and all(t["status"] == "proposed" for t in targets)
    coverage = report["content"]["coverage"]
    assert coverage["farmers_enrolled_with_consent"] <= coverage["farmers_registered"]
    assert pa.post(f"/reports/{report['id']}/approve").status_code == 403  # generator cannot approve
    assert admin.post(f"/reports/{report['id']}/publish").status_code == 409  # must be approved first
    assert admin.post(f"/reports/{report['id']}/approve").json()["status"] == "approved"
    assert admin.post(f"/reports/{report['id']}/publish").json()["status"] == "published"
    # Research partners see only published reports and no live data
    researcher = api.as_("researcher")
    assert any(r["id"] == report["id"] for r in researcher.get("/reports").json()["items"])
    assert researcher.get("/reports/programme", params={"programme_id": str(programme_id), "period_start": "2026-07-01",
                                                        "period_end": "2026-12-31"}).status_code == 403
    second = pa.post("/reports", json={"programme_id": str(programme_id), "period_start": "2026-07-01",
                                       "period_end": "2026-12-31"}).json()
    assert second["version"] == report["version"] + 1
    assert researcher.get(f"/reports/{second['id']}").status_code == 404


def test_report_totals_reconcile_with_registry(api, db, programme_id) -> None:
    from app.models import ProgrammeParticipation
    live = api.as_("analyst").get("/reports/programme", params={
        "programme_id": str(programme_id), "period_start": "2026-07-01", "period_end": "2026-12-31"}).json()
    enrolled = db.scalar(select(func.count(func.distinct(Farmer.id))).join(
        ProgrammeParticipation, ProgrammeParticipation.farmer_id == Farmer.id).where(
        ProgrammeParticipation.status == "enrolled", Farmer.consent_status == "given"))
    assert live["coverage"]["farmers_enrolled_with_consent"] == enrolled


def test_dashboard_is_role_aware(api) -> None:
    field = api.as_("field").get("/dashboard").json()
    admin = api.as_("admin").get("/dashboard").json()
    farmer = api.as_("farmer").get("/dashboard").json()
    assert admin["coverage"]["farmers_registered"] > field["coverage"]["farmers_registered"] > 0
    assert "coverage" not in farmer and "my_tasks" not in farmer
    assert api.as_("reviewer").get("/dashboard").json()["my_tasks"]


def test_transaction_rollback_on_failed_request(api, db, programme_id) -> None:
    """A request that fails validation leaves no partial records behind."""
    before = db.scalar(select(func.count()).select_from(Farmer))
    r = api.as_("programme_admin").post("/farmers", json={"farmer_code": "ROLLBACK-1", "full_name": "x",
                                                          "geo_unit_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 422
    assert db.scalar(select(func.count()).select_from(Farmer)) == before
