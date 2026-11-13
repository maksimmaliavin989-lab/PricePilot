"""TASK_006: landed cost — configurable policy, unknown values, taxes, FX and snapshots."""

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
    Category,
    ComponentType,
    CostComponent,
    CostInput,
    CostPolicy,
    FxRate,
    PercentBase,
    ValueOrigin,
    VerifiedRate,
    VerifiedRateSource,
    WarningCode,
    calculate,
    round_money,
)
from pricepilot.domain.units import conversion_to, pack_from_name
from pricepilot.domain.values import Money, Quantity

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 13)
D = Decimal
K = CostComponent.known


def delivery(amount: str = "1500", currency: str = "RUB") -> CostComponent:
    return K("delivery", D(amount), currency=currency, source="счёт перевозчика")


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


def amounts(snapshot: object) -> dict[str, Decimal | None]:
    return {c.code: c.amount for c in snapshot.components}  # type: ignore[attr-defined]


def tariff(rate: str = "20", valid_to: date | None = None, code: str = "vat") -> VerifiedRate:
    source = VerifiedRateSource.create(
        name="Справочник ставок закупок",
        reference="Приказ № 12 от 01.01.2026, проверено бухгалтерией",
        verified_by="user:accountant",
        verified_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    return VerifiedRate(source, code, D(rate), date(2026, 1, 1), valid_to)


# --- basic result ---------------------------------------------------------------------------


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


# --- configurable, extensible policy ---------------------------------------------------------


def test_policy_is_extensible_with_own_component_types() -> None:
    unloading = ComponentType.create(
        code="unloading", label="Разгрузка", category=Category.CHARGE, basis=Basis.PER_UNIT
    )
    policy = CostPolicy.default().extended(unloading, before="duty")
    assert [t.code for t in policy.types] == [
        "discount", "delivery", "insurance", "unloading", "duty", "vat",
    ]  # fmt: skip
    s = calculate(
        cement(policy=policy, components=(delivery(), K("unloading", D("15"), currency="RUB"))),
        now=NOW,
    )
    assert amounts(s)["unloading"] == D("300.00")  # 15 ₽ × 20 bags
    assert s.total == Money(D("12600.00"), "RUB")
    assert s.policy_fingerprint == policy.fingerprint != CostPolicy.default().fingerprint
    assert s.input["policy"] == policy.to_json()


def test_policy_validation() -> None:
    with pytest.raises(DomainValidationError):  # component not configured in the policy
        calculate(cement(components=(delivery(), K("pallets", D(1), currency="RUB"))), now=NOW)
    with pytest.raises(DomainValidationError):
        CostPolicy.default().extended(
            ComponentType.create(
                code="delivery", label="Ещё доставка", category=Category.CHARGE,
                basis=Basis.PER_ORDER,
            )
        )  # fmt: skip
    with pytest.raises(DomainValidationError):
        ComponentType.create(
            code="Bad code", label="x", category=Category.CHARGE, basis=Basis.PER_ORDER
        )
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(delivery(), delivery("10"))), now=NOW)
    assert [t.code for t in CostPolicy.default().without("insurance").types] == [
        "discount", "delivery", "duty", "vat",
    ]  # fmt: skip


def test_policy_default_value_for_a_charge() -> None:
    handling = ComponentType.create(
        code="handling", label="Обработка заказа", category=Category.CHARGE,
        basis=Basis.PER_ORDER, default_value=D("250"), default_currency="RUB",
    )  # fmt: skip
    s = calculate(cement(policy=CostPolicy.default().extended(handling)), now=NOW)
    handling_row = next(c for c in s.components if c.code == "handling")
    assert handling_row.amount == D("250.00")
    assert handling_row.origin is ValueOrigin.POLICY_DEFAULT


# --- unknown is not zero ----------------------------------------------------------------------


def test_missing_required_component_is_unknown_not_zero() -> None:
    s = calculate(cement(components=()), now=NOW)
    assert amounts(s) == {"goods": D("10800.00"), "delivery": None}
    assert s.total is None
    assert s.unit_cost is None
    assert s.known_subtotal == Money(D("10800.00"), "RUB")
    assert s.unknown_components == ("delivery",)
    assert WarningCode.UNKNOWN_COMPONENT in codes(s)
    assert not s.complete
    assert json.loads(json.dumps(s.to_json()))["components"][1]["amount"] is None


def test_unknown_propagates_to_dependent_percentages() -> None:
    vat = K("vat", D(20), source="договор: цены без НДС")
    s = calculate(
        cement(price_includes_vat=False, components=(CostComponent.unknown("delivery"), vat)),
        now=NOW,
    )
    # VAT is a percentage of the subtotal, which includes an unknown delivery.
    assert amounts(s)["vat"] is None
    assert set(s.unknown_components) == {"delivery", "vat"}
    assert s.total is None


