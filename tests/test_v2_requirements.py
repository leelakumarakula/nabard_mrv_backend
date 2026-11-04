"""Version 2.0 acceptance tests (spec §13): programme configuration, inclusion, demonstrations, surveillance,
conservation, approval-gated calculations, independent verification, reports and integration identity maps."""

from sqlalchemy import select

from app.models import (
    Device,
    Farm,
    Farmer,
    FarmPlot,
    GeoUnit,
    IndicatorDefinition,
    PracticeRecord,
    ProgrammeTarget,
    ReportingPeriod,
    SystemSetting,
)


def _ap0(db):
    district = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    clusters = list(db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id == district.id)))
    farmers = list(db.scalars(select(Farmer).where(Farmer.geo_unit_id.in_(clusters)).order_by(Farmer.farmer_code)))
    return district, clusters, farmers


def _plot_of(db, farmer):
    return db.scalars(select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id).where(Farm.farmer_id == farmer.id)
                      .order_by(FarmPlot.plot_code)).first()


# -- programme configuration --------------------------------------------------------------------------------------

def test_latest_programme_is_primary_with_proposed_versioned_targets(api, db, programme_id) -> None:
    pa = api.as_("programme_admin")
    programmes = pa.get("/programmes").json()["items"]
    assert programmes[0]["code"] == "CCRL-INCLUSIVE" and programmes[0]["duration_months"] == 36
    # Programme-wide figures (state-wise figures from proposal §5.1 are checked in test_fixes.py)
    targets = {t["metric_code"]: t for t in pa.get(f"/programmes/{programme_id}/targets").json() if not t["geo_unit_id"]}
    expected = {"HOUSEHOLDS": 6500, "COFFEE_AREA_HA": 3000, "DEMO_SITES_PLANTATION": 60, "DEMO_SITES_SOIL_MOISTURE": 60,
                "PEST_SURVEILLANCE_CLUSTERS": 30, "WOMEN_BENEFICIARIES": 2600, "CRPS_TRAINED": 100, "CRPS_WOMEN_TRAINED": 50,
                "HH_CLIMATE_RESILIENT_ADOPTION": 4725, "HH_SOIL_MOISTURE_CONSERVATION": 4000, "HH_PEST_SURVEILLANCE": 3000,
                "WOMEN_ADOPTING": 1600, "OUTLAY_CR": 15}
    for code, value in expected.items():
        assert float(targets[code]["target_value"]) == value and targets[code]["status"] == "proposed"
        assert targets[code]["target_version"] == 1
    # Earlier-proposal figures are never loaded into the V2 programme
    assert not db.scalar(select(ProgrammeTarget.id).where(ProgrammeTarget.target_value.in_([8000, 16000])))
    assert float(targets["OUTLAY_CR"]["target_value"]) == 15  # not the earlier ₹30 crore
    states = [g["name"] for g in pa.get("/geo-units", params={"unit_type": "state"}).json()]
    assert sorted(states) == sorted(["Andhra Pradesh", "Odisha", "Nagaland", "Arunachal Pradesh", "Mizoram", "Manipur"])


def test_revised_target_is_a_new_version(api, programme_id) -> None:
    pa = api.as_("programme_admin")
    r = pa.post(f"/programmes/{programme_id}/targets", json={
        "metric_code": "HOUSEHOLDS", "metric_name": "Households", "target_value": 6400, "unit": "households",
        "status": "revised", "target_version": 2, "source_note": "Revised after baseline (test)"})
    assert r.status_code == 201
    versions = [t["target_version"] for t in pa.get(f"/programmes/{programme_id}/targets").json()
                if t["metric_code"] == "HOUSEHOLDS" and not t["geo_unit_id"]]
    assert versions == [2, 1]


# -- approval-gated calculations ----------------------------------------------------------------------------------

