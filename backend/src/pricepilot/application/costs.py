"""Use cases: cost policy, exchange and tax rates, comparisons and landed-cost snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pricepilot.application.errors import NotFoundError, VersionConflictError
from pricepilot.application.ports import (
    MAX_PAGE_SIZE,
    Clock,
    CorrelationProvider,
    Page,
    PageRequest,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.catalog import Product
from pricepilot.domain.common import DomainValidationError, new_id, utc_now
from pricepilot.domain.costs import (
    ComponentType,
    CostInput,
    CostPolicy,
    CostSnapshot,
    FxRate,
    VerifiedRate,
    VerifiedRateSource,
    calculate,
)
from pricepilot.domain.matching import DecisionKind, MatchHistory
from pricepilot.domain.offers import (
    Comparison,
    ComparisonItem,
    ComparisonOffer,
    OfferTerms,
    SupplierOffer,
)
from pricepilot.domain.units import ConversionError, conversion_to
from pricepilot.domain.values import Quantity


def _no_correlation() -> str | None:
    return None


@dataclass(frozen=True, slots=True)
class PolicyView:
    policy: CostPolicy
    version: int  # 0 = built-in default, not saved yet


@dataclass(frozen=True, slots=True)
class RateSourceView:
    source: VerifiedRateSource
    rates: list[VerifiedRate]


@dataclass(frozen=True, slots=True)
class ComparisonOfferView:
    comparison_offer: ComparisonOffer
    offer: SupplierOffer
    snapshot: dict[str, Any] | None  # latest snapshot (JSON form) or None


@dataclass(frozen=True, slots=True)
class ItemView:
    item: ComparisonItem
    product: Product
    offers: list[ComparisonOfferView]


@dataclass(frozen=True, slots=True)
class ComparisonView:
    comparison: Comparison
    policy_version: int
    items: list[ItemView]


class CostService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    def _audit(
        self,
        uow: UnitOfWork,
        workspace_id: UUID,
        entity: str,
        entity_id: UUID,
        action: str,
        after: Mapping[str, object],
    ) -> None:
        uow.audit.add(
            AuditEvent.record(
                workspace_id=workspace_id,
                actor_id=LOCAL_ACTOR,
                entity_type=entity,
                entity_id=entity_id,
                action=action,
                before=None,
                after=dict(after),
                now=self._clock(),
                correlation_id=self._correlation(),
            )
        )

    @staticmethod
    def _workspace(uow: UnitOfWork, workspace_id: UUID) -> None:
        if uow.workspaces.get(workspace_id) is None:
            raise NotFoundError("workspace")

    # --- policy ---------------------------------------------------------------------------
    @staticmethod
    def _policy(uow: UnitOfWork, workspace_id: UUID) -> PolicyView:
        stored = uow.costs.get_policy(workspace_id)
        return PolicyView(*stored) if stored else PolicyView(CostPolicy.default(), 0)

    def policy(self, workspace_id: UUID) -> PolicyView:
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            return self._policy(uow, workspace_id)

    def save_policy(
        self,
        workspace_id: UUID,
        *,
        name: str,
        types: Sequence[ComponentType],
        expected_version: int,
    ) -> PolicyView:
        policy = CostPolicy.create(name, list(types))
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            current = self._policy(uow, workspace_id)
            if current.version != expected_version:
                raise VersionConflictError("cost_policy", current.version)
            saved = uow.costs.save_policy(
                workspace_id,
                policy,
                expected_version=expected_version or None,
                policy_id=new_id(),
                now=self._clock(),
            )
            if not saved:
                raise VersionConflictError("cost_policy", current.version)
            self._audit(
                uow,
                workspace_id,
                "cost_policy",
                workspace_id,
                "update",
                {"fingerprint": policy.fingerprint, "types": [t.code for t in policy.types]},
            )
            uow.commit()
        return PolicyView(policy, expected_version + 1)

    # --- FX and verified tax rates --------------------------------------------------------
    def add_fx(self, workspace_id: UUID, rate: FxRate) -> tuple[UUID, FxRate]:
        rate_id = new_id()
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            uow.costs.add_fx(workspace_id, rate_id, rate, self._clock())
            self._audit(uow, workspace_id, "fx_rate", rate_id, "create", dict(rate.to_json()))
            uow.commit()
        return rate_id, rate

    def list_fx(self, workspace_id: UUID) -> list[tuple[UUID, FxRate]]:
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            return uow.costs.list_fx(workspace_id)

    def add_rate_source(
        self,
        workspace_id: UUID,
        *,
        name: str,
        reference: str,
        verified_by: str,
        verified_at: datetime,
        rates: Sequence[tuple[str, Decimal, date, date | None, str | None]],
    ) -> RateSourceView:
        source = VerifiedRateSource.create(
            name=name, reference=reference, verified_by=verified_by, verified_at=verified_at
        )
        built = []
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            policy = self._policy(uow, workspace_id).policy
            for code, rate, valid_from, valid_to, scope in rates:
                ctype = policy.type(code)
                if ctype.category.value not in {"tax", "duty"}:
                    raise DomainValidationError(
                        "rates.component_code", "Проверенные ставки — только для налогов и пошлин."
                    )
                if rate < 0 or rate > 100:
                    raise DomainValidationError("rates.rate", "Ставка — процент от 0 до 100.")
                if valid_to is not None and valid_to < valid_from:
                    raise DomainValidationError("rates.valid_to", "Окончание раньше начала.")
                built.append(VerifiedRate(source, code, rate, valid_from, valid_to, scope))
            if not built:
                raise DomainValidationError("rates", "Добавьте хотя бы одну ставку.")
            uow.costs.add_rate_source(workspace_id, source, built, self._clock())
            self._audit(
                uow,
                workspace_id,
                "tax_rate_source",
                source.id,
                "create",
                {"name": source.name, "verified_by": source.verified_by, "rates": len(built)},
            )
            uow.commit()
        return RateSourceView(source, built)

    def rate_sources(self, workspace_id: UUID) -> list[RateSourceView]:
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            rates = uow.costs.list_rates(workspace_id)
            sources = uow.costs.list_rate_sources(workspace_id)
        return [RateSourceView(s, [r for r in rates if r.source.id == s.id]) for s in sources]

    # --- comparisons ----------------------------------------------------------------------
    def create_comparison(
        self, workspace_id: UUID, *, name: str, target_currency: str, as_of: date
    ) -> ComparisonView:
        comparison = Comparison.create(
            workspace_id=workspace_id,
            name=name,
            target_currency=target_currency,
            as_of=as_of,
            now=self._clock(),
        )
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            uow.costs.add_comparison(comparison)
            self._audit(uow, workspace_id, "comparison", comparison.id, "create", {"name": name})
            uow.commit()
        return self.get(comparison.id)

    def list_comparisons(self, workspace_id: UUID, page: PageRequest) -> Page[Comparison]:
        bounded = PageRequest(limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after=page.after)
        with self._uow() as uow:
            self._workspace(uow, workspace_id)
            return uow.costs.list_comparisons(workspace_id, bounded)

    def _view(self, uow: UnitOfWork, comparison: Comparison) -> ComparisonView:
        items = uow.costs.items(comparison.id)
        cos = uow.costs.comparison_offers([i.id for i in items])
        snapshots = uow.costs.latest_snapshots([c.id for c in cos])
        views = []
        for item in items:
            found = uow.catalog.get(item.product_id)
            assert found is not None  # noqa: S101 - FK
            offers = []
            for co in (c for c in cos if c.item_id == item.id):
                offer = uow.offers.get(co.offer_id)
                assert offer is not None  # noqa: S101 - FK
                offers.append(ComparisonOfferView(co, offer, snapshots.get(co.id)))
            views.append(ItemView(item, found[0], offers))
        policy_version = self._policy(uow, comparison.workspace_id).version
        return ComparisonView(comparison, policy_version, views)

    def get(self, comparison_id: UUID) -> ComparisonView:
        with self._uow() as uow:
            comparison = uow.costs.get_comparison(comparison_id)
            if comparison is None:
                raise NotFoundError("comparison")
            return self._view(uow, comparison)

    def add_item(
        self, comparison_id: UUID, *, product_id: UUID, required: Quantity
    ) -> ComparisonView:
        with self._uow() as uow:
            comparison = uow.costs.get_comparison(comparison_id)
            if comparison is None:
                raise NotFoundError("comparison")
            found = uow.catalog.get(product_id)
            if found is None or found[0].workspace_id != comparison.workspace_id:
                raise NotFoundError("product")
            existing = uow.costs.items(comparison_id)
            if any(i.product_id == product_id for i in existing):
                raise DomainValidationError("product_id", "Товар уже есть в сравнении.")
            item = ComparisonItem.create(
                comparison_id=comparison_id,
                product_id=product_id,
                required=required,
                base_unit=found[0].base_unit_code,
                position=len(existing) + 1,
            )
            uow.costs.add_item(item)
            uow.commit()
        return self.get(comparison_id)

    def _cost_input(
        self,
        uow: UnitOfWork,
        comparison: Comparison,
        item: ComparisonItem,
        offer: SupplierOffer,
        terms: OfferTerms,
    ) -> CostInput:
        found = uow.catalog.get(item.product_id)
        assert found is not None  # noqa: S101 - FK
        product = found[0]
        if offer.unit is None:
            raise DomainValidationError(
                "offer", "У предложения нет единицы цены: исправьте единицу при проверке строки."
            )
        try:
            conversion = conversion_to(offer.unit, product.base_unit_code, offer.pack)
        except ConversionError as exc:
            raise DomainValidationError(
                "offer.pack",
                f"Цена за «{offer.unit}», товар учитывается в «{product.base_unit_code}»: "
                "укажите фасовку предложения. " + exc.message,
            ) from exc
        return CostInput(
            price=offer.price,
            priced_unit=offer.unit,
            conversion=conversion,
            required=item.required,
            target_currency=comparison.target_currency,
            as_of=comparison.as_of,
            policy=self._policy(uow, comparison.workspace_id).policy,
            components=terms.components,
            verified_rates=tuple(uow.costs.list_rates(comparison.workspace_id)),
            fx_rates=tuple(r for _, r in uow.costs.list_fx(comparison.workspace_id)),
            minimum_order=terms.minimum_order,
            order_multiple=terms.order_multiple,
            price_includes_vat=terms.price_includes_vat,
            price_confirmed=offer.price_confirmed,
        )

    def add_offer(self, item_id: UUID, *, offer_id: UUID, terms: OfferTerms) -> ComparisonView:
        """Attach an offer matched to the item's product; the terms are checked by a dry run."""
        with self._uow() as uow:
            item = uow.costs.get_item(item_id)
            if item is None:
                raise NotFoundError("comparison_item")
            comparison = uow.costs.get_comparison(item.comparison_id)
            offer = uow.offers.get(offer_id)
            if comparison is None or offer is None or offer.workspace_id != comparison.workspace_id:
                raise NotFoundError("supplier_offer")
            history = MatchHistory(offer.id, uow.matches.for_offers([offer.id])[offer.id])
            if history.accepted_product != item.product_id:
                raise DomainValidationError(
                    "offer_id",
                    "Предложение не сопоставлено с этим товаром: сначала подтвердите "
                    "сопоставление.",
                )
            decision = next(
                d
                for d in reversed(history.decisions)
                if d.kind is DecisionKind.ACCEPT and d.product_id == item.product_id
            )
            if any(c.offer_id == offer_id for c in uow.costs.comparison_offers([item_id])):
                raise DomainValidationError("offer_id", "Предложение уже добавлено.")
            calculate(self._cost_input(uow, comparison, item, offer, terms), now=self._clock())
            uow.costs.add_comparison_offer(
                ComparisonOffer(new_id(), item_id, offer_id, decision.id, terms, self._clock())
            )
            uow.commit()
        return self.get(item.comparison_id)

    def update_terms(self, comparison_offer_id: UUID, terms: OfferTerms) -> ComparisonView:
        with self._uow() as uow:
            co = uow.costs.get_comparison_offer(comparison_offer_id)
            if co is None:
                raise NotFoundError("comparison_offer")
            item = uow.costs.get_item(co.item_id)
            assert item is not None  # noqa: S101 - FK
            comparison = uow.costs.get_comparison(item.comparison_id)
            offer = uow.offers.get(co.offer_id)
            assert comparison is not None  # noqa: S101 - FK
            assert offer is not None  # noqa: S101 - FK
            calculate(self._cost_input(uow, comparison, item, offer, terms), now=self._clock())
            uow.costs.update_terms(co.id, terms)
            uow.commit()
        return self.get(item.comparison_id)

    def calculate(self, comparison_id: UUID) -> ComparisonView:
        """New immutable snapshots for every offer of the comparison (current inputs)."""
        now = self._clock()
        with self._uow() as uow:
            comparison = uow.costs.get_comparison(comparison_id)
            if comparison is None:
                raise NotFoundError("comparison")
            items = {i.id: i for i in uow.costs.items(comparison_id)}
            cos = uow.costs.comparison_offers(list(items))
            previous = uow.costs.latest_snapshots([c.id for c in cos])
            snapshots: list[tuple[UUID, CostSnapshot]] = []
            for co in cos:
                offer = uow.offers.get(co.offer_id)
                assert offer is not None  # noqa: S101 - FK
                data = self._cost_input(uow, comparison, items[co.item_id], offer, co.terms)
                prior = previous.get(co.id)
                snapshot = calculate(
                    data, now=now, supersedes_id=UUID(prior["id"]) if prior else None
                )
                uow.costs.add_snapshot(co.id, snapshot)
                snapshots.append((co.id, snapshot))
            self._audit(
                uow,
                comparison.workspace_id,
                "comparison",
                comparison.id,
                "calculate",
                {"snapshots": len(snapshots)},
            )
            uow.commit()
        return self.get(comparison_id)

    def snapshot(self, snapshot_id: UUID) -> dict[str, Any]:
        with self._uow() as uow:
            found = uow.costs.get_snapshot(snapshot_id)
        if found is None:
            raise NotFoundError("cost_snapshot")
        return found
