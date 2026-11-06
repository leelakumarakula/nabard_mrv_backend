"""Workstream 1: rainfall status vs the 30-year normal – the calculation and the platform calculation source."""

from datetime import date, datetime, time

from sqlalchemy import select, true

from app.models import Farm, Farmer, FarmPlot, FieldRecord, IntegrationSource, SystemSetting
from app.services.climate_status import classify, month_statuses, usual_rain
from app.services.integration.platform_adapters import IST
from app.services.record_types import MONTHS
from app.services.settings import DEFAULT_SETTINGS

BANDS = sorted(DEFAULT_SETTINGS["climate.rainfall_status_bands"][0], key=lambda b: -b["min_pct"])
# 30-year normals: 31 mm in every 31-day month, 30 mm in 30-day months, 28 mm in February (1 mm a day), January record 40
NORMALS = {**{f"rain_{k}_mm": float(31 if k in ("jan", "mar", "may", "jul", "aug", "oct", "dec") else 30) for k, _ in MONTHS},
           "rain_feb_mm": 28.0, "rain_jan_max_mm": 40.0}


def test_usual_rain_is_pro_rata_for_part_months() -> None:
    assert usual_rain(NORMALS, date(2026, 1, 1), date(2026, 1, 31)) == 31.0
    assert round(usual_rain(NORMALS, date(2026, 1, 1), date(2026, 2, 14)), 6) == 45.0  # 31 + 14/28 × 28
    assert [classify(p, BANDS) for p in (110, 109.9, 90, 75, 74.9, 50, 49.9, None)] == [
        "extra", "usual", "usual", "little_less", "much_less", "much_less", "very_much_less", None]


def test_month_statuses_count_missing_days_as_missing() -> None:
    rain = {date(2026, 1, d): 2.0 for d in range(1, 21)}  # 20 measured days in January (40 mm), none on 21–31
    rain.update({date(2026, 2, d): 0.5 for d in range(1, 11)})  # 10 days of February up to the 10th
    jan, feb = month_statuses(rain, NORMALS, date(2026, 2, 10), BANDS)
    assert (jan["month"], jan["month_rain_mm"], jan["month_normal_mm"], jan["month_received_pct"]) == ("2026-01", 40.0, 31.0, 129.0)
    assert (jan["month_status"], jan["month_data_days"], jan["month_days"], jan["month_above_record"]) == ("extra", 20, 31, False)
    assert (feb["as_of_date"], feb["month_normal_mm"], feb["month_rain_mm"], feb["month_status"]) == ("2026-02-10", 10.0, 5.0, "much_less")
    assert (feb["ytd_rain_mm"], feb["ytd_normal_mm"], feb["ytd_received_pct"], feb["ytd_difference_mm"]) == (45.0, 41.0, 109.8, 4.0)
    assert (feb["ytd_status"], feb["ytd_data_days"], feb["ytd_days"]) == ("usual", 30, 41)
    # a month with no measured day gets no status (it is not "very much less rain")
    (only,) = month_statuses({}, NORMALS, date(2026, 1, 15), BANDS)
    assert only["month_status"] is None and only["ytd_status"] is None and only["ytd_data_days"] == 0
    # with a coverage minimum, too few measured days give "insufficient data" instead of a band (rain still shown)
    jan, feb = month_statuses(rain, NORMALS, date(2026, 2, 10), BANDS, coverage_min=80)
    assert (jan["month_status"], jan["month_coverage_pct"], jan["month_rain_mm"]) == ("insufficient_data", 64.5, 40.0)
    assert (feb["month_status"], feb["month_coverage_pct"]) == ("much_less", 100.0)
    assert (feb["ytd_status"], feb["ytd_coverage_pct"], feb["ytd_received_pct"]) == ("insufficient_data", 73.2, 109.8)


def _record(db, plot: FarmPlot, programme_id, record_type: str, day: date, attributes: dict, status: str = "valid") -> None:
    farm = db.get(Farm, plot.farm_id)
    farmer = db.get(Farmer, farm.farmer_id)
    db.add(FieldRecord(programme_id=programme_id, module_code="climate", record_type=record_type, farm_plot_id=plot.id,
                       farmer_id=farmer.id, geo_unit_id=farmer.geo_unit_id, data_nature="observed",
                       observed_at=datetime.combine(day, time(12), IST), attributes=attributes,
                       submission_status="imported", validation_status=status))


