"""Pull adapters that decide what to fetch from the platform's own registry (located plots, active devices).

Public-data adapters – real data from free services that need no API key:
  * OpenMeteoAdapter      daily weather at each plot's GPS point: past days (gridded estimate) and a forecast.
  * NasaPowerAdapter      daily satellite/reanalysis weather at each plot's GPS point.
  * Sentinel2NdviAdapter  NDVI from the latest cloud-free Sentinel-2 scene (Microsoft Planetary Computer).
  None of these is IMD data; IMD access needs an agreement with IMD.

Sample adapters – synthetic data standing in for the FarmFuture engines and the IoT gateway until their APIs are
supplied. Their sources must be flagged is_mock, so every record they create is flagged is_demo and labelled in the UI.
Values are deterministic per plot/device and period, so re-running a pull reports duplicates instead of new data.
"""

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, ClassVar

import httpx
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Device, Farm, Farmer, FarmPlot, IntegrationSource, Programme, ProgrammeParticipation
from app.services.integration.base import PageResult, TransientSourceError
from app.services.record_types import DEVICE_PARAMETERS, RECORD_TYPES_BY_CODE, RISK_LEVELS

IST = timezone(timedelta(hours=5, minutes=30))
USER_AGENT = "coffee-mrv-platform/1.0 (NABARD CCF-ID programme)"
HTTP_TRANSPORT: httpx.BaseTransport | None = None  # tests replace this with an httpx.MockTransport


def today_ist() -> date:
    return datetime.now(IST).date()


def _noon(day: date) -> str:
    return datetime.combine(day, time(12), IST).isoformat()


def _midnight(day: date) -> str:
    return datetime.combine(day, time(0), IST).isoformat()


def _int(config: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(config.get(key, default))))
    except (TypeError, ValueError):
        return default


