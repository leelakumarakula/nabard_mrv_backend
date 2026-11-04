"""Regression tests for the fixes made after the code review of 4 Nov 2026 (one section per fix)."""

from sqlalchemy import false, select

from app.models import GeoUnit, IndicatorDefinition, ReportingPeriod


def _period(db):
    return db.scalar(select(ReportingPeriod).where(ReportingPeriod.code == "2026-Q4"))


def _indicator(db, code):
    return db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == code))


def _district(db, name):
    return db.scalar(select(GeoUnit).where(GeoUnit.name == name, GeoUnit.unit_type == "district"))


def _calculate(analyst, programme_id, period, indicator, **scope):
    r = analyst.post("/mrv/calculations", json={"indicator_id": str(indicator.id), "programme_id": str(programme_id),
                                                "reporting_period_id": str(period.id), **scope})
    assert r.status_code == 201, r.text
    return r.json()


# -- Review tasks, results and calculation runs respect the user's geography ---------------------------------------

def test_review_tasks_results_and_runs_are_limited_to_the_users_area(api, db, programme_id) -> None:
    analyst, coordinator, reviewer = api.as_("analyst"), api.as_("coordinator"), api.as_("reviewer")
    period, indicator = _period(db), _indicator(db, "COV_FARMERS_ENROLLED")
    ap, odisha = _district(db, "Alluri Sitharama Raju"), _district(db, "Koraput")

    outputs = {
        "programme": _calculate(analyst, programme_id, period, indicator),
        "ap": _calculate(analyst, programme_id, period, indicator, scope_type="district", scope_geo_unit_id=str(ap.id)),
        "odisha": _calculate(analyst, programme_id, period, indicator, scope_type="district",
                             scope_geo_unit_id=str(odisha.id)),
    }
    tasks = {}
    for key, out in outputs.items():
        r = analyst.post(f"/mrv/results/{out['result']['id']}/submit-for-review", json={"note": key})
        assert r.status_code == 200, r.text
        tasks[key] = r.json()["id"]

    # The Andhra Pradesh coordinator sees only the AP task, result and run
    visible = {t["id"] for t in coordinator.get("/mrv/review-tasks", params={"page_size": 200}).json()["items"]}
    assert tasks["ap"] in visible and tasks["odisha"] not in visible and tasks["programme"] not in visible
    assert coordinator.get(f"/mrv/review-tasks/{tasks['ap']}").status_code == 200
    assert coordinator.get(f"/mrv/review-tasks/{tasks['odisha']}").status_code == 403
    assert coordinator.get(f"/mrv/review-tasks/{tasks['programme']}").status_code == 403
    assert coordinator.get(f"/mrv/results/{outputs['ap']['result']['id']}").status_code == 200
    assert coordinator.get(f"/mrv/results/{outputs['odisha']['result']['id']}").status_code == 403
    runs = {r["id"] for r in coordinator.get("/mrv/calculation-runs", params={"page_size": 200}).json()["items"]}
    assert outputs["ap"]["run"]["id"] in runs and outputs["odisha"]["run"]["id"] not in runs
    assert coordinator.get(f"/mrv/calculation-runs/{outputs['odisha']['run']['id']}").status_code == 403
    assert coordinator.get(f"/mrv/calculation-runs/{outputs['programme']['run']['id']}/inputs").status_code == 403
    assert coordinator.get(f"/mrv/entity-history/indicator_result/{outputs['odisha']['result']['id']}").status_code == 403

    # A research partner with no assigned area sees no record-level review tasks at all
    assert api.as_("researcher").get(f"/mrv/review-tasks/{tasks['ap']}").status_code == 403

    # Programme-wide roles still see everything
    all_tasks = {t["id"] for t in reviewer.get("/mrv/review-tasks", params={"page_size": 200}).json()["items"]}
    assert set(tasks.values()) <= all_tasks


# -- Targets are versioned, never overwritten -------------------------------------------------------------------------

