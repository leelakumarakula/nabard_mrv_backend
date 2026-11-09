"""FarmFuture's coffee pest & disease screen (coffee-screen-2026-09-v2), ported – checked against its rules."""

from datetime import datetime, timedelta

from app.services.integration.coffee_pest_screen import (
    crop_stage,
    on_watch_list,
    registered_crops,
    screen_farm,
    variety_of,
)
from app.services.integration.platform_adapters import IST

NOW = datetime(2026, 11, 9, 6, 0, tzinfo=IST)


def forecast(temp_c=24.0, humidity=70, rain=None, slots=16, start=NOW + timedelta(hours=1)):
    """FarmFuture /WeatherForecast/forecast shape (kelvin); rain(i) gives slot i's 3-hour rain (None = no rain key)."""
    out = []
    for i in range(slots):
        at = start + timedelta(hours=3 * i)
        r = rain(i) if callable(rain) else rain
        t = temp_c(i) if callable(temp_c) else temp_c
        h = humidity(i) if callable(humidity) else humidity
        out.append({"dt": int(at.timestamp()), "main": {"temp": t + 273.15, "humidity": h},
                    **({"rain": {"3h": r}} if r else {})})
    return {"list": out}


def screen(fc=None, species="arabica", records=(), stations=None, calendar=None, now=NOW):
    return {r["topic"]: r for r in screen_farm(forecast=fc if fc is not None else forecast(), stations=stations,
                                                calendar=calendar, records=list(records), species=species, now=now)}


def test_rust_needs_two_warm_humid_wet_slots_and_respects_the_host() -> None:
    wet_warm = forecast(24.0, 85, rain=lambda i: 1.0 if i == 1 else None)
    arabica = screen(wet_warm)["rust"]
    assert (arabica["priority"], arabica["status"], arabica["trigger"]) == (2, "Susceptible host · rust weather watch", True)
    assert any("consecutive 3-hour samples at 22–27 °C" in f for f in arabica["factors"])
    robusta = screen(wet_warm, species="robusta")["rust"]
    assert (robusta["priority"], robusta["status"]) == (1, "Tolerant host · wet-weather review")
    dry = screen(forecast(24.0, 85))["rust"]  # humid and warm but no rain in the run
    assert (dry["priority"], dry["status"]) == (1, "No rust weather trigger")
    unknown = screen(fc={})["rust"]  # no forecast: FarmFuture cannot judge
    assert (unknown["priority"], unknown["status"]) == (0, "Rust weather unknown")
    assert "Fresh temperature, humidity and rain forecast" in unknown["gaps"]


def test_black_rot_shot_hole_mealybug_and_root_triggers() -> None:
    wet = screen(forecast(20.0, 95, rain=lambda i: 2.0 if i % 2 else None))
    assert (wet["blackrot"]["priority"], wet["blackrot"]["status"]) == (2, "Persistent wet-leaf watch")
    humid = screen(forecast(20.0, 88), species="robusta")["shothole"]  # November is in the Sep–Jan watch period
    assert (humid["priority"], humid["status"]) == (2, "Humid twig-borer watch")
    assert screen(forecast(20.0, 88), species="arabica")["shothole"]["priority"] == 1
    mealy = screen(forecast(lambda i: 28.0, 60, rain=lambda i: 1.0 if i == 0 else None))["mealybug"]
    assert (mealy["priority"], mealy["status"]) == (2, "Warm / intermittent-moisture watch")
    storm = screen(forecast(22.0, 80, rain=lambda i: 12.0 if i == 3 else None))["root"]
    assert (storm["priority"], storm["status"]) == (2, "Rain-related root check")
    assert screen()["root"]["status"] == "Root cause review"


def test_white_stem_borer_follows_the_flight_calendar() -> None:
    november = screen()["wsb"]
    assert (november["priority"], november["status"]) == (2, "Seasonal stem-borer watch")
    march = screen(now=datetime(2026, 3, 9, 6, 0, tzinfo=IST), fc=forecast(start=datetime(2026, 3, 9, 7, 0, tzinfo=IST)))
    assert march["wsb"]["status"] == "Pre-flight stem inspection"
    assert screen(species="robusta")["wsb"]["priority"] == 1
    assert screen(species=None)["wsb"]["priority"] == 0  # host not recorded


