"""FarmFuture's coffee pest & disease screen, ported to Python so the platform shows the same risk as FarmFuture.

Source: the FarmFuture web app (agricentral-web, reviewed 9 Nov 2026) – ``wsb/models/intelligence-model.ts``
(``weatherSignals``, ``fieldDecisions``), ``wsb/models/risk-model.ts`` (``assessRisk``, model version
``coffee-screen-2026-09-v2``), ``wsb/models/farm-model.ts`` (``cycleAt``) and the dashboard's seasonal watch list
(``user/pages/dashboard/farm-today/coffee-profile.util.ts``). FarmFuture runs these rules in the browser on its Farm Health
→ Forecast screen; it has no API that returns the result, so the platform runs the same rules on the same FarmFuture
inputs (the 3-hourly forecast, the weather station's latest reading, the farm calendar and the farm's scouting records).

These are screening rules, not fitted disease probabilities (FarmFuture's own words): a "trigger" means *go and
inspect*, not that the pest is present. Only the seven pest and disease topics are ported; FarmFuture's heat, drainage,
nutrition and canopy topics are agronomy, not pests. FarmFuture screens one block (zone) at a time; the platform screens
the whole farm, using every scouting record of the farm.
"""

import math
import re
from datetime import date, datetime
from typing import Any

from app.services.integration.platform_adapters import IST

MODEL_VERSION = "coffee-screen-2026-09-v2"
MODEL_NAME = f"FarmFuture Farm Health pest & disease screen ({MODEL_VERSION})"
STEP = 3 * 3600  # forecast interval, seconds
DAY = 86400
RULES = {"heat_c": 30, "vpd_kpa": 2, "humid_rh": 90, "rain24_mm": 20, "rain3h_mm": 10}

# FarmFuture topic → (platform pest code, FarmFuture title)
TOPICS = {"wsb": ("white_stem_borer", "White stem borer"), "rust": ("leaf_rust", "Leaf rust"),
          "blackrot": ("black_rot", "Black rot / koleroga"), "berryborer": ("coffee_berry_borer", "Coffee berry borer"),
          "shothole": ("shot_hole_borer", "Shot-hole borer"), "mealybug": ("mealybug", "Mealybugs and scale insects"),
          "root": ("root_rot", "Root health")}
SOURCES = {
    "rust": "https://ccri100.org/new-ui/pdf/pathology/INTEGRATED%20MANAGEMENT%20OF%20COFFEE%20LEAF%20RUST%20DISEASE.pdf",
    "wsb": "https://coffeeboard.gov.in/Planter/WSM-Eng.pdf",
    "shothole": "https://ccri100.org/new-ui/pdf/entomology/MANAGEMENT%20OF%20SHOT%20HOLE%20BORER.pdf",
    "mealybug": "https://ccri100.org/new-ui/pdf/entomology/MANAGEMENT%20OF%20SUCKING%20PESTS.pdf",
    "root": "https://ccri100.org/new-ui/pdf/pathology/INTEGRATED%20MANAGEMENT%20OF%20ROOT%20DISEASES.pdf",
    "berryborer": "https://www3.ctahr.hawaii.edu/oc/freepubs/pdf/IP-47.pdf",
    "board": "https://coffeeboard.gov.in/planter.aspx"}
# What FarmFuture tells the grower to check (PRACTICES[topic][0]) and how many days until the next check
WHAT_TO_CHECK = {
    "wsb": ("Walk Arabica rows; check stems for raised ridges, emergence holes and persistent wilt. Mark affected plants "
            "so they can be identified before sanitation.", 7),
    "rust": ("Inspect lower and middle leaf undersides for orange powder and record leaf fall. Confirm the cause and "
             "cultivar before choosing control.", 7),
    "blackrot": ("Check inner foliage and berries for blackening and hanging fungal threads. Inspect slow drying and "
                 "obstructed existing outlets; confirm the disease before sanitation.", 3),
    "berryborer": ("Inspect berry tips for entry holes and record affected plants and note berry sample counts "
                   "separately. Check residual fruit and harvest sanitation; confirm the pest.", 7),
    "shothole": ("Inspect tender twigs for small entry holes and wilt. Verify shot-hole borer before removing affected "
                 "material; review excessive crowding locally.", 7),
    "mealybug": ("Inspect colonies, honeydew and ant attendance on growth, leaf junctions and berry clusters. Record "
                 "natural enemies and distinguish mealybugs from scales.", 7),
    "root": ("Examine persistently wilted plants, collars and roots. Distinguish waterlogging from stump-contact disease, "
             "nutrition and other causes.", 3)}
