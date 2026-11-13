"""Landed cost: explicit components, exchange rates and immutable snapshots (TASK_006).

Formula ``landed-2026.1`` (versioned; a change of rules is a new version):

1. **Quantity.** The required quantity in the product's base unit is converted into priced
   units (the unit the supplier's price refers to) with the offer's conversion, then rounded
   up: count units to whole units, then to the order multiple (кратность) and the minimum
   order. The surplus is reported.
2. **Goods** = price × ordered units, converted to the calculation currency.
3. **Components** are applied in the order given. A component is a fixed amount per order,
   an amount per priced unit, or a percentage of an explicit base: goods before discounts,
   goods after discounts, or the running subtotal at that point (e.g. VAT on goods +
   delivery + duty). Discounts are negative. Nothing is added implicitly: no tax, duty or
   delivery rate is ever assumed; a component the user marked as unknown is reported.
4. **Rounding.** Every component is rounded to the currency's minor unit (half-up), the total
   is the sum of the rounded components (so the breakdown always adds up), the unit cost is
   total ÷ purchased base quantity rounded to 4 places.

Every input that influenced the result — prices, conversion, FX rates with date and source,
component sources — is copied into the snapshot. A recalculation is a new snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.common import DomainValidationError, currency_code, ensure_utc, new_id
from pricepilot.domain.units import Conversion
from pricepilot.domain.values import UNITS, Dimension, Money, Quantity, decimal_text, unit_code

COST_FORMULA_VERSION = "landed-2026.1"
FX_MAX_AGE_DAYS = 7
UNIT_COST_PLACES = 4
# Minor units for currencies whose exponent differs from 2 (ISO 4217); others use 2.
MINOR_UNITS: dict[str, int] = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "KWD": 3, "BHD": 3}


class ComponentKind(StrEnum):
    DISCOUNT = "discount"
    DELIVERY = "delivery"
    INSURANCE = "insurance"
    VAT = "vat"
    DUTY = "duty"
    FEE = "fee"
    OTHER = "other"


class Basis(StrEnum):
    PER_ORDER = "per_order"  # fixed amount for the whole order
    PER_UNIT = "per_unit"  # amount per priced unit (e.g. delivery 50 ₽ per bag)
    PERCENT = "percent"


class PercentBase(StrEnum):
    GOODS_GROSS = "goods_gross"  # goods before discounts
    GOODS_NET = "goods_net"  # goods after discounts applied so far
    SUBTOTAL = "subtotal"  # everything computed before this component


class InputStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"  # the user knows it applies but has no value yet
    NOT_APPLICABLE = "not_applicable"  # explicitly confirmed as not applicable


_SOURCE_REQUIRED = {ComponentKind.VAT, ComponentKind.DUTY}


@dataclass(frozen=True, slots=True)
class CostComponent:
    kind: ComponentKind
    label: str
    basis: Basis
    value: Decimal | None  # amount (PER_ORDER/PER_UNIT) or percent (PERCENT), None if unknown
    currency: str | None = None  # amounts only
    base: PercentBase = PercentBase.GOODS_NET
    source: str | None = None  # where the value comes from (contract, invoice, law…)
    status: InputStatus = InputStatus.KNOWN

    @staticmethod
    def create(
        *,
        kind: ComponentKind,
        label: str,
        basis: Basis,
        value: Decimal | None,
        currency: str | None = None,
        base: PercentBase = PercentBase.GOODS_NET,
        source: str | None = None,
        status: InputStatus = InputStatus.KNOWN,
    ) -> CostComponent:
        if status is InputStatus.KNOWN:
            if value is None:
                raise DomainValidationError("components.value", f"«{label}»: нет значения.")
            if value < 0:
                raise DomainValidationError(
                    "components.value",
                    f"«{label}»: значение указывается положительным, скидка вычитается сама.",
                )
            if basis is Basis.PERCENT:
                if currency is not None:
                    raise DomainValidationError("components.currency", "Процент без валюты.")
                if kind is ComponentKind.DISCOUNT and value > 100:
                    raise DomainValidationError("components.value", "Скидка больше 100%.")
            elif currency is None:
                raise DomainValidationError("components.currency", f"«{label}»: нужна валюта.")
            if kind in _SOURCE_REQUIRED and not (source and source.strip()):
                raise DomainValidationError(
                    "components.source",
                    f"«{label}»: укажите источник ставки — налоговые и таможенные ставки "
                    "не подставляются автоматически.",
                )
        elif value is not None:
            raise DomainValidationError("components.value", "Значение только для известных.")
        return CostComponent(
            kind,
            label.strip() or kind.value,
            basis,
            value,
            currency_code(currency) if currency else None,
            base,
            source.strip() if source else None,
            status,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "basis": self.basis.value,
            "value": decimal_text(self.value) if self.value is not None else None,
            "currency": self.currency,
            "base": self.base.value,
            "source": self.source,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class FxRate:
    """1 ``base`` = ``rate`` × ``quote``, valid on ``effective_date``, from ``source``."""

    base: str
    quote: str
    rate: Decimal
    effective_date: date
    source: str

    @staticmethod
    def create(
        *, base: str, quote: str, rate: Decimal, effective_date: date, source: str
    ) -> FxRate:
        if rate <= 0:
            raise DomainValidationError("fx.rate", "Курс должен быть больше нуля.")
        if not source.strip():
            raise DomainValidationError("fx.source", "Укажите источник курса.")
        b, q = currency_code(base, "fx.base"), currency_code(quote, "fx.quote")
        if b == q:
            raise DomainValidationError("fx.quote", "Курс между одинаковыми валютами.")
        return FxRate(b, q, rate, effective_date, source.strip())

    def to_json(self) -> dict[str, str]:
        return {
            "base": self.base,
            "quote": self.quote,
            "rate": decimal_text(self.rate),
            "effective_date": self.effective_date.isoformat(),
            "source": self.source,
        }


class WarningCode(StrEnum):
    MISSING_COST_INPUT = "MISSING_COST_INPUT"  # a required or unknown component has no value
    TAX_NOT_SPECIFIED = "TAX_NOT_SPECIFIED"  # no VAT component and price VAT status unknown
    VAT_RECOVERY_NOT_ASSUMED = "VAT_RECOVERY_NOT_ASSUMED"
    FX_MISSING = "FX_MISSING"
    FX_STALE = "FX_STALE"
    FX_AFTER_DATE = "FX_AFTER_DATE"
    FX_INVERTED = "FX_INVERTED"
    CONVERSION_UNCONFIRMED = "CONVERSION_UNCONFIRMED"
    PRICE_UNCONFIRMED = "PRICE_UNCONFIRMED"
    QUANTITY_ROUNDED_UP = "QUANTITY_ROUNDED_UP"


# Warnings that make the result incomplete (it must not be presented as final).
_INCOMPLETE = {
    WarningCode.MISSING_COST_INPUT,
    WarningCode.FX_MISSING,
    WarningCode.CONVERSION_UNCONFIRMED,
    WarningCode.PRICE_UNCONFIRMED,
}


@dataclass(frozen=True, slots=True)
class CostWarning:
    code: WarningCode
    message: str

    def to_json(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}


@dataclass(frozen=True, slots=True)
class CostInput:
    price: Money  # per priced unit, as stated by the supplier
    priced_unit: str
    conversion: Conversion  # priced unit → product base unit
    required: Quantity  # in the product base unit
    target_currency: str
    as_of: date
    components: tuple[CostComponent, ...] = ()
    fx_rates: tuple[FxRate, ...] = ()
    minimum_order: Decimal | None = None  # priced units
    order_multiple: Decimal | None = None  # priced units
    price_includes_vat: bool | None = None
    price_confirmed: bool = True  # the extracted price was reviewed
    required_kinds: frozenset[ComponentKind] = frozenset({ComponentKind.DELIVERY})

    def to_json(self) -> dict[str, object]:
        return {
            "price": self.price.to_json(),
            "priced_unit": self.priced_unit,
            "conversion": self.conversion.to_json(),
            "required": self.required.to_json(),
            "target_currency": self.target_currency,
            "as_of": self.as_of.isoformat(),
            "components": [c.to_json() for c in self.components],
            "fx_rates": [r.to_json() for r in self.fx_rates],
            "minimum_order": _opt(self.minimum_order),
            "order_multiple": _opt(self.order_multiple),
            "price_includes_vat": self.price_includes_vat,
            "price_confirmed": self.price_confirmed,
            "required_kinds": sorted(k.value for k in self.required_kinds),
        }


def _opt(value: Decimal | None) -> str | None:
    return decimal_text(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class ComponentResult:
    kind: str  # "goods" or a ComponentKind value
    label: str
    amount: Decimal  # signed, in the calculation currency, rounded to minor units
    currency: str
    detail: str  # how it was computed, human readable
    original: Money | None = None  # before FX conversion
    fx: FxRate | None = None
    source: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "amount": decimal_text(self.amount),
            "currency": self.currency,
            "detail": self.detail,
            "original": self.original.to_json() if self.original else None,
            "fx": self.fx.to_json() if self.fx else None,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    id: UUID
    created_at: datetime
    formula_version: str
    input: dict[str, object]  # JSON-safe copy of everything the result depends on
    input_hash: str
    ordered: Quantity  # priced units actually ordered
    purchased: Quantity  # in the base unit
    components: tuple[ComponentResult, ...]
    total: Money | None  # None when it cannot be computed (missing FX)
    unit_cost: Money | None  # per base unit
    warnings: tuple[CostWarning, ...]
    supersedes_id: UUID | None = None

    @property
    def complete(self) -> bool:
        return self.total is not None and not any(w.code in _INCOMPLETE for w in self.warnings)

    def to_json(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "created_at": self.created_at.isoformat(),
            "formula_version": self.formula_version,
            "input": self.input,
            "input_hash": self.input_hash,
            "ordered": self.ordered.to_json(),
            "purchased": self.purchased.to_json(),
            "components": [c.to_json() for c in self.components],
            "total": self.total.to_json() if self.total else None,
            "unit_cost": self.unit_cost.to_json() if self.unit_cost else None,
            "warnings": [w.to_json() for w in self.warnings],
            "complete": self.complete,
            "supersedes_id": str(self.supersedes_id) if self.supersedes_id else None,
        }


def minor_units(currency: str) -> int:
    return MINOR_UNITS.get(currency, 2)


def round_money(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_units(currency)), rounding=ROUND_HALF_UP)


def _ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


@dataclass(slots=True)
class _Fx:
    rates: Sequence[FxRate]
    as_of: date
    warnings: list[CostWarning] = field(default_factory=list)
    used: dict[tuple[str, str], FxRate] = field(default_factory=dict)

    def rate(self, source: str, target: str) -> tuple[Decimal, FxRate | None] | None:
        if source == target:
            return Decimal(1), None
        direct = [r for r in self.rates if (r.base, r.quote) == (source, target)]
        inverse = [r for r in self.rates if (r.base, r.quote) == (target, source)]
        usable = [r for r in direct + inverse if r.effective_date <= self.as_of]
        if not usable:
            if direct or inverse:
                self._warn(
                    WarningCode.FX_AFTER_DATE,
                    f"Курс {source}/{target} есть только на даты позже {self.as_of.isoformat()}.",
                )
            self._warn(WarningCode.FX_MISSING, f"Нет курса {source}/{target} на дату расчёта.")
            return None
        best = max(usable, key=lambda r: (r.effective_date, r in direct))
        if (self.as_of - best.effective_date).days > FX_MAX_AGE_DAYS:
            self._warn(
                WarningCode.FX_STALE,
                f"Курс {best.base}/{best.quote} от {best.effective_date.isoformat()} старше "
                f"{FX_MAX_AGE_DAYS} дней на дату расчёта.",
            )
        self.used[(best.base, best.quote)] = best
        if best.base == source:
            return best.rate, best
        self._warn(
            WarningCode.FX_INVERTED,
            f"Использован обратный курс {best.base}/{best.quote} ({best.source}).",
        )
        return Decimal(1) / best.rate, best

    def _warn(self, code: WarningCode, message: str) -> None:
        if all(w.message != message for w in self.warnings):
            self.warnings.append(CostWarning(code, message))


def _ordered_units(data: CostInput, warnings: list[CostWarning]) -> Decimal:
    factor = data.conversion.factor
    if factor <= 0:
        raise DomainValidationError("conversion", "Коэффициент пересчёта должен быть > 0.")
    needed = data.required.value / factor
    ordered = needed
    if UNITS[data.priced_unit].dimension is Dimension.COUNT:
        ordered = ordered.to_integral_value(rounding=ROUND_CEILING)
    if data.order_multiple:
        ordered = _ceil_to(ordered, data.order_multiple)
    if data.minimum_order and ordered < data.minimum_order:
        ordered = data.minimum_order
    if ordered != needed:
        surplus = ordered * factor - data.required.value
        warnings.append(
            CostWarning(
                WarningCode.QUANTITY_ROUNDED_UP,
                f"Заказ округлён до {decimal_text(ordered)} {data.priced_unit} "
                f"(кратность/минимальный заказ); излишек {decimal_text(surplus.normalize())} "
                f"{data.required.unit}.",
            )
        )
    return ordered


def _validate(data: CostInput) -> None:
    unit_code(data.priced_unit, "priced_unit")
    unit_code(data.required.unit, "required.unit")
    if data.conversion.source != data.priced_unit or data.conversion.target != data.required.unit:
        raise DomainValidationError(
            "conversion", "Пересчёт не соответствует единице цены и базовой единице товара."
        )
    if data.required.value <= 0:
        raise DomainValidationError("required", "Требуемое количество должно быть больше 0.")
    for name, value in (
        ("minimum_order", data.minimum_order),
        ("order_multiple", data.order_multiple),
    ):
        if value is not None and value <= 0:
            raise DomainValidationError(name, "Должно быть больше 0.")
    currency_code(data.target_currency, "target_currency")
    if data.price_includes_vat and any(
        c.kind is ComponentKind.VAT and c.status is InputStatus.KNOWN for c in data.components
    ):
        raise DomainValidationError(
            "components", "Цена уже включает НДС: отдельный компонент НДС посчитает его дважды."
        )


def _input_warnings(data: CostInput) -> list[CostWarning]:
    warnings: list[CostWarning] = []
    if not data.price_confirmed:
        warnings.append(
            CostWarning(WarningCode.PRICE_UNCONFIRMED, "Цена из прайса ещё не подтверждена.")
        )
    if not data.conversion.confirmed:
        warnings.append(
            CostWarning(
                WarningCode.CONVERSION_UNCONFIRMED,
                f"Пересчёт 1 {data.conversion.source} = {decimal_text(data.conversion.factor)} "
                f"{data.conversion.target} взят из названия и не подтверждён.",
            )
        )
    kinds = {c.kind for c in data.components}
    for kind in sorted(data.required_kinds - kinds):
        warnings.append(
            CostWarning(
                WarningCode.MISSING_COST_INPUT,
                f"Не указано: {kind.value}. Укажите сумму или отметьте «не применяется».",
            )
        )
    for c in data.components:
        if c.status is InputStatus.UNKNOWN:
            warnings.append(
                CostWarning(WarningCode.MISSING_COST_INPUT, f"«{c.label}»: значение неизвестно.")
            )
    has_vat = any(c.kind is ComponentKind.VAT for c in data.components)
    if not has_vat and data.price_includes_vat is None:
        warnings.append(
            CostWarning(
                WarningCode.TAX_NOT_SPECIFIED,
                "Не указано, включает ли цена НДС; налог в расчёт не добавлен.",
            )
        )
    if any(c.kind is ComponentKind.VAT and c.status is InputStatus.KNOWN for c in data.components):
        warnings.append(
            CostWarning(
                WarningCode.VAT_RECOVERY_NOT_ASSUMED,
                "НДС включён в стоимость полностью: возмещение не предполагается.",
            )
        )
    return warnings


def calculate(data: CostInput, *, now: datetime, supersedes_id: UUID | None = None) -> CostSnapshot:
    _validate(data)
    target = currency_code(data.target_currency)
    warnings = _input_warnings(data)
    ordered = _ordered_units(data, warnings)
    fx = _Fx(data.fx_rates, data.as_of)
    results: list[ComponentResult] = []
    computable = True

    def to_target(money: Money) -> tuple[Decimal, FxRate | None] | None:
        found = fx.rate(money.currency, target)
        if found is None:
            return None
        rate, used = found
        return money.amount * rate, used

    goods_original = Money(data.price.amount * ordered, data.price.currency)
    converted = to_target(goods_original)
    if converted is None:
        computable = False
        goods_gross = Decimal(0)
    else:
        goods_gross = round_money(converted[0], target)
        results.append(
            ComponentResult(
                "goods",
                "Товар",
                goods_gross,
                target,
                f"{decimal_text(data.price.amount)} {data.price.currency} × "
                f"{decimal_text(ordered)} {data.priced_unit}",
                original=goods_original if converted[1] else None,
                fx=converted[1],
            )
        )

    discounts = Decimal(0)
    for c in data.components:
        if c.status is not InputStatus.KNOWN or c.value is None:
            continue
        sign = Decimal(-1) if c.kind is ComponentKind.DISCOUNT else Decimal(1)
        original: Money | None = None
        used: FxRate | None = None
        if c.basis is Basis.PERCENT:
            base_value = {
                PercentBase.GOODS_GROSS: goods_gross,
                PercentBase.GOODS_NET: goods_gross + discounts,
                PercentBase.SUBTOTAL: sum((r.amount for r in results), Decimal(0)),
            }[c.base]
            amount = base_value * c.value / 100
            detail = (
                f"{decimal_text(c.value)}% от {c.base.value} {decimal_text(base_value)} {target}"
            )
        else:
            assert c.currency is not None  # noqa: S101 - enforced by CostComponent.create
            per = c.value * ordered if c.basis is Basis.PER_UNIT else c.value
            original = Money(per, c.currency)
            conv = to_target(original)
            if conv is None:
                computable = False
                continue
            amount, used = conv
            detail = (
                f"{decimal_text(c.value)} {c.currency} × {decimal_text(ordered)} {data.priced_unit}"
                if c.basis is Basis.PER_UNIT
                else f"{decimal_text(c.value)} {c.currency} за заказ"
            )
            if used is None:
                original = None
        rounded = sign * round_money(amount, target)
        if c.kind is ComponentKind.DISCOUNT:
            discounts += rounded
            if goods_gross + discounts < 0:
                raise DomainValidationError(
                    "components", f"Скидки ({decimal_text(-discounts)}) больше стоимости товара."
                )
        results.append(
            ComponentResult(
                c.kind.value, c.label, rounded, target, detail, original, used, c.source
            )
        )

    warnings.extend(fx.warnings)
    purchased = Quantity(ordered * data.conversion.factor, data.required.unit)
    total: Money | None = None
    unit_cost: Money | None = None
    if computable:
        total_amount = sum((r.amount for r in results), Decimal(0))
        total = Money(total_amount, target)
        unit_cost = Money(
            (total_amount / purchased.value).quantize(
                Decimal(1).scaleb(-UNIT_COST_PLACES), rounding=ROUND_HALF_UP
            ),
            target,
        )
    payload = data.to_json()
    payload["fx_used"] = [r.to_json() for r in fx.used.values()]
    digest = hashlib.sha256(
        json.dumps({"formula": COST_FORMULA_VERSION, "input": payload}, sort_keys=True).encode()
    ).hexdigest()
    return CostSnapshot(
        id=new_id(),
        created_at=ensure_utc(now),
        formula_version=COST_FORMULA_VERSION,
        input=payload,
        input_hash=digest,
        ordered=Quantity(ordered, data.priced_unit),
        purchased=purchased,
        components=tuple(results),
        total=total,
        unit_cost=unit_cost,
        warnings=tuple(warnings),
        supersedes_id=supersedes_id,
    )
