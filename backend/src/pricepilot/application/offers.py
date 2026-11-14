"""Use cases: supplier offers from reviewed lines, candidates and match decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pricepilot.application.errors import InvalidStateError, NotFoundError, VersionConflictError
from pricepilot.application.ports import (
    MAX_PAGE_SIZE,
    Clock,
    CorrelationProvider,
    LinePageRequest,
    Page,
    PageRequest,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.catalog import IdentifierScheme, Product, ProductIdentifier
from pricepilot.domain.common import DomainValidationError, utc_now
from pricepilot.domain.matching import (
    Candidate,
    CatalogEntry,
    DecisionKind,
    MatchDecision,
    MatchHistory,
    candidates,
    score_candidate,
)
from pricepilot.domain.offers import SkippedLine, SupplierOffer, offers_from_line
from pricepilot.domain.values import Quantity

MAX_CATALOG_SCAN = 5_000
USER_ACTOR = f"user:{LOCAL_ACTOR}"


def _no_correlation() -> str | None:
    return None


@dataclass(frozen=True, slots=True)
class OfferState:
    offer: SupplierOffer
    history: MatchHistory

    @property
    def accepted_decision(self) -> MatchDecision | None:
        product = self.history.accepted_product
        if product is None:
            return None
        return next(
            d
            for d in reversed(self.history.decisions)
            if d.product_id == product and d.kind is DecisionKind.ACCEPT
        )


@dataclass(frozen=True, slots=True)
class OfferImport:
    created: list[SupplierOffer]
    existing: int  # already there and unchanged
    updated: int  # already there, refreshed from the reviewed line
    skipped: list[SkippedLine]


def catalog_entries(uow: UnitOfWork, workspace_id: UUID) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    page = PageRequest(limit=MAX_PAGE_SIZE)
    while len(entries) < MAX_CATALOG_SCAN:
        result = uow.catalog.list(workspace_id, page, query=None)
        entries += [CatalogEntry(p, tuple(ids)) for p, ids in result.items]
        if result.next_cursor is None:
            break
        page = PageRequest(limit=MAX_PAGE_SIZE, after=result.next_cursor)
    return entries


class OfferService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
        actor: str = USER_ACTOR,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation
        self._actor = actor

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

    # --- offers ---------------------------------------------------------------------------
    def create_from_document(self, document_id: UUID) -> OfferImport:
        """Offers from the latest run's lines. Idempotent: an existing offer is not duplicated
        but follows its line after review (confirmed or corrected price, unit, name)."""
        now = self._clock()
        with self._uow() as uow:
            document = uow.documents.get(document_id)
            if document is None:
                raise NotFoundError("source_document")
            run = uow.runs.latest_for_document(document_id)
            if run is None or run.status.value not in {"completed", "review_required"}:
                raise InvalidStateError(
                    "source_document", run.status.value if run else "no_run", "offers"
                )
            existing = {
                (o.line_id, o.price_field_key): o for o in uow.offers.for_document(document_id)
            }
            updated = 0
            created: list[SupplierOffer] = []
            skipped: list[SkippedLine] = []
            kept = 0
            page = LinePageRequest(limit=MAX_PAGE_SIZE)
            while True:
                lines = uow.extraction.list_lines(run.id, page)
                for item in lines.items:
                    result = offers_from_line(
                        workspace_id=document.workspace_id,
                        supplier_id=document.supplier_id,
                        document_id=document.id,
                        line=item.line,
                        fields=item.fields,
                        now=now,
                    )
                    if isinstance(result, SkippedLine):
                        skipped.append(result)
                        continue
                    for offer in result:
                        known = existing.get((offer.line_id, offer.price_field_key))
                        if known is not None:
                            refreshed = known.refreshed(offer, now)
                            if refreshed is None:
                                kept += 1
                            elif uow.offers.update(refreshed, known.stamp.version):
                                updated += 1
                            continue
                        uow.offers.add(offer)
                        created.append(offer)
                if lines.next_ordinal is None:
                    break
                page = LinePageRequest(limit=MAX_PAGE_SIZE, after_ordinal=lines.next_ordinal)
            if created or updated:
                self._audit(
                    uow,
                    document.workspace_id,
                    "source_document",
                    document.id,
                    "offers_created",
                    {"created": len(created), "updated": updated, "skipped": len(skipped)},
                )
            uow.commit()
        return OfferImport(created, kept, updated, skipped)

    def _state(self, uow: UnitOfWork, offer: SupplierOffer) -> OfferState:
        decisions = uow.matches.for_offers([offer.id])[offer.id]
        return OfferState(offer, MatchHistory(offer.id, decisions))

    def get(self, offer_id: UUID) -> OfferState:
        with self._uow() as uow:
            offer = uow.offers.get(offer_id)
            if offer is None:
                raise NotFoundError("supplier_offer")
            return self._state(uow, offer)

    def list(
        self, workspace_id: UUID, page: PageRequest, *, document_id: UUID | None
    ) -> Page[OfferState]:
        bounded = PageRequest(limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after=page.after)
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            result = uow.offers.list(workspace_id, bounded, document_id=document_id)
            decisions = uow.matches.for_offers([o.id for o in result.items])
        return Page(
            items=[OfferState(o, MatchHistory(o.id, decisions[o.id])) for o in result.items],
            next_cursor=result.next_cursor,
        )

    def set_pack(self, offer_id: UUID, content: Quantity, expected_version: int) -> OfferState:
        with self._uow() as uow:
            offer = uow.offers.get(offer_id)
            if offer is None:
                raise NotFoundError("supplier_offer")
            if offer.stamp.version != expected_version:
                raise VersionConflictError("supplier_offer", offer.stamp.version)
            updated = offer.with_pack(content, self._clock())
            if not uow.offers.update(updated, expected_version):
                raise VersionConflictError("supplier_offer", offer.stamp.version)
            self._audit(
                uow,
                offer.workspace_id,
                "supplier_offer",
                offer.id,
                "pack_confirmed",
                {"pack": updated.pack.to_json() if updated.pack else None},
            )
            state = self._state(uow, updated)
            uow.commit()
        return state

    # --- matching -------------------------------------------------------------------------
    def candidates(self, offer_id: UUID, limit: int = 5) -> tuple[OfferState, Sequence[Candidate]]:
        with self._uow() as uow:
            offer = uow.offers.get(offer_id)
            if offer is None:
                raise NotFoundError("supplier_offer")
            entries = catalog_entries(uow, offer.workspace_id)
            state = self._state(uow, offer)
        rejected = state.history.rejected_products()
        found = [
            c
            for c in candidates(offer.view(), entries, limit + len(rejected))
            if c.product_id not in rejected
        ]
        return state, found[:limit]

    def decide(
        self, offer_id: UUID, *, kind: DecisionKind, product_id: UUID, reason: str | None
    ) -> OfferState:
        now = self._clock()
        with self._uow() as uow:
            offer = uow.offers.get(offer_id)
            if offer is None:
                raise NotFoundError("supplier_offer")
            found = uow.catalog.get(product_id)
            if found is None or found[0].workspace_id != offer.workspace_id:
                raise NotFoundError("product")
            product, identifiers = found
            state = self._state(uow, offer)
            candidate = score_candidate(offer.view(), CatalogEntry(product, tuple(identifiers)))
            decision = state.history.decide(
                kind=kind,
                product_id=product_id,
                actor=self._actor,
                now=now,
                reason=reason,
                candidate=candidate if kind is DecisionKind.ACCEPT else None,
            )
            uow.matches.add(decision)
            self._audit(
                uow,
                offer.workspace_id,
                "supplier_offer",
                offer.id,
                f"match_{kind.value}",
                {"product_id": str(product_id), "override": decision.override},
            )
            uow.commit()
        return state

    def create_product(
        self, offer_id: UUID, *, canonical_name: str | None, base_unit_code: str | None
    ) -> tuple[OfferState, Product]:
        """A new catalog product from an offer, matched to it (the user's explicit action)."""
        now = self._clock()
        with self._uow() as uow:
            offer = uow.offers.get(offer_id)
            if offer is None:
                raise NotFoundError("supplier_offer")
            state = self._state(uow, offer)
            if state.history.accepted_product is not None:
                raise DomainValidationError(
                    "offer", "Предложение уже сопоставлено: сначала отмените сопоставление."
                )
            unit = base_unit_code or offer.unit
            if unit is None:
                raise DomainValidationError("base_unit_code", "Укажите базовую единицу товара.")
            product = Product.create(
                workspace_id=offer.workspace_id,
                canonical_name=canonical_name or offer.name,
                brand=None,
                manufacturer_code=None,
                attributes={},
                base_unit=unit,
                now=now,
            )
            identifiers = []
            if offer.supplier_sku and offer.supplier_id:
                identifiers.append(
                    ProductIdentifier.create(
                        product_id=product.id,
                        scheme=IdentifierScheme.SUPPLIER_SKU,
                        value=offer.supplier_sku,
                        issuer=str(offer.supplier_id),
                    )
                )
            uow.catalog.add(product, identifiers)
            candidate = score_candidate(offer.view(), CatalogEntry(product, tuple(identifiers)))
            decision = state.history.decide(
                kind=DecisionKind.ACCEPT,
                product_id=product.id,
                actor=self._actor,
                now=now,
                reason="товар создан из предложения",
                candidate=candidate,
            )
            uow.matches.add(decision)
            self._audit(
                uow,
                offer.workspace_id,
                "product",
                product.id,
                "create_from_offer",
                {"offer_id": str(offer.id)},
            )
            uow.commit()
        return state, product