def test_calculation_requires_approved_definition_unless_provisional(api, db, programme_id) -> None:
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "WOMEN_BENEFICIARIES"))
    body = {"indicator_id": str(ind.id), "programme_id": str(programme_id), "period_start": "2026-07-01",
            "period_end": "2026-12-31"}
    analyst = api.as_("analyst")
    out = analyst.post("/mrv/calculations", json=body).json()
    assert out["result"]["is_provisional"] is True
    db.get(SystemSetting, "mrv.allow_provisional_calculations").value = False
    db.flush()
    r = analyst.post("/mrv/calculations", json=body)
    assert r.status_code == 422 and "pending programme-owner approval" in r.json()["error"]["message"]
    # Approval needs the full definition (eligibility, baseline method, evidence and verification rules)
    pa = api.as_("programme_admin")
    pa.post(f"/indicators/{ind.id}/status", json={"status": "submitted"})
    db.get(IndicatorDefinition, ind.id).eligibility = None
    db.flush()
    r = pa.post(f"/indicators/{ind.id}/status", json={"status": "approved"})
    assert r.status_code == 422 and "eligibility" in r.json()["error"]["message"]


def test_internal_reviewer_and_independent_verifier_are_separate(api, db, programme_id) -> None:
    period = db.scalar(select(ReportingPeriod).where(ReportingPeriod.code == "2026-Q4"))
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "CRPS_TRAINED"))
    admin = api.as_("admin")  # super admin holds both review and verify permissions
    result = api.as_("analyst").post("/mrv/calculations", json={
        "indicator_id": str(ind.id), "programme_id": str(programme_id), "reporting_period_id": str(period.id)}).json()["result"]
    task = api.as_("analyst").post(f"/mrv/results/{result['id']}/submit-for-review", json={}).json()
    assert admin.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={}).json()["reviewed_by_id"]
    r = admin.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "self-check"})
    assert r.status_code == 403 and "internal reviewer" in r.json()["error"]["message"]
    assert api.as_("verifier").post(f"/mrv/review-tasks/{task['id']}/decision",
                                    json={"decision": "verified", "rationale": "Independent check"}).status_code == 201
    assert "my_tasks" in api.as_("verifier").get("/dashboard").json()


# -- inclusion and privacy ------------------------------------------------------------------------------------------

def test_inclusion_report_is_aggregate(api, programme_id) -> None:
    report = api.as_("programme_admin").post("/reports", json={
        "programme_id": str(programme_id), "report_type": "inclusion", "period_start": "2026-07-01",
        "period_end": "2026-12-31"}).json()
    inclusion = report["content"]["inclusion"]
    categories = {c["code"]: c for c in inclusion["categories"]}
    assert categories["WOMAN_BENEFICIARY"]["enrolled_members"] > 0
    assert categories["TRIBAL_COMMUNITY"]["sensitive"] is True
    assert categories["WOMAN_BENEFICIARY"]["definition_status"] == "proposed"
    assert inclusion["community_resource_persons"]["women_trained"] <= inclusion["community_resource_persons"]["trained"]
    assert "Synthetic Farmer" not in str(report["content"])  # no names in aggregate reports


# -- community resource persons -------------------------------------------------------------------------------------

def test_crp_training_requires_completion_date(api, db, programme_id) -> None:
    pa = api.as_("programme_admin")
    district, _, _ = _ap0(db)
    crp = pa.post("/community-resource-persons", json={"programme_id": str(programme_id), "crp_code": "T-CRP-1",
                                                        "full_name": "Test CRP", "gender": "female",
                                                        "geo_unit_id": str(district.id)}).json()
    assert crp["status"] == "nominated"
    assert pa.patch(f"/community-resource-persons/{crp['id']}", json={"status": "trained"}).status_code == 422
    out = pa.patch(f"/community-resource-persons/{crp['id']}", json={"status": "trained", "training_completed_on": "2026-10-30"})
    assert out.json()["status"] == "trained"
    assert api.as_("analyst").get("/community-resource-persons", params={"status": "trained"}).json()["total"] >= 1


# -- demonstration sites --------------------------------------------------------------------------------------------

