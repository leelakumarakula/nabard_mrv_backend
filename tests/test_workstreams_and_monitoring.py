from sqlalchemy import select

from app.models import Farm, Farmer, FarmPlot, GeoUnit, IndicatorDefinition, MonitoringRecord


def _ap0_plot(db) -> FarmPlot:
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    clusters = list(db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id == ap0.id)))
    return db.scalars(select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id).join(Farmer, Farmer.id == Farm.farmer_id)
                      .where(Farmer.geo_unit_id.in_(clusters)).order_by(FarmPlot.plot_code)).first()


def test_all_seven_modules_listed(api) -> None:
    modules = api.as_("field").get("/workstreams").json()
    assert [m["number"] for m in modules] == [1, 2, 3, 4, 5, 6, 7]
    assert all(m["record_types"] for m in modules if m["code"] != "mrv")


def test_field_record_draft_submit_and_validation(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    field = api.as_("field")
    r = field.post("/field-records", json={"programme_id": str(programme_id), "record_type": "pest_inspection",
                                           "farm_plot_id": str(plot.id), "observed_at": "2026-10-20T09:00:00+05:30",
                                           "attributes": {"pest_or_disease": "leaf_rust", "plants_inspected": 50,
                                                          "plants_affected": 5}})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["submission_status"] == "draft" and rec["data_nature"] == "observed"
    assert rec["farmer_id"] == str(db.get(Farm, plot.farm_id).farmer_id)  # derived from plot
    submitted = field.post(f"/field-records/{rec['id']}/submit").json()
    assert submitted["validation_status"] == "valid" and float(submitted["value"]) == 10.0 and submitted["unit"] == "%"
    assert field.patch(f"/field-records/{rec['id']}", json={"notes": "x"}).status_code == 409


def test_invalid_record_is_rejected_with_reasons(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    field = api.as_("field")
    rec = field.post("/field-records", json={"programme_id": str(programme_id), "record_type": "pest_inspection",
                                             "farm_plot_id": str(plot.id), "observed_at": "2030-01-01T09:00:00+05:30",
                                             "attributes": {"pest_or_disease": "leaf_rust", "plants_inspected": 5,
                                                            "plants_affected": 9}}).json()
    out = field.post(f"/field-records/{rec['id']}/submit").json()
    assert out["validation_status"] == "rejected"
    messages = " ".join(out["validation_messages"])
    assert "Plants affected cannot exceed plants inspected" in messages and "future" in messages
    # Rejected records can be corrected and resubmitted
    fixed = field.patch(f"/field-records/{rec['id']}", json={
        "observed_at": "2026-10-20T09:00:00+05:30",
        "attributes": {"pest_or_disease": "leaf_rust", "plants_inspected": 10, "plants_affected": 2}})
    assert fixed.status_code == 200
    assert field.post(f"/field-records/{rec['id']}/submit").json()["validation_status"] == "valid"


def test_prediction_record_keeps_its_nature(api, db, programme_id) -> None:
    district = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    rec = api.as_("field").post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "climate_risk_assessment", "geo_unit_id": str(district.id),
        "attributes": {"hazard_type": "drought", "risk_level": "high", "valid_from": "2026-11-01",
                       "valid_to": "2026-11-10", "model_name": "test"}}).json()
    assert rec["data_nature"] == "model_prediction"


def test_field_officer_cannot_edit_others_records(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    rec = api.as_("admin").post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "soil_test", "farm_plot_id": str(plot.id),
        "observed_at": "2026-10-01T09:00:00+05:30", "attributes": {"lab_name": "Lab"}}).json()
    assert api.as_("field").patch(f"/field-records/{rec['id']}", json={"notes": "edit"}).status_code == 403


def test_training_participants(api, db, programme_id) -> None:
    fpo_admin = api.as_("fpo_admin")
    me = fpo_admin.get("/auth/me").json()
    farmers = fpo_admin.get("/farmers", params={"page_size": 3}).json()["items"]
    rec = fpo_admin.post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "training_session", "organisation_id": me["organisation_id"],
        "observed_at": "2026-10-15T10:00:00+05:30",
        "attributes": {"topic": "Pruning", "trainer": "T", "mode": "field"}}).json()
    for f in farmers:
        assert fpo_admin.post(f"/field-records/{rec['id']}/participants", json={"farmer_id": f["id"]}).status_code == 201
    assert fpo_admin.post(f"/field-records/{rec['id']}/participants", json={"farmer_id": farmers[0]["id"]}).status_code == 409
    assert len(fpo_admin.get(f"/field-records/{rec['id']}/participants").json()) == len(farmers)


