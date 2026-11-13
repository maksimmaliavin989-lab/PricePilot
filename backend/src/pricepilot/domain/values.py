"""Exact numeric value objects: decimals, money and quantities.

Rules:
* amounts and quantities are ``Decimal``, never ``float``; transport form is a canonical
  decimal string (``"1234.50"``) so no precision is lost in JSON;
* locale formatting (``1 234,5`` / ``1,234``) is NOT accepted here: ambiguous separators
  must be resolved during review, not guessed by the domain;
* money always carries an ISO 4217-like currency, quantity always carries a unit code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pricepilot.domain.common import DomainValidationError, currency_code

MAX_INTEGER_DIGITS = 18
MAX_FRACTION_DIGITS = 8
_DECIMAL_RE = re.compile(
    rf"^-?(?:0|[1-9]\d{{0,{MAX_INTEGER_DIGITS - 1}}})(?:\.\d{{1,{MAX_FRACTION_DIGITS}}})?$"
)


def parse_decimal(value: object, field: str = "value", *, allow_negative: bool = False) -> Decimal:
    """Parse a canonical decimal string. JSON numbers are rejected: a float may already have
    lost precision before it reaches us."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise DomainValidationError(field, 'Число передаётся строкой, например "1234.50".')
    text = value.strip()
    if not _DECIMAL_RE.fullmatch(text):
        raise DomainValidationError(
            field,
            "Число должно быть в формате 1234.50: точка как разделитель, без пробелов "
            f"и разделителей тысяч, не более {MAX_INTEGER_DIGITS} цифр до и "
            f"{MAX_FRACTION_DIGITS} после точки.",
        )
    try:
        number = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guarantees validity
        raise DomainValidationError(field, "Некорректное число.") from exc
    if number < 0 and not allow_negative:
        raise DomainValidationError(field, "Значение не может быть отрицательным.")
    return number


def decimal_text(value: Decimal) -> str:
    """Canonical string that keeps the scale (``Decimal("1.50")`` → ``"1.50"``)."""
    return format(value, "f")


class Dimension(StrEnum):
    COUNT = "count"
    LENGTH = "length"
    AREA = "area"
    VOLUME = "volume"
    MASS = "mass"


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    code: str
    dimension: Dimension
    ucum: str
    label_ru: str
    label_en: str


# Provisional controlled vocabulary (Phase 0 still has to approve the full list).
# Conversions between units are NOT defined here: they need a source/version (TASK_006).
UNITS: dict[str, UnitDefinition] = {
    u.code: u
    for u in (
        UnitDefinition("pc", Dimension.COUNT, "{piece}", "шт", "pc"),
        UnitDefinition("pack", Dimension.COUNT, "{pack}", "уп", "pack"),
        UnitDefinition("set", Dimension.COUNT, "{set}", "компл", "set"),
        UnitDefinition("sheet", Dimension.COUNT, "{sheet}", "лист", "sheet"),
        UnitDefinition("bag", Dimension.COUNT, "{bag}", "меш", "bag"),
        UnitDefinition("roll", Dimension.COUNT, "{roll}", "рул", "roll"),
        UnitDefinition("m", Dimension.LENGTH, "m", "м", "m"),
        UnitDefinition("m2", Dimension.AREA, "m2", "м²", "m²"),
        UnitDefinition("m3", Dimension.VOLUME, "m3", "м³", "m³"),
        UnitDefinition("l", Dimension.VOLUME, "L", "л", "L"),
        UnitDefinition("kg", Dimension.MASS, "kg", "кг", "kg"),
        UnitDefinition("t", Dimension.MASS, "t", "т", "t"),
    )
}


def unit_code(value: object, field: str = "unit") -> str:
    if not isinstance(value, str) or value.strip() not in UNITS:
        raise DomainValidationError(
            field, f"Неизвестная единица. Допустимые коды: {', '.join(sorted(UNITS))}."
        )
    return value.strip()


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    @staticmethod
    def parse(payload: Any, field: str = "value") -> Money:
        if not isinstance(payload, dict):
            raise DomainValidationError(field, "Ожидается объект {amount, currency}.")
        return Money(
            amount=parse_decimal(payload.get("amount"), f"{field}.amount"),
            currency=currency_code(str(payload.get("currency", "")), f"{field}.currency"),
        )

    def to_json(self) -> dict[str, str]:
        return {"amount": decimal_text(self.amount), "currency": self.currency}


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str

    @staticmethod
    def parse(payload: Any, field: str = "value") -> Quantity:
        if not isinstance(payload, dict):
            raise DomainValidationError(field, "Ожидается объект {value, unit}.")
        return Quantity(
            value=parse_decimal(payload.get("value"), f"{field}.value"),
            unit=unit_code(payload.get("unit"), f"{field}.unit"),
        )

    def to_json(self) -> dict[str, str]:
        return {"value": decimal_text(self.value), "unit": self.unit}
