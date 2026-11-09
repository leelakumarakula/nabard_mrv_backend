"""Workstream modules and their record types.

Each record type declares the nature of its data so the UI and calculations never present a model prediction
as a confirmed field condition, and never treat a reported action as an observed one.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

DataNature = Literal["observed", "user_reported", "model_prediction", "calculated", "assessment"]

NATURE_LABELS: dict[str, str] = {
    "observed": "Observed / measured fact",
    "user_reported": "Reported by user (not independently observed)",
    "model_prediction": "Model prediction – risk estimate, not a confirmed field condition",
    "calculated": "Calculated output (external engine or documented platform method)",
    "assessment": "Expert/technical assessment",
}

RISK_LEVELS = ["low", "moderate", "high", "very_high"]
PESTS = ["white_stem_borer", "coffee_berry_borer", "leaf_rust", "black_rot", "mealybug", "root_rot",
         # coffee pests and diseases named by the FarmFuture advisory and scouting services
         "shot_hole_borer", "stalk_rot", "leaf_miner", "cercospora_leaf_spot", "phoma", "anthracnose", "berry_blotch",
         "other"]
SENSOR_PARAMETERS = ["soil_moisture", "soil_temperature", "air_temperature", "humidity", "rainfall", "ph", "ec",
                     "nitrogen", "phosphorus", "potassium", "air_pressure", "wind_speed", "leaf_wetness", "light_intensity"]
SENSOR_UNITS = ["%", "°C", "mm", "pH", "dS/m", "µS/cm", "mg/kg", "kg/ha", "hPa", "m/s", "lux", "raw"]
MONTHS = [("jan", "January"), ("feb", "February"), ("mar", "March"), ("apr", "April"), ("may", "May"), ("jun", "June"),
          ("jul", "July"), ("aug", "August"), ("sep", "September"), ("oct", "October"), ("nov", "November"),
          ("dec", "December")]
# Rainfall status bands, wettest first; thresholds come from the setting climate.rainfall_status_bands.
RAINFALL_STATUSES = ["extra", "usual", "little_less", "much_less", "very_much_less"]
# A status is only given when enough days were measured (setting climate.rainfall_status_min_coverage_pct).
RAINFALL_STATUS_OPTIONS = [*RAINFALL_STATUSES, "insufficient_data"]
# Processing series of a device reading: "app" = the values the device platform shows its users (FarmFuture's daily
# statistics), "raw" = uncorrected sensor output, "calibrated" = device calibration applied, "lab_anchored" = scaled to
# a laboratory soil test. Which series counts for reporting is a pending programme decision (FARMFUTURE_SENSOR_UNITS).
SENSOR_SERIES = ["app", "raw", "calibrated", "lab_anchored"]
# Physically plausible range per (parameter, unit). A reading outside it is kept but marked as an exception for an
# analyst to review (a sensor fault is more likely than a real value) – it is never silently accepted or dropped.
SENSOR_PLAUSIBLE_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("ph", "pH"): (3.5, 9.5),
    ("soil_temperature", "°C"): (0, 50),
    ("air_temperature", "°C"): (-5, 48),
    ("soil_moisture", "%"): (0, 100),
    ("humidity", "%"): (0, 100),
    ("rainfall", "mm"): (0, 500),
    ("ec", "µS/cm"): (0, 20000),
    ("ec", "dS/m"): (0, 20),
    ("nitrogen", "kg/ha"): (0, 3000),
    ("phosphorus", "kg/ha"): (0, 3000),
    ("potassium", "kg/ha"): (0, 3000),
    ("air_pressure", "hPa"): (600, 1100),
    ("wind_speed", "m/s"): (0, 60),
    ("light_intensity", "lux"): (0, 200000),
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    type: Literal["number", "integer", "text", "textarea", "select", "date", "boolean"]
    required: bool = False
    unit: str | None = None
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None
    help: str | None = None


@dataclass(frozen=True)
class RecordType:
    code: str
    module_code: str
    name: str
    description: str
    data_nature: DataNature
    fields: list[FieldSpec]
    requires_plot: bool = False
    requires_farmer: bool = False
    requires_device: bool = False
    requires_organisation: bool = False
    supports_participants: bool = False
    requires_demonstration_site: bool = False
    supports_surveillance_cluster: bool = False
    value_field: str | None = None  # attribute copied to FieldRecord.value for aggregation
    unit_field: str | None = None  # attribute holding the unit, or None to use the value field's fixed unit
    default_activity_code: str | None = None
    caution: str | None = None
    long_term_baseline: bool = False  # describes a past multi-year period (e.g. 30-year normals): no recency check
    rules: list[Callable[[dict[str, Any]], str | None]] = field(default_factory=list)


@dataclass(frozen=True)
class Module:
    code: str
    number: int
    name: str
    description: str


MODULES: list[Module] = [
    Module("climate", 1, "Climate Intelligence & Early Warning",
           "Climate observations, risk assessments, alerts and advisories, delivery and farmer response."),
    Module("water", 2, "Soil & Water Management and Conservation",
           "Water balance and irrigation guidance, soil-moisture and water-use tracking, moisture retention, mulching and "
           "ground cover, contour-aligned soil conservation and erosion control."),
    Module("pest", 3, "Pest & Disease Risk Management",
           "Risk predictions and hotspots, field scouting in pest surveillance clusters, observed incidence and severity, "
           "preventive management and treatment records."),
    Module("soil", 4, "Soil & Microclimate Monitoring",
           "Sensor and soil-test readings with device, unit, timestamp, calibration and data quality."),
    Module("suitability", 5, "Climate & Land Suitability / Plantation Planning",
           "Agroecological and climate suitability, site assessments, plantation planning, existing plantation condition, "
           "young plantation survival and shade/canopy management. Assessment is not proof of establishment."),
    Module("farmer_fpo", 6, "Farmer & Community Implementation",
           "Farmer/FPO and grower-group registration, training, demonstrations, advisory delivery and adoption, women and "
           "tribal community participation and community resource persons."),
    Module("mrv", 7, "MRV & Verification",
           "Baselines, indicators, monitoring, evidence, calculations, reviews, findings, decisions and reports."),
]
MODULES_BY_CODE = {m.code: m for m in MODULES}


def _date_order(start: str, end: str) -> Callable[[dict[str, Any]], str | None]:
    def rule(a: dict[str, Any]) -> str | None:
        if a.get(start) and a.get(end) and str(a[end]) < str(a[start]):
            return f"'{end}' must be on or after '{start}'"
        return None
    return rule


def _not_more_than(part: str, whole: str, message: str) -> Callable[[dict[str, Any]], str | None]:
    def rule(a: dict[str, Any]) -> str | None:
        if a.get(part) is not None and a.get(whole) is not None and a[part] > a[whole]:
            return message
        return None
    return rule


def _affected_le_inspected(a: dict[str, Any]) -> str | None:
    if a.get("plants_affected") is not None and a.get("plants_inspected") is not None:
        if a["plants_affected"] > a["plants_inspected"]:
            return "Plants affected cannot exceed plants inspected"
    return None


RECORD_TYPES: list[RecordType] = [
    # 1. Climate
    RecordType(
        "climate_observation", "climate", "Climate observation",
        "Weather/climate values from an identified source (e.g. IMD, KVK, RainSync/MicroClime device).",
        "observed",
        [FieldSpec("source_name", "Source", "text", required=True, help="e.g. IMD station, KVK, device"),
         FieldSpec("station_ref", "Station / device reference", "text"),
         FieldSpec("rainfall_mm", "Rainfall", "number", unit="mm", min=0, max=1000),
         FieldSpec("temp_max_c", "Max temperature", "number", unit="°C", min=-10, max=55),
         FieldSpec("temp_min_c", "Min temperature", "number", unit="°C", min=-15, max=45),
         FieldSpec("relative_humidity_pct", "Relative humidity", "number", unit="%", min=0, max=100)],
        value_field="rainfall_mm",
    ),
    RecordType(
        "climate_risk_assessment", "climate", "Climate risk assessment",
        "Drought, heat or extreme-rainfall risk estimate for an area and validity window.",
        "model_prediction",
        [FieldSpec("hazard_type", "Hazard", "select", required=True,
                   options=["drought", "heat", "excess_rainfall", "cold", "wind"]),
         FieldSpec("risk_level", "Risk level", "select", required=True, options=RISK_LEVELS),
         FieldSpec("valid_from", "Valid from", "date", required=True),
         FieldSpec("valid_to", "Valid to", "date", required=True),
         FieldSpec("model_name", "Model / engine", "text", required=True),
         FieldSpec("model_version", "Model version", "text"),
         FieldSpec("confidence", "Confidence note", "text")],
        caution="Risk estimates are predictions; record observed events and farmer actions separately.",
        rules=[_date_order("valid_from", "valid_to")],
    ),
    RecordType(
        "weather_estimate", "climate", "Gridded weather estimate",
        "Daily weather for the plot location from a gridded model/satellite dataset (e.g. Open-Meteo, NASA POWER).",
        "calculated",
        [FieldSpec("source_name", "Source", "text", required=True),
         FieldSpec("dataset", "Dataset / model", "text", required=True),
         FieldSpec("grid_latitude", "Grid latitude", "number", min=-90, max=90),
         FieldSpec("grid_longitude", "Grid longitude", "number", min=-180, max=180),
         FieldSpec("rainfall_mm", "Rainfall", "number", unit="mm", min=0, max=1000),
         FieldSpec("temp_max_c", "Max temperature", "number", unit="°C", min=-10, max=55),
         FieldSpec("temp_min_c", "Min temperature", "number", unit="°C", min=-15, max=45),
         FieldSpec("relative_humidity_pct", "Relative humidity", "number", unit="%", min=0, max=100)],
        value_field="rainfall_mm",
        caution="A gridded estimate for the plot location, not a station or on-farm measurement.",
    ),
    RecordType(
        "weather_forecast", "climate", "Weather forecast",
        "Daily weather forecast for the plot location from a forecast model (e.g. Open-Meteo).",
        "model_prediction",
        [FieldSpec("source_name", "Source", "text", required=True),
         FieldSpec("dataset", "Forecast model", "text", required=True),
         FieldSpec("forecast_date", "Forecast for", "date", required=True),
         FieldSpec("issued_on", "Issued on", "date", required=True),
         FieldSpec("rainfall_mm", "Rainfall", "number", unit="mm", min=0, max=1000),
         FieldSpec("temp_max_c", "Max temperature", "number", unit="°C", min=-10, max=55),
         FieldSpec("temp_min_c", "Min temperature", "number", unit="°C", min=-15, max=45),
         FieldSpec("relative_humidity_pct", "Relative humidity", "number", unit="%", min=0, max=100)],
        value_field="rainfall_mm",
        caution="A forecast is a prediction; record observed weather and farmer actions separately.",
        rules=[_date_order("issued_on", "forecast_date")],
    ),
    RecordType(
        "climate_normals", "climate", "30-year climate normals",
        "Long-term average climate of the plot location (e.g. 1996–2025) from a named gridded dataset: monthly and "
        "annual rain, temperature and dry spells. The baseline that observed rain is compared with.",
        "calculated",
        [FieldSpec("source_name", "Source", "text", required=True),
         FieldSpec("rainfall_dataset", "Rainfall dataset", "text", required=True),
         FieldSpec("temperature_dataset", "Temperature dataset", "text"),
         FieldSpec("period_start_year", "First year", "integer", required=True, min=1900, max=2100),
         FieldSpec("period_end_year", "Last year", "integer", required=True, min=1900, max=2100),
         FieldSpec("annual_rain_mm", "Average yearly rain", "number", unit="mm", required=True, min=0, max=15000),
         FieldSpec("annual_rain_sd_mm", "Year-to-year variation (standard deviation)", "number", unit="mm", min=0),
         FieldSpec("annual_rain_cv_pct", "Year-to-year variation (coefficient)", "number", unit="%", min=0),
         *[FieldSpec(f"rain_{m}_mm", f"Average rain – {name}", "number", unit="mm", required=True, min=0, max=5000)
           for m, name in MONTHS],
         *[FieldSpec(f"rain_{m}_max_mm", f"Wettest {name} on record", "number", unit="mm", min=0, max=8000)
           for m, name in MONTHS],
         FieldSpec("monsoon_share_pct", "Share of yearly rain in June–September", "number", unit="%", min=0, max=100),
         FieldSpec("mean_temp_c", "Average temperature", "number", unit="°C", min=-20, max=45),
         FieldSpec("mean_min_temp_c", "Average daily minimum", "number", unit="°C", min=-30, max=45),
         FieldSpec("mean_max_temp_c", "Average daily maximum", "number", unit="°C", min=-20, max=55),
         FieldSpec("dry_day_threshold_mm", "Dry day = rain below", "number", unit="mm", min=0, max=10),
         FieldSpec("dry_days_per_year", "Dry days per year", "number", unit="days", min=0, max=366),
         FieldSpec("usual_longest_dry_spell_days", "Usual longest dry spell", "number", unit="days", min=0, max=366),
         FieldSpec("longest_dry_spell_days", "Longest dry spell on record", "number", unit="days", min=0, max=366),
         FieldSpec("longest_dry_spell_year", "Year of the longest dry spell", "integer", min=1900, max=2100)],
        requires_plot=True, value_field="annual_rain_mm",
        caution="A long-term average from a gridded dataset, not an observation at the farm.", long_term_baseline=True,
        rules=[_not_more_than("period_start_year", "period_end_year", "The first year cannot be after the last year")],
    ),
    RecordType(
        "rainfall_status", "climate", "Rainfall status vs 30-year normal",
        "Rain measured at the farm's weather station compared with the 30-year normal for the same days – for the month "
        "and for the year to date – and the resulting status band (extra rain … very much less rain).",
        "calculated",
        [FieldSpec("month", "Month", "text", required=True, help="YYYY-MM"),
         FieldSpec("as_of_date", "Rain counted up to", "date", required=True),
         FieldSpec("station_ref", "Rain station", "text"),
         FieldSpec("month_rain_mm", "Rain this month", "number", unit="mm", min=0),
         FieldSpec("month_normal_mm", "Usual rain for these days", "number", unit="mm", min=0),
         FieldSpec("month_received_pct", "Month: share of usual rain", "number", unit="%", min=0),
         FieldSpec("month_status", "Month status", "select", options=RAINFALL_STATUS_OPTIONS),
         FieldSpec("month_coverage_pct", "Month: share of days measured", "number", unit="%", min=0, max=100),
         FieldSpec("month_data_days", "Month: days with measured rain", "integer", min=0, max=31),
         FieldSpec("month_days", "Month: days counted", "integer", min=0, max=31),
         FieldSpec("month_above_record", "Month above the 30-year record", "boolean"),
         FieldSpec("ytd_rain_mm", "Rain since 1 January", "number", unit="mm", required=True, min=0),
         FieldSpec("ytd_normal_mm", "Usual rain since 1 January", "number", unit="mm", required=True, min=0),
         FieldSpec("ytd_received_pct", "Year to date: share of usual rain", "number", unit="%", min=0),
         FieldSpec("ytd_difference_mm", "Year to date: rain minus usual (negative = missing rain)", "number", unit="mm"),
         FieldSpec("ytd_status", "Year-to-date status", "select", options=RAINFALL_STATUS_OPTIONS),
         FieldSpec("ytd_coverage_pct", "Year to date: share of days measured", "number", unit="%", min=0, max=100),
         FieldSpec("ytd_data_days", "Year to date: days with measured rain", "integer", min=0, max=366),
         FieldSpec("ytd_days", "Year to date: days counted", "integer", min=0, max=366),
         FieldSpec("normals_ref", "Normals record used", "text"),
         FieldSpec("normals_dataset", "Normals dataset and period", "text"),
         FieldSpec("method_version", "Method version", "text", required=True),
         FieldSpec("bands_used", "Status bands used", "text"),
         FieldSpec("min_coverage_pct", "Least share of days measured for a status", "number", unit="%", min=0, max=100)],
        requires_plot=True, value_field="ytd_received_pct",
        caution="Calculated by the platform from observed station rain and a gridded 30-year normal. Days without a "
                "station reading count as missing, not as dry; with too few measured days the status is 'insufficient "
                "data'. The bands and the coverage rule are provisional until the programme approves them "
                "(RAINFALL_STATUS_BANDS).",
    ),
    # 2. Water
    RecordType(
        "water_measurement", "water", "Water / soil-moisture measurement",
        "Measured water use, soil moisture or water-source level at a plot.",
        "observed",
        [FieldSpec("parameter", "Parameter", "select", required=True,
                   options=["irrigation_volume", "soil_moisture", "water_source_level", "rainfall"]),
         FieldSpec("value", "Value", "number", required=True, min=0),
         FieldSpec("unit", "Unit", "select", required=True, options=["litre", "m3", "%", "mm", "cm"]),
         FieldSpec("method", "Method", "select", required=True,
                   options=["meter_reading", "probe", "manual_gauge", "sensor", "other"])],
        requires_plot=True, value_field="value", unit_field="unit", default_activity_code="WATER_MANAGEMENT",
    ),
    RecordType(
        "irrigation_event", "water", "Irrigation event",
        "An irrigation application reported for a plot.",
        "user_reported",
        [FieldSpec("irrigation_method", "Method", "select", required=True,
                   options=["drip", "sprinkler", "hose", "flood", "other"]),
         FieldSpec("duration_hours", "Duration", "number", unit="hours", min=0, max=48),
         FieldSpec("volume", "Volume applied", "number", min=0),
         FieldSpec("volume_unit", "Volume unit", "select", options=["litre", "m3"]),
         FieldSpec("water_source", "Water source", "select", options=["well", "tank", "stream", "pond", "canal", "other"])],
        requires_plot=True, value_field="volume", unit_field="volume_unit", default_activity_code="WATER_MANAGEMENT",
    ),
    RecordType(
        "water_balance_output", "water", "Water-balance engine output",
        "Output supplied by an approved water-balancing engine. Not entered as a measurement.",
        "calculated",
        [FieldSpec("engine_name", "Engine", "text", required=True),
         FieldSpec("engine_version", "Engine version", "text", required=True),
         FieldSpec("et0_mm", "Reference ET0", "number", unit="mm", min=0, max=20),
         FieldSpec("crop_water_requirement_mm", "Crop water requirement", "number", unit="mm", min=0),
         FieldSpec("recommended_irrigation_mm", "Recommended irrigation", "number", unit="mm", min=0),
         # Optional detail supplied by the FarmFuture Water Balance Engine (daily root-zone water account)
         FieldSpec("source_farm_ref", "Engine farm reference", "text"),
         FieldSpec("irrigation_status", "Engine irrigation status", "select",
                   options=["IRRIGATE_NOW", "ROUND_DUE", "IRRIGATE_BY", "HOLD", "NO_IRRIGATION", "NO_DATA"]),
         FieldSpec("advice", "Engine advice", "text"),
         FieldSpec("kc", "Crop coefficient (Kc)", "number", min=0, max=2),
         FieldSpec("rain_mm", "Rainfall", "number", unit="mm", min=0, max=1000),
         FieldSpec("effective_rain_mm", "Effective rainfall", "number", unit="mm", min=0, max=1000),
         FieldSpec("irrigation_applied_mm", "Irrigation applied", "number", unit="mm", min=0),
         FieldSpec("irrigation_effective_mm", "Effective irrigation", "number", unit="mm", min=0),
         FieldSpec("runoff_mm", "Runoff", "number", unit="mm", min=0),
         FieldSpec("drained_mm", "Deep drainage", "number", unit="mm", min=0),
         FieldSpec("net_change_mm", "Net change in root-zone water", "number", unit="mm"),
         FieldSpec("capacity_mm", "Root-zone usable water capacity (TAW)", "number", unit="mm", min=0),
         FieldSpec("usable_water_pct", "Water left (% of usable water)", "number", unit="%", min=0, max=100),
         FieldSpec("stress_mark_pct", "Stress mark (% of usable water)", "number", unit="%", min=0, max=100),
         FieldSpec("reserve_mm", "Water in reserve before stress", "number", unit="mm", min=0),
         FieldSpec("reserve_days", "Days of reserve before stress", "number", unit="days", min=0),
         FieldSpec("first_needs_water_date", "First forecast day needing water", "date"),
         FieldSpec("forecast_horizon_days", "Forecast horizon", "integer", unit="days", min=0, max=30),
         FieldSpec("water_needed_m3", "Water needed for the farm", "number", unit="m³", min=0),
         FieldSpec("sensor_reading_pct", "Sensor soil moisture (VWC)", "number", unit="%", min=0, max=100),
         FieldSpec("model_soil_water_pct", "Engine soil-moisture estimate (VWC)", "number", unit="%", min=0, max=100),
         FieldSpec("sensor_agrees", "Sensor agrees with engine", "boolean"),
         FieldSpec("growth_stage", "Growth stage used", "text"),
         FieldSpec("growth_stage_estimated", "Growth stage estimated (not recorded)", "boolean"),
         FieldSpec("sensor_difference_pct", "Sensor minus engine soil moisture", "number", unit="% points",
                   min=-100, max=100),
         FieldSpec("sensor_calibrated", "Soil sensor calibrated in the engine", "boolean"),
         FieldSpec("blocks_total", "Blocks in the plan", "integer", min=0),
         FieldSpec("blocks_needing_water", "Blocks needing water in the forecast window", "integer", min=0),
         FieldSpec("needs_water_by", "Earliest block irrigation deadline", "date"),
         FieldSpec("season_label", "Irrigation season used by the engine", "text"),
         FieldSpec("plan_tier", "Engine input tier", "text", help="e.g. FULL = the farm's own weather and soil readings"),
         FieldSpec("data_mode", "Engine data mode", "text", help="e.g. MEASURED or ESTIMATED inputs"),
         FieldSpec("confidence", "Engine confidence", "text"),
         FieldSpec("confidence_score", "Engine confidence score", "integer", min=0, max=100)],
        requires_plot=True, value_field="recommended_irrigation_mm",
        caution="Show only when supplied by an approved engine.",
    ),
    # 3. Pest & disease
    RecordType(
        "pest_risk_estimate", "pest", "Pest/disease risk estimate",
        "Predicted pest or disease risk for an area or plot.",
        "model_prediction",
        [FieldSpec("pest_or_disease", "Pest / disease", "select", required=True, options=PESTS),
         FieldSpec("risk_level", "Risk level", "select", required=True, options=RISK_LEVELS),
         FieldSpec("valid_from", "Valid from", "date", required=True),
         FieldSpec("valid_to", "Valid to", "date", required=True),
         FieldSpec("model_name", "Model / engine", "text", required=True),
         # filled by the FarmFuture Farm Health screen (coffee_pest_screen) – in FarmFuture's own words
         FieldSpec("screening_status", "Screening status", "text", help="e.g. 'Rust conditions · inspect'"),
         FieldSpec("inspect_now", "Inspection triggered", "boolean",
                   help="The weather or field evidence asks for an inspection now"),
         FieldSpec("screening_topic", "Screening topic", "text"),
         FieldSpec("why", "Why", "textarea"),
         FieldSpec("what_to_check", "What to check in the field", "textarea"),
         FieldSpec("next_check_by", "Next check by", "date"),
         FieldSpec("missing_evidence", "Evidence still missing", "textarea"),
         FieldSpec("on_seasonal_watch_list", "On this month's seasonal watch list", "boolean"),
         FieldSpec("reference", "Reference", "text")],
        caution="Predictions are risk estimates, not diagnoses; confirm with field inspection.",
        rules=[_date_order("valid_from", "valid_to")],
    ),
    RecordType(
        "pest_inspection", "pest", "Scouting / inspection",
        "Field scouting with observed incidence and severity.",
        "observed",
        [FieldSpec("pest_or_disease", "Pest / disease", "select", required=True, options=PESTS),
         FieldSpec("plants_inspected", "Plants inspected", "integer", required=True, min=1),
         FieldSpec("plants_affected", "Plants affected", "integer", required=True, min=0),
         FieldSpec("severity_score", "Severity (0–5)", "integer", min=0, max=5,
                   help="Scale definition requires programme confirmation"),
         FieldSpec("scouting_method", "Scouting method", "text"),
         FieldSpec("recommended_action", "Recommended action", "textarea"),
         FieldSpec("pest_other_name", "Pest / disease name (when 'other')", "text")],
        requires_plot=True, value_field="incidence_pct", default_activity_code="PEST_SURVEILLANCE",
        supports_surveillance_cluster=True, rules=[_affected_le_inspected],
    ),
    # 4. Soil & microclimate
    RecordType(
        "soil_test", "soil", "Soil test",
        "Laboratory or field-kit soil test result for a plot.",
        "observed",
        [FieldSpec("lab_name", "Laboratory / kit", "text", required=True),
         FieldSpec("sample_depth_cm", "Sample depth", "number", unit="cm", min=0, max=200),
         FieldSpec("ph", "pH", "number", min=3, max=10),
         FieldSpec("organic_carbon_pct", "Organic carbon", "number", unit="%", min=0, max=20),
         FieldSpec("nitrogen_kg_ha", "Available N", "number", unit="kg/ha", min=0),
         FieldSpec("phosphorus_kg_ha", "Available P", "number", unit="kg/ha", min=0),
         FieldSpec("potassium_kg_ha", "Available K", "number", unit="kg/ha", min=0),
         FieldSpec("ec_ds_m", "Electrical conductivity", "number", unit="dS/m", min=0, max=20),
         # Oxide basis as reported by some labs (e.g. FarmFuture soil reports); kept as reported, never converted.
         FieldSpec("p2o5_kg_ha", "Available P₂O₅ (oxide basis)", "number", unit="kg/ha", min=0),
         FieldSpec("k2o_kg_ha", "Available K₂O (oxide basis)", "number", unit="kg/ha", min=0),
         FieldSpec("report_ref", "Lab report number", "text")],
        requires_plot=True, value_field="ph", default_activity_code="SOIL_HEALTH",
    ),
    RecordType(
        "sensor_observation", "soil", "Sensor observation",
        "A reading from a registered IoT device (SoilSync, Moisture Probe, MicroClime, RainSync).",
        "observed",
        [FieldSpec("parameter", "Parameter", "select", required=True, options=SENSOR_PARAMETERS),
         FieldSpec("value", "Value", "number", required=True),
         FieldSpec("unit", "Unit", "select", required=True, options=SENSOR_UNITS),
         FieldSpec("quality_flag", "Device quality flag", "select", options=["ok", "suspect", "out_of_range", "calibration_due"]),
         # Optional detail for summarised readings (e.g. FarmFuture daily device statistics)
         FieldSpec("aggregation", "Aggregation", "select", options=["reading", "daily_mean", "daily_max"],
                   help="A single reading, or a daily summary of the device's readings"),
         FieldSpec("value_min", "Minimum in period", "number"),
         FieldSpec("value_max", "Maximum in period", "number"),
         FieldSpec("layer", "Sensor layer / depth", "text", help="e.g. L1 / L2 for two-depth soil-moisture probes"),
         FieldSpec("source_dataset", "Source dataset", "text"),
         FieldSpec("series", "Sensor data series", "select", options=SENSOR_SERIES,
                   help="Which processing of the device's readings this value comes from (the series used for "
                        "reporting is a pending programme decision)")],
        requires_device=True, value_field="value", unit_field="unit",
    ),
    RecordType(
        "device_health", "soil", "Device health snapshot",
        "Battery, signal and last-reading time of a registered device as reported by its platform – one snapshot per "
        "device and day. Used for data-completeness and maintenance follow-up, not as a field measurement.",
        "observed",
        [FieldSpec("battery_pct", "Battery", "number", unit="%", min=0, max=100),
         FieldSpec("signal_csq", "Signal strength (CSQ 0–31)", "integer", min=0, max=31,
                   help="Cellular signal quality: 0–9 marginal, 10–14 OK, 15–19 good, 20–31 excellent"),
         FieldSpec("reporting_now", "Reading received in the last 24 hours", "boolean"),
         FieldSpec("source_name", "Reported by", "text", required=True)],
        requires_device=True, value_field="battery_pct",
        caution="The time of the record is the device's last reading. A battery of 0 % while readings still arrive "
                "usually means the battery sensor is not wired, not an empty battery.",
    ),
    # 5. Suitability
    RecordType(
        "suitability_assessment", "suitability", "Suitability assessment",
        "Climate and land suitability assessment for an area or site.",
        "assessment",
        [FieldSpec("assessment_area", "Area / site name", "text", required=True),
         FieldSpec("suitability_class", "Suitability class", "select", required=True,
                   options=["highly_suitable", "moderately_suitable", "marginally_suitable", "not_suitable"]),
         FieldSpec("elevation_m", "Elevation", "number", unit="m", min=0, max=3000),
         FieldSpec("key_constraints", "Key constraints", "textarea"),
         FieldSpec("method", "Assessment method", "text", required=True),
         FieldSpec("assessor", "Assessor", "text", required=True),
         FieldSpec("used_in_planning", "Used in a planning decision", "boolean")],
        default_activity_code="SUITABILITY_ASSESSMENT",
        caution="An assessment does not mean the programme funds plantation establishment.",
    ),
    RecordType(
        "site_finding", "suitability", "Site finding",
        "An observation recorded during a site visit.",
        "observed",
        [FieldSpec("finding_type", "Finding type", "select", required=True,
                   options=["soil", "water", "slope", "shade", "access", "other"]),
         FieldSpec("description", "Description", "textarea", required=True),
         FieldSpec("recommendation", "Recommendation", "textarea")],
        default_activity_code="SUITABILITY_ASSESSMENT",
    ),
    # 6. Farmer & FPO
    RecordType(
        "training_session", "farmer_fpo", "Training session",
        "FPO/farmer training with attendance.",
        "user_reported",
        [FieldSpec("topic", "Topic", "text", required=True),
         FieldSpec("trainer", "Trainer", "text", required=True),
         FieldSpec("mode", "Mode", "select", required=True, options=["classroom", "field", "demonstration", "online"]),
         FieldSpec("duration_hours", "Duration", "number", unit="hours", min=0, max=40)],
        requires_organisation=True, supports_participants=True, default_activity_code="TRAINING",
        caution="Attendance alone does not demonstrate competency or capacity outcomes.",
    ),
    RecordType(
        "demonstration", "farmer_fpo", "Field demonstration",
        "Practice demonstration on a farmer's plot.",
        "user_reported",
        [FieldSpec("practice_demonstrated", "Practice", "text", required=True),
         FieldSpec("host_notes", "Notes", "textarea")],
        requires_organisation=True, supports_participants=True, default_activity_code="DEMONSTRATION",
    ),
    RecordType(
        "fpo_capacity_assessment", "farmer_fpo", "FPO capacity assessment",
        "Assessment of an FPO's ability to sustain advisory services.",
        "assessment",
        [FieldSpec("assessment_method", "Method", "text", required=True,
                   help="Competency method must be confirmed before claiming capacity outcomes"),
         FieldSpec("result_summary", "Result summary", "textarea", required=True),
         FieldSpec("follow_up_required", "Follow-up required", "boolean")],
        requires_organisation=True, default_activity_code="FPO_CAPACITY",
    ),
    # V2: soil & water conservation (module 2)
    RecordType(
        "conservation_activity", "water", "Soil & water conservation activity",
        "Mulching, ground cover, contour bunds/terracing, trenches, moisture-retention pits or erosion-control works "
        "observed on a plot.",
        "observed",
        [FieldSpec("practice_type", "Practice", "select", required=True,
                   options=["mulching", "ground_cover", "contour_bunds", "terracing", "staggered_trenches",
                            "moisture_retention_pits", "erosion_control_structure", "drainage_channel"]),
         FieldSpec("area_covered_ha", "Area covered", "number", unit="ha", min=0, max=100),
         FieldSpec("length_m", "Length of structure", "number", unit="m", min=0),
         FieldSpec("structures_count", "Number of structures", "integer", min=0),
         FieldSpec("condition", "Condition", "select", options=["good", "needs_repair", "damaged"]),
         FieldSpec("implemented_by", "Implemented by", "select", options=["farmer", "group_labour", "programme_support"])],
        requires_plot=True, value_field="area_covered_ha", default_activity_code="SOIL_WATER_CONSERVATION",
        caution="An observed structure is not verified adoption until confirmed through independent verification.",
    ),
    # V2: plantation condition and young plantation survival (module 5)
    RecordType(
        "plantation_condition", "suitability", "Existing plantation condition",
        "Condition assessment of an existing coffee plantation, including shade and canopy.",
        "assessment",
        [FieldSpec("overall_condition", "Overall condition", "select", required=True, options=["good", "fair", "poor"]),
         FieldSpec("canopy_cover_pct", "Shade canopy cover", "number", unit="%", min=0, max=100),
         FieldSpec("shade_tree_density_per_ha", "Shade trees", "number", unit="trees/ha", min=0, max=2000),
         FieldSpec("plant_health_note", "Plant health", "textarea"),
         FieldSpec("method", "Assessment method", "text", required=True)],
        requires_plot=True, value_field="canopy_cover_pct", default_activity_code="SHADE_CANOPY",
    ),
    RecordType(
        "site_characteristics", "suitability", "Site characteristics (terrain, soil, erosion)",
        "Terrain, soil and erosion profile of a plot from a farm-profile service (elevation and slope from a terrain model, "
        "soil from soil survey layers, erosion from the RUSLE soil-loss equation). Inputs for suitability and for "
        "targeting soil and water conservation – an assessment, not a field survey.",
        "assessment",
        [FieldSpec("source_name", "Source", "text", required=True),
         FieldSpec("data_basis", "Datasets / method", "text"),
         FieldSpec("elevation_mean_m", "Average elevation", "number", unit="m", min=-100, max=6000),
         FieldSpec("elevation_min_m", "Lowest point", "number", unit="m", min=-100, max=6000),
         FieldSpec("elevation_max_m", "Highest point", "number", unit="m", min=-100, max=6000),
         FieldSpec("slope_mean_deg", "Average slope", "number", unit="°", min=0, max=90),
         FieldSpec("slope_max_deg", "Steepest slope", "number", unit="°", min=0, max=90),
         FieldSpec("slope_class", "Slope class", "text"),
         FieldSpec("steep_share_pct", "Share steeper than 15°", "number", unit="%", min=0, max=100),
         FieldSpec("aspect", "Main aspect", "text"),
         FieldSpec("drainage_class", "Drainage", "text"),
         FieldSpec("frost_risk", "Frost risk", "text"),
         FieldSpec("soil_texture", "Soil texture", "text"),
         FieldSpec("soil_ph", "Soil pH (survey layers)", "number", min=3, max=10),
         FieldSpec("clay_pct", "Clay", "number", unit="%", min=0, max=100),
         FieldSpec("sand_pct", "Sand", "number", unit="%", min=0, max=100),
         FieldSpec("silt_pct", "Silt", "number", unit="%", min=0, max=100),
         FieldSpec("organic_carbon_pct", "Organic carbon", "number", unit="%", min=0, max=20),
         FieldSpec("bulk_density_g_cm3", "Bulk density", "number", unit="g/cm³", min=0, max=3),
         FieldSpec("field_capacity_pct", "Field capacity", "number", unit="%", min=0, max=100),
         FieldSpec("water_holding_mm_per_m", "Water holding capacity", "number", unit="mm/m", min=0, max=1000),
         FieldSpec("water_holding_class", "Water holding class", "text"),
         FieldSpec("erosion_mean_t_ha_yr", "Average soil loss (RUSLE)", "number", unit="t/ha/yr", min=0),
         FieldSpec("erosion_max_t_ha_yr", "Highest soil loss (RUSLE)", "number", unit="t/ha/yr", min=0),
         FieldSpec("erosion_class", "Main erosion class", "text"),
         FieldSpec("erosion_severe_share_pct", "Share in severe or very high erosion", "number", unit="%", min=0, max=100),
         FieldSpec("wetness_index_mean", "Topographic wetness index (average)", "number", min=0, max=50),
         FieldSpec("saturation_prone_pct", "Share prone to waterlogging", "number", unit="%", min=0, max=100),
         FieldSpec("profile_built_on", "Profile built on", "date")],
        requires_plot=True, value_field="erosion_mean_t_ha_yr", long_term_baseline=True,
        caution="Modelled from terrain and soil datasets for the farm outline; confirm on the ground before using it to "
                "select or exclude a site.",
    ),
    RecordType(
        "deforestation_check", "suitability", "No-deforestation screening",
        "Satellite screening of a farm for forest loss since the 31 Dec 2020 cut-off (as used for EUDR), with the "
        "compliance result and what is still missing for a complete due-diligence file.",
        "calculated",
        [FieldSpec("source_name", "Screened by", "text", required=True),
         FieldSpec("screened_on", "Screened on", "date", required=True),
         FieldSpec("compliance_flag", "Result", "text", required=True),
         FieldSpec("compliance_reason", "Reason given", "textarea"),
         FieldSpec("deforested_since_2020_ha", "Forest lost since 2020", "number", unit="ha", min=0),
         FieldSpec("risk_score", "Risk score (screening service scale)", "number", min=0),
         FieldSpec("boundary_mapped", "Farm boundary mapped", "boolean"),
         FieldSpec("report_available", "Screening report available", "boolean"),
         FieldSpec("missing_items", "Still missing for a complete file", "text"),
         FieldSpec("screening_ref", "Screening reference", "text")],
        requires_plot=True, value_field="deforested_since_2020_ha", long_term_baseline=True,
        caution="A remote screening, not a field verification; without a mapped boundary it covers the farm point or "
                "outline the service holds.",
    ),
    RecordType(
        "plantation_survival_check", "suitability", "Young plantation survival check",
        "Survival count for young plants where survival tracking is defined.",
        "observed",
        [FieldSpec("planting_date", "Planting date", "date", required=True),
         FieldSpec("plants_planted", "Plants planted", "integer", required=True, min=1),
         FieldSpec("plants_surviving", "Plants surviving", "integer", required=True, min=0),
         FieldSpec("check_round", "Check round", "select", options=["3_months", "6_months", "12_months", "24_months"])],
        requires_plot=True, value_field="survival_pct", default_activity_code="PLANTATION_SURVIVAL",
        caution="Survival tracking applies only where the programme defines it; it does not imply programme-funded planting.",
        rules=[_not_more_than("plants_surviving", "plants_planted", "Plants surviving cannot exceed plants planted")],
    ),
    # Satellite vegetation index at the plot point (module 5 – plantation condition, shade and canopy)
    RecordType(
        "vegetation_index", "suitability", "Satellite vegetation index",
        "Vegetation greenness (NDVI) at the plot location or averaged over a farm block, from a cloud-free satellite "
        "scene (e.g. Sentinel-2).",
        "calculated",
        [FieldSpec("index", "Index", "select", required=True, options=["ndvi"]),
         FieldSpec("index_value", "Index value", "number", required=True, min=-1, max=1),
         FieldSpec("satellite", "Satellite / collection", "text", required=True),
         FieldSpec("scene_id", "Scene ID", "text", required=True),
         FieldSpec("cloud_cover_pct", "Scene cloud cover", "number", unit="%", min=0, max=100),
         FieldSpec("pixel_class", "Pixel classification", "text"),
         FieldSpec("pixel_size_m", "Pixel size", "number", unit="m", min=0),
         # Block (zone) summaries, e.g. from the FarmFuture satellite service
         FieldSpec("aggregation", "Aggregation", "select", options=["point", "zone_mean"]),
         FieldSpec("zone_ref", "Farm block / zone reference", "text"),
         FieldSpec("index_min", "Lowest cell value in block", "number", min=-1, max=1),
         FieldSpec("index_max", "Highest cell value in block", "number", min=-1, max=1),
         FieldSpec("ndmi_mean", "Moisture index (NDMI), block mean", "number", min=-1, max=1),
         FieldSpec("cells_count", "Satellite cells in block", "integer", min=0),
         FieldSpec("declining_cells", "Cells with declining greenness", "integer", min=0)],
        requires_plot=True, value_field="index_value",
        caution="A satellite index for one pixel at the plot point or the mean over a farm block (both include shade "
                "trees); an indication of canopy greenness, not a field-verified plantation condition.",
    ),
    # V2: demonstration-site visits (module 6 – plantation and soil/moisture demonstrations)
    RecordType(
        "harvest_record", "farmer_fpo", "Harvest record",
        "A harvest recorded by the grower (e.g. in the FarmFuture traceability module): date, quantity, coffee form and "
        "season – production data for yield and livelihood outcomes.",
        "user_reported",
        [FieldSpec("harvest_date", "Harvest date", "date", required=True),
         FieldSpec("harvest_start_date", "Harvest started", "date"),
         FieldSpec("harvest_end_date", "Harvest ended", "date"),
         FieldSpec("gross_quantity", "Gross quantity", "number", required=True, min=0),
         FieldSpec("quantity_unit", "Unit", "select", required=True, options=["kg", "tonne", "bag"]),
         FieldSpec("coffee_form", "Coffee form", "select", options=["cherry", "dried_cherry", "parchment", "green_coffee"]),
         FieldSpec("crop_type", "Crop", "text"),
         FieldSpec("season", "Season", "text", help="e.g. 2026/27"),
         FieldSpec("variety", "Variety", "text"),
         FieldSpec("processing_method", "Processing method", "text"),
         FieldSpec("moisture_pct", "Moisture", "number", unit="%", min=0, max=100),
         FieldSpec("grade", "Grade", "text"),
         FieldSpec("record_status", "Status in the source system", "text", help="e.g. AVAILABLE_FOR_BATCHING, CLOSED"),
         FieldSpec("source_name", "Recorded in", "text")],
        requires_plot=True, value_field="gross_quantity", unit_field="quantity_unit",
        caution="Reported by the grower; bags differ in weight, so compare quantities only in the same unit.",
        rules=[_date_order("harvest_start_date", "harvest_end_date")],
    ),
    RecordType(
        "demonstration_visit", "farmer_fpo", "Demonstration-site visit",
        "Set-up, monitoring visit or field day at a plantation or soil/moisture demonstration site.",
        "observed",
        [FieldSpec("visit_type", "Visit type", "select", required=True, options=["setup", "monitoring", "field_day", "closure"]),
         FieldSpec("practices_observed", "Practices observed", "text", required=True),
         FieldSpec("participants_count", "Participants", "integer", min=0),
         FieldSpec("women_participants_count", "Women participants", "integer", min=0),
         FieldSpec("observations", "Observations", "textarea", required=True)],
        requires_demonstration_site=True, supports_participants=True, default_activity_code="DEMONSTRATION",
        rules=[_not_more_than("women_participants_count", "participants_count",
                              "Women participants cannot exceed participants")],
    ),
]
RECORD_TYPES_BY_CODE = {rt.code: rt for rt in RECORD_TYPES}
# Attributes computed by the platform (never entered); accepted when re-validating stored records.
DERIVED_FIELDS: dict[str, tuple[str, ...]] = {"pest_inspection": ("incidence_pct",),
                                              "plantation_survival_check": ("survival_pct",)}

# Parameters each device type can report. Pending the final IoT equipment schedule (V2 §15) – never assume every
# device measures every parameter.
DEVICE_PARAMETERS: dict[str, set[str]] = {
    "soilsync_7in1": {"soil_moisture", "soil_temperature", "ph", "ec", "nitrogen", "phosphorus", "potassium"},
    "moisture_probe": {"soil_moisture", "soil_temperature"},
    # FarmFuture MicroClime stations also report rain, pressure, wind, leaf wetness and light (seen in live data).
    "microclime": {"air_temperature", "humidity", "rainfall", "air_pressure", "wind_speed", "leaf_wetness",
                   "light_intensity"},
    "rainsync": {"rainfall"},
    # FarmFuture Analogue Weather stations: only the readings whose unit is clear (pressure 0 = not measured; rain is a
    # running total; its soil temperature / moisture channels have no stated unit) – see FARMFUTURE_SENSOR_UNITS.
    "analogue_weather": {"air_temperature", "humidity", "wind_speed", "leaf_wetness", "light_intensity"},
}

ACTIVITIES: list[tuple[str, str, str]] = [
    ("climate", "CLIMATE_ADVISORY", "Climate alerts and advisory dissemination"),
    ("water", "WATER_MANAGEMENT", "Irrigation and water-conservation practices"),
    ("climate", "HEAT_MITIGATION", "Shade and heat-mitigation practices"),
    ("pest", "PEST_SCOUTING", "Pest and disease scouting"),
    ("pest", "PEST_TREATMENT", "Pest and disease treatment or prevention"),
    ("soil", "SOIL_HEALTH", "Soil testing and nutrient management"),
    ("soil", "SOIL_CONSERVATION", "Drainage and soil-conservation measures"),
    ("suitability", "SUITABILITY_ASSESSMENT", "Climate and land suitability assessment"),
    ("farmer_fpo", "TRAINING", "Farmer and FPO training"),
    ("farmer_fpo", "DEMONSTRATION", "Field demonstrations"),
    ("farmer_fpo", "FPO_CAPACITY", "FPO institutional capacity"),
    ("water", "SOIL_WATER_CONSERVATION", "Soil and moisture conservation (all practices)"),
    ("water", "MULCHING", "Mulching"),
    ("water", "GROUND_COVER", "Ground cover / cover crops"),
    ("water", "CONTOUR_CONSERVATION", "Contour-aligned soil conservation (bunds, terraces, trenches)"),
    ("water", "EROSION_CONTROL", "Erosion-control structures"),
    ("water", "MOISTURE_RETENTION", "Moisture-retention practices"),
    ("pest", "PEST_SURVEILLANCE", "Pest surveillance-cluster scouting"),
    ("pest", "PEST_PREVENTION", "Preventive pest and disease management"),
    ("suitability", "SHADE_CANOPY", "Shade and canopy management"),
    ("suitability", "PLANTATION_SURVIVAL", "Young plantation survival tracking"),
    ("farmer_fpo", "CRP_TRAINING", "Community resource person training"),
]
# Activity codes counted as climate-resilient practices / soil-and-moisture conservation by the V2 adoption indicators.
# Default configuration only – the qualifying list requires programme-owner approval.
CLIMATE_RESILIENT_ACTIVITIES = ["WATER_MANAGEMENT", "HEAT_MITIGATION", "SHADE_CANOPY", "SOIL_WATER_CONSERVATION", "MULCHING",
                                "GROUND_COVER", "CONTOUR_CONSERVATION", "EROSION_CONTROL", "MOISTURE_RETENTION",
                                "SOIL_CONSERVATION", "PEST_PREVENTION", "PEST_TREATMENT"]
CONSERVATION_ACTIVITIES = ["SOIL_WATER_CONSERVATION", "MULCHING", "GROUND_COVER", "CONTOUR_CONSERVATION", "EROSION_CONTROL",
                           "MOISTURE_RETENTION", "SOIL_CONSERVATION"]


def validate_attributes(rt: RecordType, attributes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Coerce and check attributes against the record type. Returns (clean attributes, errors)."""
    clean: dict[str, Any] = {}
    errors: list[str] = []
    known = {f.name for f in rt.fields} | set(DERIVED_FIELDS.get(rt.code, ()))
    for name in attributes:
        if name not in known:
            errors.append(f"Unknown field '{name}'")
    for spec in rt.fields:
        raw = attributes.get(spec.name)
        if raw is None or raw == "":
            if spec.required:
                errors.append(f"{spec.label} is required")
            continue
        try:
            if spec.type == "number":
                value: Any = float(raw)
            elif spec.type == "integer":
                if isinstance(raw, float) and not raw.is_integer():
                    raise ValueError
                value = int(raw)
            elif spec.type == "boolean":
                if isinstance(raw, str):
                    value = raw.lower() in ("true", "1", "yes")
                else:
                    value = bool(raw)
            else:
                value = str(raw).strip()
        except (TypeError, ValueError):
            errors.append(f"{spec.label} must be a {spec.type}")
            continue
        if spec.type == "select" and spec.options and value not in spec.options:
            errors.append(f"{spec.label} must be one of: {', '.join(spec.options)}")
            continue
        if spec.type == "date" and not _is_iso_date(value):
            errors.append(f"{spec.label} must be a date (YYYY-MM-DD)")
            continue
        if spec.type in ("number", "integer"):
            if spec.min is not None and value < spec.min:
                errors.append(f"{spec.label} must be ≥ {spec.min:g}")
            if spec.max is not None and value > spec.max:
                errors.append(f"{spec.label} must be ≤ {spec.max:g}")
        clean[spec.name] = value
    if not errors:
        for rule in rt.rules:
            message = rule(clean)
            if message:
                errors.append(message)
    if rt.code == "pest_inspection" and not errors:
        clean["incidence_pct"] = round(100 * clean["plants_affected"] / clean["plants_inspected"], 2)
    if rt.code == "plantation_survival_check" and not errors:
        clean["survival_pct"] = round(100 * clean["plants_surviving"] / clean["plants_planted"], 2)
    return clean, errors


