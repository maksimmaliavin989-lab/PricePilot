"""TASK_006: units, packaging and explainable matching."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from pricepilot.domain.catalog import IdentifierScheme, Product, ProductIdentifier
from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.matching import (
    MATCHER_VERSION,
    AutoLinkPolicy,
    CatalogEntry,
    DecisionKind,
    Feature,
    MatchHistory,
    NameFeatures,
    OfferView,
    ThresholdCalibration,
    candidates,
    fold,
    score_candidate,
)
from pricepilot.domain.units import (
    ConversionError,
    Pack,
    PackSource,
    conversion_to,
    convert,
    pack_from_name,
    standard_factor,
)
from pricepilot.domain.values import Quantity

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
WS = uuid4()
SUPPLIER_A, SUPPLIER_B = uuid4(), uuid4()


def product(name: str, base_unit: str = "pc", code: str | None = None) -> Product:
    return Product.create(
        workspace_id=WS,
        canonical_name=name,
        brand=None,
        manufacturer_code=code,
        attributes={},
        base_unit=base_unit,
        now=NOW,
    )


def entry(p: Product, *ids: tuple[IdentifierScheme, str, str | None]) -> CatalogEntry:
    return CatalogEntry(
        p,
        tuple(
            ProductIdentifier.create(product_id=p.id, scheme=s, value=v, issuer=i)
            for s, v, i in ids
        ),
    )


def offer(name: str, unit: str | None = "pc", **kw: object) -> OfferView:
    supplier = kw.pop("supplier", SUPPLIER_A)
    assert isinstance(supplier, UUID)
    pack = kw.pop("pack", None)
    if pack is None and unit is not None:
        pack = pack_from_name(name, unit)
    return OfferView(uuid4(), supplier, name, unit, pack=pack, **kw)  # type: ignore[arg-type]


def codes(c: object) -> set[Feature]:
    return {f.code for f in c.features}  # type: ignore[attr-defined]


# --- units and packaging --------------------------------------------------------------------


def test_standard_conversions_inside_one_dimension() -> None:
    assert standard_factor("t", "kg") == 1000
    assert convert(Quantity(Decimal("2.5"), "t"), "kg") == Quantity(Decimal("2500.0"), "kg")
    assert standard_factor("l", "m3") == Decimal("0.001")
    assert conversion_to("t", "kg", None).method == "standard"


@pytest.mark.parametrize(
    ("source", "target"), [("kg", "m3"), ("m", "m2"), ("pc", "bag"), ("pc", "kg")]
)
def test_incompatible_units_are_refused(source: str, target: str) -> None:
    with pytest.raises(ConversionError):
        standard_factor(source, target)


@pytest.mark.parametrize(
    ("name", "unit", "content"),
    [
        ("Цемент М500 Д0, мешок 50 кг", "pc", Quantity(Decimal("50"), "kg")),
        ("Саморез по дереву 4,2×70 мм, уп. 500 шт.", "pack", Quantity(Decimal("500"), "pc")),
        ("Краска фасадная, 14 кг", "pc", Quantity(Decimal("14"), "kg")),
        ("Минеральная вата 50 мм, уп. 6 м²", "pack", Quantity(Decimal("6"), "m2")),
        ("Лента армирующая 45 мм, 90 м", "roll", Quantity(Decimal("90"), "m")),
        ("Клей, 500 г", "pc", Quantity(Decimal("0.500"), "kg")),
    ],
)
def test_pack_from_name_is_a_suggestion(name: str, unit: str, content: Quantity) -> None:
    pack = pack_from_name(name, unit)
    assert pack is not None
    assert pack.content == content
    assert pack.source is PackSource.NAME
    assert not pack.confirmed
    conversion = conversion_to(unit, content.unit, pack)
    assert (conversion.method, conversion.confirmed) == ("packaging", False)
    assert pack.confirm().confirmed


@pytest.mark.parametrize(
    ("name", "unit"),
    [
        ("ГКЛ 12,5 мм, 1200×2500 мм", "sheet"),  # dimensions are not contents
        ("Плёнка полиэтиленовая 200 мкм, 3×100 м", "roll"),
        ("Профиль направляющий ПН 27×28, 3 м", "pc"),  # length of a piece, not a pack
        ("Песок строительный", "m3"),  # measurable unit: no packaging
        ("Гвозди строительные 100 мм", "kg"),
    ],
)
def test_no_pack_where_numbers_are_dimensions(name: str, unit: str) -> None:
    assert pack_from_name(name, unit) is None


def test_pack_validation() -> None:
    with pytest.raises(DomainValidationError):
        Pack.create(
            unit="pc",
            content=Quantity(Decimal(0), "kg"),
            source=PackSource.USER,
            evidence="",
            confirmed=True,
        )
    with pytest.raises(DomainValidationError):
        Pack.create(
            unit="pc",
            content=Quantity(Decimal(5), "kg"),
            source=PackSource.USER,
            evidence="",
            confirmed=False,
        )


# --- normalisation --------------------------------------------------------------------------


def test_fold_unifies_lookalikes_and_ocr_zero() -> None:
    assert fold("Цемент M500") == fold("цемент М500")
    assert fold("М50о") == "м500"
    assert fold("600×300") == fold("600x300") == fold("600х300")


def test_name_features() -> None:
    f = NameFeatures.of("Газобетонный блок D500 600×300×200")
    assert f.grades == {"д500"}
    assert f.dimensions == {(Decimal(600), Decimal(300), Decimal(200))}
    assert f.tokens == {"газобетонный", "блок"}
    assert NameFeatures.of("ГКЛ 12,5 мм").measures == {("length_mm", Decimal("12.5"))}


# --- candidates -----------------------------------------------------------------------------


def test_different_packs_of_a_piece_product_are_not_matched() -> None:
    """The acceptance case: a 25 kg bag is not a 50 kg bag when the product is a bag."""
    bag50 = entry(product("Цемент М500 Д0, мешок 50 кг", "pc"))
    candidate = score_candidate(offer("Цемент М500 Д0, мешок 25 кг"), bag50)
    assert candidate.blocked
    assert candidate.score == 0
    assert Feature.PACK_CONFLICT in codes(candidate)
    # Still shown (after the others) so the user sees why it is not offered.
    assert candidates(offer("Цемент М500 Д0, мешок 25 кг"), [bag50])[0].blocked


def test_different_packs_of_a_product_sold_by_weight_are_comparable() -> None:
    cement_kg = entry(product("Цемент М500 Д0, мешок 50 кг", "kg"))
    candidate = score_candidate(offer("Цемент М500 Д0, мешок 25 кг"), cement_kg)
    assert not candidate.blocked
    assert Feature.PACK_DIFFERS_NORMALIZABLE in codes(candidate)
    assert candidate.score >= Decimal("0.5")


@pytest.mark.parametrize(
    ("offer_name", "product_name"),
    [
        ("Цемент М400 Д20, мешок 50 кг", "Цемент М500 Д0, мешок 50 кг"),
        ("Газобетонный блок D500 600×250×200", "Газобетонный блок D500 600×300×200"),
        ("Газобетонный блок D600 600×300×200", "Газобетонный блок D500 600×300×200"),
        ("ГКЛ 9,5 мм, 1200×2500 мм", "ГКЛ 12,5 мм, 1200×2500 мм"),
    ],
)
def test_attribute_conflicts_block(offer_name: str, product_name: str) -> None:
    candidate = score_candidate(offer(offer_name, "sheet"), entry(product(product_name, "sheet")))
    assert candidate.blocked
    assert codes(candidate) & {Feature.ATTRIBUTE_CONFLICT, Feature.PACK_CONFLICT}


def test_ocr_variant_of_the_same_product_matches() -> None:
    cement = entry(product("Цемент М500 Д0, мешок 50 кг", "pc"))
    candidate = score_candidate(offer("цемент м50о до, мешок 50 кг"), cement)
    assert not candidate.blocked
    assert candidate.score >= Decimal("0.6")
    details = [f.detail for f in candidate.features if f.code is Feature.ATTRIBUTE_EQUAL]
    assert "марка м500" in details
    assert "mass_kg: 50" in details


def test_supplier_sku_is_evidence_only_for_its_supplier() -> None:
    p = product("Штукатурка гипсовая, мешок 30 кг")
    e = entry(p, (IdentifierScheme.SUPPLIER_SKU, "СМ-101", str(SUPPLIER_A)))
    own = score_candidate(offer("Штукатурка гипс. 30кг", supplier_sku="см 101"), e)
    other = score_candidate(
        offer("Штукатурка гипс. 30кг", supplier_sku="СМ-101", supplier=SUPPLIER_B), e
    )
    assert Feature.SUPPLIER_SKU_EQUAL in codes(own)
    assert Feature.SKU_OF_OTHER_SUPPLIER in codes(other)
    assert own.score - other.score == Decimal("0.50")


def test_gtin_equal_and_conflict() -> None:
    p = product("Клей плиточный, мешок 25 кг")
    e = entry(p, (IdentifierScheme.GTIN, "4006381333931", None))
    assert Feature.GTIN_EQUAL in codes(score_candidate(offer("Клей", gtin="4006381333931"), e))
    conflict = score_candidate(offer("Клей плиточный, мешок 25 кг", gtin="96385074"), e)
    assert conflict.blocked
    assert Feature.GTIN_CONFLICT in codes(conflict)


def test_unit_without_packaging_is_penalised() -> None:
    sand = entry(product("Песок строительный карьерный", "m3"))
    by_bag = score_candidate(offer("Песок строительный карьерный", "bag"), sand)
    by_m3 = score_candidate(offer("Песок строительный карьерный", "m3"), sand)
    assert Feature.UNIT_INCOMPATIBLE in codes(by_bag)
    assert by_bag.score < by_m3.score


def test_candidates_are_ranked_and_filtered() -> None:
    catalog = [
        entry(product("Цемент М500 Д0, мешок 50 кг")),
        entry(product("Песок строительный карьерный", "m3")),
        entry(product("Цемент М500 Д0, мешок 50 кг (снят)")),
    ]
    catalog[2] = CatalogEntry(replace(catalog[2].product, active=False))
    found = candidates(offer("Цемент М500 Д0, мешок 50 кг"), catalog)
    assert [c.product_id for c in found] == [catalog[0].product.id]
    assert found[0].to_json()["features"]


# --- decisions ------------------------------------------------------------------------------


def test_accept_reject_revoke_are_append_only() -> None:
    o = offer("Цемент М500 Д0, мешок 50 кг")
    a, b = entry(product("Цемент М500 Д0, мешок 50 кг")), entry(product("Цемент М500"))
    history = MatchHistory(o.offer_id)
    first = history.decide(
        kind=DecisionKind.ACCEPT,
        product_id=a.product.id,
        actor="user:1",
        now=NOW,
        candidate=score_candidate(o, a),
    )
    assert history.accepted_product == a.product.id
    assert first.candidate is not None
    with pytest.raises(DomainValidationError):
        history.decide(kind=DecisionKind.ACCEPT, product_id=b.product.id, actor="user:1", now=NOW)
    undo = history.decide(
        kind=DecisionKind.REVOKE, product_id=a.product.id, actor="user:1", now=NOW, reason="ошибка"
    )
    assert undo.supersedes_id == first.id
    assert history.accepted_product is None
    assert len(history.decisions) == 2  # nothing deleted
    reject = history.decide(
        kind=DecisionKind.REJECT, product_id=a.product.id, actor="user:1", now=NOW
    )
    assert history.rejected_products() == {a.product.id}
    history.decide(kind=DecisionKind.ACCEPT, product_id=b.product.id, actor="user:1", now=NOW)
    assert history.accepted_product == b.product.id
    assert reject.supersedes_id is None  # the accept it would replace was already revoked


def test_accepting_a_blocked_candidate_needs_a_reason() -> None:
    o = offer("Цемент М500 Д0, мешок 25 кг")
    bag50 = entry(product("Цемент М500 Д0, мешок 50 кг"))
    blocked = score_candidate(o, bag50)
    history = MatchHistory(o.offer_id)
    with pytest.raises(DomainValidationError):
        history.decide(
            kind=DecisionKind.ACCEPT,
            product_id=bag50.product.id,
            actor="user:1",
            now=NOW,
            candidate=blocked,
        )
    decision = history.decide(
        kind=DecisionKind.ACCEPT,
        product_id=bag50.product.id,
        actor="user:1",
        now=NOW,
        reason="одна позиция в договоре",
        candidate=blocked,
    )
    assert decision.override


def test_revoke_without_decision_and_foreign_candidate() -> None:
    o = offer("Цемент")
    p = entry(product("Цемент"))
    history = MatchHistory(o.offer_id)
    with pytest.raises(DomainValidationError):
        history.decide(kind=DecisionKind.REVOKE, product_id=p.product.id, actor="user:1", now=NOW)
    other = score_candidate(offer("Цемент"), p)
    with pytest.raises(DomainValidationError):
        history.decide(
            kind=DecisionKind.ACCEPT,
            product_id=p.product.id,
            actor="user:1",
            now=NOW,
            candidate=other,
        )


def calibration(**kw: object) -> ThresholdCalibration:
    args: dict[str, object] = {
        "dataset": "обезличенные прайсы пилота, 12 поставщиков",
        "real_anonymised": True,
        "matcher_version": MATCHER_VERSION,
        "lines_evaluated": 1200,
        "wrong_links_at_threshold": 0,
        "approved_by": "user:owner",
        "approved_at": NOW,
    }
    args.update(kw)
    return ThresholdCalibration.create(**args)  # type: ignore[arg-type]


def test_auto_link_stays_off_without_a_real_data_calibration() -> None:
    p = product("Штукатурка гипсовая, мешок 30 кг")
    e = entry(p, (IdentifierScheme.SUPPLIER_SKU, "СМ-101", str(SUPPLIER_A)))
    by_sku = offer("Штукатурка гипсовая, мешок 30 кг", supplier_sku="СМ-101")
    strong = score_candidate(by_sku, e)
    assert strong.score >= Decimal("0.9")
    # Default, and a bare threshold without calibration: nothing is linked.
    assert not AutoLinkPolicy().enabled
    assert AutoLinkPolicy().apply(MatchHistory(by_sku.offer_id), strong, NOW) is None
    bare = AutoLinkPolicy(threshold=Decimal("0.80"))
    assert not bare.enabled
    assert bare.apply(MatchHistory(by_sku.offer_id), strong, NOW) is None
    # Synthetic data or too few lines cannot calibrate a threshold.
    with pytest.raises(DomainValidationError):
        calibration(real_anonymised=False)
    with pytest.raises(DomainValidationError):
        calibration(lines_evaluated=50)
    # A calibration of another matcher version does not enable the policy.
    outdated = AutoLinkPolicy.calibrated(Decimal("0.80"), calibration(matcher_version="match-0"))
    assert not outdated.enabled
    with pytest.raises(DomainValidationError):
        AutoLinkPolicy.calibrated(Decimal("0.2"), calibration())


def test_calibrated_auto_link_needs_identifier_evidence() -> None:
    p = product("Штукатурка гипсовая, мешок 30 кг")
    e = entry(p, (IdentifierScheme.SUPPLIER_SKU, "СМ-101", str(SUPPLIER_A)))
    by_sku = offer("Штукатурка гипсовая, мешок 30 кг", supplier_sku="СМ-101")
    by_name = offer("Штукатурка гипсовая, мешок 30 кг")
    policy = AutoLinkPolicy.calibrated(Decimal("0.80"), calibration())
    assert policy.enabled
    linked = policy.apply(MatchHistory(by_sku.offer_id), score_candidate(by_sku, e), NOW)
    assert linked is not None
    assert linked.actor.startswith("rule:auto-link@")
    assert policy.apply(MatchHistory(by_name.offer_id), score_candidate(by_name, e), NOW) is None
    rejected = MatchHistory(by_sku.offer_id)
    rejected.decide(kind=DecisionKind.REJECT, product_id=p.id, actor="user:1", now=NOW)
    assert policy.apply(rejected, score_candidate(by_sku, e), NOW) is None
