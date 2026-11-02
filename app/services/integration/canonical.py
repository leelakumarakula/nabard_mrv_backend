"""Canonical inbound payloads. Source payloads are mapped onto these before validation.

These are the platform's proposed contracts, not specifications of any external system.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Canonical(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str | None = None  # optional; must match the receiving source when given
    source_record_id: str = Field(min_length=1, max_length=120)
    programme_code: str

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


def _require_tz(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset, e.g. 2026-10-29T10:30:00+05:30")
    return value


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_evidence_id: str


class CanonicalMeasurement(_Canonical):
    farm_plot_external_id: str | None = None
    geo_code: str | None = None
    activity_code: str | None = None
    indicator_code: str
    value: Decimal
    unit: str
    observed_at: datetime
    method: str | None = None
    evidence: list[EvidenceRef] = []

    _tz = field_validator("observed_at")(_require_tz)


class CanonicalObservation(_Canonical):
    module_code: str
    record_type: str
    farm_plot_external_id: str | None = None
    geo_code: str | None = None
    device_code: str | None = None
    observed_at: datetime | None = None
    attributes: dict[str, Any] = {}

    _tz = field_validator("observed_at")(_require_tz)


class CanonicalDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_delivery_id: str
    farmer_code: str
    channel: Literal["sms", "whatsapp", "ivr", "app", "fpo_meeting", "field_visit"]
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    response: str | None = None
    response_at: datetime | None = None
    understood: bool | None = None

    _tz = field_validator("sent_at", "delivered_at", "read_at", "response_at")(_require_tz)


class CanonicalAdvisory(_Canonical):
    origin_module: str
    advisory_type: Literal["alert", "advisory"]
    hazard_type: str | None = None
    risk_level: str | None = None
    title: str
    message: str
    recommendation: str | None = None
    issued_at: datetime
    valid_until: datetime | None = None
    target_geo_code: str
    deliveries: list[CanonicalDelivery] = []

    _tz = field_validator("issued_at", "valid_until")(_require_tz)


PAYLOAD_SCHEMAS: dict[str, type[_Canonical]] = {
    "measurements": CanonicalMeasurement,
    "observations": CanonicalObservation,
    "advisories": CanonicalAdvisory,
}
