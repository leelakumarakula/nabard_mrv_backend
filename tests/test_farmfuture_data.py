"""FarmFuture soil & microclimate, pest scouting, satellite and advisory adapters, and device import.

Read-only GET calls only; farms resolve through the farm links made by the farm import. Response shapes follow live
FarmFuture responses (November 2026) with invented values. The API is an httpx.MockTransport – tests never call FarmFuture.
"""

import json
from datetime import datetime, time, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Advisory, AdvisoryDelivery, Device, FarmPlot, FieldRecord, GeoUnit, IntegrationSource
from app.services.integration import platform_adapters
from app.services.integration.farmfuture_data import advisory_module, forecast_days, scouting_pest
from app.services.integration.platform_adapters import IST, today_ist

PROGRAMME = "CCRL-INCLUSIVE"
FARM_A = "11111111-1111-4111-8111-111111111111"
FARM_B = "22222222-2222-4222-8222-222222222222"
ESTATE = "33333333-3333-4333-8333-333333333333"
ZONE = "44444444-4444-4444-8444-444444444444"
SOIL_MAC, CLIME_MAC, HUB_MAC, STRAY_MAC = "A0B765293650", "78421CA2A790", "2805A52F3914", "FFFFFFFFFFFF"


def _stats(day: str, today: str) -> dict:
    soil = {"soil_moisture_percentage_L1": {"min": 20.0, "max": 24.0, "mean": 22.0},
            "soil_moisture_percentage_L2": {"min": 25.0, "max": 27.0, "mean": 26.0},
            "temperature": {"min": 20.5, "max": 23.5, "mean": 21.9}, "pH": {"min": 0, "max": 0, "mean": 0},  # offline pH
            "ec": {"min": 60, "max": 70, "mean": 64.5}, "nitrogen": {"min": 120, "max": 130, "mean": 124.0},
            "phosphorous": {"min": 60, "max": 62, "mean": 61.0}, "potassium": {"min": 139, "max": 141, "mean": 140.0},
            "humidity": {"min": 20.0, "max": 24.0, "mean": 22.0}, "voltage": {"min": 100, "max": 100, "mean": 100}}
    weather = {"rainfall_mm": {"min": 4.2, "max": 4.2, "avg": 4.2}, "air_temperature_c": {"min": 18.2, "max": 26, "avg": 20.83},
               "air_humidity_pct": {"min": 60, "max": 98, "avg": 85.1}, "air_pressure_hpa": {"min": 912, "max": 916, "avg": 914.2},
               "wind_speed_ms": {"min": 0.36, "max": 7.2, "avg": 2.63}, "leaf_wetness": {"min": 30, "max": 748, "avg": 322},
               "lux": {"min": 0, "max": 52000, "avg": 9100}, "voltage": {"min": 100, "max": 100, "avg": 100}}
    return {"farmId": FARM_A, "sensorDevices": [
        {"macAddress": SOIL_MAC, "deviceName": "Soilsync Hub", "stats": {day: soil, today: soil}},
        {"macAddress": STRAY_MAC, "deviceName": "Not imported", "stats": {day: soil}}],
        "weatherDevices": [{"macAddress": CLIME_MAC, "deviceName": "Farmfuture MicroClime", "stats": {day: weather}}]}


def _forecast() -> dict:
    """3-hourly forecast in the live shape (kelvin): one step today, all 8 steps tomorrow, 4 steps the day after."""
    def step(day, hour: int, kelvin: float, rain: float | None) -> dict:
        at = datetime.combine(day, time(hour, 30), IST)
        return {"dt": int(at.timestamp()), "main": {"temp": kelvin, "temp_min": kelvin - 0.5, "temp_max": kelvin + 0.5,
                                                    "humidity": 70}, **({"rain": {"3h": rain}} if rain else {})}
    today = today_ist()
    tomorrow, after = today + timedelta(days=1), today + timedelta(days=2)
    slots = [step(today, 23, 290.0, 9.9)]
    slots += [step(tomorrow, h, 293.15 + i, 1.5 if i in (2, 3) else None) for i, h in enumerate(range(2, 24, 3))]
    slots += [step(after, h, 295.0, 5.0) for h in range(2, 14, 3)]
    return {"cod": "200", "cnt": len(slots), "list": slots,
            "city": {"name": "Madanahalli", "coord": {"lat": 13.3151, "lon": 76.1367}}}


