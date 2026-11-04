"""Pull adapter registry.

* Mock adapters (mock_*) and sample adapters (sample_*) generate synthetic data and must be flagged is_mock.
* Public-data adapters (Open-Meteo, NASA POWER, Sentinel-2) fetch real data from free services that need no key.
* FarmFuture engines stay ``unconfigured_rest`` until their API documentation is supplied.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Device, FarmPlot, IntegrationSource, Programme
from app.services.integration.base import PageResult, TransientSourceError, with_retry
from app.services.integration.platform_adapters import (
    NasaPowerAdapter,
    OpenMeteoAdapter,
    SampleClimateEngineAdapter,
    SampleIotGatewayAdapter,
    SamplePestEngineAdapter,
    SampleWaterEngineAdapter,
    Sentinel2NdviAdapter,
)

__all__ = ["ADAPTERS", "PageResult", "TransientSourceError", "catalogue", "get_adapter", "requires_mock_flag",
           "uses_platform_codes", "with_retry"]


class MockWaterMeterAdapter:
    """DEMO/MOCK source: a water-meter system that uses its own field names, units and pagination.

    Field names (id, plotRef, qty, uom, readingTime, kind, prog) differ from the canonical model on purpose, so the
    configured field mappings are exercised. Page 2 fails once to exercise retry. Includes one duplicate and one
    record with an unknown plot.
    """

    payload_kind = "measurements"
    PAGES = 3

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        self.db = db
        self.source = source
        self._failed_once: set[str] = set()
        # The mock reports against the configured (or most recent) programme so it never invents a programme code.
        self.programme_code = (source.config or {}).get("programme_code") or db.scalar(
            select(Programme.code).order_by(Programme.proposal_date.desc(), Programme.created_at.desc()))

    def fetch_page(self, cursor: str | None) -> PageResult:
        page = int(cursor or "1")
        if page == 2 and "2" not in self._failed_once:
            self._failed_once.add("2")
            raise TransientSourceError("Mock source timeout on page 2 (simulated)")
        plots = list(self.db.scalars(select(FarmPlot.plot_code).where(FarmPlot.is_demo == true())
                                     .order_by(FarmPlot.plot_code).limit(6)))
        if not plots:
            return PageResult([], None)
        base = datetime(2026, 10, 1, 7, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        records = []
        for i in range(4):
            n = (page - 1) * 4 + i
            records.append({
                "id": f"MWM-{1000 + n}", "plotRef": plots[n % len(plots)], "qty": 40000 + 2500 * n, "uom": "L",
                "readingTime": (base + timedelta(days=n)).isoformat(), "kind": "IRRIGATION_WATER_VOLUME",
                "prog": self.programme_code, "activity": "WATER_MANAGEMENT", "how": "meter_reading",
            })
        if page == 2:
            records.append(dict(records[0]))  # duplicate re-sent by the source
        if page == 3:
            records.append({**records[0], "id": "MWM-9999", "plotRef": "PLOT-UNKNOWN-42"})
        return PageResult(records, str(page + 1) if page < self.PAGES else None)


class MockIotGatewayAdapter:
    """DEMO/MOCK device gateway for SoilSync, Moisture Probe, MicroClime and RainSync readings.

    Emits gateway-style payloads (deviceId, ts, param, val, unit, q) for registered demo devices, only for parameters
    the device type supports, plus one unknown device (quarantined) and one unsupported parameter (validation rejects).
    Real device protocols and payloads are pending the final equipment schedule.
    """

    payload_kind = "observations"
    PARAMS = {"soilsync_7in1": [("VWC", 24.0, "%VWC"), ("PH", 5.6, "pH"), ("EC", 0.4, "dS/m")],
              "moisture_probe": [("VWC", 21.0, "%VWC")],
              "microclime": [("TAIR", 27.5, "degC"), ("RH", 78.0, "%RH")],
              "rainsync": [("RAIN", 6.5, "mm")]}

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        self.db = db

    def fetch_page(self, cursor: str | None) -> PageResult:
        devices = list(self.db.scalars(select(Device).where(Device.is_demo == true(), Device.status == "active")
                                       .order_by(Device.device_code)))
        base = datetime(2026, 10, 20, 6, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        records = []
        for d_index, device in enumerate(devices):
            for p_index, (param, value, unit) in enumerate(self.PARAMS.get(device.device_type, [])):
                records.append({"deviceId": device.device_code, "ts": (base + timedelta(hours=d_index)).isoformat(),
                                "param": param, "val": round(value + d_index * 0.3, 2), "unit": unit, "q": "ok",
                                "msgId": f"GW-{device.device_code}-{p_index}"})
        records.append({"deviceId": "GW-UNREGISTERED-9", "ts": base.isoformat(), "param": "VWC", "val": 20.0,
                        "unit": "%VWC", "q": "ok", "msgId": "GW-UNREGISTERED-9-0"})
        rain = next((d for d in devices if d.device_type == "rainsync"), None)
        if rain:
            records.append({"deviceId": rain.device_code, "ts": base.isoformat(), "param": "VWC", "val": 30.0,
                            "unit": "%VWC", "q": "ok", "msgId": f"GW-{rain.device_code}-unsupported"})
        return PageResult(records, None)


class UnconfiguredRestAdapter:
    """Placeholder for a real REST source. Raises until API documentation, URL and credentials are supplied."""

    payload_kind = "measurements"

    def __init__(self, db: Session, source: IntegrationSource) -> None:
        self.source = source

    def fetch_page(self, cursor: str | None) -> PageResult:
        url = os.environ.get(self.source.base_url_env_var or "", "")
        raise AppError(422, "source_not_configured",
                       f"Source '{self.source.source_code}' has no implemented API client. "
                       f"{'Base URL is set but' if url else 'No base URL configured and'} the source API "
                       "specification has not been supplied – requires source-system owner confirmation.")


ADAPTERS: dict[str, type | None] = {
    "mock_water_meter": MockWaterMeterAdapter,
    "mock_iot_gateway": MockIotGatewayAdapter,
    "push_only": None,  # sources that push to the MRV inbound endpoints
    "unconfigured_rest": UnconfiguredRestAdapter,
    "open_meteo_weather": OpenMeteoAdapter,
    "nasa_power_weather": NasaPowerAdapter,
    "sentinel2_ndvi": Sentinel2NdviAdapter,
    "sample_climate_engine": SampleClimateEngineAdapter,
    "sample_pest_engine": SamplePestEngineAdapter,
    "sample_water_engine": SampleWaterEngineAdapter,
    "sample_iot_gateway": SampleIotGatewayAdapter,
}
SYNTHETIC_ADAPTERS = {"mock_water_meter", "mock_iot_gateway", "sample_climate_engine", "sample_pest_engine",
                      "sample_water_engine", "sample_iot_gateway"}
PUBLIC_DATA_ADAPTERS = {"open_meteo_weather", "nasa_power_weather", "sentinel2_ndvi"}


def requires_mock_flag(adapter_code: str) -> bool | None:
    """True when the adapter's data is synthetic (source must be is_mock), False when it is real data (must not be),
    None when the flag is the operator's choice."""
    if adapter_code in SYNTHETIC_ADAPTERS:
        return True
    if adapter_code in PUBLIC_DATA_ADAPTERS:
        return False
    return None


def uses_platform_codes(adapter_code: str) -> bool:
    """Adapters that read the platform's own plots/devices refer to them by platform code; no identity map is needed."""
    return bool(getattr(ADAPTERS.get(adapter_code), "uses_platform_codes", False))


def catalogue() -> list[dict[str, Any]]:
    """Adapters an operator can register from the integration screen, with the defaults they need."""
    return [{"adapter_code": code, **cls.catalogue, "is_mock": code in SYNTHETIC_ADAPTERS}
            for code, cls in ADAPTERS.items() if cls is not None and hasattr(cls, "catalogue")]


def get_adapter(db: Session, source: IntegrationSource):
    cls = ADAPTERS.get(source.adapter_code)
    if cls is None:
        raise AppError(422, "pull_not_supported", f"Source '{source.source_code}' does not support pull synchronisation")
    return cls(db, source)
