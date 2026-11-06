"""Public-data adapters (Open-Meteo, NASA POWER, Sentinel-2), sample engine/IoT adapters, the adapter catalogue and the
map endpoint. External services are replaced with an httpx.MockTransport – tests never call the internet."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import Device, FarmPlot, FieldRecord, IntegrationSource
from app.services.integration import platform_adapters
from app.services.integration.platform_adapters import ndvi, today_ist
from app.services.record_types import DEVICE_PARAMETERS

PROGRAMME = "CCRL-INCLUSIVE"


@pytest.fixture()
def http(monkeypatch):
    """Route the adapters' HTTP calls to a handler set by the test; records every request."""
    state = {"handler": None, "requests": []}

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    monkeypatch.setattr(platform_adapters, "HTTP_TRANSPORT", httpx.MockTransport(dispatch))
    return state


def _register(api, adapter_code: str, is_mock: bool, **config) -> str:
    entry = next(c for c in api.as_("admin").get("/integrations/catalogue").json() if c["adapter_code"] == adapter_code)
    code = f"t_{adapter_code}"
    r = api.as_("admin").post("/integrations/sources", json={
        "source_code": code, "name": entry["name"], "module_code": entry["module_code"], "source_type": "rest_pull",
        "adapter_code": adapter_code, "auth_type": "none", "is_mock": is_mock,
        "config": {**entry["config"], "programme_code": PROGRAMME, **config}})
    assert r.status_code == 201, r.text
    return code


def _records(db, source_code: str) -> list[FieldRecord]:
    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == source_code))
    return list(db.scalars(select(FieldRecord).where(FieldRecord.source_system_id == source_id)).unique())


def test_catalogue_lists_real_and_sample_adapters(api) -> None:
    entries = {c["adapter_code"]: c for c in api.as_("analyst").get("/integrations/catalogue").json()}
    assert {"open_meteo_weather", "nasa_power_weather", "sentinel2_ndvi", "sample_climate_engine", "sample_pest_engine",
            "sample_water_engine", "sample_iot_gateway"} <= set(entries)
    assert entries["open_meteo_weather"]["kind"] == "public_data" and entries["open_meteo_weather"]["is_mock"] is False
    assert entries["sample_pest_engine"]["kind"] == "sample" and entries["sample_pest_engine"]["is_mock"] is True


