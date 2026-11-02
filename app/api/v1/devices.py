import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.db.session import get_db
from app.models import Device, DeviceCalibration
from app.schemas.common import Page, PageParams, paginate
from app.schemas.workstream import CalibrationIn, CalibrationOut, DeviceIn, DeviceOut, DeviceUpdate
from app.services import crud
from app.services.locate import resolve
from app.services.scope import apply_scope

router = APIRouter(prefix="/devices", tags=["devices"])


def _with_calibration(db: Session, devices: list[Device]) -> list[DeviceOut]:
    ids = [d.id for d in devices] or [uuid.UUID(int=0)]
    latest = {row.device_id: row for row in db.execute(
        select(DeviceCalibration.device_id, func.max(DeviceCalibration.calibrated_on).label("last"),
               func.max(DeviceCalibration.next_due_on).label("due"))
        .where(DeviceCalibration.device_id.in_(ids)).group_by(DeviceCalibration.device_id))}
    out = []
    for d in devices:
        row = latest.get(d.id)
        out.append(DeviceOut.model_validate(d).model_copy(update={
            "last_calibrated_on": row.last if row else None, "calibration_due_on": row.due if row else None}))
    return out


@router.get("", response_model=Page[DeviceOut])
def list_devices(params: PageParams = Depends(), device_type: str | None = None, status: str | None = None,
                 farm_plot_id: uuid.UUID | None = None, search: str | None = None,
                 current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(Device).order_by(Device.device_code)
    if device_type:
        stmt = stmt.where(Device.device_type == device_type)
    if status:
        stmt = stmt.where(Device.status == status)
    if farm_plot_id:
        stmt = stmt.where(Device.farm_plot_id == farm_plot_id)
    if search:
        stmt = stmt.where(or_(Device.device_code.ilike(f"%{search}%"), Device.provider.ilike(f"%{search}%")))
    stmt = apply_scope(stmt, current, Device.geo_unit_id, Device.organisation_id)
    items, total = paginate(db, stmt, params)
    return {"items": _with_calibration(db, items), "total": total, "page": params.page, "page_size": params.page_size}


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(body: DeviceIn, current: CurrentUser = Depends(require("module.write")),
                  db: Session = Depends(get_db)) -> DeviceOut:
    loc = resolve(db, current, farm_plot_id=body.farm_plot_id, organisation_id=body.organisation_id,
                  geo_unit_id=body.geo_unit_id)
    data = {**body.model_dump(), "geo_unit_id": loc.geo_unit_id, "organisation_id": loc.organisation_id}
    device = crud.create(db, current, Device, data, "device")
    db.commit()
    db.refresh(device)
    return _with_calibration(db, [device])[0]


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
               db: Session = Depends(get_db)) -> DeviceOut:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    return _with_calibration(db, [device])[0]


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(device_id: uuid.UUID, body: DeviceUpdate, current: CurrentUser = Depends(require("module.write")),
                  db: Session = Depends(get_db)) -> DeviceOut:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    data = body.model_dump(exclude_unset=True)
    if "farm_plot_id" in data and data["farm_plot_id"]:
        resolve(db, current, farm_plot_id=data["farm_plot_id"])
    crud.update(db, current, device, data, "device")
    db.commit()
    db.refresh(device)
    return _with_calibration(db, [device])[0]


@router.get("/{device_id}/calibrations", response_model=list[CalibrationOut])
def list_calibrations(device_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                      db: Session = Depends(get_db)) -> list:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    return list(db.scalars(select(DeviceCalibration).where(DeviceCalibration.device_id == device_id)
                           .order_by(DeviceCalibration.calibrated_on.desc())))


@router.post("/{device_id}/calibrations", response_model=CalibrationOut, status_code=201)
def add_calibration(device_id: uuid.UUID, body: CalibrationIn, current: CurrentUser = Depends(require("module.write")),
                    db: Session = Depends(get_db)) -> DeviceCalibration:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    obj = crud.create(db, current, DeviceCalibration, {**body.model_dump(), "device_id": device.id}, "device_calibration")
    if body.result == "fail" and device.status == "active":
        crud.update(db, current, device, {"status": "faulty"}, "device", summary="Calibration failed")
    db.commit()
    return obj
