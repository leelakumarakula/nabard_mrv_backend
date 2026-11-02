import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

LINKABLE = Literal["farmer", "farm_plot", "field_record", "monitoring_record", "baseline_record", "practice_record",
                   "advisory", "advisory_delivery", "indicator_result", "review_task", "verification_finding", "device",
                   "demonstration_site", "pest_surveillance_cluster", "community_resource_person",
                   "verification_sample", "verification_sample_item"]


class EvidenceOut(ORMModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str
    description: str | None
    evidence_type: str
    source: str
    captured_at: datetime | None
    location_consent: bool
    latitude: Decimal | None
    longitude: Decimal | None
    access_classification: str
    geo_unit_id: uuid.UUID | None
    organisation_id: uuid.UUID | None
    review_status: str
    review_note: str | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    is_demo: bool
    links: list["EvidenceLinkOut"] = []


class EvidenceLinkIn(BaseModel):
    entity_type: LINKABLE
    entity_id: uuid.UUID
    purpose: str = Field(min_length=2, max_length=60)


class EvidenceLinkOut(ORMModel):
    id: uuid.UUID
    evidence_asset_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    purpose: str
    review_status: str
    created_by_id: uuid.UUID | None
    created_at: datetime


class EvidenceReviewIn(BaseModel):
    review_status: Literal["accepted", "rejected"]
    review_note: str | None = None


class ReportRequest(BaseModel):
    programme_id: uuid.UUID
    report_type: Literal["programme", "geography", "fpo", "module", "indicator", "quarterly", "annual", "nabard_ccf_id",
                         "demonstration", "surveillance", "inclusion", "verification", "endline"] = "programme"
    reporting_period_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None
    scope_type: Literal["programme", "state", "district", "cluster", "fpo"] = "programme"
    scope_geo_unit_id: uuid.UUID | None = None
    scope_organisation_id: uuid.UUID | None = None
    module_code: str | None = None
    indicator_id: uuid.UUID | None = None
    title: str | None = None
    template_code: str | None = None  # configurable report template (sections); overrides report_type


class ReportOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    reporting_period_id: uuid.UUID | None
    period_start: date
    period_end: date
    report_type: str
    template_code: str | None
    scope_type: str
    scope_geo_unit_id: uuid.UUID | None
    scope_organisation_id: uuid.UUID | None
    module_code: str | None
    indicator_id: uuid.UUID | None
    title: str
    version: int
    status: str
    generated_by_id: uuid.UUID | None
    generated_at: datetime
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    is_demo: bool


class ReportDetail(ReportOut):
    content: dict[str, Any]


EvidenceOut.model_rebuild()