# Field observations recorded with scouting (``conditions``): label, option texts and how many days they stay valid
FIELD_INPUTS: dict[str, tuple[str, dict[str, str] | None, int]] = {
    "species": ("Planted coffee", {"arabica": "Arabica", "robusta": "Robusta", "mixed": "Mixed Arabica / Robusta"}, 180),
    "rustSusceptibility": ("Cultivar rust susceptibility", {"susceptible": "Known susceptible", "tolerant": "Known tolerant"}, 180),
    "fruitStage": ("Observed fruit stage", {"none": "No berries", "green": "Green developing berries",
                                            "ripe": "Ripe / harvest berries", "residual": "Residual berries after harvest"}, 14),
    "fruitLoad": ("Fruit load", {"normal": "Normal for these plants", "heavy": "Heavy crop load"}, 30),
    "airflow": ("Air movement through bushes", {"open": "Open / ventilated", "restricted": "Crowded / slow drying"}, 30),
    "nutrition": ("Nutrition evidence", {"adequate": "Recent nutrient plan / adequate status",
                                         "deficient": "Deficiency reported"}, 90),
    "residualBerries": ("Old berries on trees / ground", {"yes": "Present", "no": "Checked, none found"}, 14),
    "ants": ("Ants attending colonies", {"yes": "Observed", "no": "Checked, none seen"}, 14),
    "stumps": ("Rotting stumps / affected neighbouring roots", {"yes": "Present", "no": "Checked, absent"}, 180),
    "standingWater": ("Standing water / saturated soil", {"yes": "Observed", "no": "Checked, absent"}, 2),
    "tenderTwigs": ("Tender twigs available", {"yes": "Present", "no": "Checked, absent"}, 14),
    "leafWetnessHours": ("Observed leaf wetness hours in the work day", None, 1),
    "shadePercent": ("Measured shade cover", None, 30)}

# The dashboard's seasonal scouting list: topic → (months, only attacks); FarmFuture's own month lists
WATCH_LIST = {"wsb": ([3, 4, 5, 9, 10, 11, 12], "arabica"), "rust": ([6, 7, 8, 9, 10, 11, 12, 1, 2], "arabica"),
              "blackrot": ([6, 7, 8, 9], None), "berryborer": ([6, 7, 8, 9, 10, 11, 12, 1, 2, 3], None),
              "shothole": ([9, 10, 11, 12, 1, 2, 3, 4], "robusta"), "mealybug": ([2, 3, 4, 5], None),
              "root": ([6, 7, 8, 9, 10, 11], None)}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def on_watch_list(topic: str, month: int, variety: str | None) -> bool:
    """Whether FarmFuture's dashboard lists the topic for the month; a problem of the other variety is left out, and a
    mixed or unknown planting never excludes anything."""
    months, only = WATCH_LIST[topic]
    other = {"arabica": "robusta", "robusta": "arabica"}.get(only or "")
    return month in months and not (only and variety == other)


# -- inputs ------------------------------------------------------------------------------------------------------------
def registered_crops(sensors: Any) -> list[str]:
    """The crops the farm's devices are registered for (GetAllSensorsLatestDataV2 ``crops``), e.g. 'Coffee-Arabica' –
    what FarmFuture's Farm Health reads as the host."""
    items = [sensors] if isinstance(sensors, dict) else sensors if isinstance(sensors, list) else []
    names = [str(c.get("name") if isinstance(c, dict) else c) for s in items if isinstance(s, dict)
             for c in s.get("crops") or [] if c]
    return list(dict.fromkeys(n for n in names if n and n != "None"))