def test_rainfall_status_source(api, db, programme_id) -> None:
    plots = list(db.scalars(select(FarmPlot).where(FarmPlot.is_demo == true()).order_by(FarmPlot.plot_code)).unique())
    plot, without_rain = plots[0], plots[1]
    body = {"source_code": "t_rain_status", "name": "Rainfall status", "module_code": "climate", "source_type": "rest_pull",
            "adapter_code": "platform_rainfall_status", "is_mock": False,
            "config": {"programme_code": "CCRL-INCLUSIVE", "as_of_date": "2026-02-10"}}
    assert api.as_("admin").post("/integrations/sources", json=body).status_code == 201
    r = api.as_("admin").post("/integrations/t_rain_status/pull").json()
    assert r["error"]["code"] == "no_climate_normals"

    normals = {"source_name": "Test normals", "rainfall_dataset": "CHIRPS test", "period_start_year": 1996,
               "period_end_year": 2025, "annual_rain_mm": 365.0, **NORMALS}
    for p in (plot, without_rain):
        _record(db, p, programme_id, "climate_normals", date(2025, 12, 31), normals)
    for d in range(1, 21):
        _record(db, plot, programme_id, "climate_observation", date(2026, 1, d),
                {"source_name": "Station A", "station_ref": "ST-A", "rainfall_mm": 2.0})
    for d in range(1, 11):
        _record(db, plot, programme_id, "climate_observation", date(2026, 2, d),
                {"source_name": "Station A", "station_ref": "ST-A", "rainfall_mm": 0.5})
    # a second station with fewer days is never mixed in; an unvalidated reading is never used
    _record(db, plot, programme_id, "climate_observation", date(2026, 1, 5), {"source_name": "B", "station_ref": "ST-B",
                                                                               "rainfall_mm": 90.0})
    _record(db, plot, programme_id, "climate_observation", date(2026, 1, 25),
            {"source_name": "Station A", "station_ref": "ST-A", "rainfall_mm": 400.0}, status="exception")
    db.flush()

    run = api.as_("admin").post("/integrations/t_rain_status/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_rejected"]) == (2, 0), run  # Jan + Feb for the plot with rain only
    source_id = db.scalar(select(IntegrationSource.id).where(IntegrationSource.source_code == "t_rain_status"))
    rows = {r.attributes["month"]: r for r in db.scalars(select(FieldRecord).where(
        FieldRecord.source_system_id == source_id)).unique()}
    feb = rows["2026-02"]
    assert feb.record_type == "rainfall_status" and feb.data_nature == "calculated" and feb.farm_plot_id == plot.id
    assert float(feb.value) == 109.8 and feb.unit == "%" and feb.validation_status == "valid"
    a = feb.attributes  # 30 of 41 days measured (73 %) is below the provisional 80 % minimum
    assert (a["ytd_rain_mm"], a["ytd_status"], a["station_ref"], a["ytd_data_days"]) == (45.0, "insufficient_data", "ST-A", 30)
    assert (a["month_status"], a["min_coverage_pct"]) == ("much_less", 80.0)
    assert a["method_version"] == "rainfall-status-1.1" and "CHIRPS test 1996–2025" == a["normals_dataset"]
    assert feb.observed_at.astimezone(IST).date() == date(2026, 2, 10)

    run = api.as_("admin").post("/integrations/t_rain_status/pull").json()["sync_run"]
    assert (run["records_accepted"], run["records_duplicate"]) == (0, 2)  # re-running changes nothing

    db.get(SystemSetting, "climate.rainfall_status_min_coverage_pct").value = 70  # the programme lowers the minimum
    db.flush()
    run = api.as_("admin").post("/integrations/t_rain_status/pull").json()["sync_run"]
    assert run["records_accepted"] == 2  # both months recalculated with the new rule
    db.expire_all()
    assert db.get(FieldRecord, feb.id).attributes["ytd_status"] == "usual"