def test_demonstration_site_and_visits(api, db, programme_id) -> None:
    _, _, farmers = _ap0(db)
    pa, field = api.as_("programme_admin"), api.as_("field")
    site = pa.post("/demonstration-sites", json={
        "programme_id": str(programme_id), "site_code": "T-DS-1", "name": "Test soil-moisture site",
        "category": "soil_moisture", "host_farmer_id": str(farmers[0].id), "practices": ["MULCHING"]}).json()
    assert site["geo_unit_id"] == str(farmers[0].geo_unit_id) and site["status"] == "planned"
    assert pa.patch(f"/demonstration-sites/{site['id']}", json={"status": "active"}).status_code == 422
    pa.patch(f"/demonstration-sites/{site['id']}", json={"status": "active", "established_on": "2026-10-01"})
    # A visit without a site is rejected; with the site it is valid and counted
    no_site = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "demonstration_visit", "farmer_id": str(farmers[0].id),
        "observed_at": "2026-10-20T10:00:00+05:30",
        "attributes": {"visit_type": "monitoring", "practices_observed": "Mulch", "observations": "ok"}}).json()
    out = field.post(f"/field-records/{no_site['id']}/submit").json()
    assert out["validation_status"] == "rejected" and "demonstration site is required" in " ".join(out["validation_messages"])
    visit = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "demonstration_visit", "demonstration_site_id": site["id"],
        "observed_at": "2026-10-20T10:00:00+05:30",
        "attributes": {"visit_type": "field_day", "practices_observed": "Mulch", "participants_count": 10,
                       "women_participants_count": 12, "observations": "ok"}}).json()
    out = field.post(f"/field-records/{visit['id']}/submit").json()
    assert "Women participants cannot exceed participants" in " ".join(out["validation_messages"])
    field.patch(f"/field-records/{visit['id']}", json={"attributes": {
        "visit_type": "field_day", "practices_observed": "Mulch", "participants_count": 10, "women_participants_count": 4,
        "observations": "ok"}})
    assert field.post(f"/field-records/{visit['id']}/submit").json()["validation_status"] == "valid"
    assert pa.get(f"/demonstration-sites/{site['id']}").json()["visit_count"] == 1


# -- pest surveillance clusters ---------------------------------------------------------------------------------------

def test_surveillance_cluster_membership_and_inspection(api, db, programme_id) -> None:
    _, clusters, farmers = _ap0(db)
    pa, field = api.as_("programme_admin"), api.as_("field")
    cluster = pa.post("/surveillance-clusters", json={
        "programme_id": str(programme_id), "cluster_code": "T-PSC-1", "name": "Test cluster", "geo_unit_id": str(clusters[0]),
        "status": "active", "established_on": "2026-09-01"}).json()
    members = [f for f in farmers if f.geo_unit_id == clusters[0]][:2]
    for f in members:
        assert pa.post(f"/surveillance-clusters/{cluster['id']}/members", json={"farmer_id": str(f.id)}).status_code == 201
    assert pa.post(f"/surveillance-clusters/{cluster['id']}/members", json={"farmer_id": str(members[0].id)}).status_code == 409
    rec = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "pest_inspection", "farm_plot_id": str(_plot_of(db, members[0]).id),
        "surveillance_cluster_id": cluster["id"], "observed_at": "2026-10-21T09:00:00+05:30",
        "attributes": {"pest_or_disease": "leaf_rust", "plants_inspected": 50, "plants_affected": 4}}).json()
    assert field.post(f"/field-records/{rec['id']}/submit").json()["validation_status"] == "valid"
    stats = pa.get(f"/surveillance-clusters/{cluster['id']}").json()
    assert stats["inspections"] == 1 and stats["member_households"] >= 1
    soil = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "soil_test", "farm_plot_id": str(_plot_of(db, members[0]).id),
        "surveillance_cluster_id": cluster["id"], "observed_at": "2026-10-21T09:00:00+05:30", "attributes": {"lab_name": "x"}}).json()
    out = field.post(f"/field-records/{soil['id']}/submit").json()
    assert out["validation_status"] == "rejected"


# -- soil/water conservation, plantation survival, device capability ----------------------------------------------

