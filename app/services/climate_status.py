"""Rainfall status: observed station rain compared with the 30-year normal for the same days (Workstream 1).

Method (version below), reproducible from stored records:
  * Actual rain = the sum of the daily station rain records in the period. A day without a reading is missing data, not
    a dry day: it is not counted as 0 mm and the number of measured days is reported with the result.
  * Usual rain = the 30-year monthly average for each whole month in the period, plus a share of the month's average
    for a part month (days counted ÷ days in the month).
  * Share of usual rain = actual ÷ usual × 100, and the status is the first band whose minimum share it reaches (bands
    from the setting climate.rainfall_status_bands – provisional until the programme approves them).
  * A status band is given only when the share of days with a reading reaches climate.rainfall_status_min_coverage_pct;
    otherwise the status is "insufficient_data" (a silent station must not read as a drought).
This reproduces FarmFuture's rain report for the same station and normals (checked 6 Nov 2026: 1 Jan – 5 Nov 2026,
1,663.4 mm of 2,039.2 mm usual, 81.6 %, 277 of 309 days measured).
"""

import calendar
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services.record_types import MONTHS, RAINFALL_STATUSES
from app.services.settings import get_setting

METHOD_VERSION = "rainfall-status-1.1"


def status_bands(db: Session) -> list[dict[str, Any]]:
    """Configured bands, wettest first, restricted to the known status codes."""
    bands = [b for b in get_setting(db, "climate.rainfall_status_bands") or []
             if isinstance(b, dict) and b.get("code") in RAINFALL_STATUSES and isinstance(b.get("min_pct"), (int, float))]
    return sorted(bands, key=lambda b: -float(b["min_pct"]))


def classify(received_pct: float | None, bands: list[dict[str, Any]]) -> str | None:
    if received_pct is None:
        return None
    return next((b["code"] for b in bands if received_pct >= float(b["min_pct"])), None)


def min_coverage(db: Session) -> float:
    value = get_setting(db, "climate.rainfall_status_min_coverage_pct")
    return float(value) if isinstance(value, (int, float)) and 0 <= value <= 100 else 80.0


def _status(received_pct: float | None, data_days: int, days: int, bands: list[dict[str, Any]],
            coverage_min: float) -> tuple[str | None, float | None]:
    """(status, coverage %): no status without a measured day; 'insufficient_data' below the coverage minimum."""
    if not days:
        return None, None
    coverage = round(data_days / days * 100, 1)
    if not data_days:
        return None, coverage
    if coverage < coverage_min:
        return "insufficient_data", coverage
    return classify(received_pct, bands), coverage


def describe_bands(bands: list[dict[str, Any]]) -> str:
    return " · ".join(f"{b['code']} ≥{b['min_pct']}%" for b in bands)


def usual_rain(normals: dict[str, Any], start: date, end: date) -> float:
    """Normal rain for start..end (inclusive): whole months at their average, part months pro rata by days."""
    total, day = 0.0, start
    while day <= end:
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        month_end = min(date(day.year, day.month, days_in_month), end)
        counted = (month_end - day).days + 1
        total += float(normals[f"rain_{MONTHS[day.month - 1][0]}_mm"]) * counted / days_in_month
        day = month_end + timedelta(days=1)
    return total


def _period(rain: dict[date, float], start: date, end: date) -> tuple[float, int, int]:
    days = [d for d in rain if start <= d <= end]
    return sum(rain[d] for d in days), len(days), (end - start).days + 1


def _pct(actual: float, usual: float) -> float | None:
    return round(actual / usual * 100, 1) if usual > 0 else None


def month_statuses(rain: dict[date, float], normals: dict[str, Any], as_of: date,
                   bands: list[dict[str, Any]], coverage_min: float = 0.0) -> list[dict[str, Any]]:
    """One status per month from January of as_of's year to as_of's month (the last one counted up to as_of)."""
    results = []
    year_start = date(as_of.year, 1, 1)
    for month in range(1, as_of.month + 1):
        first = date(as_of.year, month, 1)
        last = min(date(as_of.year, month, calendar.monthrange(as_of.year, month)[1]), as_of)
        month_rain, month_data, month_days = _period(rain, first, last)
        ytd_rain, ytd_data, ytd_days = _period(rain, year_start, last)
        month_usual, ytd_usual = usual_rain(normals, first, last), usual_rain(normals, year_start, last)
        month_pct, ytd_pct = _pct(month_rain, month_usual), _pct(ytd_rain, ytd_usual)
        record_max = normals.get(f"rain_{MONTHS[month - 1][0]}_max_mm")
        month_status, month_coverage = _status(month_pct, month_data, month_days, bands, coverage_min)
        ytd_status, ytd_coverage = _status(ytd_pct, ytd_data, ytd_days, bands, coverage_min)
        results.append({
            "month": f"{as_of.year}-{month:02d}", "as_of_date": last.isoformat(),
            "month_rain_mm": round(month_rain, 1), "month_normal_mm": round(month_usual, 1),
            "month_received_pct": month_pct if month_data else None,
            "month_status": month_status, "month_coverage_pct": month_coverage,
            "month_data_days": month_data, "month_days": month_days,
            "month_above_record": bool(month_data and isinstance(record_max, (int, float)) and month_rain > record_max),
            "ytd_rain_mm": round(ytd_rain, 1), "ytd_normal_mm": round(ytd_usual, 1),
            "ytd_received_pct": ytd_pct if ytd_data else None,
            "ytd_difference_mm": round(ytd_rain - ytd_usual, 1) if ytd_data else None,
            "ytd_status": ytd_status, "ytd_coverage_pct": ytd_coverage,
            "ytd_data_days": ytd_data, "ytd_days": ytd_days,
            "method_version": METHOD_VERSION, "bands_used": describe_bands(bands), "min_coverage_pct": coverage_min})
    return results
