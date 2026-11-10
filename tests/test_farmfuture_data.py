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
from app.services.integration.farmfuture_data import advisory_module, forecast_days, scouting_pest, site_attributes
from app.services.integration.platform_adapters import IST, today_ist

PROGRAMME = "CCRL-INCLUSIVE"
FARM_A = "11111111-1111-4111-8111-111111111111"
FARM_B = "22222222-2222-4222-8222-222222222222"
ESTATE = "33333333-3333-4333-8333-333333333333"
ZONE = "44444444-4444-4444-8444-444444444444"
SOIL_MAC, CLIME_MAC, HUB_MAC, STRAY_MAC = "A0B765293650", "78421CA2A790", "2805A52F3914", "FFFFFFFFFFFF"
ANALOGUE_MAC = "E86BEAFAB9B4"


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


def _climate() -> dict:
    """FarmFuture farm-profile climate (30-year normals) in the live shape; the location is never stored."""
    months = [{"month": m, "month_name": "x", "mean_mm": 10.0 * m, "std_mm": 1.0, "min_mm": 0.0, "max_mm": 30.0 * m}
              for m in range(1, 13)]
    return {"farm_id": FARM_A, "lat": 12.0016, "lon": 76.0564,
            "series": {"start_year": 1996, "end_year": 2025, "rainfall_source": "CHIRPS Daily v2.0",
                       "temperature_source": "ERA5 via Open-Meteo"},
            "annual_rainfall": {"mean_mm": 780.0, "std_mm": 120.5, "cv_pct": 15.4}, "monthly_rainfall": months,
            "seasonal_rainfall": [{"season": "sw_monsoon", "pct_of_annual": 68.2}],
            "dry_spell": {"threshold_mm": 1.0, "mean_max_annual_cdd": 67.3, "longest_cdd_on_record": 105,
                          "longest_cdd_year": 2023, "mean_annual_dry_days": 257.6},
            "annual_temperature": {"mean_c": 21.62, "mean_min_c": 18.43, "mean_max_c": 26.55}}


