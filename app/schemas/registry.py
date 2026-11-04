import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, Ref

ConsentStatus = Literal["not_recorded", "given", "withdrawn"]
AreaUnit = Literal["ha", "acre"]


class FarmerIn(BaseModel):
    farmer_code: str = Field(min_length=2, max_length=50)
    full_name: str = Field(min_length=1, max_length=200)
    gender: Literal["female", "male", "other", "not_stated"] | None = None
    year_of_birth: int | None = Field(default=None, ge=1900, le=2100)
    phone: str | None = Field(default=None, max_length=30)
    village: str | None = None
    geo_unit_id: uuid.UUID
    primary_fpo_id: uuid.UUID | None = None
    consent_status: ConsentStatus = "not_recorded"
    consent_date: date | None = None
    consent_purpose: str | None = None
    household_code: str | None = Field(default=None, max_length=50)
    demographics: dict[str, Any] | None = None  # values for configured demographic fields


class FarmerUpdate(BaseModel):
    full_name: str | None = None
    gender: Literal["female", "male", "other", "not_stated"] | None = None
    year_of_birth: int | None = Field(default=None, ge=1900, le=2100)
    phone: str | None = None
    village: str | None = None
    geo_unit_id: uuid.UUID | None = None
    primary_fpo_id: uuid.UUID | None = None
    consent_status: ConsentStatus | None = None
    consent_date: date | None = None
    consent_purpose: str | None = None
    status: Literal["active", "inactive"] | None = None
    is_archived: bool | None = None
    household_code: str | None = Field(default=None, max_length=50)
    demographics: dict[str, Any] | None = None  # values for configured demographic fields


class FarmerOut(ORMModel):
    id: uuid.UUID
    farmer_code: str
    household_code: str | None
    full_name: str
    gender: str | None
    year_of_birth: int | None
    phone: str | None
    village: str | None
    geo_unit_id: uuid.UUID
    geo_unit: Ref | None
    primary_fpo_id: uuid.UUID | None
    primary_fpo: Ref | None
    consent_status: str
    consent_date: date | None
    consent_purpose: str | None
    status: str
    is_demo: bool
    is_archived: bool
    demographics: dict[str, Any] | None = None
    categories: list[dict[str, Any]] = []
    pii_masked: bool = False
    sensitive_masked: bool = False


class ParticipationIn(BaseModel):
    programme_id: uuid.UUID
    enrolled_on: date | None = None
    status: Literal["enrolled", "withdrawn", "completed"] = "enrolled"
    notes: str | None = None


class ParticipationOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID
    farmer_id: uuid.UUID
    enrolled_on: date | None
    status: str
    notes: str | None
    is_demo: bool


class FarmIn(BaseModel):
    farm_code: str = Field(min_length=2, max_length=50)
    name: str | None = None
    village: str | None = None
    geo_unit_id: uuid.UUID | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    total_area: Decimal | None = Field(default=None, ge=0)
    area_unit: AreaUnit = "ha"
    area_source: Literal["declared", "surveyed", "gps_measured", "records"] | None = None


class FarmUpdate(BaseModel):
    name: str | None = None
    village: str | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    total_area: Decimal | None = Field(default=None, ge=0)
    area_unit: AreaUnit | None = None
    area_source: Literal["declared", "surveyed", "gps_measured", "records"] | None = None
    is_archived: bool | None = None


class FarmOut(ORMModel):
    id: uuid.UUID
    farmer_id: uuid.UUID
    farm_code: str
    name: str | None
    village: str | None
    geo_unit_id: uuid.UUID | None
    latitude: Decimal | None
    longitude: Decimal | None
    total_area: Decimal | None
    area_unit: str
    area_source: str | None
    is_demo: bool
    is_archived: bool


# Proposal §5.2 plantation maturity categories
PlantationStage = Literal["productive", "young", "underperforming"]


class PlotIn(BaseModel):
    plot_code: str = Field(min_length=2, max_length=50)
    area: Decimal | None = Field(default=None, ge=0)
    area_unit: AreaUnit = "ha"
    area_source: Literal["declared", "surveyed", "gps_measured", "records"] | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    geometry_ref: str | None = None
    crop: Literal["arabica", "robusta", "mixed", "other"] | None = None
    variety: str | None = None
    plantation_year: int | None = Field(default=None, ge=1800, le=2100)
    plantation_stage: PlantationStage | None = None
    shade_type: str | None = None
    irrigation_type: str | None = None


class PlotUpdate(BaseModel):
    area: Decimal | None = Field(default=None, ge=0)
    area_unit: AreaUnit | None = None
    area_source: Literal["declared", "surveyed", "gps_measured", "records"] | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    geometry_ref: str | None = None
    crop: Literal["arabica", "robusta", "mixed", "other"] | None = None
    variety: str | None = None
    plantation_year: int | None = Field(default=None, ge=1800, le=2100)
    plantation_stage: PlantationStage | None = None
    shade_type: str | None = None
    irrigation_type: str | None = None
    is_archived: bool | None = None


class PlotOut(ORMModel):
    id: uuid.UUID
    farm_id: uuid.UUID
    plot_code: str
    area: Decimal | None
    area_unit: str
    area_source: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    geometry_ref: str | None
    crop: str | None
    variety: str | None
    plantation_year: int | None
    plantation_stage: str | None
    shade_type: str | None
    irrigation_type: str | None
    is_demo: bool
    is_archived: bool


class PlotLookupOut(BaseModel):
    id: uuid.UUID
    plot_code: str
    farm_code: str
    farmer_id: uuid.UUID
    farmer_label: str
    area: Decimal | None
    area_unit: str


class FarmWithPlots(FarmOut):
    plots: list[PlotOut] = []


class FarmerDetail(FarmerOut):
    participations: list[ParticipationOut] = []
    farms: list[FarmWithPlots] = []
    total_plot_area_ha: Decimal = Decimal(0)
