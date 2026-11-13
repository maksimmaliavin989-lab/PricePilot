"""SQL adapters for supplier offers, match decisions and landed-cost data (TASK_006)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, select

from pricepilot.application.ports import Page, PageRequest
from pricepilot.domain.common import new_id
from pricepilot.domain.costs import (
    CostPolicy,
    CostSnapshot,
    FxRate,
    VerifiedRate,
    VerifiedRateSource,
    component_from_json,
    policy_from_json,
)
from pricepilot.domain.matching import DecisionKind, MatchDecision
from pricepilot.domain.offers import (
    Comparison,
    ComparisonItem,
    ComparisonOffer,
    OfferTerms,
    SupplierOffer,
)
from pricepilot.domain.units import pack_from_json
from pricepilot.domain.values import Money, Quantity, decimal_text
from pricepilot.infrastructure.db.repositories import paginate, stamp_of, to_page
from pricepilot.infrastructure.db.tables import (
    comparison_items,
    comparison_offers,
    comparisons,
    cost_policies,
    cost_snapshots,
    fx_rates,
    match_decisions,
    supplier_offers,
    tax_rate_sources,
    tax_rates,
)


def _offer(row: Row[Any]) -> SupplierOffer:
    return SupplierOffer(
        id=row.id,
        workspace_id=row.workspace_id,
        supplier_id=row.supplier_id,
        document_id=row.source_document_id,
        line_id=row.extracted_line_id,
        line_ordinal=row.line_ordinal,
        price_field_key=row.price_field_key,
        name=row.raw_name,
        supplier_sku=row.supplier_sku,
        unit=row.unit_code,
        price=Money(Decimal(row.price_amount), row.currency),
        price_confirmed=row.price_confirmed,
        pack=pack_from_json(row.pack_json) if row.pack_json else None,
        stamp=stamp_of(row),
    )


def _offer_values(o: SupplierOffer) -> dict[str, Any]:
    return {
        "workspace_id": o.workspace_id,
        "supplier_id": o.supplier_id,
        "source_document_id": o.document_id,
        "extracted_line_id": o.line_id,
        "line_ordinal": o.line_ordinal,
        "price_field_key": o.price_field_key,
        "raw_name": o.name,
        "supplier_sku": o.supplier_sku,
        "unit_code": o.unit,
        "price_amount": decimal_text(o.price.amount),
        "currency": o.price.currency,
        "price_confirmed": o.price_confirmed,
        "pack_json": o.pack.to_json() if o.pack else None,
        "created_at": o.stamp.created_at,
        "updated_at": o.stamp.updated_at,
        "version": o.stamp.version,
    }


class SqlOfferRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, offer: SupplierOffer) -> None:
        self._c.execute(supplier_offers.insert().values(id=offer.id, **_offer_values(offer)))

    def get(self, offer_id: UUID) -> SupplierOffer | None:
        row = self._c.execute(
            select(supplier_offers).where(supplier_offers.c.id == offer_id)
        ).first()
        return _offer(row) if row else None

    def keys_for_document(self, document_id: UUID) -> set[tuple[UUID, str]]:
        rows = self._c.execute(
            select(supplier_offers.c.extracted_line_id, supplier_offers.c.price_field_key).where(
                supplier_offers.c.source_document_id == document_id
            )
        )
        return {(r.extracted_line_id, r.price_field_key) for r in rows}

    def list(
        self, workspace_id: UUID, page: PageRequest, *, document_id: UUID | None
    ) -> Page[SupplierOffer]:
        stmt = select(supplier_offers).where(supplier_offers.c.workspace_id == workspace_id)
        if document_id is not None:
            stmt = stmt.where(supplier_offers.c.source_document_id == document_id)
        rows = list(self._c.execute(paginate(supplier_offers, stmt, page)))
        return to_page(rows, page, _offer)

    def update(self, offer: SupplierOffer, expected_version: int) -> bool:
        result = self._c.execute(
            supplier_offers.update()
            .where(
                supplier_offers.c.id == offer.id,
                supplier_offers.c.version == expected_version,
            )
            .values(**_offer_values(offer))
        )
        return result.rowcount == 1


def _decision(row: Row[Any]) -> MatchDecision:
    return MatchDecision(
        id=row.id,
        offer_id=row.offer_id,
        product_id=row.product_id,
        kind=DecisionKind(row.decision),
        actor=row.actor_id,
        reason=row.reason,
        supersedes_id=row.supersedes_id,
        decided_at=row.decided_at,
        candidate=row.candidate_json,
        override=row.override,
    )


class SqlMatchRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, d: MatchDecision) -> None:
        self._c.execute(
            match_decisions.insert().values(
                id=d.id,
                offer_id=d.offer_id,
                product_id=d.product_id,
                decision=d.kind.value,
                actor_id=d.actor,
                reason=d.reason,
                supersedes_id=d.supersedes_id,
                candidate_json=d.candidate,
                override=d.override,
                decided_at=d.decided_at,
            )
        )

    def for_offers(self, offer_ids: list[UUID]) -> dict[UUID, list[MatchDecision]]:
        result: dict[UUID, list[MatchDecision]] = {i: [] for i in offer_ids}
        if offer_ids:
            for row in self._c.execute(
                select(match_decisions)
                .where(match_decisions.c.offer_id.in_(offer_ids))
                .order_by(match_decisions.c.decided_at, match_decisions.c.id)
            ):
                result[row.offer_id].append(_decision(row))
        return result


def _rate_source(row: Row[Any]) -> VerifiedRateSource:
    return VerifiedRateSource(row.id, row.name, row.reference, row.verified_by, row.verified_at)


class SqlCostRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    # policy -----------------------------------------------------------------------------
    def get_policy(self, workspace_id: UUID) -> tuple[CostPolicy, int] | None:
        row = self._c.execute(
            select(cost_policies).where(cost_policies.c.workspace_id == workspace_id)
        ).first()
        if row is None:
            return None
        return policy_from_json({"name": row.name, "types": row.types_json}), row.version

    def save_policy(
        self,
        workspace_id: UUID,
        policy: CostPolicy,
        *,
        expected_version: int | None,
        policy_id: UUID,
        now: Any,
    ) -> bool:
        values = {
            "name": policy.name,
            "types_json": policy.to_json()["types"],
            "fingerprint": policy.fingerprint,
            "updated_at": now,
        }
        if expected_version is None:
            self._c.execute(
                cost_policies.insert().values(
                    id=policy_id, workspace_id=workspace_id, created_at=now, version=1, **values
                )
            )
            return True
        result = self._c.execute(
            cost_policies.update()
            .where(
                cost_policies.c.workspace_id == workspace_id,
                cost_policies.c.version == expected_version,
            )
            .values(version=expected_version + 1, **values)
        )
        return result.rowcount == 1

    # FX ---------------------------------------------------------------------------------
    def add_fx(self, workspace_id: UUID, rate_id: UUID, rate: FxRate, now: Any) -> None:
        self._c.execute(
            fx_rates.insert().values(
                id=rate_id,
                workspace_id=workspace_id,
                base_currency=rate.base,
                quote_currency=rate.quote,
                rate=decimal_text(rate.rate),
                effective_date=rate.effective_date,
                source=rate.source,
                created_at=now,
            )
        )

    def list_fx(self, workspace_id: UUID) -> list[tuple[UUID, FxRate]]:
        rows = self._c.execute(
            select(fx_rates)
            .where(fx_rates.c.workspace_id == workspace_id)
            .order_by(fx_rates.c.effective_date.desc(), fx_rates.c.created_at.desc())
        )
        return [
            (
                r.id,
                FxRate(
                    r.base_currency,
                    r.quote_currency,
                    Decimal(r.rate),
                    r.effective_date,
                    r.source,
                ),
            )
            for r in rows
        ]

    # verified tax/duty rates ------------------------------------------------------------
    def add_rate_source(
        self, workspace_id: UUID, source: VerifiedRateSource, rates: list[VerifiedRate], now: Any
    ) -> list[UUID]:
        self._c.execute(
            tax_rate_sources.insert().values(
                id=source.id,
                workspace_id=workspace_id,
                name=source.name,
                reference=source.reference,
                verified_by=source.verified_by,
                verified_at=source.verified_at,
                created_at=now,
            )
        )
        ids = []
        for rate in rates:
            rate_id = new_id()
            ids.append(rate_id)
            self._c.execute(
                tax_rates.insert().values(
                    id=rate_id,
                    source_id=source.id,
                    component_code=rate.component_code,
                    rate=decimal_text(rate.rate),
                    valid_from=rate.valid_from,
                    valid_to=rate.valid_to,
                    scope=rate.scope,
                )
            )
        return ids

    def list_rates(self, workspace_id: UUID) -> list[VerifiedRate]:
        sources = {
            r.id: _rate_source(r)
            for r in self._c.execute(
                select(tax_rate_sources).where(tax_rate_sources.c.workspace_id == workspace_id)
            )
        }
        if not sources:
            return []
        rows = self._c.execute(
            select(tax_rates)
            .where(tax_rates.c.source_id.in_(list(sources)))
            .order_by(tax_rates.c.component_code, tax_rates.c.valid_from)
        )
        return [
            VerifiedRate(
                sources[r.source_id],
                r.component_code,
                Decimal(r.rate),
                r.valid_from,
                r.valid_to,
                r.scope,
            )
            for r in rows
        ]

    def list_rate_sources(self, workspace_id: UUID) -> list[VerifiedRateSource]:
        return [
            _rate_source(r)
            for r in self._c.execute(
                select(tax_rate_sources)
                .where(tax_rate_sources.c.workspace_id == workspace_id)
                .order_by(tax_rate_sources.c.created_at)
            )
        ]

    # comparisons ------------------------------------------------------------------------
    def add_comparison(self, c: Comparison) -> None:
        self._c.execute(
            comparisons.insert().values(
                id=c.id,
                workspace_id=c.workspace_id,
                name=c.name,
                target_currency=c.target_currency,
                as_of_date=c.as_of,
                created_at=c.stamp.created_at,
                updated_at=c.stamp.updated_at,
                version=c.stamp.version,
            )
        )

    def get_comparison(self, comparison_id: UUID) -> Comparison | None:
        row = self._c.execute(select(comparisons).where(comparisons.c.id == comparison_id)).first()
        if row is None:
            return None
        return Comparison(
            row.id, row.workspace_id, row.name, row.target_currency, row.as_of_date, stamp_of(row)
        )

    def list_comparisons(self, workspace_id: UUID, page: PageRequest) -> Page[Comparison]:
        stmt = select(comparisons).where(comparisons.c.workspace_id == workspace_id)
        rows = list(self._c.execute(paginate(comparisons, stmt, page)))
        return to_page(
            rows,
            page,
            lambda r: Comparison(
                r.id, r.workspace_id, r.name, r.target_currency, r.as_of_date, stamp_of(r)
            ),
        )

    def add_item(self, item: ComparisonItem) -> None:
        self._c.execute(
            comparison_items.insert().values(
                id=item.id,
                comparison_id=item.comparison_id,
                product_id=item.product_id,
                required_quantity=decimal_text(item.required.value),
                unit_code=item.required.unit,
                position=item.position,
            )
        )

    def items(self, comparison_id: UUID) -> list[ComparisonItem]:
        rows = self._c.execute(
            select(comparison_items)
            .where(comparison_items.c.comparison_id == comparison_id)
            .order_by(comparison_items.c.position)
        )
        return [
            ComparisonItem(
                r.id,
                r.comparison_id,
                r.product_id,
                Quantity(Decimal(r.required_quantity), r.unit_code),
                r.position,
            )
            for r in rows
        ]

    def get_item(self, item_id: UUID) -> ComparisonItem | None:
        r = self._c.execute(
            select(comparison_items).where(comparison_items.c.id == item_id)
        ).first()
        if r is None:
            return None
        return ComparisonItem(
            r.id,
            r.comparison_id,
            r.product_id,
            Quantity(Decimal(r.required_quantity), r.unit_code),
            r.position,
        )

    def add_comparison_offer(self, co: ComparisonOffer) -> None:
        self._c.execute(
            comparison_offers.insert().values(
                id=co.id,
                item_id=co.item_id,
                offer_id=co.offer_id,
                match_decision_id=co.match_decision_id,
                terms_json=terms_to_json(co.terms),
                created_at=co.created_at,
            )
        )

    def update_terms(self, comparison_offer_id: UUID, terms: OfferTerms) -> bool:
        result = self._c.execute(
            comparison_offers.update()
            .where(comparison_offers.c.id == comparison_offer_id)
            .values(terms_json=terms_to_json(terms))
        )
        return result.rowcount == 1

    def comparison_offers(self, item_ids: list[UUID]) -> list[ComparisonOffer]:
        if not item_ids:
            return []
        rows = self._c.execute(
            select(comparison_offers)
            .where(comparison_offers.c.item_id.in_(item_ids))
            .order_by(comparison_offers.c.created_at, comparison_offers.c.id)
        )
        return [_comparison_offer(r) for r in rows]

    def get_comparison_offer(self, comparison_offer_id: UUID) -> ComparisonOffer | None:
        row = self._c.execute(
            select(comparison_offers).where(comparison_offers.c.id == comparison_offer_id)
        ).first()
        return _comparison_offer(row) if row else None

    # snapshots --------------------------------------------------------------------------
    def add_snapshot(self, comparison_offer_id: UUID, s: CostSnapshot) -> None:
        self._c.execute(
            cost_snapshots.insert().values(
                id=s.id,
                comparison_offer_id=comparison_offer_id,
                formula_version=s.formula_version,
                policy_fingerprint=s.policy_fingerprint,
                input_hash=s.input_hash,
                snapshot_json=s.to_json(),
                total_amount=decimal_text(s.total.amount) if s.total else None,
                unit_amount=decimal_text(s.unit_cost.amount) if s.unit_cost else None,
                currency=s.known_subtotal.currency,
                complete=s.complete,
                supersedes_id=s.supersedes_id,
                created_at=s.created_at,
            )
        )

    def latest_snapshots(self, comparison_offer_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        """Latest snapshot (JSON form) per comparison offer."""
        latest: dict[UUID, dict[str, Any]] = {}
        if comparison_offer_ids:
            for row in self._c.execute(
                select(cost_snapshots)
                .where(cost_snapshots.c.comparison_offer_id.in_(comparison_offer_ids))
                .order_by(cost_snapshots.c.created_at, cost_snapshots.c.id)
            ):
                latest[row.comparison_offer_id] = dict(row.snapshot_json)
        return latest

    def get_snapshot(self, snapshot_id: UUID) -> dict[str, Any] | None:
        row = self._c.execute(
            select(cost_snapshots.c.snapshot_json).where(cost_snapshots.c.id == snapshot_id)
        ).first()
        return dict(row.snapshot_json) if row else None


def terms_to_json(terms: OfferTerms) -> dict[str, Any]:
    return {
        "components": [c.to_json() for c in terms.components],
        "minimum_order": decimal_text(terms.minimum_order) if terms.minimum_order else None,
        "order_multiple": decimal_text(terms.order_multiple) if terms.order_multiple else None,
        "price_includes_vat": terms.price_includes_vat,
    }


def terms_from_json(payload: dict[str, Any]) -> OfferTerms:
    return OfferTerms(
        components=tuple(component_from_json(c) for c in payload.get("components", [])),
        minimum_order=Decimal(payload["minimum_order"]) if payload.get("minimum_order") else None,
        order_multiple=(
            Decimal(payload["order_multiple"]) if payload.get("order_multiple") else None
        ),
        price_includes_vat=payload.get("price_includes_vat"),
    )


def _comparison_offer(row: Row[Any]) -> ComparisonOffer:
    return ComparisonOffer(
        row.id,
        row.item_id,
        row.offer_id,
        row.match_decision_id,
        terms_from_json(dict(row.terms_json)),
        row.created_at,
    )