def _advisory(run_date: str, generated: str, items: list[dict]) -> dict:
    return {"farmId": FARM_A, "runDate": run_date, "generatedAt": generated, "isStale": False, "items": items,
            "delivery": {"shown": [i["id"] for i in items], "more": []}}


def _item(n: int, run_date: str, code: str, kind: str = "action", window: bool = True) -> dict:
    return {"id": f"{FARM_A}:{run_date}:{n}", "code": code, "kind": kind, "intent": "check", "priority": 4,
            "title": f"Title {code}", "body": f"Body {code}", "certainty": None,
            "window": {"start": run_date, "end": run_date} if window else None}


@pytest.fixture()
def farmfuture(monkeypatch):
    """Fake FarmFuture API covering farm import and the farm-data endpoints; records every request."""
    yesterday, today = (today_ist() - timedelta(days=1)).isoformat(), today_ist().isoformat()
    older = (today_ist() - timedelta(days=3)).isoformat()
    state: dict = {"requests": [], "yesterday": yesterday, "older": older}
    monkeypatch.setattr(get_settings(), "farmfuture_api_token", "test-token")
    ok = lambda data: httpx.Response(200, json={"succeeded": True, "data": data})  # noqa: E731

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path, params = request.url.path, request.url.params
        if path == "/api/Estate/GetUserEstatesAndFarmsList":
            return ok([{"estateId": ESTATE, "estateName": "Hill Estate", "farms": [
                {"farmId": FARM_A, "farmName": "Upper"}, {"farmId": FARM_B, "farmName": "Lower"}]}])
        if path.startswith("/api/FarmProfile/"):
            return httpx.Response(200, json={"center": [75.39, 13.36], "crop_type": "arabica"})
        if path.endswith("/irrigation-plan"):
            return httpx.Response(200, json={"farm": {"area_ha": 2.0}})
        if path == f"/api/Data/GetUserDevices/{FARM_A}":
            return ok([{"macAdress": SOIL_MAC, "deviceTypeId": 4, "deviceName": "Soilsync Hub",
                        "createdDate": "2025-06-16T18:14:34Z", "isCallibrated": False},
                       {"macAdress": CLIME_MAC, "deviceTypeId": 5, "deviceName": "Farmfuture MicroClime",
                        "createdDate": "2025-06-16T18:13:18Z", "isCallibrated": True},
                       {"macAdress": HUB_MAC, "deviceTypeId": 1, "deviceName": "Hub"}])
        if path == "/api/Data/GetFarmStats":
            if params["farmId"] == FARM_A:
                return ok(_stats(yesterday, today))
            # optionally FARM_B lists FARM_A's weather station too (seen live: one station on two FarmFuture farms)
            shared = _stats(yesterday, today)["weatherDevices"] if state.get("share_station") else []
            return ok({"sensorDevices": [], "weatherDevices": shared})
        if path == f"/api/SoilLabReport/GetByFarm/{FARM_A}":
            return ok([{"id": "55555555-5555-4555-8555-555555555555", "labName": "KVK Soil Lab", "labReportNo": "SL-7",
                        "sampledOn": "2026-06-19T00:00:00Z", "reportedOn": "2026-07-01T10:00:00Z", "unitBasis": "kg_ha_oxide",
                        "ph": 5.6, "organicCarbon": 1.8, "n_KgHa": 357.3, "p2O5_KgHa": 38.9, "k2O_KgHa": 214.7, "isActive": True},
                       {"id": "66666666-6666-4666-8666-666666666666", "labName": None, "sampleRefId": "Soilsync Hub",
                        "reportedOn": "2026-10-06T11:27:52Z", "unitBasis": "kg_ha_oxide", "ph": 6.05, "isActive": True}])
        if path == "/api/wsb/records":
            if params["farm"] != FARM_A:
                return httpx.Response(200, json={"records": []})
            return httpx.Response(200, json={"records": [
                {"id": "77777777-7777-4777-8777-777777777777", "date": yesterday, "kind": "inspection", "topic": "shothole",
                 "inspected": 25, "affected": 2, "notes": "Checked 25 plants", "recordedBy": "+919800000000",
                 "point": {"lat": 12.0014, "lon": 76.0579}, "photos": ["x.jpg"]},
                {"id": "88888888-8888-4888-8888-888888888888", "date": yesterday, "kind": "inspection", "topic": "Thrips",
                 "inspected": 10, "affected": 0},
                {"id": "99999999-9999-4999-8999-999999999999", "date": yesterday, "kind": "trap", "topic": "wsb"}]})
        if path == f"/api/Satellite/ZoneSummary/{FARM_A}":
            return ok({"status": "ready", "observedOn": f"{older}T00:00:00", "zones": [
                {"zoneId": ZONE, "cells": 136, "avgNdvi": 0.76651543, "minNdvi": 0.4094, "maxNdvi": 0.8723,
                 "avgNdmi": 0.27103677, "decliningCells": 3}]})
        if path == "/api/wsb/sources":
            return ok({"sources": [{"dataset": "satellite", "sourceRef": "v2|S2C_MSIL2A_TEST_SCENE"}]})
        if path == f"/api/Advisory/{FARM_A}/latest":
            return httpx.Response(200, json=_advisory(yesterday, f"{yesterday}T09:20:51+05:30", [
                _item(0, yesterday, "irrigation.irrigate_by_date"), _item(1, yesterday, "rust.spray_if_not_done_in_september"),
                _item(2, yesterday, "outlook.pests_diseases", "outlook", window=False),
                _item(3, yesterday, "data.help_improve", "data_request", window=False)]))
        if path == f"/api/Advisory/{FARM_A}/history":
            return httpx.Response(200, json={"farmId": FARM_A, "days": [{"runDate": yesterday}, {"runDate": older}]})
        if path == "/WeatherForecast/forecast":
            return httpx.Response(200, json=_forecast())
        if path == "/api/Data/GetWeatherStats" and params["parameter"] == "rainfall_mm":
            state["weather_stats_params"] = dict(params)
            ten, nine = (today_ist() - timedelta(days=10)).isoformat(), (today_ist() - timedelta(days=9)).isoformat()
            # day -8 is missing (station did not report); yesterday lies inside the recent window and is ignored here
            return ok({"macAddress": params["macAddress"], "stats": {
                ten: {"rainfall_mm": {"last": 12.5}}, nine: {"rainfall_mm": {"last": 0}},
                yesterday: {"rainfall_mm": {"last": 99.0}}}})
        if path == "/api/Data/GetMinMaxMeanCalibrated":
            series = {"nitrogen": {"min": 350, "max": 370, "mean": 360}, "pH": {"min": 3.2, "max": 3.5, "mean": 3.38}}
            name = params["parameter"]
            return ok({"macAddress": params["macAddress"], "stats": {yesterday: {name: series[name]}} if name in series
                       else {}})
        if path == f"/api/Advisory/{FARM_A}" and params.get("date") == older:
            return httpx.Response(200, json=_advisory(older, f"{older}T08:00:00+05:30",
                                                      [_item(0, older, "stalk_rot.favourable_weather_watch")]))
        return httpx.Response(404, json={"status": 404, "message": "Not found"})

    monkeypatch.setattr(platform_adapters, "HTTP_TRANSPORT", httpx.MockTransport(dispatch))
    return state