def variety_of(species: str | None) -> str | None:
    """'arabica', 'robusta' or None (mixed or not known)."""
    text = (species or "").lower()
    arabica, robusta = "arabica" in text or "mixed" in text, "robusta" in text or "mixed" in text
    return "arabica" if arabica and not robusta else "robusta" if robusta and not arabica else None


def forecast_slots(body: Any) -> list[dict[str, Any]]:
    """FarmFuture's /WeatherForecast/forecast (OpenWeather 5-day, kelvin) → 3-hour slots, as FarmFuture normalises them:
    no ``rain`` key means 0 mm (no rain forecast), not unknown."""
    slots = []
    for p in (body or {}).get("list") or [] if isinstance(body, dict) else []:
        if not isinstance(p, dict) or not _finite(p.get("dt")):
            continue
        main = p.get("main") or {}
        temp, humidity = main.get("temp"), main.get("humidity")
        rain = (p.get("rain") or {}).get("3h") if "rain" in p else 0
        slots.append({"time": int(p["dt"]), "temperature_c": temp - 273.15 if _finite(temp) else None,
                      "humidity": humidity if _finite(humidity) else None, "rain_mm": rain if _finite(rain) else None})
    return sorted(slots, key=lambda s: s["time"])


def station_reading(items: Any, now: datetime) -> dict[str, Any]:
    """The freshest MicroClime reading (GetAllWeatherData); FarmFuture uses it only when under 6 hours old."""
    stamped = []
    for item in items or [] if isinstance(items, list) else []:
        at = item.get("updateAt") if isinstance(item, dict) else None
        if isinstance(at, str) and len(at) >= 19:
            try:
                stamped.append((datetime.fromisoformat(at[:19].replace(" ", "T")).replace(tzinfo=IST), item))
            except ValueError:
                continue
    if not stamped:
        return {"fresh": False, "temperature_c": None, "humidity": None, "leaf_wetness": None, "reported_at": None}
    at, w = max(stamped, key=lambda e: e[0])
    age = (now - at).total_seconds()
    fresh = -300 <= age <= 6 * 3600
    temp, humidity = w.get("airTemperature"), w.get("airHumidity")
    return {"fresh": fresh, "temperature_c": temp if fresh and _finite(temp) else None,
            "humidity": humidity if fresh and _finite(humidity) else None,
            "leaf_wetness": w.get("leafWetness"), "reported_at": at}


def _stage_from_title(title: str) -> int | None:
    s = title.lower()
    if re.search(r"pre-harvest|berry development|bean fill", s):
        return 6
    if "harvest" in s:
        return 8
    if "flower" in s:
        return 4
    if "fruit sett" in s:
        return 5
    if re.search(r"prun|shade management", s):
        return 12
    return None


def crop_stage(calendar: Any, day: date) -> int | None:
    """FarmFuture's seasonal crop-stage estimate: the farm calendar's template events (any year) projected onto this year."""
    events = (calendar or {}).get("userCommonCalendarEvents") or [] if isinstance(calendar, dict) else []
    for event in events:
        try:
            start = datetime.fromisoformat(str(event.get("start"))[:10]).date()
            end = datetime.fromisoformat(str(event.get("end"))[:10]).date()
        except ValueError:
            continue
        wraps = (end.month, end.day) < (start.month, start.day)
        for year in (day.year - 1, day.year):
            try:
                first = date(year, start.month, start.day)
                last = date(year + (1 if wraps else 0), end.month, end.day)
            except ValueError:  # 29 February in a common year
                continue
            if first <= day <= last:
                stage = _stage_from_title(str(event.get("title") or ""))
                if stage is not None:
                    return stage
    return None


def field_context(records: list[dict[str, Any]], now: datetime) -> dict[str, tuple[Any, str]]:
    """The newest valid value of each field observation recorded with scouting (``conditions``) → (value, date)."""
    out: dict[str, tuple[Any, str]] = {}
    for r in sorted(_recent(records, now, 366), key=lambda r: r["date"], reverse=True):
        age = _age(r, now)
        conditions = r.get("conditions") if isinstance(r.get("conditions"), dict) else {}
        for key, (_, _, days) in FIELD_INPUTS.items():
            value = (r.get("shadePercent") if r.get("shadePercent") is not None else conditions.get(key)) \
                if key == "shadePercent" else conditions.get(key)
            if key not in out and value is not None and age <= days:
                out[key] = (value, r["date"][:10])
    return out