def test_target_figure_changes_only_as_a_new_version(api, programme_id) -> None:
    pa = api.as_("programme_admin")
    targets = pa.get(f"/programmes/{programme_id}/targets").json()
    current = next(t for t in targets if t["metric_code"] == "HH_PEST_SURVEILLANCE")
    assert float(current["target_value"]) == 3000 and current["target_version"] == 1

    # The figure cannot be overwritten in place …
    r = pa.patch(f"/programme-targets/{current['id']}", json={"target_value": 2500})
    assert r.status_code == 422 and r.json()["error"]["code"] == "target_figure_locked"
    # … but status and notes can change, and resending the unchanged figure is accepted
    r = pa.patch(f"/programme-targets/{current['id']}", json={
        "status": "approved", "source_note": "Approved at appraisal (test)", "target_value": current["target_value"],
        "unit": current["unit"], "target_version": 1})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    # A changed figure becomes version 2; version 1 is kept unchanged
    r = pa.post(f"/programme-targets/{current['id']}/revise", json={
        "target_value": 2800, "source_note": "Revised after baseline (test)"})
    assert r.status_code == 201, r.text
    revised = r.json()
    assert revised["target_version"] == 2 and float(revised["target_value"]) == 2800 and revised["status"] == "revised"
    assert revised["metric_code"] == "HH_PEST_SURVEILLANCE" and revised["indicator_code"] == current["indicator_code"]
    versions = {t["target_version"]: t for t in pa.get(f"/programmes/{programme_id}/targets").json()
                if t["metric_code"] == "HH_PEST_SURVEILLANCE"}
    assert float(versions[1]["target_value"]) == 3000 and float(versions[2]["target_value"]) == 2800

    # A duplicate or older version cannot be added directly either
    r = pa.post(f"/programmes/{programme_id}/targets", json={
        "metric_code": "HH_PEST_SURVEILLANCE", "metric_name": "x", "target_value": 1, "unit": "households",
        "target_version": 2})
    assert r.status_code == 409
    # Revising needs a reason
    assert pa.post(f"/programme-targets/{current['id']}/revise", json={"target_value": 1}).status_code == 422


# -- One adoption rule everywhere; every funnel stage is limited to the programme ------------------------------------

def test_adoption_figures_agree_across_indicator_report_and_dashboard(api, db, programme_id) -> None:
    analyst, pa = api.as_("analyst"), api.as_("programme_admin")
    start, end = "2026-01-01", "2099-12-31"

    def indicator_value(code):
        r = analyst.post("/mrv/calculations", json={"indicator_id": str(_indicator(db, code).id),
                                                    "programme_id": str(programme_id), "period_start": start,
                                                    "period_end": end})
        assert r.status_code == 201, r.text
        return float(r.json()["result"]["value"])

    households_adopting = indicator_value("HH_CLIMATE_RESILIENT_ADOPTION")
    women_adopting = indicator_value("WOMEN_ADOPTING")
    report = pa.get("/reports/programme", params={"programme_id": str(programme_id), "period_start": start,
                                                  "period_end": end}).json()
    assert report["funnel"]["stages"]["adopted_observed"] == households_adopting > 0
    women = next(c for c in report["inclusion"]["categories"] if c["code"] == "WOMAN_BENEFICIARY")
    assert women["members_with_observed_adoption"] == women_adopting
    # Verified adoption can never exceed observed adoption
    assert report["funnel"]["stages"]["adopted_verified"] <= report["funnel"]["stages"]["adopted_observed"]

    breakdown = pa.get("/dashboard/breakdown", params={"by": "state", "programme_id": str(programme_id)}).json()
    assert sum(r["households_adopted_observed"] for r in breakdown["rows"]) == households_adopting


def test_funnel_ignores_activity_of_other_programmes(api, db, programme_id) -> None:
    from app.models import AdvisoryDelivery, Farmer

    pa = api.as_("programme_admin")
    params = {"programme_id": str(programme_id), "period_start": "2026-01-01", "period_end": "2099-12-31"}
    before = pa.get("/reports/programme", params=params).json()["funnel"]["stages"]

    reached = select(AdvisoryDelivery.farmer_id).where(AdvisoryDelivery.farmer_id.is_not(None))
    farmer = db.scalars(select(Farmer).where(Farmer.is_archived == false(), Farmer.id.not_in(reached))
                        .order_by(Farmer.farmer_code)).first()
    other = pa.post("/programmes", json={"code": "TEST-OTHER-PROG", "name": "Another programme (test)"}).json()
    advisory = pa.post("/advisories", json={
        "programme_id": other["id"], "origin_module": "climate", "advisory_type": "alert", "title": "Other programme",
        "message": "Not part of the coffee programme", "target_geo_unit_id": str(farmer.geo_unit_id)}).json()
    assert pa.post(f"/advisories/{advisory['id']}/issue").status_code == 200
    r = pa.post(f"/advisories/{advisory['id']}/deliveries", json={
        "farmer_id": str(farmer.id), "channel": "sms", "delivered_at": "2026-11-01T10:00:00+05:30"})
    assert r.status_code == 201, r.text

    after = pa.get("/reports/programme", params=params).json()["funnel"]["stages"]
    assert after["reached"] == before["reached"]


# -- A withdrawn confirmation never leaves a practice verified --------------------------------------------------------