def _score(*parts: Any) -> float:
    """Deterministic pseudo-random number in [0, 1) for synthetic sample values."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    host = httpx.URL(url).host
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TransportError as exc:  # timeouts and connection failures
        raise TransientSourceError(f"{host}: {type(exc).__name__}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientSourceError(f"{host} returned HTTP {response.status_code}")
    if response.status_code >= 400:
        raise AppError(502, "source_error", f"{host} rejected the request (HTTP {response.status_code}): "
                                            f"{response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise AppError(502, "source_error", f"{host} returned a response that is not JSON") from exc


class PlatformAdapter:
    """Base: pages through the programme's located plots and asks ``plot_records`` for each plot's records."""

    uses_platform_codes = True
    payload_kind = "observations"
    page_size = 10
    catalogue: ClassVar[dict[str, Any]]

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        self.db = db
        self.source = source
        self.config: dict[str, Any] = source.config or {}
        code = self.config.get("programme_code")
        programme = db.scalar(select(Programme).where(Programme.code == code)) if code else None
        if programme is None:
            raise AppError(422, "source_config", f"Source '{source.source_code}' needs config.programme_code set to an "
                                                 "existing programme code (edit the source and set it)")
        self.programme = programme
        self._plots: list[FarmPlot] | None = None

    def plots(self) -> list[FarmPlot]:
        if self._plots is None:
            enrolled = select(ProgrammeParticipation.farmer_id).where(
                ProgrammeParticipation.programme_id == self.programme.id, ProgrammeParticipation.status == "enrolled")
            stmt = (select(FarmPlot).join(Farm, Farm.id == FarmPlot.farm_id).join(Farmer, Farmer.id == Farm.farmer_id)
                    .where(Farmer.id.in_(enrolled), FarmPlot.is_archived == false(), Farm.is_archived == false(),
                           Farmer.is_archived == false(), FarmPlot.latitude.is_not(None), FarmPlot.longitude.is_not(None))
                    .order_by(FarmPlot.plot_code).limit(_int(self.config, "max_plots", 50, 1, 500)))
            self._plots = list(self.db.scalars(stmt).unique())
            if not self._plots:
                raise AppError(422, "no_located_plots",
                               f"No plots with latitude and longitude belong to farmers enrolled in programme "
                               f"{self.programme.code}. Register plots with GPS coordinates and enrol their farmers first.")
        return self._plots

    def fetch_page(self, cursor: str | None) -> PageResult:
        start = int(cursor or 0)
        plots = self.plots()
        with httpx.Client(transport=HTTP_TRANSPORT, headers={"User-Agent": USER_AGENT},
                          timeout=float(_int(self.config, "timeout_seconds", 30, 5, 120))) as client:
            records = [r for plot in plots[start:start + self.page_size] for r in self.plot_records(client, plot)]
        end = start + self.page_size
        return PageResult(records, str(end) if end < len(plots) else None)

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        raise NotImplementedError

    def observation(self, record_id: str, record_type: str, plot: FarmPlot, attributes: dict[str, Any],
                    observed_at: str | None) -> dict[str, Any]:
        return {"source_record_id": record_id, "programme_code": self.programme.code,
                "module_code": RECORD_TYPES_BY_CODE[record_type].module_code, "record_type": record_type,
                "farm_plot_external_id": plot.plot_code, "observed_at": observed_at,
                "attributes": {k: v for k, v in attributes.items() if v is not None}}


# -- real public data ------------------------------------------------------------------------------------------------
class OpenMeteoAdapter(PlatformAdapter):
    URL = "https://api.open-meteo.com/v1/forecast"
    DAILY = {"precipitation_sum": "rainfall_mm", "temperature_2m_max": "temp_max_c", "temperature_2m_min": "temp_min_c",
             "relative_humidity_2m_mean": "relative_humidity_pct"}
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "Open-Meteo weather (real data)", "module_code": "climate", "kind": "public_data",
        "default_source_code": "open_meteo_weather", "owner": "Open-Meteo.com – free public API (CC BY 4.0), no key",
        "description": "Real daily rain, temperature and humidity at every located plot: recent days as a gridded "
                       "estimate and the coming days as a forecast. Not IMD data.",
        "config": {"past_days": 7, "forecast_days": 7, "max_plots": 50}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        data = request_json(client, "GET", self.URL, params={
            "latitude": float(plot.latitude), "longitude": float(plot.longitude), "daily": ",".join(self.DAILY),
            "past_days": _int(self.config, "past_days", 7, 0, 92),
            "forecast_days": _int(self.config, "forecast_days", 7, 1, 16), "timezone": "Asia/Kolkata"})
        daily = data.get("daily") or {}
        today = today_ist()
        records = []
        for i, day_text in enumerate(daily.get("time") or []):
            values = {target: (daily.get(name) or [None] * (i + 1))[i] for name, target in self.DAILY.items()}
            if all(v is None for v in values.values()):
                continue
            day = date.fromisoformat(day_text)
            if day < today:
                records.append(self.observation(
                    f"OPEN-METEO-{plot.plot_code}-{day}", "weather_estimate", plot,
                    {"source_name": "Open-Meteo", "dataset": "Open-Meteo best-match weather models (past-day estimate)",
                     "grid_latitude": data.get("latitude"), "grid_longitude": data.get("longitude"), **values}, _noon(day)))
            else:
                records.append(self.observation(
                    f"OPEN-METEO-FC-{plot.plot_code}-{day}-ISSUED-{today}", "weather_forecast", plot,
                    {"source_name": "Open-Meteo", "dataset": "Open-Meteo best-match forecast models",
                     "forecast_date": day.isoformat(), "issued_on": today.isoformat(), **values}, _noon(day)))
        return records


class NasaPowerAdapter(PlatformAdapter):
    URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
    PARAMETERS = {"PRECTOTCORR": "rainfall_mm", "T2M_MAX": "temp_max_c", "T2M_MIN": "temp_min_c",
                  "RH2M": "relative_humidity_pct"}
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "NASA POWER weather (real data)", "module_code": "climate", "kind": "public_data",
        "default_source_code": "nasa_power_weather", "owner": "NASA Langley Research Center – free public API, no key",
        "description": "Real daily rain, temperature and humidity at every located plot from NASA's satellite and "
                       "reanalysis datasets. Published with a delay of a few days. Not IMD data.",
        "config": {"days": 14, "lag_days": 3, "max_plots": 50}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        end = today_ist() - timedelta(days=_int(self.config, "lag_days", 3, 1, 30))
        start = end - timedelta(days=_int(self.config, "days", 14, 1, 366) - 1)
        data = request_json(client, "GET", self.URL, params={
            "parameters": ",".join(self.PARAMETERS), "community": "AG", "latitude": float(plot.latitude),
            "longitude": float(plot.longitude), "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}", "format": "JSON",
            "time-standard": "LST"})
        series = (data.get("properties") or {}).get("parameter") or {}
        header = data.get("header") or {}
        fill = header.get("fill_value", -999)
        dataset = "NASA POWER daily" + (f" ({', '.join(header['sources'])})" if header.get("sources") else "")
        coordinates = (data.get("geometry") or {}).get("coordinates") or [None, None]
        records = []
        for key in sorted({k for values in series.values() for k in values}):
            values = {}
            for name, target in self.PARAMETERS.items():
                value = (series.get(name) or {}).get(key)
                values[target] = None if value is None or value == fill else value
            if all(v is None for v in values.values()):
                continue
            day = datetime.strptime(key, "%Y%m%d").date()
            records.append(self.observation(
                f"NASA-POWER-{plot.plot_code}-{day}", "weather_estimate", plot,
                {"source_name": "NASA POWER", "dataset": dataset, "grid_latitude": coordinates[1],
                 "grid_longitude": coordinates[0], **values}, _noon(day)))
        return records


def ndvi(red_dn: float, nir_dn: float, processing_baseline: str | None) -> float | None:
    """NDVI from Sentinel-2 L2A digital numbers. Processing baseline 04.00 and later adds a +1000 offset."""
    try:
        major = int(str(processing_baseline).split(".")[0])
    except ValueError:
        major = 0
    offset = 1000 if major >= 4 else 0
    red, nir = max(red_dn - offset, 0) / 10000, max(nir_dn - offset, 0) / 10000
    if red + nir <= 0:
        return None
    return round((nir - red) / (nir + red), 4)


class Sentinel2NdviAdapter(PlatformAdapter):
    STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
    POINT_QUERY = "https://planetarycomputer.microsoft.com/api/data/v1/item/point/{lon},{lat}"
    COLLECTION = "sentinel-2-l2a"
    # Scene classification classes usable for NDVI; clouds, shadows, snow and missing data are skipped.
    CLEAR_CLASSES = {4: "vegetation", 5: "not_vegetated", 6: "water", 7: "unclassified"}
    page_size = 5
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "Sentinel-2 satellite greenness – NDVI (real data)", "module_code": "suitability", "kind": "public_data",
        "default_source_code": "sentinel2_ndvi",
        "owner": "ESA Copernicus Sentinel-2 via Microsoft Planetary Computer – free, no key",
        "description": "Real satellite vegetation greenness (NDVI) at every located plot from the most recent cloud-free "
                       "Sentinel-2 scene (10 m pixels). An indication of canopy condition, not a field verification.",
        "config": {"days": 90, "max_cloud_pct": 40, "scenes_to_try": 5, "max_plots": 25}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        lon, lat = float(plot.longitude), float(plot.latitude)
        end = today_ist()
        start = end - timedelta(days=_int(self.config, "days", 90, 5, 730))
        tries = _int(self.config, "scenes_to_try", 5, 1, 20)
        search = request_json(client, "POST", self.STAC_SEARCH, json={
            "collections": [self.COLLECTION], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": _int(self.config, "max_cloud_pct", 40, 1, 100)}},
            "sortby": [{"field": "datetime", "direction": "desc"}], "limit": tries,
            "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover",
                                   "properties.s2:processing_baseline"], "exclude": ["assets", "links", "geometry"]}})
        for item in (search.get("features") or [])[:tries]:
            props = item.get("properties") or {}
            point = request_json(client, "GET", self.POINT_QUERY.format(lon=lon, lat=lat), params=[
                ("collection", self.COLLECTION), ("item", item["id"]), ("assets", "B04"), ("assets", "B08"),
                ("assets", "SCL")])
            bands = dict(zip(point.get("band_names") or [], point.get("values") or [], strict=False))
            red, nir, scl = bands.get("B04_b1"), bands.get("B08_b1"), bands.get("SCL_b1")
            if red is None or nir is None or scl is None or int(scl) not in self.CLEAR_CLASSES:
                continue
            value = ndvi(red, nir, props.get("s2:processing_baseline"))
            if value is None:
                continue
            scene_time = datetime.fromisoformat(str(props["datetime"]).replace("Z", "+00:00"))
            cloud = props.get("eo:cloud_cover")
            return [self.observation(
                f"S2-NDVI-{plot.plot_code}-{item['id']}", "vegetation_index", plot,
                {"index": "ndvi", "index_value": value, "satellite": f"Copernicus Sentinel-2 ({self.COLLECTION})",
                 "scene_id": item["id"], "cloud_cover_pct": round(cloud, 2) if cloud is not None else None,
                 "pixel_class": self.CLEAR_CLASSES[int(scl)], "pixel_size_m": 10}, scene_time.isoformat())]
        return []


# -- synthetic stand-ins for the FarmFuture engines and the IoT gateway ---------------------------------------------
def _week(day: date) -> tuple[date, str]:
    monday = day - timedelta(days=day.weekday())
    year, week, _ = monday.isocalendar()
    return monday, f"{year}W{week:02d}"


def _monsoon(day: date) -> bool:
    return 6 <= day.month <= 10


class SampleClimateEngineAdapter(PlatformAdapter):
    HAZARDS = ["drought", "heat", "excess_rainfall"]
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "SAMPLE Climate Intelligence engine (stand-in for FarmFuture)", "module_code": "climate",
        "kind": "sample", "default_source_code": "sample_climate_engine", "owner": "Sample – replace with FarmFuture",
        "description": "Synthetic weekly drought / heat / excess-rainfall risk for every located plot, until the "
                       "FarmFuture Climate Intelligence API is supplied. Records are labelled DEMO.",
        "config": {"max_plots": 50}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        monday, week = _week(today_ist())
        hazard = self.HAZARDS[int(_score(plot.plot_code, week, "hazard") * 3)]
        bias = 1 if (hazard == "excess_rainfall") == _monsoon(monday) else 0
        level = RISK_LEVELS[min(3, int(_score(plot.plot_code, week, "risk") * 3) + bias)]
        return [self.observation(
            f"SAMPLE-CLIMATE-{plot.plot_code}-{week}", "climate_risk_assessment", plot,
            {"hazard_type": hazard, "risk_level": level, "valid_from": monday.isoformat(),
             "valid_to": (monday + timedelta(days=6)).isoformat(), "model_name": "SAMPLE climate engine",
             "model_version": "sample-1", "confidence": "Synthetic sample – not a real risk estimate"}, _midnight(monday))]


class SamplePestEngineAdapter(PlatformAdapter):
    PESTS = ["coffee_berry_borer", "white_stem_borer", "leaf_rust"]
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "SAMPLE Pest & Disease Prediction engine (stand-in for FarmFuture)", "module_code": "pest",
        "kind": "sample", "default_source_code": "sample_pest_engine", "owner": "Sample – replace with FarmFuture",
        "description": "Synthetic weekly pest and disease risk for every located plot, until the FarmFuture Pest & "
                       "Disease Prediction API is supplied. Records are labelled DEMO.",
        "config": {"max_plots": 50}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        monday, week = _week(today_ist())
        pest = self.PESTS[int(_score(plot.plot_code, week, "pest") * 3)]
        bias = 1 if _monsoon(monday) and pest != "white_stem_borer" else 0
        level = RISK_LEVELS[min(3, int(_score(plot.plot_code, week, "risk") * 3) + bias)]
        return [self.observation(
            f"SAMPLE-PEST-{plot.plot_code}-{week}", "pest_risk_estimate", plot,
            {"pest_or_disease": pest, "risk_level": level, "valid_from": monday.isoformat(),
             "valid_to": (monday + timedelta(days=6)).isoformat(), "model_name": "SAMPLE pest & disease engine"},
            _midnight(monday))]


class SampleWaterEngineAdapter(PlatformAdapter):
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "SAMPLE Water Balance engine (stand-in for FarmFuture)", "module_code": "water", "kind": "sample",
        "default_source_code": "sample_water_engine", "owner": "Sample – replace with FarmFuture",
        "description": "Synthetic weekly evapotranspiration, crop water requirement and irrigation recommendation (daily "
                       "mm) for every located plot, until the FarmFuture Water Balance API is supplied. Labelled DEMO.",
        "config": {"max_plots": 50}}

    def plot_records(self, client: httpx.Client, plot: FarmPlot) -> list[dict[str, Any]]:
        monday, week = _week(today_ist())
        et0 = round(3.0 + 2.5 * _score(plot.plot_code, week, "et0"), 1)
        requirement = round(et0 * (0.9 + 0.2 * _score(plot.plot_code, week, "kc")), 1)
        expected_rain = (4.0 if _monsoon(monday) else 1.5) * _score(plot.plot_code, week, "rain") * 2
        return [self.observation(
            f"SAMPLE-WATER-{plot.plot_code}-{week}", "water_balance_output", plot,
            {"engine_name": "SAMPLE water balance engine", "engine_version": "sample-1", "et0_mm": et0,
             "crop_water_requirement_mm": requirement,
             "recommended_irrigation_mm": round(max(0.0, requirement - expected_rain), 1)}, _midnight(monday))]


class SampleIotGatewayAdapter(PlatformAdapter):
    """Readings for the platform's active registered devices, only for the parameters each device type supports."""

    RANGES = {"soil_moisture": (18, 38, "%"), "soil_temperature": (18, 27, "°C"), "air_temperature": (17, 31, "°C"),
              "humidity": (60, 95, "%"), "rainfall": (0, 12, "mm"), "ph": (4.8, 6.4, "pH"), "ec": (0.1, 0.8, "dS/m"),
              "nitrogen": (120, 280, "mg/kg"), "phosphorus": (8, 30, "mg/kg"), "potassium": (90, 260, "mg/kg")}
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "SAMPLE IoT sensor gateway (stand-in for the real gateway)", "module_code": "soil", "kind": "sample",
        "default_source_code": "sample_iot_gateway", "owner": "Sample – replace with the IoT gateway",
        "description": "Synthetic readings every few hours for each ACTIVE registered device (SoilSync, Moisture Probe, "
                       "MicroClime, RainSync), only for the parameters that device type measures. Labelled DEMO.",
        "config": {"hours": 24, "interval_hours": 6}}

    def fetch_page(self, cursor: str | None) -> PageResult:
        devices = list(self.db.scalars(select(Device).where(Device.status == "active").order_by(Device.device_code)))
        if not devices:
            raise AppError(422, "no_active_devices", "No active devices are registered. Register a device on the Devices "
                                                     "screen and set its status to active first.")
        interval = _int(self.config, "interval_hours", 6, 1, 24)
        now = datetime.now(IST)
        last = now.replace(hour=now.hour - now.hour % interval, minute=0, second=0, microsecond=0)
        slots = [last - timedelta(hours=interval * k) for k in range(max(1, _int(self.config, "hours", 24, 1, 168) // interval))]
        records = []
        for device in devices:
            for parameter in sorted(DEVICE_PARAMETERS.get(device.device_type, ())):
                low, high, unit = self.RANGES[parameter]
                for slot in slots:
                    records.append({
                        "source_record_id": f"SAMPLE-IOT-{device.device_code}-{parameter}-{slot:%Y%m%d%H}",
                        "programme_code": self.programme.code, "module_code": "soil", "record_type": "sensor_observation",
                        "device_code": device.device_code, "observed_at": slot.isoformat(),
                        "attributes": {"parameter": parameter, "value": round(low + (high - low) * _score(
                            device.device_code, parameter, slot.isoformat()), 2), "unit": unit, "quality_flag": "ok"}})
        return PageResult(records, None)
