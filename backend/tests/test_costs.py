"""TASK_006: landed cost — components, discounts, rounding, FX and snapshots."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.costs import (
    COST_FORMULA_VERSION,
    Basis,
    ComponentKind,
    CostComponent,
    CostInput,
    FxRate,
    InputStatus,
    PercentBase,
    WarningCode,
    calculate,
    round_money,
)
from pricepilot.domain.units import conversion_to, pack_from_name
from pricepilot.domain.values import Money, Quantity

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 13)
D = Decimal


def delivery(amount: str = "1500", currency: str = "RUB") -> CostComponent:
    return CostComponent.create(
        kind=ComponentKind.DELIVERY,
        label="Доставка",
        basis=Basis.PER_ORDER,
        value=D(amount),
        currency=currency,
        source="счёт перевозчика",
    )


def discount(percent: str) -> CostComponent:
    return CostComponent.create(
        kind=ComponentKind.DISCOUNT, label="Скидка", basis=Basis.PERCENT, value=D(percent)
    )


def cement(**kw: object) -> CostInput:
    """540 ₽ per 50 kg bag (packaging confirmed), product counted in kg."""
    pack = pack_from_name("Цемент М500 Д0, мешок 50 кг", "pc")
    assert pack is not None
    base: dict[str, object] = {
        "price": Money(D("540.00"), "RUB"),
        "priced_unit": "pc",
        "conversion": conversion_to("pc", "kg", pack.confirm()),
        "required": Quantity(D("1000"), "kg"),
        "target_currency": "RUB",
        "as_of": TODAY,
        "components": (delivery(),),
        "price_includes_vat": True,
    }
    base.update(kw)
    return CostInput(**base)  # type: ignore[arg-type]


def codes(snapshot: object) -> set[WarningCode]:
    return {w.code for w in snapshot.warnings}  # type: ignore[attr-defined]


def amounts(snapshot: object) -> dict[str, Decimal]:
    return {c.kind: c.amount for c in snapshot.components}  # type: ignore[attr-defined]


def test_basic_landed_cost_per_base_unit() -> None:
    s = calculate(cement(), now=NOW)
    assert s.ordered == Quantity(D(20), "pc")
    assert s.purchased == Quantity(D(1000), "kg")
    assert amounts(s) == {"goods": D("10800.00"), "delivery": D("1500.00")}
    assert s.total == Money(D("12300.00"), "RUB")
    assert s.unit_cost == Money(D("12.3000"), "RUB")  # per kg
    assert s.complete
    assert s.warnings == ()
    assert s.formula_version == COST_FORMULA_VERSION


def test_discounts_percent_then_fixed() -> None:
    fixed = CostComponent.create(
        kind=ComponentKind.DISCOUNT,
        label="Скидка за заказ",
        basis=Basis.PER_ORDER,
        value=D("300"),
        currency="RUB",
    )
    s = calculate(cement(components=(discount("7"), fixed, delivery())), now=NOW)
    a = [c.amount for c in s.components]
    assert a == [D("10800.00"), D("-756.00"), D("-300.00"), D("1500.00")]
    assert s.total == Money(D("11244.00"), "RUB")
    # A percent on the net goods after an earlier discount.
    second = CostComponent.create(
        kind=ComponentKind.DISCOUNT,
        label="Доп. скидка",
        basis=Basis.PERCENT,
        value=D("10"),
        base=PercentBase.GOODS_NET,
    )
    s2 = calculate(cement(components=(discount("7"), second, delivery())), now=NOW)
    assert amounts(s2)["discount"] == D("-1004.40")  # last discount: 10% of 10044.00


def test_discount_validation() -> None:
    with pytest.raises(DomainValidationError):
        discount("120")
    with pytest.raises(DomainValidationError):
        discount("-5")
    huge = CostComponent.create(
        kind=ComponentKind.DISCOUNT,
        label="Скидка",
        basis=Basis.PER_ORDER,
        value=D("20000"),
        currency="RUB",
    )
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(huge, delivery())), now=NOW)


def test_rounding_half_up_and_breakdown_adds_up() -> None:
    # 3 × 33.335 = 100.005 → 100.01 (half-up); 1/3 percent fees round per component.
    s = calculate(
        cement(
            price=Money(D("33.335"), "RUB"),
            conversion=conversion_to("kg", "kg", None),
            priced_unit="kg",
            required=Quantity(D(3), "kg"),
            components=(
                delivery("0.005"),
                CostComponent.create(
                    kind=ComponentKind.FEE,
                    label="Сбор",
                    basis=Basis.PERCENT,
                    value=D("0.333"),
                    base=PercentBase.SUBTOTAL,
                ),
            ),
        ),
        now=NOW,
    )
    a = amounts(s)
    assert a["goods"] == D("100.01")
    assert a["delivery"] == D("0.01")
    assert a["fee"] == D("0.33")  # 0.333% of 100.02 = 0.3331
    assert s.total is not None
    assert s.total.amount == sum(a.values())
    assert s.unit_cost == Money(D("33.4500"), "RUB")
    assert round_money(D("0.5"), "JPY") == D("1")
    assert round_money(D("1.0005"), "KWD") == D("1.001")


def test_quantity_rounded_to_whole_bags_multiple_and_minimum() -> None:
    s = calculate(cement(required=Quantity(D("1010"), "kg")), now=NOW)
    assert s.ordered == Quantity(D(21), "pc")  # 20.2 bags → 21
    assert WarningCode.QUANTITY_ROUNDED_UP in codes(s)
    assert s.unit_cost == Money(D("12.2286"), "RUB")  # (11340 + 1500) / 1050 kg
    multiple = calculate(cement(order_multiple=D(8)), now=NOW)
    assert multiple.ordered == Quantity(D(24), "pc")
    minimum = calculate(cement(minimum_order=D(40)), now=NOW)
    assert minimum.ordered == Quantity(D(40), "pc")
    assert minimum.purchased == Quantity(D(2000), "kg")


def test_missing_cost_inputs_are_reported() -> None:
    s = calculate(cement(components=()), now=NOW)
    assert WarningCode.MISSING_COST_INPUT in codes(s)
    assert not s.complete
    assert s.total == Money(D("10800.00"), "RUB")  # computed, but not final
    unknown = CostComponent.create(
        kind=ComponentKind.INSURANCE,
        label="Страхование",
        basis=Basis.PER_ORDER,
        value=None,
        status=InputStatus.UNKNOWN,
    )
    s2 = calculate(cement(components=(delivery(), unknown)), now=NOW)
    assert not s2.complete
    assert any("Страхование" in w.message for w in s2.warnings)
    not_applicable = CostComponent.create(
        kind=ComponentKind.DELIVERY,
        label="Самовывоз",
        basis=Basis.PER_ORDER,
        value=None,
        status=InputStatus.NOT_APPLICABLE,
    )
    s3 = calculate(cement(components=(not_applicable,)), now=NOW)
    assert s3.complete


def test_no_tax_or_duty_rate_is_assumed() -> None:
    s = calculate(cement(price_includes_vat=None), now=NOW)
    assert WarningCode.TAX_NOT_SPECIFIED in codes(s)
    assert set(amounts(s)) == {"goods", "delivery"}  # nothing added implicitly
    with pytest.raises(DomainValidationError):
        CostComponent.create(kind=ComponentKind.VAT, label="НДС", basis=Basis.PERCENT, value=D(20))
    with pytest.raises(DomainValidationError):
        CostComponent.create(
            kind=ComponentKind.DUTY, label="Пошлина", basis=Basis.PERCENT, value=D(5)
        )
    vat = CostComponent.create(
        kind=ComponentKind.VAT,
        label="НДС",
        basis=Basis.PERCENT,
        value=D(20),
        base=PercentBase.SUBTOTAL,
        source="договор, цены без НДС",
    )
    s2 = calculate(cement(price_includes_vat=False, components=(delivery(), vat)), now=NOW)
    assert amounts(s2)["vat"] == D("2460.00")  # 20% of goods + delivery
    assert WarningCode.VAT_RECOVERY_NOT_ASSUMED in codes(s2)
    with pytest.raises(DomainValidationError):  # VAT already inside the price
        calculate(cement(price_includes_vat=True, components=(delivery(), vat)), now=NOW)


def test_foreign_currency_with_fixed_rate_and_date() -> None:
    usd = FxRate.create(
        base="USD",
        quote="RUB",
        rate=D("92.5000"),
        effective_date=date(2026, 11, 12),
        source="ЦБ РФ",
    )
    s = calculate(
        cement(
            price=Money(D("5.80"), "USD"),
            components=(delivery("40", "USD"),),
            fx_rates=(usd,),
        ),
        now=NOW,
    )
    a = amounts(s)
    assert a == {"goods": D("10730.00"), "delivery": D("3700.00")}
    goods = s.components[0]
    assert goods.original == Money(D("116.00"), "USD")
    assert goods.fx == usd
    assert s.input["fx_used"] == [usd.to_json()]  # rate and its date are copied in
    assert s.complete


def test_fx_missing_stale_inverse_and_future() -> None:
    eur = Money(D("6.00"), "EUR")
    missing = calculate(cement(price=eur), now=NOW)
    assert missing.total is None
    assert missing.unit_cost is None
    assert WarningCode.FX_MISSING in codes(missing)
    assert not missing.complete

    old = FxRate.create(
        base="EUR", quote="RUB", rate=D(100), effective_date=date(2026, 10, 1), source="банк"
    )
    stale = calculate(cement(price=eur, fx_rates=(old,)), now=NOW)
    assert WarningCode.FX_STALE in codes(stale)
    assert stale.total is not None

    inverse = FxRate.create(
        base="RUB", quote="EUR", rate=D("0.01"), effective_date=TODAY, source="банк"
    )
    inv = calculate(cement(price=eur, fx_rates=(inverse,)), now=NOW)
    assert WarningCode.FX_INVERTED in codes(inv)
    assert amounts(inv)["goods"] == D("12000.00")

    future = FxRate.create(
        base="EUR", quote="RUB", rate=D(100), effective_date=date(2026, 12, 1), source="банк"
    )
    fut = calculate(cement(price=eur, fx_rates=(future,)), now=NOW)
    assert {WarningCode.FX_AFTER_DATE, WarningCode.FX_MISSING} <= codes(fut)


def test_unconfirmed_conversion_and_price_make_result_incomplete() -> None:
    pack = pack_from_name("Цемент М500 Д0, мешок 50 кг", "pc")
    assert pack is not None
    s = calculate(cement(conversion=conversion_to("pc", "kg", pack)), now=NOW)
    assert WarningCode.CONVERSION_UNCONFIRMED in codes(s)
    assert not s.complete
    p = calculate(cement(price_confirmed=False), now=NOW)
    assert WarningCode.PRICE_UNCONFIRMED in codes(p)
    assert not p.complete


def test_input_validation() -> None:
    with pytest.raises(DomainValidationError):
        calculate(cement(required=Quantity(D(0), "kg")), now=NOW)
    with pytest.raises(DomainValidationError):  # conversion for another unit
        calculate(cement(conversion=conversion_to("t", "kg", None)), now=NOW)
    with pytest.raises(DomainValidationError):
        calculate(cement(order_multiple=D(0)), now=NOW)
    with pytest.raises(DomainValidationError):
        CostComponent.create(
            kind=ComponentKind.DELIVERY, label="Доставка", basis=Basis.PER_ORDER, value=D(10)
        )
    with pytest.raises(DomainValidationError):
        FxRate.create(base="RUB", quote="RUB", rate=D(1), effective_date=TODAY, source="x")


def test_snapshot_is_immutable_reproducible_and_json_safe() -> None:
    first = calculate(cement(), now=NOW)
    again = calculate(cement(), now=NOW, supersedes_id=first.id)
    assert again.id != first.id
    assert again.supersedes_id == first.id
    assert again.input_hash == first.input_hash
    changed = calculate(cement(components=(delivery("1600"),)), now=NOW)
    assert changed.input_hash != first.input_hash
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.total = None  # type: ignore[misc]
    text = json.dumps(first.to_json())
    assert '"12300.00"' in text
    assert "float" not in text
