"""Farm Health – the FarmFuture "Pest & Disease Forecast" inputs for one farm, passed through for the platform's copy of
FarmFuture's own screening code (the frontend runs FarmFuture's models unchanged, so the map, the inspection areas and the
alerts match the FarmFuture app exactly).

Read live from FarmFuture with GET only and never stored. The farm outline, zones, satellite cells and device positions
are exact locations, so the sources need ``farmer.read_pii``; scouting records lose their notes, photos and recorder.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Farm, Farmer, FarmPlot
from app.services.integration.farmfuture import FarmFutureApi
from app.services.integration.farmfuture_data import linked_farms
from app.services.integration.farmfuture_view import _get
from app.services.integration.platform_adapters import today_ist

router = APIRouter(prefix="/farm-health", tags=["farm-health"])

SATELLITE_SECONDS = 3600  # seasonal composites change at most daily; they are large and slow to build
WSB_SOURCES = ("cwsb", "drainage", "satellite", "thermal")
RECORD_FIELDS = ("id", "date", "kind", "topic", "zoneId", "inspected", "affected", "nextCheck", "conditions",
                 "shadePercent", "point", "createdAt")
SENSOR_FIELDS = ("macId", "deviceName", "tenantId", "lat", "long", "updateAt", "crops", "soilProperties",
                 "soilNutrients", "extraParameters")
FORECAST_REACH_DEG = 0.2  # the forecast is only fetched near the farm it is asked for


def _api() -> FarmFutureApi:
    api = FarmFutureApi()
    api.config = {}
    api._init_api()
    return api


def _plot(db: Session, current: CurrentUser, plot_id: uuid.UUID) -> tuple[FarmPlot, str]:
    plot = db.get(FarmPlot, plot_id)
    if plot is None or plot.is_archived:
        raise AppError(404, "not_found", "Plot not found")
    farm = db.get(Farm, plot.farm_id)
    farmer = db.get(Farmer, farm.farmer_id) if farm else None
    if farmer is None:
        raise AppError(404, "not_found", "Plot not found")
    current.ensure_access(farmer.geo_unit_id, farmer.primary_fpo_id)
    farm_id = next((fid for fid, link in linked_farms(db).items() if link.plot.id == plot.id), None)
    if farm_id is None:
        raise AppError(422, "farm_not_linked", "This plot is not linked to a FarmFuture farm")
    return plot, farm_id


@router.get("/farms")
def farms(current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """The FarmFuture-linked plots the user may see, for the farm picker."""
    out = []
    for link in sorted(linked_farms(db).values(), key=lambda farm_link: farm_link.plot.label):
        farm = link.plot.farm
        farmer = farm.farmer if farm else None
        if farmer is None or not current.can_access(farmer.geo_unit_id, farmer.primary_fpo_id):
            continue
        out.append({"plot_id": link.plot.id, "label": link.plot.label, "crop": link.plot.crop})
    return out


@router.get("/{plot_id}/sources")
def sources(plot_id: uuid.UUID, current: CurrentUser = Depends(require("module.read", "farmer.read_pii")),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    """FarmFuture's raw inputs to its Pest & Disease Forecast for the farm (the same calls its web app makes)."""
    plot, farm_id = _plot(db, current, plot_id)
    api = _api()
    with api.client() as client:
        coordinates = _get(api, client, f"/api/Data/GetFarmCoordinates/{farm_id}")
        sensors = _get(api, client, f"/api/Data/GetAllSensorsLatestDataV2/{farm_id}")
        calendar = _get(api, client, f"/api/Calendar/GetUserCalendarEvents/{farm_id}")
        weather = _get(api, client, "/api/Data/GetAllWeatherData", {"farmId": farm_id})
        records = _get(api, client, "/api/wsb/records", {"farm": farm_id})
        wsb = {key: _get(api, client, f"/api/wsb/{key}", {"farm": farm_id}, SATELLITE_SECONDS) for key in WSB_SOURCES}
    sensors = [sensors] if isinstance(sensors, dict) else sensors if isinstance(sensors, list) else []
    return {
        "farm_id": farm_id, "plot_id": plot.id, "label": plot.label, "crop": plot.crop,
        "coordinates": coordinates,
        "sensors": [{k: s.get(k) for k in SENSOR_FIELDS} for s in sensors if isinstance(s, dict)],
        "calendar": calendar, "weather": weather if isinstance(weather, list) else [],
        "records": [{k: r.get(k) for k in RECORD_FIELDS} for r in ((records or {}).get("records") or [])
                    if isinstance(r, dict)] if isinstance(records, dict) else [],
        **wsb,
    }