def _register(api, adapter_code: str, **config) -> str:
    entry = next(c for c in api.as_("admin").get("/integrations/catalogue").json() if c["adapter_code"] == adapter_code)
    assert entry["is_mock"] is False and entry["kind"] == "live_api"
    code = f"t_{adapter_code}"
    r = api.as_("admin").post("/integrations/sources", json={
        "source_code": code, "name": entry["name"], "module_code": entry["module_code"], "source_type": "rest_pull",
        "adapter_code": adapter_code, "auth_type": "api_key", "is_mock": False,
        "config": {**entry["config"], "programme_code": PROGRAMME, **config}})
    assert r.status_code == 201, r.text
    return code


def _link_farms(api, db) -> FarmPlot:
    """Import the FarmFuture farms through the water-engine source, which links FARM_A and FARM_B to new plots."""
    water = _register(api, "farmfuture_water_engine")
    geo = db.scalars(select(GeoUnit).where(GeoUnit.unit_type == "cluster").order_by(GeoUnit.code)).first()
    assert api.as_("admin").post(f"/integrations/sources/{water}/import-farms",
                                 json={"geo_unit_id": str(geo.id)}).status_code == 200
    return db.scalar(select(FarmPlot).where(FarmPlot.plot_code == "FF-11111111-P1"))