def test_not_applicable_is_an_explicit_zero() -> None:
    s = calculate(
        cement(components=(CostComponent.not_applicable("delivery", "самовывоз"),)), now=NOW
    )
    assert amounts(s)["delivery"] == D("0")
    assert s.complete
    assert s.total == Money(D("10800.00"), "RUB")


def test_optional_components_not_stated_are_left_out() -> None:
    s = calculate(cement(), now=NOW)
    assert "insurance" not in amounts(s)
    assert "duty" not in amounts(s)


# --- discounts, rounding, quantities ---------------------------------------------------------


def test_discounts_percent_then_fixed() -> None:
    order_discount = ComponentType.create(
        code="order_discount", label="Скидка за заказ", category=Category.DISCOUNT,
        basis=Basis.PER_ORDER,
    )  # fmt: skip
    policy = CostPolicy.default().extended(order_discount, before="delivery")
    s = calculate(
        cement(
            policy=policy,
            components=(
                K("discount", D(7)),
                K("order_discount", D(300), currency="RUB"),
                delivery(),
            ),
        ),
        now=NOW,
    )
    assert [c.amount for c in s.components] == [
        D("10800.00"), D("-756.00"), D("-300.00"), D("1500.00"),
    ]  # fmt: skip
    assert s.total == Money(D("11244.00"), "RUB")
    net = calculate(
        cement(components=(K("discount", D(10), base=PercentBase.GOODS_NET), delivery())), now=NOW
    )
    assert amounts(net)["discount"] == D("-1080.00")


def test_discount_validation() -> None:
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(K("discount", D(120)), delivery())), now=NOW)
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(K("discount", D(-5)), delivery())), now=NOW)
    huge = K("discount", D(20000), currency="RUB", basis=Basis.PER_ORDER)
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(huge, delivery())), now=NOW)


def test_rounding_half_up_and_breakdown_adds_up() -> None:
    fee = ComponentType.create(
        code="fee", label="Сбор", category=Category.CHARGE, basis=Basis.PERCENT,
        base=PercentBase.SUBTOTAL,
    )  # fmt: skip
    s = calculate(
        cement(
            policy=CostPolicy.default().extended(fee),
            price=Money(D("33.335"), "RUB"),
            conversion=conversion_to("kg", "kg", None),
            priced_unit="kg",
            required=Quantity(D(3), "kg"),
            components=(delivery("0.005"), K("fee", D("0.333"))),
        ),
        now=NOW,
    )
    a = amounts(s)
    assert a["goods"] == D("100.01")  # 100.005 → half-up
    assert a["delivery"] == D("0.01")
    assert a["fee"] == D("0.33")  # 0.333% of 100.02
    assert s.total is not None
    assert s.total.amount == sum(v for v in a.values() if v is not None)
    assert s.unit_cost == Money(D("33.4500"), "RUB")
    assert round_money(D("0.5"), "JPY") == D("1")
    assert round_money(D("1.0005"), "KWD") == D("1.001")


def test_quantity_rounded_to_whole_bags_multiple_and_minimum() -> None:
    s = calculate(cement(required=Quantity(D("1010"), "kg")), now=NOW)
    assert s.ordered == Quantity(D(21), "pc")  # 20.2 bags → 21
    assert WarningCode.QUANTITY_ROUNDED_UP in codes(s)
    assert s.unit_cost == Money(D("12.2286"), "RUB")  # (11340 + 1500) / 1050 kg
    assert calculate(cement(order_multiple=D(8)), now=NOW).ordered == Quantity(D(24), "pc")
    minimum = calculate(cement(minimum_order=D(40)), now=NOW)
    assert minimum.ordered == Quantity(D(40), "pc")
    assert minimum.purchased == Quantity(D(2000), "kg")


# --- taxes and duties -------------------------------------------------------------------------


def test_no_tax_or_duty_is_added_implicitly() -> None:
    s = calculate(cement(price_includes_vat=None), now=NOW)
    assert WarningCode.TAX_NOT_SPECIFIED in codes(s)
    assert set(amounts(s)) == {"goods", "delivery"}
    with pytest.raises(DomainValidationError):  # tax types never carry a default value
        ComponentType.create(
            code="vat2", label="НДС", category=Category.TAX, basis=Basis.PERCENT,
            default_value=D(20),
        )  # fmt: skip


