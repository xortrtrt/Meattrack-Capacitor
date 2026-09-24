from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


STOCK_UNITS = ("kg", "g", "ml")
CONTENT_UNITS = ("g", "kg", "ml")
RECIPE_UNITS = ("g", "kg", "ml")
FINISHED_UNIT = "pack"

UNIT_ALIASES = {
    "kilogram": "kg",
    "kilograms": "kg",
    "kgs": "kg",
    "gram": "g",
    "grams": "g",
    "gms": "g",
    "milligram": "mg",
    "milligrams": "mg",
    "mgs": "mg",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "ltr": "l",
    "ltrs": "l",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "packs": "pack",
}

UNIT_FACTORS = {
    "mg": ("weight", Decimal("0.001")),
    "g": ("weight", Decimal("1")),
    "kg": ("weight", Decimal("1000")),
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
}

_DECIMAL_PATTERNS: dict[int, re.Pattern[str]] = {}
_WHOLE_PACK_PATTERN = re.compile(r"^[0-9]+$")


def canonical_unit(value: object, label: str = "Unit") -> str:
    cleaned = " ".join(str(value).strip().split()).lower().replace(".", "")
    if not cleaned:
        raise ValueError(f"{label} is required.")
    return UNIT_ALIASES.get(cleaned, cleaned)


def require_unit(value: object, choices: tuple[str, ...] | list[str], label: str = "Unit") -> str:
    unit = canonical_unit(value, label)
    if unit not in choices:
        raise ValueError(f"Choose a valid {label.lower()}.")
    return unit


def parse_decimal_exact(
    value: object,
    label: str,
    *,
    scale: int = 3,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    raw = str(value).strip()
    pattern = _DECIMAL_PATTERNS.setdefault(
        scale,
        re.compile(rf"^(?:0|[1-9][0-9]*)(?:\.[0-9]{{1,{scale}}})?$"),
    )
    if not raw or not pattern.fullmatch(raw):
        raise ValueError(f"{label} must be a number with at most {scale} decimal places.")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite number.")
    if positive and parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    if nonnegative and parsed < 0:
        raise ValueError(f"{label} cannot be negative.")
    return parsed.quantize(Decimal(1).scaleb(-scale))


def parse_whole_packs(value: object, label: str = "Quantity") -> Decimal:
    if not isinstance(value, str):
        try:
            parsed_value = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{label} must be a whole number of packs.") from exc
        if not parsed_value.is_finite() or parsed_value <= 0 or parsed_value != parsed_value.to_integral_value():
            raise ValueError(f"{label} must be a whole number of packs.")
        return parsed_value.quantize(Decimal("0.001"))
    raw = str(value).strip()
    if not _WHOLE_PACK_PATTERN.fullmatch(raw):
        raise ValueError(f"{label} must be a whole number of packs.")
    parsed = Decimal(raw)
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return parsed.quantize(Decimal("0.001"))


def convert_quantity(quantity: Decimal, from_unit: object, to_unit: object, item_name: str) -> Decimal:
    from_key = canonical_unit(from_unit, "Ingredient unit")
    to_key = canonical_unit(to_unit, "Stock unit")
    if from_key == to_key:
        converted = quantity
    else:
        if from_key not in UNIT_FACTORS or to_key not in UNIT_FACTORS:
            raise ValueError(f"{item_name} uses {to_key}. Enter the recipe in {to_key} or a compatible unit.")
        from_family, from_factor = UNIT_FACTORS[from_key]
        to_family, to_factor = UNIT_FACTORS[to_key]
        if from_family != to_family:
            raise ValueError(f"{item_name} uses {to_key}. {from_key} cannot be converted to {to_key}.")
        converted = (quantity * from_factor) / to_factor

    quantum = Decimal("0.001")
    if converted != converted.quantize(quantum):
        raise ValueError(f"{item_name} amount cannot be represented precisely in {to_key} stock units.")
    converted = converted.quantize(quantum)
    if converted <= 0:
        raise ValueError(f"{item_name} amount is too small for {to_key} stock unit.")
    return converted


def normalize_import_stock_unit(unit: object) -> tuple[str, Decimal]:
    """Return the supported stock unit and multiplier for a legacy quantity."""
    canonical = canonical_unit(unit)
    if canonical == "mg":
        return "g", Decimal("0.001")
    if canonical == "l":
        return "ml", Decimal("1000")
    if canonical in STOCK_UNITS:
        return canonical, Decimal("1")
    raise ValueError(f"Unsupported inventory unit: {unit}.")