def test_manual_monitoring_converts_units_and_validates(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    irr = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "IRRIGATION_WATER_VOLUME"))
    field = api.as_("field")
    rec = field.post("/monitoring-records", json={
        "programme_id": str(programme_id), "indicator_id": str(irr.id), "farm_plot_id": str(plot.id),
        "value": 45000, "unit": "litre", "observed_at": "2026-10-21T07:00:00+05:30", "method": "meter_reading"}).json()
    assert float(rec["value"]) == 45.0 and rec["unit"] == "m3"
    assert float(rec["original_value"]) == 45000 and rec["original_unit"] == "litre"
    out = field.post(f"/monitoring-records/{rec['id']}/submit").json()
    assert out["validation_status"] == "valid", out["validation_messages"]
    # Same value/time/plot again → possible duplicate exception
    dup = field.post("/monitoring-records", json={
        "programme_id": str(programme_id), "indicator_id": str(irr.id), "farm_plot_id": str(plot.id),
        "value": 45, "unit": "m3", "observed_at": "2026-10-21T07:00:00+05:30"}).json()
    dup = field.post(f"/monitoring-records/{dup['id']}/submit").json()
    assert dup["validation_status"] == "exception"
    # Field officer cannot resolve; analyst can
    assert field.post(f"/monitoring-records/{dup['id']}/resolve-exception",
                      json={"decision": "reject", "note": "dup"}).status_code == 403
    resolved = api.as_("analyst").post(f"/monitoring-records/{dup['id']}/resolve-exception",
                                       json={"decision": "reject", "note": "Confirmed duplicate entry"}).json()
    assert resolved["validation_status"] == "rejected"


def test_unit_mismatch_rejected_on_submit(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    irr = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "IRRIGATION_WATER_VOLUME"))
    rec = api.as_("field").post("/monitoring-records", json={
        "programme_id": str(programme_id), "indicator_id": str(irr.id), "farm_plot_id": str(plot.id),
        "value": 3, "unit": "mm", "observed_at": "2026-10-21T07:00:00+05:30"}).json()
    out = api.as_("field").post(f"/monitoring-records/{rec['id']}/submit").json()
    assert out["validation_status"] == "rejected" and "does not match indicator unit" in " ".join(out["validation_messages"])


def test_plot_farmer_mismatch_refused(api, db, programme_id) -> None:
    plot = _ap0_plot(db)
    owner = db.get(Farm, plot.farm_id).farmer_id
    other = db.scalars(select(Farmer).where(Farmer.id != owner, Farmer.geo_unit_id == db.get(Farmer, owner).geo_unit_id)).first()
    r = api.as_("field").post("/field-records", json={
        "programme_id": str(programme_id), "record_type": "soil_test", "farm_plot_id": str(plot.id),
        "farmer_id": str(other.id), "attributes": {"lab_name": "x"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "plot_farmer_mismatch"


def test_imported_records_are_read_only(api, db) -> None:
    imported = db.scalars(select(MonitoringRecord).where(MonitoringRecord.source_system_id.is_not(None))).first()
    r = api.as_("admin").patch(f"/monitoring-records/{imported.id}", json={"notes": "edit"})
    assert r.status_code == 409


def test_advisory_lifecycle_and_practice_observation(api, db, programme_id) -> None:
    analyst, field = api.as_("programme_admin"), api.as_("field")
    assert api.as_("analyst").post("/advisories", json={}).status_code in (403, 422)
    district = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    adv = analyst.post("/advisories", json={"programme_id": str(programme_id), "origin_module": "water",
                                            "advisory_type": "advisory", "title": "Irrigate early morning",
                                            "message": "Test message", "target_geo_unit_id": str(district.id)}).json()
    farmers = field.get("/farmers", params={"page_size": 2}).json()["items"]
    assert analyst.post(f"/advisories/{adv['id']}/deliveries", json={"farmer_id": farmers[0]["id"], "channel": "sms"}).status_code == 409
    assert analyst.post(f"/advisories/{adv['id']}/issue").json()["status"] == "issued"
    assert analyst.patch(f"/advisories/{adv['id']}", json={"title": "changed"}).status_code == 409
    bulk = analyst.post(f"/advisories/{adv['id']}/deliveries/bulk",
                        json={"channel": "sms", "farmer_ids": [f["id"] for f in farmers]}).json()
    assert len(bulk) == 2
    practice = field.post("/practice-records", json={
        "programme_id": str(programme_id), "farmer_id": farmers[0]["id"], "module_code": "water",
        "activity_code": "WATER_MANAGEMENT", "advisory_id": adv["id"], "status": "reported",
        "reported_action": "Farmer says irrigation moved to morning"}).json()
    assert practice["evidence_basis"] == "reported"
    observed = field.post(f"/practice-records/{practice['id']}/observe", json={
        "observed_action": "Irrigation timer set to 6am", "action_date": "2026-10-22"}).json()
    assert observed["status"] == "observed" and observed["evidence_basis"] == "observed"
    assert field.patch(f"/practice-records/{practice['id']}", json={"reported_action": "x"}).status_code == 409


def test_device_calibration_failure_marks_faulty(api, db) -> None:
    field = api.as_("field")
    device = next(d for d in field.get("/devices", params={"page_size": 50}).json()["items"] if d["status"] == "active")
    r = field.post(f"/devices/{device['id']}/calibrations", json={"calibrated_on": "2026-10-30", "result": "fail"})
    assert r.status_code == 201
    assert field.get(f"/devices/{device['id']}").json()["status"] == "faulty"
