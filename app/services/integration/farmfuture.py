"""FarmFuture Water Balance Engine – live, read-only pull for Workstream 2 (Soil & water), and farm import.

Only HTTP GET is ever sent to the FarmFuture API:
  GET /v1/farmer/farms                                                  farms with a water-engine plan
  GET /v1/farmer/farms/{farm_id}/irrigation-plan?day=&horizon_days=     the engine's daily root-zone water account
  GET /api/Estate/GetUserEstatesAndFarmsList                            estates and farms (farm import)
  GET /api/FarmProfile/{farm_id}                                        farm location and crop (farm import); farm
                                                                        outline for the map (shown live, never stored)

The token comes from settings (FARMFUTURE_API_TOKEN in the environment / .env) and is never stored or logged.
Each FarmFuture farm is fetched only when a reviewed identity map links it to a platform plot (entity farm_plot,
source id = FarmFuture farm_id). The Swagger publishes no response schemas, so the field mapping below follows live
responses from engine model 1.3.0; a changed shape fails validation instead of being guessed.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import httpx
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.models import Farm, Farmer, FarmPlot, GeoUnit, IntegrationSource, Organisation, SourceRecordMap
from app.services import audit, crud
from app.services.integration import platform_adapters
from app.services.integration.base import PageResult, TransientSourceError
from app.services.integration.platform_adapters import USER_AGENT, PlatformAdapter, _int, _noon, today_ist

ENGINE_NAME = "FarmFuture Water Balance Engine"
NEEDS_IRRIGATION = {"IRRIGATE_NOW", "ROUND_DUE", "IRRIGATE_BY"}


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def plan_attributes(plan: dict[str, Any]) -> dict[str, Any]:
    """Map one irrigation-plan response onto water_balance_output attributes (None values are dropped later)."""
    status = _get(plan, "headline", "status")
    water_needed = plan.get("water_needed") or {}
    # The engine gives a depth only when it recommends irrigation; "no irrigation needed" is a recommendation of 0 mm.
    recommended = water_needed.get("depth_mm") if water_needed else (0.0 if status == "NO_IRRIGATION" else None)
    first_dry = next((d.get("date") for d in plan.get("days") or [] if d.get("needs_water")), None)
    sensor = _get(plan, "today", "sensor") or {}
    return {
        "engine_name": ENGINE_NAME, "engine_version": plan.get("model_version"),
        "source_farm_ref": plan.get("farm_id"), "irrigation_status": status, "advice": _get(plan, "headline", "title"),
        "et0_mm": _get(plan, "water_use", "et0_mm"), "kc": _get(plan, "water_use", "kc"),
        "crop_water_requirement_mm": _get(plan, "water_use", "etc_mm"),
        "rain_mm": _get(plan, "water_use", "rain_mm"), "effective_rain_mm": _get(plan, "water_use", "effective_rain_mm"),
        "irrigation_applied_mm": _get(plan, "water_use", "irrigation_applied_mm"),
        "irrigation_effective_mm": _get(plan, "water_use", "irrigation_effective_mm"),
        "runoff_mm": _get(plan, "water_use", "runoff_mm"), "drained_mm": _get(plan, "water_use", "drained_mm"),
        "net_change_mm": _get(plan, "water_use", "net_change_mm"),
        "capacity_mm": _get(plan, "today", "capacity_mm"), "usable_water_pct": _get(plan, "today", "usable_water_pct"),
        "stress_mark_pct": _get(plan, "today", "stress_mark_pct"), "reserve_mm": _get(plan, "today", "reserve_mm"),
        "reserve_days": _get(plan, "today", "reserve_days"), "recommended_irrigation_mm": recommended,
        "first_needs_water_date": first_dry, "forecast_horizon_days": plan.get("horizon_days"),
        "water_needed_m3": water_needed.get("total_m3"),
        "sensor_reading_pct": sensor.get("reading_pct") if sensor.get("available") else None,
        "model_soil_water_pct": sensor.get("model_pct") if sensor.get("available") else None,
        "sensor_agrees": sensor.get("agrees") if sensor.get("available") else None,
        "growth_stage": _get(plan, "stage", "label"), "growth_stage_estimated": _get(plan, "stage", "source", "estimated"),
        "data_mode": _get(plan, "data_quality", "mode"), "confidence": _get(plan, "confidence", "level"),
        "confidence_score": _get(plan, "confidence", "score"),
    }


class FarmFutureApi:
    """Read-only access to the FarmFuture API shared by every FarmFuture adapter: token from settings, GET only."""

    config: dict[str, Any]

    def _init_api(self) -> None:
        settings = get_settings()
        if not settings.farmfuture_api_token:
            raise AppError(422, "source_not_configured", "FARMFUTURE_API_TOKEN is not set in the server environment "
                                                         "(.env). Add the token and restart the API.")
        self.base_url = settings.farmfuture_api_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.farmfuture_api_token}", "Accept": "application/json",
                        "User-Agent": USER_AGENT}

    def client(self) -> httpx.Client:
        return httpx.Client(transport=platform_adapters.HTTP_TRANSPORT, headers=self.headers,
                            timeout=float(_int(self.config, "timeout_seconds", 60, 5, 120)))

    def _request(self, client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = client.get(self.base_url + path, params=params)  # GET only – FarmFuture is never written to
        except httpx.TransportError as exc:
            raise TransientSourceError(f"FarmFuture: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientSourceError(f"FarmFuture returned HTTP {response.status_code}")
        if response.status_code in (401, 403):
            raise AppError(502, "source_auth", "FarmFuture refused the token (HTTP "
                                               f"{response.status_code}) – it may have expired; replace FARMFUTURE_API_TOKEN")
        return response


class FarmFutureWaterEngineAdapter(FarmFutureApi, PlatformAdapter):
    uses_platform_codes = False  # FarmFuture farm ids resolve to plots through reviewed identity maps
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "FarmFuture Water Balance Engine (live API, read-only)", "module_code": "water", "kind": "live_api",
        "default_source_code": "farmfuture_water_engine", "owner": "FarmFuture (Varsapradaya)",
        "description": "Daily root-zone water account and irrigation plan per farm: ET0, Kc, crop water use, effective "
                       "rain and irrigation, water left, days of reserve, irrigation status and amount, sensor check and "
                       "confidence. Read-only GET calls with the token in FARMFUTURE_API_TOKEN. Map each FarmFuture "
                       "farm to a platform plot first (Identity maps).",
        "config": {"past_days": 3, "horizon_days": 7}}

    def __init__(self, db, source: IntegrationSource) -> None:
        super().__init__(db, source)
        self._init_api()
        self._farms: list[str] | None = None

    def mapped_farms(self, client: httpx.Client) -> list[str]:
        """FarmFuture farm ids the token can see that are mapped to a platform plot, in a stable order."""
        if self._farms is None:
            response = self._request(client, "/v1/farmer/farms")
            if response.status_code >= 400:
                raise AppError(502, "source_error", f"FarmFuture farm list failed (HTTP {response.status_code})")
            visible = [str(f.get("farm_id")) for f in response.json() or [] if f.get("farm_id")]
            mapped = set(self.db.scalars(select(SourceRecordMap.source_record_id).where(
                SourceRecordMap.source_id == self.source.id, SourceRecordMap.entity_type == "farm_plot")))
            self._farms = sorted(f for f in visible if f in mapped)
            if not self._farms:
                raise AppError(422, "no_mapped_farms",
                               f"None of the {len(visible)} FarmFuture farms visible to this token is mapped to a "
                               "platform plot. Add identity maps (entity farm_plot, source id = FarmFuture farm id) for "
                               "the programme's farms: " + ", ".join(visible[:20]))
        return self._farms

    def fetch_page(self, cursor: str | None) -> PageResult:
        start = int(cursor or 0)
        today = today_ist()
        days = [today - timedelta(days=n) for n in range(_int(self.config, "past_days", 3, 0, 31), -1, -1)]
        horizon = _int(self.config, "horizon_days", 7, 1, 14)
        with self.client() as client:
            farms = self.mapped_farms(client)
            records = []
            for farm_id in farms[start:start + self.page_size]:
                for day in days:
                    response = self._request(client, f"/v1/farmer/farms/{farm_id}/irrigation-plan",
                                             {"day": day.isoformat(), "horizon_days": horizon})
                    if response.status_code == 422:  # "No irrigation plan has been stored for this farm on <day>"
                        continue
                    if response.status_code >= 400:
                        raise AppError(502, "source_error", f"FarmFuture irrigation plan for farm {farm_id} on {day} "
                                                            f"failed (HTTP {response.status_code}): {response.text[:200]}")
                    plan = response.json()
                    as_of = plan.get("as_of_date") or day.isoformat()
                    records.append({
                        "source_record_id": f"FF-WBE-{farm_id}-{as_of}", "programme_code": self.programme.code,
                        "module_code": "water", "record_type": "water_balance_output",
                        "farm_plot_external_id": farm_id, "observed_at": _noon(date.fromisoformat(as_of)),
                        "attributes": {k: v for k, v in plan_attributes(plan).items() if v is not None}})
        end = start + self.page_size
        return PageResult(records, str(end) if end < len(farms) else None)


# -- farm import -----------------------------------------------------------------------------------------------------
CROPS = {"arabica", "robusta", "mixed"}


def _code(prefix: str, external_id: str) -> str:
    return f"{prefix}-{external_id.replace('-', '')[:8].upper()}"


def _point(center: Any) -> tuple[Decimal | None, Decimal | None]:
    """FarmProfile 'center' is GeoJSON order [longitude, latitude], like its boundary rings."""
    if isinstance(center, list) and len(center) == 2 and all(isinstance(v, (int, float)) for v in center):
        lon, lat = center
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return Decimal(str(round(lat, 6))), Decimal(str(round(lon, 6)))
    return None, None


def import_farms(db: Session, current: CurrentUser, source: IntegrationSource, geo_unit_id: uuid.UUID,
                 fpo_id: uuid.UUID | None, test_label: bool) -> dict[str, Any]:
    """Create platform farmers, farms and plots for the FarmFuture estates and farms the token can see (read-only).

    One FarmFuture estate becomes one farmer (and household); each FarmFuture farm becomes a farm with one plot, linked
    by reviewed identity maps (farmer = estate id, farm_plot = farm id), so the water-engine pull needs no manual
    mapping. FarmFuture holds no consent, enrolment, household, gender or programme geography: imported farmers get
    consent 'not_recorded', no enrolment and the geography/FPO chosen by the operator, so they are not counted as
    beneficiaries until programme staff record those facts. Re-running only adds estates and farms not yet imported.
    """
    if source.adapter_code != "farmfuture_water_engine":
        raise AppError(422, "wrong_adapter", "Farm import is available only for the FarmFuture Water Balance Engine source")
    if db.get(GeoUnit, geo_unit_id) is None:
        raise AppError(422, "invalid_reference", "Geography unit does not exist")
    if fpo_id:
        fpo = db.get(Organisation, fpo_id)
        if fpo is None or fpo.org_type != "fpo":
            raise AppError(422, "invalid_reference", "Primary FPO must be an existing FPO organisation")
    current.ensure_access(geo_unit_id, fpo_id)
    adapter = FarmFutureWaterEngineAdapter(db, source)
    mapped = {(m.entity_type, m.source_record_id): m.internal_record_id for m in db.scalars(
        select(SourceRecordMap).where(SourceRecordMap.source_id == source.id,
                                      SourceRecordMap.entity_type.in_(["farmer", "farm_plot"])))}
    label = "TEST – " if test_label else ""
    note = "Created by FarmFuture farm import" + (" (TEST – not a programme beneficiary)" if test_label else "")
    summary: dict[str, Any] = {"estates": 0, "farmers_created": 0, "farms": 0, "farms_created": 0,
                               "already_imported": 0, "with_location": 0, "with_area": 0, "skipped": []}

    def link(entity_type: str, external_id: str, internal_id: uuid.UUID) -> None:
        db.add(SourceRecordMap(source_id=source.id, entity_type=entity_type, source_record_id=external_id,
                               internal_record_id=internal_id, mapped_by_id=current.id, review_note=note))
        mapped[(entity_type, external_id)] = internal_id

    try:
        with adapter.client() as client:
            response = adapter._request(client, "/api/Estate/GetUserEstatesAndFarmsList")
            body = response.json() if response.status_code < 400 else None
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise AppError(502, "source_error", f"FarmFuture estate list failed (HTTP {response.status_code})")
            for estate in body["data"]:
                estate_id, estate_name = str(estate.get("estateId") or ""), (estate.get("estateName") or "").strip()
                if not estate_id:
                    continue
                summary["estates"] += 1
                farmer_id = mapped.get(("farmer", estate_id))
                if farmer_id is None:
                    code = _code("FF", estate_id)
                    if db.scalar(select(Farmer.id).where(Farmer.farmer_code == code)):
                        summary["skipped"].append(f"Estate {estate_name or estate_id}: farmer code {code} already used")
                        continue
                    farmer = crud.create(db, current, Farmer, {
                        "farmer_code": code, "household_code": _code("FF-HH", estate_id),
                        "full_name": f"{label}{estate_name or 'FarmFuture estate'}"[:200], "geo_unit_id": geo_unit_id,
                        "primary_fpo_id": fpo_id, "consent_status": "not_recorded"}, "farmer",
                        summary=f"Imported FarmFuture estate {estate_id}")
                    link("farmer", estate_id, farmer.id)
                    farmer_id = farmer.id
                    summary["farmers_created"] += 1
                for farm in estate.get("farms") or []:
                    farm_id, farm_name = str(farm.get("farmId") or ""), (farm.get("farmName") or "").strip()
                    if not farm_id:
                        continue
                    summary["farms"] += 1
                    if ("farm_plot", farm_id) in mapped:
                        summary["already_imported"] += 1
                        continue
                    farm_code, plot_code = _code("FF", farm_id), _code("FF", farm_id) + "-P1"
                    if db.scalar(select(Farm.id).where(Farm.farm_code == farm_code)) or \
                            db.scalar(select(FarmPlot.id).where(FarmPlot.plot_code == plot_code)):
                        summary["skipped"].append(f"Farm {farm_name or farm_id}: code {farm_code} already used")
                        continue
                    profile = adapter._request(client, f"/api/FarmProfile/{farm_id}")
                    profile = profile.json() if profile.status_code == 200 else {}
                    lat, lon = _point(profile.get("center"))
                    crop = str(profile.get("crop_type") or "").lower()
                    plan = adapter._request(client, f"/v1/farmer/farms/{farm_id}/irrigation-plan")
                    area = _get(plan.json(), "farm", "area_ha") if plan.status_code == 200 else None
                    area = Decimal(str(round(area, 4))) if isinstance(area, (int, float)) and area > 0 else None
                    new_farm = crud.create(db, current, Farm, {
                        "farmer_id": farmer_id, "farm_code": farm_code, "name": farm_name[:200] or None,
                        "geo_unit_id": geo_unit_id, "latitude": lat, "longitude": lon, "total_area": area,
                        "area_unit": "ha", "area_source": "records" if area else None}, "farm",
                        summary=f"Imported FarmFuture farm {farm_id}")
                    plot = crud.create(db, current, FarmPlot, {
                        "farm_id": new_farm.id, "plot_code": plot_code, "area": area, "area_unit": "ha",
                        "area_source": "records" if area else None, "latitude": lat, "longitude": lon,
                        "crop": crop if crop in CROPS else None}, "farm_plot",
                        summary=f"Imported FarmFuture farm {farm_id}")
                    link("farm_plot", farm_id, plot.id)
                    summary["farms_created"] += 1
                    summary["with_location"] += lat is not None
                    summary["with_area"] += area is not None
    except TransientSourceError as exc:
        db.rollback()
        raise AppError(502, "source_unavailable", f"FarmFuture is not reachable right now ({exc}); try again") from exc
    audit.record(db, current, "import_farms", "integration_source", source.id,
                 after={k: v for k, v in summary.items() if k != "skipped"})
    db.commit()
    return summary


def _ring(value: Any) -> list[list[float]] | None:
    """A FarmFuture ring ([[lon, lat], ...], GeoJSON order) as a closed GeoJSON ring, or None if it is not a polygon."""
    if not isinstance(value, list):
        return None
    points: list[list[float]] = []
    for point in value:
        if not (isinstance(point, (list, tuple)) and len(point) >= 2 and all(isinstance(c, (int, float)) for c in point[:2])):
            return None
        lon, lat = float(point[0]), float(point[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        points.append([lon, lat])
    if len(points) < 3:
        return None
    return points if points[0] == points[-1] else [*points, points[0]]


def farm_boundary(db: Session, plot_id: uuid.UUID) -> dict[str, Any]:
    """Outline of the FarmFuture farm linked to a platform plot, fetched live for display and never stored.

    Returns {"available": False, "reason": ...} when the plot is not a FarmFuture farm, the token is not set, or the
    farm has no drawn boundary – the map then keeps showing the GPS point only.
    """
    row = db.execute(
        select(SourceRecordMap.source_record_id, IntegrationSource)
        .join(IntegrationSource, IntegrationSource.id == SourceRecordMap.source_id)
        .where(SourceRecordMap.entity_type == "farm_plot", SourceRecordMap.internal_record_id == plot_id,
               IntegrationSource.adapter_code == "farmfuture_water_engine", IntegrationSource.enabled == true())
        .order_by(SourceRecordMap.first_seen_at)).first()  # farm import (and so farm links) live on the water source
    if row is None:
        return {"available": False, "reason": "not_linked"}
    farm_id, source = row
    try:
        adapter = FarmFutureWaterEngineAdapter(db, source)
    except AppError:
        return {"available": False, "reason": "not_configured"}
    try:
        with adapter.client() as client:
            response = adapter._request(client, f"/api/FarmProfile/{farm_id}")
    except TransientSourceError as exc:
        raise AppError(502, "source_unavailable", f"FarmFuture is not reachable right now ({exc}); try again") from exc
    if response.status_code == 404:
        return {"available": False, "reason": "no_profile"}
    if response.status_code >= 400:
        raise AppError(502, "source_error", f"FarmFuture farm profile failed (HTTP {response.status_code})")
    profile = response.json() or {}
    outline = _ring(profile.get("ring"))
    if outline is None:
        return {"available": False, "reason": "no_boundary"}
    blocks = [ring for zone in profile.get("zone_profiles") or [] if (ring := _ring(zone.get("ring"))) and ring != outline]
    terrain = _get(profile, "summary", "terrain") or {}
    return {
        "available": True, "reason": None, "source": "FarmFuture", "boundary": outline, "blocks": blocks,
        "terrain": {key: terrain.get(key) for key in
                    ("elevation_mean_m", "elevation_min_m", "elevation_max_m", "slope_mean_deg", "slope_class", "aspect")},
    }
