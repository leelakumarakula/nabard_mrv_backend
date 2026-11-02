from sqlalchemy import func, select

from app.models import FarmPlot, MonitoringRecord, QuarantinedRecord


def _payload(plot_code: str, record_id: str = "WM-T-1", value: int = 80000, **extra) -> dict:
    return {"source_system": "water_management", "source_record_id": record_id, "programme_code": "CCRL-INCLUSIVE",
            "farm_plot_external_id": plot_code, "activity_code": "WATER_MANAGEMENT",
            "indicator_code": "IRRIGATION_WATER_VOLUME", "value": value, "unit": "litre",
            "observed_at": "2026-10-29T10:30:00+05:30", "method": "meter_reading",
            "evidence": [{"source_evidence_id": "EV-77"}], **extra}


def _key(api) -> str:
    return api.as_("admin").post("/integrations/sources/water_management/rotate-key").json()["inbound_key"]


def test_push_with_source_key_and_idempotency(api, client, db) -> None:
    plot = db.scalars(select(FarmPlot).order_by(FarmPlot.plot_code)).first()
    key = _key(api)
    headers = {"X-Source-Key": key}
    url = "/api/v1/integrations/water_management/measurements"
    r = client.post(url, json={"records": [_payload(plot.plot_code)]}, headers=headers)
    assert r.status_code == 200, r.text
    run = r.json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"], run["records_rejected"]) == (1, 0, 0)
    rec = db.scalar(select(MonitoringRecord).where(MonitoringRecord.source_record_id == "WM-T-1"))
    assert rec.value == 80 and rec.unit == "m3" and rec.original_value == 80000 and rec.original_unit == "litre"
    assert rec.source_evidence_refs == [{"source_evidence_id": "EV-77"}]
    # Same payload again → duplicate, no new record
    r = client.post(url, json={"records": [_payload(plot.plot_code)]}, headers=headers)
    assert r.json()["sync_run"]["records_duplicate"] == 1
    assert db.scalar(select(func.count()).select_from(MonitoringRecord).where(MonitoringRecord.source_record_id == "WM-T-1")) == 1
    # Changed payload for the same source record updates it (still unverified)
    r = client.post(url, json={"records": [_payload(plot.plot_code, value=90000)]}, headers=headers)
    assert r.json()["sync_run"]["records_accepted"] == 1
    db.expire_all()
    assert db.scalar(select(MonitoringRecord.value).where(MonitoringRecord.source_record_id == "WM-T-1")) == 90


def test_invalid_source_key_and_missing_auth(client) -> None:
    url = "/api/v1/integrations/water_management/measurements"
    assert client.post(url, json={"records": [{}]}, headers={"X-Source-Key": "nope"}).status_code == 401
    assert client.post(url, json={"records": [{}]}).status_code == 401


def test_quarantine_and_replay_after_plot_mapping(api, db) -> None:
    admin = api.as_("admin")
    r = admin.post("/integrations/water_management/measurements",
                   json={"records": [_payload("SRC-PLOT-77", record_id="WM-T-Q1")]}).json()
    assert r["sync_run"]["status"] == "failed"
    assert "Unknown plot reference 'SRC-PLOT-77'" in r["outcomes"][0]["errors"][0]
    q = admin.get("/integrations/quarantine", params={"source_code": "water_management"}).json()["items"]
    item = next(i for i in q if i["source_record_id"] == "WM-T-Q1")
    # Fix identity mapping, then replay
    plot = db.scalars(select(FarmPlot)).first()
    assert admin.post("/integrations/sources/water_management/plot-maps",
                      json={"source_plot_id": "SRC-PLOT-77", "farm_plot_id": str(plot.id)}).status_code == 201
    replay = admin.post("/integrations/water_management/replay", json={"quarantine_ids": [item["id"]]}).json()
    assert replay["sync_run"]["trigger"] == "replay" and replay["sync_run"]["records_accepted"] == 1
    db.expire_all()
    assert db.get(QuarantinedRecord, item["id"]).status == "replayed"


def test_field_mappings_and_value_maps(api, db) -> None:
    admin = api.as_("admin")
    plot = db.scalars(select(FarmPlot)).first()
    raw = {"id": "MWM-T-1", "plotRef": plot.plot_code, "qty": 5, "uom": "KL", "readingTime": "2026-10-20T06:00:00+05:30",
           "kind": "IRRIGATION_WATER_VOLUME", "prog": "CCRL-INCLUSIVE"}
    r = admin.post("/integrations/mock_water_meter/measurements", json={"records": [raw]}).json()
    assert r["sync_run"]["records_accepted"] == 1, r
    rec = db.scalar(select(MonitoringRecord).where(MonitoringRecord.source_record_id == "MWM-T-1"))
    assert rec.value == 5 and rec.unit == "m3"