def test_correcting_a_visit_result_updates_the_practice(api, db, programme_id) -> None:
    from app.models import Farmer, PracticeRecord

    admin, ver = api.as_("admin"), api.as_("verifier")
    farmer = db.scalars(select(Farmer).where(Farmer.is_archived == false()).order_by(Farmer.farmer_code.desc())).first()
    practice = admin.post("/practice-records", json={"programme_id": str(programme_id), "farmer_id": str(farmer.id),
                                                     "module_code": "water", "activity_code": "MULCHING"}).json()
    assert admin.post(f"/practice-records/{practice['id']}/observe",
                      json={"observed_action": "Mulch seen", "action_date": "2026-11-20"}).status_code == 200
    sample = ver.post("/verification-samples", json={
        "programme_id": str(programme_id), "title": "Correction test", "entity_type": "practice_record",
        "frame_description": "20 Nov observations", "selection_method": "purposive", "sample_size": 1,
        "frame_filter": {"period_start": "2026-11-20", "period_end": "2026-11-20"}}).json()
    item = ver.post(f"/verification-samples/{sample['id']}/draw", json={"entity_ids": [practice["id"]]}).json()["items"][0]

    def status_after(body):
        assert ver.patch(f"/verification-samples/items/{item['id']}", json=body).status_code == 200
        db.expire_all()
        return db.get(PracticeRecord, practice["id"]).verification_status

    assert status_after({"result": "confirmed", "visit_date": "2026-11-21"}) == "verified"
    assert status_after({"result": "not_visited"}) == "unverified"          # confirmation withdrawn
    assert status_after({"result": "discrepancy", "visit_date": "2026-11-22",
                         "discrepancy_note": "No mulch found"}) == "not_verified"
    assert status_after({"result": "confirmed", "visit_date": "2026-11-23"}) == "verified"


# -- One unexpected failure quarantines that record only; the rest of the batch is saved -----------------------------

def test_unexpected_error_in_one_record_does_not_fail_the_batch(api, client, db, monkeypatch) -> None:
    from app.models import FarmPlot, MonitoringRecord, QuarantinedRecord
    from app.services.integration import pipeline

    original = pipeline.Processor.measurement

    def flaky(self, m, digest):
        if m.source_record_id == "WM-BOOM":
            raise RuntimeError("simulated database failure")
        return original(self, m, digest)

    monkeypatch.setattr(pipeline.Processor, "measurement", flaky)
    key = api.as_("admin").post("/integrations/sources/water_management/rotate-key").json()["inbound_key"]
    plot = db.scalars(select(FarmPlot).order_by(FarmPlot.plot_code)).first()

    def payload(record_id):
        return {"source_record_id": record_id, "programme_code": "CCRL-INCLUSIVE", "farm_plot_external_id": plot.plot_code,
                "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 1000, "unit": "litre",
                "observed_at": "2026-10-30T10:00:00+05:30"}

    r = client.post("/api/v1/integrations/water_management/measurements", headers={"X-Source-Key": key},
                    json={"records": [payload("WM-OK-1"), payload("WM-BOOM"), payload("WM-OK-2")]})
    assert r.status_code == 200, r.text
    run = r.json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"], run["status"]) == (2, 1, "partial")
    saved = set(db.scalars(select(MonitoringRecord.source_record_id).where(
        MonitoringRecord.source_record_id.in_(["WM-OK-1", "WM-BOOM", "WM-OK-2"]))))
    assert saved == {"WM-OK-1", "WM-OK-2"}
    quarantined = db.scalar(select(QuarantinedRecord).where(QuarantinedRecord.source_record_id == "WM-BOOM"))
    assert quarantined is not None and "Unexpected error" in quarantined.errors[0]
    assert "simulated database failure" not in quarantined.errors[0]  # technical detail stays in the server log


# -- A temporary password must be changed before the account can do anything else -----------------------------------