def _profile() -> dict:
    """FarmFuture farm profile summary and hydrology in the live shape (outline, points and devices are never stored)."""
    return {"built_at": "2026-09-22T06:38:48+00:00", "ring": [[75.39, 13.36]], "terrain_points": [{"lat": 13.36, "lon": 75.39}],
            "summary": {"terrain": {"elevation_mean_m": 876.3, "elevation_min_m": 861.5, "elevation_max_m": 894.0,
                                    "slope_mean_deg": 5.71, "slope_max_deg": 11.44, "slope_class": "gentle", "steep_cell_pct": 0.0,
                                    "aspect": "S", "drainage": "imperfectly_drained", "frost_risk": "low-medium"},
                        "soil": {"ph": 5.1, "texture": "Clay loam", "clay": 39.0, "sand": 35.5, "silt": 25.5, "oc_pct": 1.75,
                                 "bulk_density": 1.12, "field_capacity": 36.833, "whc_class": "high", "whc_mm_per_m": 212.0}},
            "hydrology": {"erosion": {"rusle_mean_t_ha_yr": 65.33, "rusle_max_t_ha_yr": 241.85, "dominant_class": "severe",
                                      "class_pct": {"severe": 62.5, "very high": 25.0, "moderate": 5.4, "slight": 5.4}},
                          "wetness": {"twi_mean": 7.21, "saturation_prone_pct": 21.4}}}


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
    now = datetime.now(IST)
    state: dict = {"requests": [], "yesterday": yesterday, "older": older,
                   "recent": (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                   "stale": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")}
    monkeypatch.setattr(get_settings(), "farmfuture_api_token", "test-token")
    ok = lambda data: httpx.Response(200, json={"succeeded": True, "data": data})  # noqa: E731

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path, params = request.url.path, request.url.params
        if path == "/api/Estate/GetUserEstatesAndFarmsList":
            return ok([{"estateId": ESTATE, "estateName": "Hill Estate", "farms": [
                {"farmId": FARM_A, "farmName": "Upper"}, {"farmId": FARM_B, "farmName": "Lower"}]}])
        if path == f"/api/FarmProfile/{FARM_A}/climate":
            return httpx.Response(200, json=_climate())
        if path.endswith("/climate"):
            return httpx.Response(404, json={"detail": "No climate profile"})
        if path.startswith("/api/FarmProfile/"):
            return httpx.Response(200, json={"center": [75.39, 13.36], "crop_type": "arabica", **_profile()})
        if path.endswith("/irrigation-plan"):
            return httpx.Response(200, json={"farm": {"area_ha": 2.0}})
        if path == f"/api/Data/GetUserDevices/{FARM_A}":
            # positions: SoilSync ~150 m from the farm point, MicroClime ~250 km away (a wrong position), Analogue unset
            return ok([{"macAdress": SOIL_MAC, "deviceTypeId": 4, "deviceName": "Soilsync Hub",
                        "createdDate": "2025-06-16T18:14:34Z", "isCallibrated": False, "lat": 13.3612, "long": 75.3911},
                       {"macAdress": CLIME_MAC, "deviceTypeId": 5, "deviceName": "Farmfuture MicroClime",
                        "createdDate": "2025-06-16T18:13:18Z", "isCallibrated": True, "lat": 12.97, "long": 77.59},
                       {"macAdress": ANALOGUE_MAC, "deviceTypeId": 11, "deviceName": "Analogue Weather ", "lat": 0, "long": 0},
                       {"macAdress": HUB_MAC, "deviceTypeId": 1, "deviceName": "Hub"}])
        if path == f"/api/Data/GetAllSensorsLatestDataV2/{FARM_A}":
            return ok([{"macId": SOIL_MAC, "deviceName": "Soilsync Hub", "updateAt": state["recent"], "lat": 12.0011,
                        "soilProperties": {"voltage": 100, "ph": 5.3}}])
        if path == f"/api/Data/GetAllSensorsLatestData/{FARM_A}":  # the FarmFuture Sensors page (uncorrected reading)
            npk = {"isNpkDisplayed": not state.get("npk_hidden"),
                   "displayMessage": "Soil is dry at all levels. Water now." if state.get("npk_hidden")
                   else "Soil moisture is optimal at both levels. Nutrient readings are accurate."}
            return ok([{"macId": SOIL_MAC, "deviceName": "Soilsync Hub", "updateAt": "2026-11-09 14:25:37", "lat": 12.0011,
                        "long": 76.05, "addOnId": 1, "moistureThresholdsResponse": npk,
                        "soilProperties": {"soilMoisturePercentage1": 21.5, "soilMoisturePercentage2": 25,
                                           "temperature": 27.70000076, "ph": 6.869999886, "electricalConductivity": 49,
                                           "voltage": 100},
                        "soilNutrients": {"nitrogen": 305, "phosphorous": 1230, "potassium": 278},
                        "extraParameters": {"solarRadiation": 179, "lux": 0}}])
        if path == "/api/Data/GetAllWeatherData" and params["farmId"] == FARM_A:
            return ok([{"macId": CLIME_MAC, "deviceName": "Farmfuture MicroClime", "updateAt": state["stale"], "voltage": 97.5}])
        if path == "/api/Data/GetAllAnalogueWeatherData" and params["farmId"] == FARM_A:
            return ok([{"macId": ANALOGUE_MAC, "deviceName": "Analogue Weather ", "updateAt": state["recent"],
                        "airTemperature": 26.3, "airHumidity": 80.05, "airPressure": 0, "windSpeed": 3.17, "leafWetness": 3160,
                        "lux": 5401.3, "rainfall": 12.0, "batteryPercentage": 0, "signalStrength": 17, "soilTemperature": -1,
                        "moisture1": 53, "moisture2": 2294}])
        if path in ("/api/Data/GetAllWeatherData", "/api/Data/GetAllAnalogueWeatherData"):
            return ok([])
        if path == "/api/Data/GetFarmStats":
            if params["farmId"] == FARM_A:
                stats = _stats(yesterday, today)
                if state.get("soil_history"):  # an earlier day inside sensor_history_days
                    stats["sensorDevices"][0]["stats"][older] = stats["sensorDevices"][0]["stats"][yesterday]
                return ok(stats)
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
        if path == f"/api/EudrReadiness/{FARM_A}":
            return ok({"farmId": FARM_A, "farmName": "Upper", "hasBoundary": False, "hasReport": False, "missing": ["BOUNDARY", "REPORT"],
                       "screening": {"id": "abababab-0000-4000-8000-000000000001", "complianceFlag": "Compliant (risk-based)",
                                     "complianceReason": "No deforestation detected; compliant.", "riskScore": 0,
                                     "deforestedSince2020Ha": 0, "screenedAt": "2026-03-03T07:05:41Z"}})
        if path.startswith("/api/EudrReadiness/"):
            return ok({"hasBoundary": False, "screening": None, "missing": ["BOUNDARY", "SCREENING", "REPORT"]})
        if path == f"/api/plots/{FARM_A}/harvests":
            return ok([{"id": "cdcdcdcd-0000-4000-8000-000000000001", "farmId": FARM_A, "cropId": "c-robusta",
                        "harvestDate": "2026-01-15T00:00:00Z", "grossQuantity": 1250.5, "quantityUnit": "KG",
                        "coffeeForm": "CHERRY", "season": "2025/26", "status": "AVAILABLE_FOR_BATCHING",
                        "notes": "Picked by Ramesh, phone +919800000000", "evidenceRef": "photo.jpg"},
                       {"id": "cdcdcdcd-0000-4000-8000-000000000002", "harvestDate": "2026-01-20T00:00:00Z",
                        "grossQuantity": 10, "quantityUnit": "BAG", "status": "CANCELLED"},
                       {"id": "cdcdcdcd-0000-4000-8000-000000000003", "harvestDate": "2026-01-22T00:00:00Z",
                        "grossQuantity": 3, "quantityUnit": "BASKET", "status": "CLOSED"}])
        if path == f"/api/plots/{FARM_A}/harvest-context":
            return ok({"crops": [{"cropId": "c-robusta", "cropType": "Coffee-Robusta"}]})
        if path.endswith("/harvests"):
            return ok([])
        if path == "/api/StaticData/GetCropsData":
            return ok([{"cropId": "c-arabica", "cropType": "Coffee-Arabica"}, {"cropId": "c-robusta", "cropType": "Coffee-Robusta"}])
        if path == "/api/CalendarV2/GetDefaultCalendarEventsByMonth":
            assert params["cropid"] == "c-robusta"
            return ok([{"id": "t1", "description": "Install broca traps in field and drying yards if not done.",
                        "category": "Established blocks", "startDate": "2025-11-01T00:00:00Z", "endDate": "2025-11-30T00:00:00Z"},
                       {"id": "t2", "description": "Take up lime washing on young dadap stems.", "category": "Established blocks"},
                       {"id": "t3", "description": "Take up clean weeding in Robusta blocks.", "category": "Established blocks"}])
        if path == "/api/Data/GetWeatherStats" and params.get("parameter") == "rainfall_mm":
            state["weather_stats_params"] = dict(params)
            ten, nine = (today_ist() - timedelta(days=10)).isoformat(), (today_ist() - timedelta(days=9)).isoformat()
            # day -8 is missing (station did not report); yesterday lies inside the recent window and is ignored here
            return ok({"macAddress": params["macAddress"], "stats": {
                ten: {"rainfall_mm": {"last": 12.5}}, nine: {"rainfall_mm": {"last": 0}},
                yesterday: {"rainfall_mm": {"last": 99.0}}}})
        if path == f"/api/Calendar/GetUserCalendarEvents/{FARM_A}":  # the farm calendar (crop stage for the pest screen)
            return ok({"userCommonCalendarEvents": [{"title": "Berry development", "start": "2024-01-01T00:00:00",
                                                     "end": "2024-12-31T00:00:00"}], "userCalendarEvents": []})
        if path == "/api/Data/GetMinMaxMean":  # FarmFuture's Device Statistics screen (N, P, K, pH not corrected)
            assert params["macAddress"] == SOIL_MAC
            long_ago = (today_ist() - timedelta(days=20)).isoformat()  # outside the asked week: left out
            return ok({"startDate": params["startDate"], "endDate": params["endDate"], "macAddress": SOIL_MAC, "stats": {
                yesterday: {"nitrogen": {"min": 384, "max": 386, "mean": 385}, "pH": {"min": 5.2, "max": 5.3, "mean": 5.26},
                            "temperature": {"min": 21, "max": 22, "mean": 21.54}},
                older: {"nitrogen": {"min": 380, "max": 385, "mean": 384}, "pH": {"min": 4.86, "max": 5.39, "mean": 5.1},
                        "temperature": {"min": 21, "max": 21.5, "mean": 21.4}},
                long_ago: {"nitrogen": {"min": 1, "max": 999, "mean": 500}}}})
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


def test_site_attributes_need_a_profile() -> None:
    assert site_attributes({"summary": {}}) is None
    assert site_attributes(_profile())["erosion_mean_t_ha_yr"] == 65.3


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
    assert (out["devices_created"], out["already_imported"], len(out["skipped"])) == (3, 0, 1), out
    assert "type 1" in out["skipped"][0]  # the hub has no platform device type
    soil = db.scalar(select(Device).where(Device.device_code == f"FF-{SOIL_MAC}"))
    assert soil.device_type == "soilsync_7in1" and soil.farm_plot_id == plot.id and soil.status == "active"
    assert db.scalar(select(Device.device_type).where(Device.device_code == f"FF-{CLIME_MAC}")) == "microclime"
    assert db.scalar(select(Device.device_type).where(Device.device_code == f"FF-{ANALOGUE_MAC}")) == "analogue_weather"
    again = api.as_("admin").post(f"/integrations/sources/{code}/import-devices").json()
    assert (again["devices_created"], again["already_imported"]) == (0, 3)

    farmfuture["requests"].clear()
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    # soil: L1, L2, soil temperature, EC, N, P, K (pH all-zero = offline, skipped); microclime: 7; lab report: 1;
    # device health: 3 snapshots; Analogue Weather: 5 latest readings (pressure 0, running rain, soil channels skipped)
    assert (run["records_accepted"], run["records_rejected"]) == (23, 0), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    records = _records(db, code)
    analogue = {r.attributes["parameter"]: r for r in records
                if r.record_type == "sensor_observation" and r.attributes.get("aggregation") == "reading"}
    assert set(analogue) == {"air_temperature", "humidity", "wind_speed", "leaf_wetness", "light_intensity"}
    assert analogue["wind_speed"].unit == "m/s" and float(analogue["leaf_wetness"].value) == 3160
    health = {r.device.device_code: r for r in records if r.record_type == "device_health"}
    assert health[f"FF-{ANALOGUE_MAC}"].attributes == {"battery_pct": 0.0, "signal_csq": 17, "reporting_now": True,
                                                         "source_name": "FarmFuture device status"}
    assert health[f"FF-{CLIME_MAC}"].attributes["reporting_now"] is False  # last reading two days ago
    assert health[f"FF-{SOIL_MAC}"].attributes["battery_pct"] == 100.0
    readings = {(r.attributes["parameter"], r.attributes.get("layer")): r for r in records
                if r.record_type == "sensor_observation" and r.attributes.get("aggregation") != "reading"}
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
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 23)

    devices = {d["device_code"]: d for d in api.as_("admin").get("/devices", params={"search": "FF-"}).json()["items"]}
    soil = devices[f"FF-{SOIL_MAC}"]  # one day with readings, installed long ago → 1 of 30 expected days
    assert (soil["reporting_days_30"], soil["expected_days_30"], soil["battery_pct"]) == (1, 30, 100.0)
    assert devices[f"FF-{ANALOGUE_MAC}"]["signal_csq"] == 17 and devices[f"FF-{ANALOGUE_MAC}"]["last_reading_at"]


def test_sensor_series_choice_and_implausible_readings(api, db, farmfuture) -> None:
    _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate", past_days=1, series="calibrated", include_lab_reports=False,
                     device_health=False, analogue_readings=False)
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


