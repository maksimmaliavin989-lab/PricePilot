"""Use cases: canonical product catalog."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pricepilot.application.errors import NotFoundError
from pricepilot.application.ports import (
    MAX_PAGE_SIZE,
    Clock,
    CorrelationProvider,
    Page,
    PageRequest,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.catalog import IdentifierScheme, Product, ProductIdentifier
from pricepilot.domain.common import DomainValidationError, optional_text, utc_now


@dataclass(frozen=True, slots=True)
class IdentifierInput:
    scheme: IdentifierScheme
    value: str
    issuer: str | None = None


@dataclass(frozen=True, slots=True)
class ProductView:
    product: Product
    identifiers: list[ProductIdentifier]


def _no_correlation() -> str | None:
    return None


class CatalogService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    def create(
        self,
        *,
        workspace_id: UUID,
        canonical_name: str,
        base_unit_code: str,
        brand: str | None = None,
        manufacturer_code: str | None = None,
        attributes: dict[str, str] | None = None,
        identifiers: list[IdentifierInput] | None = None,
    ) -> ProductView:
        product = Product.create(
            workspace_id=workspace_id,
            canonical_name=canonical_name,
            brand=brand,
            manufacturer_code=manufacturer_code,
            attributes=attributes or {},
            base_unit=base_unit_code,
            now=self._clock(),
        )
        ids = [
            ProductIdentifier.create(
                product_id=product.id, scheme=i.scheme, value=i.value, issuer=i.issuer
            )
            for i in identifiers or []
        ]
        keys = [(i.scheme, i.normalized_value) for i in ids]
        if len(keys) != len(set(keys)):
            raise DomainValidationError("identifiers", "Идентификаторы повторяются.")
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            uow.catalog.add(product, ids)
            uow.audit.add(
                AuditEvent.record(
                    workspace_id=workspace_id,
                    actor_id=LOCAL_ACTOR,
                    entity_type="product",
                    entity_id=product.id,
                    action="create",
                    before=None,
                    after={"version": product.stamp.version},
                    now=product.stamp.created_at,
                    correlation_id=self._correlation(),
                )
            )
            uow.commit()
        return ProductView(product, ids)

    def get(self, product_id: UUID) -> ProductView:
        with self._uow() as uow:
            found = uow.catalog.get(product_id)
        if found is None:
            raise NotFoundError("product")
        return ProductView(*found)

    def list(
        self, workspace_id: UUID, page: PageRequest, *, query: str | None
    ) -> Page[ProductView]:
        bounded = PageRequest(limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after=page.after)
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            result = uow.catalog.list(
                workspace_id, bounded, query=optional_text(query, "query", 200)
            )
        return Page(
            items=[ProductView(*row) for row in result.items], next_cursor=result.next_cursor
        )