PROFILE_DROP = ("center", "ring", "zones", "terrain_points")  # farm location and outlines
STATION_DROP = ("lat", "long", "Lat", "Long", "latitude", "longitude")


def _no_location(item: Any) -> Any:
    return {k: v for k, v in item.items() if k not in STATION_DROP} if isinstance(item, dict) else item


def _clean_profile(prof: Any) -> Any:
    """The farm profile without the farm's location: no centre, outline, zone shapes or device positions."""
    if not isinstance(prof, dict):
        return prof
    return {k: v for k, v in prof.items() if k not in (*PROFILE_DROP, "log", "devices")} | {
        "zone_profiles": [{k: v for k, v in z.items() if k not in ("center", "ring")}
                          for z in prof.get("zone_profiles") or [] if isinstance(z, dict)],
        "devices": [{"name": d.get("name"), "label": d.get("label")} for d in prof.get("devices") or [] if isinstance(d, dict)]}


@router.get("/{plot_id}/climate")
def climate(plot_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    """FarmFuture's inputs to its dashboard weather, rain and Missing Rain Report screens for the farm – the weather
    stations' latest readings, the 30-year climate layer and farm profile, the stations' daily rain this year, their
    last 7 days of statistics and the forecast. No location is returned (the forecast is asked for server-side)."""
    plot, farm_id = _plot(db, current, plot_id)
    today = today_ist()
    api = _api()
    with api.client() as client:
        micro = _get(api, client, "/api/Data/GetAllWeatherData", {"farmId": farm_id})
        analogue = _get(api, client, "/api/Data/GetAllAnalogueWeatherData", {"farmId": farm_id})
        stations = [{**s, "isAnalogWeather": False} for s in micro or [] if isinstance(s, dict)] + \
                   [{**s, "isAnalogWeather": True} for s in analogue or [] if isinstance(s, dict)]
        layer = _get(api, client, f"/api/FarmProfile/{farm_id}/climate", seconds=SATELLITE_SECONDS)
        profile = _get(api, client, f"/api/FarmProfile/{farm_id}", seconds=SATELLITE_SECONDS)
        coordinates = _get(api, client, f"/api/Data/GetFarmCoordinates/{farm_id}")
        rain, week = {}, {}
        for s in stations:
            mac = str(s.get("macId") or "")
            if not mac or mac in rain:
                continue
            rain[mac] = _get(api, client, "/api/Data/GetWeatherStats", {
                "startDate": date(today.year, 1, 1).isoformat(), "endDate": today.isoformat(), "macAddress": mac,
                "parameter": "rainfall_mm"})
            week[mac] = _get(api, client, "/api/Data/GetWeatherStats", {
                "startDate": (today - timedelta(days=6)).isoformat(), "endDate": today.isoformat(), "macAddress": mac})
        # FarmFuture's dashboard asks the forecast for the first device's position, else the farm's.
        devices = (coordinates or {}).get("deviceCoordinates") or [] if isinstance(coordinates, dict) else []
        spot = next(((d.get("lat"), d.get("long")) for d in devices if isinstance(d, dict)
                     and isinstance(d.get("lat"), (int, float)) and isinstance(d.get("long"), (int, float))
                     and (d.get("lat"), d.get("long")) != (0, 0)), None)
        if spot is None and plot.latitude is not None and plot.longitude is not None:
            spot = (float(plot.latitude), float(plot.longitude))
        forecast = _get(api, client, "/WeatherForecast/forecast", {"lat": spot[0], "lon": spot[1]}) if spot else None
    if isinstance(forecast, dict):
        forecast = {k: v for k, v in forecast.items() if k != "city"} | {"city": {"name": (forecast.get("city") or {}).get("name")}}
    profile = _clean_profile(profile)
    return {"farm_id": farm_id, "plot_id": plot.id, "label": plot.label, "crop": plot.crop, "today": today.isoformat(),
            "stations": [_no_location(s) for s in stations], "climate": layer, "profile": profile,
            "rain": rain, "week": week, "forecast": forecast}


@router.get("/{plot_id}/insights")
def insights(plot_id: uuid.UUID, start: date | None = None, end: date | None = None,
             current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """FarmFuture's Farm Insights inputs for a period (default today − 6 … today, its "Last 7 Days"): the farm's daily
    device statistics (GetFarmStats) and each weather station's daily statistics (GetWeatherStats), as its screen asks."""
    plot, farm_id = _plot(db, current, plot_id)
    today = today_ist()
    end = min(end or today, today)
    start = start or end - timedelta(days=6)
    if start > end or (end - start).days > 366:
        raise AppError(422, "invalid_period", "The period must start before it ends and be at most a year")
    api = _api()
    with api.client() as client:
        stats = _get(api, client, "/api/Data/GetFarmStats",
                     {"farmId": farm_id, "startDate": start.isoformat(), "endDate": end.isoformat()})
        stats = stats if isinstance(stats, dict) else {}
        weather = {}
        for station in stats.get("weatherDevices") or []:
            mac = str((station or {}).get("macAddress") or "")
            if mac and mac not in weather:
                weather[mac] = _get(api, client, "/api/Data/GetWeatherStats",
                                    {"startDate": start.isoformat(), "endDate": end.isoformat(), "macAddress": mac})
    keep = ("macAddress", "deviceName", "stats", "deviceType", "deviceTypeId")
    return {"farm_id": farm_id, "plot_id": plot.id, "label": plot.label, "crop": plot.crop,
            "start": start.isoformat(), "end": end.isoformat(),
            "sensor_devices": [{k: d.get(k) for k in keep if k in d} for d in stats.get("sensorDevices") or [] if isinstance(d, dict)],
            "weather_devices": [{k: d.get(k) for k in keep if k in d} for d in stats.get("weatherDevices") or [] if isinstance(d, dict)],
            "weather_stats": weather}


@router.get("/{plot_id}/irrigation")
def irrigation(plot_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    """FarmFuture's watering plan for the farm (its Irrigation Planning screen): GET /v1/farmer/farms/{farm}/irrigation-plan.
    The block map (exact outlines and device positions) is kept only for users who may see precise locations."""
    plot, farm_id = _plot(db, current, plot_id)
    api = _api()
    with api.client() as client:
        plan = _get(api, client, f"/v1/farmer/farms/{farm_id}/irrigation-plan")
    if isinstance(plan, dict) and not current.has("farmer.read_pii"):
        plan = {k: v for k, v in plan.items() if k != "map"}
    return {"farm_id": farm_id, "plot_id": plot.id, "label": plot.label,
            "plan": plan if isinstance(plan, dict) and plan.get("found", True) is not False else None}


@router.get("/{plot_id}/profile")
def profile(plot_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    """FarmFuture's Farm Profile (terrain, soil, 30-year climate, zones – no location), its EUDR readiness screening and the
    latest satellite zone summary for the farm (its Farm Profile and Estate Compliance screens)."""
    plot, farm_id = _plot(db, current, plot_id)
    api = _api()
    with api.client() as client:
        prof = _get(api, client, f"/api/FarmProfile/{farm_id}", seconds=SATELLITE_SECONDS)
        eudr = _get(api, client, f"/api/EudrReadiness/{farm_id}")
        zones = _get(api, client, f"/api/Satellite/ZoneSummary/{farm_id}", seconds=SATELLITE_SECONDS)
    prof = _clean_profile(prof)
    screening = (eudr or {}).get("screening") if isinstance(eudr, dict) else None
    eudr_out = {"has_boundary": eudr.get("hasBoundary"), "has_report": eudr.get("hasReport"), "missing": eudr.get("missing") or [],
                "screening": {k: screening.get(k) for k in ("complianceFlag", "complianceReason", "riskScore",
                                                            "deforestedSince2020Ha", "screenedAt")} if isinstance(screening, dict) else None
                } if isinstance(eudr, dict) else None
    zone_out = {k: zones.get(k) for k in ("status", "observedOn")} | {"zones": [
        {k: z.get(k) for k in ("cells", "avgNdvi", "minNdvi", "maxNdvi", "avgNdmi", "decliningCells")}
        for z in zones.get("zones") or [] if isinstance(z, dict)]} if isinstance(zones, dict) else None
    return {"farm_id": farm_id, "plot_id": plot.id, "label": plot.label, "crop": plot.crop, "profile": prof,
            "eudr": eudr_out, "satellite": zone_out}


@router.get("/{plot_id}/forecast")
def forecast(plot_id: uuid.UUID, lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
             current: CurrentUser = Depends(require("module.read", "farmer.read_pii")),
             db: Session = Depends(get_db)) -> Any:
    """FarmFuture's 5-day, 3-hourly forecast at the farm centre (as its web app asks for it)."""
    plot, _ = _plot(db, current, plot_id)
    if plot.latitude is not None and plot.longitude is not None and (
            abs(float(plot.latitude) - lat) > FORECAST_REACH_DEG or abs(float(plot.longitude) - lon) > FORECAST_REACH_DEG):
        raise AppError(422, "too_far", "The forecast is only given at the farm")
    api = _api()
    with api.client() as client:
        return _get(api, client, "/WeatherForecast/forecast", {"lat": lat, "lon": lon})