def test_mock_flag_must_match_the_adapter(api) -> None:
    admin = api.as_("admin")
    base = {"name": "x", "source_type": "rest_pull", "auth_type": "none", "config": {"programme_code": PROGRAMME}}
    r = admin.post("/integrations/sources", json={**base, "source_code": "t_bad1", "adapter_code": "sample_pest_engine"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "mock_adapter"
    r = admin.post("/integrations/sources", json={**base, "source_code": "t_bad2", "adapter_code": "open_meteo_weather",
                                                  "is_mock": True})
    assert r.status_code == 422
    code = _register(api, "open_meteo_weather", False)
    r = admin.patch(f"/integrations/sources/{code}", json={"adapter_code": "sample_climate_engine"})
    assert r.status_code == 422  # a real source can never be switched to synthetic data
    assert "LIVE public data" in admin.get(f"/integrations/sources/{code}").json()["connection_status"]


def test_open_meteo_imports_estimates_and_forecasts(api, db, http) -> None:
    today = today_ist()
    days = [today - timedelta(days=1), today, today + timedelta(days=1)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(200, json={
            "latitude": float(request.url.params["latitude"]), "longitude": float(request.url.params["longitude"]),
            "daily": {"time": [d.isoformat() for d in days], "precipitation_sum": [12.5, 3.0, None],
                      "temperature_2m_max": [27.1, 26.0, 25.5], "temperature_2m_min": [19.0, 18.5, 18.0],
                      "relative_humidity_2m_mean": [88, 85, 80]}})

    http["handler"] = handler
    code = _register(api, "open_meteo_weather", False, max_plots=2)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (6, 0), run
    assert len(http["requests"]) == 2  # one call per plot
    records = _records(db, code)
    estimate = next(r for r in records if r.record_type == "weather_estimate")
    assert estimate.data_nature == "calculated" and estimate.is_demo is False and estimate.value == 12.5
    assert estimate.attributes["source_name"] == "Open-Meteo" and estimate.farm_plot_id is not None
    forecasts = [r for r in records if r.record_type == "weather_forecast"]
    assert len(forecasts) == 4 and {r.data_nature for r in forecasts} == {"model_prediction"}
    assert all(r.validation_status == "valid" for r in records), [r.validation_messages for r in records]
    # The same data again is recognised as duplicate, not imported twice
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 6)


def test_nasa_power_skips_fill_values(api, db, http) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "power.larc.nasa.gov"
        return httpx.Response(200, json={
            "geometry": {"coordinates": [82.7, 18.8, 640]}, "header": {"fill_value": -999.0, "sources": ["POWER"]},
            "properties": {"parameter": {"PRECTOTCORR": {"20261020": 1.2, "20261021": -999.0},
                                         "T2M_MAX": {"20261020": 27.7, "20261021": -999.0},
                                         "T2M_MIN": {"20261020": -999.0, "20261021": -999.0},
                                         "RH2M": {"20261020": 86.1, "20261021": -999.0}}}})

    http["handler"] = handler
    code = _register(api, "nasa_power_weather", False, max_plots=1)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (1, 0)
    (record,) = _records(db, code)
    assert record.attributes["rainfall_mm"] == 1.2 and "temp_min_c" not in record.attributes
    assert record.attributes["dataset"] == "NASA POWER daily (POWER)"


def test_ndvi_offset_by_processing_baseline() -> None:
    assert ndvi(1500, 4000, "05.11") == round((3000 - 500) / 3500, 4)
    assert ndvi(1500, 4000, "03.00") == round((4000 - 1500) / 5500, 4)
    assert ndvi(800, 900, "05.11") is None  # both bands below the offset: no usable reflectance


def test_sentinel2_ndvi_skips_cloudy_pixels(api, db, http) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stac/v1/search"):
            return httpx.Response(200, json={"features": [
                {"id": "SCENE-CLOUDY", "properties": {"datetime": "2026-10-23T05:02:31Z", "eo:cloud_cover": 8.4,
                                                      "s2:processing_baseline": "05.11"}},
                {"id": "SCENE-CLEAR", "properties": {"datetime": "2026-10-21T04:58:31Z", "eo:cloud_cover": 3.2,
                                                     "s2:processing_baseline": "05.11"}}]})
        scl = 9.0 if request.url.params["item"] == "SCENE-CLOUDY" else 4.0
        return httpx.Response(200, json={"band_names": ["B04_b1", "B08_b1", "SCL_b1"], "values": [1500.0, 4000.0, scl]})

    http["handler"] = handler
    code = _register(api, "sentinel2_ndvi", False, max_plots=1)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (1, 0)
    (record,) = _records(db, code)
    assert record.record_type == "vegetation_index" and record.attributes["scene_id"] == "SCENE-CLEAR"
    assert float(record.value) == 0.7143 and record.attributes["pixel_class"] == "vegetation"


def test_unavailable_service_is_reported_after_retries(api, http) -> None:
    http["handler"] = lambda request: httpx.Response(503)
    code = _register(api, "open_meteo_weather", False, max_plots=1, retries=2)
    r = api.as_("admin").post(f"/integrations/{code}/pull")
    assert r.status_code == 502 and r.json()["error"]["code"] == "source_unavailable"
    assert len(http["requests"]) == 2


def test_source_without_programme_is_explained(api) -> None:
    # Refused when the source is saved, so a pull never meets a missing programme
    r = api.as_("admin").post("/integrations/sources", json={
        "source_code": "t_no_programme", "name": "No programme", "source_type": "rest_pull",
        "adapter_code": "sample_pest_engine", "auth_type": "none", "is_mock": True, "config": {"programme_code": None}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_config"
    assert "Programme code is required" in r.json()["error"]["message"]


@pytest.mark.parametrize(("adapter_code", "record_type", "nature"), [
    ("sample_climate_engine", "climate_risk_assessment", "model_prediction"),
    ("sample_pest_engine", "pest_risk_estimate", "model_prediction"),
    ("sample_water_engine", "water_balance_output", "calculated"),
])
def test_sample_engines_create_labelled_demo_records(api, db, http, adapter_code, record_type, nature) -> None:
    http["handler"] = lambda request: pytest.fail("sample adapters must not call the internet")
    code = _register(api, adapter_code, True, max_plots=3)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (3, 0), run
    records = _records(db, code)
    assert {(r.record_type, r.data_nature, r.is_demo) for r in records} == {(record_type, nature, True)}
    assert all(r.validation_status == "valid" for r in records), [r.validation_messages for r in records]
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert run["records_duplicate"] == 3 and run["records_accepted"] == 0


def test_sample_iot_gateway_reads_only_supported_parameters(api, db) -> None:
    code = _register(api, "sample_iot_gateway", True)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert run["records_rejected"] == 0 and run["records_accepted"] > 0, run
    records = _records(db, code)
    assert {r.record_type for r in records} == {"sensor_observation"} and all(r.is_demo for r in records)
    device_types = dict(db.execute(select(Device.id, Device.device_type)).all())
    for record in records:
        assert record.attributes["parameter"] in DEVICE_PARAMETERS[device_types[record.device_id]]
    faulty = db.scalars(select(Device.id).where(Device.status != "active")).all()
    assert not any(r.device_id in faulty for r in records)  # only active devices report


def test_map_shows_plots_with_latest_values_and_masks_locations(api, db) -> None:
    code = _register(api, "sample_pest_engine", True, max_plots=3)
    api.as_("admin").post(f"/integrations/{code}/pull")
    first = db.scalars(select(FarmPlot).where(FarmPlot.latitude.is_not(None)).order_by(FarmPlot.plot_code)).first()
    admin_map = api.as_("admin").get("/map/plots").json()
    assert admin_map["precise_locations"] is True and admin_map["total"] >= 3
    by_code = {p["plot_code"]: p for p in admin_map["plots"]}
    assert by_code[first.plot_code]["latitude"] == float(first.latitude)
    assert sum(1 for p in admin_map["plots"] if "pest_risk" in p) >= 1
    assert {p["practice_stage"] for p in admin_map["plots"]} <= {"verified", "observed", "reported", "recommended", "none"}
    # Without farmer.read_pii the point is rounded to about 1 km
    analyst_map = api.as_("analyst").get("/map/plots").json()
    assert analyst_map["precise_locations"] is False
    point = next(p for p in analyst_map["plots"] if p["plot_code"] == first.plot_code)
    assert point["latitude"] == round(float(first.latitude), 2)
    # Scope: a district field officer sees only plots in their area
    field_total = api.as_("field").get("/map/plots").json()["total"]
    assert 0 < field_total < admin_map["total"]
    assert api.as_("farmer").get("/map/plots").status_code == 403