def test_conservation_survival_and_device_rules(api, db, programme_id) -> None:
    _, _, farmers = _ap0(db)
    field = api.as_("field")
    plot = _plot_of(db, farmers[5])
    rec = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "conservation_activity", "farm_plot_id": str(plot.id),
        "observed_at": "2026-10-21T09:00:00+05:30",
        "attributes": {"practice_type": "contour_bunds", "area_covered_ha": 0.4, "length_m": 60}}).json()
    out = field.post(f"/field-records/{rec['id']}/submit").json()
    assert out["validation_status"] == "valid" and out["module_code"] == "water" and out["activity_code"] == "SOIL_WATER_CONSERVATION"
    survival = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "plantation_survival_check", "farm_plot_id": str(plot.id),
        "observed_at": "2026-10-21T09:00:00+05:30",
        "attributes": {"planting_date": "2026-06-01", "plants_planted": 100, "plants_surviving": 87}}).json()
    out = field.post(f"/field-records/{survival['id']}/submit").json()
    assert out["validation_status"] == "valid" and float(out["value"]) == 87.0 and out["unit"] == "%"
    rain = db.scalar(select(Device).where(Device.device_type == "rainsync"))
    reading = field.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "sensor_observation", "device_id": str(rain.id),
        "observed_at": "2026-10-21T09:00:00+05:30", "attributes": {"parameter": "soil_moisture", "value": 20, "unit": "%"}}).json()
    out = field.post(f"/field-records/{reading['id']}/submit").json()
    assert out["validation_status"] == "rejected" and "does not report 'soil_moisture'" in " ".join(out["validation_messages"])


# -- independent verification sampling -----------------------------------------------------------------------------

def _sample(api, programme_id, size=3, seed=7):
    return api.as_("verifier").post("/verification-samples", json={
        "programme_id": str(programme_id), "title": "Test sample", "entity_type": "practice_record",
        "frame_description": "Observed practices Oct 2026", "selection_method": "simple_random", "sample_size": size,
        "random_seed": seed, "frame_filter": {"period_start": "2026-10-01", "period_end": "2026-10-31"}}).json()


def test_verification_sample_is_reproducible_and_updates_adoption(api, db, programme_id) -> None:
    ver = api.as_("verifier")
    first, second = _sample(api, programme_id), _sample(api, programme_id)
    assert first["frame_size"] >= 3 and first["status"] == "planned"
    a = ver.post(f"/verification-samples/{first['id']}/draw", json={}).json()
    b = ver.post(f"/verification-samples/{second['id']}/draw", json={}).json()
    assert [i["entity_id"] for i in a["items"]] == [i["entity_id"] for i in b["items"]]  # same seed → same draw
    assert ver.post(f"/verification-samples/{first['id']}/draw", json={}).status_code == 422
    assert ver.post(f"/verification-samples/{first['id']}/complete",
                    json={"conclusion": "confirmed", "conclusion_note": "all good"}).status_code == 422
    items = a["items"]
    r = ver.patch(f"/verification-samples/items/{items[0]['id']}", json={"result": "discrepancy", "visit_date": "2026-11-01"})
    assert r.status_code == 422  # discrepancy needs a note
    ver.patch(f"/verification-samples/items/{items[0]['id']}", json={"result": "confirmed", "visit_date": "2026-11-01"})
    ver.patch(f"/verification-samples/items/{items[1]['id']}",
              json={"result": "discrepancy", "visit_date": "2026-11-01", "discrepancy_note": "Not as recorded"})
    ver.patch(f"/verification-samples/items/{items[2]['id']}", json={"result": "not_visited"})
    db.expire_all()
    assert db.get(PracticeRecord, items[0]["entity_id"]).verification_status == "verified"
    assert db.get(PracticeRecord, items[1]["entity_id"]).verification_status == "not_verified"
    done = ver.post(f"/verification-samples/{first['id']}/complete",
                    json={"conclusion": "discrepancies_found", "conclusion_note": "1 discrepancy"}).json()
    assert done["status"] == "completed" and done["result_counts"] == {"confirmed": 1, "discrepancy": 1, "not_visited": 1}
    # Verified practice records can no longer be edited or re-observed
    assert api.as_("admin").post(f"/practice-records/{items[0]['entity_id']}/observe",
                                 json={"observed_action": "x" * 5, "action_date": "2026-11-02"}).status_code == 409