def test_tax_entered_explicitly_needs_its_source() -> None:
    with pytest.raises(DomainValidationError):
        calculate(
            cement(price_includes_vat=False, components=(delivery(), K("vat", D(20)))), now=NOW
        )
    with pytest.raises(DomainValidationError):
        calculate(cement(components=(delivery(), K("duty", D(5)))), now=NOW)
    vat = K("vat", D(20), source="договор: цены без НДС")
    s = calculate(cement(price_includes_vat=False, components=(delivery(), vat)), now=NOW)
    assert amounts(s)["vat"] == D("2460.00")  # 20% of goods + delivery
    row = next(c for c in s.components if c.code == "vat")
    assert (row.origin, row.source) == (ValueOrigin.MANUAL, "договор: цены без НДС")
    assert WarningCode.VAT_RECOVERY_NOT_ASSUMED in codes(s)
    with pytest.raises(DomainValidationError):  # VAT already inside the price
        calculate(cement(price_includes_vat=True, components=(delivery(), vat)), now=NOW)


def test_tax_from_a_configured_verified_source() -> None:
    s = calculate(cement(price_includes_vat=False, verified_rates=(tariff(),)), now=NOW)
    row = next(c for c in s.components if c.code == "vat")
    assert row.amount == D("2460.00")
    assert row.origin is ValueOrigin.VERIFIED_SOURCE
    assert row.verified_rate is not None
    assert s.input["verified_rates"][0]["source"]["verified_by"] == "user:accountant"  # type: ignore[index]


def test_verified_rate_outside_its_validity_is_unknown() -> None:
    expired = tariff(valid_to=date(2026, 6, 30))
    s = calculate(cement(price_includes_vat=False, verified_rates=(expired,)), now=NOW)
    assert amounts(s)["vat"] is None
    assert WarningCode.TAX_SOURCE_NOT_VALID in codes(s)
    assert s.total is None


def test_verified_sources_only_for_taxes_and_need_a_verifier() -> None:
    with pytest.raises(DomainValidationError):
        calculate(cement(verified_rates=(tariff(code="delivery"),)), now=NOW)
    with pytest.raises(DomainValidationError):
        VerifiedRateSource.create(name="Справочник", reference="", verified_by="", verified_at=NOW)


# --- FX ---------------------------------------------------------------------------------------


def test_foreign_currency_with_fixed_rate_and_date() -> None:
    usd = FxRate.create(
        base="USD",
        quote="RUB",
        rate=D("92.5000"),
        effective_date=date(2026, 11, 12),
        source="ЦБ РФ",
    )
    s = calculate(
        cement(price=Money(D("5.80"), "USD"), components=(delivery("40", "USD"),), fx_rates=(usd,)),
        now=NOW,
    )
    assert amounts(s) == {"goods": D("10730.00"), "delivery": D("3700.00")}
    goods = s.components[0]
    assert goods.original == Money(D("116.00"), "USD")
    assert goods.fx == usd
    assert s.input["fx_used"] == [usd.to_json()]
    assert s.complete


def test_missing_fx_makes_goods_and_percentages_unknown() -> None:
    eur = Money(D("6.00"), "EUR")
    s = calculate(cement(price=eur, components=(K("discount", D(5)), delivery())), now=NOW)
    assert amounts(s) == {"goods": None, "discount": None, "delivery": D("1500.00")}
    assert s.total is None
    assert s.known_subtotal == Money(D("1500.00"), "RUB")
    assert {WarningCode.FX_MISSING, WarningCode.UNKNOWN_COMPONENT} <= codes(s)


def test_fx_stale_inverse_and_future() -> None:
    eur = Money(D("6.00"), "EUR")
    old = FxRate.create(
        base="EUR", quote="RUB", rate=D(100), effective_date=date(2026, 10, 1), source="банк"
    )
    assert WarningCode.FX_STALE in codes(calculate(cement(price=eur, fx_rates=(old,)), now=NOW))
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


# --- confirmations, validation, snapshots ----------------------------------------------------


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
    with pytest.raises(DomainValidationError):  # an amount needs a currency
        calculate(cement(components=(K("delivery", D(10)),)), now=NOW)
    with pytest.raises(DomainValidationError):
        FxRate.create(base="RUB", quote="RUB", rate=D(1), effective_date=TODAY, source="x")


def test_snapshot_is_immutable_reproducible_and_json_safe() -> None:
    first = calculate(cement(), now=NOW)
    again = calculate(cement(), now=NOW, supersedes_id=first.id)
    assert again.id != first.id
    assert again.supersedes_id == first.id
    assert again.input_hash == first.input_hash
    assert calculate(cement(components=(delivery("1600"),)), now=NOW).input_hash != first.input_hash
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.total = None  # type: ignore[misc]
    text = json.dumps(first.to_json())
    assert '"12300.00"' in text
