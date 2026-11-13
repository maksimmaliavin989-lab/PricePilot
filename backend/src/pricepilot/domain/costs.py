"""Landed cost: a configurable cost policy, explicit components, FX and immutable snapshots.

Formula ``landed-2026.2`` (versioned; a change of rules is a new version):

1. **Policy.** A ``CostPolicy`` lists the component types that apply, in calculation order.
   Built-in types (discount, delivery, insurance, VAT, duty…) can be extended with own types
   ("разгрузка", "поддоны"). A type may be *required* — then it must be stated for every
   calculation — and charges/discounts may carry a policy default. Taxes and duties never
   have a default.
2. **Unknown is not zero.** A required type with no value, a value marked unknown, a tax
   whose verified source is not valid on the date, or an amount that cannot be converted
   (no FX rate) is ``unknown``: it stays in the breakdown without an amount, everything that
   depends on it (a percentage of a subtotal that includes it) is unknown too, and there is
   no total — only an explicitly labelled sum of the known parts. ``not_applicable`` is the
   user's explicit statement and counts as 0.
3. **Taxes and duties** are included only when entered explicitly (with where the rate comes
   from) or taken from a configured, verified rate source valid on the calculation date.
4. **Quantity.** Required quantity in the product's base unit → priced units via the offer's
   conversion, rounded up to whole count units, the order multiple and the minimum order.
5. **Rounding.** Each component to the currency's minor unit (half-up); the total is the sum
   of the rounded components; the unit cost is total ÷ purchased base quantity, 4 places.

Every input — policy, prices, conversion, FX rates and tax sources with dates — is copied
into the snapshot. A recalculation is a new snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.common import (
    DomainValidationError,
    currency_code,
    display_name,
    ensure_utc,
    new_id,
    optional_text,
)
from pricepilot.domain.units import Conversion
from pricepilot.domain.values import UNITS, Dimension, Money, Quantity, decimal_text, unit_code

COST_FORMULA_VERSION = "landed-2026.2"
FX_MAX_AGE_DAYS = 7
UNIT_COST_PLACES = 4
MAX_COMPONENT_TYPES = 50
# Minor units for currencies whose exponent differs from 2 (ISO 4217); others use 2.
MINOR_UNITS: dict[str, int] = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "KWD": 3, "BHD": 3}


class Category(StrEnum):
    DISCOUNT = "discount"  # subtracted
    CHARGE = "charge"  # delivery, insurance, handling, fees…
    TAX = "tax"  # VAT and other taxes
    DUTY = "duty"  # customs duties and fees


class Basis(StrEnum):
    PER_ORDER = "per_order"  # fixed amount for the whole order
    PER_UNIT = "per_unit"  # amount per priced unit (e.g. delivery 50 ₽ per bag)
    PERCENT = "percent"


class PercentBase(StrEnum):
    GOODS_GROSS = "goods_gross"  # goods before discounts
    GOODS_NET = "goods_net"  # goods after the discounts applied so far
    SUBTOTAL = "subtotal"  # everything computed before this component


class InputStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"  # applies, but the value is not known
    NOT_APPLICABLE = "not_applicable"  # explicitly confirmed as not applicable (= 0)


class ValueOrigin(StrEnum):
    MANUAL = "manual"  # entered by the user for this calculation
    POLICY_DEFAULT = "policy_default"  # charges/discounts only
    VERIFIED_SOURCE = "verified_source"  # taxes/duties from a configured, verified source


_TAXES = {Category.TAX, Category.DUTY}
_CODE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


@dataclass(frozen=True, slots=True)
class ComponentType:
    code: str
    label: str
    category: Category
    basis: Basis
    base: PercentBase = PercentBase.GOODS_NET
    required: bool = False  # must be stated (a value, unknown, or not applicable)
    default_value: Decimal | None = None
    default_currency: str | None = None

    @staticmethod
    def create(
        *,
        code: str,
        label: str,
        category: Category,
        basis: Basis,
        base: PercentBase = PercentBase.GOODS_NET,
        required: bool = False,
        default_value: Decimal | None = None,
        default_currency: str | None = None,
    ) -> ComponentType:
        if not _CODE.fullmatch(code):
            raise DomainValidationError("types.code", "Код: латиница, цифры и _, 2–40 символов.")
        if default_value is not None:
            if category in _TAXES:
                raise DomainValidationError(
                    "types.default_value",
                    "Налоги и пошлины не имеют значения по умолчанию: только явный ввод или "
                    "проверенный источник ставок.",
                )
            _check_value(default_value, basis, default_currency, category, "types.default_value")
        elif default_currency is not None:
            raise DomainValidationError("types.default_currency", "Валюта без значения.")
        return ComponentType(
            code,
            display_name(label, "types.label", 100),
            category,
            basis,
            base,
            required,
            default_value,
            currency_code(default_currency) if default_currency else None,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code,
            "label": self.label,
            "category": self.category.value,
            "basis": self.basis.value,
            "base": self.base.value,
            "required": self.required,
            "default_value": _opt(self.default_value),
            "default_currency": self.default_currency,
        }


def _check_value(
    value: Decimal, basis: Basis, currency: str | None, category: Category, name: str
) -> None:
    if value < 0:
        raise DomainValidationError(
            name, "Значение указывается положительным; скидка вычитается сама."
        )
    if basis is Basis.PERCENT:
        if currency is not None:
            raise DomainValidationError(name, "Процент указывается без валюты.")
        if category is Category.DISCOUNT and value > 100:
            raise DomainValidationError(name, "Скидка больше 100%.")
    elif currency is None:
        raise DomainValidationError(name, "Для суммы нужна валюта.")


@dataclass(frozen=True, slots=True)
class CostPolicy:
    """Which component types apply and in which order. Immutable; ``extended`` and
    ``without`` return new policies. ``fingerprint`` identifies the configuration."""

    name: str
    types: tuple[ComponentType, ...]

    @staticmethod
    def create(name: str, types: Sequence[ComponentType]) -> CostPolicy:
        codes = [t.code for t in types]
        if len(set(codes)) != len(codes):
            raise DomainValidationError("types", "Коды компонентов повторяются.")
        if len(types) > MAX_COMPONENT_TYPES:
            raise DomainValidationError("types", f"Не более {MAX_COMPONENT_TYPES} компонентов.")
        return CostPolicy(display_name(name, "policy.name", 100), tuple(types))

    @staticmethod
    def default() -> CostPolicy:
        """Built-in types; only delivery is required. No values, no tax rates."""
        return CostPolicy.create(
            "Базовая",
            [
                ComponentType.create(
                    code="discount",
                    label="Скидка",
                    category=Category.DISCOUNT,
                    basis=Basis.PERCENT,
                    base=PercentBase.GOODS_GROSS,
                ),
                ComponentType.create(
                    code="delivery",
                    label="Доставка",
                    category=Category.CHARGE,
                    basis=Basis.PER_ORDER,
                    required=True,
                ),
                ComponentType.create(
                    code="insurance",
                    label="Страхование",
                    category=Category.CHARGE,
                    basis=Basis.PERCENT,
                ),
                ComponentType.create(
                    code="duty",
                    label="Пошлина",
                    category=Category.DUTY,
                    basis=Basis.PERCENT,
                    base=PercentBase.SUBTOTAL,
                ),
                ComponentType.create(
                    code="vat",
                    label="НДС",
                    category=Category.TAX,
                    basis=Basis.PERCENT,
                    base=PercentBase.SUBTOTAL,
                ),
            ],
        )

    def type(self, code: str) -> ComponentType:
        for t in self.types:
            if t.code == code:
                return t
        raise DomainValidationError(
            "components.code", f"Компонента «{code}» нет в политике «{self.name}»."
        )

    def extended(self, new: ComponentType, *, before: str | None = None) -> CostPolicy:
        types = list(self.types)
        index = len(types) if before is None else types.index(self.type(before))
        types.insert(index, new)
        return CostPolicy.create(self.name, types)

    def without(self, code: str) -> CostPolicy:
        self.type(code)
        return CostPolicy.create(self.name, [t for t in self.types if t.code != code])

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "types": [t.to_json() for t in self.types]}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedRateSource:
    """A configured source of tax/duty rates that a person has checked (e.g. a tariff
    reference with its document number). Rates from it are used only inside its validity."""

    id: UUID
    name: str
    reference: str  # document, publication, URL as text
    verified_by: str
    verified_at: datetime

    @staticmethod
    def create(
        *, name: str, reference: str, verified_by: str, verified_at: datetime
    ) -> VerifiedRateSource:
        ref = optional_text(reference, "rate_source.reference", 500)
        who = optional_text(verified_by, "rate_source.verified_by", 200)
        if ref is None or who is None:
            raise DomainValidationError(
                "rate_source", "Источник ставок: укажите документ и кто его проверил."
            )
        return VerifiedRateSource(
            new_id(), display_name(name, "rate_source.name", 200), ref, who, ensure_utc(verified_at)
        )

    def to_json(self) -> dict[str, str]:
        return {
            "id": str(self.id),
            "name": self.name,
            "reference": self.reference,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class VerifiedRate:
    source: VerifiedRateSource
    component_code: str
    rate: Decimal  # percent
    valid_from: date
    valid_to: date | None = None
    scope: str | None = None  # e.g. a customs code or a product group, as text

    def valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    def to_json(self) -> dict[str, object]:
        return {
            "source": self.source.to_json(),
            "component_code": self.component_code,
            "rate": decimal_text(self.rate),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class CostComponent:
    """A value for one component type of the policy, for one calculation."""

    code: str
    status: InputStatus
    value: Decimal | None = None
    currency: str | None = None
    basis: Basis | None = None  # overrides the type's basis
    base: PercentBase | None = None
    origin: ValueOrigin = ValueOrigin.MANUAL
    source: str | None = None  # where a manual value comes from (contract, invoice…)
    verified_rate: VerifiedRate | None = None

    @staticmethod
    def known(
        code: str,
        value: Decimal,
        *,
        currency: str | None = None,
        source: str | None = None,
        basis: Basis | None = None,
        base: PercentBase | None = None,
    ) -> CostComponent:
        return CostComponent(
            code,
            InputStatus.KNOWN,
            value,
            currency_code(currency) if currency else None,
            basis,
            base,
            ValueOrigin.MANUAL,
            source.strip() if source and source.strip() else None,
        )

    @staticmethod
    def unknown(code: str, note: str | None = None) -> CostComponent:
        return CostComponent(code, InputStatus.UNKNOWN, source=note)

    @staticmethod
    def not_applicable(code: str, note: str | None = None) -> CostComponent:
        return CostComponent(code, InputStatus.NOT_APPLICABLE, source=note)

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code,
            "status": self.status.value,
            "value": _opt(self.value),
            "currency": self.currency,
            "basis": self.basis.value if self.basis else None,
            "base": self.base.value if self.base else None,
            "origin": self.origin.value,
            "source": self.source,
            "verified_rate": self.verified_rate.to_json() if self.verified_rate else None,
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
    UNKNOWN_COMPONENT = "UNKNOWN_COMPONENT"  # a value is unknown: no total
    TAX_NOT_SPECIFIED = "TAX_NOT_SPECIFIED"  # no VAT component and price VAT status unknown
    TAX_SOURCE_NOT_VALID = "TAX_SOURCE_NOT_VALID"
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
    WarningCode.UNKNOWN_COMPONENT,
    WarningCode.TAX_SOURCE_NOT_VALID,
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
    policy: CostPolicy = field(default_factory=CostPolicy.default)
    components: tuple[CostComponent, ...] = ()
    verified_rates: tuple[VerifiedRate, ...] = ()
    fx_rates: tuple[FxRate, ...] = ()
    minimum_order: Decimal | None = None  # priced units
    order_multiple: Decimal | None = None  # priced units
    price_includes_vat: bool | None = None
    price_confirmed: bool = True  # the extracted price was reviewed

    def to_json(self) -> dict[str, object]:
        return {
            "price": self.price.to_json(),
            "priced_unit": self.priced_unit,
            "conversion": self.conversion.to_json(),
            "required": self.required.to_json(),
            "target_currency": self.target_currency,
            "as_of": self.as_of.isoformat(),
            "policy": self.policy.to_json(),
            "components": [c.to_json() for c in self.components],
            "verified_rates": [r.to_json() for r in self.verified_rates],
            "fx_rates": [r.to_json() for r in self.fx_rates],
            "minimum_order": _opt(self.minimum_order),
            "order_multiple": _opt(self.order_multiple),
            "price_includes_vat": self.price_includes_vat,
            "price_confirmed": self.price_confirmed,
        }


def _opt(value: Decimal | None) -> str | None:
    return decimal_text(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class ComponentResult:
    code: str  # "goods" or a component type code
    label: str
    category: str
    status: InputStatus
    amount: Decimal | None  # signed, calculation currency, rounded; None = unknown
    currency: str
    detail: str  # how it was computed or why it is unknown
    origin: ValueOrigin | None = None
    original: Money | None = None  # before FX conversion
    fx: FxRate | None = None
    source: str | None = None
    verified_rate: VerifiedRate | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code,
            "label": self.label,
            "category": self.category,
            "status": self.status.value,
            "amount": _opt(self.amount),
            "currency": self.currency,
            "detail": self.detail,
            "origin": self.origin.value if self.origin else None,
            "original": self.original.to_json() if self.original else None,
            "fx": self.fx.to_json() if self.fx else None,
            "source": self.source,
            "verified_rate": self.verified_rate.to_json() if self.verified_rate else None,
        }


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    id: UUID
    created_at: datetime
    formula_version: str
    policy_fingerprint: str
    input: dict[str, object]  # JSON-safe copy of everything the result depends on
    input_hash: str
    ordered: Quantity  # priced units actually ordered
    purchased: Quantity  # in the base unit
    components: tuple[ComponentResult, ...]
    total: Money | None  # None while any component is unknown
    unit_cost: Money | None  # per base unit
    known_subtotal: Money  # sum of the known parts only — never presented as the total
    warnings: tuple[CostWarning, ...]
    supersedes_id: UUID | None = None

    @property
    def unknown_components(self) -> tuple[str, ...]:
        return tuple(c.code for c in self.components if c.amount is None)

    @property
    def complete(self) -> bool:
        return self.total is not None and not any(w.code in _INCOMPLETE for w in self.warnings)

    def to_json(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "created_at": self.created_at.isoformat(),
            "formula_version": self.formula_version,
            "policy_fingerprint": self.policy_fingerprint,
            "input": self.input,
            "input_hash": self.input_hash,
            "ordered": self.ordered.to_json(),
            "purchased": self.purchased.to_json(),
            "components": [c.to_json() for c in self.components],
            "total": self.total.to_json() if self.total else None,
            "unit_cost": self.unit_cost.to_json() if self.unit_cost else None,
            "known_subtotal": self.known_subtotal.to_json(),
            "unknown_components": list(self.unknown_components),
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
class _Warnings:
    items: list[CostWarning] = field(default_factory=list)

    def add(self, code: WarningCode, message: str) -> None:
        if all(w.message != message for w in self.items):
            self.items.append(CostWarning(code, message))


@dataclass(slots=True)
class _Fx:
    rates: Sequence[FxRate]
    as_of: date
    warnings: _Warnings
    used: dict[tuple[str, str], FxRate] = field(default_factory=dict)

    def rate(self, source: str, target: str) -> tuple[Decimal, FxRate | None] | None:
        if source == target:
            return Decimal(1), None
        direct = [r for r in self.rates if (r.base, r.quote) == (source, target)]
        inverse = [r for r in self.rates if (r.base, r.quote) == (target, source)]
        usable = [r for r in direct + inverse if r.effective_date <= self.as_of]
        if not usable:
            if direct or inverse:
                self.warnings.add(
                    WarningCode.FX_AFTER_DATE,
                    f"Курс {source}/{target} есть только на даты позже {self.as_of.isoformat()}.",
                )
            self.warnings.add(
                WarningCode.FX_MISSING, f"Нет курса {source}/{target} на дату расчёта."
            )
            return None
        best = max(usable, key=lambda r: (r.effective_date, r in direct))
        if (self.as_of - best.effective_date).days > FX_MAX_AGE_DAYS:
            self.warnings.add(
                WarningCode.FX_STALE,
                f"Курс {best.base}/{best.quote} от {best.effective_date.isoformat()} старше "
                f"{FX_MAX_AGE_DAYS} дней на дату расчёта.",
            )
        self.used[(best.base, best.quote)] = best
        if best.base == source:
            return best.rate, best
        self.warnings.add(
            WarningCode.FX_INVERTED,
            f"Использован обратный курс {best.base}/{best.quote} ({best.source}).",
        )
        return Decimal(1) / best.rate, best


def _ordered_units(data: CostInput, warnings: _Warnings) -> Decimal:
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
        warnings.add(
            WarningCode.QUANTITY_ROUNDED_UP,
            f"Заказ округлён до {decimal_text(ordered)} {data.priced_unit} "
            f"(кратность/минимальный заказ); излишек {decimal_text(surplus.normalize())} "
            f"{data.required.unit}.",
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
    seen: set[str] = set()
    for c in data.components:
        ctype = data.policy.type(c.code)
        if c.code in seen:
            raise DomainValidationError("components", f"«{ctype.label}» указан дважды.")
        seen.add(c.code)
        if c.status is InputStatus.KNOWN:
            if c.value is None:
                raise DomainValidationError("components.value", f"«{ctype.label}»: нет значения.")
            _check_value(
                c.value, c.basis or ctype.basis, c.currency, ctype.category, "components.value"
            )
            if ctype.category in _TAXES and c.origin is ValueOrigin.MANUAL and not c.source:
                raise DomainValidationError(
                    "components.source",
                    f"«{ctype.label}»: укажите, откуда ставка — налоги и пошлины учитываются "
                    "только при явном вводе с источником или из проверенного источника ставок.",
                )
            if ctype.category in _TAXES and c.origin is ValueOrigin.POLICY_DEFAULT:
                raise DomainValidationError("components.origin", "Налог не берётся по умолчанию.")
        elif c.value is not None:
            raise DomainValidationError(
                "components.value", f"«{ctype.label}»: значение только у известных компонентов."
            )
    vat_codes = {t.code for t in data.policy.types if t.category is Category.TAX}
    if data.price_includes_vat and any(
        c.code in vat_codes and c.status is InputStatus.KNOWN for c in data.components
    ):
        raise DomainValidationError(
            "components", "Цена уже включает НДС: отдельный налог посчитает его дважды."
        )
    for rate in data.verified_rates:
        if data.policy.type(rate.component_code).category not in _TAXES:
            raise DomainValidationError(
                "verified_rates", "Проверенные источники ставок — только для налогов и пошлин."
            )


def _resolve(
    data: CostInput, warnings: _Warnings
) -> list[tuple[ComponentType, CostComponent | None]]:
    """Component per policy type, in policy order. ``None`` = optional and not stated."""
    entered = {c.code: c for c in data.components}
    resolved: list[tuple[ComponentType, CostComponent | None]] = []
    for ctype in data.policy.types:
        component = entered.get(ctype.code)
        if component is None and ctype.category in _TAXES:
            rates = [r for r in data.verified_rates if r.component_code == ctype.code]
            valid = [r for r in rates if r.valid_on(data.as_of)]
            if valid:
                rate = max(valid, key=lambda r: r.valid_from)
                component = CostComponent(
                    ctype.code,
                    InputStatus.KNOWN,
                    rate.rate,
                    basis=Basis.PERCENT,
                    origin=ValueOrigin.VERIFIED_SOURCE,
                    source=rate.source.name,
                    verified_rate=rate,
                )
            elif rates:
                warnings.add(
                    WarningCode.TAX_SOURCE_NOT_VALID,
                    f"«{ctype.label}»: ставка проверенного источника не действует на "
                    f"{data.as_of.isoformat()}; значение неизвестно.",
                )
                component = CostComponent.unknown(ctype.code, "нет действующей ставки")
        if component is None and ctype.default_value is not None:
            component = CostComponent(
                ctype.code,
                InputStatus.KNOWN,
                ctype.default_value,
                ctype.default_currency,
                origin=ValueOrigin.POLICY_DEFAULT,
                source=f"политика «{data.policy.name}»",
            )
        if component is None and ctype.required:
            component = CostComponent.unknown(ctype.code, "обязательный компонент не указан")
        resolved.append((ctype, component))
    return resolved


def _input_warnings(
    data: CostInput, resolved: list[tuple[ComponentType, CostComponent | None]], w: _Warnings
) -> None:
    if not data.price_confirmed:
        w.add(WarningCode.PRICE_UNCONFIRMED, "Цена из прайса ещё не подтверждена.")
    if not data.conversion.confirmed:
        w.add(
            WarningCode.CONVERSION_UNCONFIRMED,
            f"Пересчёт 1 {data.conversion.source} = {decimal_text(data.conversion.factor)} "
            f"{data.conversion.target} взят из названия и не подтверждён.",
        )
    taxes = [(t, c) for t, c in resolved if t.category is Category.TAX and c is not None]
    if not taxes and data.price_includes_vat is None:
        w.add(
            WarningCode.TAX_NOT_SPECIFIED,
            "Не указано, включает ли цена НДС; налог в расчёт не добавлен.",
        )
    if any(c.status is InputStatus.KNOWN for _, c in taxes if c is not None):
        w.add(
            WarningCode.VAT_RECOVERY_NOT_ASSUMED,
            "Налог включён в стоимость полностью: возмещение не предполагается.",
        )


def calculate(data: CostInput, *, now: datetime, supersedes_id: UUID | None = None) -> CostSnapshot:
    _validate(data)
    target = currency_code(data.target_currency)
    warnings = _Warnings()
    resolved = _resolve(data, warnings)
    _input_warnings(data, resolved, warnings)
    ordered = _ordered_units(data, warnings)
    fx = _Fx(data.fx_rates, data.as_of, warnings)
    results: list[ComponentResult] = []

    def to_target(money: Money) -> tuple[Decimal, FxRate | None] | None:
        found = fx.rate(money.currency, target)
        if found is None:
            return None
        rate, used = found
        return money.amount * rate, used

    def subtotal() -> Decimal | None:
        amounts = [r.amount for r in results if r.amount is not None]
        return None if len(amounts) < len(results) else sum(amounts, Decimal(0))

    goods_original = Money(data.price.amount * ordered, data.price.currency)
    converted = to_target(goods_original)
    goods_detail = (
        f"{decimal_text(data.price.amount)} {data.price.currency} × "
        f"{decimal_text(ordered)} {data.priced_unit}"
    )
    goods_gross = round_money(converted[0], target) if converted else None
    results.append(
        ComponentResult(
            "goods",
            "Товар",
            "goods",
            InputStatus.KNOWN if converted else InputStatus.UNKNOWN,
            goods_gross,
            target,
            goods_detail if converted else f"{goods_detail}: нет курса {data.price.currency}",
            original=goods_original if converted and converted[1] else None,
            fx=converted[1] if converted else None,
        )
    )

    discounts: Decimal | None = Decimal(0)
    for ctype, c in resolved:
        if c is None:
            continue
        common = {
            "code": ctype.code,
            "label": ctype.label,
            "category": ctype.category.value,
            "currency": target,
            "origin": c.origin,
            "source": c.source,
            "verified_rate": c.verified_rate,
        }
        if c.status is InputStatus.NOT_APPLICABLE:
            results.append(
                ComponentResult(
                    status=c.status,
                    amount=Decimal(0),
                    detail="не применяется (указано)",
                    **common,  # type: ignore[arg-type]
                )
            )
            continue
        if c.status is InputStatus.UNKNOWN or c.value is None:
            warnings.add(
                WarningCode.UNKNOWN_COMPONENT,
                f"«{ctype.label}»: значение неизвестно — итог не рассчитан.",
            )
            results.append(
                ComponentResult(
                    status=InputStatus.UNKNOWN,
                    amount=None,
                    detail=f"неизвестно{': ' + c.source if c.source else ''}",
                    **common,  # type: ignore[arg-type]
                )
            )
            if ctype.category is Category.DISCOUNT:
                discounts = None
            continue
        basis = c.basis or ctype.basis
        base = c.base or ctype.base
        sign = Decimal(-1) if ctype.category is Category.DISCOUNT else Decimal(1)
        original: Money | None = None
        used: FxRate | None = None
        amount: Decimal | None
        if basis is Basis.PERCENT:
            base_value = {
                PercentBase.GOODS_GROSS: goods_gross,
                PercentBase.GOODS_NET: (
                    goods_gross + discounts
                    if goods_gross is not None and discounts is not None
                    else None
                ),
                PercentBase.SUBTOTAL: subtotal(),
            }[base]
            if base_value is None:
                amount = None
                detail = f"{decimal_text(c.value)}% от {base.value}, база неизвестна"
            else:
                amount = base_value * c.value / 100
                detail = (
                    f"{decimal_text(c.value)}% от {base.value} {decimal_text(base_value)} {target}"
                )
        else:
            currency = c.currency or ctype.default_currency
            assert currency is not None  # noqa: S101 - enforced by validation
            per = c.value * ordered if basis is Basis.PER_UNIT else c.value
            original = Money(per, currency)
            conv = to_target(original)
            detail = (
                f"{decimal_text(c.value)} {currency} × {decimal_text(ordered)} {data.priced_unit}"
                if basis is Basis.PER_UNIT
                else f"{decimal_text(c.value)} {currency} за заказ"
            )
            if conv is None:
                amount = None
                detail += f": нет курса {currency}"
            else:
                amount, used = conv
            if used is None:
                original = None
        rounded = sign * round_money(amount, target) if amount is not None else None
        if rounded is None:
            warnings.add(
                WarningCode.UNKNOWN_COMPONENT,
                f"«{ctype.label}»: не рассчитан ({detail}) — итог не рассчитан.",
            )
        if ctype.category is Category.DISCOUNT:
            discounts = (
                discounts + rounded if discounts is not None and rounded is not None else None
            )
            if goods_gross is not None and discounts is not None and goods_gross + discounts < 0:
                raise DomainValidationError(
                    "components", f"Скидки ({decimal_text(-discounts)}) больше стоимости товара."
                )
        results.append(
            ComponentResult(
                status=InputStatus.KNOWN if rounded is not None else InputStatus.UNKNOWN,
                amount=rounded,
                detail=detail,
                original=original,
                fx=used,
                **{**common, "currency": target},  # type: ignore[arg-type]
            )
        )

    purchased = Quantity(ordered * data.conversion.factor, data.required.unit)
    known = sum((r.amount for r in results if r.amount is not None), Decimal(0))
    total_amount = subtotal()
    total = Money(total_amount, target) if total_amount is not None else None
    unit_cost = (
        Money(
            (total_amount / purchased.value).quantize(
                Decimal(1).scaleb(-UNIT_COST_PLACES), rounding=ROUND_HALF_UP
            ),
            target,
        )
        if total_amount is not None
        else None
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
        policy_fingerprint=data.policy.fingerprint,
        input=payload,
        input_hash=digest,
        ordered=Quantity(ordered, data.priced_unit),
        purchased=purchased,
        components=tuple(results),
        total=total,
        unit_cost=unit_cost,
        known_subtotal=Money(known, target),
        warnings=tuple(warnings.items),
        supersedes_id=supersedes_id,
    )