def _age(record: dict[str, Any], now: datetime) -> float:
    """Days since the record's date began (India time), as a fraction – FarmFuture's recordAge."""
    try:
        start = datetime.combine(date.fromisoformat(str(record.get("date"))[:10]), datetime.min.time(), IST)
    except ValueError:
        return math.inf
    return (now - start).total_seconds() / DAY


def _recent(records: list[dict[str, Any]], now: datetime, days: int) -> list[dict[str, Any]]:
    return [r for r in records if 0 <= _age(r, now) <= days]


# -- the weather screen (weatherSignals) -------------------------------------------------------------------------------
def _vpd(temp: Any, rh: Any) -> float | None:
    if _finite(temp) and _finite(rh) and 0 <= rh <= 100:
        return 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - rh / 100)
    return None


def weather_signals(slots: list[dict[str, Any]], now: int) -> dict[str, Any]:
    """FarmFuture's weatherSignals: the forecast slots of the next 48 hours read against its screening rules."""
    window = {s["time"]: s for s in slots if _finite(s.get("time")) and now <= s["time"] <= now + 2 * DAY}
    slots = [window[t] for t in sorted(window)]

    def temp_ok(s): return _finite(s["temperature_c"]) and -20 <= s["temperature_c"] <= 60
    def humid_ok(s): return _finite(s["humidity"]) and 0 <= s["humidity"] <= 100
    def rain_ok(s): return _finite(s["rain_mm"]) and s["rain_mm"] >= 0
    def complete(s): return temp_ok(s) and humid_ok(s) and rain_ok(s)
    def contiguous(s, p): return p is None or s["time"] - p["time"] == STEP

    def longest(predicate, min_wet=0, validator=complete) -> int:
        run: list[dict[str, Any]] = []
        best = 0
        for s in slots:
            if not validator(s) or not predicate(s):
                run = []
                continue
            if run and not contiguous(s, run[-1]):
                run = []
            run.append(s)
            if sum(1 for x in run if x["rain_mm"] > 0) >= min_wet:
                best = max(best, len(run))
        return best

    available = longest(lambda s: True) >= 6
    heat_available = longest(lambda s: True, 0, temp_ok) >= 6
    blackrot_available = longest(lambda s: True, 0, lambda s: humid_ok(s) and rain_ok(s)) >= 6
    humidity_available = longest(lambda s: True, 0, humid_ok) >= 6
    valid = [s for s in slots if complete(s)]
    temps = [s for s in slots if temp_ok(s)]
    rain = [s for s in slots if rain_ok(s)]
    next24 = [s for s in slots if s["time"] <= now + DAY]
    rain_complete = (len(next24) >= 8 and next24[0]["time"] - now <= STEP
                     and all(rain_ok(s) and contiguous(s, next24[i - 1] if i else None) for i, s in enumerate(next24)))
    rain24 = sum(s["rain_mm"] for s in next24) if rain_complete else None
    vpds = [v for s in slots if temp_ok(s) and humid_ok(s) if (v := _vpd(s["temperature_c"], s["humidity"])) is not None]
    rust_run = longest(lambda s: 22 <= s["temperature_c"] <= 27 and s["humidity"] >= 80, 1) if available else 0
    blackrot_run = longest(lambda s: s["humidity"] >= 90, 2, lambda s: humid_ok(s) and rain_ok(s)) \
        if blackrot_available else 0
    humid_run = longest(lambda s: s["humidity"] >= 85, 0, humid_ok) if humidity_available else 0
    wet = sum(1 for s in valid if s["rain_mm"] > 0)
    dry = sum(1 for s in valid if s["rain_mm"] == 0)
    warm = sum(1 for s in valid if s["temperature_c"] >= 27)
    persistent_wet = available and longest(lambda s: s["humidity"] >= 90 and s["rain_mm"] > 0) >= 4
    return {"available": available, "heat_available": heat_available, "blackrot_available": blackrot_available,
            "humidity_available": humidity_available, "rain24": rain24,
            "max_temp": max((s["temperature_c"] for s in temps), default=None),
            "max_vpd": max(vpds, default=None), "rust_run": rust_run, "blackrot_run": blackrot_run,
            "humid_run": humid_run, "wet": wet, "dry": dry, "warm": warm, "persistent_wet": persistent_wet,
            "rust_watch": rust_run >= 2, "blackrot_watch": blackrot_run >= 4,
            "mealy_watch": available and warm >= 2 and wet >= 1 and dry >= 4 and not persistent_wet,
            "heavy_rain": (rain24 is not None and rain24 >= RULES["rain24_mm"])
            or any(s["rain_mm"] >= RULES["rain3h_mm"] for s in rain)}


