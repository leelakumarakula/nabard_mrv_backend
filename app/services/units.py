"""Explicit, tested unit conversions. Only conversions listed here are ever applied."""

from decimal import Decimal

ALIASES = {
    "l": "litre", "liter": "litre", "liters": "litre", "litres": "litre", "ltr": "litre",
    "m³": "m3", "cubic_metre": "m3", "cubic_meter": "m3", "cum": "m3",
    "hectare": "ha", "hectares": "ha", "acres": "acre",
    "percent": "%", "pct": "%", "percentage": "%",
    "degc": "°C", "celsius": "°C", "c": "°C", "degf": "°F", "fahrenheit": "°F", "f": "°F",
    "millimetre": "mm", "millimeter": "mm", "centimetre": "cm", "centimeter": "cm",
    "kilogram": "kg", "kgs": "kg", "gram": "g", "grams": "g", "tonnes": "tonne", "t": "tonne",
    "farmers": "farmer", "count": "count", "number": "count", "no.": "count",
}

FACTORS: dict[tuple[str, str], Decimal] = {
    ("litre", "m3"): Decimal("0.001"),
    ("m3", "litre"): Decimal("1000"),
    ("ml", "litre"): Decimal("0.001"),
    ("acre", "ha"): Decimal("0.40468564224"),
    ("ha", "acre"): Decimal("2.4710538147"),
    ("cm", "mm"): Decimal("10"),
    ("mm", "cm"): Decimal("0.1"),
    ("g", "kg"): Decimal("0.001"),
    ("kg", "g"): Decimal("1000"),
    ("kg", "tonne"): Decimal("0.001"),
    ("tonne", "kg"): Decimal("1000"),
}


class UnitConversionError(ValueError):
    pass


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    cleaned = unit.strip()
    return ALIASES.get(cleaned.lower(), cleaned)


def can_convert(from_unit: str | None, to_unit: str | None) -> bool:
    a, b = normalize_unit(from_unit), normalize_unit(to_unit)
    return a == b or (a, b) in FACTORS or {a, b} == {"°C", "°F"}


def convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal:
    a, b = normalize_unit(from_unit), normalize_unit(to_unit)
    value = Decimal(str(value))
    if a == b:
        return value
    if (a, b) in FACTORS:
        return value * FACTORS[(a, b)]
    if (a, b) == ("°F", "°C"):
        return (value - 32) * Decimal(5) / Decimal(9)
    if (a, b) == ("°C", "°F"):
        return value * Decimal(9) / Decimal(5) + 32
    raise UnitConversionError(f"No approved conversion from '{from_unit}' to '{to_unit}'")


def area_in_ha(area: Decimal | None, unit: str | None) -> Decimal:
    if area is None:
        return Decimal(0)
    return convert(area, unit or "ha", "ha")
