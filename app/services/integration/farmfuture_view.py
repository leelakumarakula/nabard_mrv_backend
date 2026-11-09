"""A device's readings exactly as the FarmFuture app shows them – read live, GET only, nothing stored.

The FarmFuture web app (agricentral-web, reviewed 9 Nov 2026) shows a device's readings on two screens, each from its own
API and worked out its own way:

  Sensors page (device cards)  GET /api/Data/GetAllSensorsLatestData/{farm_id}   SoilSync – latest reading
                               GET /api/Data/GetAllWeatherData?farmId=           MicroClime – latest reading
                               GET /api/Data/GetAllAnalogueWeatherData?farmId=   Analogue Weather – latest reading
  Farm Insights (soil cards)   GET /api/Data/GetFarmStats?farmId=&startDate=&endDate=   last 7 days (today − 6 … today):
                               Average = mean of the device's daily means, Low / High = lowest / highest daily mean,
                               trend = last day against first day (±2 %)

The Sensors page reading is FarmFuture's latest reading before its server-side correction (the corrected reading is
GetAllSensorsLatestDataV2 – FarmFuture's own code says so); the platform's MRV records keep the corrected daily values
from GetFarmStats. This view is for comparing with the FarmFuture app; it is never written to MRV data. Device positions
and names in these responses are not passed on.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Device
from app.services.integration.farmfuture import FarmFutureApi
from app.services.integration.farmfuture_data import linked_farms
from app.services.integration.platform_adapters import IST, today_ist

CACHE_SECONDS = 300  # one farm's latest readings are shared by its devices; FarmFuture updates them every few minutes
_CACHE: dict[tuple[str, ...], tuple[datetime, Any]] = {}

# Sensors page, SoilSync card: (section, label, group, field, unit, decimals) – labels, units and rounding as on the card
F2, F1, UP2 = (2, 2), (1, 1), (0, 2)  # card number formats: (min, max) fraction digits; None = shown as sent
SOIL_CARD = [
    ("Soil Properties", "Top Moisture", "soilProperties", "soilMoisturePercentage1", "%", F2),
    ("Soil Properties", "Bottom Moisture", "soilProperties", "soilMoisturePercentage2", "%", F2),
    ("Soil Properties", "Temperature", "soilProperties", "temperature", "°C", F1),
    ("Soil Properties", "pH", "soilProperties", "ph", "", UP2),
    ("Soil Properties", "EC", "soilProperties", "electricalConductivity", "µS/cm", F2),
    ("Soil Nutrients", "Nitrogen", "soilNutrients", "nitrogen", "kg/ha", UP2),
    ("Soil Nutrients", "Phosphorus", "soilNutrients", "phosphorous", "kg/ha", UP2),
    ("Soil Nutrients", "Potassium", "soilNutrients", "potassium", "kg/ha", UP2),
]
# Sensors page, weather station card: (label, field, unit, format)
MICROCLIME_CARD = [("Temperature", "airTemperature", "°C", None), ("Humidity", "airHumidity", "%", None),
                   ("Wind Speed", "windSpeed", "Km/hr", F2), ("Wind Direction", "windDirection", "", None),
                   ("Rain Fall", "rainfall", "mm", F1), ("Air Pressure", "airPressure", "hPa", F2),
                   ("Leaf Wetness", "leafWetness", "", None), ("Battery Percentage", "voltage", "%", F2),
                   ("Lux", "lux", "lx", None)]
ANALOGUE_CARD = [("Temperature", "airTemperature", "°C", None), ("Humidity", "airHumidity", "%", None),
                 ("Wind Speed", "windSpeed", "m/s", F2), ("Wind Direction", "windDirection", "", None),
                 ("Rainfall (cumulative)", "rainfall", "mm", F1), ("Leaf Wetness", "leafWetness", "", None),
                 ("Battery Percentage", "batteryPercentage", "%", F2),
                 ("Signal strength", "signalStrength", "CSQ (0-31)", None),
                 ("Soil temperature", "soilTemperature", "°C", (1, 2)), ("Moisture 1", "moisture1", "", None),
                 ("Moisture 2", "moisture2", "", None), ("Lux", "lux", "lx", None)]
# Farm Insights soil cards, in FarmFuture's order: (GetFarmStats key, label, unit)
INSIGHTS = [("pH", "pH", ""), ("nitrogen", "Nitrogen", "kg/ha"), ("phosphorous", "Phosphorus", "kg/ha"),
            ("potassium", "Potassium", "kg/ha"), ("soil_moisture_percentage_L1", "Soil Moisture 1", "%"),
            ("soil_moisture_percentage_L2", "Soil Moisture 2", "%"), ("temperature", "Soil Temperature", "deg C"),
            ("ec", "EC", "uS/cm")]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any, fmt: tuple[int, int] | None, unit: str) -> str | None:
    """The value as the FarmFuture card prints it: its rounding (Angular number pipe), then its unit."""
    number = _number(value)
    if number is None:
        text = value if isinstance(value, str) and value else None
    elif fmt is None:
        text = str(int(number)) if number.is_integer() else repr(number)  # as sent, like the card's plain binding
    else:
        # the Angular number pipe rounds the decimal digits half up (Python's float formatting would round half even)
        rounded = Decimal(repr(number)).quantize(Decimal(1).scaleb(-fmt[1]), rounding=ROUND_HALF_UP)
        text = f"{rounded:,.{fmt[1]}f}"
        if fmt[1] > fmt[0] and "." in text:  # trailing zeros dropped down to the minimum fraction digits
            whole, frac = text.split(".")
            frac = frac.rstrip("0").ljust(fmt[0], "0")
            text = f"{whole}.{frac}" if frac else whole
    if text is None:
        return None
    return f"{text}{unit}" if unit in ("%", "°C", "") else f"{text} {unit}"


def _row(section: str | None, label: str, value: Any, fmt: tuple[int, int] | None, unit: str) -> dict[str, Any]:
    number = _number(value)
    return {"section": section, "label": label, "unit": unit, "value": number if number is not None else value,
            "text": _text(value, fmt, unit)}


def _shown(value: Any) -> bool:
    """FarmFuture leaves out a weather reading that is missing or negative (a negative cannot be real)."""
    number = _number(value)
    return (number is not None and number >= 0) or (isinstance(value, str) and bool(value))


def leaf_wetness_category(value: Any) -> str | None:
    """MicroClime leaf wetness in FarmFuture's words (the Analogue station's raw value is shown as sent)."""
    number = _number(value)
    if number is None:
        return None
    return "Dry" if number < 2 else "Moderate" if number < 3.9 else "Wet"


def _farm_id(db: Session, device: Device) -> str:
    if not device.device_code.startswith("FF-"):
        raise AppError(422, "not_farmfuture", "This device does not come from FarmFuture")
    farm_id = next((link.farm_id for link in linked_farms(db).values()
                    if device.farm_plot_id and link.plot.id == device.farm_plot_id), None)
    if farm_id is None:
        raise AppError(422, "farm_not_linked", "The device's plot is not linked to a FarmFuture farm")
    return farm_id


def _get(api: FarmFutureApi, client, path: str, params: dict[str, Any] | None = None) -> Any:
    key = (path, *sorted(f"{k}={v}" for k, v in (params or {}).items()))
    cached = _CACHE.get(key)
    now = datetime.now(IST)
    if cached and cached[0] > now:
        return cached[1]
    response = api._request(client, path, params)
    if response.status_code in (204, 400, 404, 422):
        data = None
    elif response.status_code >= 400:
        raise AppError(502, "source_error", f"FarmFuture {path} failed (HTTP {response.status_code})")
    else:
        body = response.json()
        data = (body.get("data") if body.get("succeeded") else None) if isinstance(body, dict) and "succeeded" in body \
            else body
    _CACHE[key] = (now + timedelta(seconds=CACHE_SECONDS), data)
    return data


def _item(items: Any, mac: str) -> dict[str, Any] | None:
    return next((i for i in items or [] if isinstance(i, dict) and str(i.get("macId")) == mac), None)


def _updated(item: dict[str, Any]) -> str | None:
    """'2026-11-09 14:25:37' (India time) → ISO with offset."""
    text = item.get("updateAt")
    if not isinstance(text, str) or len(text) < 19:
        return None
    try:
        return datetime.fromisoformat(text[:19].replace(" ", "T")).replace(tzinfo=IST).isoformat()
    except ValueError:
        return None


def _soil_card(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("moistureThresholdsResponse") or {}
    npk_shown = status.get("isNpkDisplayed") is not False
    rows = []
    for section, label, group, field, unit, fmt in SOIL_CARD:
        if section == "Soil Nutrients" and not npk_shown:
            continue  # FarmFuture hides N, P and K while the soil is too dry for a usable reading
        rows.append(_row(section, label, (item.get(group) or {}).get(field), fmt, unit))
    extra = item.get("extraParameters") or {}
    if item.get("addOnId") == 1 and extra.get("solarRadiation") is not None:
        rows.append(_row("Soil Nutrients", "Solar Radiation", extra["solarRadiation"], UP2, "W/m²"))
    if item.get("addOnId") == 2 and extra.get("lux") is not None:
        rows.append(_row("Soil Nutrients", "Lux", extra["lux"], None, "lx"))
    battery = _number((item.get("soilProperties") or {}).get("voltage"))
    return {"rows": rows, "battery_pct": round(battery) if battery is not None else None, "npk_shown": npk_shown,
            "note": status.get("displayMessage") or None}


def _weather_card(item: dict[str, Any], analogue: bool) -> dict[str, Any]:
    rows = []
    for label, field, unit, fmt in ANALOGUE_CARD if analogue else MICROCLIME_CARD:
        value = item.get(field)
        always = analogue and field in ("signalStrength", "soilTemperature", "moisture1", "moisture2")
        if field != "windDirection" and not always and not _shown(value):
            continue
        if field == "leafWetness" and not analogue:
            value = leaf_wetness_category(value)
        rows.append(_row(None, label, value, fmt, unit))
    return {"rows": rows, "battery_pct": None, "npk_shown": None, "note": None}


def _insight_devices(stats: Any) -> list[dict[str, Any]]:
    """Every soil device of the farm, in FarmFuture's order and with its Farm Insights device-picker name
    ("Soilsync Hub - 1"). FarmFuture opens Farm Insights on the first one."""
    devices = (stats or {}).get("sensorDevices") or [] if isinstance(stats, dict) else []
    return [{"mac_id": str(d.get("macAddress") or ""), "label": f"{d.get('deviceName') or 'Device'} - {i + 1}",
             "metrics": _insights(d)} for i, d in enumerate(devices) if isinstance(d, dict)]


def _insights(device: dict[str, Any]) -> list[dict[str, Any]]:
    days = sorted((day, values) for day, values in (device.get("stats") or {}).items() if isinstance(values, dict))
    out = []
    for key, label, unit in INSIGHTS:
        daily = []
        for _, values in days:
            stat = values.get(key)
            mean = (stat.get("mean") if stat.get("mean") is not None else stat.get("avg")) if isinstance(stat, dict) else None
            if _number(mean) is not None:
                daily.append(float(mean))
        if not daily:
            continue
        trend = "Stable"
        if len(daily) > 1:
            first, last = daily[0], daily[-1]
            if first != 0:
                change = (last - first) / first
                trend = "Rising" if change > 0.02 else "Falling" if change < -0.02 else "Stable"
            elif last > 0:
                trend = "Rising"
        average, low, high = sum(daily) / len(daily), min(daily), max(daily)
        # the Farm Insights card prints the average with up to 2 decimals and Low / High with up to 1
        out.append({"label": label, "unit": unit, "average": average, "low": low, "high": high,
                    "average_text": _text(average, (0, 2), ""), "low_text": _text(low, (0, 1), ""),
                    "high_text": _text(high, (0, 1), ""), "trend": trend, "days": len(daily)})
    return out


def farmfuture_view(db: Session, device: Device) -> dict[str, Any]:
    farm_id = _farm_id(db, device)
    mac = device.device_code[3:]
    api = FarmFutureApi()
    api.config = {}
    api._init_api()
    today = today_ist()
    start = today - timedelta(days=6)
    sensors_page = insights = None
    with api.client() as client:
        if device.device_type == "soilsync_7in1":
            item = _item(_get(api, client, f"/api/Data/GetAllSensorsLatestData/{farm_id}"), mac)
            if item:
                sensors_page = {**_soil_card(item), "updated_at": _updated(item),
                                "api": "GET /api/Data/GetAllSensorsLatestData/{farm}"}
            # the dates exactly as the FarmFuture app sends them
            stats = _get(api, client, "/api/Data/GetFarmStats",
                         {"farmId": farm_id, "startDate": start.isoformat(), "endDate": today.isoformat()})
            devices = _insight_devices(stats)
            insights = {"from": start.isoformat(), "to": today.isoformat(), "devices": devices,
                        "farmfuture_opens_on": devices[0]["mac_id"] if devices else None,
                        "api": "GET /api/Data/GetFarmStats?farmId=&startDate=&endDate="}
        elif device.device_type in ("microclime", "analogue_weather"):
            analogue = device.device_type == "analogue_weather"
            path = "/api/Data/GetAllAnalogueWeatherData" if analogue else "/api/Data/GetAllWeatherData"
            item = _item(_get(api, client, path, {"farmId": farm_id}), mac)
            if item:
                sensors_page = {**_weather_card(item, analogue), "updated_at": _updated(item),
                                "api": f"GET {path}?farmId="}
        else:
            raise AppError(422, "not_supported", f"FarmFuture shows no readings for a {device.device_type} device here")
    return {"mac_id": mac, "sensors_page": sensors_page, "farm_insights": insights,
            "fetched_at": datetime.now(IST).isoformat()}