def _is_iso_date(value: str) -> bool:
    from datetime import date
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def value_and_unit(rt: RecordType, attributes: dict[str, Any]) -> tuple[float | None, str | None]:
    if not rt.value_field:
        return None, None
    value = attributes.get(rt.value_field)
    if rt.unit_field:
        unit = attributes.get(rt.unit_field)
    elif rt.value_field in ("incidence_pct", "survival_pct"):
        unit = "%"
    else:
        spec = next((f for f in rt.fields if f.name == rt.value_field), None)
        unit = spec.unit if spec else None
    return value, unit


def describe(rt: RecordType) -> dict[str, Any]:
    return {
        "code": rt.code, "module_code": rt.module_code, "name": rt.name, "description": rt.description,
        "data_nature": rt.data_nature, "data_nature_label": NATURE_LABELS[rt.data_nature],
        "requires_plot": rt.requires_plot, "requires_farmer": rt.requires_farmer or rt.requires_plot,
        "requires_device": rt.requires_device, "requires_organisation": rt.requires_organisation,
        "supports_participants": rt.supports_participants, "default_activity_code": rt.default_activity_code,
        "requires_demonstration_site": rt.requires_demonstration_site,
        "supports_surveillance_cluster": rt.supports_surveillance_cluster,
        "caution": rt.caution,
        "fields": [f.__dict__ for f in rt.fields],
    }
