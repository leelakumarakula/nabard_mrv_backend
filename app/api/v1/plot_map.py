"""Map view: located plots with the latest weather, satellite, risk and sensor values and practice progress."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Farm, Farmer, FarmPlot, FieldRecord, PracticeRecord, ProgrammeParticipation
from app.services.integration.farmfuture import farm_boundary
from app.services.integration.platform_adapters import today_ist
from app.services.scope import apply_scope

router = APIRouter(prefix="/map", tags=["map"])

MAX_PLOTS = 2000
LATEST_TYPES = ("weather_estimate", "weather_forecast", "vegetation_index", "pest_risk_estimate",
                "climate_risk_assessment", "sensor_observation", "rainfall_status", "climate_observation")
RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "very_high": 3}


def _coordinate(value: Decimal, precise: bool) -> float:
    # Without farmer.read_pii the point is rounded to about 1 km so a farm cannot be pinpointed.
    return float(value) if precise else round(float(value), 2)


def _summary(rec: FieldRecord) -> dict[str, Any]:
    return {"record_id": rec.id, "observed_at": rec.observed_at, "is_demo": rec.is_demo, **(rec.attributes or {})}


@router.get("/plots")
def map_plots(programme_id: uuid.UUID | None = None,
              current: CurrentUser = Depends(require("farmer.read", "module.read")),
              db: Session = Depends(get_db)) -> dict[str, Any]:
    precise = current.has("farmer.read_pii")
    stmt = (select(FarmPlot, Farmer).join(Farm, Farm.id == FarmPlot.farm_id).join(Farmer, Farmer.id == Farm.farmer_id)
            .where(FarmPlot.is_archived == false(), Farm.is_archived == false(), Farmer.is_archived == false(),
                   FarmPlot.latitude.is_not(None), FarmPlot.longitude.is_not(None))
            .order_by(FarmPlot.plot_code))
    if programme_id:
        stmt = stmt.where(Farmer.id.in_(select(ProgrammeParticipation.farmer_id).where(
            ProgrammeParticipation.programme_id == programme_id, ProgrammeParticipation.status == "enrolled")))
    stmt = apply_scope(stmt, current, Farmer.geo_unit_id, Farmer.primary_fpo_id)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.limit(MAX_PLOTS)).unique().all()
    plot_ids = [p.id for p, _ in rows] or [uuid.UUID(int=0)]
    farmer_ids = list({f.id for _, f in rows}) or [uuid.UUID(int=0)]

    # Latest values per plot. Forecasts are kept per forecast date (latest issue wins) for the 7-day outlook.
    since = datetime.now(UTC) - timedelta(days=120)
    records = db.scalars(select(FieldRecord).where(
        FieldRecord.farm_plot_id.in_(plot_ids), FieldRecord.record_type.in_(LATEST_TYPES),
        FieldRecord.validation_status != "rejected", FieldRecord.observed_at >= since,
        *([FieldRecord.programme_id == programme_id] if programme_id else []))
        .order_by(FieldRecord.observed_at.desc(), FieldRecord.created_at.desc())).unique().all()
    latest: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
    forecasts: dict[uuid.UUID, dict[str, FieldRecord]] = defaultdict(dict)
    today = today_ist()
    for rec in records:
        attrs = rec.attributes or {}
        bucket = latest[rec.farm_plot_id]
        if rec.record_type == "weather_forecast":
            day = attrs.get("forecast_date", "")
            current_best = forecasts[rec.farm_plot_id].get(day)
            if day >= today.isoformat() and (current_best is None or
                                             attrs.get("issued_on", "") > (current_best.attributes or {}).get("issued_on", "")):
                forecasts[rec.farm_plot_id][day] = rec
            continue
        if rec.record_type in ("pest_risk_estimate", "climate_risk_assessment") and attrs.get("valid_to", "") < today.isoformat():
            continue  # expired risk estimates are history, not the current picture
        key = {"weather_estimate": "weather", "vegetation_index": "ndvi", "pest_risk_estimate": "pest_risk",
               "climate_risk_assessment": "climate_risk", "rainfall_status": "rainfall"}.get(rec.record_type)
        if rec.record_type == "sensor_observation":
            key = "soil_moisture" if attrs.get("parameter") == "soil_moisture" else None
        if rec.record_type == "climate_observation":  # the latest full station day (rain-only history days are skipped)
            key = "station" if attrs.get("temp_max_c") is not None else None
        if key and key not in bucket:
            bucket[key] = _summary(rec)
        elif key in ("pest_risk", "climate_risk") and RISK_ORDER.get(attrs.get("risk_level"), -1) > \
                RISK_ORDER.get(bucket[key].get("risk_level"), -1):
            bucket[key] = _summary(rec)  # show the highest current risk
    for plot_id, by_day in forecasts.items():
        week = [by_day[d] for d in sorted(by_day)[:7]]
        rain = [r.attributes.get("rainfall_mm") for r in week if r.attributes.get("rainfall_mm") is not None]
        highs = [r.attributes.get("temp_max_c") for r in week if r.attributes.get("temp_max_c") is not None]
        latest[plot_id]["forecast"] = {"days": len(week), "rainfall_mm": round(sum(rain), 1) if rain else None,
                                       "max_temp_c": max(highs) if highs else None,
                                       "source_name": week[0].attributes.get("source_name") if week else None,
                                       "is_demo": any(r.is_demo for r in week)}

    practice_stmt = select(PracticeRecord.farmer_id, PracticeRecord.status, PracticeRecord.verification_status,
                           func.count()).where(PracticeRecord.farmer_id.in_(farmer_ids))
    if programme_id:
        practice_stmt = practice_stmt.where(PracticeRecord.programme_id == programme_id)
    practices: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for farmer_id, status, verification, count in db.execute(practice_stmt.group_by(
            PracticeRecord.farmer_id, PracticeRecord.status, PracticeRecord.verification_status)):
        practices[farmer_id][status] += count
        if verification == "verified":
            practices[farmer_id]["verified"] += count

    plots = []
    for plot, farmer in rows:
        counts = practices.get(farmer.id, {})
        stage = next((s for s in ("verified", "observed", "reported", "recommended") if counts.get(s)), "none")
        plots.append({
            "id": plot.id, "plot_code": plot.plot_code, "farmer_id": farmer.id, "farmer_label": farmer.label,
            "area_label": farmer.geo_unit.label if farmer.geo_unit else None, "village": farmer.village,
            "latitude": _coordinate(plot.latitude, precise), "longitude": _coordinate(plot.longitude, precise),
            "area": plot.area, "area_unit": plot.area_unit, "crop": plot.crop, "plantation_stage": plot.plantation_stage,
            "is_demo": plot.is_demo, "practice_stage": stage, "practices": dict(counts), **latest.get(plot.id, {}),
        })
    return {"precise_locations": precise, "total": total, "truncated": total > len(plots), "plots": plots}


@router.get("/plots/{plot_id}/boundary")
def plot_boundary(plot_id: uuid.UUID,
                  current: CurrentUser = Depends(require("farmer.read", "module.read", "farmer.read_pii")),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    """Real farm outline for one plot, read live from FarmFuture. An exact outline is a precise location, so it needs
    farmer.read_pii like exact map points; it is not stored on the platform."""
    row = db.execute(select(FarmPlot.id, Farmer.geo_unit_id, Farmer.primary_fpo_id)
                     .join(Farm, Farm.id == FarmPlot.farm_id).join(Farmer, Farmer.id == Farm.farmer_id)
                     .where(FarmPlot.id == plot_id, FarmPlot.is_archived == false())).first()
    if row is None:
        raise AppError(404, "not_found", "Plot not found")
    current.ensure_access(row.geo_unit_id, row.primary_fpo_id)
    return farm_boundary(db, plot_id)
