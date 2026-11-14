"""Supplier offers (one priced line of a price list) and comparisons (TASK_006).

An offer is created from a reviewed extracted line and keeps a link to it: the name, SKU,
unit and price are copied as they are at that moment, the extracted fields stay the source.
A line with several typed prices (опт / розница) gives one offer per price; untyped
``price_1…n`` give an offer only for the price the user accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pricepilot.domain.common import (
    AuditStamp,
    DomainValidationError,
    currency_code,
    display_name,
    ensure_utc,
    new_id,
)
from pricepilot.domain.costs import CostComponent
from pricepilot.domain.extraction import ExtractedField, ExtractedLine, ReviewStatus
from pricepilot.domain.matching import OfferView
from pricepilot.domain.units import Pack, PackSource, pack_from_name
from pricepilot.domain.values import Money, Quantity, parse_decimal, unit_code

_UNTYPED_PRICE = re.compile(r"^price_\d+$")
_CONFIRMED = {ReviewStatus.ACCEPTED, ReviewStatus.CORRECTED}


@dataclass(frozen=True, slots=True)
class SupplierOffer:
    id: UUID
    workspace_id: UUID
    supplier_id: UUID | None
    document_id: UUID
    line_id: UUID
    line_ordinal: int  # position of the line in the price list
    price_field_key: str  # "price", "price_wholesale", "price_2"…
    name: str
    supplier_sku: str | None
    unit: str | None
    price: Money
    price_confirmed: bool
    pack: Pack | None
    stamp: AuditStamp

    def view(self) -> OfferView:
        return OfferView(
            offer_id=self.id,
            supplier_id=self.supplier_id or self.workspace_id,
            name=self.name,
            unit=self.unit,
            supplier_sku=self.supplier_sku,
            pack=self.pack,
        )

    def refreshed(self, current: SupplierOffer, now: datetime) -> SupplierOffer | None:
        """The same offer re-read from its (since reviewed) line: name, SKU, unit, price and
        its confirmation follow the line. A pack confirmed by the user is kept while the unit
        is unchanged. ``None`` when nothing changed."""
        pack = (
            self.pack
            if self.pack and self.pack.confirmed and self.unit == current.unit
            else current.pack
        )
        updated = replace(
            self,
            name=current.name,
            supplier_sku=current.supplier_sku,
            unit=current.unit,
            price=current.price,
            price_confirmed=current.price_confirmed,
            pack=pack,
        )
        if updated == self:
            return None
        return replace(updated, stamp=self.stamp.bumped(now))

    def with_pack(self, content: Quantity, now: datetime) -> SupplierOffer:
        if self.unit is None:
            raise DomainValidationError("unit", "У предложения нет единицы цены.")
        pack = Pack.create(
            unit=self.unit,
            content=content,
            source=PackSource.USER,
            evidence=self.pack.evidence if self.pack else "указано пользователем",
            confirmed=True,
        )
        return replace(self, pack=pack, stamp=self.stamp.bumped(now))


@dataclass(frozen=True, slots=True)
class SkippedLine:
    line_id: UUID
    ordinal: int
    reason: str


def _usable(field: ExtractedField) -> bool:
    return field.review_status is not ReviewStatus.REJECTED and field.normalized_value is not None


def offers_from_line(
    *,
    workspace_id: UUID,
    supplier_id: UUID | None,
    document_id: UUID,
    line: ExtractedLine,
    fields: list[ExtractedField],
    now: datetime,
) -> list[SupplierOffer] | SkippedLine:
    by_key = {f.field_key: f for f in fields}
    name_field = by_key.get("name")
    if name_field is None or not _usable(name_field):
        return SkippedLine(line.id, line.ordinal, "нет названия или оно отклонено")
    assert name_field.normalized_value is not None  # noqa: S101 - checked by _usable
    name = str(name_field.normalized_value.get("value", "")).strip()
    if not name:
        return SkippedLine(line.id, line.ordinal, "пустое название")
    prices = [f for f in fields if f.field_key.startswith("price") and _usable(f)]
    if any(_UNTYPED_PRICE.match(f.field_key) for f in prices):
        prices = [f for f in prices if f.review_status in _CONFIRMED]
        if not prices:
            return SkippedLine(
                line.id, line.ordinal, "несколько цен без типа: выберите цену при проверке"
            )
    if not prices:
        return SkippedLine(line.id, line.ordinal, "нет цены")
    unit_field = by_key.get("unit")
    unit: str | None = None
    if unit_field is not None and _usable(unit_field) and unit_field.normalized_value:
        unit = str(unit_field.normalized_value.get("value"))
    sku_field = by_key.get("sku")
    sku = None
    if sku_field is not None and _usable(sku_field) and sku_field.normalized_value:
        sku = str(sku_field.normalized_value.get("value")) or None
    offers = []
    stamp = AuditStamp.initial(ensure_utc(now))
    for field in sorted(prices, key=lambda f: f.field_key):
        value: dict[str, Any] = field.normalized_value or {}
        price = Money(
            parse_decimal(value.get("amount"), "price.amount"),
            currency_code(str(value.get("currency", "")), "price.currency"),
        )
        offers.append(
            SupplierOffer(
                id=new_id(),
                workspace_id=workspace_id,
                supplier_id=supplier_id,
                document_id=document_id,
                line_id=line.id,
                line_ordinal=line.ordinal,
                price_field_key=field.field_key,
                name=display_name(name, "name", 1000),
                supplier_sku=sku,
                unit=unit,
                price=price,
                price_confirmed=field.review_status in _CONFIRMED,
                pack=pack_from_name(name, unit) if unit else None,
                stamp=stamp,
            )
        )
    return offers


# --- comparisons ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Comparison:
    id: UUID
    workspace_id: UUID
    name: str
    target_currency: str
    as_of: date  # date of the exchange rates and tax rates used
    stamp: AuditStamp

    @staticmethod
    def create(
        *, workspace_id: UUID, name: str, target_currency: str, as_of: date, now: datetime
    ) -> Comparison:
        return Comparison(
            new_id(),
            workspace_id,
            display_name(name, "name", 200),
            currency_code(target_currency, "target_currency"),
            as_of,
            AuditStamp.initial(ensure_utc(now)),
        )


@dataclass(frozen=True, slots=True)
class ComparisonItem:
    id: UUID
    comparison_id: UUID
    product_id: UUID
    required: Quantity  # in the product's base unit
    position: int

    @staticmethod
    def create(
        *, comparison_id: UUID, product_id: UUID, required: Quantity, base_unit: str, position: int
    ) -> ComparisonItem:
        if required.unit != unit_code(base_unit):
            raise DomainValidationError(
                "required_quantity.unit", f"Количество указывается в базовой единице «{base_unit}»."
            )
        if required.value <= 0:
            raise DomainValidationError("required_quantity", "Количество должно быть больше 0.")
        return ComparisonItem(new_id(), comparison_id, product_id, required, position)


@dataclass(frozen=True, slots=True)
class OfferTerms:
    """What the user entered for one offer in one comparison."""

    components: tuple[CostComponent, ...] = ()
    minimum_order: Decimal | None = None
    order_multiple: Decimal | None = None
    price_includes_vat: bool | None = None


@dataclass(frozen=True, slots=True)
class ComparisonOffer:
    id: UUID
    item_id: UUID
    offer_id: UUID
    match_decision_id: UUID  # the accepted match that justifies comparing this offer
    terms: OfferTerms
    created_at: datetime
