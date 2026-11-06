"""FarmFuture farm data – live, read-only pulls for Workstreams 3, 4 and 5, plus the farm advisories, and device import.

Only HTTP GET is ever sent to the FarmFuture API (through ``FarmFutureApi._request``):
  GET /api/Data/GetUserDevices/{farm_id}                  devices installed on a farm (device import)
  GET /api/Data/GetFarmStats?farmId=&startDate=&endDate=  daily min / max / mean per SoilSync and MicroClime device
  GET /api/Data/GetMinMaxMean{,Calibrated,LabAnchored}     per-device daily statistics of another sensor series
  GET /api/Data/GetAllSensorsLatestDataV2/{farm_id}       latest SoilSync reading time and battery (device health)
  GET /api/Data/GetAllWeatherData?farmId=                 latest MicroClime reading time and battery (device health)
  GET /api/Data/GetAllAnalogueWeatherData?farmId=         latest Analogue Weather reading, battery and signal
  GET /api/Data/GetWeatherStats?macAddress=&parameter=    daily station rain history
  GET /api/FarmProfile/{farm_id}                          terrain, soil and erosion profile of the farm (site characteristics)
  GET /api/FarmProfile/{farm_id}/climate                  30-year climate normals of the farm location
  GET /api/SoilLabReport/GetByFarm/{farm_id}              soil lab reports
  GET /api/wsb/records?farm=                              field scouting records (plants inspected / with signs)
  GET /api/Satellite/ZoneSummary/{farm_id}                block (zone) NDVI / NDMI summary of the latest satellite pass
  GET /api/wsb/sources?farm=                              which satellite scene the summary used
  GET /api/Advisory/{farm_id}/latest | /history | ?date=  the FarmFuture advisory engine's farm advisories
  GET /WeatherForecast/forecast?lat=&lon=                 5-day, 3-hourly weather forecast for a farm location

Farms are the FarmFuture farms already linked to platform plots by a reviewed identity map (entity farm_plot) on any
FarmFuture source – normally created by "Import farms from FarmFuture" on the Water Balance Engine source. The adapters
therefore send platform plot / device / farmer codes. The Swagger publishes no response schemas, so the field mapping
follows live responses (November 2026); a changed shape fails validation and is quarantined instead of being guessed.
Personal data in FarmFuture responses (phone numbers, notes, photos, GPS points of scouting) is never stored.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.models import Device, Farm, Farmer, FarmPlot, GeoUnit, IntegrationSource, SourceRecordMap
from app.services import audit, crud
from app.services.integration.base import PageResult, TransientSourceError
from app.services.integration.farmfuture import FarmFutureApi
from app.services.integration.platform_adapters import IST, PlatformAdapter, _int, _noon, today_ist
from app.services.record_types import MONTHS, PESTS

FARMFUTURE_ADAPTERS = {"farmfuture_water_engine", "farmfuture_soil_microclimate", "farmfuture_pest_scouting",
                       "farmfuture_satellite", "farmfuture_advisories", "farmfuture_weather"}
OWNER = "FarmFuture (Varsapradaya)"


@dataclass(frozen=True)
class FarmLink:
    farm_id: str
    plot: FarmPlot
    farmer_code: str
    geo_code: str


def linked_farms(db: Session) -> dict[str, FarmLink]:
    """FarmFuture farm id → platform plot, from reviewed farm_plot identity maps on any FarmFuture source (first wins)."""
    rows = db.execute(
        select(SourceRecordMap.source_record_id, FarmPlot, Farmer.farmer_code, GeoUnit.code)
        .join(IntegrationSource, IntegrationSource.id == SourceRecordMap.source_id)
        .join(FarmPlot, FarmPlot.id == SourceRecordMap.internal_record_id)
        .join(Farm, Farm.id == FarmPlot.farm_id).join(Farmer, Farmer.id == Farm.farmer_id)
        .join(GeoUnit, GeoUnit.id == Farmer.geo_unit_id)
        .where(SourceRecordMap.entity_type == "farm_plot", IntegrationSource.adapter_code.in_(FARMFUTURE_ADAPTERS),
               FarmPlot.is_archived == false(), Farm.is_archived == false(), Farmer.is_archived == false())
        .order_by(SourceRecordMap.first_seen_at)).unique().all()
    links: dict[str, FarmLink] = {}
    for farm_id, plot, farmer_code, geo_code in rows:
        links.setdefault(str(farm_id), FarmLink(str(farm_id), plot, farmer_code, geo_code))
    return links


def _day(value: Any) -> date | None:
    """Date part of a FarmFuture date or timestamp string (they send both '2026-10-27' and '2026-10-27T00:00:00')."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _num(value: Any, digits: int = 4) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class FarmFutureFarmAdapter(FarmFutureApi, PlatformAdapter):
    """Base: pages through the linked FarmFuture farms and asks ``farm_records`` for each farm's records."""

    uses_platform_codes = True  # the adapter resolves FarmFuture ids to platform codes through reviewed maps
    page_size = 5

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        super().__init__(db, source)
        self._init_api()
        self._links: list[FarmLink] | None = None

    def links(self) -> list[FarmLink]:
        if self._links is None:
            found = linked_farms(self.db)
            self._links = [found[k] for k in sorted(found)][:_int(self.config, "max_farms", 200, 1, 1000)]
            if not self._links:
                raise AppError(422, "no_linked_farms",
                               "No FarmFuture farm is linked to a platform plot yet. Run 'Import farms from FarmFuture' on "
                               "the FarmFuture Water Balance Engine source first (or add farm_plot identity maps).")
        return self._links

    def get_data(self, client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a FarmFuture resource. None when the farm has no such data (HTTP 204/400/404/422, or succeeded=false);
        the {"succeeded", "data"} envelope is unwrapped."""
        response = self._request(client, path, params)
        if response.status_code in (204, 400, 404, 422):
            return None
        if response.status_code >= 400:
            raise AppError(502, "source_error", f"FarmFuture {path} failed (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError as exc:
            raise AppError(502, "source_error", f"FarmFuture {path} returned a response that is not JSON") from exc
        if isinstance(body, dict) and "succeeded" in body:
            return body.get("data") if body.get("succeeded") else None
        return body

    def fetch_page(self, cursor: str | None) -> PageResult:
        start = int(cursor or 0)
        links = self.links()
        with self.client() as client:
            records = [r for link in links[start:start + self.page_size] for r in self.farm_records(client, link)]
        end = start + self.page_size
        return PageResult(records, str(end) if end < len(links) else None)

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        raise NotImplementedError


# -- Workstream 4: soil & microclimate ------------------------------------------------------------------------------
# FarmStats parameter → (platform parameter, unit or None = from config "units", layer). The FarmFuture web app labels
# N, P and K in kg/ha and EC in µS/cm (Farm Insights, device cards – reviewed 6 Nov 2026), so those units are fixed here
# and an older source config cannot override them.
SOIL_STATS: dict[str, tuple[str, str | None, str | None]] = {
    "soil_moisture_percentage_L1": ("soil_moisture", "%", "L1"),
    "soil_moisture_percentage_L2": ("soil_moisture", "%", "L2"),
    "temperature": ("soil_temperature", "°C", None),
    "pH": ("ph", "pH", None),
    "ec": ("ec", "µS/cm", None),
    "nitrogen": ("nitrogen", "kg/ha", None),
    "phosphorous": ("phosphorus", "kg/ha", None),
    "potassium": ("potassium", "kg/ha", None),
}  # "humidity" repeats soil_moisture_percentage_L1 and "voltage" is battery level – neither is imported
# Soil parameters whose value depends on the processing series. Moisture and temperature are the same in every series.
SERIES_PARAMETERS = ("pH", "ec", "nitrogen", "phosphorous", "potassium")
# Series → FarmFuture per-device daily statistics endpoint ("app" = GetFarmStats, the values the FarmFuture app shows).
SERIES_ENDPOINTS = {"raw": "/api/Data/GetMinMaxMean", "calibrated": "/api/Data/GetMinMaxMeanCalibrated",
                    "lab_anchored": "/api/Data/GetMinMaxMeanLabAnchored"}
SERIES_LABELS = {"app": "FarmFuture daily device statistics (as shown in the FarmFuture app)",
                 "raw": "FarmFuture daily device statistics – raw sensor series",
                 "calibrated": "FarmFuture daily device statistics – calibrated series",
                 "lab_anchored": "FarmFuture daily device statistics – lab-anchored series"}
WEATHER_STATS: dict[str, tuple[str, str | None]] = {
    "air_temperature_c": ("air_temperature", "°C"),
    "air_humidity_pct": ("humidity", "%"),
    "rainfall_mm": ("rainfall", "mm"),
    "air_pressure_hpa": ("air_pressure", "hPa"),
    "wind_speed_ms": ("wind_speed", "m/s"),
    "leaf_wetness": ("leaf_wetness", None),
    "lux": ("light_intensity", "lux"),
}
# Units FarmFuture does not state (leaf wetness has no unit in the app either); pending FarmFuture confirmation.
DEFAULT_UNITS = {"leaf_wetness": "raw"}
# FarmFuture deviceTypeId → platform device type, for the types whose readings were seen in live data.
DEFAULT_DEVICE_TYPES = {"2": "soilsync_7in1", "4": "soilsync_7in1", "5": "microclime", "11": "analogue_weather"}
# Analogue Weather latest-reading field → (platform parameter, unit). Pressure (0 = not measured), the running rain total
# and the soil channels without a stated unit are not imported.
ANALOGUE_READINGS = {"airTemperature": ("air_temperature", "°C"), "airHumidity": ("humidity", "%"),
                     "windSpeed": ("wind_speed", "m/s"), "leafWetness": ("leaf_wetness", "raw"),
                     "lux": ("light_intensity", "lux")}


def _ff_time(value: Any) -> datetime | None:
    """FarmFuture 'updateAt' ('2026-11-06 14:25:49', India time) → aware datetime."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return datetime.fromisoformat(value[:19].replace(" ", "T")).replace(tzinfo=IST)
    except ValueError:
        return None


class FarmFutureSoilMicroclimateAdapter(FarmFutureFarmAdapter):
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture soil & microclimate sensors (live API, read-only)", "module_code": "soil", "kind": "live_api",
        "default_source_code": "farmfuture_soil_microclimate", "owner": OWNER,
        "description": "Daily minimum / maximum / mean of every SoilSync (soil moisture at two depths, soil temperature, "
                       "pH, EC, N, P, K) and MicroClime (air temperature, humidity, rain, pressure, wind, leaf wetness, "
                       "light) device on the linked FarmFuture farms, plus soil lab reports. Run 'Import devices from "
                       "FarmFuture' first. N/P/K are in kg/ha and EC in µS/cm (as labelled in the FarmFuture app); the "
                       "leaf-wetness unit is not stated. 'series' chooses which processing of pH, EC and N/P/K is "
                       "imported: app (default – the values the FarmFuture app shows), raw, calibrated or lab_anchored.",
        "config": {"past_days": 3, "sensor_history_days": 30, "series": "app", "units": DEFAULT_UNITS,
                   "device_types": DEFAULT_DEVICE_TYPES,
                   "include_lab_reports": True, "include_reports_without_lab_name": False, "device_health": True,
                   "analogue_readings": True}}

    def __init__(self, db: Session, source: IntegrationSource, require_devices: bool = True) -> None:
        super().__init__(db, source)
        self.units = {**DEFAULT_UNITS, **(self.config.get("units") or {})}
        self.series = str(self.config.get("series") or "app")
        if self.series not in SERIES_LABELS:
            raise AppError(422, "source_config", f"Unknown sensor series '{self.series}' – use one of "
                                                 + ", ".join(SERIES_LABELS))
        self.devices = farmfuture_devices(db)  # device links made by any FarmFuture source
        self._imported_days: set[tuple[str, date]] | None = None
        if require_devices and not self.devices:
            raise AppError(422, "no_mapped_devices", "No FarmFuture device is linked to a platform device yet. Run "
                                                     "'Import devices from FarmFuture' on this source first.")

    def imported_days(self) -> set[tuple[str, date]]:
        """(MAC, day) pairs whose daily statistics this source already imported (from its idempotency records)."""
        if self._imported_days is None:
            self._imported_days = set()
            for record_id in self.db.scalars(select(SourceRecordMap.source_record_id).where(
                    SourceRecordMap.source_id == self.source.id, SourceRecordMap.entity_type == "field_record",
                    SourceRecordMap.source_record_id.like("FF-SENS-%"))):
                parts, day = record_id.split("-"), _day(record_id[-10:])
                if len(parts) > 3 and day is not None:
                    self._imported_days.add((parts[2], day))
        return self._imported_days

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        end = today_ist() - timedelta(days=1)  # whole days only; today is still filling up
        recent = end - timedelta(days=_int(self.config, "past_days", 3, 1, 31) - 1)
        # Earlier days (sensor_history_days) are filled in once: a day already imported for a device is not sent again.
        start = min(recent, end - timedelta(days=_int(self.config, "sensor_history_days", 30, 0, 365) - 1))
        stats = self.get_data(client, "/api/Data/GetFarmStats", {
            "farmId": link.farm_id, "startDate": f"{start}T00:00:00", "endDate": f"{end}T23:59:59"}) or {}
        done = self.imported_days() if start < recent else set()
        records: list[dict[str, Any]] = []
        for group, table in (("sensorDevices", SOIL_STATS), ("weatherDevices", WEATHER_STATS)):
            for device in (stats.get(group) or []) if isinstance(stats, dict) else []:
                device_code = self.devices.get(str(device.get("macAddress") or ""))
                if device_code is None:  # not imported (unsupported type or added after the import) – skipped
                    continue
                daily = device.get("stats") or {}
                if group == "sensorDevices" and self.series != "app":
                    daily = self._series_stats(client, device["macAddress"], start, end, daily)
                for day_text, values in daily.items():
                    day = _day(day_text)
                    if day is None or not (start <= day <= end) or not isinstance(values, dict):
                        continue
                    if day < recent and (device["macAddress"], day) in done:
                        continue
                    for name, spec in table.items():
                        records.extend(self._reading(device_code, device["macAddress"], day, name, spec, values.get(name)))
        if self.config.get("include_lab_reports", True):
            records.extend(self._lab_reports(client, link))
        if self.config.get("device_health", True) or self.config.get("analogue_readings", True):
            records.extend(self._device_status(client, link))
        return records

    def _device_status(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        """Daily health snapshot (battery, signal, last reading) of every linked device, and the latest Analogue
        Weather reading (that station type has no daily statistics in the API)."""
        latest: list[tuple[dict[str, Any], float | None, int | None]] = []
        if self.config.get("device_health", True):
            for item in self.get_data(client, f"/api/Data/GetAllSensorsLatestDataV2/{link.farm_id}") or []:
                latest.append((item, _num(((item or {}).get("soilProperties") or {}).get("voltage"), 1), None))
            for item in self.get_data(client, "/api/Data/GetAllWeatherData", {"farmId": link.farm_id}) or []:
                latest.append((item, _num((item or {}).get("voltage"), 1), None))
        analogue = self.get_data(client, "/api/Data/GetAllAnalogueWeatherData", {"farmId": link.farm_id}) or []
        records: list[dict[str, Any]] = []
        for item in analogue if isinstance(analogue, list) else []:
            if self.config.get("device_health", True):
                signal = item.get("signalStrength") if isinstance(item.get("signalStrength"), int) else None
                latest.append((item, _num(item.get("batteryPercentage"), 1), signal))
            if self.config.get("analogue_readings", True):
                records.extend(self._analogue_readings(item))
        now = datetime.now(IST)
        for item, battery, signal in latest:
            mac = str((item or {}).get("macId") or "")
            device_code = self.devices.get(mac)
            when = _ff_time(item.get("updateAt")) if isinstance(item, dict) else None
            if device_code is None or when is None:
                continue
            records.append({
                "source_record_id": f"FF-HEALTH-{mac}-{when.date()}", "programme_code": self.programme.code,
                "module_code": "soil", "record_type": "device_health", "device_code": device_code,
                "observed_at": when.isoformat(), "attributes": {k: v for k, v in {
                    "battery_pct": battery if battery is not None and 0 <= battery <= 100 else None, "signal_csq": signal,
                    "reporting_now": now - when <= timedelta(hours=24),
                    "source_name": "FarmFuture device status"}.items() if v is not None}})
        return records

    def _analogue_readings(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        mac = str(item.get("macId") or "")
        device_code = self.devices.get(mac)
        when = _ff_time(item.get("updateAt"))
        if device_code is None or when is None:
            return []
        stamp = when.strftime("%Y%m%dT%H%M")
        records = []
        for field, (parameter, unit) in ANALOGUE_READINGS.items():
            value = _num(item.get(field), 2)
            if value is None:
                continue
            records.append({
                "source_record_id": f"FF-SENS-{mac}-{parameter}-{stamp}", "programme_code": self.programme.code,
                "module_code": "soil", "record_type": "sensor_observation", "device_code": device_code,
                "observed_at": when.isoformat(), "attributes": {
                    "parameter": parameter, "value": value, "unit": unit, "aggregation": "reading", "series": "app",
                    "source_dataset": "FarmFuture latest Analogue Weather reading"}})
        return records

    def _series_stats(self, client: httpx.Client, mac: str, start: date, end: date,
                      daily: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Replace pH, EC and N/P/K in the app statistics by the chosen series (one GET per parameter); a parameter the
        series does not supply for a day is left out rather than mixed with the app value."""
        merged = {day: {k: v for k, v in (values or {}).items() if k not in SERIES_PARAMETERS}
                  for day, values in daily.items() if isinstance(values, dict)}
        for name in SERIES_PARAMETERS:
            body = self.get_data(client, SERIES_ENDPOINTS[self.series], {
                "startDate": f"{start}T00:00:00", "endDate": f"{end}T23:59:59", "macAddress": mac, "parameter": name})
            for day_text, values in ((body or {}).get("stats") or {}).items() if isinstance(body, dict) else []:
                if isinstance(values, dict) and isinstance(values.get(name), dict):
                    merged.setdefault(day_text, {})[name] = values[name]
        return merged

    def _reading(self, device_code: str, mac: str, day: date, name: str, spec: tuple, summary: Any) -> list[dict]:
        if not isinstance(summary, dict):
            return []
        series = self.series if name in SERIES_PARAMETERS else "app"
        parameter, unit = spec[0], spec[1] or self.units.get(spec[0])
        layer = spec[2] if len(spec) > 2 else None
        low, high = _num(summary.get("min")), _num(summary.get("max"))
        mean = _num(summary.get("mean", summary.get("avg")))
        # The rain value repeats through the day (a running daily amount), so the day's maximum is used, never the mean.
        aggregation, value = ("daily_max", high) if parameter == "rainfall" else ("daily_mean", mean)
        if value is None or unit is None:
            return []
        if parameter != "rainfall" and low == high == mean == 0:
            return []  # an offline device reports the day as all zeros (0 % moisture, pH 0) – no reading, not a value
        suffix = f"-{layer}" if layer else ""
        return [{"source_record_id": f"FF-SENS-{mac}-{parameter}{suffix}-{day}", "programme_code": self.programme.code,
                 "module_code": "soil", "record_type": "sensor_observation", "device_code": device_code,
                 "observed_at": _noon(day),
                 "attributes": {k: v for k, v in {
                     "parameter": parameter, "value": value, "unit": unit, "aggregation": aggregation,
                     "value_min": low, "value_max": high, "layer": layer,
                     "source_dataset": SERIES_LABELS[series], "series": series}.items() if v is not None}}]

    def _lab_reports(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        records = []
        for report in self.get_data(client, f"/api/SoilLabReport/GetByFarm/{link.farm_id}") or []:
            if not isinstance(report, dict) or not report.get("id") or report.get("isActive") is False:
                continue
            lab = (report.get("labName") or "").strip()
            if not lab and not self.config.get("include_reports_without_lab_name"):
                continue  # device reference values, not a laboratory test
            day = _day(report.get("sampledOn")) or _day(report.get("reportedOn"))
            oxide = report.get("unitBasis") == "kg_ha_oxide"
            attributes = {
                "lab_name": lab or f"FarmFuture soil report ({report.get('sampleRefId') or 'no lab named'})",
                "report_ref": report.get("labReportNo"), "ph": _num(report.get("ph"), 2),
                "organic_carbon_pct": _num(report.get("organicCarbon"), 3),
                "nitrogen_kg_ha": _num(report.get("n_KgHa", report.get("availableN")), 2),
                "p2o5_kg_ha": _num(report.get("p2O5_KgHa", report.get("availableP2O5")), 2) if oxide else None,
                "k2o_kg_ha": _num(report.get("k2O_KgHa", report.get("availableK2O")), 2) if oxide else None}
            records.append(self.observation(f"FF-SOIL-{report['id']}", "soil_test", link.plot, attributes,
                                            _noon(day) if day else None))
        return records


def import_devices(db: Session, current: CurrentUser, source: IntegrationSource) -> dict[str, Any]:
    """Register the SoilSync / MicroClime devices of the linked FarmFuture farms in the platform device registry.

    Each device is placed on the plot linked to its FarmFuture farm and linked by a reviewed identity map (entity device,
    source id = MAC address). Device types without a platform equivalent (hub, air quality, analogue weather) are
    listed as skipped. Re-running only adds devices not yet imported. Read-only on the FarmFuture side.
    """
    if source.adapter_code != "farmfuture_soil_microclimate":
        raise AppError(422, "wrong_adapter", "Device import is available only for the FarmFuture soil & microclimate source")
    adapter = FarmFutureSoilMicroclimateAdapter(db, source, require_devices=False)
    types = {str(k): v for k, v in {**DEFAULT_DEVICE_TYPES, **(adapter.config.get("device_types") or {})}.items()}
    mapped = set(adapter.devices)  # already linked by this or another FarmFuture source
    summary: dict[str, Any] = {"farms": 0, "devices": 0, "devices_created": 0, "already_imported": 0, "skipped": []}
    try:
        with adapter.client() as client:
            for link in adapter.links():
                summary["farms"] += 1
                for item in adapter.get_data(client, f"/api/Data/GetUserDevices/{link.farm_id}") or []:
                    mac = str(item.get("macAdress") or item.get("macAddress") or "").strip()
                    if not mac:
                        continue
                    summary["devices"] += 1
                    name = (item.get("deviceName") or "").strip() or mac
                    if mac in mapped:
                        summary["already_imported"] += 1
                        continue
                    device_type = types.get(str(item.get("deviceTypeId")))
                    if device_type is None:
                        summary["skipped"].append(f"{name} ({mac}): FarmFuture device type {item.get('deviceTypeId')} "
                                                  "has no platform device type")
                        continue
                    code = f"FF-{mac}"[:60]
                    existing = db.scalar(select(Device).where(Device.device_code == code))
                    if existing is not None:
                        if existing.provider != "FarmFuture":
                            summary["skipped"].append(f"{name} ({mac}): device code {code} already used")
                            continue
                        # an earlier FarmFuture import whose link was removed: re-link it instead of duplicating
                        db.add(SourceRecordMap(source_id=source.id, entity_type="device", source_record_id=mac,
                                               internal_record_id=existing.id, mapped_by_id=current.id,
                                               review_note="Re-linked by FarmFuture device import"))
                        mapped.add(mac)
                        summary["already_imported"] += 1
                        continue
                    farmer = db.get(Farmer, db.scalar(select(Farm.farmer_id).where(Farm.id == link.plot.farm_id)))
                    device = crud.create(db, current, Device, {
                        "device_code": code, "device_type": device_type, "provider": "FarmFuture",
                        "farm_plot_id": link.plot.id, "geo_unit_id": farmer.geo_unit_id,
                        "organisation_id": farmer.primary_fpo_id, "installed_on": _day(item.get("createdDate")),
                        "status": "active",
                        "notes": f"Imported from FarmFuture: {name[:120]} (device type {item.get('deviceTypeId')}, "
                                 f"calibrated in FarmFuture: {'yes' if item.get('isCallibrated') else 'no'})"},
                        "device", summary=f"Imported FarmFuture device {mac}")
                    db.add(SourceRecordMap(source_id=source.id, entity_type="device", source_record_id=mac,
                                           internal_record_id=device.id, mapped_by_id=current.id,
                                           review_note="Created by FarmFuture device import"))
                    mapped.add(mac)
                    summary["devices_created"] += 1
    except TransientSourceError as exc:
        db.rollback()
        raise AppError(502, "source_unavailable", f"FarmFuture is not reachable right now ({exc}); try again") from exc
    audit.record(db, current, "import_devices", "integration_source", source.id,
                 after={k: v for k, v in summary.items() if k != "skipped"})
    db.commit()
    return summary


# -- Workstream 1: climate – on-farm station weather and the FarmFuture forecast ------------------------------------
def farmfuture_devices(db: Session) -> dict[str, str]:
    """FarmFuture MAC address → platform device code, from device identity maps on any FarmFuture source."""
    return dict(db.execute(
        select(SourceRecordMap.source_record_id, Device.device_code)
        .join(IntegrationSource, IntegrationSource.id == SourceRecordMap.source_id)
        .join(Device, Device.id == SourceRecordMap.internal_record_id)
        .where(SourceRecordMap.entity_type == "device", IntegrationSource.adapter_code.in_(FARMFUTURE_ADAPTERS))).all())


def _summary(values: dict[str, Any], name: str, key: str) -> float | None:
    item = values.get(name)
    return _num(item.get(key), 2) if isinstance(item, dict) else None


def climate_normals_attributes(body: dict[str, Any]) -> dict[str, Any] | None:
    """FarmFuture farm-profile climate (30-year normals) → climate_normals attributes; None when incomplete."""
    series = body.get("series") or {}
    annual = body.get("annual_rainfall") or {}
    months = {m.get("month"): m for m in body.get("monthly_rainfall") or [] if isinstance(m, dict)}
    start, end = series.get("start_year"), series.get("end_year")
    if not isinstance(start, int) or not isinstance(end, int) or len(months) != 12 or _num(annual.get("mean_mm")) is None:
        return None
    attributes: dict[str, Any] = {
        "source_name": "FarmFuture farm climate profile", "rainfall_dataset": str(series.get("rainfall_source") or "")[:200]
        or "not stated", "temperature_dataset": str(series.get("temperature_source") or "")[:200] or None,
        "period_start_year": start, "period_end_year": end, "annual_rain_mm": _num(annual.get("mean_mm"), 1),
        "annual_rain_sd_mm": _num(annual.get("std_mm"), 1), "annual_rain_cv_pct": _num(annual.get("cv_pct"), 1)}
    for number, (key, _) in enumerate(MONTHS, start=1):
        month = months.get(number) or {}
        mean = _num(month.get("mean_mm"), 1)
        if mean is None:
            return None
        attributes[f"rain_{key}_mm"] = mean
        attributes[f"rain_{key}_max_mm"] = _num(month.get("max_mm"), 1)
    monsoon = next((s for s in body.get("seasonal_rainfall") or [] if s.get("season") == "sw_monsoon"), {})
    temperature = body.get("annual_temperature") or {}
    dry = body.get("dry_spell") or {}
    attributes.update({
        "monsoon_share_pct": _num(monsoon.get("pct_of_annual"), 1), "mean_temp_c": _num(temperature.get("mean_c"), 1),
        "mean_min_temp_c": _num(temperature.get("mean_min_c"), 1), "mean_max_temp_c": _num(temperature.get("mean_max_c"), 1),
        "dry_day_threshold_mm": _num(dry.get("threshold_mm"), 1), "dry_days_per_year": _num(dry.get("mean_annual_dry_days"), 1),
        "usual_longest_dry_spell_days": _num(dry.get("mean_max_annual_cdd"), 1),
        "longest_dry_spell_days": _num(dry.get("longest_cdd_on_record"), 0),
        "longest_dry_spell_year": dry.get("longest_cdd_year") if isinstance(dry.get("longest_cdd_year"), int) else None})
    return attributes


def forecast_days(body: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Daily rain total, max / min temperature and mean humidity from the 3-hourly FarmFuture forecast.

    Days are India Standard Time days; only days covered by all eight 3-hour steps are returned (a part day would
    understate the rain), and only from tomorrow on. Temperatures arrive in kelvin and are converted to °C.
    """
    slots: dict[date, list[dict[str, Any]]] = {}
    for slot in body.get("list") or []:
        if not isinstance(slot, dict) or not isinstance(slot.get("dt"), (int, float)):
            continue
        day = datetime.fromtimestamp(slot["dt"], IST).date()
        slots.setdefault(day, []).append(slot)

    def celsius(value: Any) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return round(value - 273.15, 1) if value > 170 else round(float(value), 1)  # kelvin unless clearly °C

    days = []
    for day in sorted(slots):
        items = slots[day]
        if day <= today or len(items) < 8:
            continue
        mains = [s.get("main") or {} for s in items]
        highs = [t for m in mains if (t := celsius(m.get("temp_max", m.get("temp")))) is not None]
        lows = [t for m in mains if (t := celsius(m.get("temp_min", m.get("temp")))) is not None]
        humid = [m["humidity"] for m in mains if isinstance(m.get("humidity"), (int, float))]
        rain = sum(float((s.get("rain") or {}).get("3h") or 0) for s in items)
        days.append({"forecast_date": day, "rainfall_mm": round(rain, 1),
                     "temp_max_c": max(highs) if highs else None, "temp_min_c": min(lows) if lows else None,
                     "relative_humidity_pct": round(sum(humid) / len(humid), 1) if humid else None})
    return days


class FarmFutureWeatherAdapter(FarmFutureFarmAdapter):
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture on-farm weather & forecast (live API, read-only)", "module_code": "climate",
        "kind": "live_api", "default_source_code": "farmfuture_weather", "owner": OWNER,
        "description": "Climate observations from the MicroClime weather stations on the linked FarmFuture farms (daily "
                       "rain, maximum / minimum temperature, mean humidity) and FarmFuture's 5-day weather forecast for "
                       "each located farm (daily rain total, maximum / minimum temperature, mean humidity). "
                       "'rain_history_days' also fills in the station's daily rain for earlier days not yet imported "
                       "(days without data stay missing, never zero), and 'climate_normals' imports each farm's 30-year "
                       "climate normals (CHIRPS rain, ERA5 temperature) used for the rainfall status. FarmFuture "
                       "publishes no climate-risk API.",
        "config": {"past_days": 3, "rain_history_days": 365, "station_observations": True, "forecast": True,
                   "climate_normals": True}}

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        super().__init__(db, source)
        self.devices = farmfuture_devices(db)
        # A station listed on several FarmFuture farms belongs to the plot its device is registered on (or, when it is not
        # registered, to the first farm that lists it) – otherwise its days would flip between plots on every pull.
        self.device_plots = dict(db.execute(select(Device.device_code, Device.farm_plot_id).where(
            Device.device_code.in_(list(self.devices.values()) or [""]))).all())
        self._station_farm: dict[str, str] = {}  # unregistered station → the farm that listed it first

    def _owns_station(self, link: FarmLink, mac: str) -> bool:
        plot_id = self.device_plots.get(self.devices.get(mac, ""))
        if plot_id is not None:
            return plot_id == link.plot.id
        return self._station_farm.setdefault(mac, link.farm_id) == link.farm_id

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if self.config.get("station_observations", True):
            records.extend(self._station_days(client, link))
        if self.config.get("forecast", True) and link.plot.latitude is not None and link.plot.longitude is not None:
            records.extend(self._forecast(client, link))
        if self.config.get("climate_normals", True):
            records.extend(self._normals(client, link))
        return records

    def _normals(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        """The farm's 30-year climate normals (FarmFuture farm profile); the location itself is not stored."""
        body = self.get_data(client, f"/api/FarmProfile/{link.farm_id}/climate")
        if not isinstance(body, dict):
            return []
        attributes = climate_normals_attributes(body)
        if attributes is None:
            return []
        end = attributes["period_end_year"]
        return [self.observation(f"FF-NORM-{link.farm_id}-{attributes['period_start_year']}-{end}", "climate_normals",
                                 link.plot, attributes, _noon(date(end, 12, 31)))]

    def _station_days(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        end = today_ist() - timedelta(days=1)  # whole days only
        start = end - timedelta(days=_int(self.config, "past_days", 3, 1, 31) - 1)
        stats = self.get_data(client, "/api/Data/GetFarmStats", {
            "farmId": link.farm_id, "startDate": f"{start}T00:00:00", "endDate": f"{end}T23:59:59"}) or {}
        records = []
        for station in (stats.get("weatherDevices") or []) if isinstance(stats, dict) else []:
            mac = str(station.get("macAddress") or "")
            if not mac or not self._owns_station(link, mac):
                continue
            for day_text, values in (station.get("stats") or {}).items():
                day = _day(day_text)
                if not mac or day is None or not (start <= day <= end) or not isinstance(values, dict):
                    continue
                attributes = {
                    "source_name": f"FarmFuture MicroClime station ({(station.get('deviceName') or mac).strip()[:80]})",
                    "station_ref": self.devices.get(mac, mac), "rainfall_mm": _summary(values, "rainfall_mm", "max"),
                    "temp_max_c": _summary(values, "air_temperature_c", "max"),
                    "temp_min_c": _summary(values, "air_temperature_c", "min"),
                    "relative_humidity_pct": _summary(values, "air_humidity_pct", "avg")}
                if attributes["temp_max_c"] == attributes["temp_min_c"] == attributes["relative_humidity_pct"] == 0:
                    continue  # an offline station reports the day as all zeros
                if all(attributes[k] is None for k in ("rainfall_mm", "temp_max_c", "temp_min_c", "relative_humidity_pct")):
                    continue
                records.append(self._station_record(link, mac, day, attributes))
            records.extend(self._rain_history(client, link, station, start))
        return records

    def _station_record(self, link: FarmLink, mac: str, day: date, attributes: dict[str, Any]) -> dict[str, Any]:
        record = self.observation(f"FF-WX-{mac}-{day}", "climate_observation", link.plot, attributes, _noon(day))
        if mac in self.devices:
            record["device_code"] = self.devices[mac]
        return record

    def _rain_history(self, client: httpx.Client, link: FarmLink, station: dict[str, Any],
                      window_start: date) -> list[dict[str, Any]]:
        """Daily station rain for the days before the recent window that are not imported yet (rain only).

        A day already imported is left as it is, so a full station record is never replaced by a rain-only one. A day
        the station did not report is absent from FarmFuture's series and stays missing – it is never counted as 0 mm.
        """
        mac = str(station.get("macAddress") or "")
        history = _int(self.config, "rain_history_days", 365, 0, 1100)
        first = window_start - timedelta(days=history)
        last = window_start - timedelta(days=1)
        if not mac or history == 0 or last < first:
            return []
        prefix = f"FF-WX-{mac}-"
        done = set(self.db.scalars(select(SourceRecordMap.source_record_id).where(
            SourceRecordMap.source_id == self.source.id, SourceRecordMap.entity_type == "field_record",
            SourceRecordMap.source_record_id.like(f"{prefix}%"))))
        body = self.get_data(client, "/api/Data/GetWeatherStats", {
            "startDate": f"{first}T00:00:00", "endDate": f"{last}T23:59:59", "macAddress": mac,
            "parameter": "rainfall_mm"}) or {}
        name = (station.get("deviceName") or mac).strip()[:80]
        records = []
        for day_text, values in ((body.get("stats") or {}).items() if isinstance(body, dict) else []):
            day = _day(day_text)
            rain = (values or {}).get("rainfall_mm") if isinstance(values, dict) else None
            amount = _num(rain.get("last", rain.get("max")), 2) if isinstance(rain, dict) else None
            if day is None or not (first <= day <= last) or amount is None or f"{prefix}{day}" in done:
                continue
            records.append(self._station_record(link, mac, day, {
                "source_name": f"FarmFuture MicroClime station ({name})", "station_ref": self.devices.get(mac, mac),
                "rainfall_mm": amount}))
        return records

    def _forecast(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        body = self.get_data(client, "/WeatherForecast/forecast", {
            "lat": float(link.plot.latitude), "lon": float(link.plot.longitude)})
        if not isinstance(body, dict):
            return []
        today = today_ist()
        city = body.get("city") or {}
        coord = city.get("coord") or {}
        where = ", ".join(str(v) for v in (city.get("name"), coord.get("lat"), coord.get("lon")) if v not in (None, ""))
        dataset = "FarmFuture 5-day forecast (3-hourly, summed / averaged per day)" + (f" for {where}" if where else "")
        records = []
        for day in forecast_days(body, today):
            forecast_date = day.pop("forecast_date")
            records.append(self.observation(
                f"FF-FC-{link.plot.plot_code}-{forecast_date}-ISSUED-{today}", "weather_forecast", link.plot,
                {"source_name": "FarmFuture", "dataset": dataset[:200], "forecast_date": forecast_date.isoformat(),
                 "issued_on": today.isoformat(), **day}, _noon(forecast_date)))
        return records


# -- Workstream 3: pest & disease scouting --------------------------------------------------------------------------
SCOUTING_TOPICS = {"shothole": "shot_hole_borer", "wsb": "white_stem_borer", "stem_borer": "white_stem_borer",
                   "cbb": "coffee_berry_borer", "berry_borer": "coffee_berry_borer", "rust": "leaf_rust",
                   "coffee_leaf_rust": "leaf_rust", "mealybugs": "mealybug", "cercospora": "cercospora_leaf_spot",
                   "coffee_phoma": "phoma", "leafminer": "leaf_miner", "coffee_leaf_miner": "leaf_miner"}


def scouting_pest(topic: Any) -> tuple[str, str | None]:
    """FarmFuture scouting topic → (platform pest code, original name when it is 'other')."""
    key = str(topic or "").strip().lower().replace(" ", "_").replace("-", "_")
    code = SCOUTING_TOPICS.get(key, key)
    return (code, None) if code in PESTS and code != "other" else ("other", str(topic or "unspecified")[:120])


class FarmFuturePestScoutingAdapter(FarmFutureFarmAdapter):
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture pest & disease scouting (live API, read-only)", "module_code": "pest", "kind": "live_api",
        "default_source_code": "farmfuture_pest_scouting", "owner": OWNER,
        "description": "Field scouting recorded in the FarmFuture app on the linked farms – pest or disease, plants "
                       "inspected and plants with signs – as scouting / inspection records (incidence is calculated). "
                       "Phone numbers, notes, photos and GPS points of the scouting are not imported.",
        "config": {"past_days": 365}}

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        body = self.get_data(client, "/api/wsb/records", {"farm": link.farm_id}) or {}
        since = today_ist() - timedelta(days=_int(self.config, "past_days", 365, 1, 3650))
        records = []
        for item in (body.get("records") or []) if isinstance(body, dict) else []:
            day = _day(item.get("date"))
            if item.get("kind") != "inspection" or not item.get("id") or day is None or day < since:
                continue
            pest, other_name = scouting_pest(item.get("topic"))
            records.append(self.observation(f"FF-SCOUT-{item['id']}", "pest_inspection", link.plot, {
                "pest_or_disease": pest, "pest_other_name": other_name, "plants_inspected": item.get("inspected"),
                "plants_affected": item.get("affected"),
                "scouting_method": "FarmFuture app scouting (recorded on the farm)"}, _noon(day)))
        return records


# -- Workstream 5: satellite canopy condition per farm block, and the farm's site profile ----------------------------
def site_attributes(profile: dict[str, Any]) -> dict[str, Any] | None:
    """FarmFuture farm profile → site_characteristics attributes (no outline, points or device locations)."""
    summary = profile.get("summary") or {}
    terrain, soil = summary.get("terrain") or {}, summary.get("soil") or {}
    erosion = (profile.get("hydrology") or {}).get("erosion") or {}
    wetness = (profile.get("hydrology") or {}).get("wetness") or {}
    if not terrain and not soil:
        return None
    classes = erosion.get("class_pct") or {}
    severe = [v for k, v in classes.items() if k in ("severe", "very high") and isinstance(v, (int, float))]
    return {
        "source_name": "FarmFuture farm profile",
        "data_basis": "Terrain model (elevation, slope, aspect, drainage), soil survey layers averaged over the farm, "
                      "RUSLE soil-loss estimate; datasets not named by FarmFuture",
        "elevation_mean_m": _num(terrain.get("elevation_mean_m"), 1), "elevation_min_m": _num(terrain.get("elevation_min_m"), 1),
        "elevation_max_m": _num(terrain.get("elevation_max_m"), 1), "slope_mean_deg": _num(terrain.get("slope_mean_deg"), 2),
        "slope_max_deg": _num(terrain.get("slope_max_deg"), 2), "slope_class": terrain.get("slope_class"),
        "steep_share_pct": _num(terrain.get("steep_cell_pct"), 1), "aspect": terrain.get("aspect"),
        "drainage_class": str(terrain.get("drainage") or "").replace("_", " ") or None,
        "frost_risk": terrain.get("frost_risk"), "soil_texture": soil.get("texture"), "soil_ph": _num(soil.get("ph"), 2),
        "clay_pct": _num(soil.get("clay"), 1), "sand_pct": _num(soil.get("sand"), 1), "silt_pct": _num(soil.get("silt"), 1),
        "organic_carbon_pct": _num(soil.get("oc_pct"), 2), "bulk_density_g_cm3": _num(soil.get("bulk_density"), 2),
        "field_capacity_pct": _num(soil.get("field_capacity"), 1), "water_holding_mm_per_m": _num(soil.get("whc_mm_per_m"), 0),
        "water_holding_class": soil.get("whc_class"),
        "erosion_mean_t_ha_yr": _num(erosion.get("rusle_mean_t_ha_yr"), 1),
        "erosion_max_t_ha_yr": _num(erosion.get("rusle_max_t_ha_yr"), 1), "erosion_class": erosion.get("dominant_class"),
        "erosion_severe_share_pct": round(sum(severe), 1) if severe else None,
        "wetness_index_mean": _num(wetness.get("twi_mean"), 2), "saturation_prone_pct": _num(wetness.get("saturation_prone_pct"), 1),
        "profile_built_on": (_day(profile.get("built_at")) or today_ist()).isoformat()}


# -- Workstream 5: satellite canopy condition per farm block --------------------------------------------------------
class FarmFutureSatelliteAdapter(FarmFutureFarmAdapter):
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture satellite block greenness – NDVI / NDMI (live API, read-only)", "module_code": "suitability",
        "kind": "live_api", "default_source_code": "farmfuture_satellite", "owner": OWNER,
        "description": "Mean, lowest and highest NDVI, mean moisture index (NDMI) and declining cells for every block of "
                       "the linked FarmFuture farms, from FarmFuture's latest cloud-free Sentinel-2 pass; with "
                       "'site_profile', each farm's terrain, soil and erosion (RUSLE) profile as site characteristics.",
        "config": {"site_profile": True}}

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        records = self._site_profile(client, link) if self.config.get("site_profile", True) else []
        return records + self._zones(client, link)

    def _site_profile(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        profile = self.get_data(client, f"/api/FarmProfile/{link.farm_id}")
        attributes = site_attributes(profile) if isinstance(profile, dict) else None
        if attributes is None:
            return []
        built = attributes["profile_built_on"]
        return [self.observation(f"FF-SITE-{link.farm_id}-{built}", "site_characteristics", link.plot, attributes,
                                 _noon(date.fromisoformat(built)))]

    def _zones(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        summary = self.get_data(client, f"/api/Satellite/ZoneSummary/{link.farm_id}") or {}
        day = _day(summary.get("observedOn")) if isinstance(summary, dict) else None
        if day is None or summary.get("status") != "ready" or not summary.get("zones"):
            return []
        sources = self.get_data(client, "/api/wsb/sources", {"farm": link.farm_id}) or {}
        ref = next((s.get("sourceRef") for s in sources.get("sources") or []
                    if s.get("dataset") == "satellite" and s.get("sourceRef")), None) if isinstance(sources, dict) else None
        scene = str(ref).split("|")[-1] if ref else f"FarmFuture satellite pass {day}"
        records = []
        for zone in summary["zones"]:
            value = _num(zone.get("avgNdvi"))
            if value is None or not zone.get("zoneId"):
                continue
            records.append(self.observation(f"FF-SAT-{link.farm_id}-{zone['zoneId']}-{day}", "vegetation_index", link.plot, {
                "index": "ndvi", "index_value": value, "aggregation": "zone_mean", "zone_ref": str(zone["zoneId"]),
                "satellite": "Copernicus Sentinel-2 L2A (FarmFuture satellite service)", "scene_id": scene[:200],
                "index_min": _num(zone.get("minNdvi")), "index_max": _num(zone.get("maxNdvi")),
                "ndmi_mean": _num(zone.get("avgNdmi")), "cells_count": zone.get("cells"),
                "declining_cells": zone.get("decliningCells")}, _noon(day)))
        return records


# -- farm advisories (pest & disease warnings, irrigation, seasonal outlook) ----------------------------------------
# FarmFuture advisory code prefix → pest / disease it is about
ADVISORY_PESTS = {"rust": "leaf_rust", "leaf_rust": "leaf_rust", "stalk_rot": "stalk_rot", "wsb": "white_stem_borer",
                  "white_stem_borer": "white_stem_borer", "cbb": "coffee_berry_borer", "berry_borer": "coffee_berry_borer",
                  "shot_hole": "shot_hole_borer", "shothole": "shot_hole_borer", "mealybug": "mealybug",
                  "black_rot": "black_rot", "root_rot": "root_rot", "anthracnose": "anthracnose",
                  "berry_blotch": "berry_blotch", "leaf_miner": "leaf_miner", "cercospora": "cercospora_leaf_spot",
                  "phoma": "phoma"}


def advisory_module(code: str) -> tuple[str, str | None]:
    """FarmFuture advisory code → (platform origin module, pest/disease hazard or None)."""
    prefix = code.split(".", 1)[0]
    if prefix == "irrigation":
        return "water", None
    if prefix in ADVISORY_PESTS:
        return "pest", ADVISORY_PESTS[prefix]
    if code == "outlook.pests_diseases":
        return "pest", "pests_and_diseases"
    return "climate", None


def _message(item: dict[str, Any]) -> str:
    lines = [str(item.get("body") or item.get("title") or "").strip()]
    for section in item.get("sections") or []:
        texts = [str(i.get("text")).strip() for i in section.get("items") or [] if i.get("text")]
        if texts:
            lines.append(f"{section.get('heading') or ''}:\n" + "\n".join(f"- {t}" for t in texts))
    return "\n\n".join(line for line in lines if line)


class FarmFutureAdvisoryAdapter(FarmFutureFarmAdapter):
    payload_kind = "advisories"
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture farm advisories – pest, disease & irrigation (live API, read-only)", "module_code": "pest",
        "kind": "live_api", "default_source_code": "farmfuture_advisories", "owner": OWNER,
        "description": "Advisories the FarmFuture advisory engine issued to the linked farms: pest and disease warnings "
                       "(e.g. leaf rust, stalk rot), the monthly pests-and-diseases outlook and irrigation timing. Each "
                       "becomes an advisory for the farmer's area; an app delivery is recorded as sent only – it is not "
                       "counted as delivered or read.",
        "config": {"history_days": 0, "skip_kinds": ["data_request", "stage"], "record_app_delivery": True}}

    def farm_records(self, client: httpx.Client, link: FarmLink) -> list[dict[str, Any]]:
        latest = self.get_data(client, f"/api/Advisory/{link.farm_id}/latest")
        runs = [latest] if isinstance(latest, dict) else []
        history_days = _int(self.config, "history_days", 0, 0, 60)
        if history_days:
            history = self.get_data(client, f"/api/Advisory/{link.farm_id}/history", {"limit": history_days}) or {}
            seen = {r.get("runDate") for r in runs}
            for day in (history.get("days") or []) if isinstance(history, dict) else []:
                if day.get("runDate") and day["runDate"] not in seen:
                    run = self.get_data(client, f"/api/Advisory/{link.farm_id}", {"date": day["runDate"]})
                    if isinstance(run, dict):
                        runs.append(run)
                        seen.add(day["runDate"])
        skip = set(self.config.get("skip_kinds") or [])
        records = []
        for run in runs:
            issued = run.get("generatedAt")
            shown = set((run.get("delivery") or {}).get("shown") or [])
            for item in run.get("items") or []:
                if not item.get("id") or not item.get("code") or item.get("kind") in skip or not issued:
                    continue
                module, hazard = advisory_module(item["code"])
                window_end = _day((item.get("window") or {}).get("end"))
                deliveries = []
                if self.config.get("record_app_delivery", True) and item["id"] in shown:
                    deliveries.append({"source_delivery_id": f"FF-ADV-{item['id']}:app", "farmer_code": link.farmer_code,
                                       "channel": "app", "sent_at": issued})
                records.append({
                    "source_record_id": f"FF-ADV-{item['id']}", "programme_code": self.programme.code,
                    "origin_module": module, "advisory_type": "advisory", "hazard_type": hazard,
                    "title": str(item.get("title") or item["code"])[:255], "message": _message(item),
                    "issued_at": issued, "target_geo_code": link.geo_code, "deliveries": deliveries,
                    "valid_until": datetime.combine(window_end, time(23, 59, 59), IST).isoformat() if window_end else None})
        return records