def test_temporary_password_blocks_everything_except_changing_it(api, client) -> None:
    admin = api.as_("admin")
    r = admin.post("/admin/users", json={"email": "temp.user@demo.local", "full_name": "Temp User",
                                         "password": "Temporary-Pass-1", "role_codes": ["mrv_analyst"]})
    assert r.status_code == 201 and r.json()["must_change_password"] is True
    token = client.post("/api/v1/auth/login", data={"username": "temp.user@demo.local",
                                                    "password": "Temporary-Pass-1"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    blocked = client.get("/api/v1/farmers", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"
    assert client.get("/api/v1/dashboard", headers=headers).status_code == 403
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["must_change_password"] is True

    r = client.post("/api/v1/auth/change-password", headers=headers,
                    json={"current_password": "Temporary-Pass-1", "new_password": "My-Own-Pass-2026"})
    assert r.status_code == 200
    assert client.get("/api/v1/farmers", headers=headers).status_code == 200


# -- State-wise targets from the proposal (§5.1) ----------------------------------------------------------------------

def test_state_targets_follow_the_proposal_and_reports_pick_the_right_area(api, db, programme_id) -> None:
    pa = api.as_("programme_admin")
    states = {g["id"]: g["name"] for g in pa.get("/geo-units", params={"unit_type": "state"}).json()}
    targets = pa.get(f"/programmes/{programme_id}/targets").json()
    by_state = {(states[t["geo_unit_id"]], t["metric_code"]): float(t["target_value"]) for t in targets if t["geo_unit_id"]}
    expected = {"Andhra Pradesh": (3300, 1500), "Odisha": (1500, 700), "Nagaland": (1000, 450),
                "Arunachal Pradesh": (250, 120), "Mizoram": (300, 150), "Manipur": (150, 80)}
    for state, (households, area) in expected.items():
        assert by_state[(state, "HOUSEHOLDS")] == households and by_state[(state, "COFFEE_AREA_HA")] == area
    assert sum(v for (_, m), v in by_state.items() if m == "HOUSEHOLDS") == 6500
    assert sum(v for (_, m), v in by_state.items() if m == "COFFEE_AREA_HA") == 3000

    def households_row(**scope):
        report = pa.get("/reports/programme", params={"programme_id": str(programme_id), "period_start": "2026-10-01",
                                                      "period_end": "2026-12-31", **scope}).json()
        rows = [p for p in report["progress_vs_targets"] if p["metric_code"] == "HOUSEHOLDS"]
        assert len(rows) == 1
        return rows[0]

    whole = households_row()
    assert float(whole["target_value"]) == 6500 and whole["target_area"] == "Whole programme"
    ap_id = next(i for i, n in states.items() if n == "Andhra Pradesh")
    ap = households_row(scope_type="state", scope_geo_unit_id=ap_id)
    assert float(ap["target_value"]) == 3300 and ap["target_area"] == "Andhra Pradesh"

    dashboard = pa.get("/dashboard").json()
    assert [float(t["target_value"]) for t in dashboard["planning_targets"] if t["metric_name"].startswith("Households (")] \
        == [6500]


# -- Plantation stage on plots (proposal §5.2) -------------------------------------------------------------------------

def test_plot_records_plantation_stage(api, db) -> None:
    from app.models import Farm

    pa = api.as_("programme_admin")
    farm = db.scalars(select(Farm).order_by(Farm.farm_code)).first()
    r = pa.post(f"/farms/{farm.id}/plots", json={"plot_code": "TEST-STAGE-1", "area": 0.4, "plantation_stage": "young"})
    assert r.status_code == 201 and r.json()["plantation_stage"] == "young"
    plot_id = r.json()["id"]
    r = pa.patch(f"/plots/{plot_id}", json={"plantation_stage": "underperforming"})
    assert r.status_code == 200 and r.json()["plantation_stage"] == "underperforming"
    assert pa.patch(f"/plots/{plot_id}", json={"plantation_stage": "old"}).status_code == 422
    history = api.as_("admin").get("/admin/audit-events", params={"entity_type": "farm_plot", "entity_id": plot_id}).json()
    assert any((e.get("after") or {}).get("plantation_stage") == "underperforming" for e in history["items"])


# -- Proposal §10.1 outcome indicators are catalogued but cannot produce numbers until defined --------------------------

def test_outcome_indicators_are_pending_and_not_calculable(api, db, programme_id) -> None:
    analyst = api.as_("analyst")
    catalogue = {i["code"]: i for i in analyst.get("/indicators", params={"page_size": 200}).json()["items"]}
    for code in ("OUT_IRRIGATION_WATER_REDUCTION", "OUT_PEST_RESPONSE_TIME", "OUT_CROP_LOSS_REDUCTION"):
        indicator = catalogue[code]
        assert indicator["status"] == "draft" and indicator["method_code"] is None
        assert "Pending programme-owner approval" in indicator["method_reference"]
        r = analyst.post("/mrv/calculations", json={"indicator_id": indicator["id"], "programme_id": str(programme_id),
                                                    "period_start": "2026-10-01", "period_end": "2026-12-31"})
        assert r.status_code == 422 and "no supported calculation method" in r.json()["error"]["message"]


# -- Empty JSON boxes in the indicator form ----------------------------------------------------------------------------
def test_indicator_accepts_empty_json_boxes(api, db) -> None:
    pa = api.as_("programme_admin")
    r = pa.post("/indicators", json={"code": "T_EMPTY_JSON", "name": "x", "family": "outcome", "unit": "households",
                                     "definition": "x", "method_code": "households_adopting",
                                     "method_parameters": None, "validation_rules": None})
    assert r.status_code == 201, r.text
    assert r.json()["method_parameters"] == {} and r.json()["validation_rules"] == {}
    r = pa.patch(f"/indicators/{r.json()['id']}", json={"method_parameters": None, "validation_rules": None})
    assert r.status_code == 200, r.text
    assert r.json()["method_parameters"] == {}
