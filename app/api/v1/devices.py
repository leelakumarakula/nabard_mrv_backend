import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Device, DeviceCalibration, FieldRecord
from app.schemas.common import Page, PageParams, paginate
from app.schemas.workstream import CalibrationIn, CalibrationOut, DeviceIn, DeviceOut, DeviceUpdate
from app.services import crud
from app.services.device_location import location_fields, parse_position, reporting_state
from app.services.integration.farmfuture_view import farmfuture_view
from app.services.locate import resolve
from app.services.scope import apply_scope

router = APIRouter(prefix="/devices", tags=["devices"])


COMPLETENESS_DAYS = 30
READING_TYPES = ("sensor_observation", "climate_observation")


def completeness(db: Session, devices: list[Device], days: int = COMPLETENESS_DAYS) -> dict[uuid.UUID, dict]:
    """Per device: last reading, days with at least one stored reading in the last `days` days, the days it was
    expected to report (since installation, until removal) and the latest battery / signal snapshot."""
    ids = [d.id for d in devices] or [uuid.UUID(int=0)]
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    last = dict(db.execute(select(FieldRecord.device_id, func.max(FieldRecord.observed_at)).where(
        FieldRecord.device_id.in_(ids), FieldRecord.record_type.in_(READING_TYPES),
        FieldRecord.validation_status != "rejected").group_by(FieldRecord.device_id)).all())
    reporting = dict(db.execute(select(FieldRecord.device_id, func.count(func.distinct(cast(FieldRecord.observed_at, Date))))
                                .where(FieldRecord.device_id.in_(ids), FieldRecord.record_type.in_(READING_TYPES),
                                       FieldRecord.validation_status != "rejected", FieldRecord.observed_at >= since)
                                .group_by(FieldRecord.device_id)).all())
    health: dict[uuid.UUID, dict] = {}
    for record in db.scalars(select(FieldRecord).where(FieldRecord.device_id.in_(ids),
                                                       FieldRecord.record_type == "device_health")
                             .order_by(FieldRecord.observed_at.desc())).unique():
        health.setdefault(record.device_id, record.attributes or {})
    out = {}
    for d in devices:
        start = max(since.date(), d.installed_on) if d.installed_on else since.date()
        end = min(now.date(), d.removed_on) if d.removed_on else now.date()
        expected = max(0, min(days, (end - start).days)) if d.status in ("active", "faulty") else 0
        got = min(reporting.get(d.id, 0), expected) if expected else reporting.get(d.id, 0)
        out[d.id] = {"last_reading_at": last.get(d.id), "reporting_days_30": got, "expected_days_30": expected,
                     "completeness_pct": round(got / expected * 100, 1) if expected else None,
                     "battery_pct": health.get(d.id, {}).get("battery_pct"),
                     "signal_csq": health.get(d.id, {}).get("signal_csq")}
    return out


def _with_calibration(db: Session, devices: list[Device], precise: bool = False) -> list[DeviceOut]:
    ids = [d.id for d in devices] or [uuid.UUID(int=0)]
    latest = {row.device_id: row for row in db.execute(
        select(DeviceCalibration.device_id, func.max(DeviceCalibration.calibrated_on).label("last"),
               func.max(DeviceCalibration.next_due_on).label("due"))
        .where(DeviceCalibration.device_id.in_(ids)).group_by(DeviceCalibration.device_id))}
    data = completeness(db, devices)
    out = []
    for d in devices:
        row = latest.get(d.id)
        out.append(DeviceOut.model_validate(d).model_copy(update={
            "last_calibrated_on": row.last if row else None, "calibration_due_on": row.due if row else None,
            **data[d.id], "reporting_state": reporting_state(d.status, data[d.id]["last_reading_at"]),
            **location_fields(d, precise)}))
    return out


def _position(data: dict, current: CurrentUser) -> dict:
    """Entered position: both coordinates or neither; stamped with its source and time."""
    if "latitude" not in data and "longitude" not in data:
        return data
    lat, lon = data.pop("latitude", None), data.pop("longitude", None)
    if (lat is None) != (lon is None):
        raise AppError(422, "position_incomplete", "Give both latitude and longitude, or neither")
    if lat is not None and not current.has("farmer.read_pii"):
        raise AppError(403, "forbidden", "Setting an exact device position needs permission to see precise locations")
    position = parse_position(lat, lon) if lat is not None else None
    if lat is not None and position is None:
        raise AppError(422, "invalid_position", "The position is not a usable latitude / longitude")
    return {**data, "latitude": position[0] if position else None, "longitude": position[1] if position else None,
            "location_source": "entered" if position else None,
            "location_updated_at": datetime.now(UTC) if position else None}


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
    return {"items": _with_calibration(db, items, current.has("farmer.read_pii")), "total": total, "page": params.page,
            "page_size": params.page_size}


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(body: DeviceIn, current: CurrentUser = Depends(require("module.write")),
                  db: Session = Depends(get_db)) -> DeviceOut:
    loc = resolve(db, current, farm_plot_id=body.farm_plot_id, organisation_id=body.organisation_id,
                  geo_unit_id=body.geo_unit_id)
    data = _position({**body.model_dump(), "geo_unit_id": loc.geo_unit_id, "organisation_id": loc.organisation_id}, current)
    device = crud.create(db, current, Device, data, "device")
    db.commit()
    db.refresh(device)
    return _with_calibration(db, [device], current.has("farmer.read_pii"))[0]


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
               db: Session = Depends(get_db)) -> DeviceOut:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    return _with_calibration(db, [device], current.has("farmer.read_pii"))[0]


@router.get("/{device_id}/farmfuture")
def farmfuture_readings(device_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                        db: Session = Depends(get_db)) -> dict:
    """The device's readings exactly as the FarmFuture app shows them (Sensors page and Farm Insights) – read live with
    GET, for comparison only; nothing is stored."""
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    return farmfuture_view(db, device)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(device_id: uuid.UUID, body: DeviceUpdate, current: CurrentUser = Depends(require("module.write")),
                  db: Session = Depends(get_db)) -> DeviceOut:
    device = crud.get_or_404(db, Device, device_id, "Device")
    current.ensure_access(device.geo_unit_id, device.organisation_id)
    data = _position(body.model_dump(exclude_unset=True), current)
    if "farm_plot_id" in data and data["farm_plot_id"]:
        resolve(db, current, farm_plot_id=data["farm_plot_id"])
    crud.update(db, current, device, data, "device")
    db.commit()
    db.refresh(device)
    return _with_calibration(db, [device], current.has("farmer.read_pii"))[0]


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
