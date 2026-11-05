"""FarmFuture Water Balance Engine adapter: read-only GET calls, identity-mapped farms only, field mapping from the live
response shape (model 1.3.0). The API is replaced with an httpx.MockTransport – tests never call FarmFuture."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, true

from app.core.config import get_settings
from app.models import Farmer, FarmPlot, FieldRecord, GeoUnit, IntegrationSource, ProgrammeParticipation
from app.services.integration import platform_adapters
from app.services.integration.farmfuture import plan_attributes
from app.services.integration.platform_adapters import today_ist

PROGRAMME = "CCRL-INCLUSIVE"
FARM_A = "11111111-1111-4111-8111-111111111111"  # mapped
FARM_B = "22222222-2222-4222-8222-222222222222"  # visible to the token but not mapped
ESTATE = "33333333-3333-4333-8333-333333333333"
RING = [[75.3920, 13.3595], [75.3938, 13.3595], [75.3938, 13.3610], [75.3920, 13.3610]]  # open ring, [lon, lat]
BLOCK = [[75.3920, 13.3595], [75.3929, 13.3595], [75.3929, 13.3610], [75.3920, 13.3595]]


def plan(farm_id: str, day: str, status: str = "IRRIGATE_BY") -> dict:
    """Synthetic irrigation plan with the structure of a live FarmFuture response (values invented for the test)."""
    irrigate = status != "NO_IRRIGATION"
    return {
        "farm_id": farm_id, "as_of_date": day, "horizon_days": 7, "model_version": "1.3.0",
        "headline": {"status": status, "title": "Irrigate by Tue 10 Nov" if irrigate else "No irrigation needed"},
        "water_use": {"date": day, "kc": 0.95, "et0_mm": 3.13, "etc_mm": 2.98, "rain_mm": 2.8, "effective_rain_mm": 2.8,
                      "irrigation_applied_mm": 0.0, "irrigation_effective_mm": 0.0, "runoff_mm": 0.0, "drained_mm": 0.0,
                      "net_change_mm": -0.18},
        "today": {"capacity_mm": 50.3, "usable_water_pct": 69.1, "stress_mark_pct": 53.1, "reserve_mm": 8.0,
                  "reserve_days": 2.4, "sensor": {"available": True, "reading_pct": 19.1, "model_pct": 34.1,
                                                  "agrees": False}},
        "days": [{"date": "2026-11-09", "needs_water": False}, {"date": "2026-11-10", "needs_water": irrigate}],
        "water_needed": {"depth_mm": 25.0, "total_m3": 556.5} if irrigate else None,
        "stage": {"label": "Bean filling and hardening", "source": {"estimated": True}},
        "data_quality": {"mode": "MEASURED"}, "confidence": {"level": "MEDIUM", "score": 55},
        "map": {"blocks": [{"ring": [[75.39, 13.36]]}]},  # geometry is ignored by the adapter
    }


@pytest.fixture()
def farmfuture(monkeypatch):
    """Fake FarmFuture API: farm list plus irrigation plans; records every request."""
    state = {"requests": [], "plans": {}, "token": "test-token"}
    monkeypatch.setattr(get_settings(), "farmfuture_api_token", state["token"])

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.headers.get("Authorization") != f"Bearer {state['token']}":
            return httpx.Response(401)
        if request.url.path == "/v1/farmer/farms":
            return httpx.Response(200, json=[{"farm_id": FARM_A, "name": "Farm A"}, {"farm_id": FARM_B, "name": "Farm B"}])
        if request.url.path == "/api/Estate/GetUserEstatesAndFarmsList":
            return httpx.Response(200, json={"succeeded": True, "data": [
                {"estateId": ESTATE, "estateName": "Hill Estate", "farms": [
                    {"farmId": FARM_A, "farmName": "Upper block"}, {"farmId": FARM_B, "farmName": "Lower block"}]}]})
        if request.url.path.startswith("/api/FarmProfile/"):
            if request.url.path.endswith(FARM_A):
                return httpx.Response(200, json={
                    "center": [75.392909, 13.360239], "crop_type": "robusta", "ring": RING,
                    "zone_profiles": [{"ring": RING}, {"ring": BLOCK}],
                    "summary": {"terrain": {"elevation_mean_m": 739.0, "slope_mean_deg": 6.52, "slope_class": "gentle"}}})
            return httpx.Response(404, json={"detail": "No profile"})
        farm_id = request.url.path.split("/")[4]
        if "day" not in request.url.params:  # latest plan
            return httpx.Response(200, json={**plan(farm_id, "2026-11-04"), "farm": {"area_ha": 2.226}})
        found = state["plans"].get((farm_id, request.url.params["day"]))
        if found is None:
            return httpx.Response(422, json={"detail": "No irrigation plan has been stored for this farm."})
        return httpx.Response(200, json=found)

    monkeypatch.setattr(platform_adapters, "HTTP_TRANSPORT", httpx.MockTransport(dispatch))
    return state


def _register(api, **config) -> str:
    entry = next(c for c in api.as_("admin").get("/integrations/catalogue").json()
                 if c["adapter_code"] == "farmfuture_water_engine")
    assert entry["is_mock"] is False and entry["kind"] == "live_api"
    r = api.as_("admin").post("/integrations/sources", json={
        "source_code": "t_farmfuture_water", "name": entry["name"], "module_code": "water", "source_type": "rest_pull",
        "adapter_code": "farmfuture_water_engine", "auth_type": "api_key", "is_mock": False,
        "config": {**entry["config"], "programme_code": PROGRAMME, **config}})
    assert r.status_code == 201, r.text
    return "t_farmfuture_water"


def _map_farm(api, db, code: str) -> FarmPlot:
    plot = db.scalars(select(FarmPlot).where(FarmPlot.is_demo == true()).order_by(FarmPlot.plot_code)).first()
    r = api.as_("admin").post(f"/integrations/sources/{code}/identity-maps",
                              json={"entity_type": "farm_plot", "source_id": FARM_A, "internal_id": str(plot.id)})
    assert r.status_code == 201, r.text
    return plot


def test_plan_mapping_follows_the_engine_response() -> None:
    a = plan_attributes(plan(FARM_A, "2026-11-04"))
    assert a["engine_version"] == "1.3.0" and a["irrigation_status"] == "IRRIGATE_BY"
    assert (a["et0_mm"], a["kc"], a["crop_water_requirement_mm"], a["recommended_irrigation_mm"]) == (3.13, 0.95, 2.98, 25.0)
    assert a["first_needs_water_date"] == "2026-11-10" and a["sensor_agrees"] is False
    quiet = plan_attributes(plan(FARM_A, "2026-11-04", "NO_IRRIGATION"))
    assert quiet["recommended_irrigation_mm"] == 0.0 and quiet["water_needed_m3"] is None
    assert quiet["first_needs_water_date"] is None


def test_pull_imports_mapped_farms_read_only(api, db, farmfuture) -> None:
    today = today_ist()
    days = [(today - timedelta(days=n)).isoformat() for n in (1, 2)]
    for d in days:
        farmfuture["plans"][(FARM_A, d)] = plan(FARM_A, d)
        farmfuture["plans"][(FARM_B, d)] = plan(FARM_B, d)
    code = _register(api, past_days=2)
    assert "LIVE FarmFuture API" in api.as_("admin").get(f"/integrations/sources/{code}").json()["connection_status"]
    plot = _map_farm(api, db, code)

    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (2, 0), run
    assert {r.method for r in farmfuture["requests"]} == {"GET"}  # never writes to FarmFuture
    assert not any(FARM_B in r.url.path for r in farmfuture["requests"])  # unmapped farms are not fetched
    assert sum("irrigation-plan" in r.url.path for r in farmfuture["requests"]) == 3  # today + 2 past days

    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == code))
    records = list(db.scalars(select(FieldRecord).where(FieldRecord.source_system_id == source_id)).unique())
    assert len(records) == 2 and {r.farm_plot_id for r in records} == {plot.id}
    record = records[0]
    assert record.record_type == "water_balance_output" and record.data_nature == "calculated"
    assert record.is_demo is False and record.validation_status == "valid", record.validation_messages
    assert float(record.value) == 25.0 and record.attributes["source_farm_ref"] == FARM_A

    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 2)


def test_pull_explains_missing_maps_and_token(api, db, farmfuture, monkeypatch) -> None:
    code = _register(api)
    r = api.as_("admin").post(f"/integrations/{code}/pull")
    assert r.status_code == 422 and FARM_A in r.json()["error"]["message"]  # lists the farms to map
    _map_farm(api, db, code)
    farmfuture["token"] = "rotated-on-the-farmfuture-side"
    r = api.as_("admin").post(f"/integrations/{code}/pull")
    assert r.status_code == 502 and "FARMFUTURE_API_TOKEN" in r.json()["error"]["message"]
    monkeypatch.setattr(get_settings(), "farmfuture_api_token", None)
    assert "not set" in api.as_("admin").get(f"/integrations/sources/{code}").json()["connection_status"]
    r = api.as_("admin").post(f"/integrations/{code}/pull")
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_not_configured"


def test_import_creates_farmers_farms_plots_and_maps(api, db, farmfuture) -> None:
    code = _register(api)
    geo = db.scalars(select(GeoUnit).where(GeoUnit.unit_type == "cluster").order_by(GeoUnit.code)).first()
    r = api.as_("admin").post(f"/integrations/sources/{code}/import-farms", json={"geo_unit_id": str(geo.id)})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["estates"], out["farmers_created"], out["farms_created"], out["with_location"], out["with_area"]) == \
        (1, 1, 2, 1, 2)
    assert {r.method for r in farmfuture["requests"]} == {"GET"}
    farmer = db.scalar(select(Farmer).where(Farmer.farmer_code == "FF-33333333"))
    assert farmer.full_name == "TEST – Hill Estate" and farmer.consent_status == "not_recorded"
    assert farmer.household_code == "FF-HH-33333333" and farmer.geo_unit_id == geo.id
    assert db.scalar(select(ProgrammeParticipation.id).where(ProgrammeParticipation.farmer_id == farmer.id)) is None
    plot = db.scalar(select(FarmPlot).where(FarmPlot.plot_code == "FF-11111111-P1"))
    assert plot.crop == "robusta" and float(plot.latitude) == 13.360239 and float(plot.longitude) == 75.392909
    assert float(plot.area) == 2.226
    maps = api.as_("admin").get(f"/integrations/sources/{code}/identity-maps").json()
    assert {(m["entity_type"], m["source_record_id"]) for m in maps} == {
        ("farmer", ESTATE), ("farm_plot", FARM_A), ("farm_plot", FARM_B)}
    # Running it again adds nothing; the water-engine pull now works without manual mapping
    again = api.as_("admin").post(f"/integrations/sources/{code}/import-farms", json={"geo_unit_id": str(geo.id)}).json()
    assert (again["farmers_created"], again["farms_created"], again["already_imported"]) == (0, 0, 2)
    day = (today_ist() - timedelta(days=1)).isoformat()
    farmfuture["plans"][(FARM_A, day)] = plan(FARM_A, day)
    run = api.as_("admin").post(f"/integrations/{code}/pull").json()["sync_run"]
    assert run["records_accepted"] == 1, run


def test_import_needs_farmer_permission(api, db, farmfuture) -> None:
    code = _register(api)
    geo = db.scalars(select(GeoUnit).where(GeoUnit.unit_type == "cluster")).first()
    r = api.as_("analyst").post(f"/integrations/sources/{code}/import-farms", json={"geo_unit_id": str(geo.id)})
    assert r.status_code == 403


def test_farm_boundary_is_read_live_and_needs_precise_location(api, db, farmfuture, monkeypatch) -> None:
    code = _register(api)
    geo = db.scalars(select(GeoUnit).where(GeoUnit.unit_type == "cluster").order_by(GeoUnit.code)).first()
    assert api.as_("admin").post(f"/integrations/sources/{code}/import-farms", json={"geo_unit_id": str(geo.id)}).status_code == 200
    plot_a = db.scalar(select(FarmPlot.id).where(FarmPlot.plot_code == "FF-11111111-P1"))
    plot_b = db.scalar(select(FarmPlot.id).where(FarmPlot.plot_code == "FF-22222222-P1"))
    farmfuture["requests"].clear()

    out = api.as_("admin").get(f"/map/plots/{plot_a}/boundary").json()
    assert out["available"] is True and out["source"] == "FarmFuture"
    assert out["boundary"] == [*RING, RING[0]]  # closed for GeoJSON
    assert out["blocks"] == [BLOCK]  # the zone that repeats the whole outline is not drawn twice
    assert out["terrain"]["slope_class"] == "gentle"
    assert [r.method for r in farmfuture["requests"]] == ["GET"]
    assert db.scalar(select(FieldRecord.id).where(FieldRecord.farm_plot_id == plot_a)) is None  # nothing stored

    assert api.as_("admin").get(f"/map/plots/{plot_b}/boundary").json() == {"available": False, "reason": "no_profile"}
    demo_plot = db.scalars(select(FarmPlot.id).where(FarmPlot.is_demo == true()).order_by(FarmPlot.plot_code)).first()
    assert api.as_("admin").get(f"/map/plots/{demo_plot}/boundary").json()["reason"] == "not_linked"
    assert api.as_("analyst").get(f"/map/plots/{plot_a}/boundary").status_code == 403  # no precise-location permission
    monkeypatch.setattr(get_settings(), "farmfuture_api_token", None)
    assert api.as_("admin").get(f"/map/plots/{plot_a}/boundary").json()["reason"] == "not_configured"