def test_scouting_raises_the_level_and_field_observations_count() -> None:
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    records = [{"kind": "inspection", "topic": "shothole", "date": yesterday, "inspected": 25, "affected": 2},
               {"kind": "inspection", "topic": "berryborer", "date": "2026-10-01", "inspected": 10, "affected": 4,
                "nextCheck": "2026-10-08"},
               {"kind": "practice", "topic": "mealybug", "date": yesterday, "conditions": {"ants": "yes"}}]
    out = screen(records=records, species="robusta")
    assert (out["shothole"]["priority"], out["shothole"]["status"]) == (3, "Signs found")
    assert out["shothole"]["factors"][0] == f"Inspection: 2 of 25 plants had signs found on {yesterday}."
    # an old finding is not "signs found", but its follow-up check is overdue
    assert (out["berryborer"]["priority"], out["berryborer"]["overdue_check"]) == (2, "2026-10-08")
    assert out["mealybug"]["status"] == "Ant-attended colony check"


def test_crop_stage_from_the_farm_calendar_and_the_seasonal_watch_list() -> None:
    calendar = {"userCommonCalendarEvents": [
        {"title": "Berry development", "start": "2024-08-01T00:00:00", "end": "2024-11-30T00:00:00"},
        {"title": "Harvest", "start": "2024-12-01T00:00:00", "end": "2025-02-15T00:00:00"}]}
    assert crop_stage(calendar, NOW.date()) == 6
    assert crop_stage(calendar, NOW.date().replace(month=12, day=20)) == 8
    assert crop_stage(calendar, NOW.date().replace(year=2027, month=1, day=20)) == 8  # wraps over the new year
    berry = screen(calendar=calendar)["berryborer"]
    assert (berry["priority"], berry["status"]) == (1, "Estimated berry-stage watch")
    # FarmFuture's dashboard list for November Robusta: berry borer, shot-hole borer and root health (not rust or WSB)
    assert [t for t in ("wsb", "rust", "blackrot", "berryborer", "shothole", "mealybug", "root")
            if on_watch_list(t, 11, "robusta")] == ["berryborer", "shothole", "root"]


def test_host_comes_from_the_registered_crops() -> None:
    sensors = [{"macId": "A", "crops": [{"name": "Coffee-Arabica"}]}, {"macId": "B", "crops": [{"name": "Coffee-Robusta"}]},
               {"macId": "C", "crops": []}]
    assert registered_crops(sensors) == ["Coffee-Arabica", "Coffee-Robusta"]
    assert registered_crops({"macId": "T", "crops": [{"name": "Tea-Assamica"}]}) == ["Tea-Assamica"]
    assert (variety_of("Coffee-Arabica"), variety_of("Coffee-Arabica, Coffee-Robusta"), variety_of("robusta")) == (
        "arabica", None, "robusta")
    out = screen(species="Coffee-Arabica")
    assert out["wsb"]["priority"] == 2 and out["rust"]["on_watch_list"] and not out["shothole"]["on_watch_list"]


def test_station_reading_only_counts_while_fresh() -> None:
    wet_leaves = [{"kind": "practice", "topic": "rust", "date": NOW.date().isoformat(), "conditions": {"leafWetnessHours": 5}}]
    station = [{"macId": "X", "updateAt": "2026-11-09 05:30:00", "airTemperature": 24, "airHumidity": 88}]
    fresh = screen(fc={}, records=wet_leaves, stations=station)["rust"]
    assert (fresh["priority"], fresh["trigger"]) == (2, True)
    stale = [{**station[0], "updateAt": "2026-11-08 20:00:00"}]
    assert screen(fc={}, records=wet_leaves, stations=stale)["rust"]["priority"] == 0