def _records(db, code: str) -> list[FieldRecord]:
    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == code))
    return list(db.scalars(select(FieldRecord).where(FieldRecord.source_system_id == source_id)).unique())


def test_mapping_helpers() -> None:
    assert scouting_pest("shothole") == ("shot_hole_borer", None)
    assert scouting_pest("Coffee Leaf Rust") == ("leaf_rust", None)
    assert scouting_pest("Thrips") == ("other", "Thrips")
    assert advisory_module("rust.spray_if_not_done_in_september") == ("pest", "leaf_rust")
    assert advisory_module("stalk_rot.favourable_weather_watch") == ("pest", "stalk_rot")
    assert advisory_module("outlook.pests_diseases") == ("pest", "pests_and_diseases")
    assert advisory_module("irrigation.irrigate_by_date") == ("water", None)
    assert advisory_module("frost.warning") == ("climate", None)


def test_device_import_and_daily_sensor_pull(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate", past_days=1,
                     units={"nitrogen": "mg/kg", "phosphorus": "mg/kg", "potassium": "mg/kg", "leaf_wetness": "raw"})
    assert api.as_("admin").post(f"/integrations/{code}/pull").json()["error"]["code"] == "no_mapped_devices"

    out = api.as_("admin").post(f"/integrations/sources/{code}/import-devices").json()
    assert (out["devices_created"], out["already_imported"], len(out["skipped"])) == (2, 0, 1), out
    assert "type 1" in out["skipped"][0]  # the hub has no platform device type
    soil = db.scalar(select(Device).where(Device.device_code == f"FF-{SOIL_MAC}"))
    assert soil.device_type == "soilsync_7in1" and soil.farm_plot_id == plot.id and soil.status == "active"
    assert db.scalar(select(Device.device_type).where(Device.device_code == f"FF-{CLIME_MAC}")) == "microclime"
    again = api.as_("admin").post(f"/integrations/sources/{code}/import-devices").json()
    assert (again["devices_created"], again["already_imported"]) == (0, 2)

    farmfuture["requests"].clear()
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    # soil: L1, L2, soil temperature, EC, N, P, K (pH all-zero = offline, skipped); microclime: 7; lab report: 1
    assert (run["records_accepted"], run["records_rejected"]) == (15, 0), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    records = _records(db, code)
    readings = {(r.attributes["parameter"], r.attributes.get("layer")): r for r in records if r.record_type == "sensor_observation"}
    assert ("ph", None) not in readings and all(r.observed_at.date().isoformat() == farmfuture["yesterday"]
                                                for r in readings.values())
    rain = readings[("rainfall", None)]
    assert float(rain.value) == 4.2 and rain.attributes["aggregation"] == "daily_max" and rain.device_id is not None
    moisture = readings[("soil_moisture", "L2")]
    assert float(moisture.value) == 26.0 and moisture.attributes["value_min"] == 25.0 and moisture.farm_plot_id == plot.id
    # units as labelled in the FarmFuture app; an older source config ("mg/kg") cannot override them
    assert readings[("ec", None)].unit == "µS/cm" and readings[("nitrogen", None)].unit == "kg/ha"
    assert readings[("leaf_wetness", None)].unit == "raw" and readings[("wind_speed", None)].unit == "m/s"
    assert {r.attributes["series"] for r in readings.values()} == {"app"}
    assert readings[("nitrogen", None)].attributes["source_dataset"].endswith("(as shown in the FarmFuture app)")
    assert all(r.validation_status == "valid" and r.is_demo is False for r in records), [r.validation_messages for r in records]
    lab = next(r for r in records if r.record_type == "soil_test")  # the report with no laboratory named is skipped
    assert lab.attributes["lab_name"] == "KVK Soil Lab" and lab.attributes["p2o5_kg_ha"] == 38.9
    assert "phosphorus_kg_ha" not in lab.attributes and lab.attributes["report_ref"] == "SL-7"

    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 15)


