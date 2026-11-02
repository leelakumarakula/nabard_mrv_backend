from decimal import Decimal

import pytest

from app.services import record_types
from app.services.units import UnitConversionError, area_in_ha, can_convert, convert, normalize_unit


def test_litres_to_cubic_metres() -> None:
    assert convert(Decimal(80000), "litre", "m3") == Decimal("80.000")
    assert convert(Decimal(80000), "L", "m³") == Decimal("80.000")


def test_round_trip_and_aliases() -> None:
    assert normalize_unit("Litres") == "litre"
    assert convert(convert(Decimal("2.5"), "ha", "acre"), "acre", "ha").quantize(Decimal("0.0001")) == Decimal("2.5000")
    assert area_in_ha(Decimal(1), "acre").quantize(Decimal("0.0001")) == Decimal("0.4047")
    assert area_in_ha(None, "ha") == 0


def test_temperature_conversion() -> None:
    assert convert(Decimal(212), "°F", "°C") == Decimal(100)
    assert convert(Decimal(0), "celsius", "degF") == Decimal(32)


def test_unapproved_conversion_raises() -> None:
    assert not can_convert("bucket", "litre")
    with pytest.raises(UnitConversionError):
        convert(Decimal(1), "bucket", "litre")
    with pytest.raises(UnitConversionError):
        convert(Decimal(1), "%", "mm")


def test_record_type_required_select_and_range() -> None:
    rt = record_types.RECORD_TYPES_BY_CODE["soil_test"]
    _, errors = record_types.validate_attributes(rt, {"ph": 12, "unknown": 1})
    assert "Laboratory / kit is required" in errors
    assert any("pH must be ≤ 10" in e for e in errors)
    assert "Unknown field 'unknown'" in errors
    rt = record_types.RECORD_TYPES_BY_CODE["water_measurement"]
    _, errors = record_types.validate_attributes(rt, {"parameter": "x", "value": 1, "unit": "%", "method": "probe"})
    assert any("Parameter must be one of" in e for e in errors)


def test_pest_inspection_cross_field_rule_and_derived_incidence() -> None:
    rt = record_types.RECORD_TYPES_BY_CODE["pest_inspection"]
    _, errors = record_types.validate_attributes(rt, {"pest_or_disease": "leaf_rust", "plants_inspected": 10, "plants_affected": 12})
    assert "Plants affected cannot exceed plants inspected" in errors
    clean, errors = record_types.validate_attributes(rt, {"pest_or_disease": "leaf_rust", "plants_inspected": 40, "plants_affected": 10})
    assert not errors and clean["incidence_pct"] == 25.0
    # Stored records include the derived field and must still validate.
    assert record_types.validate_attributes(rt, clean)[1] == []
    assert record_types.value_and_unit(rt, clean) == (25.0, "%")


def test_prediction_types_are_labelled() -> None:
    for code in ("climate_risk_assessment", "pest_risk_estimate"):
        described = record_types.describe(record_types.RECORD_TYPES_BY_CODE[code])
        assert described["data_nature"] == "model_prediction"
        assert "not a confirmed field condition" in described["data_nature_label"]


def test_every_module_has_record_types_except_mrv() -> None:
    modules = {rt.module_code for rt in record_types.RECORD_TYPES}
    assert modules == {m.code for m in record_types.MODULES} - {"mrv"}