def test_validation_failures_are_readable(api) -> None:
    r = api.as_("admin").post("/integrations/water_management/measurements", json={"records": [
        {"source_record_id": "X1", "programme_code": "NOPE", "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 1,
         "unit": "litre", "observed_at": "2026-10-20T06:00:00+05:30", "geo_code": "IN-AP"},
        {"source_record_id": "X2", "programme_code": "CCRL-INCLUSIVE", "indicator_code": "IRRIGATION_WATER_VOLUME",
         "value": 1, "unit": "bucket", "observed_at": "2026-10-20T06:00:00+05:30", "geo_code": "IN-AP"},
        {"source_record_id": "X3", "programme_code": "CCRL-INCLUSIVE", "indicator_code": "IRRIGATION_WATER_VOLUME",
         "value": 1, "unit": "litre", "observed_at": "2026-10-20T06:00:00"},
        {"source_system": "climate_intelligence", "source_record_id": "X4", "programme_code": "CCRL-INCLUSIVE",
         "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 1, "unit": "litre",
         "observed_at": "2026-10-20T06:00:00+05:30", "geo_code": "IN-AP"},
    ]}).json()
    errors = [o["errors"][0] for o in r["outcomes"]]
    assert "Unknown programme code 'NOPE'" in errors[0]
    assert "No approved conversion from 'bucket'" in errors[1]
    assert "timezone offset" in errors[2]
    assert "does not match the receiving source" in errors[3]
    assert r["sync_run"]["records_rejected"] == 4


def test_dry_run_does_not_persist(api, db) -> None:
    plot = db.scalars(select(FarmPlot)).first()
    r = api.as_("admin").post("/integrations/water_management/test-payload", json={
        "payload_kind": "measurements", "records": [_payload(plot.plot_code, record_id="DRY-1")]}).json()
    assert r["outcomes"][0]["result"] == "accepted" and r["sync_run"]["trigger"] == "test"
    assert db.scalar(select(MonitoringRecord).where(MonitoringRecord.source_record_id == "DRY-1")) is None


def test_mock_pull_with_retry_pagination_duplicates(api) -> None:
    r = api.as_("admin").post("/integrations/mock_water_meter/pull").json()
    run = r["sync_run"]
    # Second pull: everything already imported → duplicates; unknown plot quarantined again
    assert run["records_read"] == 14 and run["records_duplicate"] == 13 and run["records_rejected"] == 1
    assert run["cursor"] == "pages=3"


def test_unconfigured_source_never_fakes_data(api) -> None:
    r = api.as_("admin").post("/integrations/climate_intelligence/pull")
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_not_configured"
    sources = {s["source_code"]: s for s in api.as_("admin").get("/integrations/sources").json()}
    assert sources["climate_intelligence"]["connection_status"].startswith("not_configured")
    assert sources["mock_water_meter"]["is_mock"] is True


def test_observations_and_advisories_inbound(api, db) -> None:
    admin = api.as_("admin")
    plot = db.scalars(select(FarmPlot)).first()
    r = admin.post("/integrations/water_management/observations", json={"records": [{
        "source_record_id": "OBS-1", "programme_code": "CCRL-INCLUSIVE", "module_code": "soil",
        "record_type": "soil_test", "farm_plot_external_id": plot.plot_code, "observed_at": "2026-10-10T09:00:00+05:30",
        "attributes": {"lab_name": "Source lab", "ph": 5.6}}]}).json()
    assert r["sync_run"]["records_accepted"] == 1, r
    from app.models import Farmer
    farmer = db.scalars(select(Farmer)).first()
    r = admin.post("/integrations/water_management/advisories", json={"records": [{
        "source_record_id": "ADV-1", "programme_code": "CCRL-INCLUSIVE", "origin_module": "water",
        "advisory_type": "advisory", "title": "Irrigation advisory", "message": "m",
        "issued_at": "2026-10-10T09:00:00+05:30", "target_geo_code": "IN-AP",
        "deliveries": [{"source_delivery_id": "D-1", "farmer_code": farmer.farmer_code, "channel": "sms",
                        "delivered_at": "2026-10-10T09:05:00+05:30"}]}]}).json()
    assert r["sync_run"]["records_accepted"] == 1, r
