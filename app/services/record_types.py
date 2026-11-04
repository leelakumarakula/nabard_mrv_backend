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
    "calculated": "Calculated output from an external engine",
    "assessment": "Expert/technical assessment",
}

RISK_LEVELS = ["low", "moderate", "high", "very_high"]
PESTS = ["white_stem_borer", "coffee_berry_borer", "leaf_rust", "black_rot", "mealybug", "root_rot", "other"]


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
         FieldSpec("recommended_irrigation_mm", "Recommended irrigation", "number", unit="mm", min=0)],
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
         FieldSpec("model_name", "Model / engine", "text", required=True)],
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
         FieldSpec("recommended_action", "Recommended action", "textarea")],
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
         FieldSpec("ec_ds_m", "Electrical conductivity", "number", unit="dS/m", min=0, max=20)],
        requires_plot=True, value_field="ph", default_activity_code="SOIL_HEALTH",
    ),
    RecordType(
        "sensor_observation", "soil", "Sensor observation",
        "A reading from a registered IoT device (SoilSync, Moisture Probe, MicroClime, RainSync).",
        "observed",
        [FieldSpec("parameter", "Parameter", "select", required=True,
                   options=["soil_moisture", "soil_temperature", "air_temperature", "humidity", "rainfall", "ph", "ec",
                            "nitrogen", "phosphorus", "potassium"]),
         FieldSpec("value", "Value", "number", required=True),
         FieldSpec("unit", "Unit", "select", required=True, options=["%", "°C", "mm", "pH", "dS/m", "mg/kg"]),
         FieldSpec("quality_flag", "Device quality flag", "select", options=["ok", "suspect", "out_of_range", "calibration_due"])],
        requires_device=True, value_field="value", unit_field="unit",
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
        "Vegetation greenness (NDVI) at the plot location from a cloud-free satellite scene (e.g. Sentinel-2).",
        "calculated",
        [FieldSpec("index", "Index", "select", required=True, options=["ndvi"]),
         FieldSpec("index_value", "Index value", "number", required=True, min=-1, max=1),
         FieldSpec("satellite", "Satellite / collection", "text", required=True),
         FieldSpec("scene_id", "Scene ID", "text", required=True),
         FieldSpec("cloud_cover_pct", "Scene cloud cover", "number", unit="%", min=0, max=100),
         FieldSpec("pixel_class", "Pixel classification", "text"),
         FieldSpec("pixel_size_m", "Pixel size", "number", unit="m", min=0)],
        requires_plot=True, value_field="index_value",
        caution="One satellite pixel at the plot point (it includes shade trees); an indication of canopy greenness, not "
                "a field-verified plantation condition.",
    ),
    # V2: demonstration-site visits (module 6 – plantation and soil/moisture demonstrations)
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
    "microclime": {"air_temperature", "humidity"},
    "rainsync": {"rainfall"},
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
