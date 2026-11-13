"""Unit conversions and packaging (TASK_006).

Two kinds of conversion exist and are kept apart:

* **standard** — fixed factors inside one dimension (``t → kg``, ``l → m3``). They come from
  a versioned table and need no confirmation;
* **packaging** — how much of a measurable unit one selling unit holds ("мешок 50 кг",
  "уп. 500 шт."). This is a fact about one offer, not about units: a value read from the
  product name is only a *suggestion* and stays unconfirmed until the user confirms it.

Count units (pc, pack, bag, roll, …) never convert into each other by a standard factor: a
bag of cement and a piece of brick have nothing in common without a stated packaging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.values import UNITS, Dimension, Quantity, decimal_text, unit_code

CONVERSIONS_VERSION = "units-2026.1"

# Factor to the reference unit of the dimension (length m, area m², volume m³, mass kg).
_STANDARD: dict[str, Decimal] = {
    "m": Decimal(1),
    "m2": Decimal(1),
    "m3": Decimal(1),
    "l": Decimal("0.001"),
    "kg": Decimal(1),
    "t": Decimal(1000),
}


class ConversionError(DomainValidationError):
    """Units cannot be converted (different dimensions or count units without packaging)."""


@dataclass(frozen=True, slots=True)
class Conversion:
    factor: Decimal  # 1 ``source`` = factor × ``target``
    source: str
    target: str
    method: str  # "identity" | "standard" | "packaging"
    version: str
    confirmed: bool

    def to_json(self) -> dict[str, object]:
        return {
            "factor": decimal_text(self.factor),
            "source": self.source,
            "target": self.target,
            "method": self.method,
            "version": self.version,
            "confirmed": self.confirmed,
        }


def dimension_of(unit: str) -> Dimension:
    return UNITS[unit_code(unit)].dimension


def standard_factor(source: str, target: str) -> Decimal:
    """Factor such that 1 ``source`` = factor × ``target``; raises ``ConversionError``."""
    source, target = unit_code(source, "unit"), unit_code(target, "unit")
    if source == target:
        return Decimal(1)
    if source not in _STANDARD or target not in _STANDARD:
        raise ConversionError(
            "unit",
            f"«{UNITS[source].label_ru}» и «{UNITS[target].label_ru}» не переводятся друг в "
            "друга без указанной фасовки.",
        )
    if dimension_of(source) is not dimension_of(target):
        raise ConversionError(
            "unit",
            f"Нельзя перевести «{UNITS[source].label_ru}» в «{UNITS[target].label_ru}»: "
            "разные величины.",
        )
    return _STANDARD[source] / _STANDARD[target]


def convert(quantity: Quantity, target: str) -> Quantity:
    return Quantity(quantity.value * standard_factor(quantity.unit, target), unit_code(target))


class PackSource(StrEnum):
    STATED = "stated"  # a separate column / field of the source
    NAME = "name"  # read from the product name — a suggestion
    USER = "user"  # entered or confirmed by the user


@dataclass(frozen=True, slots=True)
class Pack:
    """``content`` of measurable units in one ``unit`` (the unit the price refers to)."""

    unit: str
    content: Quantity
    source: PackSource
    evidence: str  # the text the value came from
    confirmed: bool

    @staticmethod
    def create(
        *, unit: str, content: Quantity, source: PackSource, evidence: str, confirmed: bool
    ) -> Pack:
        unit = unit_code(unit, "pack.unit")
        if content.value <= 0:
            raise DomainValidationError("pack.content", "Фасовка должна быть больше нуля.")
        if content.unit == unit:
            raise DomainValidationError("pack.content", "Фасовка в той же единице, что и цена.")
        if source is PackSource.USER and not confirmed:
            raise DomainValidationError("pack.confirmed", "Введённая пользователем фасовка.")
        return Pack(unit, content, source, evidence, confirmed)

    def confirm(self) -> Pack:
        return Pack(self.unit, self.content, PackSource.USER, self.evidence, True)

    def to_json(self) -> dict[str, object]:
        return {
            "unit": self.unit,
            "content": self.content.to_json(),
            "source": self.source.value,
            "evidence": self.evidence,
            "confirmed": self.confirmed,
        }


def conversion_to(unit: str, target: str, pack: Pack | None) -> Conversion:
    """How many ``target`` units one ``unit`` (the priced unit) is.

    Standard factors first; otherwise the packaging of the offer. An unconfirmed packaging
    still yields a conversion, marked ``confirmed=False`` — callers must warn about it."""
    unit, target = unit_code(unit), unit_code(target)
    if unit == target:
        return Conversion(Decimal(1), unit, target, "identity", CONVERSIONS_VERSION, True)
    try:
        factor = standard_factor(unit, target)
    except ConversionError:
        if pack is None or pack.unit != unit:
            raise
        factor = pack.content.value * standard_factor(pack.content.unit, target)
        return Conversion(factor, unit, target, "packaging", CONVERSIONS_VERSION, pack.confirmed)
    return Conversion(factor, unit, target, "standard", CONVERSIONS_VERSION, True)


# --- packaging read from a product name -----------------------------------------------------

_CONTENT_UNITS: dict[str, tuple[str, Decimal]] = {
    "кг": ("kg", Decimal(1)),
    "kg": ("kg", Decimal(1)),
    "г": ("kg", Decimal("0.001")),
    "гр": ("kg", Decimal("0.001")),
    "т": ("t", Decimal(1)),
    "л": ("l", Decimal(1)),
    "l": ("l", Decimal(1)),
    "мл": ("l", Decimal("0.001")),
    "шт": ("pc", Decimal(1)),
    "м2": ("m2", Decimal(1)),
    "м²": ("m2", Decimal(1)),
    "м3": ("m3", Decimal(1)),
    "м³": ("m3", Decimal(1)),
    "м": ("m", Decimal(1)),
    "пм": ("m", Decimal(1)),
}
_CONTAINER = (
    r"(?:мешок|меш\.?|ведро|канистра|банка|бочка|упаковка|упак\.?|уп\.?|пачка|рулон|бухта"
    r"|в\s+рулоне)"
)
_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT = r"(кг|kg|гр|г|т|мл|л|l|шт|м²|м2|м³|м3|пм|м)(?![a-zа-яё0-9²³])"
# "мешок 50 кг", "уп. 500 шт.", "ведро 20 кг", "рулон 15 м²"
_WITH_CONTAINER = re.compile(rf"{_CONTAINER}\s*[,:]?\s*({_NUMBER})\s*{_UNIT}", re.IGNORECASE)
# a trailing ", 14 кг" / ", 10 л" after a comma (typical for paints, mixes); not "3×100 м"
_TRAILING = re.compile(rf"(?:,|\s)\s*(?<![×x*])({_NUMBER})\s*{_UNIT}\.?\s*$", re.IGNORECASE)
_DIMENSION_BEFORE = re.compile(r"[×x*]\s*$")


def pack_from_name(name: str, unit: str) -> Pack | None:
    """Suggest the packaging of an offer priced per ``unit`` from its name.

    Only count units (шт, уп, меш, рулон…) have a packaging. Dimensions ("12,5 мм",
    "600×300×200", "3×100 м") are not contents. The result is never confirmed."""
    unit = unit_code(unit)
    if dimension_of(unit) is not Dimension.COUNT:
        return None
    text = " ".join(name.split())
    match = _WITH_CONTAINER.search(text)
    if match is None:
        match = _TRAILING.search(text)
        if match is not None and _DIMENSION_BEFORE.search(text[: match.start(1)]):
            match = None
    if match is None:
        return None
    number = Decimal(match.group(1).replace(",", "."))
    content_unit, factor = _CONTENT_UNITS[match.group(2).lower()]
    if content_unit == unit or number <= 0:
        return None
    if content_unit == "m" and unit != "roll":  # "6 м" of a profile is its length, not a pack
        return None
    return Pack.create(
        unit=unit,
        content=Quantity(number * factor, content_unit),
        source=PackSource.NAME,
        evidence=match.group(0).strip(" ,"),
        confirmed=False,
    )