def test_sensor_series_choice_and_implausible_readings(api, db, farmfuture) -> None:
    _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate", past_days=1, series="calibrated", include_lab_reports=False)
    api.as_("admin").post(f"/integrations/sources/{code}/import-devices")
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    # soil: L1, L2, soil temperature (app series) + N, pH (calibrated series; EC/P/K not supplied → left out); microclime: 7
    assert (run["records_accepted"], run["records_rejected"]) == (12, 0), run
    calls = [r for r in farmfuture["requests"] if r.url.path == "/api/Data/GetMinMaxMeanCalibrated"]
    assert {r.url.params["parameter"] for r in calls} == {"pH", "ec", "nitrogen", "phosphorous", "potassium"}
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    readings = {(r.attributes["parameter"], r.attributes.get("layer")): r for r in _records(db, code)}
    nitrogen = readings[("nitrogen", None)]
    assert float(nitrogen.value) == 360 and nitrogen.attributes["series"] == "calibrated"
    assert nitrogen.attributes["source_dataset"].endswith("calibrated series") and nitrogen.validation_status == "valid"
    assert readings[("soil_moisture", "L1")].attributes["series"] == "app"
    assert ("ec", None) not in readings and ("phosphorus", None) not in readings  # never mixed with app values
    ph = readings[("ph", None)]  # pH 3.2–3.5 is physically implausible: kept, but held for an analyst
    assert ph.validation_status == "exception" and "plausible range" in " ".join(ph.validation_messages)

    r = api.as_("admin").patch(f"/integrations/sources/{code}", json={"config": {"programme_code": PROGRAMME,
                                                                               "series": "smoothed"}})
    assert r.status_code == 200
    assert api.as_("admin").post(f"/integrations/{code}/pull").json()["error"]["code"] == "source_config"


def test_water_plan_block_and_sensor_detail() -> None:
    from app.services.integration.farmfuture import plan_attributes
    plan = {"model_version": "1.3.0", "farm_id": FARM_A, "headline": {"status": "IRRIGATE_BY", "title": "Irrigate by Tue"},
            "water_needed": {"depth_mm": 25.0, "total_m3": 433.7}, "season": {"label": "Early dry season"},
            "plan_tier": {"tier": "FULL"}, "zone_totals": {"blocks": 2, "blocks_needing_water": 1},
            "zones": [{"needs_by": "2026-11-12"}, {"needs_by": "2026-11-10"}, {"needs_by": None}],
            "today": {"sensor": {"available": True, "reading_pct": 12.8, "model_pct": 33.9, "agrees": False,
                                 "difference_pct": -21.1, "uncalibrated": False}}}
    a = plan_attributes(plan)
    assert (a["recommended_irrigation_mm"], a["needs_water_by"], a["blocks_total"], a["blocks_needing_water"]) == (
        25.0, "2026-11-10", 2, 1)
    assert (a["sensor_difference_pct"], a["sensor_calibrated"], a["plan_tier"]) == (-21.1, True, "FULL")
    assert a["season_label"] == "Early dry season"
    assert plan_attributes({"today": {"sensor": {"available": False, "difference_pct": 5}}})["sensor_difference_pct"] is None