def test_verifier_cannot_verify_own_observation_and_others_cannot_sample(api, db, programme_id) -> None:
    assert api.as_("analyst").post("/verification-samples", json={}).status_code in (403, 422)
    admin = api.as_("admin")
    _, _, farmers = _ap0(db)
    practice = admin.post("/practice-records", json={"programme_id": str(programme_id), "farmer_id": str(farmers[1].id),
                                                     "module_code": "water", "activity_code": "MULCHING"}).json()
    admin.post(f"/practice-records/{practice['id']}/observe", json={"observed_action": "Mulch seen", "action_date": "2026-11-10"})
    sample = admin.post("/verification-samples", json={
        "programme_id": str(programme_id), "title": "Purposive", "entity_type": "practice_record",
        "frame_description": "Nov observations", "selection_method": "purposive", "sample_size": 1,
        "frame_filter": {"period_start": "2026-11-01", "period_end": "2026-11-30"}}).json()
    drawn = admin.post(f"/verification-samples/{sample['id']}/draw", json={"entity_ids": [practice["id"]]}).json()
    r = admin.patch(f"/verification-samples/items/{drawn['items'][0]['id']}", json={"result": "confirmed", "visit_date": "2026-11-12"})
    assert r.status_code == 403 and "not involved" in r.json()["error"]["message"]


def test_households_adopting_counts_households_not_farmers(api, db, programme_id) -> None:
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "HH_CLIMATE_RESILIENT_ADOPTION"))
    out = api.as_("analyst").post("/mrv/calculations", json={
        "indicator_id": str(ind.id), "programme_id": str(programme_id), "period_start": "2026-07-01",
        "period_end": "2026-12-31"}).json()
    details = out["run"]["output"]["details"]
    assert int(float(out["result"]["value"])) <= details["farmers"] <= details["practice_records"]
    verified = api.as_("analyst").post("/mrv/calculations", json={
        "indicator_id": str(ind.id), "programme_id": str(programme_id), "period_start": "2026-07-01",
        "period_end": "2026-12-31", "parameters": {"require_verified": True}}).json()
    assert float(verified["result"]["value"]) <= float(out["result"]["value"])


# -- reports ----------------------------------------------------------------------------------------------------------