# -- the risk screen (assessRisk + fieldDecisions) ---------------------------------------------------------------------
def assess(topic: str, w: dict[str, Any], species: str | None, stage: int | None, month: int,
           context: dict[str, tuple[Any, str]], station: dict[str, Any]) -> dict[str, Any]:
    """FarmFuture's assessRisk for one topic: priority 0 = unknown, 1 = routine inspection, 2 = trigger (inspect)."""
    factors: list[str] = []
    gaps: list[str] = []

    def v(key):
        value = context.get(key, (None, ""))[0]
        return None if value in (None, "unknown") else value

    def field(key):
        value = v(key)
        label, options, _ = FIELD_INPUTS[key]
        if value is None:
            gaps.append(label)
            return None
        suffix = "%" if key == "shadePercent" else " h" if key == "leafWetnessHours" else ""
        factors.append(f"{label}: {(options or {}).get(value, value)}{suffix} · {context[key][1]}")
        return value

    def num(value) -> float | None:
        return float(value) if _finite(value) else None

    def weather_factor(name, detail, available=None):
        available = w["available"] if available is None else available
        factors.append(f"{name}: {detail if available else 'No complete, fresh forecast sequence'}")
        if not available:
            gaps.append("Fresh temperature, humidity and rain forecast")

    host = v("species") or species or ""
    arabica, robusta = bool(re.search("arabica|mixed", host, re.I)), bool(re.search("robusta|mixed", host, re.I))
    host_known = arabica or robusta
    priority, status, trigger = 1, "Routine inspection", False
    if topic in ("rust", "wsb", "shothole", "mealybug"):
        factors.append(f"Host: {host if host_known else 'Not recorded'}")
        if v("species") is None:
            gaps.append("Record the planted rows and cultivar for this zone")
    temp, humid = station.get("temperature_c"), station.get("humidity")
    fresh = station.get("fresh") and _finite(temp) and _finite(humid)

    if topic == "rust":
        susceptibility, wet, load, nutrition = field("rustSusceptibility"), field("leafWetnessHours"), field("fruitLoad"), \
            field("nutrition")
        field("airflow")
        field("shadePercent")
        weather_factor("Warm, humid wet-weather screen", f"{w['rust_run']} consecutive 3-hour samples at 22–27 °C and "
                       "humidity of 80% or more, with rain in the sequence")
        observed = bool(fresh and 22 <= temp <= 27 and humid >= 80 and (num(wet) or 0) > 0)
        trigger = w["rust_watch"] or observed
        priority = 2 if trigger else 1 if w["available"] else 0
        status = "Rust conditions · inspect" if trigger else "No rust weather trigger" if w["available"] \
            else "Rust weather unknown"
        lower = susceptibility == "tolerant" or (not arabica and robusta and susceptibility != "susceptible")
        if trigger and lower and not (load == "heavy" and nutrition == "deficient"):
            priority, status = 1, "Tolerant host · wet-weather review"
        elif trigger and (susceptibility == "susceptible" or arabica):
            status = "Susceptible host · rust weather watch"
        gaps += ["Rust spores / leaf check by inspection", "Hours of leaf wetness measured in the field"]
    elif topic == "blackrot":
        airflow, shade, wet = field("airflow"), field("shadePercent"), field("leafWetnessHours")
        weather_factor("Persistent humid rain sequence", f"{w['blackrot_run']} consecutive 3-hour samples at humidity of "
                       "90% or more, with rain in at least two samples", w["blackrot_available"])
        trigger = w["blackrot_watch"] or bool(fresh and humid >= 90 and (num(wet) or 0) >= 12)
        priority = 2 if trigger else 1 if w["blackrot_available"] else 0
        status = "Persistent wet-leaf watch" if trigger else "No persistent wet trigger" if w["blackrot_available"] \
            else "Wet-leaf evidence needed"
        if airflow == "restricted" or (num(shade) or 0) >= 70:
            factors.append("Drying constraint: Reported crowding / high shade warrants checking inner foliage during wet "
                           "spells.")
        gaps += ["Hanging mist and actual leaf cover drying history",
                 "Fungal threads / affected leaves and berries confirmed in the field"]
    elif topic == "wsb":
        shade = field("shadePercent")
        flight, preflight = month in (4, 5, 10, 11, 12), month in (3, 9)
        trigger = arabica and (flight or preflight)
        priority = 2 if trigger else 1 if host_known else 0
        status = ("Pre-flight stem inspection" if preflight else "Seasonal stem-borer watch") if trigger \
            else "Host / stem inspection"
        factors.append("Flight calendar: " + ("March / September pre-flight planning" if preflight else
                                              "Typical April–May / October–December flight period" if flight else
                                              "Outside default flight window"))
        if w["available"]:
            factors.append("Flight weather: " + ("Wet forecast may delay flight; it does not cancel stem checks."
                                                 if w["persistent_wet"] else
                                                 "Check actual sun exposure and local adult / trap activity."))
        if num(shade) is not None and num(shade) < 35:
            factors.append("Stem exposure: Low measured shade: inspect exposed Arabica stems.")
        gaps.append("Stem symptoms, infestation history and local adult / trap observations")
    elif topic == "shothole":
        shade, twigs, airflow = field("shadePercent"), field("tenderTwigs"), field("airflow")
        season = month in (9, 10, 11, 12, 1)
        weather_factor("Humid-period context", f"{w['humid_run']} consecutive 3-hour samples at humidity of 85% or more",
                       w["humidity_available"])
        trigger = robusta and w["humid_run"] >= 4 and (season or (num(shade) or 0) >= 70 or airflow == "restricted") \
            and twigs != "no"
        priority = 2 if trigger else 0 if not w["available"] and not host_known else 1
        status = "Humid twig-borer watch" if trigger else "Twig / host review"
        factors.append("Season: " + ("September–January population-watch period" if season
                                     else "Outside the default September–January peak"))
        gaps.append("Twig entry holes and pest identification; wilt alone is nonspecific")
    elif topic == "mealybug":
        ants = field("ants")
        weather_factor("Warm intermittent-moisture screen", f"{w['warm']} samples ≥27 °C; {w['wet']} rainy and {w['dry']} "
                       f"dry samples; prolonged wet sequence {'present' if w['persistent_wet'] else 'absent'}")
        trigger = ants == "yes" or w["mealy_watch"]
        priority = 2 if trigger else 1 if w["available"] else 0
        status = "Ant-attended colony check" if ants == "yes" else "Warm / intermittent-moisture watch" \
            if w["mealy_watch"] else "No mealybug weather trigger" if w["available"] else "Colony evidence needed"
        gaps += ["Mealybug versus scale identification, colony counts and natural enemies",
                 "Irrigation history; weather rain does not capture irrigation"]
    elif topic == "berryborer":
        fruit, residual = field("fruitStage"), field("residualBerries")
        estimated = stage in (6, 7, 8)
        trigger = fruit in ("green", "ripe", "residual") or residual == "yes"
        priority = 2 if trigger else 1
        status = "Berry / sanitation inspection" if trigger else "Estimated berry-stage watch" if estimated and not fruit \
            else "Confirm berry availability"
        factors.append("Crop-cycle basis: " + ("Field-reported fruit condition overrides the calendar." if fruit else
                                               "Calendar suggests berries; check seed maturity and fruit on the plants."
                                               if estimated else "No fruit stage recorded yet."))
        if w["available"] and w["max_temp"] is not None:
            factors.append(f"Temperature context: Forecast maximum {w['max_temp']:.1f} °C; no uncalibrated "
                           "temperature-to-infestation conversion.")
        gaps += ["Berry entry holes / traps / infestation history",
                 "No within-zone berry-load or sanitation map without located observations"]
    elif topic == "root":
        pond, stumps = field("standingWater"), field("stumps")
        trigger = pond == "yes" or stumps == "yes" or w["heavy_rain"]
        priority = 2 if trigger else 1
        status = "Reported saturation · check roots" if pond == "yes" else "Root-contact / stump inspection" \
            if stumps == "yes" else "Rain-related root check" if w["heavy_rain"] else "Root cause review"
        factors.append("Rain context: " + (("Heavy-rain screen active; inspect actual drainage." if w["heavy_rain"] else
                                            "No heavy-rain screen triggered.") if w["available"] else "Forecast unavailable"))
        gaps += ["Root / collar examination and disease identification",
                 "Calibrated root-zone moisture, persistence and drainage history"]
    else:
        raise ValueError(topic)
    if station.get("fresh") and topic in ("rust", "blackrot"):
        factors.append(f"Current weather station: {temp if _finite(temp) else 'unknown'} °C / "
                       f"{humid if _finite(humid) else 'unknown'}% humidity")
    if topic in ("rust", "blackrot") and station.get("leaf_wetness") is not None:
        gaps.append("Hours of leaf wetness recorded in the field")
    return {"topic": topic, "priority": priority, "status": status, "trigger": bool(trigger), "factors": factors,
            "gaps": list(dict.fromkeys(gaps)), "source": SOURCES.get(topic, SOURCES["board"])}


