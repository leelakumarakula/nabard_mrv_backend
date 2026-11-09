"""Where an IoT device stands, whether that fits its plot, and whether it is reporting.

A device position is a precise location: callers show coordinates only to users with farmer.read_pii. The check against
the plot is a data-quality aid (a sensor far from its farm is almost always a wrong position in the source system), not
an MRV rule – a flagged device keeps its readings; it is only left off the map until the position is corrected.
"""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.models import Device

MAX_KM_FROM_PLOT = 2.0      # estates are rarely wider than a few hundred metres around their recorded point
REPORTING_WITHIN = timedelta(days=2)  # daily statistics: yesterday's day counts as current
LATE_WITHIN = timedelta(days=7)       # same window as the dashboard's "devices reporting" figure


def parse_position(lat: Any, lon: Any) -> tuple[Decimal, Decimal] | None:
    """A usable position, or None for missing, zero (unset) or out-of-range values."""
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float, Decimal)) \
            or not isinstance(lon, (int, float, Decimal)):
        return None
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0) or math.isnan(lat) or math.isnan(lon):
        return None
    return Decimal(str(round(lat, 6))), Decimal(str(round(lon, 6)))


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def location_check(device: Device) -> dict[str, Any]:
    """no_position | plot_not_located | far_from_plot | ok, with the distance to the plot's point when both are known."""
    if device.latitude is None or device.longitude is None:
        return {"location_check": "no_position", "distance_from_plot_km": None}
    plot = device.farm_plot
    if plot is None or plot.latitude is None or plot.longitude is None:
        return {"location_check": "plot_not_located", "distance_from_plot_km": None}
    km = distance_km(float(device.latitude), float(device.longitude), float(plot.latitude), float(plot.longitude))
    return {"location_check": "far_from_plot" if km > MAX_KM_FROM_PLOT else "ok", "distance_from_plot_km": round(km, 2)}


def reporting_state(status: str, last_reading_at: datetime | None, now: datetime | None = None) -> str:
    """reporting | late | silent | faulty | planned | removed – from the device status and its last stored reading."""
    if status in ("faulty", "planned", "removed"):
        return status
    if last_reading_at is None:
        return "silent"
    when = last_reading_at if last_reading_at.tzinfo else last_reading_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - when
    return "reporting" if age <= REPORTING_WITHIN else "late" if age <= LATE_WITHIN else "silent"


def location_fields(device: Device, precise: bool) -> dict[str, Any]:
    """Position (only when precise), its source and the plot check."""
    return {"latitude": float(device.latitude) if precise and device.latitude is not None else None,
            "longitude": float(device.longitude) if precise and device.longitude is not None else None,
            "location_source": device.location_source, "location_updated_at": device.location_updated_at,
            **location_check(device)}