def test_sensor_history_is_filled_in_once(api, db, farmfuture) -> None:
    _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate", past_days=1, include_lab_reports=False, device_health=False,
                     analogue_readings=False)
    api.as_("admin").post(f"/integrations/sources/{code}/import-devices")
    farmfuture["soil_history"] = True
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (21, 0), run  # (7 soil + 7 weather) yesterday + 7 soil earlier
    asked = next(r for r in farmfuture["requests"] if r.url.path == "/api/Data/GetFarmStats").url.params
    assert asked["startDate"].startswith((today_ist() - timedelta(days=30)).isoformat())
    farmfuture["requests"].clear()
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 14)  # the earlier day is not sent again


def test_harvest_records(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    code = _register(api, "farmfuture_harvests")
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    # one harvest imported, the cancelled one skipped, the one in an unknown unit (basket) quarantined – never guessed
    assert (run["records_accepted"], run["records_rejected"]) == (1, 1), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    (harvest,) = _records(db, code)
    a = harvest.attributes
    assert (harvest.record_type, harvest.data_nature, harvest.farm_plot_id) == ("harvest_record", "user_reported", plot.id)
    assert (float(harvest.value), harvest.unit, a["coffee_form"], a["crop_type"], a["season"]) == (
        1250.5, "kg", "cherry", "Coffee-Robusta", "2025/26")
    assert "Ramesh" not in json.dumps(a) and "photo" not in json.dumps(a)  # notes and evidence files are not imported


def test_coffee_calendar_is_read_live(api, farmfuture) -> None:
    from app.services.integration import farmfuture_data
    farmfuture_data._CALENDAR_CACHE.clear()
    r = api.as_("field").get("/coffee-calendar", params={"month": 11, "crop": "robusta"})
    assert r.status_code == 200, r.text
    tasks = {t["id"]: t for t in r.json()["tasks"]}
    assert tasks["t1"]["suggested_activity_code"] == "PEST_PREVENTION" and tasks["t1"]["window"] == "01 Nov – 30 Nov"
    assert tasks["t2"]["suggested_activity_code"] == "SHADE_CANOPY"  # lime washing protects shade trees, not soil pH
    assert tasks["t3"]["suggested_activity_code"] is None  # staff choose the practice
    assert {q.method for q in farmfuture["requests"]} == {"GET"}
    assert api.as_("field").get("/coffee-calendar", params={"month": 13}).status_code == 422
    farmfuture_data._CALENDAR_CACHE.clear()


def test_forecast_days_are_whole_ist_days_in_celsius() -> None:
    days = forecast_days(_forecast(), today_ist())
    assert [d["forecast_date"] for d in days] == [today_ist() + timedelta(days=1)]  # today and the part day dropped
    day = days[0]
    assert day["rainfall_mm"] == 3.0 and day["relative_humidity_pct"] == 70.0
    assert (day["temp_min_c"], day["temp_max_c"]) == (19.5, 27.5)  # 293.15 K - 0.5 … 300.15 K + 0.5


def test_weather_station_days_and_forecast(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    soil = _register(api, "farmfuture_soil_microclimate")
    assert api.as_("admin").post(f"/integrations/sources/{soil}/import-devices").json()["devices_created"] == 3
    # a second soil source re-uses the device links instead of skipping the devices
    second = api.as_("admin").post("/integrations/sources", json={
        "source_code": "t_soil_two", "name": "Second", "source_type": "rest_pull", "is_mock": False,
        "adapter_code": "farmfuture_soil_microclimate", "config": {"programme_code": PROGRAMME}})
    assert second.status_code == 201
    again = api.as_("admin").post("/integrations/sources/t_soil_two/import-devices").json()
    assert (again["devices_created"], again["already_imported"]) == (0, 3)
    farmfuture["requests"].clear()
    farmfuture["share_station"] = True  # FARM_B also lists the station; it stays on the plot its device is registered on

    weather = _register(api, "farmfuture_weather")
    run = api.as_("admin").post(f"/integrations/{weather}/pull").json()["sync_run"]
    # FARM_A: 1 station day + 2 earlier rain days + 30-year normals; FARM_A and FARM_B (located): 1 forecast day each
    assert (run["records_accepted"], run["records_rejected"]) == (6, 0), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    asked = farmfuture["weather_stats_params"]
    assert asked["macAddress"] == CLIME_MAC and asked["endDate"].startswith((today_ist() - timedelta(days=4)).isoformat())
    records = _records(db, weather)
    assert {r.farm_plot_id for r in records if r.record_type == "climate_observation"} == {plot.id}
    history = sorted((r for r in records if r.record_type == "climate_observation" and "temp_max_c" not in r.attributes),
                     key=lambda r: r.observed_at)
    assert [r.attributes["rainfall_mm"] for r in history] == [12.5, 0.0]  # a missing day stays missing, never 0 mm
    assert history[0].observed_at.date() == today_ist() - timedelta(days=10) and history[0].device_id is not None
    normals = next(r for r in records if r.record_type == "climate_normals")
    n = normals.attributes
    assert (normals.data_nature, normals.farm_plot_id, float(normals.value)) == ("calculated", plot.id, 780.0)
    assert (n["rain_jan_mm"], n["rain_dec_mm"], n["rain_dec_max_mm"], n["period_end_year"]) == (10.0, 120.0, 360.0, 2025)
    assert n["rainfall_dataset"] == "CHIRPS Daily v2.0" and "12.0016" not in json.dumps(n)  # no location stored
    assert normals.validation_status == "valid"
    run = api.as_("admin").post(f"/integrations/{weather}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 4)  # history days already imported are not re-sent
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
    assert run["records_rejected"] == 0, run
    pulled = _records(db, scouting)
    found = {r.attributes["pest_or_disease"]: r for r in pulled if r.record_type == "pest_inspection"}
    assert len(found) == 2  # the trap record is not an inspection
    # FarmFuture's Farm Health pest & disease screen, run on the farm's forecast, station, calendar and scouting
    risk = {r.attributes["pest_or_disease"]: r for r in pulled
            if r.record_type == "pest_risk_estimate" and r.farm_plot_id == plot.id}
    shot = risk["shot_hole_borer"]  # 2 of 25 plants with signs yesterday
    assert (shot.attributes["risk_level"], shot.attributes["screening_status"], shot.attributes["inspect_now"]) == (
        "very_high", "Signs found", True)
    assert shot.data_nature == "model_prediction" and shot.validation_status == "valid"
    assert shot.attributes["model_name"] == "FarmFuture Farm Health pest & disease screen (coffee-screen-2026-09-v2)"
    assert shot.attributes["why"].startswith("Inspection: 2 of 25 plants had signs found on")
    assert (shot.attributes["valid_from"], shot.attributes["next_check_by"]) == (
        today_ist().isoformat(), (today_ist() + timedelta(days=1)).isoformat())
    berry = risk["coffee_berry_borer"]  # the farm calendar puts the crop in berry development
    assert (berry.attributes["risk_level"], berry.attributes["screening_status"]) == ("low", "Estimated berry-stage watch")
    wsb = risk["white_stem_borer"]  # an Arabica farm in a white-stem-borer flight month (or not), by the calendar
    flight = today_ist().month in (3, 4, 5, 9, 10, 11, 12)
    assert (wsb.attributes["risk_level"], wsb.attributes["on_seasonal_watch_list"]) == (
        "high" if flight else "low", flight)
    assert "12.0014" not in json.dumps([r.attributes for r in risk.values()])  # scouting GPS points are not passed on
    asked = {r.url.path for r in farmfuture["requests"]}
    assert {"/WeatherForecast/forecast", f"/api/Calendar/GetUserCalendarEvents/{FARM_A}"} <= asked
    shot = found["shot_hole_borer"]
    assert shot.record_type == "pest_inspection" and shot.farm_plot_id == plot.id and float(shot.value) == 8.0
    assert found["other"].attributes["pest_other_name"] == "Thrips"
    stored = json.dumps([r.attributes for r in found.values()])
    assert "+91" not in stored and "Checked" not in stored and "12.0014" not in stored  # no phone, notes or GPS

    satellite = _register(api, "farmfuture_satellite")
    run = api.as_("admin").post(f"/integrations/{satellite}/pull").json()["sync_run"]
    assert run["records_accepted"] == 4, run  # FARM_A: zone NDVI + site profile + screening; FARM_B: site profile only
    check = next(r for r in _records(db, satellite) if r.record_type == "deforestation_check")
    c = check.attributes
    assert (check.farm_plot_id, float(check.value), c["compliance_flag"], c["screened_on"]) == (
        plot.id, 0.0, "Compliant (risk-based)", "2026-03-03")
    assert (c["boundary_mapped"], c["report_available"], c["missing_items"]) == (False, False, "boundary, report")
    assert check.validation_status == "valid" and check.data_nature == "calculated"
    site = next(r for r in _records(db, satellite) if r.record_type == "site_characteristics" and r.farm_plot_id == plot.id)
    s = site.attributes
    assert (site.data_nature, float(site.value), s["erosion_class"], s["erosion_severe_share_pct"]) == ("assessment", 65.3, "severe", 87.5)
    assert (s["elevation_mean_m"], s["drainage_class"], s["water_holding_mm_per_m"]) == (876.3, "imperfectly drained", 212.0)
    assert "13.36" not in json.dumps(s) and site.validation_status == "valid"  # no outline, points or location stored
    ndvi = next(r for r in _records(db, satellite) if r.record_type == "vegetation_index")
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


def test_device_positions_on_the_map_with_privacy(api, db, farmfuture) -> None:
    plot = _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate")
    out = api.as_("admin").post(f"/integrations/sources/{code}/import-devices").json()
    assert (out["devices_created"], out["positions_updated"]) == (3, 2), out  # the unset (0, 0) position is not stored
    ids = {mac: db.scalar(select(Device.id).where(Device.device_code == f"FF-{mac}"))
           for mac in (SOIL_MAC, CLIME_MAC, ANALOGUE_MAC)}

    soil = api.as_("admin").get(f"/devices/{ids[SOIL_MAC]}").json()
    assert (soil["latitude"], soil["longitude"], soil["location_source"]) == (13.3612, 75.3911, "FarmFuture")
    assert soil["location_check"] == "ok" and soil["distance_from_plot_km"] < 0.5
    clime = api.as_("admin").get(f"/devices/{ids[CLIME_MAC]}").json()
    assert clime["location_check"] == "far_from_plot" and clime["distance_from_plot_km"] > 200
    assert api.as_("admin").get(f"/devices/{ids[ANALOGUE_MAC]}").json()["location_check"] == "no_position"
    # an exact position is a precise location: hidden without farmer.read_pii, the check stays visible
    hidden = api.as_("analyst").get(f"/devices/{ids[SOIL_MAC]}").json()
    assert (hidden["latitude"], hidden["longitude"], hidden["location_check"]) == (None, None, "ok")
    assert all(d["latitude"] is None for d in api.as_("analyst").get("/devices", params={"page_size": 200}).json()["items"])

    def plot_devices(who: str) -> tuple[dict, dict]:
        body = api.as_(who).get("/map/plots").json()
        return {d["device_code"]: d for p in body["plots"] if p["id"] == str(plot.id) for d in p["devices"]}, body["devices"]
    on_map, summary = plot_devices("admin")
    assert on_map[f"FF-{SOIL_MAC}"]["latitude"] == 13.3612
    assert on_map[f"FF-{CLIME_MAC}"]["latitude"] is None  # far from its farm: listed and flagged, not drawn
    assert on_map[f"FF-{CLIME_MAC}"]["location_check"] == "far_from_plot"
    assert on_map[f"FF-{SOIL_MAC}"]["reporting_state"] == "silent"  # no reading stored yet
    assert summary["position_flagged"] >= 1 and summary["on_map"] >= 1
    assert all(d["latitude"] is None for d in plot_devices("analyst")[0].values())

    # a position entered in the platform is kept by later imports; half a position is refused
    api.as_("admin").patch(f"/devices/{ids[SOIL_MAC]}", json={"latitude": 13.3605, "longitude": 75.3899})
    again = api.as_("admin").post(f"/integrations/sources/{code}/import-devices").json()
    assert again["positions_updated"] == 0
    kept = api.as_("admin").get(f"/devices/{ids[SOIL_MAC]}").json()
    assert (kept["latitude"], kept["location_source"]) == (13.3605, "entered")
    r = api.as_("admin").patch(f"/devices/{ids[SOIL_MAC]}", json={"latitude": 13.1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "position_incomplete"
    assert {req.method for req in farmfuture["requests"]} == {"GET"}


def test_device_readings_as_shown_in_farmfuture(api, db, farmfuture) -> None:
    from app.services.integration import farmfuture_view
    farmfuture_view._CACHE.clear()
    _link_farms(api, db)
    code = _register(api, "farmfuture_soil_microclimate")
    api.as_("admin").post(f"/integrations/sources/{code}/import-devices")
    ids = {mac: db.scalar(select(Device.id).where(Device.device_code == f"FF-{mac}"))
           for mac in (SOIL_MAC, CLIME_MAC, ANALOGUE_MAC)}

    soil = api.as_("analyst").get(f"/devices/{ids[SOIL_MAC]}/farmfuture").json()
    page = soil["sensors_page"]
    shown = {r["label"]: r["text"] for r in page["rows"]}
    # the FarmFuture Sensors-page card: its labels, rounding and units
    assert shown == {"Top Moisture": "21.50%", "Bottom Moisture": "25.00%", "Temperature": "27.7°C", "pH": "6.87",
                     "EC": "49.00 µS/cm", "Nitrogen": "305 kg/ha", "Phosphorus": "1,230 kg/ha", "Potassium": "278 kg/ha",
                     "Solar Radiation": "179 W/m²"}
    assert (page["battery_pct"], page["npk_shown"], page["updated_at"]) == (100, True, "2026-11-09T14:25:37+05:30")
    assert "lat" not in json.dumps(page)  # the device position is not passed on
    # Farm Insights: average / lowest / highest of the daily means over the last 7 days, asked with FarmFuture's dates
    # every soil device of the farm under FarmFuture's picker name; FarmFuture opens on the first one
    fi = soil["farm_insights"]
    assert [d["label"] for d in fi["devices"]] == ["Soilsync Hub - 1", "Not imported - 2"]
    assert fi["farmfuture_opens_on"] == SOIL_MAC
    insight = {m["label"]: m for m in fi["devices"][0]["metrics"]}
    assert (insight["Nitrogen"]["average_text"], insight["Nitrogen"]["low_text"], insight["Nitrogen"]["days"]) == ("124", "124", 2)
    assert (insight["Soil Temperature"]["average_text"], insight["Soil Temperature"]["high_text"]) == ("21.9", "21.9")
    assert insight["Soil Moisture 1"]["trend"] == "Stable" and "Humidity" not in insight
    asked = [r.url.params for r in farmfuture["requests"] if r.url.path == "/api/Data/GetFarmStats"][-1]
    assert (asked["startDate"], asked["endDate"]) == ((today_ist() - timedelta(days=6)).isoformat(), today_ist().isoformat())
    # Device Statistics (Weekly): lowest daily min, highest daily max, mean of the daily means, last day's mean – 2 decimals
    ds = soil["device_statistics"]
    stat = {m["label"]: m for m in ds["metrics"]}
    assert [stat["Nitrogen"][f"{k}_text"] for k in ("minimum", "maximum", "average", "latest")] == \
        ["380.00", "386.00", "384.50", "385.00"]
    assert [stat["pH"][f"{k}_text"] for k in ("minimum", "maximum", "average", "latest")] == ["4.86", "5.39", "5.18", "5.26"]
    assert (stat["Temperature"]["unit"], stat["Temperature"]["average_text"], stat["Nitrogen"]["days"]) == ("°C", "21.47", 2)
    # a reading missing on a day counts as 0, as on FarmFuture's screen
    assert (stat["Phosphorus"]["minimum"], stat["Phosphorus"]["latest"]) == (0, 0)
    asked = [r.url.params for r in farmfuture["requests"] if r.url.path == "/api/Data/GetMinMaxMean"][-1]
    assert (asked["endDate"], asked["macAddress"]) == (today_ist().isoformat(), SOIL_MAC)
    assert asked["startDate"] >= (today_ist() - timedelta(days=6)).isoformat()

    analogue ={r["label"]: r["text"] for r in
                api.as_("admin").get(f"/devices/{ids[ANALOGUE_MAC]}/farmfuture").json()["sensors_page"]["rows"]}
    assert (analogue["Rainfall (cumulative)"], analogue["Leaf Wetness"], analogue["Soil temperature"],
            analogue["Battery Percentage"], analogue["Signal strength"]) == ("12.0 mm", "3160", "-1.0°C", "0.00%", "17 CSQ (0-31)")
    assert "Air Pressure" not in analogue  # FarmFuture's Analogue card has no pressure
    clime = api.as_("admin").get(f"/devices/{ids[CLIME_MAC]}/farmfuture").json()
    assert {r["label"]: r["text"] for r in clime["sensors_page"]["rows"]} == {"Wind Direction": None,
                                                                             "Battery Percentage": "97.50%"}
    assert clime["farm_insights"] is None

    # FarmFuture hides N, P and K while the soil is too dry – so does the view
    farmfuture["npk_hidden"] = True
    farmfuture_view._CACHE.clear()
    dry = api.as_("admin").get(f"/devices/{ids[SOIL_MAC]}/farmfuture").json()["sensors_page"]
    assert dry["npk_shown"] is False and "Nitrogen" not in {r["label"] for r in dry["rows"]}
    assert dry["note"].startswith("Soil is dry")
    assert {req.method for req in farmfuture["requests"]} == {"GET"}


def test_farm_health_views_pass_farmfuture_through_without_locations(api, db, farmfuture) -> None:
    """The Climate Resilience workstream pages read FarmFuture live through /farm-health (GET only, nothing stored): exact
    locations only for users who may see them, scouting without notes or recorder, the profile without its outline."""
    from app.services.integration import farmfuture_view
    farmfuture_view._CACHE.clear()
    plot = _link_farms(api, db)
    farms = api.as_("analyst").get("/farm-health/farms").json()
    assert str(plot.id) in {f["plot_id"] for f in farms}
    base = f"/farm-health/{plot.id}"

    # the pest map's sources hold the farm outline and positions: only with farmer.read_pii
    assert api.as_("analyst").get(f"{base}/sources").status_code == 403
    src = api.as_("admin").get(f"{base}/sources").json()
    assert src["farm_id"] == FARM_A and {r["topic"] for r in src["records"]} >= {"shothole"}
    assert "Checked" not in json.dumps(src["records"]) and "+91" not in json.dumps(src["records"])  # no notes or recorder
    assert api.as_("admin").get(f"{base}/forecast", params={"lat": 1.0, "lon": 1.0}).status_code == 422  # only at the farm

    climate = api.as_("analyst").get(f"{base}/climate").json()
    assert {s["macId"] for s in climate["stations"]} == {CLIME_MAC, ANALOGUE_MAC}
    assert "12.0011" not in json.dumps(climate) and "13.36" not in json.dumps(climate)  # no station or farm position
    assert climate["climate"]["annual_rainfall"]["mean_mm"] == 780.0 and climate["forecast"]["list"]

    profile = api.as_("analyst").get(f"{base}/profile").json()
    assert "center" not in profile["profile"] and profile["profile"]["summary"]
    assert profile["eudr"]["screening"]["complianceFlag"] == "Compliant (risk-based)"
    assert profile["satellite"]["zones"][0]["cells"] == 136

    assert api.as_("analyst").get(f"{base}/insights", params={"start": "2026-11-09", "end": "2026-11-01"}).status_code == 422
    week = api.as_("analyst").get(f"{base}/insights").json()
    assert (week["start"], week["end"]) == ((today_ist() - timedelta(days=6)).isoformat(), today_ist().isoformat())
    assert [d["deviceName"] for d in week["sensor_devices"]][0] == "Soilsync Hub"

    plan = api.as_("analyst").get(f"{base}/irrigation").json()
    assert plan["plan"] == {"farm": {"area_ha": 2.0}}
    assert {req.method for req in farmfuture["requests"]} == {"GET"}
