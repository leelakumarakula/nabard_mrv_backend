"""Platform calculations registered as pull sources: they read the platform's own stored records (no external call)
and write their results through the integration pipeline, so results get the same idempotency, validation, audit and
sync history as imported data, and can run on the same schedule.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import FarmPlot, FieldRecord, IntegrationSource
from app.services.climate_status import min_coverage, month_statuses, status_bands
from app.services.integration.base import PageResult
from app.services.integration.platform_adapters import IST, PlatformAdapter, _int, _noon, today_ist
from app.services.record_types import MONTHS


class RainfallStatusAdapter(PlatformAdapter):
    """Workstream 1: rainfall status per plot and month from stored station rain and stored 30-year normals."""

    page_size = 20
    catalogue: ClassVar[dict[str, Any]] = {
        "name": "Rainfall status vs 30-year normal (platform calculation)", "module_code": "climate",
        "kind": "platform_calculation", "default_source_code": "rainfall_status", "owner": "MRV platform",
        "description": "Compares the rain measured by each plot's weather station with the plot's 30-year normal for the "
                       "same days – per month and for the year to date – and records the share of usual rain, the "
                       "missing rain and the status band (extra … very much less rain). Uses validated records already in the "
                       "platform (station rain and 30-year normals, e.g. from the FarmFuture weather source); nothing is "
                       "fetched from outside. Days without a station reading are missing, not dry.",
        "config": {"schedule_daily_at": "07:30"}}

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        super().__init__(db, source)
        self.bands = status_bands(db)
        self.coverage_min = min_coverage(db)
        if not self.bands:
            raise AppError(422, "source_config", "No valid rainfall status bands are configured "
                                                 "(System settings → climate.rainfall_status_bands)")
        override = self.config.get("as_of_date")  # for re-running a past date; normally yesterday (whole days only)
        self.as_of = date.fromisoformat(override) if override else today_ist() - timedelta(days=1)

    def plots(self) -> list[FarmPlot]:
        if self._plots is None:
            ids = self.db.scalars(select(FieldRecord.farm_plot_id).where(
                FieldRecord.programme_id == self.programme.id, FieldRecord.record_type == "climate_normals",
                FieldRecord.validation_status == "valid", FieldRecord.farm_plot_id.is_not(None)).distinct())
            self._plots = sorted((self.db.get(FarmPlot, i) for i in ids), key=lambda p: p.plot_code)
            self._plots = [p for p in self._plots if not p.is_archived][:_int(self.config, "max_plots", 500, 1, 5000)]
            if not self._plots:
                raise AppError(422, "no_climate_normals",
                               f"No plot in programme {self.programme.code} has 30-year climate normals yet. Run the "
                               "FarmFuture weather source (setting climate_normals) or record normals for the plots first.")
        return self._plots

    def fetch_page(self, cursor: str | None) -> PageResult:
        start = int(cursor or 0)
        plots = self.plots()
        records = [r for plot in plots[start:start + self.page_size] for r in self.plot_status(plot)]
        end = start + self.page_size
        return PageResult(records, str(end) if end < len(plots) else None)

    def _normals(self, plot: FarmPlot) -> FieldRecord | None:
        return self.db.scalars(select(FieldRecord).where(
            FieldRecord.programme_id == self.programme.id, FieldRecord.farm_plot_id == plot.id,
            FieldRecord.record_type == "climate_normals", FieldRecord.validation_status == "valid")
            .order_by(FieldRecord.observed_at.desc(), FieldRecord.updated_at.desc())).first()

    def station_rain(self, plot: FarmPlot) -> tuple[str | None, dict[date, float]]:
        """Daily rain of the plot's station with the most measured days this year (one station, never mixed)."""
        first = date(self.as_of.year, 1, 1)
        rows = self.db.scalars(select(FieldRecord).where(
            FieldRecord.programme_id == self.programme.id, FieldRecord.farm_plot_id == plot.id,
            FieldRecord.record_type == "climate_observation", FieldRecord.validation_status == "valid",
            FieldRecord.observed_at >= datetime.combine(first, time(0), IST),
            FieldRecord.observed_at < datetime.combine(self.as_of + timedelta(days=1), time(0), IST)))
        stations: dict[str, dict[date, float]] = defaultdict(dict)
        for record in rows:
            rain = (record.attributes or {}).get("rainfall_mm")
            day = record.observed_at.astimezone(IST).date() if record.observed_at else None
            if isinstance(rain, (int, float)) and not isinstance(rain, bool) and day and first <= day <= self.as_of:
                key = str((record.attributes or {}).get("station_ref") or record.device_id or record.source_system_id)
                stations[key][day] = float(rain)
        if not stations:
            return None, {}
        station = min(stations, key=lambda k: (-len(stations[k]), k))
        return station, stations[station]

    def plot_status(self, plot: FarmPlot) -> list[dict[str, Any]]:
        normals = self._normals(plot)
        station, rain = self.station_rain(plot)
        if normals is None or not rain:
            return []  # no status without measured rain – never a status computed from no data
        n = normals.attributes or {}
        if any(not isinstance(n.get(f"rain_{key}_mm"), (int, float)) for key, _ in MONTHS):
            return []  # incomplete normals – no status rather than a wrong one
        reference = normals.source_record_id or str(normals.id)
        dataset = f"{n.get('rainfall_dataset')} {n.get('period_start_year')}–{n.get('period_end_year')}"
        return [self.observation(
            f"RAIN-{plot.plot_code}-{status['month']}", "rainfall_status", plot,
            {**status, "station_ref": station, "normals_ref": reference[:120], "normals_dataset": dataset[:200]},
            _noon(date.fromisoformat(status["as_of_date"])))
            for status in month_statuses(rain, n, self.as_of, self.bands, self.coverage_min)]
