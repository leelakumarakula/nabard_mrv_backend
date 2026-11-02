import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.models import Device, Farm, Farmer, FarmPlot, GeoUnit, Organisation


@dataclass
class Location:
    farmer_id: uuid.UUID | None
    farm_plot_id: uuid.UUID | None
    geo_unit_id: uuid.UUID
    organisation_id: uuid.UUID | None
    device_id: uuid.UUID | None = None


def resolve(db: Session, current: CurrentUser, *, farmer_id: uuid.UUID | None = None,
            farm_plot_id: uuid.UUID | None = None, device_id: uuid.UUID | None = None,
            organisation_id: uuid.UUID | None = None, geo_unit_id: uuid.UUID | None = None) -> Location:
    """Derive farmer/geography/FPO from the most specific reference given and enforce the user's scope."""
    device = None
    if device_id:
        device = db.get(Device, device_id)
        if device is None:
            raise AppError(422, "invalid_reference", "Device does not exist")
        farm_plot_id = farm_plot_id or device.farm_plot_id
    if farm_plot_id:
        owner = db.scalar(select(Farm.farmer_id).join(FarmPlot, FarmPlot.farm_id == Farm.id).where(FarmPlot.id == farm_plot_id))
        if owner is None:
            raise AppError(422, "invalid_reference", "Plot does not exist")
        if farmer_id and farmer_id != owner:
            raise AppError(422, "plot_farmer_mismatch", "Plot does not belong to the selected farmer")
        farmer_id = owner
    farmer = None
    if farmer_id:
        farmer = db.get(Farmer, farmer_id)
        if farmer is None:
            raise AppError(422, "invalid_reference", "Farmer does not exist")
    if organisation_id and db.get(Organisation, organisation_id) is None:
        raise AppError(422, "invalid_reference", "Organisation does not exist")
    org = organisation_id or (farmer.primary_fpo_id if farmer else None) or (device.organisation_id if device else None)
    geo = (farmer.geo_unit_id if farmer else None) or (device.geo_unit_id if device else None) or geo_unit_id
    if geo is None and org:
        geo = db.scalar(select(Organisation.geo_unit_id).where(Organisation.id == org))
    if geo is None:
        raise AppError(422, "location_required", "Select a farmer, plot, device, FPO or geography for this record")
    if db.get(GeoUnit, geo) is None:
        raise AppError(422, "invalid_reference", "Geography unit does not exist")
    current.ensure_access(geo, org)
    return Location(farmer_id, farm_plot_id, geo, org, device_id)


def can_edit_own(current: CurrentUser, created_by_id: uuid.UUID | None) -> bool:
    """Field-level users edit their own records; programme-wide roles may edit any record in scope."""
    return current.all_scope or created_by_id == current.id