def test_quarterly_nabard_and_endline_reports(api, programme_id) -> None:
    pa = api.as_("programme_admin")
    body = {"programme_id": str(programme_id), "period_start": "2026-10-01", "period_end": "2026-12-31"}
    nabard = pa.post("/reports", json={**body, "report_type": "nabard_ccf_id"}).json()
    content = nabard["content"]
    assert "template" in content["meta"]["template_note"] and content["meta"]["contains_demo_data"] is True
    stages = content["funnel"]["stages"]
    assert stages["registered"] >= stages["enrolled"] and stages["adopted_observed"] >= stages["adopted_verified"]
    assert set(content["funnel"]["definitions"]) == set(stages)
    progress = {p["metric_code"]: p for p in content["progress_vs_targets"]}
    assert progress["HH_CLIMATE_RESILIENT_ADOPTION"]["latest_result"]["provisional"] is True
    assert content["demonstrations"]["sites"] and content["surveillance"]["clusters"]
    assert content["verification"]["samples"][0]["conclusion"] == "discrepancies_found"
    csv = pa.get(f"/reports/{nabard['id']}/export", params={"format": "csv"}).text
    assert "funnel,adopted_verified" in csv and "PROVISIONAL" in csv
    r = pa.post("/reports", json={**body, "report_type": "endline"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "methodology_pending"
    for kind in ("quarterly", "annual", "demonstration", "surveillance", "verification"):
        assert pa.post("/reports", json={**body, "report_type": kind}).status_code == 201


def test_dashboard_progress_against_targets(api) -> None:
    d = api.as_("programme_admin").get("/dashboard").json()
    assert d["programme"]["duration_months"] == 36
    assert any(p["metric_code"] == "WOMEN_BENEFICIARIES" and p["latest_result"] for p in d["progress"])
    assert d["community"]["surveillance_clusters_active"] >= 1
    assert "progress" not in api.as_("field").get("/dashboard").json()  # scoped users see their own scope only


# -- integrations: identity maps, payload defaults, mock IoT --------------------------------------------------------

def test_identity_maps_and_exact_code_setting(api, db) -> None:
    admin = api.as_("admin")
    device = db.scalar(select(Device).where(Device.device_type == "soilsync_7in1", Device.status == "active"))
    db.get(SystemSetting, "integration.allow_exact_code_matching").value = False
    db.flush()
    reading = {"msgId": "T-GW-1", "deviceId": "VENDOR-SN-001", "ts": "2026-10-25T06:00:00+05:30", "param": "PH", "val": 5.9,
               "unit": "pH", "q": "ok"}
    r = admin.post("/integrations/mock_iot_gateway/observations", json={"records": [reading]}).json()
    assert r["sync_run"]["records_rejected"] == 1 and "identity map" in r["outcomes"][0]["errors"][0]
    m = admin.post("/integrations/sources/mock_iot_gateway/identity-maps",
                   json={"entity_type": "device", "source_id": "VENDOR-SN-001", "internal_id": str(device.id)})
    assert m.status_code == 201 and m.json()["mapped_by_id"]
    r = admin.post("/integrations/mock_iot_gateway/observations", json={"records": [reading]}).json()
    assert r["sync_run"]["records_accepted"] == 1, r
    assert any(x["source_record_id"] == "VENDOR-SN-001" for x in admin.get("/integrations/sources/mock_iot_gateway/identity-maps").json())


def test_mock_iot_pull_is_labelled_and_idempotent(api) -> None:
    admin = api.as_("admin")
    run = admin.post("/integrations/mock_iot_gateway/pull").json()["sync_run"]
    assert run["records_duplicate"] == run["records_read"] - 1 and run["records_rejected"] == 1
    sources = {s["source_code"]: s for s in admin.get("/integrations/sources").json()}
    assert sources["mock_iot_gateway"]["connection_status"].startswith("MOCK")
    assert sources["pest_prediction"]["connection_status"].startswith("not_configured")


def test_received_timestamp_is_recorded(api, db) -> None:
    plot = db.scalars(select(FarmPlot).order_by(FarmPlot.plot_code)).first()
    r = api.as_("admin").post("/integrations/water_management/measurements", json={"records": [{
        "source_record_id": "RCV-1", "programme_code": "CCRL-INCLUSIVE", "farm_plot_external_id": plot.plot_code,
        "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 5, "unit": "m3", "observed_at": "2026-10-20T06:00:00+05:30"}]}).json()
    assert r["sync_run"]["records_accepted"] == 1
    from app.models import MonitoringRecord
    rec = db.scalar(select(MonitoringRecord).where(MonitoringRecord.source_record_id == "RCV-1"))
    assert rec.received_at is not None and rec.source_timestamp is not None


def test_roles_match_v2(api) -> None:
    roles = {r["code"]: r["name"] for r in api.as_("admin").get("/admin/roles").json()}
    assert roles["internal_reviewer"] == "Internal Reviewer" and roles["verifier"] == "Independent Verifier"
    assert roles["fpo_admin"] == "FPO/Grower Group Coordinator"
    assert roles["field_officer"] == "Field Officer / Community Resource Person"
    me = api.as_("reviewer").get("/auth/me").json()
    assert "mrv.review" in me["permissions"] and "mrv.verify" not in me["permissions"]


def test_v2_modules(api) -> None:
    modules = {m["code"]: m for m in api.as_("field").get("/workstreams").json()}
    assert modules["water"]["name"] == "Soil & Water Management and Conservation"
    assert modules["farmer_fpo"]["name"] == "Farmer & Community Implementation"
    codes = {rt["code"] for m in modules.values() for rt in m["record_types"]}
    assert {"conservation_activity", "plantation_condition", "plantation_survival_check", "demonstration_visit"} <= codes


