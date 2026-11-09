"""Farm Health – the FarmFuture "Pest & Disease Forecast" inputs for one farm, passed through for the platform's copy of
FarmFuture's own screening code (the frontend runs FarmFuture's models unchanged, so the map, the inspection areas and the
alerts match the FarmFuture app exactly).

Read live from FarmFuture with GET only and never stored. The farm outline, zones, satellite cells and device positions
are exact locations, so the sources need ``farmer.read_pii``; scouting records lose their notes, photos and recorder.
"""

import uuid
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