def screen_farm(*, forecast: Any, stations: Any, calendar: Any, records: list[dict[str, Any]], species: str | None,
                now: datetime) -> list[dict[str, Any]]:
    """Every pest and disease topic for a farm, as FarmFuture's fieldDecisions works it out: the weather screen, then a
    recent scouting record with signs found raises the topic to 3 ("Signs found"), and an overdue follow-up check to 2."""
    today = now.astimezone(IST).date()
    w = weather_signals(forecast_slots(forecast), int(now.timestamp()))
    station = station_reading(stations, now)
    stage = crop_stage(calendar, today)
    records = [r for r in records if isinstance(r, dict) and isinstance(r.get("date"), str)]
    context = field_context([r for r in records if not r.get("point")], now)
    observed = context.get("species", (None,))[0]
    variety = variety_of(observed if observed not in (None, "unknown") else species)
    out = []
    for topic in TOPICS:
        result = assess(topic, w, species, stage, today.month, context, station)
        topic_records = sorted((r for r in records if r.get("topic") == topic), key=lambda r: r["date"], reverse=True)
        seen: set[str] = set()
        for r in _recent([r for r in topic_records if r.get("kind") == "inspection"], now, 14):
            point = r.get("point") if isinstance(r.get("point"), dict) else None
            key = f"{point.get('lat')},{point.get('lon')}" if point else "zone"
            if key in seen:
                continue
            seen.add(key)
            if _finite(r.get("affected")) and r["affected"] > 0:
                result["priority"], result["status"] = 3, "Signs found"
                result["factors"].insert(0, f"Inspection: {r['affected']} of {r.get('inspected')} plants had signs "
                                            f"found on {r['date'][:10]}.")
                break
        follow = topic_records[0].get("nextCheck") if topic_records else None
        if isinstance(follow, str) and follow[:10] < today.isoformat():
            result["priority"] = max(result["priority"], 2)
            result["overdue_check"] = follow[:10]
        result["on_watch_list"] = on_watch_list(topic, today.month, variety)
        result["what_to_check"], result["follow_days"] = WHAT_TO_CHECK[topic]
        out.append(result)
    return out
