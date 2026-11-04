import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel, Ref

ProgrammeStatus = Literal["draft", "submitted", "approved", "active", "inactive"]
TargetStatus = Literal["proposed", "approved", "revised"]
GeoType = Literal["state", "district", "cluster"]
OrgType = Literal["fpo", "partner", "department", "implementing_agency"]


class ProgrammeIn(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: ProgrammeStatus = "draft"
    funding_framework: str | None = None
    funding_note: str | None = None
    version_label: str | None = None
    proposal_title: str | None = None
    proposal_date: date | None = None
    duration_months: int | None = Field(default=None, ge=1, le=240)
    supersedes_programme_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def check_dates(self) -> "ProgrammeIn":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ProgrammeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: ProgrammeStatus | None = None
    funding_framework: str | None = None
    funding_note: str | None = None
    version_label: str | None = None
    proposal_title: str | None = None
    proposal_date: date | None = None
    duration_months: int | None = Field(default=None, ge=1, le=240)


class ProgrammeOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    start_date: date | None
    end_date: date | None
    status: str
    funding_framework: str | None
    funding_note: str | None
    version_label: str | None
    proposal_title: str | None
    proposal_date: date | None
    duration_months: int | None
    supersedes_programme_id: uuid.UUID | None
    is_demo: bool


class TargetIn(BaseModel):
    metric_code: str = Field(min_length=2, max_length=80)
    metric_name: str
    target_value: Decimal
    unit: str
    period_label: str | None = None
    status: TargetStatus = "proposed"
    source_note: str | None = None
    target_version: int = Field(default=1, ge=1)
    geo_unit_id: uuid.UUID | None = None
    indicator_code: str | None = None


class TargetUpdate(BaseModel):
    """Descriptive changes and status/approval only. The figure itself (value, unit, period, version) never changes in
    place – a changed figure is a new version (POST /programme-targets/{id}/revise). Those fields are accepted only when
    they equal the stored values, so a client that sends the whole record still works."""

    metric_name: str | None = None
    target_value: Decimal | None = None
    unit: str | None = None
    period_label: str | None = None
    status: TargetStatus | None = None
    source_note: str | None = None
    target_version: int | None = Field(default=None, ge=1)
    indicator_code: str | None = None


class TargetRevision(BaseModel):
    """A revised figure for an existing target, stored as the next version; earlier versions are kept unchanged."""

    target_value: Decimal
    unit: str | None = None
    period_label: str | None = None
    status: TargetStatus = "revised"
    source_note: str = Field(min_length=3, description="Why the figure changed and who approved it")


class TargetOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    metric_code: str
    metric_name: str
    target_value: Decimal
    unit: str
    period_label: str | None
    status: str
    source_note: str | None
    target_version: int
    geo_unit_id: uuid.UUID | None
    indicator_code: str | None
    is_demo: bool


class PeriodIn(BaseModel):
    code: str
    name: str
    period_type: Literal["month", "quarter", "half_year", "year", "custom"] = "quarter"
    start_date: date
    end_date: date
    status: Literal["open", "closed"] = "open"

    @model_validator(mode="after")
    def check_dates(self) -> "PeriodIn":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PeriodUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["open", "closed"] | None = None


class PeriodOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    code: str
    name: str
    period_type: str
    start_date: date
    end_date: date
    status: str
    is_demo: bool


class GeoUnitIn(BaseModel):
    parent_id: uuid.UUID | None = None
    unit_type: GeoType
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=50)
    geometry_ref: str | None = None
    requires_confirmation: bool = False
    confirmation_note: str | None = None


class GeoUnitUpdate(BaseModel):
    name: str | None = None
    geometry_ref: str | None = None
    is_archived: bool | None = None
    requires_confirmation: bool | None = None
    confirmation_note: str | None = None


class GeoUnitOut(ORMModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    unit_type: str
    name: str
    code: str
    geometry_ref: str | None
    requires_confirmation: bool
    confirmation_note: str | None
    is_demo: bool
    is_archived: bool


class GeoTreeNode(GeoUnitOut):
    children: list["GeoTreeNode"] = []
    farmer_count: int = 0
    fpo_count: int = 0


class OrganisationIn(BaseModel):
    org_type: OrgType
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=255)
    registration_ref: str | None = None
    geo_unit_id: uuid.UUID | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    status: Literal["active", "inactive"] = "active"


class OrganisationUpdate(BaseModel):
    name: str | None = None
    registration_ref: str | None = None
    geo_unit_id: uuid.UUID | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    status: Literal["active", "inactive"] | None = None
    is_archived: bool | None = None


class OrganisationOut(ORMModel):
    id: uuid.UUID
    org_type: str
    code: str
    name: str
    registration_ref: str | None
    geo_unit_id: uuid.UUID | None
    geo_unit: Ref | None
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None
    status: str
    is_demo: bool
    is_archived: bool


class OrganisationDetail(OrganisationOut):
    farmer_count: int = 0
    enrolled_farmer_count: int = 0
    plot_area_ha: Decimal = Decimal(0)


class ProgrammeOrgIn(BaseModel):
    organisation_id: uuid.UUID
    role: str
    geo_unit_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProgrammeOrgOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    organisation_id: uuid.UUID
    role: str
    geo_unit_id: uuid.UUID | None
    start_date: date | None
    end_date: date | None
    organisation_name: str | None = None