def test_forecast_days_are_whole_ist_days_in_celsius() -> None:
    days = forecast_days(_forecast(), today_ist())
    assert [d["forecast_date"] for d in days] == [today_ist() + timedelta(days=1)]  # today and the part day dropped
    day = days[0]
    assert day["rainfall_mm"] == 3.0 and day["relative_humidity_pct"] == 70.0
    assert (day["temp_min_c"], day["temp_max_c"]) == (19.5, 27.5)  # 293.15 K - 0.5 … 300.15 K + 0.5


def test_weather_station_days_and_forecast(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    soil = _register(api, "farmfuture_soil_microclimate")
    assert api.as_("admin").post(f"/integrations/sources/{soil}/import-devices").json()["devices_created"] == 2
    # a second soil source re-uses the device links instead of skipping the devices
    second = api.as_("admin").post("/integrations/sources", json={
        "source_code": "t_soil_two", "name": "Second", "source_type": "rest_pull", "is_mock": False,
        "adapter_code": "farmfuture_soil_microclimate", "config": {"programme_code": PROGRAMME}})
    assert second.status_code == 201
    again = api.as_("admin").post("/integrations/sources/t_soil_two/import-devices").json()
    assert (again["devices_created"], again["already_imported"]) == (0, 2)
    farmfuture["requests"].clear()
    farmfuture["share_station"] = True  # FARM_B also lists the station; it stays on the plot its device is registered on

    weather = _register(api, "farmfuture_weather")
    run = api.as_("admin").post(f"/integrations/{weather}/pull").json()["sync_run"]
    # FARM_A: 1 station day + 2 earlier rain days; FARM_A and FARM_B (both located): 1 whole forecast day each
    assert (run["records_accepted"], run["records_rejected"]) == (5, 0), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    asked = farmfuture["weather_stats_params"]
    assert asked["macAddress"] == CLIME_MAC and asked["endDate"].startswith((today_ist() - timedelta(days=4)).isoformat())
    records = _records(db, weather)
    assert {r.farm_plot_id for r in records if r.record_type == "climate_observation"} == {plot.id}
    history = sorted((r for r in records if r.record_type == "climate_observation" and "temp_max_c" not in r.attributes),
                     key=lambda r: r.observed_at)
    assert [r.attributes["rainfall_mm"] for r in history] == [12.5, 0.0]  # a missing day stays missing, never 0 mm
    assert history[0].observed_at.date() == today_ist() - timedelta(days=10) and history[0].device_id is not None
    run = api.as_("admin").post(f"/integrations/{weather}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 3)  # history days already imported are not re-sent
    station = next(r for r in records if r.record_type == "climate_observation" and "temp_max_c" in r.attributes)
    assert station.module_code == "climate" and station.data_nature == "observed" and station.farm_plot_id == plot.id
    assert station.device_id == db.scalar(select(Device.id).where(Device.device_code == f"FF-{CLIME_MAC}"))
    a = station.attributes
    assert (a["rainfall_mm"], a["temp_max_c"], a["temp_min_c"], a["relative_humidity_pct"]) == (4.2, 26.0, 18.2, 85.1)
    forecast = next(r for r in records if r.record_type == "weather_forecast" and r.farm_plot_id == plot.id)
    assert forecast.data_nature == "model_prediction" and float(forecast.value) == 3.0
    assert "Madanahalli" in forecast.attributes["dataset"] and forecast.attributes["issued_on"] == today_ist().isoformat()
    assert all(r.validation_status == "valid" for r in records), [r.validation_messages for r in records]


def test_pull_source_needs_an_existing_programme(api, db, farmfuture) -> None:
    body = {"source_code": "t_no_programme", "name": "No programme", "source_type": "rest_pull",
            "adapter_code": "farmfuture_satellite", "is_mock": False, "config": {}}
    r = api.as_("admin").post("/integrations/sources", json=body)
    assert r.status_code == 422 and "Programme code is required" in r.json()["error"]["message"]
    r = api.as_("admin").post("/integrations/sources", json={**body, "config": {"programme_code": "NOPE"}})
    assert r.status_code == 422 and "does not exist" in r.json()["error"]["message"]
    code = _register(api, "farmfuture_satellite")
    r = api.as_("admin").patch(f"/integrations/sources/{code}", json={"config": {"programme_code": None}})
    assert r.status_code == 422
    r = api.as_("admin").patch(f"/integrations/sources/{code}", json={"config": {"programme_code": PROGRAMME}})
    assert r.status_code == 200 and r.json()["config"]["programme_code"] == PROGRAMME


def test_pulls_need_linked_farms(api, db, farmfuture) -> None:
    code = _register(api, "farmfuture_satellite")
    r = api.as_("admin").post(f"/integrations/{code}/pull")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_linked_farms"
    soil = _register(api, "farmfuture_soil_microclimate")
    assert api.as_("analyst").post(f"/integrations/sources/{soil}/import-devices").status_code == 403


def test_scouting_satellite_and_advisories(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    farmfuture["requests"].clear()

    scouting = _register(api, "farmfuture_pest_scouting")
    run = api.as_("admin").post(f"/integrations/{scouting}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (2, 0), run  # the trap record is not an inspection
    found = {r.attributes["pest_or_disease"]: r for r in _records(db, scouting)}
    shot = found["shot_hole_borer"]
    assert shot.record_type == "pest_inspection" and shot.farm_plot_id == plot.id and float(shot.value) == 8.0
    assert found["other"].attributes["pest_other_name"] == "Thrips"
    stored = json.dumps([r.attributes for r in found.values()])
    assert "+91" not in stored and "Checked" not in stored and "12.0014" not in stored  # no phone, notes or GPS

    satellite = _register(api, "farmfuture_satellite")
    run = api.as_("admin").post(f"/integrations/{satellite}/pull").json()["sync_run"]
    assert run["records_accepted"] == 1, run
    ndvi = _records(db, satellite)[0]
    assert ndvi.record_type == "vegetation_index" and float(ndvi.value) == 0.7665
    assert ndvi.attributes["scene_id"] == "S2C_MSIL2A_TEST_SCENE" and ndvi.attributes["ndmi_mean"] == 0.271
    assert (ndvi.attributes["zone_ref"], ndvi.attributes["declining_cells"]) == (ZONE, 3)

    advisories = _register(api, "farmfuture_advisories", history_days=5)
    run = api.as_("admin").post(f"/integrations/{advisories}/pull").json()["sync_run"]
    assert (run["payload_kind"], run["records_accepted"], run["records_rejected"]) == ("advisories", 4, 0), run
    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == advisories))
    rows = {a.title: a for a in db.scalars(select(Advisory).where(Advisory.source_system_id == source_id))}
    rust = rows["Title rust.spray_if_not_done_in_september"]
    assert (rust.origin_module, rust.hazard_type, rust.risk_level, rust.status) == ("pest", "leaf_rust", None, "issued")
    assert rust.valid_until is not None and rust.is_demo is False
    assert rows["Title irrigation.irrigate_by_date"].origin_module == "water"
    assert rows["Title stalk_rot.favourable_weather_watch"].hazard_type == "stalk_rot"  # from the older run (history)
    assert "Title data.help_improve" not in rows  # data requests are skipped
    delivery = db.scalar(select(AdvisoryDelivery).where(AdvisoryDelivery.advisory_id == rust.id))
    assert delivery.channel == "app" and delivery.sent_at is not None
    assert delivery.delivered_at is None and delivery.read_at is None  # never counted as delivered or read

    assert {r.method for r in farmfuture["requests"]} == {"GET"}
